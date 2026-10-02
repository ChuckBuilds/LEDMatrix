# Changelog

Notable changes to the LEDMatrix core. The version below is the value of
`src.__version__`, which the plugin loader reports to compatibility checks and
which plugin manifests reference via `ledmatrix_min_version`.

**Why this file exists:** the plugin monorepo bundles fallback copies of several
core modules (see `docs/plugin-development/08-shared-sports-code.md` in
[ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins)). A plugin
may delete its bundled copy only when its manifest floors on the first core
release that ships the module — which requires module additions to be recorded
here, against a version number. When you add a module plugins will import via
`src.*`, note it in the Unreleased section and bump `src/__init__.py` in the
release that ships it.

**Use `ledmatrix_min_version` in manifests, not `ledmatrix_min`.** The loader
accepts both, but the store flags the old spelling as deprecated
(`store_manager.py`) and only the new one is in `schema/manifest_schema.json`.

## Unreleased

### Fewer SD-card writes from the cache

- **An unchanged `CacheManager.set()` no longer rewrites the file.**
  `DiskCache` already skipped a payload identical to the last one it wrote,
  but `set()` stamps every record with the current time, so for `set()` the
  payload never matched and every unchanged re-save was a full rewrite. The
  comparison now leaves out a header-first record's timestamp (the `ttl` and
  the data still count), and the newer timestamp is kept in the file's mtime
  instead: a skipped save touches the file to the record's timestamp, and a
  real write pins mtime to the record's own timestamp. Every reader ages a
  record from the newer of the two -- `DiskCache.get`, its header-only
  staleness check, and the record it returns, whose `timestamp` is the newer
  value, so `CacheManager.get`, the memory tier and plugins reading
  `record['timestamp']` all agree; the retention sweep and the web UI's cache
  list already used mtime. The mtime is trusted at most an hour past the
  record's own timestamp, and unchanged data is rewritten once an hour, so a
  file copied without its mtime reads at most an hour fresher than its
  contents. 100 identical `set()` calls of a 32 KB record: 100 writes before,
  1 after.
- **Plugin metrics are one record, written at most once a minute.** The
  resource monitor wrote a `plugin_metrics:<id>` record per plugin, each at
  most every 30 s: two writes a minute per plugin, 28 on a fourteen-plugin
  rig. Every plugin's metrics now go in one `plugin_metrics_snapshot` record
  (`{"schema": 1, "plugins": {id: record}}`, each record shaped as before),
  written at most once a minute. `GET /api/v3/plugins/metrics` and
  `/plugins/metrics/<id>` return the same fields; the numbers can be up to a
  minute old instead of 30 s. A plugin the snapshot does not have yet is
  still read from its old `plugin_metrics:<id>` record, which nothing writes
  any more and the cache's retention removes. Each write starts from the
  snapshot on disk, so plugins the display has not run since a restart keep
  their numbers, and a reset from the web UI sticks for a plugin the display
  is not running, as it did. A plugin with no call for 30 days is dropped from
  the snapshot, as its record used to age out.

### Garbage-collection pauses in the frame stats

- The display now times every Python garbage collection
  (`src.common.frame_timing.GcMonitor`, installed once per process from
  `gc.callbacks`). The collector stops every thread while it runs, and a
  long one looked like any other render stall. A collection of 20 ms or more
  tags the next presented frame `gc`, so `scripts/frame_soak.py` shows its
  late rate under *after work*; the stats file gains an additive `gc` block
  (collections and seconds per generation, the longest, the long ones),
  which the soak report prints as a "Garbage collection" line; and a
  `Render stall` dump says when a long collection ran inside the stall.
  `scripts/render_bench.py` records the same. Diagnostic only: nothing tunes,
  freezes or disables the collector.

### Outlined text: one rasterization

- New `draw_text_outlined(draw, xy, text, font, fill, outline_color=(0, 0,
  0), offsets=OUTLINE_SQUARE)` in `src/common/text_helper.py`, with
  `OUTLINE_SQUARE` (the eight-sided outline the scoreboards draw) and
  `OUTLINE_CROSS` (four sides). Outlined text was one `draw.text` per
  outline offset plus one for the text, so FreeType rasterized the same
  string nine times. This rasterizes it once and stamps the mask at each
  offset: the same pixels, about 8x faster per outlined string (Pillow 12.3,
  desktop). `test/test_text_helper.py` compares it with the nine-draw loop
  across the bundled fonts, image and font modes, colours and positions,
  and fails if it stops rasterizing once. Fractional coordinates, multiline
  text, fonts other than a plain `FreeTypeFont`, image modes other than
  RGB, RGBA and L, and a subclassed or replaced `draw.text` take the old
  loop unchanged. A whole-pixel float such as `52.0`, which the scorebugs'
  centring passes, is not fractional.
- `SportsCoreSharedMixin._draw_text_with_outline`, which eight of the nine
  scoreboards inherit for their switch-mode scorebug (ufc has its own), and
  `TextHelper.draw_text_with_outline` now draw through it. Scroll and Vegas
  cards still use each plugin's own `game_renderer.py` loop, so building a
  scroll strip costs the same until the plugins adopt `draw_text_outlined`,
  importing it with an `ImportError` fallback to their own loop (a separate
  ledmatrix-plugins change after a core release ships it).

### Shared fetch service (stage 1)

Core's own HTTP fetch paths now go through one service, so the plugins that
use them get pooling, merging, host budgets and per-plugin request counts
without a code change. Return values, exceptions, cache keys, TTLs and retry
policies are unchanged.

- **What goes through it.** `APIHelper.get`/`post`, `fetch_espn_scoreboard`
  and its date chunks (`src/common/espn_dates.py` -- every scoreboard's live,
  recent and upcoming fetch, and `SportsFetchMixin._fetch_season_directly`),
  `BackgroundDataService` and `BaseOddsManager.get_odds`. Plugins' own
  `requests` calls are not covered yet.
- **Shared connection pools.** Core sessions with the same retry policy mount
  one shared adapter, so the odds managers (one per scoreboard league
  manager), the background service and the APIHelpers reuse one connection
  pool per host. Headers, cookies and auth stay per session.
- **Merged requests.** Identical GETs in flight at once (same URL and query,
  effective headers, timeout and retry policy) go out once; the others get a
  copy of that response or the same exception. `BackgroundDataService`'s own
  request opts out (`share_in_flight=False`): it cancels and replaces fetches,
  and already merges by cache key.
- **Host budgets.** Per-host token buckets, `fetch_service.rate_limits` in
  `config.json` (new optional section in the template). ESPN hosts default to
  20 requests/s with a burst of 200, far above normal traffic; no request waits
  longer than `max_wait_seconds` (2 s). Other hosts are unthrottled.
- **Conditional GET.** A response with `ETag` or `Last-Modified` is kept in a
  small bounded store (64 entries, 4 MB, 1 MB each) and revalidated; a `304`
  is returned to the caller as the original `200`. ESPN sends neither
  validator today, so on ESPN this is dormant.
- **Counters.** Requests, merged, bytes, 304s, errors, HTTP errors, adapter
  retries, throttled requests and seconds waited, per plugin and per host.
  Which plugin made a request comes from a context variable the plugin
  executor and plugin loader set (carried across the background service's and
  `espn_dates`' worker threads), or else from the plugin directory on the
  stack, so a plugin's own threads count too. The display publishes them to
  the shared cache at most once a minute on change; read them at
  `GET /api/v3/plugins/fetch-stats`.
- `fetch_service` is a core config section (`src/core_config_keys.py`).

### Control socket (stage 2: wake-ups, brightness, plugin reload)

- **Socket commands land at once.** Stage 1's socket was no faster than the
  mailbox: a command waited for the static screen's 1 s frame sleep, the
  dwell's 0.25 s tick, or Vegas's interrupt check every 10 frames (about
  0.4 s on a Pi 4). The render thread now waits on the socket's queue
  instead of sleeping, and Vegas checks the queue every frame, so an
  on-demand start or stop is applied within about a millisecond on a static
  screen or in a dwell, and at the next frame in Vegas or on a scrolling
  screen. Commands still run only on the render thread. The file mailbox
  keeps its old delays. Idle CPU is unchanged in practice: the waits are
  timed `Event` waits with the same wake-ups as the sleeps they replace
  (about 25 µs more per wait, measured).
- **`brightness.set`.** Saving a brightness (`POST /api/v3/config/main`)
  also puts it on the panel at once over the socket, instead of when the
  display's config watcher next reads `config.json` (up to about 2 s). The
  response says `brightness_transport: "socket"`, or `"config"` with
  `brightness_socket_error` when the watcher applies it as before. The
  command itself writes nothing; the dim schedule still applies on top.
- **`plugin.reload`.** Updating an enabled plugin from the store no longer
  asks for a display restart when the display can reload it: the update
  route asks the display over the socket, which reloads the plugin on its
  render thread at the start of the next screen (its modes keep their place
  in the rotation) and answers once the new code runs. The response then
  says `restart_required: false`, `reloaded: true` and `reloaded_version`.
  Without the socket, with a display older than this command, or when the
  reload fails, the route answers `restart_required: true` as before, with
  `reload_error` giving the reason. The route now also uses the manifest's
  plugin id (the one the display runs it under) for this decision, so an
  update through a registry alias of an enabled plugin no longer reports
  that no restart is needed.
- Both commands answer with the render thread's outcome, or `pending` when
  it did not get to them in time (2 s and 10 s). Socket protocol version is
  still 1: new commands are additive, and an older display answers
  `unknown_command`, which the web interface falls back from. The security
  model is unchanged: the same `0660` group socket and peer-credential
  check. `config.reload` was not added; see
  `docs/IPC_CONTROL_SOCKET.md` for why.

### New modules

- `src/common/fetch_service.py` -- the fetch service above. Core-internal in
  this release: plugins reach it through `APIHelper` and `espn_dates`, and
  should not import it directly until a plugin-facing API ships (stage 3), so
  it sets no `ledmatrix_min_version` floor.

### Tooling

- Golden trace tests for the display loop. `test/test_run_loop_golden.py`
  runs the real `DisplayController.run()` against fake plugins on a fake
  clock (`test/_run_loop_harness.py`), with no hardware and no real sleeps,
  and compares which mode was shown, for how long and why it ended with
  `test/fixtures/run_loop_golden/`. It has 15 scenarios: rotation,
  empty and failing modes, dynamic duration, live priority, on-demand
  (including pinned and resumed after a restart), the schedule and dim
  schedule, WiFi notices, sync follower and Vegas. The whole file runs in
  about a second. This is stage 1 of restructuring `run()`, described in
  `docs/RUN_LOOP_REDESIGN.md`. The other part of stage 1 is internal and
  changes no behaviour: twelve blocks of `run()` move into named helpers
  (`_dispatch_first_frame`, `_resolve_durations`, `_resolve_active_mode`,
  `_needs_high_fps`, `_advance_after_screen` and others), and the traces are
  identical before and after the move.

### Fixes

- A plugin reload after a store update (`plugin.reload`, #720) no longer
  freezes the panel during Vegas. On ledpi a football reload froze it for
  3.0 s (`Render stall over: no frame for 3043ms`). The reload ran on the
  render thread, and its unload waited for the plugin's lock. The strip's
  prefetch thread held that lock while it rebuilt the old instance's Vegas
  content. The render thread now only takes the plugin out of the rotation
  and out of the plugin manager (`PluginManager.detach_plugin`). A
  `plugin-reload-<id>` thread waits for the lock, tears the old instance
  down and loads the new one, and the new instance joins the rotation
  between two frames. In a test with a 3.0 s render holding the lock, the
  longest gap between frames went from 3017 ms to 9 ms. The reply still
  reports the real outcome, the modes keep their places in the rotation,
  and Vegas fetches the plugin again. While it reloads, an on-demand request
  for the plugin is refused (`plugin-reloading`), and a config reconcile
  neither loads it twice nor unloads it mid-load. A Vegas fetch that waited
  out a reload for the lock skips the old instance.
- The schedule-off blank and the WiFi notice no longer start with a
  scroller's leftovers. Both are drawn by the display controller rather than
  dispatched to a plugin, so #716's handover never reached them: drawn while
  the last scroll's state was still set, the blank went out with the
  ticker's lagging rows on a scan-compensated panel and stayed up for its
  60 s dwell, and the notice's redraws (which #712 now shows over a running
  scroller or Vegas) were counted as 0.5-1 s freezes and logged as a
  `Render stall ... mid-scroll`. The controller now ends the scroll state
  before drawing either.
- A plugin whose `display()` raises now opens its circuit breaker. The first
  frame of each screen goes through the plugin executor, which caught the
  exception and returned False. The display read that as "no content" and
  recorded a success, which reset the plugin's failure streak, so the breaker
  never tripped. The plugin stayed in rotation and logged a traceback on
  every screen. The raise now counts as a failure, so after three in a row
  the plugin leaves rotation until the cooldown ends, the same as a raising
  `update()`. The display still moves straight on to the next mode. A hung
  `display()` is still recorded once, as a hang.
- A WiFi notice (such as "Connected to HomeNet" or "AP mode on") now shows
  within about a second of being posted. It was only checked between
  screens, so a 5 s notice posted during a 20 s screen expired before that
  screen ended and never appeared. The screen it interrupts comes back in
  full once the notice ends. When Vegas stops scrolling for a notice, the
  notice is what shows next, and Vegas resumes after it; before, a rotation
  screen showed instead and the notice expired behind it. An active
  on-demand session still holds the panel until it ends.
- A game that goes live now takes over the panel within about a second.
  Live priority was only checked between screens, so a game that went live
  during a 30 s screen waited for that screen to end. The frame loops and the
  dwell sleep now check too, at most once a second, and not while an
  on-demand session is running or a live game is already showing. When Vegas
  stops for a live game, the game is the next screen. Before, one rotation
  screen showed first and the game came after it. Each check also asks each
  plugin `has_live_content()` once, where a plugin registered under several
  modes used to be asked once per mode.
- The display schedule turns the panel off at exactly the end time. A window
  now runs from its start time up to, but not including, its end time: with
  07:00-23:00 the panel is on at 07:00 and off at 23:00. Before, the end
  minute counted as on, and because the schedule is checked once a minute,
  the panel went off at 23:00 or at 23:01 depending on when in the minute
  that check ran. Windows that cross midnight and per-day schedules follow
  the same rule, and so does the dim schedule.
- An on-demand session that ends during scheduled-off hours, by expiring or
  being stopped, blanks the panel within about a second. It used to stay on
  until the next minute, because the once-a-minute schedule check had
  already run that minute and the session had overridden its answer.

### Scrolling

- A scoreboard in scroll mode no longer freezes the panel at the start of a
  recent or upcoming turn whose games have not changed.
  `SportsScrollDisplayManager.prepare_and_display()` redrew every card on
  every turn while the render thread waited (~1.4s for seven football cards
  at 192x48 on a Pi 4); it now rewinds the strip it built last time when
  nothing it is drawn from has changed (the games, rankings, config, panel
  size and date), and redraws it at least every 10 minutes. Each slate (game
  type and leagues) keeps its own display, so leagues that take turns
  (`nfl_recent`, `ncaa_fb_recent`) each find their strip again: up to 4 per
  game type, with at most 6MB per plugin of strips kept for slates not on
  screen. The first turn of each slate after a start, a slate whose games
  changed, live strips and a turn with no games are drawn as before.
  `get_scroll_display()` and `_scroll_displays` still answer with the strip
  on screen; a sport's `prepare_scroll_content()` is no longer called on
  every turn.

### Web preview: less work per frame

- Mid-scroll, `update_display()` no longer checksums every frame. The
  checksum (`tobytes()` plus `adler32` over the whole framebuffer: ~0.17 ms a
  frame at 256x64 on a Pi 4, so roughly twice that at 512x64 and well under
  0.1 ms at 128x32) fed only the dirty-tracking skip, which never applies
  while scrolling, and the preview snapshot's changed-frame check. The
  snapshot now asks its policy first and hashes the frame only when a write
  or touch could follow. That changes no snapshot decision:
  `snapshot_policy.decide()` is monotone in `frame_changed`, and a test holds
  it to that. Two small differences on the panel: the first static frame
  after a scroll is pushed even when it matches the scroll's last frame (one
  extra swap), and the frame on which a scroll that never said it stopped
  times out is presented at a hold of 1 rather than the scroll's hold.
- With the web preview open, the display writes the snapshot at most once a
  second (`snapshot_policy.VIEWER_INTERVAL`, was 0.2 s). The preview already
  showed at most one frame a second: its SSE stream re-read the file once a
  second, so four PNG encodes in five were overwritten unread. The stream now
  checks the file's mtime every 0.25 s (new `VIEWER_POLL_INTERVAL`) and sends
  each frame soon after it is written, so the preview stays about as fresh;
  it still touches the viewer marker once a second, and with no snapshot
  file it still sends its placeholder once a second. A screen that animates
  faster than once a second without marking itself as scrolling (a GIF, say)
  was encoded on the render thread up to five times a second while the
  preview was open, 12-14 ms each at 512x64 on a Pi 4; now at most once.
- The snapshot PNG is written at `compress_level=1`. On a desktop that
  encoded a text-dense 512x64 frame in about half Pillow's default time, into
  a larger file (12 KB instead of 7 KB); sparser frames gain less.
- `scripts/frame_soak.py --preview` soaks are not comparable across this
  change: an open preview now costs at most one encode a second, not up to
  five. Take both sides of an A/B pair on the same side of it.

### Scroller-to-static handovers

- A static plugin screen that follows a scroller no longer starts with the
  scroller's leftovers. Nothing ended the scroll state at a handover; it
  expired 2 s after the last scroll frame. So on a panel with scan-order
  compensation the static screen's first frame went out with the lagging
  rows (the bottom half on a 96x48 panel) taken from the ticker's last
  frame: for the whole second it stays up after a scroll at one frame per
  refresh, and for its first refresh after a slower, held one. The display
  controller now calls the new
  `DisplayManager.end_scroll_for_static_screen()` just before such a
  screen's first `display()`, so the frames that call draws go out as
  drawn, in one swap each, and `set_scrolling_state(False)` once it
  returns. The scroll state and its frame hold stay until then, so the
  handover is still timed, against the scroller's own pacing: late-frame
  counts are unchanged.
- The phantom ~1 s freeze when a static plugin screen follows a scroller is
  no longer recorded: the 1 Hz loop's second frame was timed as a frame of the old
  scroll, in the soak's freezes and as a `Render stall` in the log. On ledpi
  that was 17 of 31 `Render stall over` lines (2026-09-15 to 10-01).
- A screen's first frame is tagged `handover` in the frame stats, every
  turn's, also when the rotation comes back to the same mode. A gap of
  250 ms or more before it is counted in the new `handover_freezes`
  (additive; the schema version is unchanged), not in `freezes` /
  `freeze_by`, and `frame_soak.py` prints it as "Handover gaps": a
  scroller rebuilding its content at the start of a turn shows up there.
  **Freeze counts from soaks before and after this change are not
  comparable.** A stall dump taken while that first `display()` is still
  drawing says `in a handover gap` instead of `mid-scroll`, and the call
  runs on a thread named `display-<plugin id>`.

## 3.8.0

Live Vegas elements: plugin content that keeps changing while it scrolls
(scores on the scoreboards' cards, the flight map's gliding aircraft, the
weather radar's loop), with live games kept in the ticker by default. Also
stable/beta update channels, the display control socket, the systemd
display watchdog, optional web login, ES-module web UI pages, sports
consolidation stage 4, and the removal of the 35 plugin APIs deprecated
since 3.5.0 (see Removed).

### New modules

A plugin may import these via `src.*` once it floors on 3.8.0 (and should
guard the import, since the loader's version check is advisory).

- `src/plugin_system/vegas_elements.py` -- `VegasElement`, the unit a
  plugin's `get_vegas_elements()` returns (also re-exported from
  `base_plugin`). See "Live Vegas elements" below.
- `src/plugin_system/testing/vegas.py` -- the harness for those hooks:
  `render_vegas_elements`, `check_vegas_elements`, `render_vegas_timeline`.
- `src/common/sports_vegas.py` -- what a scoreboard needs for live cards:
  `game_key`, `game_fingerprint`, `dedupe_games`, `VegasCardCache`,
  `StickyOdds`, `finished_games`.
- `src/common/sports_plugin_host.py`, `sports_live_scroll.py`,
  `sports_display_rules.py` and `sports_font_path.py` -- sports
  consolidation stage 4; see "New modules (sports consolidation stage 4)"
  below.
- `src/display_watchdog.py`, `src/plugin_system/plugin_catalog.py`,
  `src/plugin_system/plugin_runtime.py`, `src/plugin_system/field_model.py`
  and the `src/ipc/` package -- new core modules (described below) that
  plugins do not normally import.

### Web UI: ES modules and one form model (stage 1)

- The web UI gains a native ES-module layer, loaded with
  `<script type="module">` and served as-is (no bundler, nothing built on
  the Pi): `static/v3/js/core/` (`boot.js`, `registry.js`, `api.js`,
  `facade.js`) and `static/v3/js/pages/`. `window.LEDMatrix` is its one
  global: `api`, `pages`, `notify`, `escape`, `widgets` and `deprecate`, the
  last keeping old `window.*` names working as aliases that warn once.
- Tab partials can become page modules: a partial whose root says
  `data-page="<name>"` carries no inline script, and the page registry calls
  the page's `init` once when htmx swaps it in and `destroy` when it is
  swapped out, aborting a signal that removes its listeners and cancels its
  requests. The Cache tab is converted as the reference
  (`js/pages/cache.js`); `window.deleteCacheFile` remains as an alias.
- Static `.js` files are always served as `text/javascript`, which module
  scripts require, and a `.js` request without the `?v=` content version
  (how modules import each other) is revalidated instead of cached as
  immutable for a year.
- `src/plugin_system/field_model.py`: `build_field_model(schema, config)`
  describes a plugin's config form as one JSON field model. Nothing renders
  from it yet; `test/test_field_model_parity.py` checks it names exactly the
  form controls and starting values the `render_field` macro emits, for every
  schema available (all 46 official plugins, when a checkout is present).
- `docs/WEB_FRONTEND_ARCHITECTURE.md`: the target architecture, the
  page-by-page migration order, and how forms switch to the model and to
  JSON submit behind a flag.

### Control socket (stage 1: on-demand)

- **The display now serves a control socket**,
  `/run/ledmatrix/control.sock`. It carries versioned JSON commands, one per
  line, and every command gets an answer
  ([docs/IPC_CONTROL_SOCKET.md](docs/IPC_CONTROL_SOCKET.md)).
  - On-demand start, stop and status are the first commands, plus `hello`
    (version negotiation) and `ping`.
  - Start and stop are acknowledged once the render thread has them queued.
    The render thread applies them through the same handler as the file
    mailbox, at its next on-demand check. On a scrolling screen that is the
    next frame (the mailbox waits up to 0.25 s). On a static screen it is up
    to 1 s, the same as the mailbox.
  - The server's threads never touch rendering. Garbage, oversize messages
    and slow or vanishing clients are answered or dropped without blocking the
    display.
  - New core modules: `src/ipc/contract.py`, `server.py` and `client.py`.
    They are internal, not a plugin API.
- **`POST /api/v3/display/on-demand/start` and `/stop` try the socket
  first.** On any failure (the display is stopped or predates the socket, a
  timeout, a refusal), they write the `display_on_demand_request` mailbox
  exactly as before. The response's new `transport` field says which path
  was used (`"socket"` or `"mailbox"`), and `socket_error` gives the reason
  for a fallback. Both paths carry the same `request_id`, so a request that
  arrives both ways runs once. The mailbox, and the plugins that write it
  directly, keep working for at least one more release.
- **Permissions.** The socket is `0660` and owned by the group the two
  services already share (the cache directory's group, `ledmatrix` on an
  installed device). On Linux the server also checks each connection's
  `SO_PEERCRED`: root, the display's own user, or a member of that group.
  `/run/ledmatrix` comes from the existing `RuntimeDirectory=` (#687), or the
  display creates it as root under an older unit, so no installer or unit
  change is needed. `LEDMATRIX_CONTROL_SOCKET` overrides the path for both
  processes, or turns the socket off with `off`. A non-root dev run uses a
  private per-user path under the temp directory.

### Scroll speed

- The Vegas Scroll Speed slider now says what the panel will do with the speed
  it is on, and offers the nearest smooth ones to click. Only speeds that advance
  a whole number of pixels per refresh look smooth, and which those are depends
  on the panel (`GET /api/v3/config/scroll-speed-advice`, built on
  `scroll_config.speed_advice()`; it uses the refresh the display measured, not
  the `limit_refresh_rate_hz` cap). The slider steps by 1 px/s instead of 5.
- The default 50 px/s no longer snaps to a stepped 48 px/s (2 px every 5
  refreshes, 24 fps) on a 120 Hz panel: `solve_crisp()` now prefers 60 or 40 px/s,
  which move one pixel at a time. 100 Hz panels are unaffected.

### Update channels

- Devices no longer pick up every merge to `main`. A new setting,
  `auto_update.channel`, picks what Update Code and the weekly automatic
  update install: `stable` follows the newest release tag (`vX.Y.Z` by
  semantic version; pre-releases and other tags are ignored) and checks it
  out with a detached HEAD, and `beta` follows `main` as every device did
  before. New installs default to `stable` (config template and installer).
- Nobody is moved backwards. A device running code newer than the newest
  release, which is any device that pulled `main` since that release, keeps
  following `main` until a release contains its commit, then moves to it and
  follows releases. A config written before channels existed behaves the
  same way and is saved as `stable` when that move happens. Switching from
  beta to stable says so instead of installing an older version.
- Switch channels on the General tab (Update Channel, under Automatic
  Updates) or with `GET`/`POST /api/v3/system/update-channel`. The Overview
  update banner compares release tags on stable ("LEDMatrix v3.8.0 is
  available") rather than commits on `main`. A detached checkout newer
  than the newest release gets no banner: Update Code leaves it where it
  is until a release includes it.
- A move between `main` and a release tag carries local edits across as the
  pull's `--autostash` does, and the automatic update's health check rolls
  it back to where HEAD was: the branch, or the detached release.

### Frozen-panel detection

A render loop stuck inside a plugin's `display()` left `ledmatrix.service`
"active" with the panel frozen, and nothing noticed: `/api/v3/health` judged
the display by the preview PNG's age, and the automatic update's health check
passed "service active plus one HTTP 200".

- **systemd watchdog.** `ledmatrix.service` now has `WatchdogSec=120` and
  `NotifyAccess=main` (still `Type=simple`). The render thread itself pings
  systemd over `$NOTIFY_SOCKET` (`src/display_watchdog.py`, standard library
  only), so a stuck render thread stops the pings even while the update
  worker and Vegas's tick thread carry on. systemd then kills the display with
  SIGABRT -- faulthandler writes every thread's stack to the journal, which
  names the plugin -- and restarts it. The process widens the limit to 15
  minutes while it starts and while it loads a plugin enabled from the web UI
  (either can run pip), and sends `READY=1` and narrows it back after its
  first frame.
- **Heartbeat.** The render loop writes `/run/ledmatrix/display-heartbeat.json`
  every 5 seconds (`RuntimeDirectory=ledmatrix`; tmpfs, so no SD-card
  writes). `/api/v3/health` reports it as `checks.display_loop`: `running`,
  `stalled` (older than 60s; the overall status turns `degraded`) or
  `not_reported` when there is no heartbeat (dev server, emulator, Windows),
  which leaves the verdict to the older checks as before.
- **Update health check.** When the display wrote a heartbeat before an
  automatic update, the restarted display must keep one fresh (30s) for the
  update to pass; a frozen panel is rolled back. Code that never wrote one is
  checked as before. The check runs as the copy taken before the update, so
  this takes effect from the update after the one that installs it.
- **Crash loops back off.** `RestartSteps=4` and `RestartMaxDelaySec=2min`
  stretch the delay between automatic restarts from 10s to two minutes, instead
  of retrying every 10s forever. systemd before 254 (Bookworm) ignores the two
  lines with a warning. A start limit was ruled out: once tripped it leaves the
  panel dark and refuses the web UI's Start button and the update rollback.
- **Existing installs** keep their old unit until `sudo
  ./scripts/install/install_service.sh` is re-run (an update never rewrites
  units; the startup validator warns about the drift). Until then there is no
  watchdog, but the display creates `/run/ledmatrix` itself, so the heartbeat,
  the health check and the update check work straight away.

### Security

- The web interface refuses state-changing requests (`POST`, `PUT`, `PATCH`,
  `DELETE`) sent by another website's page. Any site a LAN user visited could
  make their browser submit a plain HTML form to `http://<pi>:5000` -- CORS
  does not stop such a request, only hides its answer -- and
  `/api/v3/system/action` accepted form bodies, so that page could reboot or
  power off the Pi, pull code, or reach any other mutating route. A request
  whose `Origin` (or, without one, `Referer`) is not the host it was sent to,
  or is `null`, now gets 403 `CROSS_SITE_REQUEST`
  (`web_interface/origin_guard.py`). `/api/v3/system/action` also refuses a
  form-encoded or `text/plain` body (415) unless it carries HTMX's
  `HX-Request` header; every caller in the interface already sends JSON.
- **Behaviour change for API scripts:** clients that send no `Origin` or
  `Referer` -- curl, Python `requests`, Home Assistant, the MQTT bridge --
  are unaffected. A browser page served from a *different* origin (a
  dashboard or userscript on another host) can no longer call the mutating
  API; call it server-side instead. Anyone posting a form body to
  `system/action` must switch to JSON. Behind a reverse proxy, forward the
  original `Host`, port included (`proxy_set_header Host $http_host;`;
  nginx's `$host` drops the port); `X-Forwarded-Host` is not trusted. A
  TLS-terminating proxy needs nothing more: a portless `Host` matches an
  `https://` page.

### Optional web login

- The web interface can require a password, **off by default**: a device that
  does not set one behaves exactly as before. Set it under **General >
  Security**; from then on every page and API route needs a login (a session
  cookie, 30 days, kept across restarts) or an API token. Unauthenticated page
  loads go to the new `/login` page, HTMX requests get `HX-Redirect` to it,
  and API calls get `401` JSON (`AUTH_REQUIRED` / `INVALID_TOKEN`). Wrong
  passwords are rate-limited per address (5 a minute, 30 an hour, through the
  existing flask-limiter). Log out from the header. Changing the password
  signs every other browser out. (`web_interface/auth.py`)
- **API tokens** for Home Assistant, scripts and the MQTT bridge: create,
  list and revoke them in the same section, send them as
  `Authorization: Bearer <token>`. A token is shown once; only its SHA-256 is
  stored. Tokens cannot change login settings. The MQTT bridge takes one as
  `ledmatrix_api_token` (or `LEDMATRIX_MQTT_LEDMATRIX_API_TOKEN`, or the
  Tools tab); it needs one only when it runs on another machine.
- Always open, login or not: requests from the Pi itself (loopback, without
  proxy headers), the Wi-Fi setup flow (`/setup` and the Wi-Fi status, scan
  and connect routes) while the Pi is in access-point mode, static files, the
  captive-portal probe URLs, and `/api/v3/health`, which then answers only
  `{"status": "healthy" | "degraded"}` to a caller that is not logged in.
- The password hash (werkzeug), the token hashes and the cookie-signing key
  live in the `web_auth` section of `config/config_secrets.json`. No API
  returns them: `GET /api/v3/config/main`, `GET /api/v3/config/secrets` and
  the raw JSON editor leave the section out, the raw secrets save keeps the
  stored one, a `/config/main` save drops a `web_auth` key, and orphaned-plugin
  cleanup no longer treats it as a plugin (`CORE_SECRETS_KEYS`).
- **Lost password:** `sudo python3 scripts/reset_web_password.py` on the Pi
  turns login off (`--revoke-tokens` also deletes the tokens), or open the
  interface from the Pi itself.
- New routes: `/login`, `/logout`, `GET /api/v3/auth/status`,
  `POST /api/v3/auth/password`, `POST /api/v3/auth/disable`,
  `GET|POST /api/v3/auth/tokens`, `DELETE /api/v3/auth/tokens/<id>`.

### Vegas participation

A plugin now takes part in Vegas mode in one declared way: `'scroll'` (its
content scrolls by), `'pause'` (the scroll stops for its turn and its
`display()` draws it full screen) or `'exclude'`. No plugin changes
behaviour: one that declares nothing gets exactly what the old hooks gave
it, checked against every official plugin.

- `BasePlugin.get_vegas_participation()` resolves, in order: the user's
  `vegas_participation` config value, the manifest's `vegas_participation`,
  then the legacy hooks (`get_vegas_display_mode()` returning `STATIC` →
  pause, else `get_vegas_content_type()` returning `'none'` → exclude, else
  scroll). `resolve_vegas_participation()` in `src.plugin_system.base_plugin`
  is what the core calls; the user's setting wins even over a plugin that
  overrides the method.
- The Vegas stream manager decides inclusion and pauses through it, and
  `PluginAdapter.get_content_type()` is removed (core-internal, now unused).
  Swap mode no longer drops a plugin's segment for a cycle when its
  `get_vegas_display_mode()` raises something other than
  `AttributeError`/`TypeError`: like every other decision point it now
  treats that as "not paused".
- `vegas_participation` is a core-owned per-plugin property (an enum with no
  default) and a manifest field in `schema/manifest_schema.json`.
- `GET /api/v3/plugins/installed` reports each plugin's
  `vegas_participation`, and the Vegas plugin-order list badges it (Scroll /
  Pause / Excluded) instead of the old Scroll / Fixed / Static.
- `src.deprecation.warn_deprecated()` warns once per process for what
  `@deprecated` cannot decorate, such as a config key.

Deprecated, removed in 3.9.0 (each logs a warning on first use). Vegas never
read any of them:

- `BasePlugin.get_supported_vegas_modes()` and
  `BasePlugin.get_vegas_segment_width()`.
- The `vegas_panel_count` per-plugin setting (warns once per plugin that sets
  it).
- The SCROLL / FIXED_SEGMENT distinction (`vegas_mode` `"scroll"` vs
  `"fixed"`): both always scrolled. Documented only; no warning, because
  official plugins' schemas still offer `"fixed"`.

### Plugin store

- The store reads three optional registry fields that ledmatrix-plugins'
  `update_registry.py` now publishes (ChuckBuilds/ledmatrix-plugins#579). An
  older `plugins.json` without them behaves as before.
  - `ledmatrix_min_version`: an install or update this core cannot run is
    refused before anything is downloaded, pulled or moved aside, and the web
    UI says why ("requires LEDMatrix X or newer…", HTTP 409) instead of "check
    logs for details". The store card shows a "Needs LEDMatrix X+" badge. The
    check on the downloaded manifest stays as the fallback (older registries,
    an explicitly requested other branch, `compatible_versions`).
  - `aliases`: the entry's other ids. Update, uninstall and reinstall by the
    registry id now find a plugin installed under its manifest id
    (`weather` → `ledmatrix-weather/`; likewise leaderboard, music, stocks).
    Only registry proof counts: the entry's `aliases` or its `plugin_path`
    name, or a folder whose manifest declares one of those ids. A
    `ledmatrix-<id>/` folder with no such proof is never replaced or removed;
    uninstall and update report "not installed" and log the folder's path.
    Install and update fetch the registry first when such a folder exists
    and none is loaded; uninstall stays offline.
  - `commit`: the monorepo commit that introduced the listed version, shown
    on the store card and linked to the plugin's source at that commit.
    Informational only; installs still come from the branch head.

### Changes

- The web interface no longer loads or runs plugins (web plugin catalog,
  stage 1). It built its own `PluginManager` and loaded plugins into the web
  process: store installs and updates loaded or reloaded a web-side copy, and
  config saves and enable/disable called `on_config_change`, `on_enable` and
  `on_disable` on it. None of that reached the panel. The web process now
  reads plugins as files through the new `PluginCatalog`
  (`src/plugin_system/plugin_catalog.py`); only the display runs them, and
  config changes reach them through its config watcher, as they already did.
  - A plugin update, an install of a plugin that is already enabled, or an
    uninstall that keeps an enabled plugin's config now answers
    `restart_required: true` and shows the restart banner, because the
    running display keeps the code it loaded until it restarts. Before, the
    update looked applied and the panel kept the old version.
  - The restart banner follows `restart_required` in any response
    (`POST /api/v3/config/main` sends it) rather than the URL that was
    called.
  - `/api/v3/plugins/installed` reports `loaded`, `state` and `error_info`
    as `null`: the display does not publish them, and the old values
    described web-side copies. `enabled` follows the display's rule, so a
    plugin whose config has no `enabled` flag shows as disabled (it never
    ran). `vegas_mode` is the configured value only.
  - `vegas_participation` there is the user's setting, else the manifest's
    declaration, with a new `vegas_participation_source` (`config` or
    `manifest`). When only the plugin's code decides it (a
    `get_vegas_participation()` override or the legacy Vegas hooks) it is
    `null` with source `runtime`: the display derives it, and the web no
    longer asks a web-side plugin instance.
  - Starlark routes always use their on-disk path. The one place the web
    process still imports plugin code -- the Starlark helper modules and an
    `oauth_flow` action script -- is `_import_plugin_code_in_web_process()`,
    until a plugin web-entry contract replaces it.
- The display publishes its plugin runtime state, and the web interface
  reads it (web plugin catalog, stage 2). A new snapshot in the shared cache
  (`plugin_runtime_snapshot`, `src/plugin_system/plugin_runtime.py`) lists,
  per plugin, whether the display has it loaded, its lifecycle state, a
  short redacted summary of its last error, the version it loaded and when.
  It is written when something changes (at most every 10 s; an ordinary
  plugin update is not a change) and otherwise once a minute, carries its
  publish time, and says `running: false` when the display stops.
  - `/api/v3/plugins/installed` fills `loaded`, `state` and `error_info`
    again, from that snapshot, and adds `loaded_version` and `loaded_at`.
    Only a live snapshot counts: when the display is stopped, has not
    published, or has not refreshed for 3 minutes, those fields are `null`
    and the new `data.runtime.status` says `stopped`, `unknown` or `stale`.
  - `data/plugin_state.json` is retired: nothing reads or writes it. It held
    copies of config.json's enabled flags and the manifests' versions, plus
    install timestamps only `GET /api/v3/plugins/state` returned, so nothing
    in it is migrated; an existing file is left in place and can be deleted.
    The web-side `PluginStateManager` (`src/plugin_system/state_manager.py`)
    that wrote it is removed; the display's state machine in
    `plugin_state.py` is now the only `PluginStateManager`.
  - `GET /api/v3/plugins/state` is built per request from config.json, the
    plugins on disk and the display's snapshot (`installed`, `in_config`,
    `enabled`, `version`, `status`, the runtime fields, and `installed_at` /
    `last_updated` from the operation history), with a top-level `runtime`.
    It no longer returns `config_version` or `metadata`.
  - State reconciliation compares desired state (config.json plus disk) with
    the display's snapshot. New findings -- enabled but not loaded (with the
    load error), and loaded at an older version than is installed -- are
    reported with `fix_action: no_action`; the unresolved-issues banner is
    unchanged. `StateReconciliation` takes `config_manager`, `plugins_dir`,
    `store_manager` and `runtime_source` as keywords.
  - Backups list the installed plugins from disk, with `enabled` from
    config.json, instead of merging in `plugin_state.json`. A plugin that
    only that file still named (not installed, not configured) is no longer
    listed. Restores are unchanged.

### Fixes

- Quieter routine logging. Every rotation logged each mode twice
  ("Switching to mode", then "Processing mode"), and a mode with nothing to
  show added "display() returned False" and "No content to display". Those
  three repeats are now DEBUG; "Switching to mode" stays INFO, and `--debug`
  shows the rest. On ledpi this cut the display's journal lines by about 30%
  (~105 to ~75 per 5 minutes). Each stored line costs roughly 9 KB of SD-card
  writes through the persistent journal (display at INFO vs WARNING: about
  190 KiB/min apart), so the saving is real but small.
- Reinstalling a plugin by its registry id when it is installed under its
  manifest id (`weather` in `ledmatrix-weather/`) no longer deletes it when
  the install then fails. The safety copy was taken of `weather/`, which did
  not exist, and the real install was removed to make room for the download,
  so a refusal by the compatibility gate left no plugin at all. Uninstalling
  by the registry id reported success and removed nothing; updating by it
  said "not installed". All three now find the install.

- On-demand no longer restarts a running display. `POST
  /display/on-demand/start` treated `start_service` (on by default, and what
  "Preview on display", the on-demand dialog and the MQTT bridge all send) as
  "restart": it stopped the service, waited 1.5s and started it again, so
  every request reloaded every plugin and left the panel blank for seconds.
  The running display already reads the request within a quarter of a second,
  mid-screen and mid-Vegas included, so the route now only starts the service
  when it is not running. `POST /display/on-demand/stop` reads
  `stop_service` as a boolean, so `"false"` no longer stops the service.
- On-demand works for a disabled plugin. The display only loads enabled
  plugins, so "Preview on display" on a disabled plugin's config page (which
  says the plugin will be enabled for the preview) failed with
  `invalid-mode`. The display now loads the plugin live for the session,
  without writing `enabled` to `config.json`, and unloads it when on-demand
  is stopped, expires or moves to another plugin. A plugin that fails to
  load reports on-demand status `error` with `load-failed`. A session
  restored after a restart unloads its disabled plugin the same way; it used
  to stay loaded until the next restart.
- A stop request now clears an on-demand error. After a failed request,
  `/display/on-demand/status` kept reporting `status: error` for up to two
  minutes even after a stop.
- One hung plugin no longer stops every plugin from updating. The single
  update worker waited on each plugin's lock with no time limit, and the
  render thread holds that lock while it runs the plugin's display(); a
  display() that never returned (or a first frame still running after the
  executor's 30s timeout) parked the worker for good, so scores, weather and
  clocks all froze while the panel kept scrolling. The worker now waits at
  most 5s (the bound `unload_plugin()` already uses) and skips that update;
  the other plugins keep updating. The skip is logged (at most once a minute
  per plugin) and counted in plugin health as a busy skip (`busy_skip_count`,
  `last_busy_skip`), but it is not a failure and never opens the circuit
  breaker: Vegas mode holds a plugin's lock for its whole content render,
  which on a slow Pi can outlast 5s, and a healthy plugin must not be pulled
  from rotation for that.
- display() calls are timed on every frame. One taking 2s or more is logged
  (at most once a minute per plugin) and counted in plugin health
  (`slow_call_count`, `last_slow_call`); one that runs past the executor's
  timeout counts as a hang (`hang_count`, `last_hang`) and as a failure to
  the circuit breaker. A first frame that times out is no longer recorded as
  a success, and an update() still running after its timeout is recorded as
  a hang instead of leaving the plugin silently stuck. Only these real hangs
  count toward the breaker.
- A plugin's `on_config_change()` no longer runs while its update() is
  running on the worker thread. It now runs under the plugin's lock; if the
  lock stays busy past the same 5s bound the change is handed to the update
  worker, which applies the latest one as soon as the lock frees, and before
  the plugin's next update() at the latest. The plugin API is unchanged.
- With a Vegas width budget set (`max_plugin_width_ratio` or a plugin's
  `vegas_max_width_screens`), a single image over the budget with no gaps
  between items -- a map, one long headline -- no longer takes a pass of its
  own showing four blank columns. The cut landed in the middle of the blank
  margin trimming leaves at the image's edge; margins are no longer cut
  points, so such an image is cropped to the budget as intended.

### Live Vegas elements (plugin API)

- New plugin hooks for content that can change while it scrolls:
  `BasePlugin.get_vegas_elements()` returns `VegasElement`s -- named,
  fixed-width pieces of Vegas content -- instead of pictures;
  `redraw_vegas_element(key, width, height, at)` redraws one without the
  plugin lock for content that changes with time; and
  `notify_vegas_data_changed()` reports data that arrived outside
  `update()`. New module `src/plugin_system/vegas_elements.py`
  (`VegasElement`, also re-exported from `base_plugin`). See "Live Vegas
  elements" in `docs/PLUGIN_API_REFERENCE.md`.
- The ticker asks a plugin that implements the hook for elements on its
  background fetch (under the plugin's lock, on a canvas of its own) and
  records where each one lands in the strip, in absolute columns a trim does
  not move (`src/vegas_mode/elements.py`). Live elements are never trimmed to
  their ink: each is padded with `content_padding` black columns either side.
  Every other path -- the first strip, the render-thread fallback, plugins
  without the hook -- is unchanged. Swapping redraws into the strip builds
  on this.
- `PluginManager.add_update_listener()` / `remove_update_listener()` /
  `notify_data_changed()`: a listener hears a plugin id the moment its
  `update()` completes, rather than at the next ~4s Vegas poll.
- New `display.vegas_scroll` settings: `live_refresh` (default `true`; the
  kill switch), `live_max_hz`, `live_min_interval`, `live_lead_screens`, and
  a per-plugin core-owned `vegas_live`. Live elements are off whatever these
  say under multi-display sync, in swap mode and with `offscreen_prefetch`
  off.
- `scripts/check_plugin.py` checks the element contract for any plugin that
  implements it (`src/plugin_system/testing/vegas.py`), and
  `test/fixtures/plugins/vegas-live-stub` is a working example.
- **Live elements update in place.** When a plugin's `update()` completes,
  one background worker (`src/vegas_mode/live_worker.py`) redraws its live
  elements that are on or ahead of the screen, nearest first, and hands the
  ones whose pixels changed to the render thread, which copies them into the
  strip between two frames (`RenderPipeline.apply_live_patches`,
  `ScrollHelper.patch_columns`): at most four patches or two screens of bytes
  a frame, no drawing and no locks on the render thread. Elements with
  `refresh_hz` are redrawn that often while near the screen, through the
  plugin's lock-free `redraw_vegas_element()`. The worker also takes over
  group prefetching once the strip holds a live element, so one thread
  still does all the drawing; it runs inside the render gate, starts only
  when a live element is placed, and is restarted if it dies (three times in
  ten minutes turns live updates off for the run). While live elements exist,
  the Vegas update tick runs every second instead of every four.
- Web UI: "Update live content while it scrolls" under Vegas mode's Cycle
  Pacing (`display.vegas_scroll.live_refresh`).
- **Live cards for the scoreboards (shared code).** New module
  `src/common/sports_vegas.py`: `game_key()`, `dedupe_games()`,
  `VegasCardCache` (draws a card only when its fingerprint changes) and
  `StickyOdds` (keeps a card's odds through a live poll that left them out),
  `finished_games()` and `with_finished_games()` (a game that just went final
  keeps its card, showing FINAL, where its live card was).
  `SportsScrollDisplay` gains `make_vegas_renderer()` (the override point; a
  sport that does not implement it keeps its ordinary Vegas content),
  `render_vegas_card()`, `vegas_separator()` and `build_vegas_elements()`,
  and `SportsScrollDisplayManager` gains `get_vegas_elements_for()`.
  `SportsLiveSharedMixin` gains `_record_finished_game()` /
  `finished_games_snapshot()`, so a game that goes final keeps a card to show
  FINAL on until the hourly recent list takes it over.
- `scripts/render_plugin.py --vegas` renders a plugin's block of the Vegas
  strip as the ticker lays it out (live elements, or with `--no-live` its
  ordinary content) and writes the live elements' keys and columns beside
  it. `--timeline ROWS` stacks the block at successive moments as the
  ticker would update it in place (`--timeline-step`, and
  `--timeline-update` to run `update()` between rows).
  `render_vegas_strip()` and `render_vegas_timeline()` in
  `src/plugin_system/testing/vegas.py`; the join is now
  `render_pipeline.join_plugin_rows()`.
- **Behaviour change: live games stay in the Vegas ticker by default.**
  `display.vegas_scroll.live_in_ticker` now defaults to `true`: the marquee
  keeps running through a live game, which takes extra turns in it, instead
  of giving way to the full-screen scoreboard. Existing configs all held the
  old `false`, copied from the template, so the first start turns it on once
  (`ConfigManager._migrate_live_in_ticker_default`; the previous config is
  kept as `config.json.backup` and `live_in_ticker_migrated` records that it
  ran). To keep the full-screen scoreboard, untick the new **Keep live games
  in the ticker** under Vegas mode; a `false` set after the migration stays.

### Scrolling

- A Vegas strip extension no longer costs a late frame. Appending the next
  group rebuilt the whole strip (`np.concatenate`, 2-2.6ms for a 10-14k px
  strip at 512x64 on a Pi 4) and trimming copied what was left (1.2-1.8ms),
  so on hdpi every extension frame missed its refresh. The strip now lives in
  a buffer with spare room (`ScrollHelper.STRIP_SPARE_FACTOR`): an append
  writes only the new columns (~0.2ms), a trim only moves the start, and the
  one full copy happens when the buffer is reallocated, about once every two
  strip-lengths scrolled. A strip set from outside (the multi-display
  follower's) is never written through.
- A Vegas strip extension costs the render thread about a third of what it
  did. Appending the next group and trimming what has scrolled past each
  rebuilt the strip's PIL image from its numpy array in full
  (`Image.fromarray`: 1.7ms for an 8,000px strip, 3.8ms for 20,000px, on a
  Pi 4 -- twice per extension), though every frame is cut from the array and
  nothing on the frame path reads the image's pixels. `ScrollHelper` now
  builds `cached_image` only when something reads it, which in Vegas means
  only a multi-display sync push, and the strip is no longer held in memory
  twice. Assigning `cached_image` still stores exactly what was assigned.
  New `ScrollHelper.has_strip()` says whether there is a strip without
  building its image; the frame path and Vegas use it.
- The frame after a Vegas strip extension is no longer late on a Pi 4. The
  render thread also laid out every plugin block of the new group (joining
  its rows, measuring the separation between each pair) and pasted the
  blocks into one image, about 37ms on hdpi against ~3.75ms of slack. The
  thread that fetches the group now does that as each plugin arrives, and
  the extension only writes the prepared pixels into the strip
  (`ScrollHelper.append_content` takes RGB arrays): 3.2ms. In a 4 x 8 minute
  A/B soak, extension frames went from 10 of 10 late to 3 of 10.

### Tooling

- The frame-timing recorder says which render-thread work a late frame
  followed. Work done between two frames calls
  `FrameTimingRecorder.note_op(kind, nbytes)` and the next presented frame
  carries the tag; the stats gain `op_frames`, `late_op_frames`, `op_freezes`
  and `op_bytes` per kind (additive; the file's schema version is unchanged).
  Vegas tags every strip `compose` and `extend`, and `frame_soak.py` prints an
  "after work" table with each kind's own late rate.
  `scripts/render_bench.py` can drive the same work on a panel with nothing
  else running: `--strip-screens` for a Vegas-sized strip, `--patch-bytes /
  --patch-every / --patch-where` for in-place column writes, and
  `--extend-every-screens` for appending and trimming on a fixed cadence.
  See "Soaking a rig" in `docs/SCROLL_PERFORMANCE.md`.
- `scripts/sports_drift_report.py`: for a ledmatrix-plugins checkout, counts
  how many different bodies each method family has across the nine
  scoreboards' `sports.py`, `manager.py` and `game_renderer.py`, lists the
  families still identical everywhere and those with one outlier, and with
  `--family ... --diff` shows the variants. It is the progress measure for
  the reconcile-then-promote roadmap in `docs/SPORTS_UNIFICATION.md`, which
  this release rewrites. CI runs it against the monorepo's main as a
  report-only job ("Sports drift report"; never fails the build).

### Deprecations

- The 35 plugin-facing methods deprecated in 3.5.0 are now removed in 3.8.0,
  not 3.7.0: 3.7.0 shipped with all of them still in place, still warning
  "will be removed in LEDMatrix 3.7.0". The warning, the docs and
  `test/test_deprecation.py` now say 3.8.0. They are removed in this
  release (see Removed, below).
- New `scripts/plugin_api_usage.py` lists every `@deprecated` core method and
  scans core, the plugin monorepo and the registry's third-party plugins for
  calls and overrides, telling real uses from unrelated methods of the same
  name. Its output is `docs/DEPRECATIONS_3.8.md` (linked from
  `docs/PLUGIN_API_REFERENCE.md#deprecated-apis`); no plugin uses any of
  the 35.
- `test/test_deprecation.py` fails while any `@deprecated` marker names a
  release at or below `src.__version__`, so a release can no longer ship
  warning about a removal it has already passed.

### Removed

The 35 plugin-facing methods deprecated in 3.5.0 (each has logged a warning
on first call since, announced for 3.7.0 and then moved to 3.8.0) are gone.
The usage scan (`docs/DEPRECATIONS_3.8.md`, re-run 2026-10-01) found no call
or override of any of them in the 46 monorepo plugins or the 8 third-party
plugins `plugins.json` lists, and core's own last callers went with them. A
plugin that still calls one gets an `AttributeError`;
`docs/PLUGIN_API_REFERENCE.md#deprecated-apis` lists what to use instead.

- `CacheManager`: `has_data_changed`, `update_cache`, `setup_persistent_cache`,
  `get_sport_live_interval`, `get_sport_key_from_cache_key`,
  `get_background_cached_data`, `is_background_data_available`,
  `record_cache_hit`, `record_cache_miss`, `record_fetch_time`,
  `get_cache_metrics`, `log_cache_metrics`, `get_memory_cache_stats`. The
  private change-detection helpers behind `has_data_changed`
  (`_has_weather_changed` and friends, `_is_market_open`) went with it.
- `DisplayManager`: `draw_weather_icon`, `draw_sun`, `draw_cloud`, `draw_rain`,
  `draw_snow`, `draw_text_with_icons`, `get_scrolling_stats`, and with them
  the `WEATHER_COLORS` table and the private `_draw_sun`/`_draw_cloud`/
  `_draw_rain`/`_draw_snow`/`_draw_storm` helpers.
  `VisualTestDisplayManager` (the plugin test harness) drops its copies of
  the icon methods too, so a plugin's visual tests fail the way the real
  display would instead of passing against methods that no longer exist.
- `FontManager`: `set_override`, `remove_override`, `get_overrides`,
  `add_font`, `remove_font`, `validate_font`, `get_font_catalog`,
  `get_available_fonts`, `get_size_tokens`, `get_performance_stats`,
  `get_manager_fonts`, `get_detected_fonts`, `get_plugin_fonts`,
  `unregister_plugin_fonts`, plus the `size_tokens` attribute and the private
  `_save_overrides` and `_clear_plugin_font_cache`. `resolve_font()` still
  applies `config/font_overrides.json`.
- `PluginManager.get_enabled_plugins` (check `enabled` on the entries in
  `plugin_manager.plugins`).

### Web UI styling: a real Tailwind build

- The web UI's utility classes now come from a generated
  `static/v3/tailwind.css` (Tailwind v3.4.19 standalone CLI, no Node)
  instead of ~500 hand-written rules in `app.css`. The CSS is built on a
  dev machine with `python3 scripts/build_css.py` and committed; the Pi
  never builds anything. CI's new "Tailwind CSS is up to date" job rebuilds
  it and fails when the committed file is stale. `app.css` keeps the theme
  tokens, components and dark theme, and loads after `tailwind.css`. The
  values `app.css` had customised (darker gray text, emerald/amber button
  fills, token shadows, font line-heights, keyboard-only focus rings) are
  kept in `web_interface/tailwind/tailwind.config.js`.
- Border utilities now draw. `border-b`, `border-t` and `divide-y` set only
  a width, and nothing gave them a style, so the tab-row underlines and
  section dividers the markup asks for never showed. They do now.
- `2xl:` classes now apply (the hand-written `.2xl\:…` selectors were
  invalid CSS): at 1536px and wider the plugin grids show five columns and
  the page gutters widen, as the markup intended.
- Classes the hand-written file never defined now work, e.g. the teal
  "configure" badge in Operation History, the button of a purple
  `web_ui_actions` card (it had white text on no background), the
  toggle-switch knob offsets, the slider accent colours and the password
  strength colours.
- A scrollable container with its own background (the live preview stage,
  command output in Tools) keeps it. The scroll-hint rule's `background`
  shorthand wiped it, so the preview stage rendered white instead of dark.
- Plugin `web_ui/` pages no longer load Tailwind from a CDN, which failed
  in AP mode with no internet. They get a local `static/v3/plugin-frame.css`
  with the v2 palette they were written against.

### New modules (sports consolidation stage 4)

A plugin may import these via `src.*` once it floors on 3.8.0. All four hold code the
scoreboard plugins carry as identical copies (checked at ledmatrix-plugins
`56c4f15`), moved without behaviour change under the plugins' own names;
each docstring lists what the host class must provide. Nothing in core uses
them yet. The plugins delete their copies when they floor on 3.8.0.

- `src/common/sports_plugin_host.py` — `SportsPluginHostMixin`, ten helpers
  of the scoreboard plugin class (`manager.py`) identical in all nine:
  `_dispatch_switch_refresh` (with `_SWITCH_REFRESH_MIN_GAP_SECONDS`),
  `get_vegas_priority_weight`, `_favorite_team_is_live`,
  `_favorite_scan_targets`, `_favorite_scan_games`, `_game_involves`,
  `get_vegas_content_type`, `_dynamic_feature_enabled`,
  `_get_total_games_for_manager` and `_build_manager_key`. List it before
  `BasePlugin`: two of these override its defaults.
- `src/common/sports_live_scroll.py` — `SportsLiveScrollMixin`, the eight
  `manager.py` methods that rebuild a live scroll strip mid-cycle without
  moving the marquee (`_live_scroll_needs_rebuild`,
  `_preserving_scroll_position`, ...), with `LIVE_SCROLL_REBUILD_MIN_SECONDS`
  and `LIVE_SCROLL_REBUILD_DUTY_DIVISOR`; identical in the eight scoreboards
  with a strip (not ufc). `LIVE_VOLATILE_FIELDS` stays in each plugin.
- `src/common/sports_display_rules.py` — `SportsCardOptionsMixin`
  (`_card_option`, `_recent_date_text`; the eight team scoreboards; list it
  before `SportsCoreSharedMixin`) and `SportsGameRulesMixin`
  (`_filtered_or_all`, `_effective_live_duration`; all nine).
- `src/common/sports_font_path.py` — `resolve_font_path`, what every
  scoreboard's `_resolve_font_path` (nine `sports.py`, eight
  `game_renderer.py`) returns on a core that ships it: the path as given when
  it exists, else `font_layout.resolve_asset_path`.

## 3.7.0

Sports consolidation stage 3 (#672). No behaviour change: nothing in core
uses these yet, and the scoreboards adopt them when they floor on 3.7.0.

### New modules

A plugin may import these via `src.*` (floor on 3.7.0). All three hold code
the scoreboard plugins carry as identical copies, moved without behaviour
change under the plugins' own method names; each docstring lists what the
host class must provide. The plugins delete their copies when they floor on
3.7.0.

- `src/common/sports_celebration.py` — `SportsCelebrationMixin`, the
  score/win celebration takeover drawn by afl, football, hockey, nrl and
  soccer (`_draw_celebration_layout` and the palette, backdrop, scenery,
  confetti and crest steps behind it), plus its colour helpers as free
  functions: `logo_palette`, `lift_color`, `cap_luminance`, `mix_color`,
  `scale_color`, `dim_rgba`, `rgb_luminance`, `rgb_saturation`,
  `color_distance`. Only the drawing: when to celebrate, the phrase and the
  scenery stay in each plugin.
- `src/common/sports_fetch.py` — `SportsFetchMixin`, four `SportsCore`
  methods identical in all nine scoreboards: `_fetch_season_directly`,
  `_background_fetches_espn_ranges`, `_needs_previous_day` and
  `_wants_live_odds` (with `_LOOKBACK_CUTOFF_HOUR` and
  `_LIVE_ODDS_LOOKAHEAD`).
- `src/common/sports_card_wrappers.py` — `SportsCardWrappersMixin`, the
  seventeen `sports_card` delegations the eight scoreboard game renderers
  carry (`_vs_text`, `_element_color`, `_format_game_date`, ...): the methods
  `SportsGameRendererMixin` expects its host to provide.

## 3.6.2

A fix to `src.common.favorite_team_check` (#670).

### Fixes

- The favourite-team check no longer says the Europa League season has
  finished between matchdays. Its scoreboard keeps showing the last matchday,
  and its calendar is a "list" of rounds rather than match days, so neither
  3.6.1 rule applied. When every event is past, a round in a list calendar
  that has not started yet (outside an offseason phase) now draws no
  conclusion. PLL, the World Cup and AFL, whose seasons are over, are still
  reported as finished: no round of theirs is still to start. (#670)

## 3.6.1

A fix to `src.common.favorite_team_check` (#667). Plugins that drop their
bundled copy of it should floor on 3.6.1, not 3.6.0.

### Fixes

- The favourite-team check no longer logs "the season has finished" for a
  league that is still playing. ESPN's default scoreboard keeps showing the
  last slate after it: MLB's regular-season games two days into the
  postseason, a soccer league's previous matchday between rounds. When every
  event is in the past, the check now looks first at the league's phase (a
  regular season or postseason that has moved past the events shown draws no
  conclusion) and at a match-day calendar (`calendarType` "day" with
  `calendarIsWhitelist`, as soccer, the NHL and the NBA use), whose next date
  becomes "nothing on until <date>". An offseason, or a payload without these
  fields, is reported as before.

## 3.6.0

New modules a plugin may import via `src.*` (floor on 3.6.0). Both are
promoted from files the scoreboard plugins carry as copies; the plugins keep
their copies as a fallback until they floor on 3.6.0. No other change since
3.5.0.

- `src/common/favorite_team_check.py` — `FavoriteTeamCheck(logger, leagues)`:
  checks configured favourite team codes against ESPN once per league, on a
  daemon thread, and logs why a league shows nothing (a wrong code, with the
  nearest real one, or a season that has not started). The seven copies
  (`<sport>_favorite_check.py`) were byte-identical; this is the same code,
  with type annotations added.
- `src/common/sports_timezone.py` — `resolve_timezone_name()` /
  `resolve_timezone()` (plus `system_timezone_name()`): the timezone a
  scoreboard draws start times in. The ten copies (`<sport>_timezone.py`)
  differed only in two values, which are keyword-only arguments here:
  `plugin_label` (named in the warning logged when nothing resolves) and
  `writeback_fixed_in` (for a plugin that once wrote `"UTC"` back into the
  saved config; `None` otherwise). Same resolution order and log messages.

## 3.5.0

New modules a plugin may import via `src.*` (floor on 3.5.0):

- `src/common/sports_helpers.py` — the helpers the scoreboards' `sports.py`
  carry byte-identical copies of: `clamp_window`, `clamp_seconds`,
  `logo_needs_refresh`, `spread_weighted_order` (+ `MIN_WINDOW_DAYS`,
  `MAX_WINDOW_DAYS`), and `SportsHelpersMixin` with `_mode_customization`,
  `_setting_int`, `_reset_dwell_on_reentry`, `_next_switch_index`,
  `_spread_weighted_order`, `_odds_color`, `_upcoming_date_and_time_text` under
  the plugins' names and signatures, plus the `_favorite_key` override point.
  Constructor-free; keeps lazy state on its host (see the module docstring,
  which also gives the host contract).
  A new module rather than more methods on `sports_shared`: a plugin that
  deletes a copy and leans on an older module having grown the method fails at
  runtime with `AttributeError`, which no load-time check sees, while a missing
  module fails at load. Nothing in core uses it yet.
- `src/common/espn_dates.py` — `fetch_espn_scoreboard`,
  `fetch_espn_date_chunks`, `espn_date_chunks`, `clamp_espn_limit`,
  `ESPN_MAX_LIMIT`: fetch an ESPN scoreboard date range now that ESPN rejects
  ranges (see Sports data below). Plugins bundle a copy of it.
- `src/common/json_body.py` — `response_json(response)`: `response.json()`,
  parsed by orjson when it is installed (an optional dependency) and by the
  stdlib otherwise; an orjson parse error falls back to `response.json()` so
  requests raises its usual error. Same Python objects either way; a season
  schedule parses about 1.7x faster on a Pi 4, and the parse holds the GIL (so
  freezes the display) for that much less time. `espn_dates` and
  `BackgroundDataService` use it; `espn_dates` falls back to `response.json()`
  when it is missing, so the plugins' bundled copies of `espn_dates` still load
  on an older core.
- `src/common/bdf_font.py` — `load_bdf_face(path, size)` (a cached
  `freetype.Face` plus the pixel size it really renders at, falling back to
  the file's native strike) and `draw_bdf_text(draw, text, x, y, face, color)`.
  `DisplayManager`, `FontManager`, `element_style` and the plugin test harness
  now all load and draw BDF text through it; the panel's pixels are unchanged
  and BDF text draws 10-250x faster. The plugin test harness's
  `calendar_font` / `bdf_5x7_font` now has the panel's 7px size set: it used
  to be an unsized face, so in golden images and `check_plugin` /
  `dev_server` previews its text sat 6px above where the panel draws it (off
  the canvas entirely near the top) and `get_font_height()` returned 0.

Also new under `src/` since 3.4.0, but internal to core rather than for plugins:
`src/common/frame_timing.py` and `src/common/render_gate.py` (see Scrolling),
`src/core_config_keys.py`, `src/deprecation.py`, `src/device_location.py`,
`src/font_usage.py`, `src/matrix_support.py`, `src/pi5_matrix_support.py`,
`src/redaction.py`, `src/scan_order.py`, `src/web_interface/config_arrays.py`,
and in `src/plugin_system/`: `plugin_dirs.py`, `repo_urls.py`,
`store_install.py`, `store_registry.py` and `store_update.py`.

New names in existing modules (a plugin using these must floor on 3.5.0):

- `src.common.api_helper`: `USER_AGENT`, `DEFAULT_HTTP_HEADERS` (read-only).
- `src.logo_downloader`: `fetch_logo`, `save_png_atomically`,
  `shared_downloader`.
- `src.common.sports_card.unshare_element_fonts` takes an optional third
  argument, `element_for_font` (default: the module's `ELEMENT_FOR_FONT`, so
  existing calls are unchanged).
- `src.wifi_manager.get_wifi_status_path()` — where WiFi status messages for
  the display are written (`config/wifi_status.json`).
- `BackgroundDataService.handles_espn_date_ranges` (see Sports data).
- `FontManager.register_plugin_fonts()` takes an optional `plugin_dir`, and
  `FontManager.forget_manager_fonts()` is new (see Fonts).

Deprecated for removal in 3.7.0, later moved to 3.8.0 (each logs a warning
on first use; see
`docs/PLUGIN_API_REFERENCE.md#deprecated-apis` for replacements). Nothing in
core, the monorepo or the registry's third-party plugins calls them:

- `CacheManager`: `has_data_changed`, `update_cache`, `setup_persistent_cache`,
  `get_sport_live_interval`, `get_sport_key_from_cache_key`,
  `get_background_cached_data`, `is_background_data_available`,
  `record_cache_hit`, `record_cache_miss`, `record_fetch_time`,
  `get_cache_metrics`, `log_cache_metrics`, `get_memory_cache_stats`.
- `DisplayManager`: `draw_weather_icon`, `draw_sun`, `draw_cloud`, `draw_rain`,
  `draw_snow`, `draw_text_with_icons`, `get_scrolling_stats`.
- `FontManager`: `set_override`, `remove_override`, `get_overrides`,
  `add_font`, `remove_font`, `validate_font`, `get_font_catalog`,
  `get_available_fonts`, `get_size_tokens`, `get_performance_stats`,
  `get_manager_fonts`, `get_detected_fonts`, `get_plugin_fonts`,
  `unregister_plugin_fonts`.
- `PluginManager.get_enabled_plugins`.

### Config saves and plugin config preparation

- A JSON `POST /api/v3/config/main` changes only the keys it sends. The MQTT
  bridge's brightness slider used to turn off `disable_hardware_pulsing`,
  `inverse_colors`, `show_refresh_rate` and `use_short_date_format`, and a
  timezone- or location-only save turned off web-UI autostart and weekly
  automatic updates. Missing checkboxes still save as unchecked for the
  settings forms (they now send a hidden `__form_section` field) and for
  form-encoded posts.
- A partial JSON `POST /api/v3/plugins/config` merges onto the plugin's stored
  settings instead of resetting everything it didn't send to the schema
  defaults, and keeps a submitted `skin`, `skin_options`, `vegas_width_pct`,
  `vegas_overflow` or `vegas_max_width_screens` (they were silently dropped).
- Plugin sections posted to `/config/main` are validated and prepared exactly
  like `/plugins/config`; a value that endpoint rejects is rejected here too,
  and nothing is saved.
- Legacy boolean settings (#588) are read as `{"enabled": ...}` objects
  everywhere, not just when the plugin loads: `GET /plugins/config` returns
  the object, posting it back saves, and hot reload hands plugins the same
  shape (schema defaults included) they were constructed with.
  `schema_manager.prepare_plugin_config` is the one implementation.
- A plugin's settings tab shows schema defaults for options its saved config
  doesn't have yet. A boolean added with `"default": true` in a plugin update
  (geochron 1.2.0's `show_date` and `show_date_line`) used to render unchecked,
  and the next save of that tab stored it as `false`. Enum dropdowns likewise
  showed their first option instead of the default. The partial now runs the
  stored section through `prepare_plugin_config` like `GET /plugins/config`
  (secrets are still masked, after the merge), and the form falls back to a
  field's own `default` inside objects that declare a default of their own.
- `scripts/dev_server.py`, `check_plugin.py`, `render_plugin.py` and the plugin
  harness build configs the way a device does: nested defaults are included,
  a schema `enabled: false` no longer beats the forced `enabled: true` in the
  dev server, and nested overrides such as `{"nhl": {"enabled": true}}` keep
  the other defaults of that section.
- Clearing Vegas "Min/Max Cycle Time" no longer rejects the whole Display save,
  and those fields no longer add junk entries to `display.display_durations`.
- Turning automatic updates on from the Raw JSON editor finishes their setup
  like the General tab does, instead of waiting for the next display restart.
- `POST /config/schedule` and `/config/dim-schedule` accept the per-day
  `days.<day>.{enabled,start_time,end_time}` shape their GETs return, as well
  as the flat form keys.
- The startup check no longer warns that `auto_update` or `dim_schedule` is
  "enabled but not found in plugins directory", and plugin ids that collide
  with any core config section are flagged: the last private copies of the
  core-key list now use `src/core_config_keys.py`.
- Plugin config saves recombine position-keyed inputs for nullable array fields (`"type": ["array", "null"]`).
- A blank Max Dynamic Duration keeps the stored value instead of failing the Display save with a 500; other values must be whole seconds from 30 to 1800.
- A power cut or crash mid-save can no longer leave `config/config.json`
  truncated. `ConfigManager.save_config()` wrote the file in place; it,
  `save_config_atomic()`, `save_raw_file_content()` and backup rollback now
  share one writer (`atomic_write_text` in `src/config_manager_atomic.py`)
  that fsyncs a temp file, renames it into place and fsyncs the directory.
- `save_config_atomic()` no longer rewrites `config_secrets.json` on every
  save, only when its content changes, and rotating backups no longer re-reads
  every backup. The backups themselves are unchanged:
  `config/backups/config.json.backup.<version>` plus its paired secrets
  backup, five newest kept.
- A save by the root-run display service keeps the file's previous owner
  instead of handing `config.json` to root, and an install path with
  "secrets" in a directory name no longer makes `config.json` mode 0640.

### Sports data, logos and odds

- Since 2026-09-15 ESPN answers `dates=YYYYMMDD-YYYYMMDD` scoreboard queries
  with `400 Bad Request` for every sport, so season schedules, the weeks window
  and today's games all failed ("400 Client Error" from the NFL/NCAAFB managers
  and `src.background_data_service`). A rejected range is now re-fetched as
  whole months (`dates=YYYYMM`) plus the leftover days at each end, which cover
  the window exactly: a football season is 8 requests. A month that returns
  exactly 500 events is truncated and is re-fetched day by day.
- Scoreboard requests send `limit=500` at most. Above 500 ESPN silently returns
  a short list: college football gave 25 of 68 games for one Saturday at the
  `limit=1000` everything used to send.
- `BackgroundDataService.handles_espn_date_ranges` is `True`. Plugins check it
  to decide whether to submit a season range to the service or fetch it
  themselves on an older core.
- A league with no live games no longer backs its poll off past the next
  kickoff. The escalation counted empty looks and nothing else, so a league
  three hours before kickoff was indistinguishable from one out of season and
  both reached `live_idle_max_interval`: measured gaps of up to 928 seconds,
  and a rig that sat for a quarter of an hour with eight NFL games in progress
  without noticing any of them. The wait is now clamped so it cannot run past
  the earliest start still ahead, which the live fetch already downloads, so
  it costs no extra request. Just after a kickoff the live cadence is held for
  a grace window, because a provider that has not yet flipped the status would
  otherwise read as another empty check and escalate the back-off again.
- ESPN date chunks are fetched six at a time (`ESPN_CHUNK_WORKERS`) in two
  passes: months and edge days first, then the days of any month that came
  back at the cap. A cold college-baseball season is about 130 requests, and
  they went out one at a time; March and April measured on a Pi 4 (63
  requests, 3101 events) went from 11.2s to 1.6s. Merged events still follow
  `espn_date_chunks` order, so the payload does not depend on which request
  won the race, and a capped month's payload is dropped before its days are
  fetched, which keeps the peak memory of a four-capped-month fetch to about
  16 MB over the sequential path rather than 43 MB — `docs/LOW_MEMORY_BOARDS.md`
  puts a 1 GB Pi 3B+ at under 200 MB of headroom.
- `ESPNDataSource.fetch_standings` asks each league the endpoint that league
  actually publishes. It tried `/standings` first whatever the league and fell
  back to `/rankings` only on a 404, but college leagues answer `/standings`
  with a 200 that carries no poll, so the fallback never fired: the rank badge
  simply never appeared and anything keyed off rankings quietly did nothing.
  Endpoints are now ordered by whether the league publishes a poll, and a 200
  that lacks the key counts as a miss, so a league answering both still ends up
  with whichever carries the poll. Only a 404 is routine — that is how a league
  says it has none; a connection error, a timeout or an unparseable body is
  logged as an error again, and a bug raised while inspecting the payload is no
  longer swallowed as a missing poll. This is the implementation the football,
  baseball and hockey boards already ship; core was the last copy on the old
  one.
- `BaseOddsManager.get_odds()` no longer returns the cached "no odds" marker (`{"no_odds": True}`) as if it were odds. A game ESPN had no odds for is cached that way so it isn't re-requested every update; on the next update the cache hit handed the marker back, and callers saw a truthy dict. It now returns `None` for it, on the cache hit and in the stale-cache fallback after a failed fetch, as the plugins' bundled copies already did.
- Background data fetches retry at one level instead of two. The session adapter retried a connection error three times inside every attempt of the service's own retry loop, so a dead network cost up to 16 connection attempts per request and held one of the few worker threads throughout; now it is the loop's `max_retries + 1` attempts. ESPN date-range chunks, which don't go through that loop and skip a chunk that fails, keep a small connection retry of their own so a brief blip doesn't drop a month from a cached season.
- `LogoHelper.load_logo_with_download()` sizes its placeholder to the scaled logo box, like a real logo (only differs when `scale` isn't 1).
- The AP Top 25 resolver remembers a failed or empty rankings fetch for 5 minutes, so an ESPN outage no longer costs every scoreboard update a 30s timeout. Its duplicate INFO log line is gone.
- `BackgroundDataService` runs a cache-hit callback outside its lock, as the fetch path does.
- `LogoHelper.load_logo_with_download()` waits an hour before retrying a download that failed for a missing logo, instead of retrying (with a 30 s timeout) on every call.
- Restamping a placeholder logo writes the file atomically.
- The odds manager logs cache hits, misses and fetches at DEBUG, and a bad JSON body is logged as a parse error rather than a failed fetch.
- `APIHelper` keeps cached responses for the `cache_ttl` it was given, instead of always 300 s.
- Logo scales from 0.1 to 10 are honoured everywhere; values outside that range are clamped.
- `LogoHelper` and `logo_downloader`: an empty ESPN logo list counts as a failed download, and the placeholder is written at the requested path.
- The `SportsCoreSharedMixin` helpers that behave identically to their
  `sports_card` twins (`_card_option`, `_vs_text`, `_format_game_time`,
  `_coerce_rgb`, `_crisp_size`, `_unshare_element_fonts`, the colour/month/
  weekday/font-grid tables) are now thin wrappers over the `sports_card`
  functions, and `_format_game_date` / `_schema_font_size` share its
  formatting body and schema parser. No method was removed or renamed and
  nothing renders differently: `test/test_sports_twins.py` checks each pair
  against the same inputs, and the old and new mixin agree on every input
  there. The pairs that do differ -- favourite-result colours on nested
  payloads, the weekday's timezone, the element-name map, per-mode colours --
  are left as they are and pinned in that test.
- `download_missing_logo` / `LogoDownloader.download_logo` (the path the
  scoreboard plugins use) now stream the logo with a 10 MB cap, accept only an
  `image/*` response that Pillow can decode, and move the finished RGBA PNG
  into place atomically. A failed, oversized or non-image download no longer
  leaves a partial file behind, and no longer replaces a logo already on disk.
  `LogoHelper._download_logo` goes through the same code. Signatures and return
  values are unchanged; saved files are pixel-identical to before.
- `download_missing_logo` reuses one downloader (one `requests.Session`) per
  thread instead of building a new one for every logo.
- Placeholder logos are written atomically, without the `test_write.tmp`
  probe file.
- The logo downloader and the background data service send the real
  `LEDMatrix/1.0 (+https://github.com/ChuckBuilds/LEDMatrix)` User-Agent
  instead of a `yourusername` / `contact@example.com` placeholder, and no
  longer set `Accept-Encoding: ... br` by hand (brotli is not installed, so a
  `br` response could not be decoded); requests picks the encodings.

### Scrolling

- **Scoreboard scroll speed no longer changes with the General tab's "Scroll
  Frame Rate" (`target_fps`).** Scoreboards on `src.common.sports_scroll`
  computed their speed for that rate while the panel kept presenting at its
  real refresh, so on a 100 Hz panel 60 ran a 50 px/s scoreboard at 100 px/s
  and 200 ran it at 25 px/s. Speed now comes from `scroll_speed` and the panel
  refresh only. The field is labelled legacy: nothing in core scrolling reads
  it. Anyone who lowered it will see scoreboards scroll slower than before --
  at the speed they configured.
- `scripts/scroll_speeds.py --measure` / `--demo` open the panel with the
  display service's own options (`DisplayManager.apply_matrix_options`), so
  `display.runtime.gpio_slowdown`, `rp1_rio`, `panel_type` and orientation are
  honoured; the script used to read `gpio_slowdown` from `display.hardware`.
  Its closing advice now gives the `scroll_speed` + `scroll_delay` pair
  instead of `scroll_pixels_per_second`, which the resolver ignores whenever
  the pair is present.
- The frame-stats log no longer opens a scroll with a one-frame window for
  scrollers that never call `reset_scroll()`.
- Removed dead scroll code: the optional scipy import (`HAS_SCIPY`),
  `ScrollHelper._last_integer_position` and `frame_time_target`.
  `ScrollHelper.target_fps` / `set_target_fps()` remain, documented as
  informational.
- Docs describe the fixed-step scroll model: `PLUGIN_API_REFERENCE.md`
  documents `set_scrolling_state(..., frame_hold)` (omitting the hold runs a
  scroll `frame_hold` times too fast), `SCROLL_PERFORMANCE.md` no longer reads a
  held 20 ms frame as missed refreshes, and Vegas `frame_based_scrolling` /
  `scroll_delay` are described as the speed clamp they are rather than frame
  stepping. Scoreboard `scroll_delay` is documented as ignored for pacing.
- `ScrollHelper.set_scrolling_image()` accepts RGBA, L and palette images (transparent pixels become black), and a new scrolling image no longer jumps ahead by the time the helper sat idle.
- `src.common.frame_timing` -- times every frame the display presents, whoever
  drew it, and writes cumulative counters to `/dev/shm`. Two tools read it:
  `scripts/frame_soak.py` judges a running service (late frames, freezes,
  where the time goes), and `scripts/render_bench.py` judges the hardware and
  render path alone on a synthetic strip. Both fail a run above 0.1% late
  frames, and both call a loop that never waited for the panel NOT LOCKED. A
  stall watchdog logs the stack of whatever holds a scroll up for 250 ms or
  more. See `docs/SCROLL_PERFORMANCE.md`, "Soaking a rig".
- `display.scan_order_compensation` (`"auto"` by default): while something
  scrolls at one pixel per refresh, one half of each panel is shown a refresh
  behind the other, which removes the 1px step a 1:N-scan panel shows across
  its middle. Only for layouts whose row order is known; `"off"` disables it.
  See `docs/SCROLL_PERFORMANCE.md`, "A tear across the middle on fast scrolls".

### Display and Vegas

- Vegas scrolls in step with the panel's refresh (#628). With `smooth_scroll`
  (on by default) the strip moves a whole number of pixels per presented
  frame, each held for `frame_hold` refreshes and timed by `SwapOnVSync`,
  the same pacing as the plugin tickers; it used to advance by elapsed time
  and sleep to `target_fps`, missing a vsync every few frames. The speed is
  solved against the panel's measured refresh when that is below its
  `limit_refresh_rate_hz` cap. The old sub-pixel blend, which the panel shows
  as shimmer, is kept as `vegas_scroll.sub_pixel_blend` (default off). While
  scrolling, the web preview's PNG is encoded on a background writer instead
  of the render thread. On a Pi 4 driving 512x64, late frames went from about
  6.4% to 0.7%.
- Vegas prepares plugin content off the render thread (#630). A plugin that
  needed the shared canvas used to be fetched on the render thread, stalling
  the scroll for as long as it took (320 ms for news, 660 ms for a hockey
  scoreboard, measured). `DisplayManager.offscreen(width, height)` gives the
  calling thread a canvas of its own: `image`, `draw` and `matrix` are now
  properties that resolve to it inside the block, where `update_display()`,
  the hardware half of `clear()` and `set_scrolling_state()` /
  `set_frame_hold()` do nothing. Background fetches take the plugin's lock,
  waiting up to 2 s for a running `update()` and otherwise skipping the plugin
  that round. A GIL gate (`src/common/render_gate.py`) pauses the prefetch
  thread outside a window around each vsync swap, so the render thread finds
  the GIL free; it needs the rebuilt binding that releases the GIL in
  `SwapOnVSync` and stays off (one INFO line per Vegas run) on a stock one.
  New `vegas_scroll` keys: `offscreen_prefetch` and `prefetch_gate` (both on by
  default) and `switch_interval_ms` (experimental, default 0, off). Design in
  `docs/OFFSCREEN_RENDERING.md`.
- On-demand requests, the display on/off schedule and brightness take effect
  within about a quarter of a second instead of at the next screen (#618). A
  screen can stay up for a minute and a Vegas iteration for 240 s, so an
  on-demand request during Vegas waited for the iteration to end and a
  brightness save mid-screen could be lost. Vegas now stops for an on-demand
  request and for the display being scheduled off, and a brightness change
  re-sends the current frame. Plugin enable/disable, screen durations and Vegas
  settings still apply at the next screen.
- The Rotation & Durations page takes effect (#605). A saved
  `display.display_durations` value now wins over the plugin's own duration;
  the plugin was asked first, and every plugin inherits
  `get_display_duration()`, so saved values did nothing. The page shows an
  unsaved screen blank with the plugin's own duration as the placeholder (it
  showed 30 where the real default is 15), and saving a blank removes the
  override. Durations saved before this now apply.
- Vegas settings reach a running scroll (#605): they are queued when
  `display.vegas_scroll` changes (unrelated saves don't rebuild the strip) and
  also applied while Vegas is stopped. The sync follower's scroll-speed
  default (75) now matches `VegasModeConfig`'s (50). The Vegas live-priority
  scan is throttled to 4 Hz.
- `DisplayManager.defer_update()` from a plugin's update thread no longer loses queued updates while the render thread processes the queue; the queue is locked, and the queued callables still run outside the lock.
- **Behaviour change:** when `display.hardware.limit_refresh_rate_hz` is missing from config, the panel is now capped at 100 Hz (the config template's value) instead of 90 Hz. Scroll pacing already assumed 100 Hz in that case, so it now matches what the panel does. Configs that set the key (every config migrated from the template) are unaffected.
- A sync follower adopts the leader's scroll image between frames on the render thread, instead of the TCP thread swapping the image, array and width while a frame is being drawn.
- `update_display()` errors are logged once with a traceback, then at most once a minute with a count, instead of an untraced line every frame. Several swallowed exceptions in `DisplayController` now log at DEBUG.
- The repo-root `display_controller.py` now runs `run.py` (the real entry point), so it gets run.py's `-e`/`-d` flags, logging setup and `sys.dont_write_bytecode`.
- Vegas: a plugin set to `vegas_mode: "static"` pauses the scroll for its turn again. The pause was triggered by peeking at the front of a segment buffer that continuous scrolling (the default) never advances, so a static plugin paused only if it happened to be first, once, at startup, and otherwise scrolled past as ordinary content; swap mode had the same problem for any static plugin not first in its cycle. The render pipeline now marks where each static plugin's turn falls in the strip and the scroll pauses when it gets there. The pause runs the plugin's `display()` under its plugin lock, and a static plugin's content is no longer rendered for the strip.
- The display loop no longer spins at 100% CPU when no enabled mode has anything to show (for example, only a sports plugin enabled in its off-season). After one full rotation of empty modes it checks one mode per second until something shows; live content still takes over at once.
- Stopping `ledmatrix.service` runs the controller's cleanup (SIGTERM now takes the Ctrl-C path).
- Turning Vegas on in the web UI works without a restart when it was off at startup.
- Vegas comes back after live content interrupts it. It stayed paused, and the display fell back to normal rotation until a restart.
- A day with dimming turned off in a per-day dim schedule stays at normal brightness. Before, brightness went back to dim for most of each minute.
- Stopping on-demand after a second request resumes rotation where it was first interrupted, not at the first request's screen.
- Turning Vegas off and on no longer shows content prepared for the previous run, including plugins disabled in between.
- How long a Vegas iteration runs is timed with the monotonic clock, so an NTP clock step on a Pi without an RTC doesn't cut it short or stretch it.
- The sync status file is removed when the display service stops, and at startup in standalone mode, so the web UI no longer reports a peer from an earlier run. Concurrent writes each use their own temp file.
- `render_gate.swap_releases_gil()` delegates to `frame_timing.binding_releases_gil()` instead of duplicating it.
- Vegas `max_cycle_duration` defaults to 240 s when unset, as documented (it was 600 s). The Vegas defaults are now defined once.
- The display controller stops Vegas mode on shutdown.
- Startup validation warnings are logged once, not twice.
- Vegas logs one INFO line per plugin-list refresh.
- `run.py -d` shows `display_manager` debug output.
- Removed: the Vegas staging buffer that was never filled (`swap_buffers()`, and `staging_count` / `current_index` in `get_buffer_status()`), unread `ContentSegment` fields, and `geometry.find_blank_cut()`.

### Web interface

- The plugin settings form honours `"x-display": "hidden"` in config schemas:
  the property gets no control at any depth (top level, nested objects, array
  rows, Advanced Settings), and saving the form never changes its stored value.
  JSON API saves are unaffected. Lets plugins keep deprecated or internal keys
  declared, e.g. countdown's row `id` and weather's `api_key` / `radar_zoom`.
  See `docs/widget-guide.md`.
- Display settings no longer silently cut values on save: columns were capped
  at 128, chain length at 24 and PWM LSB nanoseconds at 500. Columns have no
  upper limit, chain length is 1–255 and rows must be even and 8–64 (see
  "Display hardware settings the library refuses" below);
  parallel is 1–3 and PWM dither bits 0–2, matching the library. A stored GPIO
  slowdown, PWM dither bits or refresh-rate cap of 0 no longer shows (and
  re-saves) as 3, 1 or 120, and the refresh cap accepts 0 (no cap). The config
  API rejects out-of-range or non-integer `rows`, `cols`, `chain_length`,
  `parallel`, `brightness`, `scan_mode`, `pwm_bits`, `pwm_dither_bits`,
  `pwm_lsb_nanoseconds`, `limit_refresh_rate_hz`, `row_address_type`,
  `multiplexing` and `gpio_slowdown` with a 400 (JSON `true` or `5.5` used to
  save as 1 or 5) instead of saving a config the matrix refuses to start with.
- Display setting help tips and README / config-reference entries corrected
  and completed: `panel_type` and `rp1_rio` are documented,
  `show_refresh_rate` prints to the console rather than drawing on the panel,
  PWM dither bits raise the refresh rate rather than lowering it, and every
  numeric setting states its range.
- Row Address Type offers 5, the SM5368 / B707 row shift register. The
  Waveshare 96x48 V2 panel (back silkscreen `24S-A1`) needs it with RGB
  sequence BGR and, on a Pi 4, a GPIO slowdown of 6–8. Panels with FM6124
  column drivers need no Panel Type.
- On a Raspberry Pi 5 the pinned rgbmatrix library can drive only row address
  types 0 and 2, parallel 1–3 and the standard mappings. For anything else it
  returns no matrix, which the Python binding doesn't catch, so the display
  service crashed and restarted every 10 seconds. `DisplayManager` now refuses
  those settings before creating the matrix (logged, reported by
  `/api/v3/hardware/status`, fallback mode), the config API rejects them, and
  the Display form offers only row address types 0 and 2 on a Pi 5. The rule
  lives in `src/pi5_matrix_support.py` and must be re-checked when the
  submodule is bumped.
- The Plugin Config Warning no longer lists core settings as plugins that are
  "in config but not installed" (seen as `auto_update` on 3.4.0, where the
  advice would have deleted the weekly-update setting). Core top-level config
  keys now live in one list, `src/core_config_keys.py`, which reconciliation
  uses and tests pin to `config.template.json` and the settings save endpoint.
  A stored warning is also dropped once its entry is no longer a plugin in
  config, so an old verdict clears without a restart.
- **Check & Update All** no longer sends installed Starlark apps
  (`starlark:<app_id>` entries in `/plugins/installed`) to the plugin updater,
  which answered each with a 500 "plugin not found". `POST /plugins/update`
  now answers a `starlark:` id with a 400 saying it is a Starlark app. A
  request that gets no HTTP answer (e.g. the web service restarting mid-run) is
  re-sent with backoff instead of being counted as failed and skipped — that is
  how a disabled plugin with an update waiting was silently left out.
- Three routes consulted the web process's plugin manifests without
  discovering plugins first, so they misbehaved from every `ledmatrix-web`
  restart until something else ran a discovery — in practice until someone
  opened the dashboard, measured at over three minutes on one rig.
  `POST /display/on-demand/start` and `POST /plugins/toggle` answered 404
  "Plugin not found", and `POST /config/main` did not recognise a plugin
  section, so it skipped secret separation and wrote the plugin's API key to
  `config.json` in plain text instead of `config_secrets.json`. The routes now
  discover when nothing has been discovered yet, and rescan once when a
  specific plugin id (or, for on-demand by mode, a mode) is not found, so a
  plugin installed since the last scan is found too.
- `web_interface/blueprints/api_v3/plugins.py` (3,285 lines) is split by area into `plugins.py` (installed list, enable/disable, plugin actions), `plugin_store.py`, `plugin_config.py`, `plugin_assets.py`, `plugin_health.py`, `plugin_operations.py` and `plugin_calendar.py`. Pure move: every function body and route decorator is byte-identical, and URLs and endpoint names are unchanged.
- Plugins installed as `ledmatrix-<id>` (or in a directory not named after their id) work in the installed list, the update button, recorded versions, the plugin config form and plugin web UI pages. Those routes built `plugins_dir/<id>` themselves instead of asking the plugin manager.
- Uploading several plugin images checks every file before saving any, so a rejected file no longer leaves the others saved; the images' `.metadata.json` and the calendar plugin's `credentials.json` are written atomically, and the credentials upload no longer returns the server's absolute path.
- `"false"` sent as a string no longer counts as true when toggling a plugin (including Starlark apps) or starting on-demand mode (`pinned`, `start_service`); `force` on the AP-enable route is parsed like every other WiFi boolean (`"yes"` and `1` now force).
- The live-preview stream starts a new broadcast thread for a client that connects while the previous one is shutting down; that client got no updates.
- The web server's log filter no longer raises when werkzeug logs with `exc_info=True`.
- The raw secrets editor's save errors carry `error_code` like the main config's; the asset delete route answers 400 for a missing body instead of 415/500. Dead code removed: an unused manifest scan on each Plugins-tab load, backup routes' duplicate catch-alls, redundant imports.
- A plugin's own config widget (`/static/plugin-widgets/<id>/<widget>.js`) is requested with `?v=<plugin version>`, so an updated plugin's widget reaches browsers instead of the copy cached as immutable for a year.
- A failed installed-plugins reload after a toggle, install or uninstall shows one error, not a second generic "unexpected error" toast.
- The timezone picker on the General tab renders again when the tab is reloaded in the same page session.
- Removed dead code: the plugin-action button's six plugin-id fallbacks (the button always passes its id) and its `[DEBUG]` logging, `window.currentPluginConfig` (never set to anything but `null`), the file-upload widget's JSON delete branch (its endpoint never existed), unused `PluginAPI` / `PluginInstallManager` / `PluginStateManager` helpers, `loadPluginWidgetsFromManifest`, no-longer-reachable fallbacks for a stale `install_manager.js` and a missing `LEDVisibility`, and 13 unused CSS utility rules.
- The Logs tab's "Now showing" no longer reads "unknown" when one screen stays up longer than 2 minutes.
- A network failure fetching GitHub repo info logs a warning, not an error.
- The Operation History plugin filter lists installed plugins (it showed one option, "plugins").
- Ctrl/Cmd+S submits the active tab's visible form (with its validation) instead of the first form in the page; it does nothing inside a dialog or on a tab without a form. The Ctrl/Cmd+R override (the browser's own reload) and the textarea auto-resize (no textarea exists at load) are removed.
- Tools tab actions and diagnostics show the server's error message; only a non-JSON error falls back to `HTTP <status>`.
- An uninstalled plugin no longer reappears in the installed list: writes through `PluginAPI` clear its 5s GET cache, and Refresh and the post-uninstall reload bypass both list caches.
- Plugin widgets load from `/static/plugin-widgets/` only; the two other paths it tried have no route.
- The raw JSON editor escapes the parse error, and the slider widget escapes its value, min, max and step.
- Removed unused array-of-objects and key-value helpers from `plugins_manager.js` (about 640 lines, no callers) and a redundant `?v=` on its script tag.
- Plugin tabs show the manifest's `icon`: `/api/v3/plugins/installed` now includes it.
- `POST /api/v3/starlark/apps/<id>/toggle` goes through the same code as `/plugins/toggle`: `"false"` disables, a failed save no longer leaves the running app out of step with disk, and a loaded app with no manifest entry no longer answers 500.
- `/api/v3/` JSON responses are sent `Cache-Control: no-store`, so a reload right after an install, toggle or Wi-Fi connect shows the new state. Non-JSON files served through the API keep the 5 s cache.
- Startup plugin validation no longer gives up on a `null` plugin block, and plugins are discovered once at startup instead of twice.
- A plugin save drops repeated entries in lists whose schema says `uniqueItems`, instead of failing validation.
- `/api/v3/health` reports the real plugin count.
- A malformed `vegas_plugin_order` or `vegas_excluded_plugins` is refused with a 400 and nothing is saved. It used to wipe the saved list.
- The per-plugin health and metrics routes return the display service's latest state.
- Resetting a plugin's config takes a backup first and reports a failed save.
- System metrics that can't be read are `null` everywhere: `cpu_temp` off a Pi, and every metric without psutil, where `/system/status` now answers 200 instead of 503.
- `/plugins/store/refresh` no longer claims a commit-metadata refresh it doesn't do.
- The plugin-config list repair code is in one place, `src/web_interface/config_arrays.py`.
- Cache tab errors no longer show up in the Logs tab.
- A tab that fails to load shows "Try again" instead of a skeleton that never goes away.
- Plugin Store search and registry errors appear as a notification, and the Plugin Manager stays on screen.
- The image schedule button works on uploaded images, and the editor stays open while you edit.
- A failed plugin toggle moves the switch back.
- Each save shows one notification; a failed Durations save says it failed.
- Stats the server can't read show `--`.
- New `window.LEDEscape` (`html`, `attr`, `jsStringAttr`) replaces about 30 copied escapers. `window.escapeHtml` and `window.escapeAttribute` remain as aliases for plugin pages.
- The web service (`ledmatrix-web`) logs through `src.logging_config` like the
  display service, so `journalctl -p err -u ledmatrix-web` works. Successful
  GET/HEAD/OPTIONS requests (the UI's polling) are logged at DEBUG instead of
  INFO; 4xx at WARNING, 5xx at ERROR. `LEDMATRIX_DEBUG=true` shows them again.
  `web_interface/logging_config.py` is removed. The web cache
  (`web_interface/cache.py`) now honours the TTL a value was stored with and is
  thread-safe.
- `/api/v3` routes answer an exception they don't handle themselves from one
  blueprint error handler, with the same `{status, message, details}` body the
  53 removed per-route catch-alls returned. `ErrorCategory` and the
  `error_category` key are removed from `src.web_interface.errors` (nothing read
  them); `exception_error_response()` replaces the `from_exception` +
  `error_response` pairs. A failing plugin action script's error now names the
  real failure instead of `UnboundLocalError`.
- Installing a Starlark app works on a fresh install (#604). `starlark-apps/`
  is created by whichever service reaches it first, and on a fresh install
  that was usually the root display service, so the web interface could not
  write to it and every install path answered "Failed to install from
  repository". The display service now hands the directory and its contents
  to the checkout's owner on every start (a no-op when not root or when the
  checkout belongs to root), which also repairs devices already affected; a
  permission error from the install routes names the directory and the fix.
- Clicks on plugin cards reach `handlePluginAction` (#605). Every click took
  a copied fallback that asked twice before uninstalling and sent Starlark app
  uninstalls to `POST /plugins/uninstall` instead of
  `DELETE /starlark/apps/<id>`. A failed plugin toggle no longer always says
  "A plugin operation is already in progress".
- The web interface starts with an absolute `plugin_system.plugins_directory`
  (#616); it crashed at import with `NameError: project_root`.
- Stopping a Pixlet editor that ignores SIGTERM restarts the display instead
  of answering 500 and leaving the panel dark (#625).
- Removed dead routes and files (#609): `POST /plugins/authenticate/spotify`
  and `/ytm` (the music plugin runs its auth scripts through `web_ui_actions`),
  `POST /plugins/of-the-day/json/upload` and `/json/delete` (they used the
  wrong plugin id), `js/plugins/store_manager.js`, `js/config/diff_viewer.js`
  and `js/htmx-sse.js`, and `web_interface/run.sh`. `htmx-config.js` no longer
  replaces `console.error` / `console.warn`, which hid some real errors.

### Plugin error reporting

- `/api/v3/errors/summary` and `/api/v3/errors/plugin/<id>` report the errors
  the display service recorded. They used to read the web process's own error
  aggregator, which never records anything, so they always answered "no
  errors". The display service now publishes a bounded snapshot to the shared
  cache (`plugin_error_snapshot`, at most every 10 seconds and only on change;
  `src/error_aggregator.py`, started from `DisplayController.__init__`).
  Responses keep their shape and add `snapshot_available`, `generated_at` and
  `clear_pending`; exception text has credentials redacted.
- `POST /api/v3/errors/clear` records a request (`plugin_error_clear_request`)
  the display service applies within about 5 seconds; reads hide the cleared
  errors at once. It accepts `"all": true`, and `cleared_count` can be `null`
  when the count is only known to the display service.
- The Logs tab has a **Plugin errors** panel: per-plugin counts, repeating
  errors and a Clear button.
- Credential redaction in exception text (`src/redaction.py`) takes time
  proportional to the text, not its square. Two patterns were quadratic: URL
  `user:password@`, on a long unbroken run of letters or digits (a hex digest,
  an ID), and `Authorization:` followed by a long run of whitespace. Either
  used to stall every thread of the display service for up to seconds each
  time the snapshot was published: about 0.5s for 20k characters of hex, 8s
  for 20k spaces. What gets redacted is unchanged.

### Wi-Fi

- WiFi status messages reach the panel (#605). The display controller looked
  for `wifi_status.json` one directory above the repo; both sides now use
  `wifi_manager.get_wifi_status_path()`, the file is written atomically, and
  the plugin that resumes afterwards redraws the whole panel.
- The captive-portal checks (`/generate_204` and friends) also detect an access point brought up through NetworkManager, the fallback `enable_ap_mode` uses without hostapd; only hostapd was checked, so phones on that AP were told the internet worked.
- The WiFi monitor daemon re-reads `wifi_config.json` when it changes, so the "auto-enable AP mode" toggle takes effect without restarting the daemon.
- Disconnecting from WiFi in the web UI no longer runs an AP-mode check that could never enable the AP; it only added seconds of waiting. The daemon still enables the AP after its grace period.
- The WiFi status message file follows each WiFi manager's own config directory, and the config path falls back to this checkout rather than `/home/ledpi/LEDMatrix`.
- A wrong Wi-Fi password is reported as one again ("Incorrect password for ..."); the fallback that restores the old network or brings up the setup AP was replacing the signal.
- Wi-Fi disconnect takes the saved connection profile down.
- `wifi_config.json` is written atomically, and a save that fails now gets a 500.

### Fonts

- Fonts tab: the preview endpoint renders BDF fonts with the panel's own rasterizer instead of refusing them. (The Fonts page still skips the request for `.bdf`; enabling it there is a separate template change.)
- A plugin font declared as a `.zip` URL is served as the font extracted from it after a restart, instead of registering the archive itself. Font downloads time out after 30s and land in the cache only once complete, so an interrupted download is retried rather than served forever.
- BDF fonts: `FontManager.get_font()` and `element_style.load_font()` no longer hand one `freetype.Face` to every thread. BDF faces come from `load_bdf_face`, which already caches them per thread; TrueType fonts are cached as before. `element_style`'s font cache is locked (a concurrent eviction could raise `KeyError`).
- `FontManager.clear_cache()` and unregistering a plugin's fonts bump `cache_generation`, so cached layouts are rebuilt.
- `plugin://` fonts load from the plugin's own install directory. `FontManager.register_plugin_fonts()` takes an optional `plugin_dir`.
- Bundled font paths no longer depend on the directory the process was started from.
- `FontManager.get_font()` returns a BDF font at its native size when asked for
  a size the file doesn't contain (5x7.bdf at 8 or 10px, say). It used to
  return PIL's default font, a different typeface, so a plugin that relied on
  that will now render the font it asked for.
- The web UI's Fonts tab has a **Used by** column: the loaded plugins that
  registered each font with `FontManager.register_manager_font()`, published
  by the display service to the shared cache (`src/font_usage.py`) and merged
  into `GET /api/v3/fonts/catalog` as `used_by`. Deleting a font a plugin
  uses now names those plugins in the confirmation (it is not blocked).
  `FontManager.forget_manager_fonts()` is new; unloading a plugin calls it.

### Security

- `POST /api/v3/plugins/assets/upload`, `GET .../assets/list` and
  `POST .../assets/delete` validate `plugin_id` with `src/common/path_safety`
  and answer 400 otherwise. A `plugin_id` of `../../config` used to create an
  `uploads/` directory outside `assets/plugins`, write images and
  `.metadata.json` there, list it, and delete whatever file a metadata entry
  named. Delete now unlinks only a path that resolves inside that plugin's
  uploads directory (any other entry is dropped without touching a file).
- `PluginManager.get_plugin_directory()` returns `None` for anything but a
  plain name, so `POST /api/v3/plugins/action` can no longer run a manifest
  script from a directory outside the plugins directory (`../elsewhere`); the
  route also rejects such ids with 400.
- Plugin Store, saved-repository and custom-registry buttons escape registry
  values for their inline `onclick` handlers (`jsStringAttr` in
  `plugins_manager.js`). An entry id containing `'` used to close the attribute
  and add its own script. The store's View button opens only `http(s)` links.
- The uploaded-images list escapes each file's original name, path and ids; a
  name like `<img src=x onerror=...>.png` was inserted as markup.
- Installing from a URL (and a registry install whose manifest renames the plugin) refuses a plugin id that isn't a single safe name, so `../x` can no longer delete and replace a directory outside the plugins directory.
- Plugin uninstall and config reset refuse core config sections (`display`, `schedule`, ...) and ids with path parts. Uninstall still cleans the config of a plugin whose directory is already gone.
- A config field marked `x-secret` whose value is an object or array is saved to `config_secrets.json`, not to `config.json` in plain text.
- Restoring a backup onto a device without `config_secrets.json`, `wifi_config.json` or `ytm_auth.json` creates them with mode 640 instead of world-readable 644.
- Backup export skips a plugin `manifest.json` that isn't a JSON object instead of failing, and two exports in the same second no longer share a temp file or overwrite each other (the second gets a `-2` suffix).
- Every font that ships in `assets/fonts/` is protected from deletion; `MatrixChunky8X`, `MatrixLight6X`, `MatrixLight8X` and `ic8x8u` could be deleted from the Fonts tab.
- The raw config and secrets editors, and endpoints using `validate_request_json`, answer 400 for a JSON body that isn't an object.
- `fix_web_permissions.sh` makes `safe_plugin_rm.sh` and `safe_pip_install.sh` root-owned again after resetting ownership. A web-user-owned copy of either is a root shell, since sudo lets the web user run them as root. It also restores `config_secrets.json` to mode 640.
- Wi-Fi passwords are no longer stored in `config/wifi_config.json` (#608).
  `WiFiManager` appended every joined network's SSID and password, in plain
  text, to `saved_networks`, and nothing read them back (NetworkManager keeps
  its own credentials). Loading the config now drops a `saved_networks` key and
  rewrites the file, so passwords already on disk are removed.
- The installers no longer grant the web user passwordless root on
  `display_controller.py`, `start_display.sh` and `stop_display.sh` (#606).
  Those files are owned by the user, so the web user could rewrite them and
  run them as root; nothing ran them through sudo. Existing devices keep the
  old rules until the installer or `configure_web_sudo.sh` is run again.

### Display hardware settings the library refuses

- The rgbmatrix library answers several settings with no matrix or `abort()`
  rather than an error, on every board, so the display service crash-looped
  instead of falling back: rows above 64, `chain_length` above 255 (the Python
  binding stores it in one byte; this was documented as "no upper limit"), a
  misspelled `hardware_mapping`, and `parallel` 2–3 on a mapping with one output
  (`adafruit-hat`, `adafruit-hat-pwm`, `regular-pi1`, `classic-pi1`) — the last
  one reachable from the Display form on the default mapping. The config API
  now refuses them with a 400 naming the setting, and `DisplayManager` refuses
  a hand-edited one before creating the matrix: logged, fallback mode, reported
  by `/api/v3/hardware/status`. The rules, including the Pi 5 ones, live in
  `src/matrix_support.py` and must be re-checked when the submodule is bumped.
- `/api/v3/hardware/status` adds `cause`: `"settings"` when LEDMatrix refused
  the config, `"library"` when the library failed. The Display tab banner and
  the fallback log line give the Pi 5 rebuild hint only for a library failure;
  they used to follow every failure with it and with GPIO slowdown advice.
- The Display form offers the `classic` and `classic-pi1` mappings and the
  `90` / `270` orientations, and renders any other stored mapping selected with
  a warning. With no option selected the browser posted the first one, so one
  unrelated save rewrote those settings. The API accepts orientation `90` and
  `270`, which `DisplayManager` already applied.
- The display size the web preview, Starlark magnify default and
  `scripts/dev/vegas_audit.py` compute (`src/display_geometry.py`) now applies
  `orientation` and `pixel_mapper_config` as the library does: `Rotate:90`
  swaps width and height, `U-mapper` folds the chain.
- One Raspberry Pi 5 GPIO slowdown recommendation everywhere: 1–3 in PIO mode,
  starting at 1. README and the config reference now describe the template
  values as the defaults; the "code default" values they listed never apply,
  because config migration fills missing keys from the template.

### Plugin system

- A plugin no longer starts with a schema warning and a degraded flag because
  config.json still holds a boolean where its schema now has an object with an
  `enabled` property (news' `global.dynamic_duration: true`). The loader reads
  the boolean as `{"enabled": <bool>}` before merging schema defaults and
  validating, the same rule the settings form already applies
  (`legacy_bool_as_object` in `src/plugin_system/schema_manager.py`). Nothing
  is written at load; the next save of that plugin's settings stores the object.
  Other type mismatches still warn.
- A plugin that is reloaded (switched off and on again from the web UI) imports its own modules again, not another plugin's. Plugins import their own files by bare name (`from sports import ...`), which resolves to the first plugin directory on `sys.path` that has the file; the loader only added a directory that was missing, so a reloaded plugin's directory stayed behind any loaded since. On a Pi, re-enabling UFC with hockey running failed with "cannot import name '_status_is_final' from 'sports'". A loading plugin's directory is now always moved to the front.
- `src/plugin_system/store_manager.py` (2,977 lines) is split into mixins: `store_registry.py` (registry, GitHub metadata, search, manifest validation), `store_install.py` (install paths and dependencies) and `store_update.py` (updates, rollback, local git state). `PluginStoreManager` is still imported from `store_manager.py` and has exactly the same methods and attributes; every method body is byte-identical.
- Unloading a plugin waits (up to 5s) for an in-flight `update()` before running `cleanup()`/`on_disable()`, and an update that finishes after the unload no longer puts the plugin back to ENABLED.
- A plugin whose load fails after its module was imported (constructor, `validate_config()` or `on_enable()` raising) no longer leaves that module cached: fixing the plugin and reloading it runs the new code without a restart. Its font registrations are dropped too.
- `POST /api/v3/plugins/limits/<id>` answers 400 for a limit that isn't a non-negative number (a string limit used to make every later update of that plugin raise). A bad cached limits record is ignored with a warning instead of raising.
- The config schema is found for a plugin installed as `ledmatrix-<id>` or in a directory named differently from its manifest id, resolved the way the loader resolves it (plugins/ is still searched before plugin-repos/). A plugin with no schema is logged once at DEBUG instead of a warning on every lookup.
- Installing from a URL over an existing install sets the old copy aside and restores it if the move fails, under the same per-plugin lock as a registry install.
- The operation queue refuses a second operation for a plugin whose first is still waiting (a double-clicked Install ran twice), and no longer keeps every finished operation in memory.
- `get_vegas_render_width()` reads `display_manager.width` first, as plugins are told to.
- Store and state files are read as UTF-8 regardless of the system locale.
- Docs: `update_interval` in `config.json` sets the scheduler's cadence only for a plugin whose manifest has none (TROUBLESHOOTING, PLUGIN_CONFIGURATION_GUIDE). The health/metrics reset and limits routes note that they only change the web process's view.
- A plugin whose `on_enable()` raises is no longer left registered: the next load retries it instead of reporting "already loaded" for a plugin that never ran.
- One plugin's `get_info()` raising no longer breaks the installed-plugins list; it is logged and shown with empty runtime info.
- `plugin_state.json` and the operation history are written atomically (temp file + rename) under their lock, so concurrent saves or a failed save can't leave a truncated file.
- Plugin dependency installs run one `pip` at a time during parallel startup loading.
- A failed store download no longer leaves its extraction directory in the temp dir.
- Test doubles: `draw_image()` on `MockDisplayManager`, `VisualTestDisplayManager` and `BoundsCheckingDisplayManager` now emits a `DeprecationWarning` — the real `DisplayManager` has no such method; use `display_manager.image.paste(img, (x, y))`. `MockDisplayManager.draw_text` accepts the real signature's `small_font`/`centered` and default `x`/`y`, and `VisualTestDisplayManager` logs draw errors at WARNING.
- Removed the unused `PluginOperationQueue.get_active_operations()`.
- Updating a plugin that was installed from a ZIP no longer tries to reinstall it from the LEDMatrix repository's own URL.
- Repository URLs with `.git` in the middle are no longer mangled. The URL helpers now live in `src/plugin_system/repo_urls.py`.
- Installing from a URL works when the repository's only branch isn't `main` or `master`.
- A missing required config field is reported once, by name.
- A plugin that went over `max_memory_mb` once is no longer refused on every call after that.
- `reload_plugin` reads the manifest from the plugin's discovered directory.
- Removed: `last_display` from plugin state info and `get_last_display()` (nothing recorded them); `PluginOperationQueue`'s `history_file` and `lazy_load` arguments; and `data/plugin_operations.json`, which nothing read.
- One plugin-directory resolver, `src/plugin_system/plugin_dirs.py`, behind
  discovery, `PluginManager.get_plugin_directory`, `PluginLoader`, the store and
  state reconciliation. A manifest's `id` wins over a directory merely named for
  the id; hidden and `.standalone-backup-` directories are never treated as
  plugins (auto-update could previously try to update a backup); ids like
  `a/b` or `..` resolve to nothing everywhere. Installs where each directory is
  named for its manifest id, the installer's layout, behave as before.

### Core

- `ConfigManager.load_config()` no longer raises on a host without the POSIX
  ownership APIs. The self-heal that chgrp's `config_secrets.json` to the
  shared group (added in #416) looked up `os.geteuid` unguarded; that name does
  not exist on Windows, and the resulting `AttributeError` is not an `OSError`,
  so it escaped the helper's own "best-effort" handling and every caller's.
  Any Windows checkout with a `config/config_secrets.json` got a `ConfigError`
  from every config load and could not `import web_interface.app` at all.
  `ensure_shared_group_ownership()` now returns immediately when `os.geteuid`
  or `os.chown` is missing. No behaviour change on the Pi.
- Restoring a backup on Windows no longer fails over files that already exist.
  The restore carries each replaced file's owner across with `os.chown`, which
  does not exist on Windows; the `AttributeError` escaped the per-file error
  handling, so the restore stopped at `config.json` with nothing restored. The
  ownership step is now skipped where `os.chown` is missing. No behaviour
  change on the Pi.
- `APIHelper`'s rate limit and the display-sync heartbeat/leader timeouts measure elapsed time with `time.monotonic()`. A wall-clock step (NTP correcting a Pi with no RTC) could stall API requests for as long as the step or fake a sync timeout. `get_request_stats()['last_request_time']` is still wall-clock time.
- `sudo_remove_directory()` tries each bash path the sudoers rule might name, as `install_requirements_file()` already did.
- An element's saved layout `scale` equal to its schema default is no longer treated as a user choice when the default is declared under an alias (`score` for `score_text`).
- `CacheError`/`ConfigError`/`PluginError`/`DisplayError` no longer write their key into the caller's `context` dict; the JSON log formatter stringifies values it can't encode instead of dropping the record.
- Removed `ErrorAggregator`'s unused JSON export (`export_path`, `export_to_file()`); nothing called it. Docstring fixes in `validate_file_upload`, `StartupValidator.raise_on_errors`, `DisplaySyncManager.set_on_new_cycle`, `dynamic_team_resolver` and `config_arrays`.
- `/api/v3/errors` shows each exception's real stack trace instead of `NoneType: None`.
- Backups record `src.__version__`.
- Removed: `BackgroundDataService`'s `queue_size` stat and `clear_completed_requests()`.
- `src.device_location` — a blank `Location` field on a Starlark (Tidbyt) app
  now renders at the device's City / State / Country (geocoded once via
  Open-Meteo and cached) instead of the app author's hard-coded default,
  usually San Francisco. A location saved on the app still wins. With no
  device city set, or when the lookup fails or finds no match, the app keeps
  its own default (a failed lookup is retried after 30 minutes). Clearing an
  app's location in the web UI now actually clears it; the save used to drop
  the blank field, so the old value stayed.
- Fixed a memory leak in the display service (#605): `ErrorAggregator`
  appended every plugin in the time window to a pattern's `affected_plugins`
  on each repeat (3,000 errors from three plugins reached 2.5 million
  entries).
- An expired cache record is refused without being parsed (#633).
  `CacheManager.set` writes `timestamp` and `ttl` ahead of `data`, and
  `DiskCache.get` reads the first 256 bytes to decide staleness, with the same
  rules as before. A 53 MB MLB season file used to be parsed in full (about
  1.8 s holding the GIL on a Pi 4, freezing the display) only to be thrown
  away. Files in the old layout are parsed as before and convert when
  rewritten.
- Cache internals (#613): `CacheManager` delegates memory-tier cleanup and
  stats to `MemoryCache`; `list_cache_files` no longer holds the memory lock
  during directory I/O; `BackgroundDataService.get_sport_cache_key()` formats
  the key instead of building a whole `CacheManager` (and probing the cache
  directory) on every call; the unused request queue is gone, and `priority=`
  is accepted and documented as ignored.

### Cache permissions

- The web interface can read what the display service caches again.
  `ledmatrix-web.service` carried `CacheDirectory=ledmatrix`, and systemd
  re-owns `/var/cache/ledmatrix` and its contents to the unit's `User=`
  whenever the directory's owner differs, which erased the `root:ledmatrix`
  setgid layout the installers set up: every file the root display service
  wrote afterwards was `root:root` 0660 and unreadable by the web interface
  (392 unreadable files on one rig, with display status, on-demand state and
  plugin health empty). Since #547 the web unit is rendered from its template
  on every install, so every fresh install hit this.
  `DiskCache.set` now gives each file the directory's group (when that
  directory is group-writable) and 0660 on the open descriptor before the
  rename, independent of setgid, which also closes a window where a fresh
  file was visible as mkstemp's 0600. `DiskCache.share_existing_files`
  repairs files an older version left behind, once per process, through
  `O_NOFOLLOW` descriptors, skipping hard links and other users' files.
  Existing installs only ever receive `git pull`, so that repair is the fix
  for them; new installs also drop `CacheDirectory=` and
  `CacheDirectoryMode=` from the web unit.
- `install_web_service.sh` replaces an existing cache directory's group
  whenever the installing user is not in it. It used to replace only root's,
  so a `root:ledmatrix` directory belonging to a user outside that group was
  left alone and everything root wrote there stayed unreadable.
- `/display/on-demand/status` and the current-display status read the display
  service's keys with `memory_ttl=0`, as every other cross-process reader
  already does. They served the first copy the web process had read for the
  full 120s `max_age`, so on-demand reported "active" for over 100 seconds
  after the file on disk said "idle".

### Automatic updates and Update Code

- An update that changes `web_interface/requirements.txt` is no longer rolled
  back on every auto-updating device. `safe_pip_install.sh` allowed only the
  root `requirements.txt`, so the install Update Code and the health check run
  for the web requirements was refused, and the health check rolls back any
  update whose dependencies failed (Install Base Requirements failed the same
  way). The wrapper now allows both core requirement files; a core requirement
  file symlinked out of the project is refused.
- The automatic update's local-change check and Update Code now count changes
  the same way (`auto_update.local_changes`): permission-only changes and
  anything under `plugins/` or `plugin-repos/` don't count, and a core path
  that merely contains `plugins/` does. Such edits used to pass the check and
  then be stashed by the pull and never restored, despite "will not stash your
  changes". The pull's `--autostash` now carries them across. Update Code
  still stashes other edits; the automatic update refuses instead.
- When the automatic update's own rollback fails (a partial pull, or a health
  check that never started), plugins are no longer updated and the display is
  not restarted, as the 3.4.0 notes promised.
- The health check's dependency reinstall no longer retries pip failures or
  timeouts with a second bash path, and all reinstalls share a 10-minute
  budget, so a rollback finishes inside the unit's 30-minute limit instead of
  being killed mid-way.
- A hand-edited non-object `auto_update` value reads as off instead of raising at startup, and a failed result write no longer leaves a temp file behind.
- Overview "Check Updates" asks for the same confirmation as "Update Code" and shows the server's message. Both, and the Tools tab's git pull, show the restart-pending banner when the update needs a restart.

### Installers

- The generated `ledmatrix_web` sudoers rules are parsed before they are
  installed. Both installers built the drop-in from `which` lookups and copied
  it into `/etc/sudoers.d` without ever checking it, and a malformed file there
  makes sudo refuse every command for every user — on a headless Pi, that is
  unrecoverable over SSH. `first_time_install.sh` now runs `visudo -c` on the
  generated file and, if it does not parse, prints what visudo said and leaves
  the installed file untouched instead of replacing it with a broken one;
  `configure_web_sudo.sh` does the same before offering the rules for
  confirmation. `first_time_install.sh` also built that file at a fixed `/tmp`
  path as root; `mktemp` now picks the name.
- `check_system_compatibility.sh` treats Python 3.13 (what Trixie ships) as supported and anything below 3.10 as an error.
- `configure_web_sudo.sh` run as the web user keeps the reboot/poweroff rules.
- `check_system_compatibility.sh` no longer reports installed packages as missing.
- `configure_wifi_permissions.sh` checks its rules with `visudo -c` before installing them, and grants the NetworkManager captive-portal `cp` and `rm` commands `wifi_manager` runs.
- `configure_web_sudo.sh` uses a random temp file and installs its rules with mode 440.
- The installer prints its completion summary before the `-y` reboot, and describes the setup access point as an open network (it was shown with a password it doesn't have).
- `fix_cache_permissions.sh` applies `setup_cache.sh`'s `ledmatrix`-group model instead of setting 777.
- `check_system_compatibility.sh` reports anything but Debian 13 (Trixie) as unsupported, and reaches its summary.
- `one-shot-install.sh`'s `retry()` retries (#606). It read `$?` after `!`,
  which is always 0, so a failed command ran once and was reported as a
  success. It now tries three times and returns the command's status; both
  apt steps still warn and continue after their retries, and a clone that
  keeps failing stops the install sooner, with its own message.
- One generator for the web sudoers rules, `scripts/install/lib_sudoers.sh`,
  used by `first_time_install.sh` and `configure_web_sudo.sh` (#622); the two
  copies had drifted.

### Small fixes (update-all, plugin system settings, scripts)

- **Check & Update All** counts a plugin that had nothing to update as
  "already up to date" instead of "updated". ZIP-installed monorepo plugins
  (most official ones) already at the registry version were called "updated
  successfully" on every run. `POST /plugins/update` now returns
  `data.update_status` (`updated`, `up_to_date`, `local_only`).
- An update request that got an HTTP error answer without an `error_code`, or
  a body that is not JSON (e.g. a reverse proxy's 502 page), is no longer
  classified as `NETWORK_ERROR` and re-sent five times. Only a request that got
  no HTTP answer is retried; the rest are `API_ERROR` with the HTTP status.
- The General tab no longer shows Auto Discover Plugins, Auto Load Enabled
  Plugins or Development Mode. Nothing read `plugin_system.auto_discover`,
  `auto_load_enabled` or `development_mode`: every enabled plugin was always
  discovered and loaded. Stored values are kept, and saving the General tab no
  longer rewrites them to `false`.
- `BackgroundDataService` shares the 6-hour "ESPN rejects date ranges" memo
  with `fetch_espn_scoreboard`, so a background season fetch no longer spends a
  doomed range request first once either path has seen a rejection.
- `scripts/install_plugin_dependencies.sh` installs from the configured
  `plugin_system.plugins_directory` (default `plugin-repos`, where the Plugin
  Store installs) and also scans `plugins/` for dev symlinks. It used to scan
  only `plugins/` and find nothing. A failed `pip install` is now reported as a
  failure instead of being hidden by `tee`.
- `scripts/verify_installation.sh` no longer fails a healthy install: it
  checked for the removed `web_interface_v2.py` and port 5001. It and
  `scripts/verify_web_ui.sh` now check port 5000, where the web interface
  listens.
- `scripts/install/install_service.sh --help` prints usage and exits without
  changes. It used to ignore the flag and reinstall and restart every service.
  Unknown arguments are rejected before anything runs.
- `scripts/diagnose_web_ui.sh`, `scripts/diagnose_web_interface.sh` and
  `scripts/debug/debug_web_manual.py` apply the launcher's own autostart rule
  (only an explicit `web_display_autostart: false` keeps the web interface
  down), so a missing key no longer shows as disabled. The shell scripts also
  check `web_interface/blueprints/api_v3/`, which became a package, instead of
  reporting `api_v3.py` as missing. (`scripts/verify_web_ui.sh`,
  `scripts/diagnose_web_ui.sh` and `scripts/debug/debug_web_manual.py` were
  later deleted as unreferenced; see Docs and developer tools.)

### Docs and developer tools

- `docs/REST_API_REFERENCE.md` rechecked against every handler: request
  fields that made documented calls fail (`repo_url`, `action_id`/`params`,
  `files`/`image_id`, `font_file`+`font_family`, `?font=`, cache `key`,
  `auto_enable_ap_mode`, plugin limit keys) and response shapes are fixed, the
  removed font-override endpoints are gone, and the 26 undocumented routes
  (backup, git/auto-update, WiFi radio, Starlark editor, MQTT bridge, status
  endpoints, skins) are listed. Store search is `/plugins/store/list?query=`.
- `FONT_MANAGER.md` no longer tells plugins to read
  `display_manager.font_manager`, which does not exist; use
  `plugin_manager.font_manager` / `BasePlugin._get_font_manager()`.
- Plugin docs, `DisplayManager` docstrings and the bundled `starlark-apps`
  plugin now all read the display size from `display_manager.width/height`,
  which works in fallback mode where `matrix` is `None`.
- `scripts/dev/dev_plugin_setup.sh link-github <name>` links the plugin from a
  clone of the `ledmatrix-plugins` monorepo (per-plugin `ledmatrix-<name>`
  repositories no longer exist). `dev_plugins.json` honours `github_user`,
  `plugins_repo` and `plugins_branch`; `dev_plugins.json.example` ships and
  `dev_plugins.json` is git-ignored. `update`/`status` handle monorepo links,
  and `status` no longer exits 1 when nothing is broken.
- Rewritten for current behaviour: plugin dependency installation (web service
  runs as the installing user and installs through `safe_pip_install.sh`),
  `PLUGIN_CONFIG_ARCHITECTURE.md`, `MULTI_ROOT_WORKSPACE_SETUP.md`; stale
  `app.py` line numbers, `api_v3.py` paths, StreamManager method names,
  nonexistent version-bump scripts and `ledmatrix` service user references
  removed.
- A mypy ratchet in CI. `mypy-clean.txt` lists the 71 modules under `src/` that type-check clean, and the new "Type check (mypy ratchet)" job runs `python scripts/check_types.py` (mypy 1.20.2 on exactly those files) so they stay clean; add a module when you make it clean (see CONTRIBUTING.md). The manual pre-commit `mypy` hook runs the same script. 35 modules were made clean for it with annotation-only fixes, no behaviour change. Their public signatures only widened (`declared_min_version()` now says it returns the manifest's value as-is, `Any`); `DynamicTeamResolver._rankings_cache` is annotated as the abbreviation-to-rank dict it holds. `mypy.ini` treats numpy and orjson as `Any`, so it parses with `python_version = 3.10` against numpy 2.3+ stubs and gives the same result whether orjson is installed or not.
- CI runs the web UI's DOM test suites (jsdom against the real server-rendered pages and API) in a new **Web UI JS tests** job, with the web interface started in emulator mode; `REQUIRE_DOM=1` makes a suite that can't run fail instead of being skipped. Two suites that had gone stale were fixed: the Tools suite now installs `LEDEscape` the way `base.html` does and supplies sample Starlark apps when the server has none, and the Store suite no longer assumes the registry has 48 plugins or fewer.
- CI installs `web_interface/requirements.txt` too, so flask-limiter, flask-compress and the web floors are tested. `test_api_helper_does_not_hand_set_brotli` now checks what it meant: core doesn't add `br` itself, and `requests` may advertise it when a brotli decoder is installed.
- All Discord links point to the LEDMatrix server's invite.
- `pytz` may be any release before 2027, so current timezone data installs; `requirements-test.txt` caps `psutil` below 7 like the runtime requirements and allows `pytest-cov` up to 7.x (checked against pytest 9 with the CI coverage run).
- The Claude GitHub Actions workflows pin `anthropics/claude-code-action` to a commit SHA like the other actions.
- `mypy.ini` parses again. A multi-line `exclude` and trailing comments on values made mypy refuse the whole file, so none of its settings applied and the pre-commit hook failed with "Missing target". The mypy hook is now manual (`pre-commit run mypy --hook-stage manual`) while the ~500 existing type errors in `src/` are paid down.
- `.gitignore` ignores everything in `config/` except the templates; `ytm_auth.json`, `saved_repositories.json`, `wifi_status.json` and `font_overrides.json` weren't ignored.
- `.sh` and `.service` files are always checked out with LF line endings.
- The Claude code-review check is skipped on pull requests from forks, which get no secrets and always failed it.
- Doc fixes: emulator guide (Python 3.10+, `emulator_config.json` isn't in the repo), README's nonexistent "API Metrics" feature, a stale route count, and missing index entries for the scroll-performance and offscreen-rendering docs and the frame-soak and render-bench scripts.
- `src/common/README.md` lists `frame_timing`, `json_body` and `render_gate`.
- New `scripts/README.md` lists every script.
- New `docs/ARCHITECTURE.md` (processes, shared state, display loop, plugin system, web UI) and `docs/PERMISSIONS.md` (owners, modes, both sudoers files, repair scripts).
- Deprecated plugin APIs are marked in the plugin docs.
- `src/common/README.md` covers every module.
- Stale setup, service and troubleshooting claims are corrected.
- Deleted 13 scripts nothing referenced (#607): `utils/cleanup_venv.sh`,
  `utils/clear_python_cache.sh`, `install/migrate_config.sh`,
  `install/debug_install.sh`, `debug/debug_web_manual.py`,
  `diagnose_web_ui.sh`, `verify_web_ui.sh`, `fix_internet_connectivity.sh`,
  `diagnose_plugin_permissions.sh`, `dev/validate_python.py`,
  `download_nba_logos.py` (with `README_NBA_LOGOS.md`) and
  `setup_plugin_repos.py`, all under `scripts/`; also `docs/archive/` and
  `PLUGIN_IMPLEMENTATION_SUMMARY.md`. `config.template.json` no longer carries
  `plugin_system.auto_discover`, `auto_load_enabled` or `development_mode`,
  which nothing reads (existing configs keep them). About 20 docs had stale
  claims corrected against the code.
- `test/test_js_unit_suites.py` runs every `test/js/unit/*.js` suite under
  pytest; CI used to run one of the eight (#605).
- New test `test/test_common_is_hardware_free.py`: `src/common` must import
  without `rgbmatrix`, and never import `src.base_classes`,
  `src.display_manager` or `src.plugin_system` at module level, so plugins can
  use it on machines with no panel library.

### Removed

- **The skin system.** Skins never rendered with the current scoreboard
  plugins, so they are gone rather than "not supported yet": `src/skin_system/`,
  `skins/`, `scripts/validate_skin.py`, `GET /api/v3/skins`, the store's
  `"type": "skin"` handling and `docs/SKIN_SYSTEM.md` / `docs/CREATING_SKINS.md`.
  A `skin` or `skin_options` key left in a plugin's saved config still loads
  and saves without a validation error; it is ignored, and the next save of
  that plugin's settings removes it (unless the plugin's own schema declares
  the key).
- **`src/base_classes/`** (`SportsCore`, the sport and mode classes,
  `CelebrationMixin`, the rotation strategies, `data_sources`,
  `api_extractors`). No known plugin imports it. A plugin that does must use
  `src.common` or its own copy of the code.
- **Unused `src.common` modules and plugin-system helpers** (#608):
  `src/common/config_helper.py`, `display_helper.py`, `game_helper.py`,
  `utils.py` and `error_handler.py` (its re-exports leave `src.common`'s
  `__all__`), `src/plugin_system/health_monitor.py` (`PluginHealthMonitor`,
  whose loop did nothing; `PluginHealthTracker` is unchanged), and
  `src.plugin_system.get_store_manager` / `__api_version__`. Nothing in core,
  the scripts or the plugin monorepo imported them. `APIHelper`, `TextHelper`,
  `ScrollHelper`, `LogoHelper` and the adaptive-layout exports of `src.common`
  are unchanged. The same change removed unused methods from `ConfigService`,
  `PluginStateManager`, `PluginManager`, `PluginExecutor`, `PluginLoader`,
  `PluginStoreManager`, `VegasModeConfig` and `DisplayController`; none had
  callers in core, the scripts or the monorepo.

## 3.4.0

Plugin-facing changes since 3.3.0 (tag `v3.3.1`) not covered further down:

- `BasePlugin.get_update_interval()` (#555) — return seconds to override the
  manifest's `update_interval` at runtime (e.g. poll fast only while a game is
  live), or `None` to keep it. Clamped to at least 5 seconds; a raising or
  non-numeric return is ignored. Called every scheduling tick, so keep it
  cheap. Older cores never call it. See `docs/PLUGIN_API_REFERENCE.md`.
- `src.common.scroll_config` (#523) — turns a plugin's scroll config into a
  configured `ScrollHelper` in one place, replacing per-plugin resolution that
  disagreed between tickers, and warns when a speed won't advance whole pixels
  per panel refresh. Floor on 3.4.0 to import it.
- **Skins are marked unsupported.** No current scoreboard plugin builds on
  `src.base_classes`, so the skin hook (`SportsCore._render_game`) never runs.
  The web UI no longer shows the Visual Skin dropdown, the store hides and
  refuses `"type": "skin"` entries, and `GET /api/v3/skins` reports
  `"supported": false`. Saved `skin` config values still load and save.
  `src/skin_system/` is unchanged.
- **Web preview size** now comes from `src/display_geometry.py`, the same
  computation `DisplayManager` uses: double-sided setups preview one screen,
  and a missing `chain_length` defaults to 2 everywhere (the Starlark magnify
  default and the sync handshake used 1). The module is core-internal: plugins
  keep reading `display_manager.width`/`height`.
- `src.common.font_layout` (#539, #565) — `load_truetype()` is
  `ImageFont.truetype` with the layout engine pinned, so text lays out the same
  whether or not the host's Pillow was built with libraqm; `crisp_size()` and
  `FONT_PIXEL_GRID` give the size a bundled face renders on whole pixels at
  (`sports_card` still re-exports them); `resolve_asset_path()` resolves
  `assets/fonts/...` against the install root, not the working directory.
  Floor on 3.4.0 to import it. Relatedly, `DisplayManager` now draws text
  1-bit (#521), so golden images recorded against 3.3.x may need regenerating.

### Install and updates

**Weekly automatic updates (#581), off by default.** Switching on
*Automatically check for and install updates once a week* on the General tab
(or `first_time_install.sh --enable-auto-update` / `LEDMATRIX_AUTO_UPDATE=1`)
updates the core and then every installed plugin once a week, preferably 2–5 AM
local time. It follows the branch the checkout tracks — `main` on a standard
install — so a device gets whatever has merged there, not only tagged releases.
See `docs/WEB_INTERFACE_GUIDE.md`.

- The core step is skipped, with the reason shown, when the checkout has local
  edits or commits, a rebase or merge is in progress, the branch has no
  upstream, less than 300 MB is free, or that commit was already rolled back.
- After pulling, `ledmatrix-update-verify.service` restarts the services and
  requires the web interface to answer and the display to stay up. If they
  don't, or the new requirements fail to install, it resets to the previous
  commit, reinstalls its requirements and restarts again. Anything but success
  shows under the toggle and as a banner on Overview.
- Plugins update through the Plugin Store even when the core step is skipped,
  fails or is rolled back. A plugin version whose `ledmatrix_min_version` is
  above the device's core is held back, not installed. When the core did
  update, plugins wait for its health check, and are left alone if that check
  never reports or the rollback fails.
- No SSH is needed: switching the toggle on restarts the display service, which
  installs the health-check units (`src/auto_update_setup.py`, core-internal
  and not a plugin API).

Installer and service fixes:

- rgbmatrix builds on ARMv6 boards (Pi Zero, Pi 1); an existing checkout is
  moved forward to the new pin and no longer left root-owned (#577).
- `first_time_install.sh` grants the web user `safe_pip_install.sh`, as
  `configure_web_sudo.sh` already did, so plugin requirements install where
  the display service can see them (#579).
- The web interface starts when `web_display_autostart` is missing or
  `config.json` is unreadable; only an explicit `false` keeps it down (#556).
- Installers render every systemd unit from its `systemd/` template, so the
  boot-time unit-drift warning can clear, non-root installs included (#547).

### Scrolling

- **Frame pacing (#523).** The loop waits only for the rest of each panel
  refresh instead of a flat 8 ms: 44–46 fps → 100 fps, and slow frames 14% →
  0.02%, on a 2×128×64 chain. Sub-pixel blending is off by default again (it
  shimmered on pixel fonts; Vegas mode still opts in).
- **Whole-pixel steps (#545).** At a speed `scroll_config` can render in whole
  pixels, every frame advances by exactly the same amount, removing about six
  hitches a second. A loop that can't keep up now scrolls slightly slow rather
  than jumping.
- The eight sports scoreboards scroll through `scroll_config` too (#542): the
  default 50 px/s holds each frame for two refreshes instead of alternating
  0 px and 1 px steps.
- **Frame stats ignore the pause between scrolls (#582).** The `Scroll frame
  stats` log line counted the idle wait before each scroll as one frame,
  inflating `max` and the stall rate. `docs/SCROLL_PERFORMANCE.md` now
  describes the line actually logged.

### Plugins

- `FontManager` registers the bundled `tom_thumb` font, so plugins no longer
  need a private loader (#534).
- The test harness's `set_scrolling_state()` accepts `frame_hold`, as
  `DisplayManager`'s does (#534).
- A `display()` with nothing to draw should return `False`, the only value the
  controller skips on; starlark-apps now does, rather than holding a black
  panel (#534).
- Starlark apps may set `render_width`/`render_height` in their `config.json`
  to render at their own canvas size instead of Pixlet's 64×32 (#552).
- `scripts/render_plugin.py --display-mode <mode>` renders one mode of a
  multi-mode plugin; scoreboards previously rendered blank (#522).
- Scoreboards resolve their own directory under the real plugin loader
  (declare `_PLUGIN_DIR`), so 4x6 text snaps to its 7px grid instead of
  rendering a pixel narrow, and an unreadable schema is logged (#519, #520).
  `DisplayManager` loads 4x6 on that grid too (#565).
- The 5x7 BDF face reports a real height, so rows stacked by
  `get_font_height()` no longer overlap (#539).
- `LogoHelper` remembers a missing logo instead of warning every rotation
  (#548), and the decoded sports logo cache is bounded (#559).

### Web interface

- Installed Plugins has search, All / Enabled / Disabled / Updates filters and
  sort (#540).
- Hardened and polished per the September 2026 audit (#568): utility classes
  such as `.hidden` actually exist, focus rings, labels and modal focus
  trapping, dark theme throughout, no overflow at phone width, and background
  streams pause when hidden, with first-load JS/CSS down from 1358 KB to 291 KB.
- WiFi Connect works from the LEDMatrix-Setup hotspot: the page is answered
  before the hotspot drops, and reopening it shows why an attempt failed (#571).
- Pixlet install, the Starlark app store and app toggles work again (#535,
  #537); the store uses the configured GitHub token and reports a rate limit
  instead of drawing a blank grid (#541).
- Plugin config: geochron and news saves no longer always fail (#575), the page
  survives stored values the schema outgrew (#578), the form uses the full page
  height (#573), and file-manager widgets show the script's error (#574).
- The live status stream reports real disk usage and available memory (#558);
  a system action refused for want of passwordless sudo says so and names
  `configure_web_sudo.sh` (#560).

### Tools and security

- **CodeQL triage (#561):** 129 of 134 alerts fixed. Three were exploitable
  path-handling flaws in the web interface and are closed; web UI escapers now
  escape quotes, and URL fields refuse script schemes. Path checks share
  `src/common/path_safety.py` (core-internal).
- **Home Assistant MQTT bridge** (`integrations/mqtt_bridge`, #538): mode
  select, stop, power and brightness over MQTT Discovery.
- **Tools tab** manages the MQTT bridge and the Pixlet editor (#554); the
  editor stays on loopback when `PIXLET_EDITOR_HOST` says so.

### Fixes

- Updating a plugin whose directory is named for its manifest id (leaderboard,
  music, stocks, weather) silently did nothing (#536).
- Plugin reconciliation no longer reports working plugins as stale or replaces
  their config with a stub, and the Overview banner advises each case correctly
  (#557).
- Two config saves in the same second no longer share one backup, so rollback
  restores the version asked for (#564).
- On-demand: a second request is honoured without a restart (#534), a pinned
  request stays on its mode, and restarting mid-session loads every plugin
  again (#538).
- `/health` and `/display/current` report real state, and the preview no longer
  freezes on a leftover snapshot temp file (#534).

### Per-element display customization

**Per-element display customization, and the last mile of it into the web UI.**
A user can set the font, size, colour, position, visibility and alignment of
individual display elements per plugin -- and, where a plugin has display
modes, separately per mode.

New public API a plugin may import via `src.*` (floor on the release that
ships this):

- `src.element_style.layout_offset(config, element, axis, default, mode)` and
  `element_color(config, element, default, mode)` — the stateless reads the
  scoreboard helpers share. There were three copies of the offset read and two
  of the colour read; these are the one implementation, and they carry the
  element-name aliasing and the per-mode lookup.
- `src.element_style.alias_keys(element)` — the names one element may be stored
  under. The style block names elements `score_text` while the layout block
  says `score`, and `records`/`record` and `status_text`/`status` split seven
  to two across the published schemas. A lookup tries the exact name first, so
  this is inert for a config that already matches.
- `src.element_style.element_visible(config, element, default, mode)`,
  `element_align(...)` and `element_scale(...)` — the stateless reads for the
  three knobs the resolver already understood but no draw path consumed, so an
  element could be marked hidden in the web UI and still render.
- `SportsCoreSharedMixin._draw_text_with_outline(..., element="score_text")` —
  naming the element resolves its colour by name and honours its visibility
  toggle. Without a name the colour is inferred from font-object identity,
  which cannot separate two elements sharing a face; that is the case every
  bitmap font is in, because a `freetype.Face` cannot be re-instantiated, and
  it is how a BDF-rendered element silently lost a configured colour. Shared
  faces now resolve when exactly one sharer has a colour set.
- `LogoHelper.load_logo(..., scale=)` — applies a user's image scale, and keys
  the cache on the scaled box so two elements scaled differently cannot be
  served each other's image.
- `src.element_style.native_bdf_size(font)` — the one pixel size a bitmap font
  can render at, or None for a scalable one. The web UI needs this to know
  whether a size control can take effect at all.
- `ElementStyleResolver(config, defaults, mode=...)` plus `visible`, `align`
  and `scale` on `ElementStyle`. The mode binds to the resolver rather than
  being passed per call, so a plugin with one instance per mode makes every
  existing lookup mode-aware by setting one class attribute.
- `BasePlugin.styles` / `styles_for(mode)` / `STYLE_MODE` — the accessor every
  plugin inherits, so adopting this is no longer a guarded import plus schema
  discovery plus resolver invalidation in each plugin.
- `SportsCoreSharedMixin._get_layout_offset` — promoted from the plugins'
  bundled copies. Each still carries its own, which wins by MRO, so adopting
  it is a deletion.

Schema and web UI:

- A `customization` block is now rendered by a composite style editor: one row
  per element rather than nested accordions, with a tab per declared mode.
  Plugins that hand-wrote their style blocks get it without a plugin release;
  `x-style-elements` and `x-style-modes` declare it compactly.
- Font fields become a real picker rather than a hardcoded `enum`, so a font
  the user uploads is selectable. Bitmap fonts taller than the element's
  declared size ceiling are filtered out, because a bitmap font ignores
  `font_size` and renders at its own size.
- `/static/plugin-widgets/<plugin>/<widget>.js` serves a plugin's own web-UI
  widgets. The client half and the docs already existed; nothing served them.

Fixed:

- A bitmap font asked for a size it has no strike for fell back to
  *PressStart2P* — a different typeface — rather than to its own native size.
  32 of the 35 shipped fonts are bitmap, so this was reachable for most font
  choices.
- The plugin config form read `config_schema.json` directly while the save
  route read it through `SchemaManager`. Only the latter expands a compact
  `x-style-elements` declaration, so a plugin using that form had a
  customization section that rendered as empty space.
- `unshare_element_fonts` rebuilt faces through bare `ImageFont.truetype`,
  bypassing the layout engine `src/common/font_layout.py` pins. These were the
  only two call sites in `src/` doing so.
- The form parser compared a schema type to a bare string, so a nullable field
  (`["array", "null"]`) never had its indexed colour inputs recombined, and a
  blank one became `[]` rather than null.

Removed:

- The Fonts tab's "Element Font Overrides" panel and its three endpoints. They
  reported success and saved nothing, and the element keys the panel offered
  (`nfl.live.score`, `clock.time`) are read by no plugin, so wiring them to the
  real `FontManager` methods would still have changed nothing on the panel.
  Per-element font choice now lives in each plugin's own config editor.
- "Detected Manager Fonts", which listed every installed font with a hardcoded
  usage count.
- Two dead client-side config-form renderers in `app-shell.js` (~580 lines) and
  the legacy `plugins/config_manager.js`, superseded by server-side rendering.

## 3.3.0

Historical note: tags `v3.3.0` and `v3.3.1` both report `__version__` "3.3.0" and both ship `src/common/sports_shared.py`, so a "3.3.0" floor always means a core with `sports_shared`.

**The release the sports scoreboards floor on to delete their bundled copies.**
3.2.0 shipped the unified sports library and made `ledmatrix_min_version`
enforceable; this ships the last three shared modules and completes the store
gate, so a scoreboard can now floor here and carry no fallback at all.

New modules a plugin may import via `src.*` and floor on 3.3.0 for:

- `src/common/sports_card.py` — settings, colour, font and date helpers for a
  scoreboard's `game_renderer.py`. Free functions taking `config`/`fonts`
  explicitly, so nothing about the caller's class is assumed.
- `src/common/sports_game_renderer.py` — `SportsGameRendererMixin`: scroll/Vegas
  card geometry (centre gap, logo slot and cache key, layout offsets, the
  upcoming-card date and time layout). No `__init__` and no state, so adoption
  is one line on the class statement.
- `src/common/sports_shared.py` — `SportsCoreSharedMixin`,
  `SportsLiveSharedMixin`, `SportsRecentSharedMixin`: the `sports.py` bodies
  byte-identical in all eight lineage-sharing scoreboards.

All three sit under `src/common/` rather than `src/base_classes/sports/`,
deliberately: importing that package pulls `core.py` → `DisplayManager` →
`rgbmatrix`, and these are pure logic. Plugins importing them must not acquire a
hardware dependency.

Three notes for anyone adopting `sports_shared`:

- `SportsRecentSharedMixin` defines `__init__`. Its bare `super()` binds to the
  mixin, so it reaches the host only when the mixin is listed **first** in the
  bases. Reversing that order silently skips the host constructor.
- Methods that resolve the plugin's `config_schema.json` use `_plugin_dir()`,
  which walks the MRO rather than reading `__file__` — `__file__` is now
  `src/common/`. It walks because `SportsCore` is an ABC: a subclass built with
  `type(name, bases, ns)` reports `__module__` as `"abc"`.
- `_get_timezone`, `_extract_game_details` and `_fetch_data` are byte-identical
  across the eight but stay in the plugins. The first binds a per-plugin
  timezone module whose contents differ; the other two are the abstract stubs
  that define the sport.

**The store's compatibility gate is now on every registry-managed route.** 3.2.0
gated `install_plugin`. This release gates the git-pull update path and
`install_from_url`, so a plugin whose floor the core cannot meet is refused
after download with no partial directory left behind.

### Fixed

- **ESPN 403s.** `site.api` began rejecting the User-Agent strings this repo
  sent on 2026-08-04; every shared-data-source scoreboard returned
  `403 Forbidden`. Requests now send an identifying token with a project URL —
  browser-style strings and bare custom tokens are both refused.
- **Low-memory boards becoming unreachable under load** while still answering
  pings and serving the web UI. Fetched payloads are released after delivery
  rather than pinned on the completed request for up to an hour, malloc arenas
  are capped, and log volume and SD writes are reduced. Available memory is now
  reported in Tools diagnostics.
- **A failed logo download pinning a team to a grey box**: the placeholder was
  written under the real logo's filename, so later attempts found a file and
  reported success without retrying.
- **A plugin enabled but never loaded is retried** rather than staying absent
  with `error = null`.
- `ttl` now controls cache expiry; abandoned cache writes no longer leave temp
  files; one cache-cleanup thread per directory rather than per manager.
- Odds are fetched for the games displayed, not the whole schedule window, and a
  stalled ESPN no longer stalls the whole plugin update.
- Array-item secrets are no longer wiped or logged.
- A restored backup matches the device it was taken from.
- The web UI reports the real error instead of "unknown", rejects non-finite
  JSON numbers, and stops checkbox groups posting back hidden options.

### Added

- `display.hardware.orientation` for panels mounted upside down.
- Vegas keeps live content in the ticker rather than being preempted by it.
- Schemas can label enum dropdown options.

## 3.2.0

**The first release shipping the unified sports library.** This is the version
a sports plugin floors `ledmatrix_min_version` at before deleting its bundled
copy of `sports.py`, `scroll_display.py`, `data_sources.py` or
`base_odds_manager.py` — the sunset rule in
`docs/plugin-development/08-shared-sports-code.md` keys on exactly this number.

Adoption is deliberately staged: the modules below ship here, plugins adopt them
behind guarded imports, and only then do the bundled copies go away. Nothing in
this release changes what an existing plugin loads.

**This is also the first release that *enforces* `ledmatrix_min_version`.**
Before it, the floor was advisory — the loader logged a warning and continued,
and the plugin store never compared the core version at all, so an update could
deliver a plugin that could not run. From 3.2.0 the store refuses such an
install. That matters for the sunset rule: a plugin may only delete its bundled
fallback once the cores in the field actually enforce the floor, which means
waiting for 3.2.0 to be widely installed rather than merely released. See
`docs/SPORTS_UNIFICATION.md`, phase B6.

One deliberate exception: a core reporting a version below `2.0.0` is treated as
*unknown* rather than old and is never blocked. The v3.1.0 release ships
`__version__ = "1.0.0"` (the tag was cut before the string was bumped), and
nearly every published manifest floors at `2.0.0` — so blocking on that number
would lock those users out of the plugin store entirely.

### Added
- `src/element_style.py` — per-element style resolver backing the
  `x-style-elements` config-schema extension. Already consumed (behind guarded
  imports with classic fallbacks) by the `of-the-day`, `ledmatrix-music`, and
  `football-scoreboard` plugins.
- Core unit-test CI job enrolling the previously unenrolled suites (skin
  system, data sources, API extractors, scroll helper, adaptive layout, loader
  compatibility warning) plus new characterization tests for
  `src/base_classes/sports.py` ahead of the shared sports-code unification.
- `src/base_classes/sports/` — `sports.py` is now a package (`core.py` +
  `modes.py`). The import path is unchanged: `from src.base_classes.sports
  import SportsCore` still works.
- Nine methods promoted onto the sports base classes from the plugins'
  bundled copies, plus the override points `_favorite_key`,
  `_config_schema_path` and `_font_root` and the class attributes
  `FINAL_PERIOD` / `CLOCK_COUNTS_DOWN`. See `docs/SPORTS_UNIFICATION.md`.
  A plugin may start calling these once its manifest floors
  `ledmatrix_min_version` at the release that ships them.

- `src/base_classes/sports/capabilities/` — opt-in capabilities for the sports
  scoreboards, composed by inheritance rather than gated by config branches
  inside the base classes:
  - `CelebrationMixin` — the score/win takeover, merging the goal and score
    dialects behind the `score_phrase()` / `win_phrase()` hooks, the
    `COALESCE_SCORING_SEQUENCE` class attribute and the `_favorite_key` seam.
    Reads both the `celebrate_opponent_goals` and `celebrate_opponent_scores`
    config spellings. Sports that do not mix it in have none of this code in
    their MRO.
  - `RotationStrategy` + a name registry (`swrr`, `weighted`, `simple`,
    plus `register_rotation_strategy` for plugin-supplied orderings). Each
    built-in is verified against a verbatim transcription of the plugin
    implementation it replaces. An unknown name degrades to `simple`.

- `src/common/sports_scroll.py` — `SportsScrollDisplay` and
  `SportsScrollDisplayManager`, the shared scroll **orchestration** layer for
  the sports scoreboards, plus native support for
  `global_config['target_fps']` (the bundled plugin copies hardcode ~100 FPS
  via `scroll_delay` and never consult the global target). Content building
  (`prepare_scroll_content`, `_load_separator_icons`) is per-sport and stays an
  override point — see `docs/SPORTS_UNIFICATION.md` for where the line falls
  and why.

- `src/plugin_system/compatibility.py` — the single place that answers "can this
  plugin run on this core?", shared by the loader (advisory, at load time) and
  the store (blocking, at install/update time) so the two cannot drift. Reads
  every spelling published manifests use, including the deprecated
  `versions[].ledmatrix_min`. It does **not** yet evaluate `compatible_versions`,
  which is the schema-required field and can express upper bounds; closing that
  is tracked in `docs/SPORTS_UNIFICATION.md` before B6.
- `scripts/check_release_version.py` and a `Release version check` workflow —
  assert that a tag, the newest CHANGELOG heading and `src.__version__` agree,
  on pushed `v*` tags and published releases. Runnable via `workflow_dispatch`
  to check a tag *before* creating it. Added because `v3.1.0` was tagged six
  weeks before `src/__init__.py` was bumped to match, which is why devices
  installed from that release report `1.0.0`.

### Changed
- `src/__init__.py` bumped to **3.2.0** — the number the sunset rule keys on.
- **The plugin store refuses an incompatible install.**
  `StoreManager.install_plugin` now checks the downloaded manifest's declared
  floor against `src.__version__` and refuses when the plugin needs a newer
  core. The check sits in `install_plugin` because `_reinstall_with_rollback`
  calls it, so a refused *update* restores the version the user already had.
  Refusal requires evidence: an undeclared floor, an unparseable version on
  either side, or an untrustworthy core version all allow the install.
- **A failed install no longer destroys the plugin it replaced.**
  `install_plugin` previously deleted the existing plugin directory before
  downloading, so any later failure — a dropped connection, a malformed
  manifest, or the new compatibility refusal — left the user with nothing. The
  existing copy is now set aside and restored if the install fails, matching
  the protection `_reinstall_with_rollback` already gave the update path.
- `web_interface.__version__` re-exports `src.__version__` instead of carrying
  its own hardcoded `"3.0.0"`, which had drifted two majors from the core.
- **Live games are no longer dropped when the feed omits a game clock.**
  `SportsLive._is_game_really_over` previously (in the baseball and UFC
  plugin lineages) coerced a missing or non-string clock to the literal
  `"0:00"` and then treated the game as finished once `period >= 4`. Baseball
  has no game clock and `period` is the inning, so live MLB games disappeared
  from the scoreboard from the 5th inning onward; UFC was affected the same
  way. The clock check is now skipped when the clock is unusable, and the
  period threshold is the per-sport `FINAL_PERIOD` (hockey ends in P3).
  Sports whose clocks count up — soccer, AFL, NRL — set
  `CLOCK_COUNTS_DOWN = False` and never run the check at all, since `0:00`
  there means kickoff rather than expiry.

### Fixed
- **Plugin updates could hang the web request thread.** The per-plugin reinstall
  locks were non-reentrant, and `_reinstall_with_rollback` holds one across its
  call to `install_plugin` — which now takes the same lock to protect the
  set-aside/restore above. That nesting deadlocked
  `update_plugin → _reinstall_with_rollback → install_plugin`, the standard
  path for every monorepo plugin update. The locks are now `RLock`s.
- `FontManager` resolves `assets/fonts` against the core install root instead
  of the process working directory, so font loading works when the process
  starts elsewhere (e.g. the plugin safety harness on CI).
- Hockey events whose competitors carry no `statistics` array are no longer
  discarded. The extractor read `competitor["statistics"]` unguarded, so a
  `KeyError` inside the generator dropped the entire event despite valid
  scores and status; shot counts now fall back to `0`.
- Live baseball events that populate status only at the competition level are
  no longer discarded. The extractor read the event top-level
  `game_event["status"]` for the inning; real ESPN events duplicate it, but
  MiLB events synthesized from the MLB Stats API do not, so the lookup raised
  a bare `KeyError`. It now reads the already-validated competition-level
  status.
- `SportsLive._is_game_really_over` no longer crashes the live-update pass when
  a feed sends an explicit null `period`. `None >= FINAL_PERIOD` raised
  `TypeError`, and the only caller (`_detect_stale_games`) has no `try/except`
  — the same failure shape as the already-fixed null `period_text`.
- An expired clock spelled `"00:00"` now ends the game. The check compared the
  colon-stripped clock against a hand-listed set of literals, which `"0000"` is
  not a member of, so a finished game with a two-digit-minute clock stayed on
  the scoreboard indefinitely. The comparison is now numeric.
- `SportsCore._load_fonts` resolves `assets/fonts` through the `_font_root()`
  seam instead of the process working directory. Started outside the install
  root, every scoreboard font silently degraded to PIL's default bitmap face.
- `SportsCore._should_log` no longer raises `AttributeError` on the first
  warning of a run; `_last_warning_time` is initialized in `__init__` rather
  than lazily by an unrelated method.
- `SportsCore._resolve_project_path` resolved relative logo directories
  against `<root>/src` instead of the repo root after `sports.py` became a
  package — the class bodies moved byte-identically but `__file__` gained a
  directory. Both it and `_font_root` now derive from one `_INSTALL_ROOT`
  constant.

## 3.1.0

Baseline for this changelog. Highlights already shipped at this version:
skin system for sports scoreboards (#419), Vegas continuous-scroll overhaul
(#423), plugin update surfacing (#421).
