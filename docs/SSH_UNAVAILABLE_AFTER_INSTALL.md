# SSH Unavailable After Installation - Troubleshooting Guide

## Why SSH Becomes Unavailable

After running `first_time_install.sh`, SSH may become unavailable for the following reasons:

### 1. WiFi Monitor Service Enables AP Mode

**Primary Cause**: The WiFi monitor service (`ledmatrix-wifi-monitor`) automatically enables Access Point (AP) mode when it detects that the Raspberry Pi is not connected to WiFi. When AP mode is active:

- The Pi creates its own WiFi network: **LEDMatrix-Setup** (password: `ledmatrix123`)
- The Pi's WiFi interface (`wlan0`) switches from client mode to AP mode
- **This disconnects the Pi from your original WiFi network**
- SSH becomes unavailable because the Pi is no longer on your network

### 2. Network Configuration Changes

The installation script:
- Installs and configures `hostapd` (Access Point daemon)
- Installs and configures `dnsmasq` (DHCP server for AP mode)
- These services can interfere with normal WiFi client mode

### 3. Reboot After Installation

If the script reboots the Pi (which it recommends), network services may restart in a different state, potentially triggering AP mode.

## How to Regain SSH Access

### Option 1: Connect to AP Mode (Recommended for Initial Setup)

1. **Find the AP Network**:
   - Look for a WiFi network named **LEDMatrix-Setup** on your phone/computer
   - Default password: `ledmatrix123`

2. **Connect to the AP**:
   - Connect your device to the **LEDMatrix-Setup** network
   - The Pi will have IP address: `192.168.4.1`

3. **SSH via AP Mode**:
   ```bash
   ssh devpi@192.168.4.1
   ```

4. **Disable AP Mode and Reconnect to WiFi**:
   Once connected via SSH:
   ```bash
   # Check WiFi status
   nmcli device status
   
   # Disable AP mode manually
   sudo systemctl stop hostapd
   sudo systemctl stop dnsmasq
   
   # Connect to your WiFi network (replace with your SSID and password)
   sudo nmcli device wifi connect "YourWiFiSSID" password "YourPassword"
   
   # Or use the web interface at http://192.168.4.1:5001
   # Navigate to WiFi tab and connect to your network
   ```

### Option 2: Disable WiFi Monitor Service Temporarily

If you have physical access to the Pi or can connect via AP mode:

```bash
# Stop the WiFi monitor service
sudo systemctl stop ledmatrix-wifi-monitor

# Disable it from starting on boot (optional)
sudo systemctl disable ledmatrix-wifi-monitor

# Stop AP mode services
sudo systemctl stop hostapd
sudo systemctl stop dnsmasq

# Reconnect to your WiFi network
sudo nmcli device wifi connect "YourWiFiSSID" password "YourPassword"
```

### Option 3: Use Ethernet Connection

If your Pi is connected via Ethernet:
- SSH should remain available via Ethernet even if WiFi is in AP mode
- Connect via: `ssh devpi@<pi-ip-address>`

### Option 4: Physical Access

If you have physical access to the Pi:
1. Connect a keyboard and monitor
2. Log in locally
3. Follow Option 2 to disable AP mode and reconnect to WiFi

## Preventing SSH Loss in the Future

### Method 1: Configure WiFi Before Installation

Before running `first_time_install.sh`, ensure WiFi is properly configured and connected:

```bash
# Check WiFi status
nmcli device status

# If not connected, connect to WiFi
sudo nmcli device wifi connect "YourWiFiSSID" password "YourPassword"

# Verify connection
ping -c 3 8.8.8.8
```

### Method 2: Disable WiFi Monitor Service

If you don't need the WiFi setup feature:

```bash
# After installation, disable the WiFi monitor service
sudo systemctl stop ledmatrix-wifi-monitor
sudo systemctl disable ledmatrix-wifi-monitor
```

### Method 3: Configure WiFi Monitor to Not Auto-Enable AP

Edit the WiFi monitor configuration to prevent automatic AP mode:

```bash
# Edit the WiFi config (if it exists)
nano /home/devpi/LEDMatrix/config/wifi_config.json

# Or modify the WiFi monitor daemon behavior
# (requires code changes to wifi_monitor_daemon.py)
```

## Verification Steps

After regaining SSH access, verify your installation:

```bash
cd /home/devpi/LEDMatrix
./scripts/verify_installation.sh
```

This script will check:
- Systemd services status
- Python dependencies
- Configuration files
- File permissions
- Web interface availability
- Network connectivity

## Quick Reference Commands

```bash
# Check WiFi status
nmcli device status
nmcli device wifi list

# Check AP mode status
sudo systemctl status hostapd
sudo systemctl status dnsmasq

# Check WiFi monitor service
sudo systemctl status ledmatrix-wifi-monitor

# View WiFi monitor logs
sudo journalctl -u ledmatrix-wifi-monitor -f

# Connect to WiFi
sudo nmcli device wifi connect "SSID" password "password"

# Disable AP mode
sudo systemctl stop hostapd dnsmasq

# Restart network services
sudo systemctl restart NetworkManager
```

## Web Interface Access

Even if SSH is unavailable, you can access the web interface:

1. **Via AP Mode**: Connect to **LEDMatrix-Setup** network and visit `http://192.168.4.1:5001`
2. **Via WiFi**: If WiFi is connected, visit `http://<pi-ip-address>:5001`
3. **Via Ethernet**: Visit `http://<pi-ip-address>:5001`

The web interface allows you to:
- Configure WiFi connections
- Enable/disable AP mode
- Check service status
- View logs
- Manage the LED Matrix display

## Summary

**SSH becomes unavailable because**:
- WiFi monitor service enables AP mode when WiFi disconnects
- AP mode switches WiFi from client to access point mode
- Pi loses connection to your original network

**To regain SSH**:
1. Connect to **LEDMatrix-Setup** AP network (password: `ledmatrix123`)
2. SSH to `192.168.4.1`
3. Disable AP mode and reconnect to your WiFi network
4. Or disable the WiFi monitor service if not needed

**To prevent future issues**:
- Ensure WiFi is connected before installation
- Or disable WiFi monitor service if you don't need AP mode feature

