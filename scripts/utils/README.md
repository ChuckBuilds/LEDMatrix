# Utility Scripts

This directory contains utility scripts for maintenance and system operations.

## Scripts

- **`clear_cache.py`** - Clears LEDMatrix cache data (specific keys or all cache)
- **`start_web_conditionally.py`** - Conditionally starts the web interface based on config settings
- **`wifi_monitor_daemon.py`** - Background daemon that monitors WiFi/Ethernet connection and manages access point mode
- **`cleanup_venv.sh`** - Cleans up Python virtual environment files
- **`clear_python_cache.sh`** - Clears Python cache files (__pycache__, *.pyc, etc.)
- **`pixlet_config_editor.sh`** - Opens Pixlet's own config UI for one installed Starlark app
- **`apply_dns_single_request.sh`** - Adds `options single-request` to the resolver (run by `ledmatrix-dns-fix.service`)

## Usage

### Clear Cache
```bash
python3 scripts/utils/clear_cache.py --list          # List cache keys
python3 scripts/utils/clear_cache.py --clear-all      # Clear all cache
python3 scripts/utils/clear_cache.py --clear <key>    # Clear specific key
```

### Start Web Interface Conditionally
This script is typically called by the systemd service (`ledmatrix-web.service`) and checks the `web_display_autostart` setting in `config/config.json` before starting the web interface.

### WiFi Monitor Daemon
This daemon is typically run as a systemd service (`ledmatrix-wifi-monitor.service`) and automatically manages WiFi access point mode based on network connectivity.


### Pixlet Config Editor
Run it when you want Pixlet's own config form for a Starlark app -- live
render preview, cascading dropdowns -- rather than the LEDMatrix one.

```bash
./scripts/utils/pixlet_config_editor.sh                 # list installed apps
./scripts/utils/pixlet_config_editor.sh penndot_signs   # edit, on localhost:8080
./scripts/utils/pixlet_config_editor.sh penndot_signs --lan   # reachable from the LAN
```

Deliberately not a service. It stops the display for the length of the
session and `pixlet serve` listens with no authentication, so it should only
be running while you are actually editing. It backs the config up first and
restarts the display on exit, however it exits.

From another machine, forward the port rather than using `--lan`:

```bash
ssh -L 8080:localhost:8080 pi@ledpi.local
```

### Apply DNS Single-Request Fix
Installed and run by `ledmatrix-dns-fix.service`; see `systemd/README.md`.
Safe to run by hand (`sudo ./scripts/utils/apply_dns_single_request.sh`) and
idempotent.
