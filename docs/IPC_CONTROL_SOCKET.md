# Control socket (web → display)

The display process serves a Unix socket that the web interface uses to send
it commands and get an answer back. It replaces the cache-file "mailboxes" on
the SD card one command at a time. Stage 1, described here, carries on-demand
start/stop/status. The file mailbox stays as a fallback for one release.

| | |
|---|---|
| Socket | `/run/ledmatrix/control.sock` (tmpfs) |
| Served by | the display process ([`src/ipc/server.py`](../src/ipc/server.py)), started by `DisplayController.run()` |
| Used by | the web interface ([`src/ipc/client.py`](../src/ipc/client.py)): `POST /api/v3/display/on-demand/start` and `/stop` |
| Contract | [`src/ipc/contract.py`](../src/ipc/contract.py): messages, versions, framing and the socket path; both sides import it |
| Override | `LEDMATRIX_CONTROL_SOCKET=/some/path.sock` for both processes, or `=off` to disable it |

## Why

Before the socket, the web interface sent commands by writing a cache key
(`display_on_demand_request`) that the display read every 0.25 s.

- **No acknowledgement.** The route answered "success" once the file was
  written, whether or not a display was running to read it.
- **Lost requests.** The display had to read the request and then delete it.
  A request written between those two steps could be thrown away (see
  `_consume_on_demand_request`). The cache has no atomic claim to prevent it.
- **Fragile.** Each channel repeated its own permission, atomic-write,
  staleness and in-memory-cache rules. Two of them caused bugs: a `memory_ttl`
  bug ignored every on-demand request after the first for an hour, and a
  stopped display was still reported as "active" for two minutes.

The socket answers every command, carries one request per message (so nothing
can overwrite it), and belongs to the display process. If the display is not
running, the socket does not exist, and the web interface knows right away.

## Protocol (version 1)

**Framing.** One JSON object per line (newline-delimited JSON), UTF-8, at
most 64 KiB per line (`MAX_MESSAGE_BYTES`). Senders encode with
`ensure_ascii`, so a newline never appears inside a message. A connection
can carry several requests. Each request gets exactly one response, in order.

**Request**

```json
{"v": 1, "id": "5f0c…", "cmd": "on_demand.start",
 "args": {"plugin_id": "clock", "mode": null, "duration": 30, "pinned": false}}
```

- `v` is the protocol version.
- `id` is a printable string of 1-128 characters. It is echoed back in the
  response, and for on-demand commands it is also the on-demand `request_id`.
- `cmd` is a command name.
- `args` is an object. It may be omitted when a command takes no arguments.

**Response**

```json
{"v": 1, "id": "5f0c…", "ok": true,  "result": {"accepted": true, "request_id": "5f0c…", "queued": 1}}
{"v": 1, "id": "5f0c…", "ok": false, "error": {"code": "busy", "message": "…"}}
```

`id` is `null` only when the request could not be parsed far enough to have
one. Clients branch on `error.code`, never on the message text.

**Commands**

| `cmd` | `args` | `result` | Kind |
|---|---|---|---|
| `hello` | `{versions: [int], client?: str}` | `{version, versions, commands, max_message_bytes, server}` | answered directly |
| `ping` | — | `{pong: true}` | answered directly |
| `on_demand.start` | `{plugin_id?, mode?, duration?, pinned?}` (at least one of `plugin_id` and `mode`) | ack | queued |
| `on_demand.stop` | — | ack | queued |
| `on_demand.status` | — | `{on_demand: {...}, current_mode, display_active}` | answered directly |

`duration` is a number of seconds, or a numeric string. `0`, `null` or `""`
mean "until stopped". `pinned` must be a real boolean: the REST route has
already converted strings like `"false"` before it sends the command. The
`on_demand` object in `on_demand.status` is the same dict the display
publishes to `display_on_demand_state`.

**Acknowledgements.** A queued command is *accepted*, not *done*.
`{"accepted": true, "request_id": …}` means the command is waiting in the
render thread's queue, and the render thread will apply it at its next
on-demand check. That is within one frame on a scrolling screen, 0.25 s
during a dwell, and up to 1 s on a static screen, whose frame loop sleeps a
second between frames. Except on a scrolling screen, where the mailbox waits
up to 0.25 s, these are the mailbox's delays too: stage 1 adds
acknowledgements, not speed. Any outcome is published as before
(`display_on_demand_state`, and `status`/`error` for a bad plugin or mode),
and it can be read with `on_demand.status`.

**Versions.** Every request carries `v`. For any command except `hello`, a
`v` the display does not speak gets `unsupported_version`. `hello` is checked
by its `versions` list instead, and its result names the highest version both
sides share, so a client can find out what a display supports before it
relies on anything newer. Stage 1's client sends `v: 1` and falls back to the
mailbox when the display refuses it. It does not send `hello` first, which
saves a round trip.

**Error codes:** `bad_json`, `bad_request`, `message_too_large`,
`unsupported_version`, `unknown_command`, `invalid_args`, `busy` (queue full,
or too many connections), `forbidden` (peer credentials refused), `internal`.

Try it on a device:

```bash
python3 - <<'EOF'
from src.ipc import client          # run from the project directory
print(client.on_demand_status())
EOF
```

## How the display applies a command

The server's threads never touch rendering. A connection thread parses the
request, validates it against the contract, and then does one of two things:

- For a command that changes the panel, it puts a `QueuedCommand` on a
  bounded queue (16 entries) and answers with the ack.
- For a query, it answers from a status snapshot the display provides
  (`DisplayController._control_status`). The snapshot only reads attributes.

The render thread drains the queue in `_poll_on_demand_requests()`, the same
place it reads the mailbox, and hands each command to
`_handle_on_demand_request()`, which is the mailbox's own handler. The two
paths share all of their code: activation, the processed-id guard, error
publishing, and resuming the rotation afterwards. The 0.25 s floor on the
mailbox read does not apply to the queue, because draining it costs no disk
read. A queued command also lets `_service_pending_changes()` skip its own
floor, so a long scrolling screen or a Vegas iteration takes the command at
its next frame.

**Exactly once.** A command and a mailbox write for the same request share
one `request_id`. If the client times out after the display queued the
command and then also writes the mailbox, the display processes the request
once. The existing `on_demand_request_id` and processed-id checks drop the
second copy.

## Robustness

All of this runs inside the display process, so nothing a client does may
block the render loop or crash it:

- **Bounded connections.** Each connection gets its own daemon thread, with
  at most 8 at once. One more is answered `busy` and closed.
- **Timeouts.** Each read and write times out after 2 s. A message must
  arrive whole within 5 s of its first byte. An idle connection is closed
  after 10 s. A slow or stuck client costs one thread for a few seconds.
- **Malformed input.** A line that is not JSON gets `bad_json`, and the
  connection carries on. A line longer than 64 KiB gets `message_too_large`,
  and the connection is closed, because the next message boundary cannot be
  found. A client that disconnects mid-message is dropped silently. No
  exception from a handler leaves the connection thread.
- **Full queue.** When the queue is full, the client gets `busy` and falls
  back to the mailbox. A full queue means the render thread is stuck, and the
  systemd watchdog deals with that.
- **Startup.** The server binds under a temporary name, sets the mode and the
  group, then renames the socket into place, so it never appears with the
  umask's permissions. It removes a stale socket (a file that nothing is
  listening on). It never removes a live socket or a file that is not a
  socket. `close()` removes the socket only if it is still the one this
  process created.
- **Never fatal.** If the server cannot start (Windows, no `AF_UNIX`, a bind
  failure, `LEDMATRIX_CONTROL_SOCKET=off`), it logs that and the display runs
  as before. The web interface then uses the mailbox.

## Security model

The display runs as root and the web interface as the installing user (see
[PERMISSIONS.md](PERMISSIONS.md)). The socket admits exactly those two, plus
anything else in the group they share:

1. **The directory.** `/run/ledmatrix` is created by `RuntimeDirectory=ledmatrix`
   in `ledmatrix.service` (#687): root-owned, `0755`, on tmpfs, and removed
   when the display stops. Under an older unit, the display creates the
   directory itself as root, as it does for the heartbeat. No installer
   change is needed.
2. **The socket file.** The file is `root:<shared group>` with mode `0660`,
   and the kernel refuses `connect()` to anyone without write permission on
   it. The shared group is the cache directory's group whenever that
   directory is group-writable. That is `ledmatrix` on an installed device
   (`/var/cache/ledmatrix` is `root:ledmatrix 2775`), and it is the same rule
   DiskCache uses for every file the two services share. Otherwise the group
   is the project directory's (`get_shared_group_gid()`, which config files
   use). With neither, the mode is `0600` and only root can connect.
3. **Peer credentials.** Where the kernel reports them (`SO_PEERCRED`, on
   Linux), the server checks every connection again. It accepts root, the
   display's own user, or a member of the shared group: the peer's primary
   gid, or a supplementary group read from `/proc/<pid>/status`. If `/proc`
   is unreadable, it uses the group database. Any other peer gets `forbidden`
   and is disconnected. This covers a socket mode that someone loosened by
   hand.

The commands are deliberately narrow. Stage 1 can start or stop on-demand
display and read its state, which anyone who can reach the web UI can already
do. Nothing on the socket runs a shell, writes a file, or names a path.

**Development.** A display that is not root and cannot write to
`/run/ledmatrix`, such as `python3 run.py -e` from a checkout, serves the
socket at `$TMPDIR/ledmatrix-<uid>/control.sock`. That directory is private
(`0700`), and the server refuses it if another user owns it. The web
interface, run by the same user, looks there after `/run/ledmatrix`. The test
suite sets `LEDMATRIX_CONTROL_SOCKET=off` (`test/conftest.py`), so a run on a
device never touches the live display.

## Stage plan

1. **On-demand, with acks (this stage).** Contract, server, client.
   `on_demand.start`/`stop`/`status`, `hello`, `ping`. The REST routes try the
   socket first and report `transport: "socket" | "mailbox"` (plus
   `socket_error` on fallback). The mailbox is unchanged, and the plugins that
   write it directly (birdnet-go, mqtt-notifications, on-air, pomodoro-timer)
   keep working.
2. **Commands that are restarts or polls today.**
   - `brightness.set`, transient and with no `config.json` write.
   - `plugin.reload`, which replaces the `restart_required` answer from #688
     with a live reload of the updated plugin on the render thread.
   - `config.reload`, which applies a saved config without waiting for the 2 s
     mtime poll and acks which sections changed.
   - The dwell sleep and the static screen's 1 s frame sleep wait on the
     queue instead of sleeping, so a command lands within milliseconds on
     every kind of screen. Under WSL, with a static plugin on screen, a stop
     takes 1.0 s by either path today.
3. **A state stream.** A `subscribe` command that keeps the connection open
   and pushes events: mode changes, on-demand state, plugin runtime state and
   the heartbeat. It replaces the polled `display_current_state`,
   `plugin_runtime_snapshot` (#690) and `display-heartbeat.json` (#687) for
   readers that hold a connection. The web interface relays it to its
   existing SSE stream. The files remain for one release for older readers.
4. **Retire the mailboxes.** After a release in which every device has had the
   socket, the web interface stops writing `display_on_demand_request`, and
   the display stops polling it, logging the plugins that still write it so
   they can move to an in-process `request_display()`. The other cache keys
   used as messages (`plugin_error_clear_request` and the remaining
   `display_*` keys) move to the socket or to tmpfs.

## Checking it on a device

```bash
ls -l /run/ledmatrix/control.sock                 # srw-rw---- root ledmatrix
sudo journalctl -u ledmatrix | grep "Control socket"
curl -s -X POST localhost:5000/api/v3/display/on-demand/start \
  -H 'Content-Type: application/json' -d '{"plugin_id":"clock","duration":20}'
# ... "transport": "socket"
```

If the response says `"transport": "mailbox"`, `socket_error` gives the
reason. `no_socket` means the display is stopped or predates the socket.
`refused` usually means the web user is not in the socket's group, which
takes effect when the web service restarts after the user is added.
