#!/bin/bash
# Emergency script to fix internet connectivity issues
# Run this if the Pi can't access the internet after AP mode testing

echo "=========================================="
echo "Internet Connectivity Fix Script"
echo "=========================================="
echo ""

# 1. Disable IP forwarding
echo "1. Disabling IP forwarding..."
sudo sysctl -w net.ipv4.ip_forward=0
echo "   ✓ IP forwarding disabled"

# 2. Stop and disable dnsmasq (if it's interfering)
echo ""
echo "2. Checking dnsmasq..."
if sudo systemctl is-active dnsmasq > /dev/null 2>&1; then
    echo "   ⚠ dnsmasq is running - stopping it..."
    sudo systemctl stop dnsmasq
    sudo systemctl disable dnsmasq
    echo "   ✓ dnsmasq stopped"
else
    echo "   ✓ dnsmasq is not running"
fi

# 3. Restore dnsmasq config if backup exists
echo ""
echo "3. Checking dnsmasq config..."
if [ -f /etc/dnsmasq.conf.backup ]; then
    echo "   ⚠ Found backup config - restoring..."
    sudo cp /etc/dnsmasq.conf.backup /etc/dnsmasq.conf
    echo "   ✓ Config restored"
else
    echo "   ✓ No backup config found (normal)"
fi

# 4. Restart NetworkManager to restore DNS
echo ""
echo "4. Restarting NetworkManager..."
sudo systemctl restart NetworkManager
sleep 2
echo "   ✓ NetworkManager restarted"

# 5. Check DNS resolution
echo ""
echo "5. Testing DNS resolution..."
if nslookup google.com > /dev/null 2>&1; then
    echo "   ✓ DNS resolution working"
else
    echo "   ✗ DNS resolution failed"
    echo "   → Try: sudo systemctl restart systemd-resolved"
fi

# 6. Test internet connectivity
echo ""
echo "6. Testing internet connectivity..."
if ping -c 2 8.8.8.8 > /dev/null 2>&1; then
    echo "   ✓ Internet connectivity working"
else
    echo "   ✗ Internet connectivity failed"
    echo "   → Check: ip route show"
    echo "   → Check: ip addr show"
fi

# 7. Check if AP mode is still active
echo ""
echo "7. Checking AP mode status..."
if sudo systemctl is-active hostapd > /dev/null 2>&1; then
    echo "   ⚠ AP mode is still active!"
    echo "   → To disable: cd ~/LEDMatrix && python3 -c 'from src.wifi_manager import WiFiManager; wm = WiFiManager(); wm.disable_ap_mode()'"
else
    echo "   ✓ AP mode is not active"
fi

# 8. Remove any leftover iptables rules
echo ""
echo "8. Checking iptables rules..."
if command -v iptables > /dev/null 2>&1; then
    # Try to remove any port 80 redirect rules
    sudo iptables -t nat -D PREROUTING -i wlan0 -p tcp --dport 80 -j REDIRECT --to-port 5000 2>/dev/null
    sudo iptables -D INPUT -i wlan0 -p tcp --dport 80 -j ACCEPT 2>/dev/null
    echo "   ✓ Cleaned up iptables rules (if any existed)"
else
    echo "   → iptables not available (normal on some systems)"
fi

echo ""
echo "=========================================="
echo "Fix Complete"
echo "=========================================="
echo ""
echo "If internet still doesn't work:"
echo "1. Check: ip route show"
echo "2. Check: cat /etc/resolv.conf"
echo "3. Restart network: sudo systemctl restart NetworkManager"
echo "4. Check Ethernet connection: ip link show eth0"
echo ""











