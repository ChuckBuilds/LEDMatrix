# WiFi Network Setup Guide

## Overview

The LEDMatrix WiFi system provides automatic network configuration with intelligent failover to Access Point (AP) mode. When your Raspberry Pi loses network connectivity, it automatically creates a WiFi access point for easy configuration—ensuring you can always connect to your device.

### Key Features

- **Automatic AP Mode**: Creates a WiFi access point when network connection is lost
- **Intelligent Failover**: Only activates after a grace period to prevent false positives
- **Dual Connectivity**: Supports both WiFi and Ethernet with automatic priority management
- **Web Interface**: Configure WiFi through an easy-to-use web interface
- **Network Scanning**: Scan and connect to available WiFi networks
- **Secure Storage**: WiFi credentials stored securely

---

## Quick Start

### Accessing WiFi Setup

**If not connected to WiFi:**
1. Wait 90 seconds after boot (AP mode activation grace period)
2. Connect to WiFi network: **LEDMatrix-Setup** (open network)
3. Open browser to: `http://192.168.4.1:5050`
4. Navigate to the WiFi tab
5. Scan, select your network, and connect

**If already connected:**
1. Open browser to: `http://your-pi-ip:5050`
2. Navigate to the WiFi tab
3. Configure as needed

---

## Installation

### Prerequisites

The following packages are required:
- **hostapd** - Access point software
- **dnsmasq** - DHCP server for AP mode
- **NetworkManager** - WiFi management

### Install WiFi Monitor Service

```bash
cd /home/ledpi/LEDMatrix
sudo ./scripts/install/install_wifi_monitor.sh
```

This script will:
- Check for required packages and offer to install them
- Create the systemd service file
- Enable and start the WiFi monitor service
- Configure the service to start on boot

### Verify Installation

```bash
# Check service status
sudo systemctl status ledmatrix-wifi-monitor

# Run verification script
./scripts/verify_wifi_setup.sh
```

---

## Configuration

### Configuration File

WiFi settings are stored in `config/wifi_config.json`:

```json
{
  "ap_ssid": "LEDMatrix-Setup",
  "ap_password": "",
  "ap_channel": 7,
  "auto_enable_ap_mode": true,
  "saved_networks": [
    {
      "ssid": "YourNetwork",
      "password": "your-password",
      "saved_at": 1234567890.0
    }
  ]
}
```

### Configuration Options

| Setting | Default | Description |
|---------|---------|-------------|
| `ap_ssid` | `LEDMatrix-Setup` | Network name for AP mode |
| `ap_password` | `` (empty) | AP password (empty = open network) |
| `ap_channel` | `7` | WiFi channel (use 1, 6, or 11 for non-overlapping) |
| `auto_enable_ap_mode` | `true` | Automatically enable AP mode when disconnected |
| `saved_networks` | `[]` | Array of saved WiFi credentials |

### Auto-Enable AP Mode Behavior

**When enabled (`true` - recommended):**
- AP mode activates automatically after 90-second grace period
- Only when both WiFi AND Ethernet are disconnected
- Automatically disables when either WiFi or Ethernet connects
- Best for portable devices or unreliable network environments

**When disabled (`false`):**
- AP mode must be manually enabled through web interface
- Prevents unnecessary AP activation
- Best for devices with stable network connections

---

## Using WiFi Setup

### Connecting to a WiFi Network

**Via Web Interface:**
1. Navigate to the **WiFi** tab
2. Click **Scan** to search for networks
3. Select a network from the dropdown (or enter SSID manually)
4. Enter the WiFi password (leave empty for open networks)
5. Click **Connect**
6. System will attempt connection
7. AP mode automatically disables once connected

**Via API:**
```bash
# Scan for networks
curl "http://your-pi-ip:5050/api/wifi/scan"

# Connect to network
curl -X POST http://your-pi-ip:5050/api/wifi/connect \
  -H "Content-Type: application/json" \
  -d '{"ssid": "YourNetwork", "password": "your-password"}'
```

### Manual AP Mode Control

**Via Web Interface:**
- **Enable AP Mode**: Click "Enable AP Mode" button (only when WiFi/Ethernet disconnected)
- **Disable AP Mode**: Click "Disable AP Mode" button (when AP is active)

**Via API:**
```bash
# Enable AP mode
curl -X POST http://your-pi-ip:5050/api/wifi/ap/enable

# Disable AP mode
curl -X POST http://your-pi-ip:5050/api/wifi/ap/disable
```

**Note:** Manual enable still requires both WiFi and Ethernet to be disconnected.

---

## Understanding AP Mode Failover

### How the Grace Period Works

The system uses a **grace period mechanism** to prevent false positives from temporary network hiccups:

```
Check Interval: 30 seconds (default)
Required Checks: 3 consecutive
Grace Period: 90 seconds total
```

**Timeline Example:**
```
Time 0s:   WiFi disconnects
Time 30s:  Check 1 - Disconnected (counter = 1)
Time 60s:  Check 2 - Disconnected (counter = 2)
Time 90s:  Check 3 - Disconnected (counter = 3) → AP MODE ENABLED
```

If WiFi or Ethernet reconnects at any point, the counter resets to 0.

### Why Grace Period is Important

Without a grace period, AP mode would activate during:
- Brief network hiccups
- Router reboots
- Temporary signal interference
- NetworkManager reconnection attempts

The 90-second grace period ensures AP mode only activates during **sustained disconnection**.

### Connection Priority

The system checks connections in this order:
1. **WiFi Connection** (highest priority)
2. **Ethernet Connection** (fallback)
3. **AP Mode** (last resort - only when both WiFi and Ethernet disconnected)

### Behavior Summary

| WiFi Status | Ethernet Status | Auto-Enable | AP Mode Behavior |
|-------------|-----------------|-------------|------------------|
| Any | Any | `false` | Manual enable only |
| Connected | Any | `true` | Disabled |
| Disconnected | Connected | `true` | Disabled (Ethernet available) |
| Disconnected | Disconnected | `true` | Auto-enabled after 90s |

---

## Access Point Configuration

### AP Mode Settings

- **SSID**: LEDMatrix-Setup (configurable)
- **Network**: Open (no password by default)
- **IP Address**: 192.168.4.1
- **DHCP Range**: 192.168.4.2 - 192.168.4.20
- **Channel**: 7 (configurable)

### Accessing Services in AP Mode

When AP mode is active:
- Web Interface: `http://192.168.4.1:5050`
- SSH: `ssh ledpi@192.168.4.1`
- Captive portal may automatically redirect browsers

---

## Best Practices

### Security Recommendations

**1. Change AP Password (Optional):**
```json
{
  "ap_password": "your-strong-password"
}
```

**Note:** The default is an open network for easy initial setup. For deployments in public areas, consider adding a password.

**2. Use Non-Overlapping WiFi Channels:**
- Channels 1, 6, 11 are non-overlapping (2.4GHz)
- Choose a channel that doesn't conflict with your primary network
- Example: If primary uses channel 1, use channel 11 for AP mode

**3. Secure WiFi Credentials:**
```bash
sudo chmod 600 config/wifi_config.json
```

### Network Configuration Tips

**Save Multiple Networks:**
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

**Adjust Check Interval:**

Edit the systemd service file to change grace period:
```bash
sudo systemctl edit ledmatrix-wifi-monitor
```

Add:
```ini
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /path/to/LEDMatrix/scripts/utils/wifi_monitor_daemon.py --interval 20
```

**Note:** Interval affects grace period:
- 20-second interval = 60-second grace period (3 × 20)
- 30-second interval = 90-second grace period (3 × 30) ← Default
- 60-second interval = 180-second grace period (3 × 60)

---

## Configuration Scenarios

### Scenario 1: Portable Device with Auto-Failover (Recommended)

**Use Case:** Device may lose WiFi connection

**Configuration:**
```json
{
  "auto_enable_ap_mode": true
}
```

**Behavior:**
- AP mode activates automatically after 90 seconds of disconnection
- Always provides a way to connect
- Best for devices that move or have unreliable WiFi

### Scenario 2: Stable Network Connection

**Use Case:** Ethernet or reliable WiFi connection

**Configuration:**
```json
{
  "auto_enable_ap_mode": false
}
```

**Behavior:**
- AP mode must be manually enabled
- Prevents unnecessary activation
- Best for stationary devices with stable connections

### Scenario 3: Ethernet Primary with WiFi Backup

**Use Case:** Primary Ethernet, WiFi as backup

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

---

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
- Verify: `nmcli device status`
- Disconnect Ethernet to test AP mode

**Check 5: Required Packages**
```bash
# Verify hostapd is installed
which hostapd

# Verify dnsmasq is installed
which dnsmasq
```

### Cannot Access AP Mode

**Check 1: AP Mode Active**
```bash
sudo systemctl status hostapd
sudo systemctl status dnsmasq
```
Both should be running

**Check 2: Network Interface**
```bash
ip addr show wlan0
```
Should show IP `192.168.4.1`

**Check 3: WiFi Interface Available**
```bash
ip link show wlan0
```
Interface should exist

**Check 4: Try Manual Enable**
- Use web interface: WiFi tab → Enable AP Mode
- Or via API: `curl -X POST http://localhost:5050/api/wifi/ap/enable`

### Cannot Connect to WiFi Network

**Check 1: Verify Credentials**
- Ensure SSID and password are correct
- Check for hidden networks (manual SSID entry required)

**Check 2: Check Logs**
```bash
# WiFi monitor logs
sudo journalctl -u ledmatrix-wifi-monitor -f

# NetworkManager logs
sudo journalctl -u NetworkManager -n 50
```

**Check 3: Network Compatibility**
- Verify network is 2.4GHz (5GHz may not be supported on all Pi models)
- Check if network requires special authentication

### AP Mode Not Disabling After WiFi Connect

**Check 1: WiFi Connection Status**
```bash
nmcli device status
```

**Check 2: Manually Disable**
- Use web interface: WiFi tab → Disable AP Mode
- Or restart service: `sudo systemctl restart ledmatrix-wifi-monitor`

**Check 3: Check Logs**
```bash
sudo journalctl -u ledmatrix-wifi-monitor -n 50
```

### AP Mode Activating Unexpectedly

**Check 1: Network Stability**
- Verify WiFi connection is stable
- Check router status
- Check signal strength

**Check 2: Disable Auto-Enable**
```bash
nano config/wifi_config.json
# Change: "auto_enable_ap_mode": false
sudo systemctl restart ledmatrix-wifi-monitor
```

**Check 3: Increase Grace Period**
- Edit service file to increase check interval
- Longer interval = longer grace period
- See "Best Practices" section above

---

## Monitoring and Diagnostics

### Check WiFi Status

**Via Python:**
```python
from src.wifi_manager import WiFiManager

wm = WiFiManager()
status = wm.get_wifi_status()

print(f'Connected: {status.connected}')
print(f'SSID: {status.ssid}')
print(f'IP Address: {status.ip_address}')
print(f'AP Mode Active: {status.ap_mode_active}')
print(f'Auto-Enable: {wm.config.get("auto_enable_ap_mode", False)}')
```

**Via NetworkManager:**
```bash
# View device status
nmcli device status

# View connections
nmcli connection show

# View available WiFi networks
nmcli device wifi list
```

### View Service Logs

```bash
# Real-time logs
sudo journalctl -u ledmatrix-wifi-monitor -f

# Recent logs (last 50 lines)
sudo journalctl -u ledmatrix-wifi-monitor -n 50

# Logs from specific time
sudo journalctl -u ledmatrix-wifi-monitor --since "1 hour ago"
```

### Run Verification Script

```bash
cd /home/ledpi/LEDMatrix
./scripts/verify_wifi_setup.sh
```

Checks:
- Required packages installed
- WiFi monitor service running
- Configuration files valid
- WiFi interface available
- Current connection status
- AP mode status

---

## Service Management

### Useful Commands

```bash
# Check service status
sudo systemctl status ledmatrix-wifi-monitor

# Start the service
sudo systemctl start ledmatrix-wifi-monitor

# Stop the service
sudo systemctl stop ledmatrix-wifi-monitor

# Restart the service
sudo systemctl restart ledmatrix-wifi-monitor

# View logs
sudo journalctl -u ledmatrix-wifi-monitor -f

# Disable service from starting on boot
sudo systemctl disable ledmatrix-wifi-monitor

# Enable service to start on boot
sudo systemctl enable ledmatrix-wifi-monitor
```

---

## API Reference

The WiFi setup feature exposes the following API endpoints:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/wifi/status` | Get current WiFi connection status |
| GET | `/api/wifi/scan` | Scan for available WiFi networks |
| POST | `/api/wifi/connect` | Connect to a WiFi network |
| POST | `/api/wifi/ap/enable` | Enable access point mode |
| POST | `/api/wifi/ap/disable` | Disable access point mode |
| GET | `/api/wifi/ap/auto-enable` | Get auto-enable setting |
| POST | `/api/wifi/ap/auto-enable` | Set auto-enable setting |

### Example Usage

```bash
# Get WiFi status
curl "http://your-pi-ip:5050/api/wifi/status"

# Scan for networks
curl "http://your-pi-ip:5050/api/wifi/scan"

# Connect to network
curl -X POST http://your-pi-ip:5050/api/wifi/connect \
  -H "Content-Type: application/json" \
  -d '{"ssid": "MyNetwork", "password": "mypassword"}'

# Enable AP mode
curl -X POST http://your-pi-ip:5050/api/wifi/ap/enable

# Check auto-enable setting
curl "http://your-pi-ip:5050/api/wifi/ap/auto-enable"

# Set auto-enable
curl -X POST http://your-pi-ip:5050/api/wifi/ap/auto-enable \
  -H "Content-Type: application/json" \
  -d '{"auto_enable_ap_mode": true}'
```

---

## Technical Details

### WiFi Monitor Daemon

The WiFi monitor daemon (`wifi_monitor_daemon.py`) runs as a background service that:

1. Checks WiFi and Ethernet connection status every 30 seconds (configurable)
2. Maintains disconnected check counter for grace period
3. Automatically enables AP mode when:
   - `auto_enable_ap_mode` is enabled AND
   - Both WiFi and Ethernet disconnected AND
   - Grace period elapsed (3 consecutive checks)
4. Automatically disables AP mode when WiFi or Ethernet connects
5. Logs all state changes

### WiFi Detection Methods

The WiFi manager tries multiple methods:

1. **NetworkManager (nmcli)** - Preferred method
2. **iwconfig** - Fallback for systems without NetworkManager

### Network Scanning Methods

1. **nmcli** - Fast, preferred method
2. **iwlist** - Fallback for older systems

### Access Point Implementation

- Uses `hostapd` for WiFi access point functionality
- Uses `dnsmasq` for DHCP and DNS services
- Configures wlan0 interface with IP 192.168.4.1
- Provides DHCP range: 192.168.4.2-20
- Captive portal with DNS redirection

---

## Related Documentation

- [WEB_INTERFACE_GUIDE.md](WEB_INTERFACE_GUIDE.md) - Using the web interface
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - General troubleshooting
- [GETTING_STARTED.md](GETTING_STARTED.md) - Initial setup guide
