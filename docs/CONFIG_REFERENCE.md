# Configuration Reference

Every key in `config/config.json`, what it does, its default, and where the
code reads it. The file is created from `config/config.template.json` on
first run, and `ConfigManager._migrate_config()` merges any template keys
added by later releases into your existing config (your values are never
overwritten). Secrets live in `config/config_secrets.json` and are merged
into the config at load time.

Most settings are editable from the web interface; this page documents the
underlying keys for people editing `config.json` directly or writing
tooling against it.

## Top level

| Key | Type / default | Meaning | Read by |
|---|---|---|---|
| `web_display_autostart` | bool, `true` | Whether the web interface service starts with the system | `scripts/utils/start_web_conditionally.py` |
| `auto_update.enabled` | bool, `false` | Weekly automatic updates: LEDMatrix code first (health-checked, rolled back on failure), then installed plugins. Toggle in the General tab or install with `first_time_install.sh --enable-auto-update` | `web_interface/auto_update.py`, `src/auto_update_setup.py` (`is_enabled()`) |
| `timezone` | string, `"America/New_York"` | IANA timezone for schedules and displays | `ConfigManager.get_timezone()` |
| `target_fps` | int, `100` | Legacy "Scroll Frame Rate". Core scrolling no longer reads it: scroll frames are presented at `display.hardware.limit_refresh_rate_hz` divided by each scroll's frame hold, and speed comes from each plugin's scroll settings. Still exposed to plugins via `BasePlugin.global_config` | `src/plugin_system/base_plugin.py` |
| `location` | object | `city` / `state` / `country`. Supplies the **default** for a plugin's own `location_city` / `location_state` / `location_country` setting, so weather, radar and friends follow this device without being configured twice. A value saved on the plugin itself still overrides it. Starlark (Tidbyt) apps get the same treatment: a `Location` field left blank on the app renders at this city (geocoded once via Open-Meteo, coordinates cached permanently) instead of the app author's default, which is usually San Francisco. If the city can't be looked up (no match, or the geocoder is unreachable; retried after 30 minutes), the app keeps its own default. | `SchemaManager.apply_device_location()`, then plugins via merged config; `src/device_location.py` for Starlark apps |

## `schedule` — display on/off hours

| Key | Type / default | Meaning |
|---|---|---|
| `enabled` | bool, `false` | Master switch for scheduled display on/off |
| `mode` | `"global"` or `"per-day"`, template uses `"per-day"` | Whether one time range applies to all days or each day has its own |
| `start_time` / `end_time` | `"HH:MM"`, `07:00`–`23:00` | Global-mode on/off times |
| `days.<weekday>.{enabled,start_time,end_time}` | per-day objects | Per-day-mode overrides |

Read by `DisplayController._check_schedule()` (`src/display_controller.py`).
Managed in the web UI under Schedule.

## `dim_schedule` — scheduled brightness dimming

Same shape as `schedule` (the template sets its `mode` to `"global"`), plus:

| Key | Type / default | Meaning |
|---|---|---|
| `dim_brightness` | int, `30` | Brightness percentage applied while the dim window is active |

Read by `DisplayController._check_dim_schedule()` (`src/display_controller.py`;
saved via `POST /api/v3/config/dim-schedule`). The display returns to
`display.hardware.brightness` outside the window.

## `display.hardware` — matrix panel hardware

All keys map to the corresponding `rpi-rgb-led-matrix` options and are read
in `DisplayManager._setup_matrix` (`src/display_manager.py`). Defaults are the
`config/config.template.json` values: `ConfigManager` adds any key missing from
`config.json` from the template on load, so `DisplayManager`'s own fallbacks
don't apply on a normal install.

The ranges are what the pinned rgbmatrix library and its Python binding accept
(`src/matrix_support.py`). The config API refuses anything else; a value
hand-edited into `config.json` makes the display log the setting and run in
fallback mode instead of starting the matrix.

| Key | Type / default |
|---|---|
| `rows` / `cols` | int, `32` / `64` — rows: even, 8–64; cols: at least 16 |
| `chain_length` | int, `2` — 1–255 (the Python binding stores it in one byte) |
| `parallel` | int, `1` — 1–3, and no more than `hardware_mapping` has outputs (`regular`, `classic`: 3; the others: 1) |
| `brightness` | int, `90` — 1–100 |
| `hardware_mapping` | string, `"adafruit-hat"` — `"adafruit-hat-pwm"`, `"adafruit-hat"`, `"regular"`, `"regular-pi1"`, `"classic"` or `"classic-pi1"` (case-insensitive; `compute-module` isn't in the installed build). A Pi 5 doesn't support `"classic-pi1"` |
| `scan_mode` | int, `0` — `0` progressive, `1` interlaced |
| `pwm_bits` | int, `9` — 1–11 |
| `pwm_dither_bits` | int, `1` — 0–2 |
| `pwm_lsb_nanoseconds` | int, `130` — 50–3000 |
| `disable_hardware_pulsing` | bool, `false` — `true` times brightness pulses in software (less exact); hardware pulsing needs the OE line on GPIO 18 and the Pi's onboard sound driver off |
| `inverse_colors` | bool, `false` |
| `show_refresh_rate` | bool, `false` — prints the refresh rate to stdout; draws nothing on the panel |
| `led_rgb_sequence` | string, `"RGB"` — `"RGB"`, `"RBG"`, `"GRB"`, `"GBR"`, `"BRG"` or `"BGR"` |
| `limit_refresh_rate_hz` | int, `100` — `0` = no cap; scroll timing assumes 100 Hz when `0` |
| `pixel_mapper_config` | string, `""` — e.g. `"U-mapper"` / `"Rotate:90"`; mappers that rotate or fold the chain change the display size plugins and the web preview see |
| `orientation` | string, `"normal"` — `"180"` rotates the rendered image 180° for panels physically mounted upside down (e.g. to move the Pi/wiring to a more convenient side); `"90"` / `"270"` for a panel on its side, swapping width and height; composed onto `pixel_mapper_config` as a trailing `Rotate:<degrees>` mapper, so it stays independent of any custom `pixel_mapper_config` value |
| `row_address_type` | int, `0` — non-standard panel row addressing: `1` AB, `2` direct row select, `3` ABC, `4` ABC shift + DE direct, `5` SM5368 / B707 row shift register (e.g. Waveshare 96x48 V2, with `led_rgb_sequence` `"BGR"`). On a Pi 5 the library supports only `0` and `2`, and LEDMatrix enforces that (`src/pi5_matrix_support.py`) |
| `multiplexing` | int, `0` — 0–22, pixel wiring scheme for outdoor/specialty panels (names listed in the README) |
| `panel_type` | string, `""` — set to `"FM6126A"` or `"FM6127"` for panels needing init; FM6124 / FM6124D / FM6124DJ panels need none, so leave it `""` |

## `display.runtime`

| Key | Type / default | Meaning |
|---|---|---|
| `gpio_slowdown` | int, `3` | GPIO timing slowdown for faster Pis (0–10). On a Pi 5 in PIO mode start at `1` (`0` acts as `1`) and raise it if the image flickers or shows garbage. Panels on `row_address_type` `5` (SM5368 row drivers) can need 6–8 on a Pi 4 — lower values make rows jump |
| `rp1_rio` | int, `0` | Pi 5 only: `0` = PIO (less CPU), `1` = RIO (higher refresh; `gpio_slowdown` effect inverted). Applied only if the installed matrix library supports it |

## `display.double_sided`

Drives `_LogicalMatrix` in `src/display_manager.py` — renders the same
logical image to multiple chained physical panels.

| Key | Type / default | Meaning |
|---|---|---|
| `enabled` | bool, `false` | Mirror output across panel copies |
| `copies` | int, `2` | Number of physical copies in the chain |
| `axis` | `"horizontal"`, default | Axis along which panels are chained |

## `display` — other keys

| Key | Type / default | Meaning | Read by |
|---|---|---|---|
| `display_durations` | object, `{}` | Per-plugin display duration in seconds, keyed by plugin id (e.g. `"clock": 15`) | `DisplayController._get_display_duration()` (`src/display_controller.py`) |
| `plugin_rotation_order` | array, `[]` | Explicit rotation order of plugin ids; empty = all enabled plugins in discovery order | `DisplayController._apply_plugin_rotation_order()` (`src/display_controller.py`) |
| `use_short_date_format` | bool, `true` | Compact date rendering in sports scoreboards | Nothing since `src/base_classes` was removed; scoreboards read `display.use_short_date_format` from their own plugin config |
| `dynamic_duration.max_duration_seconds` | int, optional | Cap for plugins that request dynamic display time | `DisplayController._get_global_dynamic_cap()` (`src/display_controller.py`) |

## `display.vegas_scroll` — continuous scroll mode

Read by `src/vegas_mode/config.py` (`VegasScrollConfig.from_config`). See
[ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) for behavior details, including
[live content in the ticker](ADVANCED_FEATURES.md#live-content-in-the-ticker).

| Key | Type / default |
|---|---|
| `enabled` | bool, `false` |
| `scroll_speed` | int, `50` (px/s) |
| `separator_width` | int, `32` |
| `plugin_order` | array, `[]` |
| `excluded_plugins` | array, `[]` |
| `target_fps` | int, `125` |
| `buffer_ahead` | int, `2` |
| `intra_plugin_gap` | int, `8` |
| `render_width_pct` | int, `100` |
| `min_content_separation` | int, `24` |
| `min_cut_gap` | int, `6` |
| `continuous_scroll` | bool, `true` |
| `smooth_scroll` | bool, `true` |
| `extend_threshold_screens` | float, `2.0` |
| `auto_trim` | bool, `true` |
| `trim_threshold` | int, `10` |
| `content_padding` | int, `8` |
| `min_plugin_width` | int, `8` |
| `lead_in_width` | int, `0` |
| `plugins_per_cycle` | int, `6` |
| `max_plugin_width_ratio` | float, `0.0` |
| `overflow_mode` | string, `"rotate"` |
| `dynamic_duration_enabled` | bool, `true` |
| `min_cycle_duration` | int, `60` |
| `max_cycle_duration` | int, `240` |
| `frame_based_scrolling` | bool, `true` — does not step or set a frame rate; motion is by elapsed time either way. When `true`, `scroll_speed` passes through a clamp of 0.1–5 px per `scroll_delay` (see next row) |
| `scroll_delay` | float, `0.02` — not a frame period. Only used with `frame_based_scrolling`: the applied speed is `clamp(scroll_speed × scroll_delay, 0.1, 5) / scroll_delay` px/s, so at `0.02` speeds under 5 px/s run at 5, and at `0.001` nothing runs slower than 100 px/s |
| `live_in_ticker` | bool, `false` — keep scrolling during live games instead of handing the display to a full-screen scoreboard |
| `live_weight` | int, `3` (1–10) — slots per cycle for a plugin with live content |
| `favorite_live_weight` | int, `5` (1–10) — slots per cycle when a plugin reports a favorite team is live |

## `sync` — multi-display synchronization

Read by `src/common/sync_manager.py` and `src/display_controller.py`.

| Key | Type / default | Meaning |
|---|---|---|
| `role` | `"standalone"` (default), `"leader"`, or `"follower"` | This device's role in a synced pair |
| `port` | int, `5765` | TCP port used for sync traffic |
| `follower_position` | `"left"` (default) or `"right"` | Which half of the combined image this follower renders (`src/display_controller.py`) |

## `plugin_system`

| Key | Type / default | Meaning |
|---|---|---|
| `plugins_directory` | string, `"plugin-repos"` | Where the Plugin Store installs plugins and the only directory the plugin loader scans. Read by `PluginManager` and `PluginStoreManager` (`src/plugin_system/`); editable under General settings |
| `auto_discover`, `auto_load_enabled`, `development_mode` | bool | **Unused.** Legacy keys, read by nothing and no longer in the template; older configs may still carry them. Plugins are always discovered, and every plugin with `enabled: true` is loaded — to keep a plugin installed but dormant, set its own `enabled` to `false`. Not shown in the web UI; may be left in or removed from config.json |

## Plugin config blocks

Every installed plugin stores its settings under a top-level key equal to
its plugin id (the template ships one for the bundled `web-ui-info`
plugin). The shape of each block is defined by that plugin's
`config_schema.json`; common keys are `enabled` and `display_duration`.
See [PLUGIN_CONFIG_CORE_PROPERTIES.md](PLUGIN_CONFIG_CORE_PROPERTIES.md).

## `config/config_secrets.json`

| Key | Meaning |
|---|---|
| `github.api_token` | Optional GitHub token the Plugin Store uses to avoid API rate limits (`src/plugin_system/store_manager.py`) |
| `<plugin-id>.*` | Secrets a plugin declares with `"x-secret": true` in its config schema; merged into that plugin's config at load time |
