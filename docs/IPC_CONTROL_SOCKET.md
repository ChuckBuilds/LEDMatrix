# Control socket (web → display)

The display process serves a Unix socket that the web interface uses to send
it commands and get an answer back. It replaces the cache-file "mailboxes" on
the SD card one command at a time. Stage 1 carries on-demand start, stop and
status. Stage 2 makes those commands land within a frame on every kind of
screen, and adds `brightness.set` and `plugin.reload`. The file mailbox stays
as a fallback for one release.

| | |
|---|---|
| Socket | `/run/ledmatrix/control.sock` (tmpfs) |
| Served by | the display process ([`src/ipc/server.py`](../src/ipc/server.py)), started by `DisplayController.run()` |
| Used by | the web interface ([`src/ipc/client.py`](../src/ipc/client.py)): `POST /api/v3/display/on-demand/start` and `/stop`, `POST /api/v3/plugins/update` (reload), `POST /api/v3/config/main` (brightness) |
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
| `brightness.set` | `{brightness: int 0-100}` | `{brightness, panel_brightness, dimmed, display_active}` | queued, awaited (2 s) |
| `plugin.reload` | `{plugin_id}` | `{plugin_id, reloaded: true, version, modes}` | queued, awaited (10 s) |

`duration` is a number of seconds, or a numeric string. `0`, `null` or `""`
mean "until stopped". `pinned` must be a real boolean: the REST route has
already converted strings like `"false"` before it sends the command. The
`on_demand` object in `on_demand.status` is the same dict the display
publishes to `display_on_demand_state`.

`brightness.set` sets the panel's normal brightness. It is transient: it
writes nothing to `config.json`, and the next config the display's watcher
loads (or a restart) puts the configured value back. The web interface
sends it after it has saved the setting, so the two agree. The dim schedule
still applies on top, so `panel_brightness` is the dim level while the
schedule dims. While the schedule has the display off, the new level is
kept for when it comes back on.

`plugin.reload` loads a plugin the display is running again from disk,
manifest included: the steps of disabling it live and enabling it again,
with its modes kept in their place in the rotation. Only a running plugin
can be reloaded (`not_loaded` otherwise), so the id never makes the display
import anything new. A plugin loaded only for an on-demand session gets
`busy`. A new version that fails to load gets `failed` and stays out of the
rotation, as it would after a restart.

**Acknowledgements.** A queued on-demand command is *accepted*, not *done*.
`{"accepted": true, "request_id": …}` means the command is waiting in the
render thread's queue. Since stage 2 the render thread waits on that queue
instead of sleeping, so it applies the command within one frame on every
kind of screen (see below). Any outcome is published as before
(`display_on_demand_state`, and `status`/`error` for a bad plugin or mode),
and it can be read with `on_demand.status`.

**Awaited commands.** `brightness.set` and `plugin.reload` are answered only
once the render thread has applied them, with their result or their error.
The connection thread waits for that (2 s and 10 s, `AWAIT_SECONDS` in the
contract); the render thread never waits for a client. When the render thread
has not got to the command in time, the answer is `pending`: the command
stays queued and is still applied, so a client treats `pending` as "not known
to be done", not as a refusal. The client's own timeout is one second longer
than the display's wait, so `pending` arrives before the client gives up.

**Versions.** Every request carries `v`. For any command except `hello`, a
`v` the display does not speak gets `unsupported_version`. `hello` is checked
by its `versions` list instead, and its result names the highest version both
sides share, so a client can find out what a display supports before it
relies on anything newer. The client sends `v: 1` and falls back to the
mailbox when the display refuses it. It does not send `hello` first, which
saves a round trip.

New commands are added within a version, so stage 2 is still version 1. A
display that does not know a command answers `unknown_command`, which the
web interface treats like any other socket failure and falls back from, and
`hello` lists the commands a display knows. The version changes only when the
envelope or the meaning of an existing command changes.

**Error codes:** `bad_json`, `bad_request`, `message_too_large`,
`unsupported_version`, `unknown_command`, `invalid_args`, `busy` (queue full,
or too many connections), `forbidden` (peer credentials refused), `internal`.
Stage 2 adds `pending` (accepted, not applied in time, still queued),
`not_loaded` (`plugin.reload` of a plugin the display is not running) and
`failed` (the render thread tried, and it did not work).

Try it on a device:

```bash
python3 - <<'EOF'
from src.ipc import client          # run from the project directory
print(client.on_demand_status())
print(client.brightness_set(60))
EOF
```

## How the display applies a command

The server's threads never touch rendering. A connection thread parses the
request, validates it against the contract, and then does one of two things:

- For a command that changes the panel, it puts a `QueuedCommand` on a
  bounded queue (16 entries) and answers with the ack, or, for an awaited
  command, with the outcome the render thread reports back through the
  command's `CommandOutcome`.
- For a query, it answers from a status snapshot the display provides
  (`DisplayController._control_status`). The snapshot only reads attributes.

The render thread drains the queue in `_poll_on_demand_requests()`, the same
place it reads the mailbox:

- An on-demand command goes to `_handle_on_demand_request()`, which is the
  mailbox's own handler. The two paths share all of their code: activation,
  the processed-id guard, error publishing, and resuming the rotation
  afterwards.
- `brightness.set` is applied there and then (`_apply_control_brightness`),
  and the current frame is pushed again so the panel shows it.
- `plugin.reload` waits for the top of the next loop pass, the place where
  plugins are enabled and disabled live, because there no `display()` and no
  Vegas iteration is on the stack (`_apply_pending_plugin_reloads`). Until
  then the current screen ends early, as it does for a WiFi notice: the
  frame loops, the dwell and Vegas's interrupt check all treat a pending
  reload as a reason to stop (`_screen_preempted`). The rotation then
  advances, and the next pass reloads before it draws.

The 0.25 s floor on the mailbox read does not apply to the queue, because
draining it costs no disk read. A queued command also lets
`_service_pending_changes()` skip its own floor.

### Waking the render thread (stage 2)

Stage 1 made the socket answer, but not land sooner: a queued command waited
for the same polls the mailbox does. Measured on ledpi (Pi 4, 24 fps Vegas),
a start took 1.02 s on a static screen and about 0.4 s in Vegas either way.
Now the queue wakes the render thread:

- **The waits.** The server sets a `threading.Event` whenever it queues a
  command. The render thread waits on it (`ControlServer.wait_for_command`)
  where it used to sleep: the static screen's 1 s frame sleep
  (`_wait_frame_interval`) and the dwell's 0.25 s ticks
  (`_sleep_with_plugin_updates`, which also covers scheduled-off and the
  empty-rotation pause). On a wake it applies the command at once. A command
  that does not end the screen, such as a brightness, does not cut the frame
  short: the wait carries on to the end of the interval, so the plugin is
  still drawn once a second.
- **Vegas.** The coordinator still runs its interrupt check every 10 frames,
  and now also at any frame where `urgent()` is true. The display passes
  "a control socket command is queued", which is one `Event.is_set()` per
  frame.
- **Scrolling screens** already service pending changes every frame.

So a command lands within a millisecond or so on a static screen and in a
dwell, and within one frame in Vegas and on a scrolling screen. The mailbox
keeps its old delays. Commands still run only on the render thread: the
connection threads only queue them and set the event.

The waits are timed `Event.wait()` calls: no polling, and no more wake-ups
than the sleeps they replace when nothing arrives. Measured under WSL
(Python 3.12, 20 s runs in the order before, after, after, before, with the
socket's accept thread up), the idle process used 0.015–0.018% of a core
before and 0.019–0.021% after on a static screen, and 0.035–0.037% before and
0.047% after in a dwell: about 25 µs more per wait, from `Event.wait`'s own
bookkeeping. A client's send to the render thread waking took 0.72 ms median
(1.04 ms max), and a whole `brightness.set` round trip 0.64 ms median.

Without a socket (Windows, `LEDMATRIX_CONTROL_SOCKET=off`) the waits are the
plain sleeps they were.

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
- **Awaited commands.** The wait for an awaited command's outcome happens on
  its connection thread and is bounded (`AWAIT_SECONDS`), so a stuck render
  thread costs that client `pending` and one connection slot for at most
  10 s. The render thread settles an outcome without blocking; one nobody is
  waiting for any more is simply dropped.
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

The commands are deliberately narrow. They start or stop on-demand display,
read its state, set the brightness, and reload a plugin the display is
already running, all of which anyone who can reach the web UI can already do
(the last by restarting the display). Nothing on the socket runs a shell,
writes a file, or names a path, and `plugin.reload` cannot make the display
import a plugin it was not running. Stage 2 changed none of the access rules
above.

**Development.** A display that is not root and cannot write to
`/run/ledmatrix`, such as `python3 run.py -e` from a checkout, serves the
socket at `$TMPDIR/ledmatrix-<uid>/control.sock`. That directory is private
(`0700`), and the server refuses it if another user owns it. The web
interface, run by the same user, looks there after `/run/ledmatrix`. The test
suite sets `LEDMATRIX_CONTROL_SOCKET=off` (`test/conftest.py`), so a run on a
device never touches the live display.

## Stage plan

1. **On-demand, with acks (done, #706).** Contract, server, client.
   `on_demand.start`/`stop`/`status`, `hello`, `ping`. The REST routes try the
   socket first and report `transport: "socket" | "mailbox"` (plus
   `socket_error` on fallback). The mailbox is unchanged, and the plugins that
   write it directly (birdnet-go, mqtt-notifications, on-air, pomodoro-timer)
   keep working.
2. **Commands that were restarts or polls (done).**
   - The render thread waits on the queue instead of sleeping, and Vegas
     checks it every frame, so a command lands within a frame on every kind
     of screen (see "Waking the render thread").
   - `brightness.set`, transient and with no `config.json` write. `POST
     /api/v3/config/main` sends it after saving a brightness and reports
     `brightness_transport`; without the socket the config watcher applies
     the saved value, as before.
   - `plugin.reload`, which replaces the `restart_required` answer from #688
     for a store update of an enabled plugin. `POST /api/v3/plugins/update`
     answers `restart_required: false, reloaded: true` once the new code
     runs, and falls back to the restart banner (with `reload_error`)
     otherwise.
   - `config.reload` was left out. Its only gain over the config watcher
     would be skipping the watcher's 2 s mtime poll, and the one setting
     where those seconds show, brightness, now has its own command. Plugin
     settings already reach the running plugin through the watcher, and the
     "which sections changed" ack had no reader: the web interface knows
     what it saved. A reload from the socket thread would also run every
     config subscriber on a second thread beside the watcher's.
3. **A state stream.** A `subscribe` command that keeps the connection open
   and pushes events: mode changes, on-demand state (including the outcome of
   an acked on-demand command, which today is only published), plugin
   runtime state, the outcome of a reload that answered `pending`, and the
   heartbeat. It replaces the polled `display_current_state`,
   `plugin_runtime_snapshot` (#690) and `display-heartbeat.json` (#687) for
   readers that hold a connection. The web interface relays it to its
   existing SSE stream. The files remain for one release for older readers.
   - The server's per-connection threads (8 at most) do not suit long-lived
     subscribers. A subscriber needs its own bound and a writer that drops
     events for a slow reader rather than blocking the display.
   - Events are produced on the render thread, so publishing must be a
     non-blocking hand-off, like the queue in the other direction.
   - The store's install of an already-enabled plugin, and an uninstall that
     keeps its config, still answer `restart_required`. With the stream they
     can use a load/unload command and report the result the same way the
     update route does now.
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

Brightness and a plugin reload:

```bash
curl -s -X POST localhost:5000/api/v3/config/main \
  -H 'Content-Type: application/json' -d '{"brightness":40}'
# ... "brightness_transport": "socket"
curl -s -X POST localhost:5000/api/v3/plugins/update \
  -H 'Content-Type: application/json' -d '{"plugin_id":"clock-simple"}'
# after a real update of an enabled plugin: "restart_required": false, "reloaded": true
sudo journalctl -u ledmatrix | grep -E "Brightness set|Reload(ing|ed) plugin"
```

`unknown_command` in `brightness_socket_error` or `reload_error` means the
display runs a stage-1 build: restart it once to pick up this one.
