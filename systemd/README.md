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

- **`ledmatrix-dns-fix.service`** - DNS single-request fix (optional)
  - Re-applies `options single-request` to the resolver on every boot,
    because whatever manages `resolv.conf` regenerates it and drops the
    option again
  - Works around glibc's parallel A/AAAA lookup stalling ~5s per name on
    routers that answer only the A query, which makes any plugin calling an
    external API slow or (for Starlark apps, which have a render timeout)
    fail outright
  - Uses `scripts/utils/apply_dns_single_request.sh`
  - Install only if external API calls are timing out; it is not part of a
    normal install

- **`ledmatrix-mqtt-bridge.service`** - Home Assistant MQTT bridge (optional)
  - Exposes the display to Home Assistant over MQTT Discovery: force a mode,
    stop on-demand, toggle power, set brightness
  - Uses `integrations/mqtt_bridge/ledmatrix_mqtt_bridge.py`, which drives the
    web API rather than the display directly
  - Needs `integrations/mqtt_bridge/bridge_config.json`; see that directory's
    README

## Installation

These service files are installed by the installation scripts in `scripts/install/`:
- `install_service.sh` installs `ledmatrix.service`
- `install_web_service.sh` installs `ledmatrix-web.service`
- `install_wifi_monitor.sh` installs `ledmatrix-wifi-monitor.service`
- `install_dns_fix.sh` installs `ledmatrix-dns-fix.service` (opt-in, not run
  by the normal installer)
- `install_mqtt_bridge.sh` installs `ledmatrix-mqtt-bridge.service` (opt-in)

## Manual Installation

> **Important:** the unit files in this directory contain
> `__PROJECT_ROOT_DIR__` placeholders that the install scripts replace
> with the actual project directory at install time. Do **not** copy
> them directly to `/etc/systemd/system/` — the service will fail to
> start with `WorkingDirectory=__PROJECT_ROOT_DIR__` errors.
>
> Always install via the helper script:
>
> ```bash
> sudo ./scripts/install/install_service.sh
> ```
>
> If you really need to do it by hand, substitute the placeholder
> first:
>
> ```bash
> PROJECT_ROOT="$(pwd)"
> sed "s|__PROJECT_ROOT_DIR__|$PROJECT_ROOT|g" systemd/ledmatrix.service \
>   | sudo tee /etc/systemd/system/ledmatrix.service > /dev/null
> sudo systemctl daemon-reload
> sudo systemctl enable ledmatrix.service
> sudo systemctl start ledmatrix.service
> ```

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

