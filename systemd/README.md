# Systemd Service Files

This directory contains systemd service unit files for LEDMatrix services.

## Service Files

- **`ledmatrix.service`** - Main LED Matrix display service
  - Runs the display controller (`run.py`)
  - Starts automatically on boot
  - Runs as root for hardware access

- **`ledmatrix-web.service`** - Web interface service
  - Runs the web interface conditionally based on config
  - Starts automatically on boot if `web_display_autostart` is enabled
  - Uses `scripts/utils/start_web_conditionally.py`

- **`ledmatrix-wifi-monitor.service`** - WiFi monitor daemon service
  - Monitors WiFi/Ethernet connectivity
  - Automatically enables/disables access point mode
  - Uses `scripts/utils/wifi_monitor_daemon.py`

## Installation

These service files are installed by the installation scripts in `scripts/install/`:
- `install_service.sh` installs `ledmatrix.service`
- `install_web_service.sh` installs `ledmatrix-web.service`
- `install_wifi_monitor.sh` installs `ledmatrix-wifi-monitor.service`

## Manual Installation

If you need to install a service manually:

```bash
sudo cp systemd/ledmatrix.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ledmatrix.service
sudo systemctl start ledmatrix.service
```

## Service Management

```bash
# Check status
sudo systemctl status ledmatrix.service

# Start/stop/restart
sudo systemctl start ledmatrix.service
sudo systemctl stop ledmatrix.service
sudo systemctl restart ledmatrix.service

# Enable/disable autostart
sudo systemctl enable ledmatrix.service
sudo systemctl disable ledmatrix.service

# View logs
journalctl -u ledmatrix.service -f
```

