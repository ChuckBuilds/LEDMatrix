# Optimal WiFi Configuration with Failover AP Mode

## Overview

This guide explains the optimal way to configure WiFi with automatic failover to Access Point (AP) mode, ensuring you can always connect to your Raspberry Pi even when the primary WiFi network is unavailable.

## System Architecture

### How It Works

The LEDMatrix WiFi system uses a **grace period mechanism** to prevent false positives from transient network hiccups:

1. **WiFi Monitor Daemon** runs as a background service (every 30 seconds by default)
2. **Grace Period**: Requires **3 consecutive disconnected checks** before enabling AP mode
   - At 30-second intervals, this means **90 seconds** of confirmed disconnection
   - This prevents AP mode from activating during brief network interruptions
3. **Automatic Failover**: When both WiFi and Ethernet are disconnected for the grace period, AP mode activates
4. **Automatic Recovery**: When WiFi or Ethernet reconnects, AP mode automatically disables

### Connection Priority

The system checks connections in this order:
1. **WiFi Connection** (highest priority)
2. **Ethernet Connection** (fallback)
3. **AP Mode** (last resort - only when both WiFi and Ethernet are disconnected)

## Optimal Configuration

### Recommended Settings

For a **reliable failover system**, use these settings:

```json
{
  "ap_ssid": "LEDMatrix-Setup",
  "ap_password": "ledmatrix123",
  "ap_channel": 7,
  "auto_enable_ap_mode": true,
  "saved_networks": [
    {
      "ssid": "YourPrimaryNetwork",
      "password": "your-password"
    }
  ]
}
```

### Key Configuration Options

| Setting | Recommended Value | Purpose |
|---------|------------------|---------|
| `auto_enable_ap_mode` | `true` | Enables automatic failover to AP mode |
| `ap_ssid` | `LEDMatrix-Setup` | Network name for AP mode (customizable) |
| `ap_password` | `ledmatrix123` | Password for AP mode (change for security) |
| `ap_channel` | `7` (or 1, 6, 11) | WiFi channel (use non-overlapping channels) |
| `saved_networks` | Array of networks | Pre-configured networks for quick connection |

## Step-by-Step Setup

### 1. Initial Configuration

**Via Web Interface (Recommended):**

1. Connect to your Raspberry Pi (via Ethernet or existing WiFi)
2. Navigate to the **WiFi** tab in the web interface
3. Configure your primary WiFi network:
   - Click **Scan** to find networks
   - Select your network from the dropdown
   - Enter your WiFi password
   - Click **Connect**
4. Enable auto-failover:
   - Toggle **"Auto-Enable AP Mode"** to **ON**
   - This enables automatic failover when WiFi disconnects

**Via Configuration File:**

```bash
# Edit the WiFi configuration
nano config/wifi_config.json
```

Set `auto_enable_ap_mode` to `true`:

```json
{
  "auto_enable_ap_mode": true,
  ...
}
```

### 2. Verify WiFi Monitor Service

The WiFi monitor daemon must be running for automatic failover:

```bash
# Check service status
sudo systemctl status ledmatrix-wifi-monitor

# If not running, start it
sudo systemctl start ledmatrix-wifi-monitor

# Enable on boot
sudo systemctl enable ledmatrix-wifi-monitor
```

### 3. Test Failover Behavior

**Test Scenario 1: WiFi Disconnection**

1. Disconnect your WiFi router or move the Pi out of range
2. Wait **90 seconds** (3 check intervals × 30 seconds)
3. AP mode should automatically activate
4. Connect to **LEDMatrix-Setup** network from your device
5. Access web interface at `http://192.168.4.1:5000`

**Test Scenario 2: WiFi Reconnection**

1. Reconnect WiFi router or move Pi back in range
2. Within **30 seconds**, AP mode should automatically disable
3. Pi should reconnect to your primary WiFi network

## How the Grace Period Works

### Disconnected Check Counter

The system uses a **disconnected check counter** to prevent false positives:

```
Check Interval: 30 seconds (configurable)
Required Checks: 3 consecutive
Grace Period: 90 seconds total
```

**Example Timeline:**

```
Time 0s:   WiFi disconnects
Time 30s:  Check 1 - Disconnected (counter = 1)
Time 60s:  Check 2 - Disconnected (counter = 2)
Time 90s:  Check 3 - Disconnected (counter = 3) → AP MODE ENABLED
```

If WiFi reconnects at any point, the counter resets to 0.

### Why Grace Period is Important

Without a grace period, AP mode would activate during:
- Brief network hiccups
- Router reboots
- Temporary signal interference
- NetworkManager reconnection attempts

The 90-second grace period ensures AP mode only activates when there's a **sustained disconnection**.

## Best Practices

### 1. Security Considerations

**Change Default AP Password:**

```json
{
  "ap_password": "your-strong-password-here"
}
```

**Use Non-Overlapping WiFi Channels:**

- Channels 1, 6, 11 are non-overlapping (2.4GHz)
- Choose a channel that doesn't conflict with your primary network
- Example: If primary network uses channel 1, use channel 11 for AP mode

### 2. Network Configuration

**Save Multiple Networks:**

You can save multiple WiFi networks for automatic connection:

```json
{
  "saved_networks": [
    {
      "ssid": "Home-Network",
      "password": "home-password"
    },
    {
      "ssid": "Office-Network",
      "password": "office-password"
    }
  ]
}
```

**Note:** Saved networks are stored for reference but connection still requires manual selection or NetworkManager auto-connect.

### 3. Monitoring and Troubleshooting

**Check Service Logs:**

```bash
# View real-time logs
sudo journalctl -u ledmatrix-wifi-monitor -f

# View recent logs
sudo journalctl -u ledmatrix-wifi-monitor -n 50
```

**Check WiFi Status:**

```bash
# Via Python
python3 -c "
from src.wifi_manager import WiFiManager
wm = WiFiManager()
status = wm.get_wifi_status()
print(f'Connected: {status.connected}')
print(f'SSID: {status.ssid}')
print(f'IP: {status.ip_address}')
print(f'AP Mode: {status.ap_mode_active}')
print(f'Auto-Enable: {wm.config.get(\"auto_enable_ap_mode\", False)}')
"
```

**Check NetworkManager Status:**

```bash
# View device status
nmcli device status

# View connections
nmcli connection show

# View WiFi networks
nmcli device wifi list
```

### 4. Customization Options

**Adjust Check Interval:**

Edit the systemd service file:

```bash
sudo systemctl edit ledmatrix-wifi-monitor
```

Add:

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /path/to/LEDMatrix/scripts/utils/wifi_monitor_daemon.py --interval 20
```

Then restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ledmatrix-wifi-monitor
```

**Note:** Changing the interval affects the grace period:
- 20-second interval = 60-second grace period (3 × 20)
- 30-second interval = 90-second grace period (3 × 30) ← Default
- 60-second interval = 180-second grace period (3 × 60)

## Configuration Scenarios

### Scenario 1: Always-On Failover (Recommended)

**Use Case:** Portable device that may lose WiFi connection

**Configuration:**
```json
{
  "auto_enable_ap_mode": true
}
```

**Behavior:**
- AP mode activates automatically after 90 seconds of disconnection
- Always provides a way to connect to the device
- Best for devices that move or have unreliable WiFi

### Scenario 2: Manual AP Mode Only

**Use Case:** Stable network connection (e.g., Ethernet or reliable WiFi)

**Configuration:**
```json
{
  "auto_enable_ap_mode": false
}
```

**Behavior:**
- AP mode must be manually enabled via web UI
- Prevents unnecessary AP mode activation
- Best for stationary devices with stable connections

### Scenario 3: Ethernet Primary with WiFi Failover

**Use Case:** Device primarily uses Ethernet, WiFi as backup

**Configuration:**
```json
{
  "auto_enable_ap_mode": true
}
```

**Behavior:**
- Ethernet connection prevents AP mode activation
- If Ethernet disconnects, WiFi is attempted
- If both disconnect, AP mode activates after grace period
- Best for devices with both Ethernet and WiFi

## Troubleshooting

### AP Mode Not Activating

**Check 1: Auto-Enable Setting**
```bash
cat config/wifi_config.json | grep auto_enable_ap_mode
```
Should show `"auto_enable_ap_mode": true`

**Check 2: Service Status**
```bash
sudo systemctl status ledmatrix-wifi-monitor
```
Service should be `active (running)`

**Check 3: Grace Period**
- Wait at least 90 seconds after disconnection
- Check logs: `sudo journalctl -u ledmatrix-wifi-monitor -f`

**Check 4: Ethernet Connection**
- If Ethernet is connected, AP mode won't activate
- Disconnect Ethernet to test AP mode

### AP Mode Activating Unexpectedly

**Check 1: Network Stability**
- Verify WiFi connection is stable
- Check for router issues or signal problems

**Check 2: Grace Period Too Short**
- Current grace period is 90 seconds
- Brief disconnections shouldn't trigger AP mode
- Check logs for disconnection patterns

**Check 3: Disable Auto-Enable**
```bash
# Set to false
nano config/wifi_config.json
# Change: "auto_enable_ap_mode": false
sudo systemctl restart ledmatrix-wifi-monitor
```

### Cannot Connect to AP Mode

**Check 1: AP Mode Active**
```bash
sudo systemctl status hostapd
sudo systemctl status dnsmasq
```

**Check 2: Network Interface**
```bash
ip addr show wlan0
```
Should show IP `192.168.4.1`

**Check 3: Firewall**
```bash
sudo iptables -L -n
```
Check if port 5000 is accessible

**Check 4: Manual Enable**
- Try manually enabling AP mode via web UI
- Or via API: `curl -X POST http://localhost:5001/api/v3/wifi/ap/enable`

## Summary

### Optimal Configuration Checklist

- [ ] `auto_enable_ap_mode` set to `true`
- [ ] WiFi monitor service running and enabled
- [ ] Primary WiFi network configured and tested
- [ ] AP password changed from default
- [ ] AP channel configured (non-overlapping)
- [ ] Grace period understood (90 seconds)
- [ ] Failover behavior tested

### Key Takeaways

1. **Grace Period**: 90 seconds prevents false positives
2. **Auto-Enable**: Set to `true` for reliable failover
3. **Service**: WiFi monitor daemon must be running
4. **Priority**: WiFi → Ethernet → AP Mode
5. **Automatic**: AP mode disables when WiFi/Ethernet connects

This configuration provides a robust failover system that ensures you can always access your Raspberry Pi, even when the primary network connection fails.

