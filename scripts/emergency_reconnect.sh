#!/bin/bash
# Emergency WiFi Reconnection Script
# Use this if captive portal testing fails and you need to reconnect to your network

set -e

echo "=========================================="
echo "Emergency WiFi Reconnection"
echo "=========================================="
echo ""

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then 
    echo "This script requires sudo privileges."
    echo "Please run: sudo $0"
    exit 1
fi

# Stop AP mode services
echo "1. Stopping AP mode services..."
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true
echo "   ✓ AP mode services stopped"
echo ""

# Check WiFi interface
echo "2. Checking WiFi interface..."
if ! ip link show wlan0 > /dev/null 2>&1; then
    echo "   ✗ WiFi interface wlan0 not found"
    echo "   Please check your WiFi adapter"
    exit 1
fi

# Enable WiFi if disabled
if ! nmcli radio wifi | grep -q "enabled"; then
    echo "   Enabling WiFi..."
    nmcli radio wifi on
    sleep 2
fi
echo "   ✓ WiFi interface ready"
echo ""

# List available networks
echo "3. Scanning for available networks..."
echo ""
nmcli device wifi list
echo ""

# Prompt for network credentials
read -p "Enter network SSID: " SSID
if [ -z "$SSID" ]; then
    echo "Error: SSID cannot be empty"
    exit 1
fi

read -sp "Enter password (leave empty for open networks): " PASSWORD
echo ""

# Connect to network
echo ""
echo "4. Connecting to '$SSID'..."
if [ -z "$PASSWORD" ]; then
    nmcli device wifi connect "$SSID"
else
    nmcli device wifi connect "$SSID" password "$PASSWORD"
fi

# Wait for connection
echo "   Waiting for connection..."
sleep 5

# Check connection status
echo ""
echo "5. Verifying connection..."
if nmcli device status | grep -q "wlan0.*connected"; then
    echo "   ✓ Connected successfully!"
    
    # Get IP address
    IP=$(ip addr show wlan0 2>/dev/null | grep "inet " | awk '{print $2}' | cut -d/ -f1 | head -1)
    if [ -n "$IP" ]; then
        echo "   IP Address: $IP"
    fi
    
    # Test internet connectivity
    echo ""
    echo "6. Testing internet connectivity..."
    if ping -c 2 -W 2 8.8.8.8 > /dev/null 2>&1; then
        echo "   ✓ Internet connection working!"
    else
        echo "   ⚠ Connected to WiFi but no internet access"
        echo "   Check your router/gateway configuration"
    fi
else
    echo "   ✗ Connection failed"
    echo ""
    echo "Troubleshooting:"
    echo "1. Verify SSID and password are correct"
    echo "2. Check if network is in range"
    echo "3. Try: nmcli device wifi list"
    echo "4. Check: nmcli device status"
    exit 1
fi

echo ""
echo "=========================================="
echo "Reconnection Complete!"
echo "=========================================="
echo ""
echo "You can now access the Pi at: http://${IP:-<check-ip>}:5000"
echo "Or via SSH: ssh user@${IP:-<check-ip>}"

