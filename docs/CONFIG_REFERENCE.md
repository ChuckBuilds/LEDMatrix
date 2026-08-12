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
| `timezone` | string, `"America/New_York"` | IANA timezone for schedules and displays | `ConfigManager.get_timezone()` |
| `target_fps` | int, `100` | Frame-rate ceiling for plugin rendering | `src/plugin_system/base_plugin.py`, `src/common/sports_scroll.py` |
| `location` | object | `city` / `state` / `country`, offered to plugins that need a location (weather, etc.) | plugins via merged config |

## `schedule` — display on/off hours

| Key | Type / default | Meaning |
|---|---|---|
| `enabled` | bool, `false` | Master switch for scheduled display on/off |
| `mode` | `"global"` or `"per-day"`, template uses `"per-day"` | Whether one time range applies to all days or each day has its own |
| `start_time` / `end_time` | `"HH:MM"`, `07:00`–`23:00` | Global-mode on/off times |
| `days.<weekday>.{enabled,start_time,end_time}` | per-day objects | Per-day-mode overrides |

Read by `DisplayController` (`src/display_controller.py`, `_check_schedule`
around line 603). Managed in the web UI under Schedule.

## `dim_schedule` — scheduled brightness dimming

Same shape as `schedule`, plus:

| Key | Type / default | Meaning |
|---|---|---|
| `dim_brightness` | int, `30` | Brightness percentage applied while the dim window is active |

Read by `DisplayController` (`src/display_controller.py` around line 770;
saved via `POST /api/v3/config/dim-schedule`). The display returns to
`display.hardware.brightness` outside the window.

## `display.hardware` — matrix panel hardware

All keys map to the corresponding `rpi-rgb-led-matrix` options and are read
in `DisplayManager` (`src/display_manager.py`, ~lines 270–295).

| Key | Type / default |
|---|---|
| `rows` / `cols` | int, `32` / `64` |
| `chain_length` | int, `2` |
| `parallel` | int, `1` |
| `brightness` | int, `90` |
| `hardware_mapping` | string, `"adafruit-hat"` (code default `"adafruit-hat-pwm"`) |
| `scan_mode` | int, `0` |
| `pwm_bits` | int, `9` (code default 10) |
| `pwm_dither_bits` | int, `1` |
| `pwm_lsb_nanoseconds` | int, `130` (code default 150) |
| `disable_hardware_pulsing` | bool, `false` |
| `inverse_colors` | bool, `false` |
| `show_refresh_rate` | bool, `false` |
| `led_rgb_sequence` | string, `"RGB"` |
| `limit_refresh_rate_hz` | int, `100` (code default 90) |
| `pixel_mapper_config` | string, `""` — e.g. `"U-mapper"` / `"Rotate:90"` |
| `row_address_type` | int, `0` — non-standard panel row addressing |
| `multiplexing` | int, `0` — panel multiplexing scheme |
| `panel_type` | string, `""` — set to `"FM6126A"` or `"FM6127"` for panels needing init |

Where "code default" differs from the template value, the code default only
applies if the key is missing entirely from your config.

## `display.runtime`

| Key | Type / default | Meaning |
|---|---|---|
| `gpio_slowdown` | int, `3` | GPIO timing slowdown for faster Pis |
| `rp1_rio` | int, `0` | RP1 RIO mode on Pi 5 (applied only if the installed matrix library supports it) |

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
| `display_durations` | object, `{}` | Per-plugin display duration in seconds, keyed by plugin id (e.g. `"clock": 15`) | `src/display_controller.py:1030` |
| `plugin_rotation_order` | array, `[]` | Explicit rotation order of plugin ids; empty = all enabled plugins in discovery order | `src/display_controller.py:2894` |
| `use_short_date_format` | bool, `true` | Compact date rendering in sports scoreboards | `src/base_classes/sports/core.py` |
| `dynamic_duration.max_duration_seconds` | int, optional | Cap for plugins that request dynamic display time | `src/display_controller.py:405` |

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
| `frame_based_scrolling` | bool, `true` — frame-count-based scroll stepping |
| `scroll_delay` | float, `0.02` — seconds between scroll updates (~50 FPS) |
| `live_in_ticker` | bool, `false` — keep scrolling during live games instead of handing the display to a full-screen scoreboard |
| `live_weight` | int, `3` (1–10) — slots per cycle for a plugin with live content |
| `favorite_live_weight` | int, `5` (1–10) — slots per cycle when a plugin reports a favorite team is live |

## `sync` — multi-display synchronization

Read by `src/common/sync_manager.py` and `src/display_controller.py`.

| Key | Type / default | Meaning |
|---|---|---|
| `role` | `"standalone"` (default), `"leader"`, or `"follower"` | This device's role in a synced pair |
| `port` | int, `5765` | TCP port used for sync traffic |
| `follower_position` | `"left"` (default) or `"right"` | Which half of the combined image this follower renders (`src/display_controller.py:522`) |

## `plugin_system`

Read by the plugin loader/manager (`src/plugin_system/`).

| Key | Type / default | Meaning |
|---|---|---|
| `plugins_directory` | string, `"plugin-repos"` | Where the Plugin Store installs plugins |
| `auto_discover` | bool, `true` | Scan the plugins directory at startup |
| `auto_load_enabled` | bool, `true` | Load discovered plugins automatically |
| `development_mode` | bool, `false` | Development conveniences in the web UI (editable under General settings) |

## Plugin config blocks

Every installed plugin stores its settings under a top-level key equal to
its plugin id (the template ships one for the bundled `web-ui-info`
plugin). The shape of each block is defined by that plugin's
`config_schema.json`; common keys are `enabled` and `display_duration`.
See [PLUGIN_CONFIG_CORE_PROPERTIES.md](PLUGIN_CONFIG_CORE_PROPERTIES.md).

## `config/config_secrets.json`

| Key | Meaning |
|---|---|
| `github.api_token` | Optional GitHub token the Plugin Store uses to avoid API rate limits (`src/plugin_system/store_manager.py:348`) |
| `<plugin-id>.*` | Secrets a plugin declares with `"x-secret": true` in its config schema; merged into that plugin's config at load time |
