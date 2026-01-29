# AP Mode Manual Enable Configuration

## Overview

By default, Access Point (AP) mode is **not automatically enabled** after installation. AP mode must be manually enabled through the web interface when needed.

## Default Behavior

- **Auto-enable AP mode**: `false` (disabled by default)
- AP mode will **not** automatically activate when WiFi or Ethernet disconnects
- AP mode can only be enabled manually through the web interface

## Why Manual Enable?

This prevents:
- AP mode from activating unexpectedly after installation
- Network conflicts when Ethernet is connected
- SSH becoming unavailable due to automatic AP mode activation
- Unnecessary AP mode activation on systems with stable network connections

## Enabling AP Mode

### Via Web Interface

1. Navigate to the **WiFi** tab in the web interface
2. Click the **"Enable AP Mode"** button
3. AP mode will activate if:
   - WiFi is not connected AND
   - Ethernet is not connected

### Via API

```bash
# Enable AP mode
curl -X POST http://localhost:5001/api/v3/wifi/ap/enable

# Disable AP mode
curl -X POST http://localhost:5001/api/v3/wifi/ap/disable
```

## Enabling Auto-Enable (Optional)

If you want AP mode to automatically enable when WiFi/Ethernet disconnect:

### Via Web Interface

1. Navigate to the **WiFi** tab
2. Look for the **"Auto-enable AP Mode"** toggle or setting
3. Enable the toggle

### Via Configuration File

Edit `config/wifi_config.json`:

```json
{
  "auto_enable_ap_mode": true,
  ...
}
```

Then restart the WiFi monitor service:

```bash
sudo systemctl restart ledmatrix-wifi-monitor
```

### Via API

```bash
# Get current setting
curl http://localhost:5001/api/v3/wifi/ap/auto-enable

# Set auto-enable to true
curl -X POST http://localhost:5001/api/v3/wifi/ap/auto-enable \
  -H "Content-Type: application/json" \
  -d '{"auto_enable_ap_mode": true}'
```

## Behavior Summary

| Auto-Enable Setting | WiFi Status | Ethernet Status | AP Mode Behavior |
|---------------------|-------------|-----------------|------------------|
| `false` (default) | Any | Any | Manual enable only |
| `true` | Connected | Any | Disabled |
| `true` | Disconnected | Connected | Disabled |
| `true` | Disconnected | Disconnected | **Auto-enabled** |

## When Auto-Enable is Disabled (Default)

- AP mode **never** activates automatically
- Must be manually enabled via web UI or API
- Once enabled, it will automatically disable when WiFi or Ethernet connects
- Useful for systems with stable network connections (e.g., Ethernet)

## When Auto-Enable is Enabled

- AP mode automatically enables when both WiFi and Ethernet disconnect
- AP mode automatically disables when WiFi or Ethernet connects
- Useful for portable devices that may lose network connectivity

## Troubleshooting

### AP Mode Not Enabling

1. **Check if WiFi or Ethernet is connected**:
   ```bash
   nmcli device status
   ```

2. **Check auto-enable setting**:
   ```bash
   python3 -c "
   from src.wifi_manager import WiFiManager
   wm = WiFiManager()
   print('Auto-enable:', wm.config.get('auto_enable_ap_mode', False))
   "
   ```

3. **Manually enable AP mode**:
   - Use web interface: WiFi tab → Enable AP Mode button
   - Or via API: `POST /api/v3/wifi/ap/enable`

### AP Mode Enabling Unexpectedly

1. **Check auto-enable setting**:
   ```bash
   cat config/wifi_config.json | grep auto_enable_ap_mode
   ```

2. **Disable auto-enable**:
   ```bash
   # Edit config file
   nano config/wifi_config.json
   # Set "auto_enable_ap_mode": false
   
   # Restart service
   sudo systemctl restart ledmatrix-wifi-monitor
   ```

3. **Check service logs**:
   ```bash
   sudo journalctl -u ledmatrix-wifi-monitor -f
   ```

## Migration from Old Behavior

If you have an existing installation that was auto-enabling AP mode:

1. The default is now `false` (manual enable)
2. Existing configs will be updated to include `auto_enable_ap_mode: false`
3. If you want the old behavior, set `auto_enable_ap_mode: true` in `config/wifi_config.json`

## Related Documentation

- [WiFi Setup Guide](WIFI_SETUP.md)
- [SSH Unavailable After Install](SSH_UNAVAILABLE_AFTER_INSTALL.md)
- [WiFi Ethernet AP Mode Fix](WIFI_ETHERNET_AP_MODE_FIX.md)

