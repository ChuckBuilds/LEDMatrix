# Utility Scripts

This directory contains utility scripts for maintenance and system operations.

## Scripts

- **`clear_cache.py`** - Clears LEDMatrix cache data (specific keys or all cache)
- **`start_web_conditionally.py`** - Conditionally starts the web interface based on config settings
- **`wifi_monitor_daemon.py`** - Background daemon that monitors WiFi/Ethernet connection and manages access point mode
- **`cleanup_venv.sh`** - Cleans up Python virtual environment files
- **`clear_python_cache.sh`** - Clears Python cache files (__pycache__, *.pyc, etc.)

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

