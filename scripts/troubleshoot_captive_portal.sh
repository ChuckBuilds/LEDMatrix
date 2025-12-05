#!/bin/bash
# Troubleshooting script for captive portal WiFi setup
# Run this when you can SSH back into the Pi

echo "=========================================="
echo "Captive Portal Troubleshooting"
echo "=========================================="
echo ""

echo "1. Checking AP mode status..."
if sudo systemctl is-active hostapd > /dev/null 2>&1; then
    echo "   ✓ hostapd is running"
else
    echo "   ✗ hostapd is NOT running"
fi

if sudo systemctl is-active dnsmasq > /dev/null 2>&1; then
    echo "   ✓ dnsmasq is running"
else
    echo "   ✗ dnsmasq is NOT running"
fi

echo ""
echo "2. Checking wlan0 IP address..."
WLAN_IP=$(ip addr show wlan0 | grep "inet 192.168.4.1" | awk '{print $2}')
if [ -n "$WLAN_IP" ]; then
    echo "   ✓ wlan0 has IP: $WLAN_IP"
else
    echo "   ✗ wlan0 does NOT have 192.168.4.1"
    echo "   → Fix: sudo ip addr add 192.168.4.1/24 dev wlan0"
fi

echo ""
echo "3. Checking web server status..."
if sudo systemctl is-active ledmatrix-web > /dev/null 2>&1; then
    echo "   ✓ Web server (ledmatrix-web) is running"
else
    echo "   ✗ Web server is NOT running"
    echo "   → Fix: sudo systemctl start ledmatrix-web"
fi

echo ""
echo "4. Checking if web server is listening on port 5000..."
if sudo netstat -tlnp 2>/dev/null | grep -q ":5000" || sudo ss -tlnp 2>/dev/null | grep -q ":5000"; then
    echo "   ✓ Web server is listening on port 5000"
    sudo netstat -tlnp 2>/dev/null | grep ":5000" || sudo ss -tlnp 2>/dev/null | grep ":5000"
else
    echo "   ✗ Web server is NOT listening on port 5000"
    echo "   → Web server may not be running or bound incorrectly"
fi

echo ""
echo "5. Testing web server locally..."
if curl -s http://localhost:5000/v3 > /dev/null 2>&1; then
    echo "   ✓ Web server responds locally"
else
    echo "   ✗ Web server does NOT respond locally"
    echo "   → Web server may have crashed or not started"
fi

echo ""
echo "6. Testing web server from AP IP..."
if curl -s http://192.168.4.1:5000/v3 > /dev/null 2>&1; then
    echo "   ✓ Web server responds on 192.168.4.1:5000"
else
    echo "   ✗ Web server does NOT respond on 192.168.4.1:5000"
fi

echo ""
echo "7. Checking firewall rules..."
if command -v ufw > /dev/null 2>&1; then
    UFW_STATUS=$(sudo ufw status | head -1)
    echo "   UFW Status: $UFW_STATUS"
    if echo "$UFW_STATUS" | grep -qi "active"; then
        echo "   → Check if port 5000 is allowed: sudo ufw allow 5000/tcp"
    fi
fi

if command -v iptables > /dev/null 2>&1; then
    echo "   Checking iptables rules for port 5000..."
    sudo iptables -L -n | grep -E "5000|ACCEPT.*wlan0" || echo "   → No specific rules found"
fi

echo ""
echo "8. Checking DNS resolution..."
if dig @192.168.4.1 google.com > /dev/null 2>&1; then
    echo "   ✓ DNS is working"
else
    echo "   ✗ DNS may not be working correctly"
fi

echo ""
echo "9. Checking captive portal detection endpoints..."
for endpoint in "hotspot-detect.html" "generate_204" "connecttest.txt" "success.txt"; do
    if curl -s http://192.168.4.1:5000/$endpoint > /dev/null 2>&1; then
        echo "   ✓ $endpoint responds"
    else
        echo "   ✗ $endpoint does NOT respond"
    fi
done

echo ""
echo "10. Recent web server logs..."
if sudo journalctl -u ledmatrix-web -n 10 --no-pager 2>/dev/null | head -5; then
    echo ""
else
    echo "   → No logs found or service not running"
fi

echo ""
echo "=========================================="
echo "Quick Fixes:"
echo "=========================================="
echo "1. Start web server: sudo systemctl start ledmatrix-web"
echo "2. Enable web server: sudo systemctl enable ledmatrix-web"
echo "3. Check web server logs: sudo journalctl -u ledmatrix-web -f"
echo "4. Restart AP mode: cd ~/LEDMatrix && python3 -c 'from src.wifi_manager import WiFiManager; wm = WiFiManager(); wm.disable_ap_mode(); wm.enable_ap_mode()'"
echo "5. Allow port 5000 in firewall: sudo ufw allow 5000/tcp"
echo ""

