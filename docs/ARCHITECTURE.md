# Architecture

A map of the codebase for a new contributor: which process does what, how
they talk to each other, and where to start reading for common changes.

## Processes

| systemd unit | Runs as | Runs | Installed by |
|---|---|---|---|
| `ledmatrix.service` | root | [`run.py`](../run.py) → `DisplayController` | [`install_service.sh`](../scripts/install/install_service.sh) |
| `ledmatrix-web.service` | the installing user | [`start_web_conditionally.py`](../scripts/utils/start_web_conditionally.py) → [`web_interface/start.py`](../web_interface/start.py) (Flask, port 5000) | `install_service.sh`, [`install_web_service.sh`](../scripts/install/install_web_service.sh) |
| `ledmatrix-update-verify.path` / `.service` | the web user | Health check after an automatic update | the same installers, or [`src/auto_update_setup.py`](../src/auto_update_setup.py) at runtime |
| `ledmatrix-wifi-monitor.service` | root | [`wifi_monitor_daemon.py`](../scripts/utils/wifi_monitor_daemon.py) | [`install_wifi_monitor.sh`](../scripts/install/install_wifi_monitor.sh) |
| `ledmatrix-mqtt-bridge.service` | root | [MQTT bridge](../integrations/mqtt_bridge/README.md) (optional) | [`install_mqtt_bridge.sh`](../scripts/install/install_mqtt_bridge.sh) |
| `ledmatrix-dns-fix.service` | root | DNS workaround (optional) | [`install_dns_fix.sh`](../scripts/install/install_dns_fix.sh) |

Unit templates are in [`systemd/`](../systemd/README.md). The display runs as
root because the LED matrix library needs direct GPIO access. The web
interface runs unprivileged and uses a fixed list of `sudo` rules for the
few privileged things it does; see [PERMISSIONS.md](PERMISSIONS.md).

`start_web_conditionally.py` exits without starting Flask when
`web_display_autostart` is explicitly false in `config.json`.

## How the two main processes share state

The display and the web interface are separate processes that never call
each other. They share three things:

1. **`config/config.json` and `config/config_secrets.json`.** The web
   interface writes them through `ConfigManager`
   ([`src/config_manager.py`](../src/config_manager.py)); the display
   notices through `ConfigService` (below).
2. **The disk cache**, `/var/cache/ledmatrix` (owned `root:ledmatrix`,
   setgid, files `0660`), read and written through `CacheManager`
   ([`src/cache_manager.py`](../src/cache_manager.py),
   [`src/cache/disk_cache.py`](../src/cache/disk_cache.py)). Readers in the
   other process pass `memory_ttl=0` so they do not serve a stale in-memory
   copy.
3. **A few files in `/tmp`.**

| State | Where | Written by | Read by |
|---|---|---|---|
| On-demand command | control socket `/run/ledmatrix/control.sock` ([IPC_CONTROL_SOCKET.md](IPC_CONTROL_SOCKET.md)) | web: `start_on_demand_display()` / `stop_on_demand_display()` in [`api_v3/display.py`](../web_interface/blueprints/api_v3/display.py), via [`src/ipc/client.py`](../src/ipc/client.py) | display: [`src/ipc/server.py`](../src/ipc/server.py) acks; the render thread applies it in `_poll_on_demand_requests()` |
| On-demand request (fallback) | cache `display_on_demand_request` | web, only when the socket could not carry the request (`should_fall_back`); four plugins write it directly | display: `_poll_on_demand_requests()`, a `stat()` every 1 s while the socket is up (0.25 s without), read only when the file changed |
| On-demand state | cache `display_on_demand_state` | display: `_publish_on_demand_state()` | web: `/api/v3/display/on-demand/status` |
| Current screen | cache `display_current_state` | display | web: `/api/v3/display/current-status` |
| Plugin errors | cache `plugin_error_snapshot` | display: `ErrorSnapshotPublisher` ([`src/error_aggregator.py`](../src/error_aggregator.py)) | web: `read_error_report()` for `/api/v3/errors/*` |
| Error clear | control socket `errors.clear`; cache `plugin_error_clear_request` as the fallback | web: `POST /api/v3/errors/clear` | display: applied before the socket answers; the mailbox on the error publisher's 5 s tick, read only when the file changed |
| Font usage | cache `font_usage_snapshot` | display: `FontUsagePublisher` ([`src/font_usage.py`](../src/font_usage.py)) | web: Fonts tab |
| Fetch statistics (requests per plugin and host) | cache `fetch_stats_snapshot` | display: `FetchStatsPublisher` ([`src/common/fetch_service.py`](../src/common/fetch_service.py)), at most once a minute on change | web: `read_fetch_stats()` for `/api/v3/plugins/fetch-stats` |
| Plugin health | cache `plugin_health:<id>` | display (web writes on reset) | web: `/api/v3/plugins/health` |
| Plugin runtime (loaded, state, last error, version) | cache `plugin_runtime_snapshot` | display: `PluginRuntimePublisher` ([`src/plugin_system/plugin_runtime.py`](../src/plugin_system/plugin_runtime.py)) | web: `read_plugin_runtime()` for `/api/v3/plugins/installed`, `/plugins/state`, reconciliation |
| Preview frame | `/tmp/led_matrix_preview.png` | display: `DisplayManager`, gated by [`snapshot_policy`](../src/common/snapshot_policy.py): a changed frame at most once a second with a viewer, every 30 s without | web: display SSE stream (checks the mtime every 0.25 s), `/api/v3/health` (file age) |
| Preview viewer marker | `/tmp/led_matrix_preview_viewer` | web, about once a second while a preview is open | display: writes viewer-rate snapshots only while it is fresh (5 s) |
| Hardware init status | `/tmp/led_matrix_hw_status.json` | display | web: `/api/v3/hardware/status` |
| Render-loop heartbeat | `/run/ledmatrix/display-heartbeat.json` (tmpfs) | display: the render thread, via [`display_watchdog`](../src/display_watchdog.py) | web: `/api/v3/health` (`checks.display_loop`); the update health check |

The on-demand start route starts `ledmatrix.service` when it is not running
(`start_service`, on by default) but never restarts a running one. The routes
send the command over the display's control socket and get an ack; only when
the socket could not carry it (a stopped display, one older than the socket
or the command) do they write the mailbox instead. A display that had the
request and refused it is answered with the error, not posted a mailbox
copy. The display looks at the mailbox every
`MAILBOX_POLL_INTERVAL_WITH_SOCKET` (1 s) while it serves the socket, and
every `ON_DEMAND_POLL_INTERVAL` (0.25 s) without one, from its dwell sleep,
its render loops and Vegas's interrupt check as well as the main loop; a
look is one `stat()` unless the file changed. Both ways end in the same
handler, `_handle_on_demand_request()`.
The socket's handlers only queue; see [IPC_CONTROL_SOCKET.md](IPC_CONTROL_SOCKET.md)
for the protocol, the permission model and the plan to retire the mailboxes.

### Web and display processes: who runs plugins

Only the display process imports plugin code, instantiates plugins and calls
their lifecycle hooks (`update`, `display`, `on_config_change`, `on_enable`,
`on_disable`). The web process is metadata-only: it reads plugins as files
-- manifests and directories through `PluginCatalog`
([`src/plugin_system/plugin_catalog.py`](../src/plugin_system/plugin_catalog.py)),
config schemas through `SchemaManager`, and each plugin's section of
`config.json` through `ConfigManager`. The catalog keeps the
read-only method names of `PluginManager` and has nothing that can run a
plugin (no `load_plugin`, `get_plugin` or `plugins`).

How a web-side change reaches the running plugins:

| Change | How the display picks it up |
|---|---|
| Plugin settings saved, config reset | `ConfigService` sees the new `config.json` and calls the plugin's `on_config_change` with the prepared section |
| Plugin enabled or disabled | `ConfigService` → `_controller_config_change` flags a reconcile; `_reconcile_enabled_plugins` loads it (fresh from disk) or unloads it on the render thread |
| Plugin uninstalled (config removed) | the removed section flips its `enabled` flag, and the reconcile unloads it |
| Plugin installed, not enabled | nothing to do until it is enabled, which loads it |
| Plugin updated while enabled | the update route asks the display over the control socket (`plugin.reload`) to reload it on the render thread, and answers `restart_required: false` once the new code runs. Without the socket, as the next row |
| Plugin installed while already enabled, updated while enabled and not reloaded, or uninstalled with its config kept | **not picked up**: the display keeps running what it loaded. The route answers `restart_required: true` and the UI shows its restart banner |

`display_restart_required()` in `plugin_catalog.py` holds that last rule;
routes return it as `restart_required` (with the banner's wording in
`restart_message`), and `window.noteRestartRequired()` in
`static/v3/app.js` raises the banner for any response that carries it,
`POST /api/v3/config/main` included.

Runtime state shown in the UI comes from what the display publishes to the
shared cache: health and metrics (`/api/v3/plugins/health`,
`/plugins/metrics`), errors (`/api/v3/errors/*`), the current mode, and the
plugin runtime snapshot described below. `enabled` is read from
`config.json` by the display's rule (a missing flag is disabled).

Plugin code still runs in the web process in one place,
`_import_plugin_code_in_web_process()` in
[`api_v3/__init__.py`](../web_interface/blueprints/api_v3/__init__.py): the
Starlark routes import the starlark-apps plugin's `tronbyte_repository` and
`pixlet_renderer` helper modules (never the plugin class), and a web-UI
action with `oauth_flow` imports its script for `get_auth_url()`. Every
other web-UI action runs its script as a subprocess. A later, explicit
**plugin web-entry contract** -- a declared entry point for plugin web code
-- replaces that function.

The **control socket** from the web process to the display
([IPC_CONTROL_SOCKET.md](IPC_CONTROL_SOCKET.md)) carries on-demand
commands and reloads an updated plugin; its next stages stream the
display's state and retire the cache-key mailboxes. The plugin web-entry
contract above is still to come.

### Plugin state: desired, observed, and who owns it

There is one plugin state machine, and the display owns it:
`PluginStateManager` in
[`plugin_state.py`](../src/plugin_system/plugin_state.py) (unloaded →
loaded → enabled ⇄ running, error, disabled), held by the display's
`PluginManager`. It also records, per loaded plugin, the manifest version it
loaded and when. Nothing else keeps plugin state:

| Question | Answered by |
|---|---|
| Is it installed, at which version? | the plugins directory (`manifest.json`) |
| Should it run? | `config.json` (`<id>.enabled`, missing = disabled) |
| Has the user uninstalled it for good? | the store's uninstalled-plugins record |
| Is the display running it, at which version, and why not? | the display's runtime snapshot |

**The runtime snapshot.** `PluginRuntimePublisher`
([`plugin_runtime.py`](../src/plugin_system/plugin_runtime.py)), started by
`DisplayController` right after it creates the `PluginManager`, writes the
cache key `plugin_runtime_snapshot`: per plugin `loaded`, `state`, `error`
(type, a redacted message of at most 200 characters, when, recoverable),
`version`, `loaded_at` and `modes` (the display modes `DisplayController`
registered -- `plugin.modes` when the plugin computes them, else the
manifest's), plus `published_at`, `stale_after` and `running`.
The cache is on disk, usually the SD card, so it writes when something a
reader sees changes -- throttled to once per 10 s -- and otherwise once a
minute as a heartbeat. RUNNING, which every `update()` passes through, is
published as ENABLED, so plugin updates alone never cause a write.
`cleanup()` publishes `running: false`.

**Reading it.** `read_plugin_runtime()` judges the snapshot before anyone
uses it: `live` (fresh, from a running display), `stale` (older than
`stale_after`, 3 minutes: a hung or crashed display), `stopped` or
`unknown` (none, unreadable, or another schema). Only a live view reports
per-plugin facts; every other status answers `null` for them, so stale
truth cannot leak into a response. `/api/v3/plugins/installed` returns
`loaded`, `state`, `error_info`, `loaded_version` and `loaded_at` per
plugin and `data.runtime` (`status`, `published_at`, `age_seconds`);
`/api/v3/plugins/state` returns the same beside the desired state.
`PluginCatalog.get_plugin_display_modes` and `find_plugin_for_mode` prefer a
live view's `modes` to the manifest's `display_modes`, so `/display/modes`
and on-demand see modes a plugin generates from its config (#668).

**Reconciliation**
([`state_reconciliation.py`](../src/plugin_system/state_reconciliation.py))
compares desired state (config + disk) with observed state (the snapshot).
It fixes desired-state gaps -- a plugin on disk with no config section gets
`{"enabled": false}`, a configured plugin missing from disk is reinstalled
unless the user uninstalled it -- and only reports observed-state gaps
(enabled but not loaded, loaded at an older version): the display loads and
unloads by config on its own, and a version gap needs a restart.

**`data/plugin_state.json` is retired.** The web process used to keep a
second `PluginStateManager` (`state_manager.py`) persisted to that file:
per plugin an enabled flag copied from config, a version copied from the
manifest (when set at all), a status derived from those, and install/update
timestamps. Reconciliation mostly synced it back to config and backups
merged it into their plugin list. Every field is derivable (the timestamps
from the operation history), so nothing is migrated: no code reads or
writes the file, and a copy left on a device is inert and safe to delete.
The two classes shared a name but not a concern -- a persisted install
record versus the live lifecycle -- so they were not merged; the persisted
one had nothing left to hold and was removed.

## Display loop

[`src/display_controller.py`](../src/display_controller.py), class
`DisplayController`. `__init__` loads config, starts the cache and the
error-snapshot publisher, runs the startup validator, creates the
`DisplayManager` ([`src/display_manager.py`](../src/display_manager.py)),
`FontManager` and `PluginManager`, loads the enabled plugins in parallel,
runs an initial `update()` pass within a 20-second budget
(`_INITIAL_UPDATE_BUDGET_SECONDS`; a plugin that misses it is deferred to
the scheduler), and sets up Vegas mode.

`run()` is the main loop. Each pass, in order: apply a pending plugin
enable/disable, poll on-demand requests, run scheduled plugin updates, check
the on/off schedule and brightness, then show one screen. Priority is
on-demand, then WiFi status messages, then live priority, then Vegas mode,
then normal rotation. [RUN_LOOP_REDESIGN.md](RUN_LOOP_REDESIGN.md) is the
plan for restructuring this loop and lists its golden trace tests.

- **Rotation.** `available_modes` is the ordered list of display modes;
  `current_mode_index` advances after each screen.
  `_apply_plugin_rotation_order()` applies `display.plugin_rotation_order`.
- **Durations.** `_get_display_duration()`: `display.display_durations[mode]`,
  else the plugin's `get_display_duration()`, else 30 s. Plugins that
  support dynamic duration run until `is_cycle_complete()`, capped by
  `display.dynamic_duration.max_duration_seconds` (default 180 s).
- **On-demand.** A request from the web interface pins one plugin (or mode)
  for a duration. `_activate_on_demand()` / `_clear_on_demand()`; the
  session is saved under `display_on_demand_config` so it survives a
  restart. It also keeps the display on during scheduled off hours. A
  request for a plugin that is disabled in config loads it live
  (`_load_plugin_for_on_demand()`, `load_plugin(force_enabled=True)`)
  without writing `config.json`; the main loop unloads it once on-demand
  moves off it (`_release_on_demand_plugins()`).
- **Live priority.** `_check_live_priority()` looks for a plugin whose
  `has_live_priority()` and `has_live_content()` are both true and switches
  to it, rotating between several live games.
- **Schedule and dim schedule.** `_check_schedule()` reads `schedule`;
  `_check_dim_schedule()` reads `dim_schedule` and
  `display.hardware.brightness`. Both are re-evaluated once a minute, and
  both windows are half-open: on (or dimmed) from the start time, off at
  the end time. When an on-demand session ends, the on/off schedule is
  re-checked at once rather than at the next minute.
- **Long screens.** While a screen is showing (a dwell, a scroll, a Vegas
  iteration), `_service_pending_changes()` repeats the on-demand, schedule
  and brightness checks every 0.25 s, so a change does not wait for the
  screen to end.
- **Config hot reload.** `ConfigService`
  ([`src/config_service.py`](../src/config_service.py)) polls the config and
  secrets files' mtimes every 2 s and notifies subscribers when the content
  changes. The controller refreshes its cached settings; enabling or
  disabling a plugin queues `_reconcile_enabled_plugins()`, which loads or
  unloads it on the display thread; each plugin gets `on_config_change()`
  for its own section, under its plugin lock
  (`PluginManager.apply_config_change()`). Set `LEDMATRIX_HOT_RELOAD=false` to turn this off.
  Matrix hardware settings are only read at start-up.
- **Vegas mode.** [`src/vegas_mode/`](../src/vegas_mode/): the display loop
  calls `VegasModeCoordinator.run_iteration()`
  ([`coordinator.py`](../src/vegas_mode/coordinator.py)) when
  `display.vegas_scroll.enabled` is set. `PluginAdapter` gets each plugin's
  content (`get_vegas_content()`, else its `scroll_helper` image, else a
  capture of `display()`), `StreamManager` orders it and `RenderPipeline`
  scrolls it. See [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md).
- **Multi-display sync.** `DisplaySyncManager`
  ([`src/common/sync_manager.py`](../src/common/sync_manager.py)), enabled by
  `sync.role`: a leader sends a follower its share of each frame over UDP
  (port 5765).

### Liveness

A render thread stuck inside a plugin leaves the service "active" and the
panel frozen, so liveness is reported by the render thread itself
([`src/display_watchdog.py`](../src/display_watchdog.py), standard library
only). `beat()` from any other thread is ignored: the update worker, Vegas's
tick thread and the prefetcher keep running while the render thread is stuck,
and must not vouch for it.

- **Check-in points.** The top of `run()`'s loop (`loop_pass()`), every
  dwell second (`_sleep_with_plugin_updates`), every frame of the per-screen
  loops (`_display_once`), every frame of Vegas's own loop and static pause
  (`coordinator.run_iteration`), each plugin fetched for a Vegas cycle
  (`StreamManager._fetch_plugin_content`), each update on the
  `synchronous_updates` path, and every frame pushed
  (`DisplayManager.update_display` -> `note_frame()`). Beats are
  rate-limited to one ping and one heartbeat write every 5 s.
- **systemd watchdog.** `ledmatrix.service` is `Type=simple` with
  `WatchdogSec=120` and `NotifyAccess=main`. `run.py` sends
  `WATCHDOG_USEC` = 15 minutes before importing anything heavy (start-up loads
  plugins and runs the 20 s update budget, and the watchdog clock starts with
  the process). After the first frame -- or the first full pass, when there is
  nothing to draw -- the loop sends `READY=1`, restores the unit's 120 s and
  pings. `PluginManager.load_plugin()` on the render thread (a plugin enabled
  from the web UI, or loaded for on-demand) gets 15 minutes again, since it
  can run pip. A missed deadline is a SIGABRT; faulthandler, enabled on
  arming, dumps every thread's stack to the journal.
- **Heartbeat.** `/run/ledmatrix/display-heartbeat.json`
  (`{"pid", "mono", "wall"}`; `RuntimeDirectory=ledmatrix`, 0755, file 0644 so
  the web user can read it). Readers compare `mono` with their own
  `time.monotonic()` -- CLOCK_MONOTONIC is shared by every process and does not
  jump when NTP first sets an RTC-less Pi's clock. `/api/v3/health` calls it
  `stalled` past 60 s; no file is `not_reported` and changes nothing. A clean
  stop removes it. Without `RuntimeDirectory=` (an older unit) the display,
  as root, creates the directory itself; off Linux, or without root, there
  is no heartbeat.

## Plugin system

[`src/plugin_system/`](../src/plugin_system/):

| Area | Where |
|---|---|
| Base class plugins implement | [`base_plugin.py`](../src/plugin_system/base_plugin.py) (`BasePlugin`, `VegasDisplayMode`) |
| Finding a plugin's directory | [`plugin_dirs.py`](../src/plugin_system/plugin_dirs.py): manifest `id` first, then directory `<id>` or `ledmatrix-<id>` |
| Discovery, load, unload, scheduled updates (display process) | [`plugin_manager.py`](../src/plugin_system/plugin_manager.py) (`PluginManager`) |
| Manifest reads (web process) | [`plugin_catalog.py`](../src/plugin_system/plugin_catalog.py) (`PluginCatalog`; see [who runs plugins](#web-and-display-processes-who-runs-plugins)) |
| Import and instantiate | [`plugin_loader.py`](../src/plugin_system/plugin_loader.py) (`PluginLoader.load_plugin()`: dependencies, module, class) |
| Timeouts | [`plugin_executor.py`](../src/plugin_system/plugin_executor.py) (`PluginExecutor`, 30 s default; a timed-out thread is abandoned, not killed) |
| Circuit breaker | [`plugin_health.py`](../src/plugin_system/plugin_health.py) (`PluginHealthTracker`: 3 consecutive failures open the circuit for 300 s) |
| Resource metrics | [`resource_monitor.py`](../src/plugin_system/resource_monitor.py) |
| Config schemas and defaults | [`schema_manager.py`](../src/plugin_system/schema_manager.py) |
| Install, update, uninstall | [`store_manager.py`](../src/plugin_system/store_manager.py) (`PluginStoreManager`), with its methods split across [`store_registry.py`](../src/plugin_system/store_registry.py) (registry, GitHub), [`store_install.py`](../src/plugin_system/store_install.py) and [`store_update.py`](../src/plugin_system/store_update.py) |
| Core-version gate | [`compatibility.py`](../src/plugin_system/compatibility.py) |

Discovery scans only `plugin_system.plugins_directory` (default
`plugin-repos/`). Scheduled `update()` calls run on one background worker
thread; a per-plugin lock keeps `display()` from running during an update.

**Store flow.** `install_plugin()` renames any existing copy aside
(`<id>.standalone-backup-preinstall`), installs the new one, and puts the old
copy back if the install fails. Monorepo plugins come from the GitHub Trees
API, falling back to the repository ZIP; other plugins by `git clone` or
download. The manifest is checked (see
[required fields](PLUGIN_API_REFERENCE.md#manifest-required-fields)), the core
version gate runs, then dependencies are installed as root through
`scripts/fix_perms/safe_pip_install.sh`. `update_plugin()` pulls git
installs, undoing a pull whose new version is incompatible, and reinstalls
everything else through `_reinstall_with_rollback()`.

## Web interface

- **App.** [`web_interface/app.py`](../web_interface/app.py) builds the
  Flask `app` at import time, creates the managers -- a `PluginCatalog`,
  never a `PluginManager` -- and registers two blueprints.
  `web_interface/start.py` runs it on port 5000.
- **Pages.** [`blueprints/pages_v3.py`](../web_interface/blueprints/pages_v3.py)
  serves the shell `templates/v3/base.html` at `/` and each tab as a
  partial at `/partials/<name>` (templates in
  `web_interface/templates/v3/partials/`). Plugin configuration tabs are
  rendered from the plugin's schema by `plugin_config.html`.
- **API.** [`blueprints/api_v3/`](../web_interface/blueprints/api_v3/) is one
  blueprint at `/api/v3`, split by area: `backup.py`, `config.py`,
  `display.py`, `fonts.py`, `misc.py` (health, logs, errors, cache, sync),
  `starlark.py`, `system.py` (service actions, updates, git), `wifi.py`, and
  the plugin routes: `plugins.py` (installed list, enable/disable, plugin
  actions), `plugin_store.py` (install, update, uninstall, store),
  `plugin_config.py` (config, schema, reset), `plugin_assets.py` (uploads,
  plugin static files), `plugin_health.py` (health, metrics, limits),
  `plugin_operations.py` (operation history, state reconciliation) and
  `plugin_calendar.py`. `__init__.py` defines the blueprint and shared helpers and
  imports the modules so their routes register. Endpoints are listed in
  [REST_API_REFERENCE.md](REST_API_REFERENCE.md).
- **Front end.** HTMX loads each tab's partial on first open
  (`hx-trigger="loadtab"`); Alpine.js holds page state. Scripts are in
  `web_interface/static/v3/js/`; form widgets are bundled from
  [`js/widgets/`](../web_interface/static/v3/js/widgets/README.md).
- **Server-sent events** (`app.py`): `/api/v3/stream/stats` (CPU, memory,
  temperature, service state, every 10 s), `/api/v3/stream/display` (preview
  frames when the PNG changes) and `/api/v3/stream/logs` (journal of both
  services). One generator thread per stream is shared by all clients.

## Updates

- **Update Code** on the Overview tab and the automatic updater both call
  `perform_core_update()` in
  [`api_v3/system.py`](../web_interface/blueprints/api_v3/system.py):
  fetch branches and tags, move the checkout for the update channel, reinstall
  changed requirement files, report whether a restart is needed.
- **Update channels** (`auto_update.channel`):
  [`web_interface/update_channel.py`](../web_interface/update_channel.py)
  decides the move. `stable` checks out the newest `vX.Y.Z` tag (detached
  HEAD) when it contains the current commit; `beta` is
  `git pull --rebase --autostash` on the current branch, and leaves a
  detached release for `main` first. A stable device newer than the newest
  release keeps pulling `main` until a release contains its commit, so no
  update ever moves backwards; a config without the key is written as
  `stable` once the device reaches a release. Checkouts carry uncommitted
  edits across with `git stash create`/`apply`, and keep them in the stash
  list if they no longer apply.
- **Automatic updates** (`auto_update.enabled`, off by default):
  `AutoUpdater` in [`web_interface/auto_update.py`](../web_interface/auto_update.py)
  runs in the web process, checks every 30 minutes, and updates at most
  weekly between 02:00 and 05:00. Before pulling it copies
  [`scripts/utils/auto_update_verify.py`](../scripts/utils/auto_update_verify.py)
  to `data/auto_update_verifier.py`, then writes
  `data/auto_update_verify.request`. That file triggers
  `ledmatrix-update-verify.path`, which runs the verifier as a separate unit
  (so restarting the web service does not kill it). The verifier restarts
  both services, waits for the web API to answer and the display service to
  stay up -- and, when the display wrote a heartbeat before the update, to
  keep one fresh from the restarted process (see Liveness) -- and on failure
  returns to where HEAD was (the branch, or detached on the previous
  release; `old_ref` in the pending file), resets to the previous commit
  and restarts again.
  Plugin updates run only after a verified core update. State is in
  `data/auto_update_state.json` and `data/auto_update_pending.json`.
- **Startup validator.** `StartupValidator`
  ([`src/startup_validator.py`](../src/startup_validator.py)) runs twice in
  `DisplayController.__init__`: config and cache directory first, then
  enabled plugins once the plugin manager exists. It also warns when an
  installed systemd unit differs from its template in `systemd/`. Results
  are logged; startup continues either way. Nothing rewrites installed units
  on update: a unit change such as the watchdog reaches an existing install
  only when `install_service.sh` is re-run.

## Where to start reading

| Task | Start with |
|---|---|
| Change rotation, durations or priorities | `DisplayController.run()` and `_get_display_duration()` in [`display_controller.py`](../src/display_controller.py) |
| Add a config key | [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md), [`config/config.template.json`](../config/config.template.json), the tab's partial and `api_v3/config.py` |
| Change drawing or fonts | [`display_manager.py`](../src/display_manager.py), [`font_manager.py`](../src/font_manager.py), [`src/common/bdf_font.py`](../src/common/bdf_font.py) |
| Add a plugin-facing API | [`base_plugin.py`](../src/plugin_system/base_plugin.py) or [`src/common/`](../src/common/README.md); document it in [PLUGIN_API_REFERENCE.md](PLUGIN_API_REFERENCE.md) |
| Plugin install/update bugs | `PluginStoreManager` in [`store_manager.py`](../src/plugin_system/store_manager.py) |
| A plugin that won't load | `PluginManager.load_plugin()` and `PluginLoader.load_plugin()`; `python3 scripts/check_plugin.py --plugin <id>` |
| Add an API endpoint | the matching module in [`api_v3/`](../web_interface/blueprints/api_v3/) |
| Add a web UI tab or control | `templates/v3/base.html`, the tab's partial, `pages_v3.py` |
| Vegas scroll | [`src/vegas_mode/coordinator.py`](../src/vegas_mode/coordinator.py) |
| Installer or permissions | [`first_time_install.sh`](../first_time_install.sh), [`scripts/install/`](../scripts/install/), [PERMISSIONS.md](PERMISSIONS.md) |
| Work without a Pi | [DEV_PREVIEW.md](DEV_PREVIEW.md), [EMULATOR_SETUP_GUIDE.md](EMULATOR_SETUP_GUIDE.md), [HOW_TO_RUN_TESTS.md](HOW_TO_RUN_TESTS.md) |
