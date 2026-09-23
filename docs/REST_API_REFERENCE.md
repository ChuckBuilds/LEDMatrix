# LEDMatrix REST API Reference

Reference for the REST API served by the LEDMatrix web interface.

**Base URL**: `http://your-pi-ip:5000/api/v3`

Most endpoints answer JSON in this envelope:
```json
{
  "status": "success" | "error",
  "data": { ... },
  "message": "Optional message"
}
```

Not every endpoint follows it exactly. Where a response puts fields at the
top level instead of under `data` (install-from-url, registry-from-url, the
auth endpoints, upload endpoints, `system/git-info`, `system/check-update`),
the entry below says so.

## Table of Contents

- [Configuration](#configuration)
- [Display Control](#display-control)
- [Plugins](#plugins)
- [Plugin Store](#plugin-store)
- [System](#system)
- [Backup and Restore](#backup-and-restore)
- [Fonts](#fonts)
- [Cache](#cache)
- [WiFi](#wifi)
- [Streams](#streams)
- [Logs](#logs)
- [Error tracking](#error-tracking)
- [Health and Status](#health-and-status)
- [Schedule (dim/power)](#schedule-dimpower)
- [Integrations](#integrations)
- [Plugin-specific endpoints](#plugin-specific-endpoints)
- [Starlark Apps](#starlark-apps)
- [Skins](#skins)

> The API blueprint is the `api_v3` package in
> `web_interface/blueprints/api_v3/` (one module per area: `config.py`,
> `display.py`, `plugins.py`, `system.py`, `backup.py`, `fonts.py`,
> `misc.py`, `wifi.py`, `starlark.py`). `web_interface/app.py` registers it
> at `/api/v3` (`app.register_blueprint(api_v3, url_prefix='/api/v3')`).
> The three SSE endpoints (`/api/v3/stream/*`) are defined directly on the
> Flask app in `app.py` (`stream_stats`, `stream_display`, `stream_logs`).
> `test/fixtures/api_v3_url_map.json` is the canonical list of blueprint
> routes (116 URL rules); a test fails if the code and that fixture differ.

---

## Configuration

### Get Main Configuration

**GET** `/api/v3/config/main`

Return `config/config.json`. Fields whose names look like credentials
(`api_key`, `token`, `password`, `secret`, ...) are blanked to `""` in the
response; `config/config_secrets.json` values are never included.

**Response**:
```json
{
  "status": "success",
  "data": {
    "timezone": "America/New_York",
    "location": {
      "city": "New York",
      "state": "NY",
      "country": "US"
    },
    "display": { ... },
    "plugin_system": { ... }
  }
}
```

### Save Main Configuration

**POST** `/api/v3/config/main`

Update the main configuration. Accepts JSON (`Content-Type: application/json`)
or form data. The body uses the web UI's flat field names, which the handler
maps into the nested config:

| Fields | Stored at |
|--------|-----------|
| `timezone`, `city`, `state`, `country` | `timezone`, `location.*` |
| `web_display_autostart`, `auto_update_enabled` | `web_display_autostart`, `auto_update.enabled` |
| `plugins_directory` (and the unused legacy flags `auto_discover`, `auto_load_enabled`, `development_mode`, stored only when sent) | `plugin_system.*` |
| `target_fps` (30-200) | `target_fps` |
| `rows`, `cols`, `chain_length`, `parallel`, `brightness`, `hardware_mapping`, `pwm_bits`, `led_rgb_sequence`, `panel_type`, `pixel_mapper_config`, `disable_hardware_pulsing`, `inverse_colors`, `show_refresh_rate`, ... | `display.hardware.*` |
| `gpio_slowdown`, `rp1_rio` | `display.runtime.*` |
| `use_short_date_format` | `display.use_short_date_format` |
| `max_dynamic_duration_seconds` | `display.dynamic_duration.max_duration_seconds` |
| `double_sided_*`, `vegas_*`, `sync_*` | `display.double_sided`, `display.vegas_scroll`, `sync` |
| `<name>_duration`, `default_duration`, `duration__<mode>` | `display.display_durations.*` |
| `plugin_rotation_order` (list of plugin ids) | `display.plugin_rotation_order` |

Any other top-level key is deep-merged into the config as given.

A JSON body changes only the keys it contains; everything else keeps its
stored value. (Form posts from the web UI send every field of a tab, and
there an unchecked checkbox — which the browser omits — is saved as
`false`.)

**Request Body** (JSON):
```json
{
  "timezone": "America/New_York",
  "city": "New York",
  "brightness": 90
}
```

**Response**:
```json
{
  "status": "success",
  "message": "Configuration saved successfully"
}
```

Invalid values (e.g. an out-of-range `target_fps`, a hardware option the
Raspberry Pi 5 driver cannot use) are rejected with `400` and nothing is
saved.

### Get Schedule Configuration

**GET** `/api/v3/config/schedule`

Retrieve the current on/off schedule.

**Response**:
```json
{
  "status": "success",
  "data": {
    "enabled": true,
    "mode": "global",
    "start_time": "07:00",
    "end_time": "23:00"
  }
}
```

**Per-day mode response**:
```json
{
  "status": "success",
  "data": {
    "enabled": true,
    "mode": "per-day",
    "days": {
      "monday": {
        "enabled": true,
        "start_time": "07:00",
        "end_time": "23:00"
      },
      "tuesday": { ... }
    }
  }
}
```

### Save Schedule Configuration

**POST** `/api/v3/config/schedule`

Replace the schedule configuration.

**Request Body** (Global mode):
```json
{
  "enabled": true,
  "mode": "global",
  "start_time": "07:00",
  "end_time": "23:00"
}
```

**Request Body** (Per-day mode, flat form-field names):
```json
{
  "enabled": true,
  "mode": "per-day",
  "monday_enabled": true,
  "monday_start": "07:00",
  "monday_end": "23:00",
  "tuesday_enabled": true,
  "tuesday_start": "08:00",
  "tuesday_end": "22:00"
}
```

A day whose `<day>_enabled` key is absent counts as enabled, with default
times `07:00`-`23:00`. At least one day must be enabled.

**Response**:
```json
{
  "status": "success",
  "message": "Schedule configuration saved successfully"
}
```

### Get Secrets Configuration

**GET** `/api/v3/config/secrets`

Retrieve `config/config_secrets.json` with every set value replaced by eight
bullet characters (`"••••••••"`). Empty values and `YOUR_*` placeholders
are returned as-is, so a client can tell "set" from "not set".

**Response**:
```json
{
  "status": "success",
  "data": {
    "ledmatrix-weather": {
      "api_key": "••••••••"
    }
  }
}
```

### Save Raw Configuration

**POST** `/api/v3/config/raw/main`

Replace `config/config.json` with the JSON body (advanced use only).

**POST** `/api/v3/config/raw/secrets`

Save the secrets file (advanced use only). Masked values (`"••••••••"`)
and blank strings in the body are dropped, and the rest is merged onto the
stored secrets, so posting back the GET response unchanged changes nothing.
A secret cannot be cleared by blanking it here.

---

## Display Control

### Get Current Display

**GET** `/api/v3/display/current`

Get the latest display snapshot as a base64 PNG (`image` is `null` when no
snapshot is available).

**Response**:
```json
{
  "status": "success",
  "data": {
    "timestamp": 1234567890.123,
    "width": 128,
    "height": 32,
    "image": "base64_encoded_image_data"
  }
}
```

### Get Current Display Status

**GET** `/api/v3/display/current-status`

The mode and plugin the display service is currently showing, as published
by the display process (stale after 120 seconds).

**Response**:
```json
{
  "status": "success",
  "data": {
    "mode": "nfl_live",
    "plugin_id": "football-scoreboard",
    "last_updated": 1234567890.123
  }
}
```

When nothing has been published, every field is `null`.

### List Display Modes

**GET** `/api/v3/display/modes`

Every display mode that can be requested on-demand, with the plugin that owns
it. This is the list the force-display dialog offers.

Send the reported `plugin_id` alongside `mode` when starting an on-demand
display: `/display/on-demand/start` falls back to `find_plugin_for_mode` when
`plugin_id` is omitted, and that lookup only sees modes declared in a static
manifest — a plugin whose modes are generated (each installed Starlark app is
one) returns 404 there.

Triggers plugin discovery, which is otherwise lazy — so a caller that never
opens the dashboard still gets the full list.

**Query Parameters**:
- `include_disabled` (optional): `1` to include modes belonging to disabled
  plugins. They are still valid on-demand targets — the controller enables the
  plugin for the duration of the request — and are reported with
  `"enabled": false`.

**Response**:
```json
{
  "status": "success",
  "data": {
    "modes": [
      {
        "mode": "nfl_live",
        "plugin_id": "football-scoreboard",
        "plugin_name": "Football Scoreboard",
        "name": "nfl_live",
        "enabled": true
      },
      {
        "mode": "clock-simple",
        "plugin_id": "clock-simple",
        "plugin_name": "Simple Clock",
        "name": "Simple Clock",
        "enabled": true
      }
    ]
  }
}
```

`name` is a label for a dropdown: a single-mode plugin's own name, or the raw
mode string for a multi-mode plugin, since there is no per-mode name anywhere.

### On-Demand Display Status

**GET** `/api/v3/display/on-demand/status`

Get the current on-demand display state.

**Response**:
```json
{
  "status": "success",
  "data": {
    "state": {
      "active": true,
      "plugin_id": "football-scoreboard",
      "mode": "nfl_live",
      "duration": 45,
      "pinned": true,
      "status": "running",
      "last_updated": 1234567890.123
    },
    "service": {
      "active": true,
      "returncode": 0,
      "stdout": "active",
      "stderr": ""
    }
  }
}
```

With no on-demand request, `state` is
`{"active": false, "status": "idle", "last_updated": null}`.

### Start On-Demand Display

**POST** `/api/v3/display/on-demand/start`

Request a specific plugin to display on-demand.

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard",
  "mode": "nfl_live",
  "duration": 45,
  "pinned": true,
  "start_service": true
}
```

**Parameters** (at least one of `plugin_id` and `mode` is required):
- `plugin_id` (string, optional): Plugin identifier
- `mode` (string, optional): Display mode name (plugin_id inferred if not provided)
- `duration` (number, optional): Duration in seconds (0 = until stopped)
- `pinned` (boolean, optional): Pin display (pause rotation)
- `start_service` (boolean, optional): (Re)start the display service so it picks the request up (default: true)

**Response**:
```json
{
  "status": "success",
  "data": {
    "request_id": "uuid-here",
    "plugin_id": "football-scoreboard",
    "mode": "nfl_live",
    "duration": 45,
    "pinned": true,
    "service": { "active": true, "returncode": 0, "stdout": "", "stderr": "" }
  }
}
```

`service` is `null` when `start_service` is false.

### Stop On-Demand Display

**POST** `/api/v3/display/on-demand/stop`

Stop the current on-demand display.

**Request Body**:
```json
{
  "stop_service": false
}
```

**Parameters**:
- `stop_service` (boolean, optional): Also stop the display service (default: false)

**Response**:
```json
{
  "status": "success",
  "data": {
    "request_id": "uuid-here",
    "service": null
  }
}
```

---

## Plugins

### Get Installed Plugins

**GET** `/api/v3/plugins/installed`

List all installed plugins with their status and metadata.

**Response**:
```json
{
  "status": "success",
  "data": {
    "plugins": [
      {
        "id": "football-scoreboard",
        "name": "Football Scoreboard",
        "version": "1.2.3",
        "latest_version": "1.2.4",
        "update_available": true,
        "author": "ChuckBuilds",
        "category": "Sports",
        "description": "NFL and NCAA Football scores",
        "tags": ["sports", "football", "nfl"],
        "enabled": true,
        "verified": true,
        "loaded": true,
        "state": "loaded",
        "error_info": null,
        "last_updated": "2025-01-15T10:30:00Z",
        "last_commit": "abc1234",
        "last_commit_message": "feat: Add live game updates",
        "branch": "main",
        "web_ui_actions": [],
        "vegas_mode": null,
        "vegas_content_type": null
      }
    ]
  }
}
```

### Get Plugin Configuration

**GET** `/api/v3/plugins/config?plugin_id=<plugin_id>`

Get a plugin's configuration, with schema defaults filled in for keys that
are not stored. `data` is the configuration object itself.

**Query Parameters**:
- `plugin_id` (required): Plugin identifier

**Response**:
```json
{
  "status": "success",
  "data": {
    "enabled": true,
    "display_duration": 30,
    "favorite_teams": ["TB", "DAL"]
  }
}
```

### Save Plugin Configuration

**POST** `/api/v3/plugins/config`

Update a plugin's configuration. With a JSON body, the keys in `config` are
merged onto the plugin's stored configuration: keys you do not send keep
their stored values. Fields the schema marks `"x-secret": true` are written
to `config/config_secrets.json` instead of `config.json`. The web UI posts
form data instead (`?plugin_id=` in the query string, fields as form fields).

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard",
  "config": {
    "display_duration": 30,
    "favorite_teams": ["TB", "DAL"]
  }
}
```

**Response**:
```json
{
  "status": "success",
  "message": "Plugin football-scoreboard configuration saved successfully"
}
```

A config that fails schema validation is rejected with `400` and nothing is
saved.

### Get Plugin Schema

**GET** `/api/v3/plugins/schema?plugin_id=<plugin_id>`

Get the JSON schema for a plugin's configuration. A plugin without a
`config_schema.json` gets a minimal default schema.

**Query Parameters**:
- `plugin_id` (required): Plugin identifier

**Response**:
```json
{
  "status": "success",
  "data": {
    "schema": {
      "type": "object",
      "properties": {
        "enabled": {
          "type": "boolean",
          "default": true
        },
        "display_duration": {
          "type": "number",
          "minimum": 1,
          "maximum": 300
        }
      }
    }
  }
}
```

### Reset Plugin Configuration

**POST** `/api/v3/plugins/config/reset`

Reset a plugin's configuration to its schema defaults.

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard",
  "preserve_secrets": true
}
```

**Response**:
```json
{
  "status": "success",
  "message": "Plugin football-scoreboard configuration reset to defaults",
  "data": { "config": { ... } }
}
```

### Toggle Plugin

**POST** `/api/v3/plugins/toggle`

Enable or disable a plugin. A `plugin_id` of the form `starlark:<app_id>`
toggles a Starlark app.

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard",
  "enabled": true
}
```

**Response**:
```json
{
  "status": "success",
  "message": "Plugin football-scoreboard enabled successfully"
}
```

### Install Plugin

**POST** `/api/v3/plugins/install`

Install a plugin from the plugin store.

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard",
  "branch": "main"
}
```

`branch` is optional.

**Response** (queued; poll `/plugins/operation/<operation_id>`):
```json
{
  "status": "success",
  "data": { "operation_id": "uuid-here" },
  "message": "Plugin football-scoreboard installation queued"
}
```

When the operation queue is unavailable the install runs synchronously and
the response has only a `message`.

### Uninstall Plugin

**POST** `/api/v3/plugins/uninstall`

Remove an installed plugin.

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard",
  "preserve_config": false
}
```

**Response** (queued):
```json
{
  "status": "success",
  "data": { "operation_id": "uuid-here" },
  "message": "Plugin uninstallation queued"
}
```

### Update Plugin

**POST** `/api/v3/plugins/update`

Update a plugin to the latest version. Runs synchronously.

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard"
}
```

**Response**:
```json
{
  "status": "success",
  "message": "Plugin football-scoreboard updated ...",
  "data": {
    "last_updated": "2025-01-15T10:30:00Z",
    "commit": "abc1234..."
  }
}
```

### Install Plugin from URL

**POST** `/api/v3/plugins/install-from-url`

Install a plugin directly from a GitHub repository URL. Runs synchronously.

**Request Body**:
```json
{
  "repo_url": "https://github.com/user/ledmatrix-my-plugin",
  "branch": "main",
  "plugin_path": null
}
```

**Parameters**:
- `repo_url` (required): GitHub repository URL
- `branch` (optional): Branch name (default: `main`, then `master`)
- `plugin_path` (optional): Path within the repository, for monorepo plugins
- `plugin_id` (optional): Plugin id, for monorepo installations

**Response** (fields at the top level):
```json
{
  "status": "success",
  "message": "Plugin my-plugin installed successfully",
  "plugin_id": "my-plugin",
  "name": "My Plugin",
  "branch": "main"
}
```

### Load Registry from URL

**POST** `/api/v3/plugins/registry-from-url`

Load a `plugins.json` registry from a GitHub repository URL.

**Request Body**:
```json
{
  "repo_url": "https://github.com/user/ledmatrix-plugins"
}
```

**Response** (fields at the top level):
```json
{
  "status": "success",
  "plugins": [
    {
      "id": "plugin-1",
      "name": "Plugin One",
      "description": "..."
    }
  ],
  "registry_url": "https://github.com/user/ledmatrix-plugins"
}
```

### Get Plugin Health

**GET** `/api/v3/plugins/health`

Get health state for all installed plugins, keyed by plugin id.

**Response**:
```json
{
  "status": "success",
  "data": {
    "football-scoreboard": {
      "plugin_id": "football-scoreboard",
      "circuit_state": "closed",
      "consecutive_failures": 0,
      "total_failures": 2,
      "total_successes": 1500,
      "success_rate": 99.87,
      "last_success_time": 1234567890.123,
      "last_failure_time": 1234560000.0,
      "last_error": null,
      "is_healthy": true,
      "degraded": false,
      "degraded_reason": null,
      "circuit_opened_time": null,
      "half_open_start_time": null
    }
  }
}
```

### Get Plugin Health (Single)

**GET** `/api/v3/plugins/health/<plugin_id>`

Health state for one plugin; `data` has the same fields as one entry above.
Answers `503` when health tracking is unavailable.

### Reset Plugin Health

**POST** `/api/v3/plugins/health/<plugin_id>/reset`

Reset health state for a plugin (manual recovery).

**Response**:
```json
{
  "status": "success",
  "message": "Health state reset for plugin football-scoreboard"
}
```

### Get Plugin Metrics

**GET** `/api/v3/plugins/metrics`

Get resource usage metrics for all installed plugins, keyed by plugin id.

**Response**:
```json
{
  "status": "success",
  "data": {
    "football-scoreboard": {
      "plugin_id": "football-scoreboard",
      "memory_mb": 24.5,
      "cpu_percent": 3.2,
      "execution_time": 0.12,
      "avg_execution_time": 0.1,
      "min_execution_time": 0.05,
      "max_execution_time": 0.9,
      "call_count": 500,
      "last_update_time": 1234567890.123,
      "limits": {
        "max_memory_mb": 50,
        "max_cpu_percent": 50,
        "max_execution_time": 5.0,
        "warning_threshold": 0.8
      }
    }
  }
}
```

`limits` (and usage percentages derived from it) appear only when limits
are configured for the plugin.

### Get Plugin Metrics (Single)

**GET** `/api/v3/plugins/metrics/<plugin_id>`

Metrics for one plugin; `data` has the same fields as one entry above.

### Reset Plugin Metrics

**POST** `/api/v3/plugins/metrics/<plugin_id>/reset`

Reset metrics for a plugin.

### Get/Set Plugin Limits

**GET** `/api/v3/plugins/limits/<plugin_id>`

Get a plugin's resource limits. `data` is `null` when none are configured.

**Response**:
```json
{
  "status": "success",
  "data": {
    "max_memory_mb": 50,
    "max_cpu_percent": 50,
    "max_execution_time": 5.0,
    "warning_threshold": 0.8
  }
}
```

**POST** `/api/v3/plugins/limits/<plugin_id>`

Set a plugin's resource limits. The body replaces all four limits: a key you
omit is stored as no limit (`warning_threshold` defaults to `0.8`).

**Request Body**:
```json
{
  "max_memory_mb": 50,
  "max_cpu_percent": 50,
  "max_execution_time": 5.0,
  "warning_threshold": 0.8
}
```

### Get Plugin State

**GET** `/api/v3/plugins/state`

Get the state manager's record for every plugin, keyed by plugin id. Pass
`?plugin_id=<id>` for one plugin (`data` is then that record).

**Response**:
```json
{
  "status": "success",
  "data": {
    "football-scoreboard": {
      "plugin_id": "football-scoreboard",
      "status": "loaded",
      "enabled": true,
      "version": "1.2.3",
      "installed_at": "2025-01-15T10:30:00",
      "last_updated": "2025-01-15T10:30:00",
      "config_version": 1,
      "metadata": {}
    }
  }
}
```

### Reconcile Plugin State

**POST** `/api/v3/plugins/state/reconcile`

Reconcile plugin state across config, disk and the state manager.

**Request Body** (optional):
```json
{
  "force": false
}
```

**Response**:
```json
{
  "status": "success",
  "message": "...",
  "data": {
    "inconsistencies_found": 1,
    "inconsistencies_fixed": 1,
    "inconsistencies_manual": 0,
    "inconsistencies": [
      {"plugin_id": "...", "type": "...", "description": "...", "fix_action": "..."}
    ],
    "fixed": [ ... ],
    "manual_fix_required": [ ... ]
  }
}
```

### Get Reconciliation Status

**GET** `/api/v3/plugins/reconciliation-status`

Result of the last startup reconciliation, as written by the display service.

**Response**:
```json
{
  "status": "success",
  "data": {
    "done": true,
    "unresolved": []
  }
}
```

Before a run has finished, `data` is `{"done": false, "unresolved": []}`.

### Get Plugin Operation

**GET** `/api/v3/plugins/operation/<operation_id>`

Get status of a queued plugin operation (install, uninstall).

**Response**:
```json
{
  "status": "success",
  "data": {
    "operation_id": "uuid-here",
    "operation_type": "install",
    "plugin_id": "football-scoreboard",
    "parameters": {},
    "status": "completed",
    "progress": 100,
    "message": "Installation completed successfully",
    "error": null,
    "result": { ... },
    "created_at": "2025-01-15T10:30:00",
    "started_at": "2025-01-15T10:30:01",
    "completed_at": "2025-01-15T10:30:20"
  }
}
```

### Get Operation History

**GET** `/api/v3/plugins/operation/history?limit=50`

Get the plugin operation audit log. `data` is a list.

**Query Parameters**:
- `limit` (optional): Maximum number of records (default: 50)
- `plugin_id` (optional): Only records for this plugin
- `operation_type` (optional): Only records of this type (`install`, `update`, `enable`, ...)

**Response**:
```json
{
  "status": "success",
  "data": [
    {
      "operation_id": "uuid-here",
      "operation_type": "install",
      "plugin_id": "football-scoreboard",
      "timestamp": "2025-01-15T10:30:00",
      "status": "success",
      "user": null,
      "details": null,
      "error": null
    }
  ]
}
```

### Clear Operation History

**DELETE** `/api/v3/plugins/operation/history`

Clear the operation audit log.

### Execute Plugin Action

**POST** `/api/v3/plugins/action`

Execute an action declared in the plugin manifest's `web_ui_actions`. See
[PLUGIN_WEB_UI_ACTIONS.md](PLUGIN_WEB_UI_ACTIONS.md).

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard",
  "action_id": "refresh_games",
  "params": {}
}
```

**Response** (fields at the top level; a script that prints JSON can return
its own object instead):
```json
{
  "status": "success",
  "message": "Action completed successfully",
  "output": "script stdout"
}
```

### Upload Plugin Assets

**POST** `/api/v3/plugins/assets/upload`

Upload images for a plugin. Stored under
`assets/plugins/<plugin_id>/uploads/`.

**Request**: Multipart form data
- `plugin_id` (required): Plugin identifier
- `files` (required, repeatable, up to 10): PNG, JPEG, BMP or GIF images, 5 MB each, 50 MB total per plugin

**Response** (fields at the top level):
```json
{
  "status": "success",
  "uploaded_files": [
    {
      "id": "uuid-here",
      "filename": "image_1700000000_abcd1234.png",
      "path": "assets/plugins/football-scoreboard/uploads/image_1700000000_abcd1234.png",
      "size": 1024,
      "uploaded_at": "2025-01-15T10:30:00Z"
    }
  ],
  "total_files": 3
}
```

### Delete Plugin Asset

**POST** `/api/v3/plugins/assets/delete`

Delete an uploaded plugin image by its id (the `id` from upload or list).

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard",
  "image_id": "uuid-here"
}
```

### List Plugin Assets

**GET** `/api/v3/plugins/assets/list?plugin_id=<plugin_id>`

List uploaded images for a plugin.

**Query Parameters**:
- `plugin_id` (required): Plugin identifier

**Response**:
```json
{
  "status": "success",
  "data": {
    "assets": [
      {
        "id": "uuid-here",
        "filename": "image_1700000000_abcd1234.png",
        "path": "assets/plugins/football-scoreboard/uploads/image_1700000000_abcd1234.png",
        "size": 1024,
        "uploaded_at": "2025-01-15T10:30:00Z",
        "original_filename": "logo.png"
      }
    ]
  }
}
```

### Upload Calendar Credentials

**POST** `/api/v3/plugins/calendar/upload-credentials`

Upload the Google OAuth client file for the calendar plugin.

**Request**: Multipart form data
- `file` (required): `credentials.json` (JSON, max 1 MB)

### Authenticate Calendar

**POST** `/api/v3/plugins/calendar/authenticate`

Google OAuth for the calendar plugin, in two steps. Step 1 (no body) returns
the consent URL. Step 2 posts the URL Google redirected to (it fails to load
in the browser, but its address carries the authorization code):

```json
{
  "redirect_url": "http://localhost/?code=..."
}
```

Requires `credentials.json` to have been uploaded first (`400` otherwise).

---

## Plugin Store

### List / Search Store Plugins

**GET** `/api/v3/plugins/store/list`

List plugins from the registry and saved repositories. The same endpoint
searches.

**Query Parameters**:
- `query` (optional): Text search over name, description, id
- `category` (optional): Category filter
- `tags` (optional, repeatable): Tag filter
- `fetch_commit_info` (optional): `false` to skip fetching commit metadata from GitHub (default: fetched)

**Response**:
```json
{
  "status": "success",
  "data": {
    "plugins": [
      {
        "id": "football-scoreboard",
        "name": "Football Scoreboard",
        "author": "ChuckBuilds",
        "category": "Sports",
        "description": "NFL and NCAA Football scores",
        "tags": ["sports"],
        "stars": 0,
        "verified": true,
        "repo": "https://github.com/ChuckBuilds/ledmatrix-plugins",
        "last_updated": "2025-01-15",
        "last_updated_iso": "2025-01-15T10:30:00Z",
        "last_commit": "abc1234",
        "last_commit_message": "...",
        "last_commit_author": "...",
        "version": "1.2.3",
        "branch": "main",
        "default_branch": "main",
        "plugin_path": "plugins/football-scoreboard"
      }
    ]
  }
}
```

### Get GitHub Status

**GET** `/api/v3/plugins/store/github-status`

Whether a GitHub token is configured and valid.

**Response**:
```json
{
  "status": "success",
  "data": {
    "token_status": "valid",
    "authenticated": true,
    "rate_limit": 5000,
    "message": "GitHub API authenticated",
    "error": null
  }
}
```

`token_status` is `none`, `valid` or `invalid`; `rate_limit` is the nominal
hourly limit (60 unauthenticated), not a live count.

### Refresh Plugin Store

**POST** `/api/v3/plugins/store/refresh`

Force refresh of the registry cache.

**Response**:
```json
{
  "status": "success",
  "message": "Plugin store refreshed",
  "plugin_count": 42
}
```

### Get Saved Repositories

**GET** `/api/v3/plugins/saved-repositories`

Get the list of saved custom plugin repositories.

**Response**:
```json
{
  "status": "success",
  "data": {
    "repositories": [
      {
        "url": "https://github.com/user/ledmatrix-plugins",
        "name": "ledmatrix-plugins",
        "type": "registry"
      }
    ]
  }
}
```

### Save Repository

**POST** `/api/v3/plugins/saved-repositories`

Save a custom plugin repository. Returns the updated list in
`data.repositories`.

**Request Body**:
```json
{
  "repo_url": "https://github.com/user/ledmatrix-plugins",
  "name": "Custom Plugins"
}
```

### Delete Saved Repository

**DELETE** `/api/v3/plugins/saved-repositories`

Remove a saved repository. Returns the updated list in `data.repositories`.

**Request Body**:
```json
{
  "repo_url": "https://github.com/user/ledmatrix-plugins"
}
```

---

## System

### Get System Status

**GET** `/api/v3/system/status`

Get system status and metrics (cached for 10 seconds).

**Response**:
```json
{
  "status": "success",
  "data": {
    "timestamp": 1234567890.123,
    "uptime": "3d 4h",
    "uptime_seconds": 273600,
    "service_active": true,
    "cpu_percent": 25.5,
    "memory_used_percent": 45.2,
    "memory_total_mb": 3794.0,
    "memory_used_mb": 1715.0,
    "memory_available_mb": 1900.0,
    "cpu_temp": 45.0,
    "disk_used_percent": 60.0,
    "disk_total_gb": 29.0,
    "disk_used_gb": 17.4
  }
}
```

### Get System Version

**GET** `/api/v3/system/version`

Get LEDMatrix repository version.

**Response**:
```json
{
  "status": "success",
  "data": {
    "version": "v2.4-10-g1234567"
  }
}
```

### Check for Update

**GET** `/api/v3/system/check-update`

Whether `origin/main` has commits the checkout lacks. Cached briefly.
Fields at the top level (no envelope):

```json
{
  "update_available": true,
  "remote_sha": "abc123...",
  "commits_behind": 3
}
```

When git cannot run the check, the response also carries
`"check_failed": true` and an `error` explaining why.

### Automatic Update Status

**GET** `/api/v3/system/auto-update`

Weekly automatic-update status for the General tab and the Overview banner:
`last_run`, `summary`, `status`, `next_due`, `alert`, `alert_id`,
`verifier_installed`, `setup_status`, `setup_message`, `verifying` (in
`data`).

**POST** `/api/v3/system/auto-update/dismiss`

Hide the current automatic-update alert until a new one replaces it.

```json
{
  "alert_id": "..."
}
```

### Git Info

**GET** `/api/v3/system/git-info`

Branch, dirty state, recent commits and remote for the Tools tab. Fields at
the top level: `branch`, `dirty`, `status`, `recent_commits`, `remote_url`
(credentials scrubbed), `upstream`, `can_pull`.

### Git Branches

**GET** `/api/v3/system/git-branches`

Fetches `origin` and lists branches to switch to: `current`, `upstream`,
`local` (list), `remote_only` (list), at the top level.

### Execute System Action

**POST** `/api/v3/system/action`

Execute system-level actions. JSON or form data.

**Request Body**:
```json
{
  "action": "restart_display_service"
}
```

**Available Actions**:
- `start_display`: Start the display service
- `stop_display`: Stop the display service
- `restart_display_service`: Restart the display service
- `restart_web_service`: Restart the web interface service
- `enable_autostart`: Enable display service autostart
- `disable_autostart`: Disable display service autostart
- `reboot_system`: Reboot the Raspberry Pi
- `shutdown_system`: Power off the Raspberry Pi
- `git_pull`: Update LEDMatrix from git (the Update button)
- `checkout_branch`: Switch branch; takes `branch` and optional `stash`
- `force_git_reset`: `git reset --hard origin/main`
- `install_base_requirements`: pip install `requirements.txt` and `web_interface/requirements.txt`
- `install_plugin_requirements`: pip install every plugin's `requirements.txt`
- `clear_pycache`: Delete `__pycache__` directories

**Response** (service actions):
```json
{
  "status": "success",
  "message": "Action completed"
}
```

A failed service action returns `"status": "error"` with `returncode` and
`stderr`. `git_pull` returns `message`, `restart_required` and
`dependency_failures`; the install actions return `output` or `details`.

---

## Backup and Restore

Backups are ZIP files kept in the backup export directory.

### Preview

**GET** `/api/v3/backup/preview`

Summary of what a new backup would include.

### List

**GET** `/api/v3/backup/list`

Stored backups, newest first. `data` is a list of
`{"filename", "size", "created_at"}`.

### Export

**POST** `/api/v3/backup/export`

Create a backup. Returns `{"status": "success", "filename": "..."}`.

### Validate

**POST** `/api/v3/backup/validate`

Check an uploaded backup and return its manifest in `data`.

**Request**: Multipart form data
- `backup_file` (required): the ZIP

### Restore

**POST** `/api/v3/backup/restore`

Restore an uploaded backup.

**Request**: Multipart form data
- `backup_file` (required): the ZIP
- `options` (optional): JSON object; keys `restore_config`, `restore_secrets`,
  `restore_wifi`, `restore_fonts`, `restore_plugin_uploads`,
  `reinstall_plugins` (each defaults to `true`; unknown keys are rejected)

A partial restore answers `500` with `"status": "error"`, a message listing
what did and didn't restore, and the result in `data`.

### Download

**GET** `/api/v3/backup/download/<filename>`

Download a stored backup.

### Delete

**DELETE** `/api/v3/backup/<filename>`

Delete a stored backup.

---

## Fonts

### Get Font Catalog

**GET** `/api/v3/fonts/catalog`

Fonts in `assets/fonts/`, keyed by file name without extension.

**Response**:
```json
{
  "status": "success",
  "data": {
    "catalog": {
      "press_start": {
        "filename": "press_start.ttf",
        "family_name": "Press Start 2P",
        "display_name": "Press Start 2P",
        "path": "assets/fonts/press_start.ttf",
        "type": "ttf",
        "is_system": true,
        "scalable": true,
        "native_size": null,
        "metadata": { ... }
      }
    }
  }
}
```

### Get Font Tokens

**GET** `/api/v3/fonts/tokens`

Get font size token definitions.

**Response**:
```json
{
  "status": "success",
  "data": {
    "tokens": {
      "xs": 6,
      "sm": 8,
      "md": 10,
      "lg": 12,
      "xl": 14,
      "xxl": 16
    }
  }
}
```

### Upload Font

**POST** `/api/v3/fonts/upload`

Upload a custom font file. It is saved as `assets/fonts/<font_family><ext>`.

**Request**: Multipart form data
- `font_file` (required): `.ttf`, `.otf` or `.bdf`, max 10 MB
- `font_family` (required): name for the font (letters, numbers, `_`, `-`)

**Response** (fields at the top level):
```json
{
  "status": "success",
  "message": "Font custom_font uploaded successfully",
  "font_family": "custom_font",
  "filename": "custom_font.ttf",
  "path": "assets/fonts/custom_font.ttf"
}
```

### Delete Font

**DELETE** `/api/v3/fonts/<font_family>`

Delete an uploaded font (`<font_family>` is the file name without
extension). System fonts answer `403`.

### Font Preview

**GET** `/api/v3/fonts/preview?font=<filename>&text=<sample>&size=12`

Render text in a font, for the web UI font picker. BDF fonts are not
previewed (`400`).

**Query Parameters**:
- `font` (required): font file name in `assets/fonts/` (e.g. `press_start.ttf`)
- `text` (optional): up to 100 characters (default `Sample Text 123`)
- `size` (optional): 4-72 (default 12)
- `bg`, `fg` (optional): hex colours without `#` (default `000000` / `ffffff`)

**Response**:
```json
{
  "status": "success",
  "data": {
    "image": "data:image/png;base64,...",
    "width": 140,
    "height": 32
  }
}
```

> Font overrides (`/api/v3/fonts/overrides`) were removed. Per-plugin font
> choices are made in each plugin's own settings.

---

## Cache

### List Cache Entries

**GET** `/api/v3/cache/list`

List cache files.

**Response**:
```json
{
  "status": "success",
  "data": {
    "cache_files": [ ... ],
    "cache_dir": "/var/cache/ledmatrix",
    "total_files": 12
  }
}
```

### Delete Cache Entry

**POST** `/api/v3/cache/delete`

Delete one cache entry by key. There is no clear-all option here; use
`scripts/utils/clear_cache.py --clear-all` on the Pi for that.

**Request Body**:
```json
{
  "key": "weather_current_12345"
}
```

---

## WiFi

### Get WiFi Status

**GET** `/api/v3/wifi/status`

Get current WiFi connection status.

**Response**:
```json
{
  "status": "success",
  "data": {
    "connected": true,
    "ssid": "MyNetwork",
    "ip_address": "192.168.1.100",
    "signal": 70,
    "ap_mode_active": false,
    "auto_enable_ap_mode": true,
    "last_connect_attempt": null
  }
}
```

### Scan WiFi Networks

**GET** `/api/v3/wifi/scan`

Scan for available WiFi networks. `data` is a list. If AP mode is active it is
turned off for the scan and back on afterwards, and `message` says so.

**Response**:
```json
{
  "status": "success",
  "data": [
    {
      "ssid": "MyNetwork",
      "signal": 70,
      "security": "WPA2",
      "frequency": 2437
    }
  ]
}
```

### Connect to WiFi

**POST** `/api/v3/wifi/connect`

Connect to a WiFi network.

**Request Body**:
```json
{
  "ssid": "MyNetwork",
  "password": "mypassword"
}
```

**Response**: `"status": "success"` when connected; `"status": "pending"`
(with `data.ssid`) when the connection continues in the background — poll
`/wifi/status` and read `last_connect_attempt`. A wrong password answers
`400` with `"error_type": "wrong_password"`.

### Disconnect from WiFi

**POST** `/api/v3/wifi/disconnect`

Disconnect from current WiFi network.

### Enable Access Point Mode

**POST** `/api/v3/wifi/ap/enable`

Enable WiFi access point mode. Optional body `{"force": true}`.

### Disable Access Point Mode

**POST** `/api/v3/wifi/ap/disable`

Disable WiFi access point mode.

### Get Auto-Enable AP Setting

**GET** `/api/v3/wifi/ap/auto-enable`

**Response**:
```json
{
  "status": "success",
  "data": {
    "auto_enable_ap_mode": true
  }
}
```

### Set Auto-Enable AP

**POST** `/api/v3/wifi/ap/auto-enable`

**Request Body**:
```json
{
  "auto_enable_ap_mode": true
}
```

### WiFi Radio

**GET** `/api/v3/wifi/radio`

Radio state: `data.enabled` (`null` if unknown), `data.ethernet_connected`,
`data.available`.

**POST** `/api/v3/wifi/radio`

Turn the WiFi radio on or off. Turning it off is refused unless Ethernet is
connected or `force` is true, so you don't cut off your own connection.

```json
{
  "enabled": false,
  "force": false
}
```

---

## Streams

Server-Sent Events, defined in `web_interface/app.py`. Each event is one
`data: <json>` line; idle connections get `: heartbeat` comments.

### System Statistics Stream

**GET** `/api/v3/stream/stats`

```
data: {"timestamp": 1234567890.1, "uptime": "Running", "service_active": true, "cpu_percent": 25.5, "memory_used_percent": 45.2, "memory_available_mb": 1900.0, "cpu_temp": 45.0, "disk_used_percent": 60.0, "power": {...}}
```

### Display Preview Stream

**GET** `/api/v3/stream/display`

```
data: {"timestamp": 1234567890.123, "width": 128, "height": 32, "image": "base64_data_here"}
```

### Service Logs Stream

**GET** `/api/v3/stream/logs`

Each event carries the latest journal lines for `ledmatrix` and
`ledmatrix-web` as one text block:

```
data: {"timestamp": 1234567890.123, "logs": "2025-01-15T10:30:00+0000 host python[123]: ..."}
```

---

## Logs

### Get Logs

**GET** `/api/v3/logs`

The last 100 journal lines for `ledmatrix.service` and
`ledmatrix-web.service`, as one text block. Takes no parameters.

**Response**:
```json
{
  "status": "success",
  "data": {
    "logs": "2025-01-15T10:30:00+0000 host python[123]: Plugin loaded: football-scoreboard\n..."
  }
}
```

---

## Error tracking

Plugin errors are recorded by the display service (`ledmatrix.service`),
which runs the plugins. It publishes a snapshot to the shared cache directory
(`plugin_error_snapshot`) at most every 10 seconds, and only when something
changed, so these endpoints lag the display by up to about 15 seconds. The
counts cover the display service's current run: they start at zero when it
restarts. Error messages and stack traces have credentials redacted, and
messages, traces and context values are truncated in the snapshot.

Every response below adds three fields to the shape it always had:

| Field | Meaning |
|---|---|
| `snapshot_available` | `false` until the display service has reported (for example, it is not running). Counts are then zero. |
| `generated_at` | When the display service produced the snapshot (ISO, the Pi's local time), or `null`. |
| `clear_pending` | A clear has been requested and the display service has not applied it yet. |

### Get Error Summary

**GET** `/api/v3/errors/summary`

Aggregated counts, detected patterns and recent errors (the last 20).

```json
{
  "status": "success",
  "data": {
    "session_start": "2026-09-23T09:40:02.118000",
    "total_errors": 13,
    "error_rate_per_hour": 41.2,
    "error_counts_by_type": {"ConnectionError": 12, "ValueError": 1},
    "plugin_error_counts": {"weather": {"ConnectionError": 12}, "stocks": {"ValueError": 1}},
    "active_patterns": {
      "ConnectionError": {
        "error_type": "ConnectionError", "count": 12,
        "first_seen": "2026-09-23T09:41:10.500000", "last_seen": "2026-09-23T09:58:36.020000",
        "affected_plugins": ["weather"], "sample_messages": ["Read timed out."],
        "severity": "error"
      }
    },
    "recent_errors": [
      {"error_type": "ValueError", "message": "could not parse price",
       "timestamp": "2026-09-23T09:58:36.100000", "context": {},
       "plugin_id": "stocks", "operation": "update", "stack_trace": "Traceback ..."}
    ],
    "generated_at": "2026-09-23T09:58:40.000000",
    "snapshot_available": true,
    "clear_pending": false
  },
  "message": "Error summary retrieved"
}
```

### Get Plugin Errors

**GET** `/api/v3/errors/plugin/<plugin_id>`

Error health and statistics for one plugin: `plugin_id`, `status`
(`healthy`, `degraded` or `unhealthy`), `total_errors`, `error_types`,
`recent_error_count`, `last_error` (a `recent_errors` entry or `null`), plus
the three fields above. A plugin with no recorded errors is `healthy`.

### Clear Errors

**POST** `/api/v3/errors/clear`

Clear error records older than `max_age_hours` (default 24, 1-8760), or every
error with `"all": true` (`max_age_hours` is then ignored).

```json
{
  "max_age_hours": 24
}
```

The clear is asynchronous. The web interface records a request
(`plugin_error_clear_request` in the shared cache), and the display service
applies it within about 5 seconds, rebuilding its counts from the errors it
keeps and republishing. Reads hide the cleared errors from the moment the
request is recorded. Until the display service applies an age-based clear,
`recent_errors` and `active_patterns` are already filtered but the counts
are the old ones, and `clear_pending` is `true`.

```json
{
  "status": "success",
  "data": {
    "cleared_count": 13,
    "clear_requested": true,
    "request_id": "5f0c1e...",
    "cutoff": "2026-09-23T09:59:02.310000"
  },
  "message": "Clear of all errors requested; the display service applies it within about 5 seconds"
}
```

`cleared_count` is how many of the reported errors the clear hides. It is
`null` when that cannot be known before the display service applies it (an
age-based clear over more errors than the report lists). A request that
could not be written to the shared cache answers `500`.

---

## Health and Status

### Health Check

**GET** `/api/v3/health`

Health of the web interface, display service, config file, plugin system and
display snapshot. `data.status` is `healthy` or `degraded`, with
`data.services` and `data.checks`.

### Hardware Status

**GET** `/api/v3/hardware/status`

LED matrix initialization result written by the display service at startup.
Before the service has written it, `data` is
`{"ok": null, "error": "Display service not yet started"}`.

### Sync Status

**GET** `/api/v3/sync/status`

Live multi-display sync status from the display process; before it has
written one, `data` is `{"role", "port", "state": "starting"}` from config.

---

## Schedule (dim/power)

### Get Dim Schedule

**GET** `/api/v3/config/dim-schedule`

Read the schedule that lowers brightness at configured times.

**Response**:
```json
{
  "status": "success",
  "data": {
    "enabled": true,
    "dim_brightness": 30,
    "mode": "per-day",
    "days": {
      "monday": { "enabled": true, "start_time": "20:00", "end_time": "07:00" },
      "tuesday": { ... }
    }
  }
}
```

In `global` mode, `start_time` and `end_time` sit at the top level instead
of `days`.

### Update Dim Schedule

**POST** `/api/v3/config/dim-schedule`

Replace the dim schedule. `dim_brightness` is 0-100 (default 30). In
`per-day` mode the days can be sent either as the `days` object that GET
returns, or as the web form's flat fields (`monday_enabled`,
`monday_start`, `monday_end`, ...). A day that is not sent counts as
enabled with default times `20:00`-`07:00`; at least one day must be
enabled.

---

## Integrations

### MQTT Bridge

**GET** `/api/v3/integrations/mqtt-bridge`

Home Assistant MQTT bridge service state and settings: `data.service`,
`data.config_exists`, `data.config_path`, `data.config` (password
omitted), `data.password_set`, `data.env_override_prefix`.

**PUT** `/api/v3/integrations/mqtt-bridge/config`

Write `integrations/mqtt_bridge/bridge_config.json`. Only the keys you send
change. The password is write-only: omit `mqtt_password` to keep it, send a
value to replace it, or send `"clear_password": true`. A password with
`mqtt_tls` off is refused unless `allow_insecure_mqtt` is true. Returns
`data.password_set` and `data.restart_required` (the bridge must be
restarted to pick up changes). See
[integrations/mqtt_bridge/README.md](../integrations/mqtt_bridge/README.md).

---

## Plugin-specific endpoints

A handful of endpoints belong to individual plugins. The music plugin's
Spotify and YouTube Music sign-in and the Of-The-Day data files go through the
plugin's own web UI actions ([Execute Plugin Action](#execute-plugin-action))
rather than dedicated routes.

### Calendar

**GET** `/api/v3/plugins/calendar/list-calendars`

List the calendars on the authenticated Google account. Used by the calendar
plugin's config UI. Returns `calendars` at the top level. The upload and
authenticate endpoints are under [Plugins](#upload-calendar-credentials).

### Plugin Static Assets

**GET** `/api/v3/plugins/<plugin_id>/static/<path:file_path>`

Serve a static file from a plugin's directory. Used internally by the web UI
to render plugin previews and icons.

---

## Starlark Apps

The Starlark plugin lets you run [Tronbyt](https://github.com/tronbyt/apps)
Starlark apps on the matrix. These endpoints expose its UI.

### Status

**GET** `/api/v3/starlark/status`

Returns whether the Pixlet binary is installed and the Starlark plugin
is operational.

### Install Pixlet

**POST** `/api/v3/starlark/install-pixlet`

Download and install the Pixlet binary on the Pi.

### Apps

**GET** `/api/v3/starlark/apps` — list installed Starlark apps
**GET** `/api/v3/starlark/apps/<app_id>` — get app details
**DELETE** `/api/v3/starlark/apps/<app_id>` — uninstall an app
**GET** `/api/v3/starlark/apps/<app_id>/config` — get app config schema
**PUT** `/api/v3/starlark/apps/<app_id>/config` — update app config
**POST** `/api/v3/starlark/apps/<app_id>/render` — render app to a frame
**POST** `/api/v3/starlark/apps/<app_id>/toggle` — enable/disable app (`{"enabled": bool}`; omit to flip)

### Repository (Tronbyt community apps)

**GET** `/api/v3/starlark/repository/categories` — list categories
**GET** `/api/v3/starlark/repository/browse` — every app with metadata (filtering happens client-side; cached for 2 hours)
**POST** `/api/v3/starlark/repository/install` — install an app: `{"app_id": "...", "render_interval": 300, "display_duration": 15}`

### Upload custom app

**POST** `/api/v3/starlark/upload`

Upload a custom Starlark `.star` file as a new app. Multipart fields: `file`
(required, max 5 MB), `name`, `app_id`, `render_interval`,
`display_duration`.

### Editor

A Pixlet editing session for one app. The display is stopped while a session
runs.

**GET** `/api/v3/starlark/editor/apps` — apps the editor can open (`data.apps`, `data.apps_dir`, `data.pixlet_available`)
**GET** `/api/v3/starlark/editor/status` — `data.running`, plus `app_id`, `port`, `pid`, `started_at`, `timeout`, `seconds_remaining`, `host_bound` while running
**POST** `/api/v3/starlark/editor/start` — `{"app_id": "...", "timeout": 1800, "port": 8080}` (`timeout` and `port` optional)
**POST** `/api/v3/starlark/editor/stop` — end the session and restart the display

---

## Skins

**GET** `/api/v3/skins`

Installed scoreboard skins (optional `?plugin_id=` filter). Skins are not
supported by the current scoreboard plugins, so the response carries
`data.supported: false` and a `data.message`; clients must not offer these
as selectable. See [SKIN_SYSTEM.md](SKIN_SYSTEM.md).

---

## Error Responses

Errors use one of two shapes. Most endpoints answer:

```json
{
  "status": "error",
  "message": "Error description",
  "details": "Additional error details (optional)"
}
```

Endpoints built on the structured error helper add a code and category:

```json
{
  "status": "error",
  "error_code": "CONFIG_SAVE_FAILED",
  "error_category": "configuration",
  "message": "Error description",
  "details": "optional",
  "context": { },
  "suggested_fixes": [ ]
}
```

**Common HTTP Status Codes**:
- `200`: Success
- `400`: Bad Request (invalid parameters)
- `403`: Forbidden (e.g. deleting a system font)
- `404`: Not Found (resource doesn't exist)
- `408`: Timed out (plugin actions, auth scripts)
- `500`: Internal Server Error
- `503`: Service Unavailable (feature not available)

---

## See Also

- [Plugin API Reference](PLUGIN_API_REFERENCE.md) - API for plugin developers
- [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md) - Complete plugin development guide
- [Web Interface README](../web_interface/README.md) - Web interface documentation
