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
├── requirements.txt          # Python dependencies
├── blueprints/               # Flask blueprints
│   ├── api_v3/              # API endpoints (package: config, display,
│   │                        #   plugins, system, backup, fonts, misc,
│   │                        #   wifi, starlark)
│   └── pages_v3.py          # Page routes
├── tailwind/                 # Tailwind config + input CSS (build inputs,
│                             #   not served; see "Styling" below)
├── templates/                # HTML templates
│   └── v3/
│       ├── base.html
│       └── partials/
└── static/                   # CSS/JS assets
    └── v3/
        ├── tailwind.css      # GENERATED utility classes (committed)
        ├── plugin-frame.css  # GENERATED styles for plugin web_ui/ iframes
        ├── app.css           # hand-written: tokens, components, dark theme
        ├── app.js
        ├── manifest.json     # PWA manifest
        ├── plugins_manager.js
        ├── icons/            # PWA / touch icons
        ├── js/               # Alpine, htmx, app shell, widgets, utils
        └── vendor/           # codemirror, fontawesome
```

## Styling (Tailwind CSS)

Templates and JS use [Tailwind](https://v3.tailwindcss.com/) utility
classes. The CSS for them is generated on a dev machine or in CI and
**committed**, so the Pi never builds anything and the UI needs no CDN
(it has to work in AP mode, with no internet).

- `static/v3/tailwind.css` holds the utilities. It is generated from the
  classes found in `templates/v3/`, `static/v3/**/*.js` and `blueprints/`,
  so it only contains what the UI uses.
- `static/v3/app.css` is hand-written: theme tokens, base element styles,
  components (`.btn`, `.card`, `.nav-tab`, ...) and the dark theme
  (`[data-theme="dark"] ...` overrides). `base.html` loads it after
  `tailwind.css`, so its rules win over utilities of equal specificity.
  Don't add utility classes to it; use the class and rebuild.
- `static/v3/plugin-frame.css` styles plugin `web_ui/` fragments served by
  `/v3/plugin-ui/<plugin>/web-ui/<file>` in an iframe. Their markup lives in
  plugin repos, so it can't be scanned; its config safelists the common
  utility families instead.

**After changing a template, a static JS file or anything in `tailwind/`,
rebuild and commit the CSS with your change:**

```bash
python3 scripts/build_css.py          # rewrites tailwind.css and plugin-frame.css
python3 scripts/build_css.py --check  # what CI runs: fails if they are stale
```

No Node or npm is needed. The script downloads Tailwind's standalone CLI
(pinned version, SHA-256 checked) for your OS once and caches it outside
the repo (`LEDMATRIX_TAILWIND_CACHE` overrides where). CI runs `--check`
on every PR.

Where to change what:

- A class built at runtime (`` `bg-${color}-100` ``) is invisible to the
  scanner: add it to `safelist` in `tailwind/tailwind.config.js`, or better,
  write the full class names in the code.
- Colours, font sizes and shadows that differ from stock Tailwind (darker
  gray text, emerald/amber button fills, token-based shadows) are set in the
  `theme` of `tailwind/tailwind.config.js`.
- Dark mode is the `data-theme="dark"` attribute on `<html>`; the `dark:`
  variant is configured to match it.

## Running the Web Interface

### Standalone (Development)

From the project root:
```bash
python3 web_interface/start.py
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

The V3 API is the `api_v3` blueprint, registered at `/api/v3/` in
`app.py`. For the complete
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
  `restart_web_service`, `git_pull`, `reboot_system`, `shutdown_system`,
  `enable_autostart`, `disable_autostart`)
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
- `GET /api/v3/plugins/store/github-status` - GitHub authentication status
- `POST /api/v3/plugins/store/refresh` - Refresh registry from GitHub

### Real-time Streams (SSE)
SSE stream endpoints are defined directly on the Flask app in `app.py`
(`stream_stats`, `stream_display`, `stream_logs`, followed by their CSRF
exemption and rate-limit hookup), not on the api_v3 blueprint:
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

