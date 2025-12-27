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
- `config/secrets.json` - API keys and secrets

## API Documentation

The V3 API is available at `/api/v3/` with the following endpoints:

### Configuration
- `GET /api/v3/config/main` - Get main configuration
- `POST /api/v3/config/main` - Save main configuration
- `GET /api/v3/config/secrets` - Get secrets configuration
- `POST /api/v3/config/secrets` - Save secrets configuration

### Display Control
- `POST /api/v3/display/start` - Start display service
- `POST /api/v3/display/stop` - Stop display service
- `POST /api/v3/display/restart` - Restart display service
- `GET /api/v3/display/status` - Get display service status

### Plugins
- `GET /api/v3/plugins` - List installed plugins
- `GET /api/v3/plugins/<id>` - Get plugin details
- `POST /api/v3/plugins/<id>/config` - Update plugin configuration
- `GET /api/v3/plugins/<id>/enable` - Enable plugin
- `GET /api/v3/plugins/<id>/disable` - Disable plugin

### Plugin Store
- `GET /api/v3/store/plugins` - List available plugins
- `POST /api/v3/store/install/<id>` - Install plugin
- `POST /api/v3/store/uninstall/<id>` - Uninstall plugin
- `POST /api/v3/store/update/<id>` - Update plugin

### Real-time Streams (SSE)
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

