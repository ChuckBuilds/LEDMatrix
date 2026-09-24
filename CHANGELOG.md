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

- Scripts and installer:
  - `fix_web_permissions.sh` makes `safe_plugin_rm.sh` and `safe_pip_install.sh` root-owned again after resetting ownership. A web-user-owned copy of either is a root shell, since sudo lets the web user run them as root. It also restores `config_secrets.json` to mode 640.
  - `configure_wifi_permissions.sh` checks its rules with `visudo -c` before installing them, and grants the NetworkManager captive-portal `cp` and `rm` commands `wifi_manager` runs.
  - `configure_web_sudo.sh` uses a random temp file and installs its rules with mode 440.
  - The installer prints its completion summary before the `-y` reboot, and describes the setup access point as an open network (it was shown with a password it doesn't have).
  - `fix_cache_permissions.sh` applies `setup_cache.sh`'s `ledmatrix`-group model instead of setting 777.
  - `check_system_compatibility.sh` reports anything but Debian 13 (Trixie) as unsupported, and reaches its summary.
  - New `scripts/README.md` lists every script.
- Docs:
  - New `docs/ARCHITECTURE.md` (processes, shared state, display loop, plugin system, web UI) and `docs/PERMISSIONS.md` (owners, modes, both sudoers files, repair scripts).
  - Deprecated plugin APIs are marked in the plugin docs.
  - `src/common/README.md` covers every module.
  - Stale setup, service and troubleshooting claims are corrected.

- Plugin store and plugin manager fixes:
  - Updating a plugin that was installed from a ZIP no longer tries to reinstall it from the LEDMatrix repository's own URL.
  - Repository URLs with `.git` in the middle are no longer mangled. The URL helpers now live in `src/plugin_system/repo_urls.py`.
  - Installing from a URL works when the repository's only branch isn't `main` or `master`.
  - A missing required config field is reported once, by name.
  - A plugin that went over `max_memory_mb` once is no longer refused on every call after that.
  - `reload_plugin` reads the manifest from the plugin's discovered directory.
  - Removed: `last_display` from plugin state info and `get_last_display()` (nothing recorded them); `PluginOperationQueue`'s `history_file` and `lazy_load` arguments; and `data/plugin_operations.json`, which nothing read.

- Core service fixes:
  - `/api/v3/errors` shows each exception's real stack trace instead of `NoneType: None`.
  - Wi-Fi disconnect takes the saved connection profile down.
  - `wifi_config.json` is written atomically, and a save that fails now gets a 500.
  - `plugin://` fonts load from the plugin's own install directory. `FontManager.register_plugin_fonts()` takes an optional `plugin_dir`.
  - `APIHelper` keeps cached responses for the `cache_ttl` it was given, instead of always 300 s.
  - Logo scales from 0.1 to 10 are honoured everywhere; values outside that range are clamped.
  - `LogoHelper` and `logo_downloader`: an empty ESPN logo list counts as a failed download, and the placeholder is written at the requested path.
  - Bundled font paths no longer depend on the directory the process was started from.
  - Backups record `src.__version__`.
  - Removed: `BackgroundDataService`'s `queue_size` stat and `clear_completed_requests()`.

- Web API fixes:
  - A plugin save drops repeated entries in lists whose schema says `uniqueItems`, instead of failing validation.
  - `/api/v3/health` reports the real plugin count.
  - A malformed `vegas_plugin_order` or `vegas_excluded_plugins` is refused with a 400 and nothing is saved. It used to wipe the saved list.
  - The per-plugin health and metrics routes return the display service's latest state.
  - Resetting a plugin's config takes a backup first and reports a failed save.
  - System metrics that can't be read are `null` everywhere: `cpu_temp` off a Pi, and every metric without psutil, where `/system/status` now answers 200 instead of 503.
  - `/plugins/store/refresh` no longer claims a commit-metadata refresh it doesn't do.
  - The plugin-config list repair code is in one place, `src/web_interface/config_arrays.py`.

- Web UI:
  - Cache tab errors no longer show up in the Logs tab.
  - A tab that fails to load shows "Try again" instead of a skeleton that never goes away.
  - Plugin Store search and registry errors appear as a notification, and the Plugin Manager stays on screen.
  - The image schedule button works on uploaded images, and the editor stays open while you edit.
  - A failed plugin toggle moves the switch back.
  - Each save shows one notification; a failed Durations save says it failed.
  - Stats the server can't read show `--`.
  - New `window.LEDEscape` (`html`, `attr`, `jsStringAttr`) replaces about 30 copied escapers. `window.escapeHtml` and `window.escapeAttribute` remain as aliases for plugin pages.

- Display and Vegas:
  - Vegas `max_cycle_duration` defaults to 240 s when unset, as documented (it was 600 s). The Vegas defaults are now defined once.
  - The display controller stops Vegas mode on shutdown.
  - Startup validation warnings are logged once, not twice.
  - Vegas logs one INFO line per plugin-list refresh.
  - `run.py -d` shows `display_manager` debug output.
  - Removed: the Vegas staging buffer that was never filled (`swap_buffers()`, and `staging_count` / `current_index` in `get_buffer_status()`), unread `ContentSegment` fields, and `geometry.find_blank_cut()`.

- The web service (`ledmatrix-web`) logs through `src.logging_config` like the
  display service, so `journalctl -p err -u ledmatrix-web` works. Successful
  GET/HEAD/OPTIONS requests (the UI's polling) are logged at DEBUG instead of
  INFO; 4xx at WARNING, 5xx at ERROR. `LEDMATRIX_DEBUG=true` shows them again.
  `web_interface/logging_config.py` is removed. The web cache
  (`web_interface/cache.py`) now honours the TTL a value was stored with and is
  thread-safe.

- One plugin-directory resolver, `src/plugin_system/plugin_dirs.py`, behind
  discovery, `PluginManager.get_plugin_directory`, `PluginLoader`, the store and
  state reconciliation. A manifest's `id` wins over a directory merely named for
  the id; hidden and `.standalone-backup-` directories are never treated as
  plugins (auto-update could previously try to update a backup); ids like
  `a/b` or `..` resolve to nothing everywhere. Installs where each directory is
  named for its manifest id, the installer's layout, behave as before.

- `/api/v3` routes answer an exception they don't handle themselves from one
  blueprint error handler, with the same `{status, message, details}` body the
  53 removed per-route catch-alls returned. `ErrorCategory` and the
  `error_category` key are removed from `src.web_interface.errors` (nothing read
  them); `exception_error_response()` replaces the `from_exception` +
  `error_response` pairs. A failing plugin action script's error now names the
  real failure instead of `UnboundLocalError`.

- `FontManager.get_font()` returns a BDF font at its native size when asked for
  a size the file doesn't contain (5x7.bdf at 8 or 10px, say). It used to
  return PIL's default font, a different typeface, so a plugin that relied on
  that will now render the font it asked for.
- `src.wifi_manager.get_wifi_status_path()` — where WiFi status messages for
  the display are written (`config/wifi_status.json`).
- `src.device_location` — a blank `Location` field on a Starlark (Tidbyt) app
  now renders at the device's City / State / Country (geocoded once via
  Open-Meteo and cached) instead of the app author's hard-coded default,
  usually San Francisco. A location saved on the app still wins. With no
  device city set, or when the lookup fails or finds no match, the app keeps
  its own default (a failed lookup is retried after 30 minutes). Clearing an
  app's location in the web UI now actually clears it; the save used to drop
  the blank field, so the old value stayed.
- `src.common.bdf_font` — `load_bdf_face(path, size)` (a cached
  `freetype.Face` plus the pixel size it really renders at, falling back to
  the file's native strike) and `draw_bdf_text(draw, text, x, y, face, color)`.
  `DisplayManager`, `FontManager`, `element_style` and the plugin test harness
  now all load and draw BDF text through it; the panel's pixels are unchanged
  and BDF text draws 10-250x faster. The plugin test harness's
  `calendar_font` / `bdf_5x7_font` now has the panel's 7px size set: it used
  to be an unsized face, so in golden images and `check_plugin` /
  `dev_server` previews its text sat 6px above where the panel draws it (off
  the canvas entirely near the top) and `get_font_height()` returned 0.

- The web UI's Fonts tab has a **Used by** column: the loaded plugins that
  registered each font with `FontManager.register_manager_font()`, published
  by the display service to the shared cache (`src/font_usage.py`) and merged
  into `GET /api/v3/fonts/catalog` as `used_by`. Deleting a font a plugin
  uses now names those plugins in the confirmation (it is not blocked).
  `FontManager.forget_manager_fonts()` is new; unloading a plugin calls it.

Deprecated, removed in 3.7.0 (each logs a warning on first use; see
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

### Config writes

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

New names in existing modules (no new modules; a plugin importing these must
floor on the release that ships them):

- `src.common.api_helper`: `USER_AGENT`, `DEFAULT_HTTP_HEADERS` (read-only).
- `src.logo_downloader`: `fetch_logo`, `save_png_atomically`,
  `shared_downloader`.
- `src.common.sports_card.unshare_element_fonts` takes an optional third
  argument, `element_for_font` (default: the module's `ELEMENT_FOR_FONT`, so
  existing calls are unchanged).

### Sports twins

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

### Logo downloads

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

### HTTP headers

- The logo downloader and the background data service send the real
  `LEDMatrix/1.0 (+https://github.com/ChuckBuilds/LEDMatrix)` User-Agent
  instead of a `yourusername` / `contact@example.com` placeholder, and no
  longer set `Accept-Encoding: ... br` by hand (brotli is not installed, so a
  `br` response could not be decoded); requests picks the encodings.

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
- `test/test_common_is_hardware_free.py` — `src/common` must import without
  `rgbmatrix` and never import `src.base_classes`, `src.display_manager` or
  `src.plugin_system` at module level.
- `src/common/espn_dates.py` — `fetch_espn_scoreboard`,
  `fetch_espn_date_chunks`, `espn_date_chunks`, `clamp_espn_limit`,
  `ESPN_MAX_LIMIT`: fetch an ESPN scoreboard date range now that ESPN rejects
  ranges (see Sports data below). Plugins bundle a copy of it.

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

### Sports data

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

### Security (request paths and inline handlers, siblings of #561)

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
  reporting `api_v3.py` as missing.

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
