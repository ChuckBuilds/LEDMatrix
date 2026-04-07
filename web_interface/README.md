# LED Matrix Web Interface V3

Modern, production web interface for controlling the LED Matrix display.

## Overview

This directory contains the active V3 web interface with the following features:
- Real-time display preview via Server-Sent Events (SSE)
- Plugin management and configuration
- System monitoring and logs
- Modern, responsive UI
- RESTful API

## Directory Structure

```
web_interface/
├── app.py                    # Main Flask application
├── start.py                  # Startup script
├── run.sh                    # Shell runner script
├── requirements.txt          # Python dependencies
├── blueprints/               # Flask blueprints
│   ├── api_v3.py            # API endpoints
│   └── pages_v3.py          # Page routes
├── templates/                # HTML templates
│   └── v3/
│       ├── base.html
│       ├── index.html
│       └── partials/
└── static/                   # CSS/JS assets
    └── v3/
        ├── app.css
        └── app.js
```

## Running the Web Interface

### Standalone (Development)

From the project root:
```bash
python3 web_interface/start.py
```

Or using the shell script:
```bash
./web_interface/run.sh
```

### As a Service (Production)

The web interface can run as a systemd service that starts automatically based on the `web_display_autostart` configuration setting:

```bash
sudo systemctl start ledmatrix-web
sudo systemctl enable ledmatrix-web  # Start on boot
```

## Accessing the Interface

Once running, access the web interface at:
- Local: http://localhost:5000
- Network: http://<raspberry-pi-ip>:5000

## Configuration

The web interface reads configuration from:
- `config/config.json` - Main configuration
- `config/config_secrets.json` - API keys and secrets

## API Documentation

The V3 API is mounted at `/api/v3/` (`app.py:144`). For the complete
list and request/response formats, see
[`docs/REST_API_REFERENCE.md`](../docs/REST_API_REFERENCE.md). Quick
reference for the most common endpoints:

### Configuration
- `GET /api/v3/config/main` - Get main configuration
- `POST /api/v3/config/main` - Save main configuration
- `GET /api/v3/config/secrets` - Get secrets configuration
- `POST /api/v3/config/raw/main` - Save raw main config (Config Editor)
- `POST /api/v3/config/raw/secrets` - Save raw secrets

### Display & System Control
- `GET /api/v3/system/status` - System status
- `POST /api/v3/system/action` - Control display (action body:
  `start_display`, `stop_display`, `restart_display_service`,
  `restart_web_service`, `git_pull`, `reboot_system`, `shutdown_system`)
- `GET /api/v3/display/current` - Current display frame
- `GET /api/v3/display/on-demand/status` - On-demand status
- `POST /api/v3/display/on-demand/start` - Trigger on-demand display
- `POST /api/v3/display/on-demand/stop` - Clear on-demand

### Plugins
- `GET /api/v3/plugins/installed` - List installed plugins
- `GET /api/v3/plugins/config?plugin_id=<id>` - Get plugin config
- `POST /api/v3/plugins/config` - Update plugin configuration
- `GET /api/v3/plugins/schema?plugin_id=<id>` - Get plugin schema
- `POST /api/v3/plugins/toggle` - Enable/disable plugin
- `POST /api/v3/plugins/install` - Install from registry
- `POST /api/v3/plugins/install-from-url` - Install from GitHub URL
- `POST /api/v3/plugins/uninstall` - Uninstall plugin
- `POST /api/v3/plugins/update` - Update plugin

### Plugin Store
- `GET /api/v3/plugins/store/list` - List available registry plugins
- `POST /api/v3/plugins/store/refresh` - Refresh registry from GitHub

### Real-time Streams (SSE)
SSE stream endpoints are defined directly on the Flask app
(`app.py:607-615`), not on the api_v3 blueprint:
- `GET /api/v3/stream/stats` - System statistics stream
- `GET /api/v3/stream/display` - Display preview stream
- `GET /api/v3/stream/logs` - Service logs stream

## Development

When making changes to the web interface:

1. Edit files in this directory
2. Test changes by running `python3 web_interface/start.py`
3. Restart the service if running: `sudo systemctl restart ledmatrix-web`

## Notes

- Templates and static files use the `v3/` prefix to allow for future versions
- The interface uses Flask blueprints for modular organization
- SSE streams provide real-time updates without polling

