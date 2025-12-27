# Reconnecting to Internet After Captive Portal Testing

If captive portal testing fails or you need to reconnect to your normal network, here are several methods to get back online.

## Quick Reference

**Before testing:** Always run `sudo ./scripts/verify_wifi_before_testing.sh` first!

**If stuck:** Run `sudo ./scripts/emergency_reconnect.sh` for automated recovery.

## Quick Recovery Methods

### Method 1: Via Web Interface (If Accessible)

If you can still access the web interface at `http://192.168.4.1:5000`:

1. **Navigate to WiFi tab**
2. **Click "Scan"** to find available networks
3. **Select your network** from the dropdown
4. **Enter your WiFi password**
5. **Click "Connect"**
6. **Wait for connection** - AP mode should automatically disable

### Method 2: Via SSH (If You Have Direct Access)

If you have SSH access to the Pi (via Ethernet, direct connection, or still connected to AP):

```bash
# Connect via SSH
ssh user@192.168.4.1  # If connected to AP
# OR
ssh user@<pi-ip>      # If on same network

# Disable AP mode first
sudo systemctl stop hostapd
sudo systemctl stop dnsmasq

# Connect to WiFi using nmcli
sudo nmcli device wifi connect "YourNetworkName" password "YourPassword"

# Or if you have a saved connection
sudo nmcli connection up "YourNetworkName"
```

### Method 3: Via API Endpoints (If Web Interface Works)

If the web interface is accessible but you can't use the UI:

```bash
# Connect to WiFi via API
curl -X POST http://192.168.4.1:5000/api/v3/wifi/connect \
  -H "Content-Type: application/json" \
  -d '{"ssid": "YourNetworkName", "password": "YourPassword"}'

# Disable AP mode
curl -X POST http://192.168.4.1:5000/api/v3/wifi/ap/disable
```

### Method 4: Direct Command Line (Physical Access)

If you have physical access to the Pi or a keyboard/monitor:

```bash
# Disable AP mode services
sudo systemctl stop hostapd
sudo systemctl stop dnsmasq

# Check available networks
nmcli device wifi list

# Connect to your network
sudo nmcli device wifi connect "YourNetworkName" password "YourPassword"

# Verify connection
nmcli device status
ip addr show wlan0
```

### Method 5: Using Saved Network Configuration

If you've previously connected to a network, it may be saved:

```bash
# List saved connections
nmcli connection show

# Activate a saved connection
sudo nmcli connection up "YourSavedConnectionName"

# Or by UUID
sudo nmcli connection up <uuid>
```

## Step-by-Step Recovery Procedure

### Scenario 1: Still Connected to AP Network

If you're still connected to "LEDMatrix-Setup":

1. **Access web interface:**
   ```
   http://192.168.4.1:5000
   ```

2. **Go to WiFi tab**

3. **Connect to your network** using the interface

4. **Wait for connection** - you'll be disconnected from AP

5. **Reconnect to your new network** and access Pi at its new IP

### Scenario 2: Can't Access Web Interface

If web interface is not accessible:

1. **SSH into Pi** (if possible):
   ```bash
   ssh user@192.168.4.1  # Via AP
   # OR via Ethernet if connected
   ```

2. **Disable AP mode:**
   ```bash
   sudo systemctl stop hostapd dnsmasq
   ```

3. **Connect to WiFi:**
   ```bash
   sudo nmcli device wifi connect "YourNetwork" password "YourPassword"
   ```

4. **Verify connection:**
   ```bash
   nmcli device status
   ping -c 3 8.8.8.8  # Test internet connectivity
   ```

### Scenario 3: No Network Access at All

If you have no network access (AP not working, no Ethernet):

1. **Physical access required:**
   - Connect keyboard and monitor to Pi
   - Or use serial console if available

2. **Disable AP services:**
   ```bash
   sudo systemctl stop hostapd
   sudo systemctl stop dnsmasq
   sudo systemctl disable hostapd  # Prevent auto-start
   sudo systemctl disable dnsmasq
   ```

3. **Connect to WiFi manually:**
   ```bash
   sudo nmcli device wifi list
   sudo nmcli device wifi connect "YourNetwork" password "YourPassword"
   ```

4. **Restart network services if needed:**
   ```bash
   sudo systemctl restart NetworkManager
   ```

## Emergency Recovery Script

Create this script for quick recovery:

```bash
#!/bin/bash
# emergency_reconnect.sh - Emergency WiFi reconnection script

echo "Emergency WiFi Reconnection"
echo "=========================="

# Stop AP mode
echo "Stopping AP mode..."
sudo systemctl stop hostapd 2>/dev/null
sudo systemctl stop dnsmasq 2>/dev/null

# List available networks
echo ""
echo "Available networks:"
nmcli device wifi list

# Prompt for network
echo ""
read -p "Enter network SSID: " SSID
read -sp "Enter password: " PASSWORD
echo ""

# Connect
echo "Connecting to $SSID..."
sudo nmcli device wifi connect "$SSID" password "$PASSWORD"

# Wait a moment
sleep 3

# Check status
if nmcli device status | grep -q "connected"; then
    echo "✓ Connected successfully!"
    IP=$(ip addr show wlan0 | grep "inet " | awk '{print $2}' | cut -d/ -f1)
    echo "IP Address: $IP"
else
    echo "✗ Connection failed. Check credentials and try again."
fi
```

Save as `scripts/emergency_reconnect.sh` and make executable:
```bash
chmod +x scripts/emergency_reconnect.sh
sudo ./scripts/emergency_reconnect.sh
```

## Preventing Issues

### Before Testing

1. **Save your current network connection:**
   ```bash
   # Your network should already be saved if you've connected before
   nmcli connection show
   ```

2. **Note your Pi's IP address** on your normal network:
   ```bash
   hostname -I
   ```

3. **Ensure you have alternative access:**
   - Ethernet cable (if available)
   - SSH access via another method
   - Physical access to Pi

### During Testing

1. **Keep a terminal/SSH session open** to the Pi
2. **Test from a secondary device** (not your main computer)
3. **Have the recovery commands ready**

### After Testing

1. **Verify internet connectivity:**
   ```bash
   ping -c 3 8.8.8.8
   curl -I https://www.google.com
   ```

2. **Check Pi's new IP address:**
   ```bash
   hostname -I
   ip addr show wlan0
   ```

3. **Update your SSH/config** if IP changed

## Troubleshooting Reconnection

### Issue: Can't Connect to Saved Network

**Solution:**
```bash
# Remove old connection and reconnect
nmcli connection delete "NetworkName"
sudo nmcli device wifi connect "NetworkName" password "Password"
```

### Issue: AP Mode Won't Disable

**Solution:**
```bash
# Force stop services
sudo systemctl stop hostapd dnsmasq
sudo systemctl disable hostapd dnsmasq

# Kill processes if needed
sudo pkill hostapd
sudo pkill dnsmasq

# Restart NetworkManager
sudo systemctl restart NetworkManager
```

### Issue: WiFi Interface Stuck

**Solution:**
```bash
# Reset WiFi interface
sudo nmcli radio wifi off
sleep 2
sudo nmcli radio wifi on
sleep 3

# Try connecting again
sudo nmcli device wifi connect "NetworkName" password "Password"
```

### Issue: No Networks Found

**Solution:**
```bash
# Check WiFi is enabled
nmcli radio wifi

# Enable if off
sudo nmcli radio wifi on

# Check interface status
ip link show wlan0

# Restart NetworkManager
sudo systemctl restart NetworkManager
```

## Quick Reference Commands

```bash
# Disable AP mode
sudo systemctl stop hostapd dnsmasq

# List WiFi networks
nmcli device wifi list

# Connect to network
sudo nmcli device wifi connect "SSID" password "Password"

# Check connection status
nmcli device status

# Get IP address
hostname -I
ip addr show wlan0

# Test internet
ping -c 3 8.8.8.8

# Restart network services
sudo systemctl restart NetworkManager
```

## Best Practices

1. **Always test from a secondary device** - Keep your main computer on your normal network
2. **Have Ethernet backup** - If available, keep Ethernet connected as fallback
3. **Save network credentials** - Ensure your network is saved before testing
4. **Document your Pi's IP** - Note the IP on your normal network before testing
5. **Keep SSH session open** - Maintain an active SSH connection during testing
6. **Test during safe times** - Don't test when you need immediate internet access

## Recovery Checklist

- [ ] Stop AP mode services (hostapd, dnsmasq)
- [ ] Verify WiFi interface is available
- [ ] Scan for available networks
- [ ] Connect to your network
- [ ] Verify connection status
- [ ] Test internet connectivity
- [ ] Note new IP address
- [ ] Update any configurations that reference old IP

