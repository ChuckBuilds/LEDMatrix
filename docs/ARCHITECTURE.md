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
| On-demand request | cache `display_on_demand_request` | web: `start_on_demand_display()` / `stop_on_demand_display()` in [`api_v3/display.py`](../web_interface/blueprints/api_v3/display.py) | display: `_poll_on_demand_requests()` |
| On-demand state | cache `display_on_demand_state` | display: `_publish_on_demand_state()` | web: `/api/v3/display/on-demand/status` |
| Current screen | cache `display_current_state` | display | web: `/api/v3/display/current-status` |
| Plugin errors | cache `plugin_error_snapshot` | display: `ErrorSnapshotPublisher` ([`src/error_aggregator.py`](../src/error_aggregator.py)) | web: `read_error_report()` for `/api/v3/errors/*` |
| Error clear | cache `plugin_error_clear_request` | web | display |
| Font usage | cache `font_usage_snapshot` | display: `FontUsagePublisher` ([`src/font_usage.py`](../src/font_usage.py)) | web: Fonts tab |
| Plugin health | cache `plugin_health:<id>` | display (web writes on reset) | web: `/api/v3/plugins/health` |
| Preview frame | `/tmp/led_matrix_preview.png` | display: `DisplayManager`, gated by [`snapshot_policy`](../src/common/snapshot_policy.py) | web: display SSE stream, `/api/v3/health` (file age) |
| Preview viewer marker | `/tmp/led_matrix_preview_viewer` | web, while a preview is open | display: writes full-rate snapshots only while it is fresh |
| Hardware init status | `/tmp/led_matrix_hw_status.json` | display | web: `/api/v3/hardware/status` |

The on-demand start route also restarts `ledmatrix.service` by default so the
request takes effect straight away.

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
then normal rotation.

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
  restart. It also keeps the display on during scheduled off hours.
- **Live priority.** `_check_live_priority()` looks for a plugin whose
  `has_live_priority()` and `has_live_content()` are both true and switches
  to it, rotating between several live games.
- **Schedule and dim schedule.** `_check_schedule()` reads `schedule`;
  `_check_dim_schedule()` reads `dim_schedule` and
  `display.hardware.brightness`. Both are re-evaluated once a minute.
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
  for its own section. Set `LEDMATRIX_HOT_RELOAD=false` to turn this off.
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

## Plugin system

[`src/plugin_system/`](../src/plugin_system/):

| Area | Where |
|---|---|
| Base class plugins implement | [`base_plugin.py`](../src/plugin_system/base_plugin.py) (`BasePlugin`, `VegasDisplayMode`) |
| Finding a plugin's directory | [`plugin_dirs.py`](../src/plugin_system/plugin_dirs.py): manifest `id` first, then directory `<id>` or `ledmatrix-<id>` |
| Discovery, load, unload, scheduled updates | [`plugin_manager.py`](../src/plugin_system/plugin_manager.py) (`PluginManager`) |
| Import and instantiate | [`plugin_loader.py`](../src/plugin_system/plugin_loader.py) (`PluginLoader.load_plugin()`: dependencies, module, class) |
| Timeouts | [`plugin_executor.py`](../src/plugin_system/plugin_executor.py) (`PluginExecutor`, 30 s default; a timed-out thread is abandoned, not killed) |
| Circuit breaker | [`plugin_health.py`](../src/plugin_system/plugin_health.py) (`PluginHealthTracker`: 3 consecutive failures open the circuit for 300 s) |
| Resource metrics | [`resource_monitor.py`](../src/plugin_system/resource_monitor.py) |
| Config schemas and defaults | [`schema_manager.py`](../src/plugin_system/schema_manager.py) |
| Install, update, uninstall | [`store_manager.py`](../src/plugin_system/store_manager.py) (`PluginStoreManager`) |
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
  Flask `app` at import time, creates the managers, and registers two
  blueprints. `web_interface/start.py` runs it on port 5000.
- **Pages.** [`blueprints/pages_v3.py`](../web_interface/blueprints/pages_v3.py)
  serves the shell `templates/v3/base.html` at `/` and each tab as a
  partial at `/partials/<name>` (templates in
  `web_interface/templates/v3/partials/`). Plugin configuration tabs are
  rendered from the plugin's schema by `plugin_config.html`.
- **API.** [`blueprints/api_v3/`](../web_interface/blueprints/api_v3/) is one
  blueprint at `/api/v3`, split by area: `backup.py`, `config.py`,
  `display.py`, `fonts.py`, `misc.py` (health, logs, errors, cache, sync),
  `plugins.py`, `starlark.py`, `system.py` (service actions, updates, git),
  `wifi.py`. `__init__.py` defines the blueprint and shared helpers and
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
  `git pull --rebase`, reinstall changed requirement files, report whether a
  restart is needed.
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
  stay up, and on failure resets to the previous commit and restarts again.
  Plugin updates run only after a verified core update. State is in
  `data/auto_update_state.json` and `data/auto_update_pending.json`.
- **Startup validator.** `StartupValidator`
  ([`src/startup_validator.py`](../src/startup_validator.py)) runs twice in
  `DisplayController.__init__`: config and cache directory first, then
  enabled plugins once the plugin manager exists. It also warns when an
  installed systemd unit differs from its template in `systemd/`. Results
  are logged; startup continues either way.

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
