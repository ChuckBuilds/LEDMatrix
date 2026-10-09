# Control socket (web → display)

The display process serves a Unix socket that the web interface uses to send
it commands and get an answer back. It replaced the cache-file "mailboxes" on
the SD card one command at a time. Stage 1 carries on-demand start, stop and
status. Stage 2 makes those commands land within a frame on every kind of
screen, and adds `brightness.set` and `plugin.reload`. Stage 3 adds a state
stream (`state.get`, `state.subscribe`), so the web interface reads what the
display is doing from the socket instead of from cache files the display
display wrote to the SD card. Stage 4 makes the socket the only way a command goes
while it works, and `errors.clear` replaces the last command that always
went through a mailbox. Stage 5 removes the mailboxes: the web interface no
longer writes them and the display no longer reads them, so the socket is
the only way a command reaches the display (see "Without the socket"). The
cache keys the display writes for readers stay as their fallback.

| | |
|---|---|
| Socket | `/run/ledmatrix/control.sock` (tmpfs) |
| Served by | the display process ([`src/ipc/server.py`](../src/ipc/server.py)), started by `DisplayController.run()` |
| Used by | the web interface ([`src/ipc/client.py`](../src/ipc/client.py)): `POST /api/v3/display/on-demand/start` and `/stop`, `POST /api/v3/plugins/update` (reload), `POST /api/v3/config/main` (brightness), `POST /api/v3/errors/clear`; and through [`web_interface/display_state.py`](../web_interface/display_state.py) (the state stream), `GET /api/v3/display/current-status`, `/display/on-demand/status`, `/plugins/installed` (`runtime`), `/plugins/state` and the reconciliations, `/health` (`display_loop`) |
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

Stage 3 is still version 1: `state.get` and `state.subscribe` are new
commands, and a stage-2 display answers them `unknown_command`, which the
web interface treats as "no socket" and falls back from.


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
| `state.get` | `{since?, epoch?}` | a state snapshot (see "The state stream") | answered directly |
| `state.subscribe` | — | a state snapshot, then pushed `state` / `tick` events | answered directly, then a stream |
| `errors.clear` | `{cutoff: number}` (epoch seconds, finite, ≥ 0) | `{request_id, cutoff, cleared}` | answered directly, once applied |

`errors.clear` (stage 4) forgets the plugin errors the display recorded at
or before `cutoff` and rewrites its error snapshot (`plugin_error_snapshot`)
before it answers, so the web interface's next read already has it. The
request `id` is the clear's id, which the snapshot reports as
`applied_clear_id`. It is answered on the connection thread by a handler the
display registers (`ControlServer(handlers=...)`, the contract's
`DIRECT_COMMANDS`): the error aggregator and its publisher have their own
locks, and nothing the render thread owns is touched. A display that has no
handler answers `unknown_command`, as an older display does.

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
rotation, as it would after a restart. The load runs off the render thread,
so the panel keeps scrolling while it happens (see below).

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
relies on anything newer. The client sends `v: 1`, and a route answers an
error when the display refuses it. It does not send `hello` first, which
saves a round trip.

New commands are added within a version, so stage 2 is still version 1. A
display that does not know a command answers `unknown_command`, which the
web interface treats like any other socket failure and falls back from, and
`hello` lists the commands a display knows.
(Since stage 5, a command the display does not know is an error the route
reports, telling the user to restart the display; there is no mailbox left
to fall back to.) The version changes only when the
envelope or the meaning of an existing command changes.

**Events.** `state.subscribe` is the one command with more than one message
in reply. After its response, the display pushes events on the same
connection until either side hangs up:

```json
{"v": 1, "id": "<the subscribe id>", "event": "state", "result": {...a state snapshot...}}
{"v": 1, "id": "<the subscribe id>", "event": "tick",  "result": {"version": 7, "epoch": "…", "pid": 812, "served_at": 1790000000.1, "changed": false, "loop": {...}, "volatile": {"display": {"last_updated": 1790000000.0}, "...": "..."}}}
```

An event has `event` where a response has `ok`, which is how a reader tells
them apart. The client sends nothing after the subscribe; anything it does
send is ignored.

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

## The state stream (stage 3)

Before stage 3 the web interface learned what the display was doing by
reading files the display kept writing:

| What | Written by the display | How often | Medium |
|---|---|---|---|
| current mode, plugin, `is_display_active`, `on_demand_active` | `display_current_state` | every mode change, every flag change, and every 30 s | cache (SD card) |
| on-demand session | `display_on_demand_state` | on each on-demand event | cache (SD card) |
| plugin runtime snapshot (#690) | `plugin_runtime_snapshot` | on a change (at most every 10 s), else every 60 s | cache (SD card) |
| render-loop liveness (#687) | `display-heartbeat.json` | every 5 s | tmpfs |

Now the display also keeps the same state in memory and serves it on the
socket.

**The snapshot.** `state.get` and `state.subscribe` answer with one object:

```json
{"schema": 1, "version": 42, "epoch": "3f9c0d1e2a4b5c6d", "pid": 812,
 "served_at": 1790000000.1, "changed": true,
 "loop": {"heartbeat_age_seconds": 1.8, "armed": true, "stale_after": 60.0},
 "state": {
   "display":    {"mode": "nfl_live", "plugin_id": "football-scoreboard", "mode_index": 3,
                  "total_modes": 9, "on_demand_active": false, "is_display_active": true,
                  "last_updated": 1790000000.0},
   "on_demand":  {"active": false, "status": "idle", "...": "as display_on_demand_state"},
   "brightness": {"brightness": 80, "panel_brightness": 40, "dimmed": true},
   "plugins":    {"schema": 1, "running": true, "published_at": 1789999998.5, "...": "as plugin_runtime_snapshot"},
   "loop":       {"heartbeat_age_seconds": 1.8, "armed": true, "stale_after": 60.0}
 }}
```

- `display` and `on_demand` are the dicts the cache keys hold (`on_demand`
  includes `request_id`, the request it answers), `plugins` is
  the runtime snapshot (`build_runtime_snapshot`), and `brightness` is the
  configured level, what the panel shows now, and whether the dim schedule
  has it dimmed. A section not published yet is `null`.
- `loop` is not published: the display measures it when it answers, from
  the render thread's last beat in memory (`RenderWatchdog.liveness()`),
  the same beat that writes the heartbeat file. So it keeps ageing while the
  render thread is stuck, and the socket's connection threads still answer.
  `heartbeat_age_seconds` is `null` until the loop has drawn its first frame.
- `version` goes up whenever a section changes, ignoring the timestamps that
  move on every publish (`last_updated`, `remaining`, `published_at`). It
  counts within an `epoch`, one run of the display process, so a reader that
  sees a new `epoch` has a restarted display.
- `state.get` with `since` and `epoch` from an earlier answer gets just
  `{changed: false, version, epoch, pid, served_at, loop, volatile}` while
  nothing has changed. `volatile` is `{section: {key: value}}`: the current
  values of those ignored timestamps, which the reader merges into the copy
  it has. They don't make a new version, but they are still news:
  `display.last_updated` is how a reader knows the render thread is still
  publishing, and `plugins.published_at` the runtime publisher. Without
  them a reader's copy kept the timestamps of the last real change, so a
  mode on screen for over 120 s read as unknown.
- A snapshot that would not fit in a message (hundreds of plugins) is sent
  without `plugins`, and `truncated: ["plugins"]` says so. Readers then use
  the cache for that section only.

**The stream.** `state.subscribe` answers with the snapshot, then:

- a `state` event (a full snapshot) whenever the version changes, and
- a `tick` at least every 5 s (`SUBSCRIBE_KEEPALIVE_SECONDS`) when nothing
  changed. It is the short `changed: false` answer, so it carries `loop`
  (a stalled render loop shows up within one tick) and `volatile` (the
  timestamps stay as fresh as the writers keep them), and it tells the
  reader the connection is alive.

A slow reader is never sent a backlog: each event is the latest version, so
one that falls behind skips the versions in between. A reader that has heard
nothing for 15 s (three keepalives) stops trusting its copy.

**Who publishes, and when.** All of it is in memory, with no disk writes:

- the render thread, at the places it already published the cache keys:
  `display` and `brightness` on every pass of
  `_publish_current_mode_state_if_changed()` (every loop pass, and every
  `_service_pending_changes()` in a dwell, a scrolling screen or Vegas), and
  `on_demand` in `_publish_on_demand_state()`. Every pass refreshes
  `display.last_updated`, so a reader can tell when the render thread has
  stopped publishing, just as the cache key's 120 s `max_age` does.
- the plugin runtime publisher's thread, on every 5 s tick: the snapshot is
  rebuilt when the state machine changed, otherwise only its `published_at`
  moves. A change reaches subscribers within a tick, without the cache's
  10 s throttle.

Publishing is a hand-off, as the command queue is in the other direction.
The hub (`StateHub` in [`src/ipc/server.py`](../src/ipc/server.py)) holds a
lock only to swap a dict reference, compare it with the last one and bump the
version. Every socket write happens on the subscriber's own connection
thread. The render thread never waits for a reader.

### Readers in the web interface

[`web_interface/display_state.py`](../web_interface/display_state.py) holds
one `state.subscribe` connection per web process
(`src.ipc.client.StateSubscription`, a daemon thread, started on the first
read and reconnecting with a backoff of 1 s up to 30 s). A route answers
from the latest pushed snapshot in memory. Before the subscription has one,
the route asks once with `state.get` (0.5 s timeout). When neither works, it
reads the cache keys and the heartbeat file as before:

| Route | From the socket | Fallback |
|---|---|---|
| `GET /api/v3/display/current-status` | `state.display` | `display_current_state` |
| `GET /api/v3/display/on-demand/status` | `state.on_demand`, with `remaining` worked out from `expires_at` now | `display_on_demand_state` |
| `GET /api/v3/plugins/installed` (`runtime`), `/plugins/state`, `POST /plugins/state/reconcile` and the startup reconciliation | `state.plugins` + `state.loop` | `plugin_runtime_snapshot` + `display-heartbeat.json` |
| `GET /api/v3/health` (`checks.display_loop`) | `state.loop` | `display-heartbeat.json` |

Each answer says where it came from: `source: "socket" | "cache"` (or
`"heartbeat_file"` for the health check).

The SSE display stream (`/api/v3/stream/display`) reads the preview frame
file, not a cache key, so it does not change.

**The same verdicts either way.** The socket's answers are judged by the
rules the cache readers apply (#726):

- the runtime view is `stalled` when the render loop's heartbeat age is at
  least `HEARTBEAT_STALE_SECONDS` (60 s, the health check's threshold), and
  then reports no per-plugin facts;
- it is `stale` when the snapshot is older than its `stale_after` (the
  publisher thread stopped);
- with no beat yet, the snapshot is judged on its own;
- there is no pid check, because the display that answered is alive;
- a `display` section the render thread has not refreshed for 120 s reads
  as unknown, as the cache key does once it ages out.

The age a reader uses is the age the display measured, plus the time since
the snapshot arrived.

### Fewer SD writes

The cache keys are still written, for one release, as the fallback. While
the socket serves the readers, the display writes two of them less often.
"Serves the readers" means a subscriber is connected, or a `state.get` came
within the last 60 s (`StateHub.readers_active()`):

- `display_current_state` is no longer written on every mode change: once
  every 60 s (`CURRENT_STATE_RELAXED_REFRESH_SECONDS`, inside the readers'
  120 s `max_age`), and at once when `is_display_active` or
  `on_demand_active` changes.
- `plugin_runtime_snapshot`'s refresh goes from 60 s to 120 s
  (`RELAXED_REFRESH_INTERVAL`), and the snapshot says so in its own
  `refresh_interval` and `stale_after` (360 s). Changes are still written at
  once, at most every 10 s.

`display_on_demand_state` is written only on events, so it is unchanged.
The heartbeat file is on tmpfs, so it costs no SD writes, and it stays: the
automatic update's health check reads it.

This is safe because the relaxed rate only applies while readers are using
the socket. If they stop (the web interface loses the socket, or is stopped),
the next publish after the reader window writes a changed mode at once, and
the runtime refresh goes back to 60 s. A fallback reader in that window sees
a mode up to 60 s old, never one older than its `max_age`.

Measured with fake clocks (`test_cache_writes_per_minute_with_and_without_socket_readers`
in `test/test_state_stream_readers.py`), for a rotation of 15 s screens:

| Key | Writes/min, no socket readers | Writes/min, socket readers |
|---|---|---|
| `display_current_state` | 4.0 | 1.0 |
| `plugin_runtime_snapshot` | 1.0 | 0.5 |
| Total | 5.0 | 1.5 |

That is 70% fewer writes for these keys: about 2,200 a day instead of 7,200.
Shorter screens save more, because the old rate followed the mode changes.
A display that rarely changes mode (one plugin, a long live game) saves less. Plugin
data caches, the error snapshot and font usage are written by other code
and are not affected.

## How the display applies a command

The server's threads never touch rendering. A connection thread parses the
request, validates it against the contract, and then does one of two things:

- For a command that changes the panel, it puts a `QueuedCommand` on a
  bounded queue (16 entries) and answers with the ack, or, for an awaited
  command, with the outcome the render thread reports back through the
  command's `CommandOutcome`.
- For a query, it answers from a status snapshot the display provides
  (`DisplayController._control_status`). The snapshot only reads attributes.

The render thread drains the queue in `_poll_on_demand_requests()`:

- An on-demand command goes to `_handle_on_demand_request()`, which also
  handles plugins' own requests. The two paths share all of their code:
  activation, the request-id guard, error publishing, and resuming the
  rotation afterwards.
- `brightness.set` is applied there and then (`_apply_control_brightness`),
  and the current frame is pushed again so the panel shows it.
- `plugin.reload` starts at the top of the next loop pass, the place where
  plugins are enabled and disabled live, because there no `display()` and no
  Vegas iteration is on the stack (`_apply_pending_plugin_reloads`). Until
  then the current screen ends early, as it does for a WiFi notice: the
  frame loops, the dwell and Vegas's interrupt check all treat a pending
  reload as a reason to stop (the frame loops through the Arbiter's
  mid-screen check, `Source.RELOAD`; the dwell through
  `_plugin_reload_pending`).
- Only the quick half of the reload runs on the render thread
  (`_start_plugin_reload`): the plugin's modes leave the rotation, its
  config subscription is dropped, and `PluginManager.detach_plugin` takes
  the instance out of `plugins`. After that nothing new calls the old
  instance: no `update()`, and no Vegas fetch. The rotation then advances
  (Vegas resumes its strip), and frames keep coming.
- The slow half runs on a `plugin-reload-<id>` thread (`_PluginReloadJob`).
  It waits for the plugin's lock, then tears the old instance down
  (`unload_detached_plugin`) and loads the new one (`reload_plugin`). The
  lock can be held for seconds by a Vegas render of the old instance. On
  ledpi the render thread used to wait for it here, and a football reload
  froze the panel for 3.0 s.
- The new instance joins the rotation between two frames
  (`_finish_plugin_reloads`, from `_service_pending_changes` or the top of
  the loop). Its modes go back to their old places, Vegas is told to fetch
  it again, and the command is answered.
- While the plugin reloads, it is out of the rotation. Vegas scrolls what
  its strip already holds of it. An on-demand request for it gets
  `plugin-reloading`. A config reconcile neither loads it a second time nor
  unloads it mid-load; a disable saved meanwhile is applied once the
  reload is done. A second reload of the same plugin runs after the first.

Draining the queue costs no disk read, so it has no floor. A queued command
also lets `_service_pending_changes()` skip its own 0.25 s floor.

### Waking the render thread (stage 2)

Stage 1 made the socket answer, but not land sooner: a queued command waited
for the same polls the mailbox did. Measured on ledpi (Pi 4, 24 fps Vegas),
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
dwell, and within one frame in Vegas and on a scrolling screen. Commands still run only on the render thread: the
connection threads only queue them and set the event. The one exception is
the slow half of `plugin.reload` (tearing down and loading the plugin),
which runs on its own thread. Every change to the display's state still
happens on the render thread.

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

**Exactly once.** A request goes over the socket once. The web route sends
it again only while no display is listening (see "Without the socket"), so
a display gets it at most once; a start with a request id the display has
just processed is dropped anyway (`on_demand_request_id`). The persisted
`display_on_demand_processed_id` guard against a mailbox replayed after a
restart went with the mailbox.

## Without the socket (stage 5)

The client tells a request the display never had from one it had and then
failed. `ControlError.sent` is True once the whole request was written to a
connected display; a refusal the display sends before reading anything
(`forbidden`, too many connections) carries no request id, and leaves it
False. `src.ipc.client.display_not_listening()` picks out the one case a
later retry can fix: nothing is listening (`no_socket`, `refused`) and the
request was never sent.

| What happened | Example reasons | On-demand start | On-demand stop | `errors.clear` |
|---|---|---|---|---|
| No display listening | `no_socket`, `refused` | service stopped: `400` without `start_service`; with it, start the service and answer `202` (`status: "starting"`) at once; the dispatcher sends the request until the display takes it (up to 45 s), else `start-timeout`. Service running (still starting): the same `202`, sent for up to 10 s | `503` ("not running" / "may still be starting"); with `stop_service` the service is stopped and the route succeeds | `503` ("not running"; its errors are the last run's, and the next run starts with none) |
| A display too old to know the command | `unknown_command`, `unsupported_version` | `503` | `503` | `503`, "restart it" |
| No socket in this process | `disabled`, `unsupported` (Windows, `LEDMATRIX_CONTROL_SOCKET=off`) | `503` | `503` | `503` |
| The display had it and failed, turned it away, or never answered | `busy`, `invalid_args`, `internal`, a timeout, a hang-up, `bad_response`, `forbidden` | `503` (`400` for `invalid_args`) | `503` (with `stop_service`: stopped anyway) | `503` |

Every error answer carries `socket_error` (a reason code, or `other`).
Nothing is written to the cache in any of these cases.

**Waiting for a display that is starting.** The socket comes up when the
display's run loop starts, after every plugin has loaded, which can take
longer than a client waits (the MQTT bridge gives up after 15 s). So the
start route never waits: it answers `202` with `status: "starting"`, and
hands the request to the web process's one dispatcher
([`web_interface/on_demand_dispatch.py`](../web_interface/on_demand_dispatch.py)).
Its worker thread sends the request every 0.5 s while nothing is listening,
until the display acknowledges it or the wait runs out (45 s after a cold
start, `START_WAIT_SECONDS`; 10 s for a service that was already running,
`ON_DEMAND_SOCKET_WAIT_RUNNING_SECONDS`). Any other failure ends it at once.
One start is pending at a time: a newer start replaces it, and a stop
cancels it (the stop then succeeds even with no display listening, and
reports `cancelled_request_id`).

The outcome is reported where clients already look:
`GET /display/on-demand/status` answers the pending start's state
(`source: "web"`, `status: "starting"`, or `status: "error"` with `error:
"start-timeout"` or the socket's reason) until the display publishes
something newer, and `GET /display/current-status` adds it as
`on_demand_pending`.

A delivered start keeps reading as `status: "starting"`, now with
`delivered: true`, until the display publishes the state that answers it.
The display acknowledges a start as soon as its socket opens, but its run
loop acts on it only after the first screen is built (about 5 s on ledpi,
while Vegas renders its first strip), and meanwhile it publishes its own
idle state. The display's on-demand state names the request it answers
(`request_id`), so "answers it" means the id matches. A display older than
that field answers with any state published after the delivery. Either
way the delivered start is reported for at most 30 s
(`DELIVERED_SHOWN_SECONDS`).

Brightness and plugin reload never had a mailbox: without the socket, the
config watcher applies the saved brightness and a reload becomes the
restart banner, as before.

### The mailboxes are gone

| Former mailbox | Last written by | Now |
|---|---|---|
| `display_on_demand_request` | the web interface on fallback (stage 4); plugins that predate `BasePlugin.request_on_demand()` | not read. A write is dropped by `CacheManager.save_cache` (`RETIRED_MAILBOX_KEYS`) and logged once per writer as a warning that names the plugin when it can be told (the plugin instance on the call stack, else the `plugin_id` in the request) |
| `plugin_error_clear_request` | the web interface on fallback (stage 4) | not read; a write is dropped and logged the same way |

A file left on the SD card by an older version is never read again, so it
is harmless. `MailboxWatch` and
`CacheManager.file_signature`, which made a look at a mailbox one `stat()`,
went with them.

The four plugins that wrote `display_on_demand_request` (birdnet-go,
mqtt-notifications, on-air, pomodoro-timer) use `request_on_demand()` on a
core that has it, and fall back to the mailbox only when that method is
missing or answers `None` (no display in the process, or a full queue).
On a stage-5 core such a fallback write is dropped with the warning above.

### Plugins in the display process

A plugin asks for the screen with `BasePlugin.request_on_demand()` and gives
it back with `end_on_demand()` (see "On-demand display" in
[PLUGIN_API_REFERENCE.md](PLUGIN_API_REFERENCE.md)). Neither goes through
the socket or a file: `PluginManager` hands the on-demand request,
marked `source: 'plugin'`, to `DisplayController.submit_plugin_on_demand`,
which queues it in memory (at most `PLUGIN_ON_DEMAND_QUEUE_SIZE`, 32) from
whatever thread the plugin called on, and wakes the render thread through
the socket's queue flag (`ControlServer.wake()`). The render thread applies
it in `_drain_control_commands`, after the socket's commands, through the
same `_handle_on_demand_request`, so it lands within a frame like a socket
command. Without a socket it lands on the next pending-changes pass. A
plugin's stop ends only a session that plugin owns.

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
- **Full queue.** When the queue is full, the client gets `busy`, and the web
  interface answers `503`. A full queue means the render thread
  is stuck, and the systemd watchdog deals with that.
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
  as before. The web interface then cannot send it commands (the routes
  answer `503`), and reads the cache keys and the heartbeat file.
- **Subscribers (stage 3).** A `state.subscribe` connection gives its request
  slot back and takes one of 4 subscriber slots (`MAX_SUBSCRIBERS`). A fifth
  gets `busy`. So a few browsers' web processes holding streams can never
  use up the 8 slots that commands need. Each subscriber has its own thread.
  A send that cannot finish within the 2 s IO timeout (a reader that stopped
  reading) drops that subscriber. Nothing else waits for it, and the render
  thread only publishes to the hub. `close()` wakes every subscriber, so
  they end at once.

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
import a plugin it was not running. Stages 2 and 3 changed none of the
access rules above. The state stream carries what the cache keys already
held, and those are readable by the same group. A subscriber goes through
the same connect-time and peer-credential checks as any other connection.

**Development.** A display that is not root and cannot write to
`/run/ledmatrix`, such as `python3 run.py -e` from a checkout, serves the
socket at `$TMPDIR/ledmatrix-<uid>/control.sock`. That directory is private
(`0700`), and the server refuses it if another user owns it. The web
interface, run by the same user, looks there after `/run/ledmatrix`. The test
suite sets `LEDMATRIX_CONTROL_SOCKET=off` (`test/conftest.py`), so a run on a
device never touches the live display.

## Stage plan

1. **On-demand, with acks (done, #706).** Contract, server, client.
   `on_demand.start`/`stop`/`status`, `hello`, `ping`. The REST routes tried
   the socket first and reported `transport: "socket" | "mailbox"` (plus
   `socket_error` on fallback).
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
3. **A state stream (done).** `state.get` (a versioned snapshot) and
   `state.subscribe` (the snapshot, then pushed changes and keepalive ticks)
   carry the current mode, the on-demand state (including the outcome of an
   acked on-demand command), the brightness, the plugin runtime snapshot and
   the render loop's liveness, all served from memory (see "The state
   stream"). The web interface's readers use it and fall back to the cache
   keys and the heartbeat file. `display_current_state` and
   `plugin_runtime_snapshot` are written less often while it serves them.
   The keys remain for one release.
   - Left for later: the outcome of a `plugin.reload` that answered
     `pending` is visible only as the plugin's new `loaded_version` in
     `state.plugins`, not as an event of its own.
   - Left for later: the SSE display stream reads the preview frame, not
     state, so nothing relays the stream to the browser yet. A browser still
     polls the REST routes, which now answer from memory.
   - Left for later: the store's install of an already-enabled plugin, and
     an uninstall that keeps its config, still answer `restart_required`.
     They can now use a load/unload command and report the result the same
     way the update route does.
4. **The mailboxes become a fallback (done).**
   - The web interface writes a mailbox only when the socket could not carry
     the request (`should_fall_back`); a display that had it and failed is
     answered as that.
   - `errors.clear` replaces `plugin_error_clear_request` as the way a clear
     reaches the display.
   - The display looks at the on-demand mailbox once a second while the
     socket is up, reads either mailbox only when its file changed, and logs
     who still writes the on-demand one.
   - Not changed, deliberately: config saves (the schedule, the dim
     schedule, plugin settings) still reach the display through
     `config.json` and its watcher, which is the setting itself rather than
     a message; see `config.reload` under stage 2. The preview viewer marker
     (`/tmp/led_matrix_preview_viewer`) is a presence signal the display
     already stats at most once a second. Plugin health and metrics resets
     write the persisted record the display publishes and do not reach the
     running display (their routes say so); they are not mailboxes.
5. **Remove the mailboxes (done, the release after 3.8.1).** The web
   interface no longer writes `display_on_demand_request` or
   `plugin_error_clear_request`, and the display no longer reads them (see
   "Without the socket"). When no display is listening, the start route
   starts the service if asked, answers `202`, and the web process's
   dispatcher sends the request once the socket is up; every other failure
   is an error the route reports. A write to
   either key is dropped with a one-time warning naming the writer. The
   display still writes `display_current_state`, `display_on_demand_state`
   and `plugin_runtime_snapshot`: the web interface reads them whenever the
   socket cannot answer (a stopped or starting display, a web user not yet
   in the socket's group, a platform without Unix sockets), and
   `display_on_demand_config` is the display's own record for resuming a
   session after a restart. Retiring those keys is left for later.

## Checking it on a device

```bash
ls -l /run/ledmatrix/control.sock                 # srw-rw---- root ledmatrix
sudo journalctl -u ledmatrix | grep "Control socket"
curl -s -X POST localhost:5000/api/v3/display/on-demand/start \
  -H 'Content-Type: application/json' -d '{"plugin_id":"clock","duration":20}'
# ... "transport": "socket"
```

On an error, `socket_error` gives the reason. `no_socket` means the display
is stopped or still starting. `refused` usually means the web user is not in
the socket's group, which takes effect when the web service restarts after
the user is added. `busy`, `timeout` and the like mean the display had the
request and did not take it. Nothing is ever written to a mailbox.

An error clear:

```bash
curl -s -X POST localhost:5000/api/v3/errors/clear \
  -H 'Content-Type: application/json' -d '{"all":true}'
# ... "applied": true, "transport": "socket"
sudo journalctl -u ledmatrix | grep -E "Cleared .* plugin error|retired"
```

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

The state stream:

```bash
curl -s localhost:5000/api/v3/display/current-status    # ... "source": "socket"
curl -s localhost:5000/api/v3/health | python3 -m json.tool | grep -A3 display_loop
python3 - <<'EOF'
from src.ipc import client          # run from the project directory
snap = client.state_get()
print(snap['version'], snap['epoch'], snap['loop'], snap['state']['display'])
EOF
```

`"source": "cache"` means the web interface could not use the socket: the
display is stopped, predates stage 3, or the web user is not in the
socket's group.
