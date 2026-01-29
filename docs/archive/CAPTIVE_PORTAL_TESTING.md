# Captive Portal Testing Guide

This guide explains how to test the captive portal WiFi setup functionality.

## Prerequisites

1. **Raspberry Pi with LEDMatrix installed**
2. **WiFi adapter** (built-in or USB)
3. **Test devices** (smartphone, tablet, or laptop)
4. **Access to Pi** (SSH or direct access)

## Important: Before Testing

**⚠️ Make sure you have a way to reconnect!**

Before starting testing, ensure you have:
- **Ethernet cable** (if available) as backup connection
- **SSH access** via another method (Ethernet, direct connection)
- **Physical access** to Pi (keyboard/monitor) as last resort
- **Your WiFi credentials** saved/noted down

**If testing fails, see:** [Reconnecting After Testing](RECONNECT_AFTER_CAPTIVE_PORTAL_TESTING.md)

**Quick recovery script:** `sudo ./scripts/emergency_reconnect.sh`

## Pre-Testing Setup

### 0. Verify WiFi is Ready (IMPORTANT!)

**⚠️ CRITICAL: Run this BEFORE disconnecting Ethernet!**

```bash
sudo ./scripts/verify_wifi_before_testing.sh
```

This script will verify:
- WiFi interface exists and is enabled
- WiFi can scan for networks
- You have saved WiFi connections (for reconnecting)
- Required services are ready
- Current network status

**Do NOT disconnect Ethernet until this script passes all checks!**

### 1. Ensure WiFi Monitor Service is Running

```bash
sudo systemctl status ledmatrix-wifi-monitor
```

If not running:
```bash
sudo systemctl start ledmatrix-wifi-monitor
sudo systemctl enable ledmatrix-wifi-monitor
```

### 2. Disconnect Pi from WiFi/Ethernet

**⚠️ Only do this AFTER running the verification script!**

To test captive portal, the Pi should NOT be connected to any network:

```bash
# First, verify WiFi is ready (see step 0 above)
sudo ./scripts/verify_wifi_before_testing.sh

# Check current network status
nmcli device status

# Disconnect WiFi (if connected)
sudo nmcli device disconnect wlan0

# Disconnect Ethernet (if connected)
# Option 1: Unplug Ethernet cable (safest)
# Option 2: Via command (if you're sure WiFi works):
sudo nmcli device disconnect eth0

# Verify disconnection
nmcli device status
# Both should show "disconnected" or "unavailable"
```

### 3. Enable AP Mode

You can enable AP mode manually or wait for it to auto-enable (if `auto_enable_ap_mode` is true):

**Manual enable via web interface:**
- Access web interface at `http://<pi-ip>:5000` (if still accessible)
- Go to WiFi tab
- Click "Enable AP Mode"

**Manual enable via command line:**
```bash
python3 -c "from src.wifi_manager import WiFiManager; wm = WiFiManager(); print(wm.enable_ap_mode())"
```

**Or via API:**
```bash
curl -X POST http://localhost:5000/api/v3/wifi/ap/enable
```

### 4. Verify AP Mode is Active

```bash
# Check hostapd service
sudo systemctl status hostapd

# Check dnsmasq service
sudo systemctl status dnsmasq

# Check if wlan0 is in AP mode
iwconfig wlan0
# Should show "Mode:Master"

# Check IP address
ip addr show wlan0
# Should show 192.168.4.1
```

### 5. Verify DNSMASQ Configuration

```bash
# Check dnsmasq config
sudo cat /etc/dnsmasq.conf

# Should contain:
# - address=/#/192.168.4.1
# - address=/captive.apple.com/192.168.4.1
# - address=/connectivitycheck.gstatic.com/192.168.4.1
# - address=/www.msftconnecttest.com/192.168.4.1
# - address=/detectportal.firefox.com/192.168.4.1
```

### 6. Verify Web Interface is Running

```bash
# Check if web service is running
sudo systemctl status ledmatrix-web

# Or check if Flask app is running
ps aux | grep "web_interface"
```

## Testing Procedures

### Test 1: DNS Redirection

**Purpose:** Verify that DNS queries are redirected to the Pi.

**Steps:**
1. Connect a device to "LEDMatrix-Setup" network (password: `ledmatrix123`)
2. Try to resolve any domain name:
   ```bash
   # On Linux/Mac
   nslookup google.com
   # Should return 192.168.4.1
   
   # On Windows
   nslookup google.com
   # Should return 192.168.4.1
   ```

**Expected Result:** All DNS queries should resolve to 192.168.4.1

### Test 2: HTTP Redirect (Manual Browser Test)

**Purpose:** Verify that HTTP requests redirect to WiFi setup page.

**Steps:**
1. Connect device to "LEDMatrix-Setup" network
2. Open a web browser
3. Try to access any website:
   - `http://google.com`
   - `http://example.com`
   - `http://192.168.4.1` (direct IP)

**Expected Result:** All requests should redirect to `http://192.168.4.1:5000/v3` (WiFi setup interface)

### Test 3: Captive Portal Detection Endpoints

**Purpose:** Verify that device detection endpoints respond correctly.

**Test each endpoint:**

```bash
# iOS/macOS detection
curl http://192.168.4.1:5000/hotspot-detect.html
# Expected: HTML response with "Success"

# Android detection
curl -I http://192.168.4.1:5000/generate_204
# Expected: HTTP 204 No Content

# Windows detection
curl http://192.168.4.1:5000/connecttest.txt
# Expected: "Microsoft Connect Test"

# Firefox detection
curl http://192.168.4.1:5000/success.txt
# Expected: "success"
```

**Expected Result:** Each endpoint should return the appropriate response

### Test 4: iOS Device (iPhone/iPad)

**Purpose:** Test automatic captive portal detection on iOS.

**Steps:**
1. On iPhone/iPad, go to Settings > Wi-Fi
2. Connect to "LEDMatrix-Setup" network
3. Enter password: `ledmatrix123`
4. Wait a few seconds

**Expected Result:**
- iOS should automatically detect the captive portal
- A popup should appear saying "Sign in to Network" or similar
- Tapping it should open Safari with the WiFi setup page
- The setup page should show the captive portal banner

**If it doesn't auto-open:**
- Open Safari manually
- Try to visit any website (e.g., apple.com)
- Should redirect to WiFi setup page

### Test 5: Android Device

**Purpose:** Test automatic captive portal detection on Android.

**Steps:**
1. On Android device, go to Settings > Wi-Fi
2. Connect to "LEDMatrix-Setup" network
3. Enter password: `ledmatrix123`
4. Wait a few seconds

**Expected Result:**
- Android should show a notification: "Sign in to network" or "Network sign-in required"
- Tapping the notification should open a browser with the WiFi setup page
- The setup page should show the captive portal banner

**If notification doesn't appear:**
- Open Chrome browser
- Try to visit any website
- Should redirect to WiFi setup page

### Test 6: Windows Laptop

**Purpose:** Test captive portal on Windows.

**Steps:**
1. Connect Windows laptop to "LEDMatrix-Setup" network
2. Enter password: `ledmatrix123`
3. Wait a few seconds

**Expected Result:**
- Windows may show a notification about network sign-in
- Opening any browser and visiting any website should redirect to WiFi setup page
- Edge/Chrome may automatically open a sign-in window

**Manual test:**
- Open any browser
- Visit `http://www.msftconnecttest.com` or any website
- Should redirect to WiFi setup page

### Test 7: API Endpoints Still Work

**Purpose:** Verify that WiFi API endpoints function normally during AP mode.

**Steps:**
1. While connected to "LEDMatrix-Setup" network
2. Test API endpoints:

```bash
# Status endpoint
curl http://192.168.4.1:5000/api/v3/wifi/status

# Scan networks
curl http://192.168.4.1:5000/api/v3/wifi/scan
```

**Expected Result:** API endpoints should return JSON responses normally (not redirect)

### Test 8: WiFi Connection Flow

**Purpose:** Test the complete flow of connecting to WiFi via captive portal.

**Steps:**
1. Connect device to "LEDMatrix-Setup" network
2. Wait for captive portal to redirect to setup page
3. Click "Scan" to find available networks
4. Select a network from the list
5. Enter WiFi password
6. Click "Connect"
7. Wait for connection to establish

**Expected Result:**
- Device should connect to selected WiFi network
- AP mode should automatically disable
- Device should now be on the new network
- Can access Pi via new network IP address

## Troubleshooting

### Issue: DNS Not Redirecting

**Symptoms:** DNS queries resolve to actual IPs, not 192.168.4.1

**Solutions:**
1. Check dnsmasq config:
   ```bash
   sudo cat /etc/dnsmasq.conf | grep address
   ```
2. Restart dnsmasq:
   ```bash
   sudo systemctl restart dnsmasq
   ```
3. Check dnsmasq logs:
   ```bash
   sudo journalctl -u dnsmasq -n 50
   ```

### Issue: HTTP Not Redirecting

**Symptoms:** Browser shows actual websites instead of redirecting

**Solutions:**
1. Check if AP mode is active:
   ```bash
   python3 -c "from src.wifi_manager import WiFiManager; wm = WiFiManager(); print(wm._is_ap_mode_active())"
   ```
2. Check Flask app logs for errors
3. Verify web interface is running on port 5000
4. Test redirect middleware manually:
   ```bash
   curl -I http://192.168.4.1:5000/google.com
   # Should return 302 redirect
   ```

### Issue: Captive Portal Not Detected by Device

**Symptoms:** Device doesn't show sign-in notification/popup

**Solutions:**
1. Verify detection endpoints are accessible:
   ```bash
   curl http://192.168.4.1:5000/hotspot-detect.html
   curl http://192.168.4.1:5000/generate_204
   ```
2. Try manually opening browser and visiting any website
3. Some devices require specific responses - check endpoint implementations
4. Clear device's network settings and reconnect

### Issue: Infinite Redirect Loop

**Symptoms:** Browser keeps redirecting in a loop

**Solutions:**
1. Check that `/v3` path is in allowed_paths list
2. Verify redirect middleware logic in `app.py`
3. Check Flask logs for errors
4. Ensure WiFi API endpoints are not being redirected

### Issue: AP Mode Not Enabling

**Symptoms:** Can't connect to "LEDMatrix-Setup" network

**Solutions:**
1. Check WiFi monitor service:
   ```bash
   sudo systemctl status ledmatrix-wifi-monitor
   ```
2. Check WiFi config:
   ```bash
   cat config/wifi_config.json
   ```
3. Manually enable AP mode:
   ```bash
   python3 -c "from src.wifi_manager import WiFiManager; wm = WiFiManager(); print(wm.enable_ap_mode())"
   ```
4. Check hostapd logs:
   ```bash
   sudo journalctl -u hostapd -n 50
   ```

## Verification Checklist

- [ ] DNS redirection works (all domains resolve to 192.168.4.1)
- [ ] HTTP redirect works (all websites redirect to setup page)
- [ ] Captive portal detection endpoints respond correctly
- [ ] iOS device auto-opens setup page
- [ ] Android device shows sign-in notification
- [ ] Windows device redirects to setup page
- [ ] WiFi API endpoints still work during AP mode
- [ ] Can successfully connect to WiFi via setup page
- [ ] AP mode disables after WiFi connection
- [ ] No infinite redirect loops
- [ ] Captive portal banner appears on setup page when AP mode is active

## Quick Test Script

Save this as `test_captive_portal.sh`:

```bash
#!/bin/bash

echo "Testing Captive Portal Functionality"
echo "===================================="

# Test DNS redirection
echo -e "\n1. Testing DNS redirection..."
nslookup google.com | grep -q "192.168.4.1" && echo "✓ DNS redirection works" || echo "✗ DNS redirection failed"

# Test HTTP redirect
echo -e "\n2. Testing HTTP redirect..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -L http://192.168.4.1:5000/google.com)
[ "$HTTP_CODE" = "200" ] && echo "✓ HTTP redirect works" || echo "✗ HTTP redirect failed (got $HTTP_CODE)"

# Test detection endpoints
echo -e "\n3. Testing captive portal detection endpoints..."
curl -s http://192.168.4.1:5000/hotspot-detect.html | grep -q "Success" && echo "✓ iOS endpoint works" || echo "✗ iOS endpoint failed"
curl -s -o /dev/null -w "%{http_code}" http://192.168.4.1:5000/generate_204 | grep -q "204" && echo "✓ Android endpoint works" || echo "✗ Android endpoint failed"
curl -s http://192.168.4.1:5000/connecttest.txt | grep -q "Microsoft" && echo "✓ Windows endpoint works" || echo "✗ Windows endpoint failed"
curl -s http://192.168.4.1:5000/success.txt | grep -q "success" && echo "✓ Firefox endpoint works" || echo "✗ Firefox endpoint failed"

# Test API endpoints
echo -e "\n4. Testing API endpoints..."
API_RESPONSE=$(curl -s http://192.168.4.1:5000/api/v3/wifi/status)
echo "$API_RESPONSE" | grep -q "status" && echo "✓ API endpoints work" || echo "✗ API endpoints failed"

echo -e "\nTesting complete!"
```

Make it executable and run:
```bash
chmod +x test_captive_portal.sh
./test_captive_portal.sh
```

## Notes

- **Port Number:** The web interface runs on port 5000 by default. If you've changed this, update all URLs accordingly.
- **Network Range:** The AP uses 192.168.4.0/24 network. If you need a different range, update both hostapd and dnsmasq configs.
- **Password:** Default AP password is `ledmatrix123`. Change it in `config/wifi_config.json` if needed.
- **Testing on Same Device:** If testing from the Pi itself, you'll need a second device to connect to the AP network.

