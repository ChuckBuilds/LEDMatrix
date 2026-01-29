# Captive Portal Troubleshooting Guide

## Problem: Can't Access Web Interface When Connected to AP

If you've connected to the "LEDMatrix-Setup" WiFi network but can't access the web interface, follow these steps:

## Quick Checks

### 1. Verify Web Server is Running

```bash
sudo systemctl status ledmatrix-web
```

If not running:
```bash
sudo systemctl start ledmatrix-web
sudo systemctl enable ledmatrix-web
```

### 2. Try Direct IP Access

On your phone/device, try accessing the web interface directly:
- **http://192.168.4.1:5000/v3**
- **http://192.168.4.1:5000**

The port `:5000` is required - the web server runs on port 5000, not the standard port 80.

### 3. Check DNS Resolution

The captive portal uses DNS redirection. Try accessing:
- **http://captive.apple.com** (should redirect to setup page)
- **http://www.google.com** (should redirect to setup page)
- **http://192.168.4.1:5000** (direct access - should always work)

### 4. Verify AP Mode is Active

```bash
sudo systemctl status hostapd
sudo systemctl status dnsmasq
ip addr show wlan0 | grep 192.168.4.1
```

All should be active/running.

### 5. Check Firewall

If you have a firewall enabled, ensure port 5000 is open:

```bash
# For UFW
sudo ufw allow 5000/tcp

# For iptables
sudo iptables -A INPUT -p tcp --dport 5000 -j ACCEPT
```

## Common Issues

### Issue: "Can't connect to server" or "Connection refused"

**Cause**: Web server not running or not listening on the correct interface.

**Solution**:
```bash
sudo systemctl start ledmatrix-web
sudo systemctl status ledmatrix-web
```

### Issue: DNS not resolving / "Server not found"

**Cause**: dnsmasq not running or DNS redirection not configured.

**Solution**:
```bash
# Check dnsmasq
sudo systemctl status dnsmasq

# Restart AP mode
cd ~/LEDMatrix
python3 -c "from src.wifi_manager import WiFiManager; wm = WiFiManager(); wm.disable_ap_mode(); wm.enable_ap_mode()"
```

### Issue: Page loads but shows "Connection Error" or blank page

**Cause**: Web server is running but Flask app has errors.

**Solution**:
```bash
# Check web server logs
sudo journalctl -u ledmatrix-web -n 50 --no-pager

# Restart web server
sudo systemctl restart ledmatrix-web
```

### Issue: Phone connects but browser doesn't open automatically

**Cause**: Some devices don't automatically detect captive portals.

**Solution**: Manually open browser and go to:
- **http://192.168.4.1:5000/v3**
- Or try: **http://captive.apple.com** (iOS) or **http://www.google.com** (Android)

## Testing Steps

1. **Disconnect Ethernet** from Pi
2. **Wait 30 seconds** for AP mode to start
3. **Connect phone** to "LEDMatrix-Setup" network (password: `ledmatrix123`)
4. **Open browser** on phone
5. **Try these URLs**:
   - `http://192.168.4.1:5000/v3` (direct access)
   - `http://captive.apple.com` (iOS captive portal detection)
   - `http://www.google.com` (should redirect)

## Automated Troubleshooting

Run the troubleshooting script:

```bash
cd ~/LEDMatrix
./scripts/troubleshoot_captive_portal.sh
```

This will check all components and provide specific fixes.

## Manual AP Mode Test

To manually test AP mode (bypassing Ethernet check):

```bash
cd ~/LEDMatrix
python3 -c "
from src.wifi_manager import WiFiManager
wm = WiFiManager()

# Temporarily disconnect Ethernet check
# (This is for testing only - normally AP won't start with Ethernet)
print('Enabling AP mode...')
result = wm.enable_ap_mode()
print('Result:', result)
"
```

**Note**: This will fail if Ethernet is connected (by design). You must disconnect Ethernet first.

## Still Not Working?

1. **Check all services**:
   ```bash
   sudo systemctl status ledmatrix-web hostapd dnsmasq ledmatrix-wifi-monitor
   ```

2. **Check logs**:
   ```bash
   sudo journalctl -u ledmatrix-web -f
   sudo journalctl -u ledmatrix-wifi-monitor -f
   ```

3. **Verify network configuration**:
   ```bash
   ip addr show wlan0
   ip route show
   ```

4. **Test from Pi itself**:
   ```bash
   curl http://192.168.4.1:5000/v3
   ```

If it works from the Pi but not from your phone, it's likely a DNS or firewall issue.

