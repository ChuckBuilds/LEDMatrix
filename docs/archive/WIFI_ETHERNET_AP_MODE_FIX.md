# WiFi Monitor Ethernet Check Fix

## Problem

The WiFi monitor service was enabling Access Point (AP) mode whenever WiFi was disconnected, even when the Raspberry Pi was connected via Ethernet. This caused:

- AP mode to activate unnecessarily when Ethernet was available
- Potential network conflicts
- Confusion for users with hardwired connections

## Solution

Updated the WiFi manager to check for Ethernet connectivity before enabling AP mode. AP mode will now only be enabled when:

- **WiFi is NOT connected** AND
- **Ethernet is NOT connected**

## Changes Made

### 1. Added Ethernet Detection Method

Added `_is_ethernet_connected()` method to `src/wifi_manager.py` that:
- Checks for active Ethernet interfaces (eth0, enp*, etc.)
- Verifies the interface has an IP address
- Uses `nmcli` if available, falls back to `ip` command
- Returns `True` if Ethernet is connected and has an IP

### 2. Updated AP Mode Enable Logic

Modified `enable_ap_mode()` to:
- Check for Ethernet connection before enabling AP mode
- Return an error message if Ethernet is connected: "Cannot enable AP mode while Ethernet is connected"

### 3. Updated AP Mode Management Logic

Modified `check_and_manage_ap_mode()` to:
- Check both WiFi and Ethernet status
- Only enable AP mode if both are disconnected
- Disable AP mode if either WiFi or Ethernet connects
- Log appropriate messages for each scenario

### 4. Enhanced Logging

Updated `wifi_monitor_daemon.py` to:
- Log Ethernet connection status
- Include Ethernet status in state change detection
- Log when AP mode is disabled due to Ethernet connection

## Testing

### Verify Ethernet Detection

```bash
# Check if Ethernet is detected
python3 -c "
from src.wifi_manager import WiFiManager
wm = WiFiManager()
print('Ethernet connected:', wm._is_ethernet_connected())
"
```

### Test AP Mode Behavior

1. **With Ethernet connected**:
   ```bash
   # AP mode should NOT enable
   sudo systemctl restart ledmatrix-wifi-monitor
   sudo journalctl -u ledmatrix-wifi-monitor -f
   # Should see: "Cannot enable AP mode while Ethernet is connected"
   ```

2. **With Ethernet disconnected and WiFi disconnected**:
   ```bash
   # Disconnect Ethernet cable
   # AP mode SHOULD enable
   sudo journalctl -u ledmatrix-wifi-monitor -f
   # Should see: "Auto-enabled AP mode (no WiFi or Ethernet connection)"
   ```

3. **With Ethernet connected and WiFi connects**:
   ```bash
   # Connect WiFi
   # AP mode should disable if it was active
   sudo journalctl -u ledmatrix-wifi-monitor -f
   # Should see: "Auto-disabled AP mode (WiFi connected)"
   ```

4. **With Ethernet connects while AP is active**:
   ```bash
   # Connect Ethernet cable while AP mode is active
   # AP mode should disable
   sudo journalctl -u ledmatrix-wifi-monitor -f
   # Should see: "Auto-disabled AP mode (Ethernet connected)"
   ```

## Deployment

### On Existing Installations

1. **Restart the WiFi monitor service**:
   ```bash
   sudo systemctl restart ledmatrix-wifi-monitor
   ```

2. **If AP mode is currently active and Ethernet is connected**, it will automatically disable:
   ```bash
   # Check current status
   sudo systemctl status hostapd
   
   # The service should automatically disable AP mode within 30 seconds
   # Or manually disable:
   sudo systemctl stop hostapd dnsmasq
   ```

3. **Verify the fix**:
   ```bash
   # Check logs
   sudo journalctl -u ledmatrix-wifi-monitor -n 20
   
   # Should see messages about Ethernet connection status
   ```

### On New Installations

The fix is included automatically - no additional steps needed.

## Behavior Summary

| WiFi Status | Ethernet Status | AP Mode | Reason |
|------------|----------------|---------|--------|
| Connected | Connected | ❌ Disabled | Both connections available |
| Connected | Disconnected | ❌ Disabled | WiFi available |
| Disconnected | Connected | ❌ Disabled | Ethernet available |
| Disconnected | Disconnected | ✅ Enabled | No network connection |

## Troubleshooting

### AP Mode Still Enables with Ethernet Connected

1. **Check Ethernet detection**:
   ```bash
   python3 -c "
   from src.wifi_manager import WiFiManager
   wm = WiFiManager()
   print('Ethernet connected:', wm._is_ethernet_connected())
   "
   ```

2. **Check network interface status**:
   ```bash
   nmcli device status
   # OR
   ip addr show
   ```

3. **Verify Ethernet has IP address**:
   ```bash
   ip addr show eth0
   # Should show an "inet" address (not just 127.0.0.1)
   ```

### Ethernet Not Detected

If Ethernet is connected but not detected:

1. **Check interface name**:
   ```bash
   ip link show
   # Look for Ethernet interfaces (may be eth0, enp*, etc.)
   ```

2. **Check NetworkManager status**:
   ```bash
   sudo systemctl status NetworkManager
   ```

3. **Manually check interface**:
   ```bash
   nmcli device status | grep ethernet
   ```

## Related Files

- `src/wifi_manager.py` - Main WiFi management logic
- `scripts/utils/wifi_monitor_daemon.py` - Background daemon that monitors WiFi/Ethernet
- `scripts/install/install_wifi_monitor.sh` - Installation script for WiFi monitor service

## Notes

- The Ethernet check uses `nmcli` if available (preferred), otherwise falls back to `ip` command
- The check verifies that the interface has an actual IP address (not just link up)
- AP mode will automatically disable within 30 seconds (check interval) when Ethernet connects
- Manual AP mode enable via web interface will also respect Ethernet connection status

