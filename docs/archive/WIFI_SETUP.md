# WiFi Setup Feature

The LED Matrix project includes a WiFi setup feature that allows you to configure WiFi connections through a web interface. When the Raspberry Pi is not connected to WiFi, it automatically broadcasts an access point (AP) that you can connect to for initial setup.

## Features

- **Automatic AP Mode**: When no WiFi connection is detected, the Raspberry Pi automatically creates a WiFi access point named "LEDMatrix-Setup"
- **Web Interface**: Access the WiFi setup interface through your web browser
- **Network Scanning**: Scan for available WiFi networks from the web interface
- **Secure Connection**: Save WiFi credentials securely
- **Automatic Management**: The WiFi monitor daemon automatically enables/disables AP mode based on connection status

## Requirements

The following packages are required for WiFi setup functionality:

- **hostapd**: Access point software
- **dnsmasq**: DHCP server for AP mode
- **NetworkManager** (or **iwlist**): WiFi management tools

These packages are automatically checked and can be installed during the WiFi monitor service installation.

## Installation

### 1. Install WiFi Monitor Service

Run the installation script to set up the WiFi monitor daemon:

```bash
cd /home/ledpi/LEDMatrix
sudo ./scripts/install/install_wifi_monitor.sh
```

This script will:
- Check for required packages and offer to install them
- Create the systemd service file
- Enable and start the WiFi monitor service
- Configure the service to start on boot

### 2. Verify Service Status

Check that the WiFi monitor service is running:

```bash
sudo systemctl status ledmatrix-wifi-monitor
```

You should see output indicating the service is active and running.

## Usage

### Accessing the WiFi Setup Interface

1. **If WiFi is NOT connected**: The Raspberry Pi will automatically create an access point (after a 90-second grace period)
   - Connect to the WiFi network: **LEDMatrix-Setup** (open network, no password required)
   - Open a web browser and navigate to: `http://192.168.4.1:5000` or `http://192.168.4.1` (captive portal may redirect)
   - Or use the IP address shown in the web interface

2. **If WiFi IS connected**: Access the web interface normally
   - Navigate to: `http://<raspberry-pi-ip>:5000`
   - Click on the **WiFi** tab in the navigation

### Connecting to a WiFi Network

1. Navigate to the **WiFi** tab in the web interface
2. Click **Scan** to search for available networks
3. Select a network from the dropdown menu, or enter the SSID manually
4. Enter the WiFi password (leave empty for open networks)
5. Click **Connect**
6. The system will attempt to connect to the selected network
7. Once connected, AP mode will automatically disable

### Manual AP Mode Control

You can manually enable or disable AP mode from the web interface:

- **Enable AP Mode**: Click "Enable AP Mode" button (only available when WiFi is not connected)
- **Disable AP Mode**: Click "Disable AP Mode" button (only available when AP mode is active)

## How It Works

### WiFi Monitor Daemon

The WiFi monitor daemon (`wifi_monitor_daemon.py`) runs as a background service that:

1. Checks WiFi connection status every 30 seconds (configurable)
2. Automatically enables AP mode only if:
   - `auto_enable_ap_mode` is enabled in config AND
   - No WiFi connection is detected AND
   - No Ethernet connection is detected
3. Automatically disables AP mode when WiFi or Ethernet connection is established
4. Logs all state changes for troubleshooting

**Note**: By default, `auto_enable_ap_mode` is `true`, meaning AP mode will automatically activate when both WiFi and Ethernet are disconnected. However, there's a 90-second grace period (3 consecutive checks at 30-second intervals) to prevent AP mode from enabling on transient network hiccups. This ensures you can always configure the device even when it has no network connection.

### WiFi Manager Module

The WiFi manager (`src/wifi_manager.py`) provides:

- **Connection Status**: Checks current WiFi connection state
- **Network Scanning**: Scans for available WiFi networks
- **Connection Management**: Connects to WiFi networks and saves credentials
- **AP Mode Control**: Manages access point mode (hostapd/dnsmasq)

### Configuration

WiFi settings are stored in `config/wifi_config.json`:

```json
{
  "ap_ssid": "LEDMatrix-Setup",
  "ap_password": "ledmatrix123",
  "ap_channel": 7,
  "auto_enable_ap_mode": true,
  "saved_networks": [
    {
      "ssid": "MyNetwork",
      "password": "mypassword",
      "saved_at": 1234567890.0
    }
  ]
}
```

**Configuration Options:**
- `ap_ssid`: SSID for the access point (default: "LEDMatrix-Setup")
- `ap_channel`: WiFi channel for AP mode (default: 7)
- `auto_enable_ap_mode`: Automatically enable AP mode when WiFi/Ethernet disconnect (default: `true`)
  - When `true`: AP mode automatically enables after a 90-second grace period when both WiFi and Ethernet are disconnected
  - When `false`: AP mode must be manually enabled through the web interface
- `saved_networks`: List of saved WiFi network credentials

**Note**: The access point is configured as an open network (no password required) for ease of initial setup. This allows any device to connect without credentials.

### Access Point Configuration

The AP mode uses `hostapd` and `dnsmasq` for access point functionality:

- **SSID**: LEDMatrix-Setup (configurable)
- **IP Range**: 192.168.4.2 - 192.168.4.20
- **Gateway**: 192.168.4.1
- **Channel**: 7 (configurable)

## Verification

### Running the WiFi Verification Script

Use the comprehensive verification script to check your WiFi setup:

```bash
cd /home/ledpi/LEDMatrix
./scripts/verify_wifi_setup.sh
```

This script checks:
- Required packages are installed
- WiFi monitor service is running
- Configuration files are valid
- WiFi permissions are configured
- WiFi interface is available
- WiFi radio status
- Current connection status
- AP mode status
- WiFi Manager module availability
- Web interface API accessibility

The script provides a summary with passed/warning/failed checks to help diagnose issues.

## Troubleshooting

### WiFi Monitor Service Not Starting

Check the service logs:

```bash
sudo journalctl -u ledmatrix-wifi-monitor -n 50
```

Common issues:
- Missing packages (hostapd, dnsmasq)
- Permission issues
- Network interface not available

### Cannot Access AP Mode

1. Check if AP mode is active:
   ```bash
   sudo systemctl status hostapd
   ```

2. Check if dnsmasq is running:
   ```bash
   sudo systemctl status dnsmasq
   ```

3. Verify WiFi interface exists:
   ```bash
   ip link show wlan0
   ```

### Cannot Connect to WiFi Network

1. Verify the SSID and password are correct
2. Check if the network requires a password (some networks may appear open but require a password)
3. Check WiFi monitor logs for connection errors:
   ```bash
   sudo journalctl -u ledmatrix-wifi-monitor -f
   ```

4. Check NetworkManager logs:
   ```bash
   sudo journalctl -u NetworkManager -n 50
   ```

### AP Mode Not Disabling

If AP mode doesn't disable after connecting to WiFi:

1. Check WiFi connection status:
   ```bash
   nmcli device status
   ```

2. Manually disable AP mode from the web interface
3. Restart the WiFi monitor service:
   ```bash
   sudo systemctl restart ledmatrix-wifi-monitor
   ```

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

### Configuration Options

You can modify the check interval by editing the service file:

```bash
sudo systemctl edit ledmatrix-wifi-monitor
```

Or modify the service file directly:

```bash
sudo nano /etc/systemd/system/ledmatrix-wifi-monitor.service
```

Change the `--interval` parameter in the `ExecStart` line (default is 30 seconds).

After modifying, reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ledmatrix-wifi-monitor
```

## Security Considerations

- **Open AP Network**: The access point is configured as an open network (no password) for ease of initial setup. This allows any device within range to connect to the setup network. Consider your deployment environment when using this feature.
- **WiFi Credentials**: Saved WiFi credentials are stored in `config/wifi_config.json`. Ensure proper file permissions:
  ```bash
  sudo chmod 600 config/wifi_config.json
  ```
- **Network Access**: When in AP mode, anyone within range can connect to the setup network. This is by design to allow easy initial configuration. For production deployments in secure environments, consider using the web interface when connected to WiFi instead.

## API Endpoints

The WiFi setup feature exposes the following API endpoints:

- `GET /api/v3/wifi/status` - Get current WiFi connection status
- `GET /api/v3/wifi/scan` - Scan for available WiFi networks
- `POST /api/v3/wifi/connect` - Connect to a WiFi network
- `POST /api/v3/wifi/ap/enable` - Enable access point mode
- `POST /api/v3/wifi/ap/disable` - Disable access point mode

## Technical Details

### WiFi Detection Methods

The WiFi manager tries multiple methods to detect WiFi status:

1. **NetworkManager (nmcli)** - Preferred method if available
2. **iwconfig** - Fallback method for systems without NetworkManager

### Network Scanning

The system supports multiple scanning methods:

1. **nmcli** - Fast, preferred method
2. **iwlist** - Fallback method for older systems

### Access Point Setup

AP mode configuration:

- Uses `hostapd` (preferred) or `nmcli hotspot` (fallback) for WiFi access point functionality
- Uses `dnsmasq` for DHCP and DNS services (hostapd mode only)
- Configures wlan0 interface in AP mode
- Provides DHCP range: 192.168.4.2-20
- Gateway IP: 192.168.4.1
- **Open network**: No password required (configures as open network for easy setup)
- Captive portal: DNS redirection for automatic browser redirects (hostapd mode only)

## Development

### Testing WiFi Manager

You can test the WiFi manager directly:

```python
from src.wifi_manager import WiFiManager

# Create WiFi manager instance
wifi_manager = WiFiManager()

# Get status
status = wifi_manager.get_wifi_status()
print(f"Connected: {status.connected}, SSID: {status.ssid}")

# Scan networks
networks = wifi_manager.scan_networks()
for net in networks:
    print(f"{net.ssid}: {net.signal}% ({net.security})")

# Connect to network
success, message = wifi_manager.connect_to_network("MyNetwork", "password")
print(f"Connection: {success}, Message: {message}")
```

### Running Monitor Daemon Manually

For testing, you can run the daemon in foreground mode:

```bash
sudo python3 wifi_monitor_daemon.py --interval 10 --foreground
```

## Support

For issues or questions:
1. Check the logs: `sudo journalctl -u ledmatrix-wifi-monitor -f`
2. Review this documentation
3. Check the main project README for general troubleshooting
4. Open an issue on GitHub if needed

