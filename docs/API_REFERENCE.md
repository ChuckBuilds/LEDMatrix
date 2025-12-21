# LEDMatrix REST API Reference

Complete reference for all REST API endpoints available in the LEDMatrix web interface.

**Base URL**: `http://your-pi-ip:5000/api/v3`

All endpoints return JSON responses with a standard format:
```json
{
  "status": "success" | "error",
  "data": { ... },
  "message": "Optional message"
}
```

## Table of Contents

- [Configuration](#configuration)
- [Display Control](#display-control)
- [Plugins](#plugins)
- [Plugin Store](#plugin-store)
- [System](#system)
- [Fonts](#fonts)
- [Cache](#cache)
- [WiFi](#wifi)
- [Streams](#streams)

---

## Configuration

### Get Main Configuration

**GET** `/api/v3/config/main`

Retrieve the complete main configuration file.

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

Update the main configuration. Accepts both JSON and form data.

**Request Body** (JSON):
```json
{
  "timezone": "America/New_York",
  "city": "New York",
  "state": "NY",
  "country": "US",
  "web_display_autostart": true,
  "rows": 32,
  "cols": 64,
  "chain_length": 2,
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

### Get Schedule Configuration

**GET** `/api/v3/config/schedule`

Retrieve the current schedule configuration.

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

Update the schedule configuration.

**Request Body** (Global mode):
```json
{
  "enabled": true,
  "mode": "global",
  "start_time": "07:00",
  "end_time": "23:00"
}
```

**Request Body** (Per-day mode):
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

**Response**:
```json
{
  "status": "success",
  "message": "Schedule configuration saved successfully"
}
```

### Get Secrets Configuration

**GET** `/api/v3/config/secrets`

Retrieve the secrets configuration (API keys, tokens, etc.). Secret values are masked for security.

**Response**:
```json
{
  "status": "success",
  "data": {
    "weather": {
      "api_key": "***"
    },
    "spotify": {
      "client_id": "***",
      "client_secret": "***"
    }
  }
}
```

### Save Raw Configuration

**POST** `/api/v3/config/raw/main`

Save raw JSON configuration (advanced use only).

**POST** `/api/v3/config/raw/secrets`

Save raw secrets configuration (advanced use only).

---

## Display Control

### Get Current Display

**GET** `/api/v3/display/current`

Get the current display state and preview image.

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
      "returncode": 0
    }
  }
}
```

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

**Parameters**:
- `plugin_id` (string, optional): Plugin identifier
- `mode` (string, optional): Display mode name (plugin_id inferred if not provided)
- `duration` (number, optional): Duration in seconds (0 = until stopped)
- `pinned` (boolean, optional): Pin display (pause rotation)
- `start_service` (boolean, optional): Auto-start display service if not running (default: true)

**Response**:
```json
{
  "status": "success",
  "data": {
    "request_id": "uuid-here",
    "plugin_id": "football-scoreboard",
    "mode": "nfl_live",
    "active": true
  }
}
```

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
  "message": "On-demand display stopped"
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
        "author": "ChuckBuilds",
        "category": "Sports",
        "description": "NFL and NCAA Football scores",
        "tags": ["sports", "football", "nfl"],
        "enabled": true,
        "verified": true,
        "loaded": true,
        "last_updated": "2025-01-15T10:30:00Z",
        "last_commit": "abc1234",
        "last_commit_message": "feat: Add live game updates",
        "branch": "main",
        "web_ui_actions": []
      }
    ]
  }
}
```

### Get Plugin Configuration

**GET** `/api/v3/plugins/config?plugin_id=<plugin_id>`

Get configuration for a specific plugin.

**Query Parameters**:
- `plugin_id` (required): Plugin identifier

**Response**:
```json
{
  "status": "success",
  "data": {
    "plugin_id": "football-scoreboard",
    "config": {
      "enabled": true,
      "display_duration": 30,
      "favorite_teams": ["TB", "DAL"]
    }
  }
}
```

### Save Plugin Configuration

**POST** `/api/v3/plugins/config`

Update plugin configuration.

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard",
  "config": {
    "enabled": true,
    "display_duration": 30,
    "favorite_teams": ["TB", "DAL"]
  }
}
```

**Response**:
```json
{
  "status": "success",
  "message": "Plugin configuration saved successfully"
}
```

### Get Plugin Schema

**GET** `/api/v3/plugins/schema?plugin_id=<plugin_id>`

Get the JSON schema for a plugin's configuration.

**Query Parameters**:
- `plugin_id` (required): Plugin identifier

**Response**:
```json
{
  "status": "success",
  "data": {
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
```

### Toggle Plugin

**POST** `/api/v3/plugins/toggle`

Enable or disable a plugin.

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
  "message": "Plugin football-scoreboard enabled"
}
```

### Install Plugin

**POST** `/api/v3/plugins/install`

Install a plugin from the plugin store.

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
  "data": {
    "operation_id": "uuid-here",
    "plugin_id": "football-scoreboard",
    "status": "installing"
  }
}
```

### Uninstall Plugin

**POST** `/api/v3/plugins/uninstall`

Remove an installed plugin.

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
  "message": "Plugin football-scoreboard uninstalled"
}
```

### Update Plugin

**POST** `/api/v3/plugins/update`

Update a plugin to the latest version.

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
  "data": {
    "operation_id": "uuid-here",
    "plugin_id": "football-scoreboard",
    "status": "updating"
  }
}
```

### Install Plugin from URL

**POST** `/api/v3/plugins/install-from-url`

Install a plugin directly from a GitHub repository URL.

**Request Body**:
```json
{
  "url": "https://github.com/user/ledmatrix-my-plugin",
  "branch": "main",
  "plugin_path": null
}
```

**Parameters**:
- `url` (required): GitHub repository URL
- `branch` (optional): Branch name (default: "main")
- `plugin_path` (optional): Path within repository for monorepo plugins

**Response**:
```json
{
  "status": "success",
  "data": {
    "operation_id": "uuid-here",
    "plugin_id": "my-plugin",
    "status": "installing"
  }
}
```

### Load Registry from URL

**POST** `/api/v3/plugins/registry-from-url`

Load a plugin registry from a GitHub repository URL.

**Request Body**:
```json
{
  "url": "https://github.com/user/ledmatrix-plugins"
}
```

**Response**:
```json
{
  "status": "success",
  "data": {
    "plugins": [
      {
        "id": "plugin-1",
        "name": "Plugin One",
        "description": "..."
      }
    ]
  }
}
```

### Get Plugin Health

**GET** `/api/v3/plugins/health`

Get health metrics for all plugins.

**Response**:
```json
{
  "status": "success",
  "data": {
    "football-scoreboard": {
      "status": "healthy",
      "last_update": 1234567890.123,
      "error_count": 0,
      "last_error": null
    }
  }
}
```

### Get Plugin Health (Single)

**GET** `/api/v3/plugins/health/<plugin_id>`

Get health metrics for a specific plugin.

**Response**:
```json
{
  "status": "success",
  "data": {
    "status": "healthy",
    "last_update": 1234567890.123,
    "error_count": 0,
    "last_error": null
  }
}
```

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

Get resource usage metrics for all plugins.

**Response**:
```json
{
  "status": "success",
  "data": {
    "football-scoreboard": {
      "update_count": 150,
      "display_count": 500,
      "avg_update_time": 0.5,
      "avg_display_time": 0.1,
      "memory_usage": 1024000
    }
  }
}
```

### Get Plugin Metrics (Single)

**GET** `/api/v3/plugins/metrics/<plugin_id>`

Get resource usage metrics for a specific plugin.

### Reset Plugin Metrics

**POST** `/api/v3/plugins/metrics/<plugin_id>/reset`

Reset metrics for a plugin.

### Get/Set Plugin Limits

**GET** `/api/v3/plugins/limits/<plugin_id>`

Get rate limits and resource limits for a plugin.

**POST** `/api/v3/plugins/limits/<plugin_id>`

Update rate limits and resource limits for a plugin.

**Request Body**:
```json
{
  "max_update_interval": 60,
  "max_display_time": 5.0,
  "max_memory_mb": 50
}
```

### Get Plugin State

**GET** `/api/v3/plugins/state`

Get the current state of all plugins.

**Response**:
```json
{
  "status": "success",
  "data": {
    "football-scoreboard": {
      "state": "loaded",
      "enabled": true,
      "last_update": 1234567890.123
    }
  }
}
```

### Reconcile Plugin State

**POST** `/api/v3/plugins/state/reconcile`

Reconcile plugin state with configuration (fix inconsistencies).

**Response**:
```json
{
  "status": "success",
  "message": "Plugin state reconciled"
}
```

### Get Plugin Operation

**GET** `/api/v3/plugins/operation/<operation_id>`

Get status of an async plugin operation (install, update, etc.).

**Response**:
```json
{
  "status": "success",
  "data": {
    "operation_id": "uuid-here",
    "type": "install",
    "plugin_id": "football-scoreboard",
    "status": "completed",
    "progress": 100,
    "message": "Installation completed successfully"
  }
}
```

### Get Operation History

**GET** `/api/v3/plugins/operation/history?limit=100`

Get history of plugin operations.

**Query Parameters**:
- `limit` (optional): Maximum number of operations to return (default: 100)

**Response**:
```json
{
  "status": "success",
  "data": {
    "operations": [
      {
        "operation_id": "uuid-here",
        "type": "install",
        "plugin_id": "football-scoreboard",
        "status": "completed",
        "timestamp": 1234567890.123
      }
    ]
  }
}
```

### Execute Plugin Action

**POST** `/api/v3/plugins/action`

Execute a custom plugin action (defined in plugin's web_ui_actions).

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard",
  "action": "refresh_games",
  "parameters": {}
}
```

### Reset Plugin Configuration

**POST** `/api/v3/plugins/config/reset`

Reset a plugin's configuration to defaults.

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard"
}
```

### Upload Plugin Assets

**POST** `/api/v3/plugins/assets/upload`

Upload assets (images, files) for a plugin.

**Request**: Multipart form data
- `plugin_id` (required): Plugin identifier
- `file` (required): File to upload
- `asset_type` (optional): Type of asset (logo, image, etc.)

**Response**:
```json
{
  "status": "success",
  "data": {
    "filename": "logo.png",
    "path": "plugins/football-scoreboard/assets/logo.png"
  }
}
```

### Delete Plugin Asset

**POST** `/api/v3/plugins/assets/delete`

Delete a plugin asset.

**Request Body**:
```json
{
  "plugin_id": "football-scoreboard",
  "filename": "logo.png"
}
```

### List Plugin Assets

**GET** `/api/v3/plugins/assets/list?plugin_id=<plugin_id>`

List all assets for a plugin.

**Query Parameters**:
- `plugin_id` (required): Plugin identifier

**Response**:
```json
{
  "status": "success",
  "data": {
    "assets": [
      {
        "filename": "logo.png",
        "path": "plugins/football-scoreboard/assets/logo.png",
        "size": 1024
      }
    ]
  }
}
```

### Authenticate Spotify

**POST** `/api/v3/plugins/authenticate/spotify`

Initiate Spotify authentication flow for music plugin.

**Request Body**:
```json
{
  "plugin_id": "music"
}
```

**Response**:
```json
{
  "status": "success",
  "data": {
    "auth_url": "https://accounts.spotify.com/authorize?..."
  }
}
```

### Authenticate YouTube Music

**POST** `/api/v3/plugins/authenticate/ytm`

Initiate YouTube Music authentication flow.

**Request Body**:
```json
{
  "plugin_id": "music"
}
```

### Upload Calendar Credentials

**POST** `/api/v3/plugins/calendar/upload-credentials`

Upload Google Calendar credentials file.

**Request**: Multipart form data
- `file` (required): credentials.json file

---

## Plugin Store

### List Store Plugins

**GET** `/api/v3/plugins/store/list?fetch_commit_info=true`

Get list of available plugins from the plugin store.

**Query Parameters**:
- `fetch_commit_info` (optional): Include commit information (default: false)

**Response**:
```json
{
  "status": "success",
  "data": {
    "plugins": [
      {
        "id": "football-scoreboard",
        "name": "Football Scoreboard",
        "description": "NFL and NCAA Football scores",
        "author": "ChuckBuilds",
        "category": "Sports",
        "version": "1.2.3",
        "repository_url": "https://github.com/ChuckBuilds/ledmatrix-football-scoreboard",
        "installed": true,
        "update_available": false
      }
    ]
  }
}
```

### Get GitHub Status

**GET** `/api/v3/plugins/store/github-status`

Get GitHub API rate limit status.

**Response**:
```json
{
  "status": "success",
  "data": {
    "rate_limit": 5000,
    "rate_remaining": 4500,
    "rate_reset": 1234567890
  }
}
```

### Refresh Plugin Store

**POST** `/api/v3/plugins/store/refresh`

Force refresh of the plugin store cache.

**Response**:
```json
{
  "status": "success",
  "message": "Plugin store refreshed"
}
```

### Get Saved Repositories

**GET** `/api/v3/plugins/saved-repositories`

Get list of saved custom plugin repositories.

**Response**:
```json
{
  "status": "success",
  "data": {
    "repositories": [
      {
        "url": "https://github.com/user/ledmatrix-plugins",
        "name": "Custom Plugins",
        "auto_load": true
      }
    ]
  }
}
```

### Save Repository

**POST** `/api/v3/plugins/saved-repositories`

Save a custom plugin repository for easy access.

**Request Body**:
```json
{
  "url": "https://github.com/user/ledmatrix-plugins",
  "name": "Custom Plugins",
  "auto_load": true
}
```

### Delete Saved Repository

**DELETE** `/api/v3/plugins/saved-repositories`

Remove a saved repository.

**Request Body**:
```json
{
  "url": "https://github.com/user/ledmatrix-plugins"
}
```

---

## System

### Get System Status

**GET** `/api/v3/system/status`

Get system status and metrics.

**Response**:
```json
{
  "status": "success",
  "data": {
    "timestamp": 1234567890.123,
    "uptime": "Running",
    "service_active": true,
    "cpu_percent": 25.5,
    "memory_used_percent": 45.2,
    "cpu_temp": 45.0,
    "disk_used_percent": 60.0
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

### Execute System Action

**POST** `/api/v3/system/action`

Execute system-level actions.

**Request Body**:
```json
{
  "action": "start_display",
  "mode": "nfl_live"
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
- `git_pull`: Update code from git repository

**Response**:
```json
{
  "status": "success",
  "message": "Action start_display completed",
  "returncode": 0,
  "stdout": "...",
  "stderr": ""
}
```

---

## Fonts

### Get Font Catalog

**GET** `/api/v3/fonts/catalog`

Get list of available fonts.

**Response**:
```json
{
  "status": "success",
  "data": {
    "fonts": [
      {
        "family": "Press Start 2P",
        "files": ["PressStart2P-Regular.ttf"],
        "sizes": [8, 10, 12]
      }
    ]
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
      "xl": 16
    }
  }
}
```

### Get Font Overrides

**GET** `/api/v3/fonts/overrides`

Get current font overrides.

**Response**:
```json
{
  "status": "success",
  "data": {
    "overrides": {
      "plugin.football-scoreboard.title": {
        "family": "Arial",
        "size_px": 12
      }
    }
  }
}
```

### Set Font Override

**POST** `/api/v3/fonts/overrides`

Set a font override for a specific element.

**Request Body**:
```json
{
  "element_key": "plugin.football-scoreboard.title",
  "family": "Arial",
  "size_px": 12
}
```

### Delete Font Override

**DELETE** `/api/v3/fonts/overrides/<element_key>`

Remove a font override.

### Upload Font

**POST** `/api/v3/fonts/upload`

Upload a custom font file.

**Request**: Multipart form data
- `file` (required): Font file (.ttf, .otf, etc.)

**Response**:
```json
{
  "status": "success",
  "data": {
    "family": "Custom Font",
    "filename": "custom-font.ttf"
  }
}
```

### Delete Font

**DELETE** `/api/v3/fonts/delete/<font_family>`

Delete an uploaded font.

---

## Cache

### List Cache Entries

**GET** `/api/v3/cache/list`

List all cache entries.

**Response**:
```json
{
  "status": "success",
  "data": {
    "entries": [
      {
        "key": "weather_current_12345",
        "age": 300,
        "size": 1024
      }
    ]
  }
}
```

### Delete Cache Entry

**POST** `/api/v3/cache/delete`

Delete a cache entry or clear all cache.

**Request Body**:
```json
{
  "key": "weather_current_12345"
}
```

**Or clear all**:
```json
{
  "clear_all": true
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
    "signal_strength": -50
  }
}
```

### Scan WiFi Networks

**GET** `/api/v3/wifi/scan`

Scan for available WiFi networks.

**Response**:
```json
{
  "status": "success",
  "data": {
    "networks": [
      {
        "ssid": "MyNetwork",
        "signal_strength": -50,
        "encryption": "WPA2",
        "connected": true
      }
    ]
  }
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

**Response**:
```json
{
  "status": "success",
  "message": "Connecting to MyNetwork..."
}
```

### Disconnect from WiFi

**POST** `/api/v3/wifi/disconnect`

Disconnect from current WiFi network.

### Enable Access Point Mode

**POST** `/api/v3/wifi/ap/enable`

Enable WiFi access point mode.

### Disable Access Point Mode

**POST** `/api/v3/wifi/ap/disable`

Disable WiFi access point mode.

### Get Auto-Enable AP Status

**GET** `/api/v3/wifi/ap/auto-enable`

Get access point auto-enable configuration.

**Response**:
```json
{
  "status": "success",
  "data": {
    "auto_enable": true,
    "timeout_seconds": 300
  }
}
```

### Set Auto-Enable AP

**POST** `/api/v3/wifi/ap/auto-enable`

Configure access point auto-enable settings.

**Request Body**:
```json
{
  "auto_enable": true,
  "timeout_seconds": 300
}
```

---

## Streams

### System Statistics Stream

**GET** `/api/v3/stream/stats`

Server-Sent Events (SSE) stream for real-time system statistics.

**Response**: SSE stream
```
data: {"cpu_percent": 25.5, "memory_used_percent": 45.2, ...}

data: {"cpu_percent": 26.0, "memory_used_percent": 45.3, ...}
```

### Display Preview Stream

**GET** `/api/v3/stream/display`

Server-Sent Events (SSE) stream for real-time display preview images.

**Response**: SSE stream with base64-encoded images
```
data: {"image": "base64_data_here", "timestamp": 1234567890.123}
```

### Service Logs Stream

**GET** `/api/v3/stream/logs`

Server-Sent Events (SSE) stream for real-time service logs.

**Response**: SSE stream
```
data: {"level": "INFO", "message": "Plugin loaded", "timestamp": 1234567890.123}
```

---

## Logs

### Get Logs

**GET** `/api/v3/logs?limit=100&level=INFO`

Get recent log entries.

**Query Parameters**:
- `limit` (optional): Maximum number of log entries (default: 100)
- `level` (optional): Filter by log level (DEBUG, INFO, WARNING, ERROR)

**Response**:
```json
{
  "status": "success",
  "data": {
    "logs": [
      {
        "level": "INFO",
        "message": "Plugin loaded: football-scoreboard",
        "timestamp": 1234567890.123
      }
    ]
  }
}
```

---

## Error Responses

All endpoints may return error responses in the following format:

```json
{
  "status": "error",
  "message": "Error description",
  "error_code": "ERROR_CODE",
  "details": "Additional error details (optional)"
}
```

**Common HTTP Status Codes**:
- `200`: Success
- `400`: Bad Request (invalid parameters)
- `404`: Not Found (resource doesn't exist)
- `500`: Internal Server Error
- `503`: Service Unavailable (feature not available)

---

## See Also

- [Plugin API Reference](PLUGIN_API_REFERENCE.md) - API for plugin developers
- [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md) - Complete plugin development guide
- [Web Interface README](../web_interface/README.md) - Web interface documentation

