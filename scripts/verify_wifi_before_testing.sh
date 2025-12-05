#!/bin/bash
# Pre-Testing WiFi Verification Script
# Run this BEFORE disconnecting Ethernet to ensure WiFi is ready

set -e

echo "=========================================="
echo "WiFi Pre-Testing Verification"
echo "=========================================="
echo ""
echo "This script verifies WiFi is enabled and working"
echo "before you disconnect Ethernet for captive portal testing."
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check counter
PASSED=0
FAILED=0
WARNINGS=0

check_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $2"
        ((PASSED++))
    else
        echo -e "${RED}✗${NC} $2"
        ((FAILED++))
    fi
}

warn_result() {
    echo -e "${YELLOW}⚠${NC} $2"
    ((WARNINGS++))
}

# Check 1: WiFi interface exists
echo "1. Checking WiFi interface..."
if ip link show wlan0 > /dev/null 2>&1; then
    check_result 0 "WiFi interface wlan0 exists"
else
    check_result 1 "WiFi interface wlan0 NOT found"
    echo "   → Check if WiFi adapter is connected"
    echo "   → Run: lsusb (for USB WiFi) or check built-in WiFi"
    exit 1
fi

# Check 2: WiFi radio is enabled
echo ""
echo "2. Checking WiFi radio status..."
WIFI_STATUS=$(nmcli radio wifi 2>/dev/null || echo "unknown")
if echo "$WIFI_STATUS" | grep -qi "enabled"; then
    check_result 0 "WiFi radio is enabled"
elif echo "$WIFI_STATUS" | grep -qi "disabled"; then
    check_result 1 "WiFi radio is DISABLED"
    echo "   → Enabling WiFi..."
    sudo nmcli radio wifi on
    sleep 2
    if nmcli radio wifi | grep -qi "enabled"; then
        check_result 0 "WiFi radio enabled successfully"
    else
        check_result 1 "Failed to enable WiFi radio"
        exit 1
    fi
else
    warn_result 1 "Could not determine WiFi radio status"
fi

# Check 3: WiFi can scan for networks
echo ""
echo "3. Testing WiFi scanning capability..."
SCAN_RESULT=$(timeout 10 nmcli device wifi list 2>&1 | head -5)
if [ $? -eq 0 ] && [ -n "$SCAN_RESULT" ]; then
    NETWORK_COUNT=$(echo "$SCAN_RESULT" | wc -l)
    if [ "$NETWORK_COUNT" -gt 1 ]; then
        check_result 0 "WiFi scanning works (found networks)"
        echo "   Sample networks found:"
        echo "$SCAN_RESULT" | head -3 | sed 's/^/   /'
    else
        warn_result 1 "WiFi scanning works but no networks found"
        echo "   → This might be okay if you're in a remote location"
        echo "   → Make sure you can see networks when you need to connect"
    fi
else
    check_result 1 "WiFi scanning FAILED"
    echo "   → WiFi adapter may not be working properly"
    echo "   → Check: dmesg | grep -i wifi"
    exit 1
fi

# Check 4: Current network connections
echo ""
echo "4. Checking current network status..."
ETH_STATUS=$(nmcli device status | grep "ethernet" | grep -v "unavailable" | head -1 || echo "")
WIFI_STATUS=$(nmcli device status | grep "wifi" | head -1 || echo "")

if echo "$ETH_STATUS" | grep -q "connected"; then
    ETH_NAME=$(echo "$ETH_STATUS" | awk '{print $1}')
    ETH_IP=$(ip addr show $ETH_NAME 2>/dev/null | grep "inet " | awk '{print $2}' | cut -d/ -f1 | head -1)
    check_result 0 "Ethernet is connected ($ETH_NAME)"
    if [ -n "$ETH_IP" ]; then
        echo "   Ethernet IP: $ETH_IP"
    fi
else
    warn_result 1 "Ethernet is NOT connected"
    echo "   → You may already be on WiFi only"
fi

if echo "$WIFI_STATUS" | grep -q "connected"; then
    WIFI_NAME=$(echo "$WIFI_STATUS" | awk '{print $1}')
    WIFI_IP=$(ip addr show $WIFI_NAME 2>/dev/null | grep "inet " | awk '{print $2}' | cut -d/ -f1 | head -1)
    WIFI_SSID=$(nmcli -t -f active,ssid dev wifi | grep "^yes:" | cut -d: -f2 | head -1)
    check_result 0 "WiFi is connected ($WIFI_NAME)"
    if [ -n "$WIFI_SSID" ]; then
        echo "   Connected to: $WIFI_SSID"
    fi
    if [ -n "$WIFI_IP" ]; then
        echo "   WiFi IP: $WIFI_IP"
    fi
    echo ""
    echo "   ⚠ You are already connected via WiFi!"
    echo "   → You may want to disconnect WiFi first to test captive portal"
    echo "   → Or test from a different device"
else
    if echo "$WIFI_STATUS" | grep -q "disconnected"; then
        check_result 0 "WiFi is disconnected (ready for AP mode)"
    else
        warn_result 1 "WiFi status unclear"
    fi
fi

# Check 5: Internet connectivity test
echo ""
echo "5. Testing internet connectivity..."
if ping -c 2 -W 3 8.8.8.8 > /dev/null 2>&1; then
    check_result 0 "Internet connectivity working"
    echo "   → You have internet access via current connection"
else
    warn_result 1 "No internet connectivity detected"
    echo "   → This might be okay if you're testing in isolation"
    echo "   → But you won't be able to download packages if needed"
fi

# Check 6: Saved WiFi connections
echo ""
echo "6. Checking saved WiFi connections..."
SAVED_CONNECTIONS=$(nmcli connection show | grep -i wifi | wc -l)
if [ "$SAVED_CONNECTIONS" -gt 0 ]; then
    check_result 0 "Found $SAVED_CONNECTIONS saved WiFi connection(s)"
    echo "   Saved connections:"
    nmcli connection show | grep -i wifi | awk '{print "   - " $1}' | head -5
    echo ""
    echo "   → You can reconnect using: sudo nmcli connection up <name>"
else
    warn_result 1 "No saved WiFi connections found"
    echo "   → Make sure you know your WiFi SSID and password"
    echo "   → You'll need them to reconnect after testing"
fi

# Check 7: Required services
echo ""
echo "7. Checking required services..."
if systemctl is-active --quiet hostapd 2>/dev/null; then
    warn_result 1 "hostapd is already running (AP mode may be active)"
else
    check_result 0 "hostapd service is stopped (normal)"
fi

if systemctl is-active --quiet dnsmasq 2>/dev/null; then
    warn_result 1 "dnsmasq is already running (AP mode may be active)"
else
    check_result 0 "dnsmasq service is stopped (normal)"
fi

# Check 8: WiFi monitor service
echo ""
echo "8. Checking WiFi monitor service..."
if systemctl is-active --quiet ledmatrix-wifi-monitor 2>/dev/null; then
    check_result 0 "WiFi monitor service is running"
else
    warn_result 1 "WiFi monitor service is NOT running"
    echo "   → Start with: sudo systemctl start ledmatrix-wifi-monitor"
fi

# Summary
echo ""
echo "=========================================="
echo "Verification Summary"
echo "=========================================="
echo -e "${GREEN}Passed: ${PASSED}${NC}"
echo -e "${YELLOW}Warnings: ${WARNINGS}${NC}"
echo -e "${RED}Failed: ${FAILED}${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    if [ $WARNINGS -eq 0 ]; then
        echo -e "${GREEN}✓ All checks passed! WiFi is ready for testing.${NC}"
        echo ""
        echo "Next steps:"
        echo "1. You can safely disconnect Ethernet"
        echo "2. Enable AP mode to test captive portal"
        echo "3. Use emergency_reconnect.sh if you need to reconnect"
    else
        echo -e "${YELLOW}⚠ Checks passed with warnings.${NC}"
        echo ""
        echo "WiFi appears ready, but review warnings above."
        echo "You can proceed with testing, but be aware of the warnings."
    fi
    exit 0
else
    echo -e "${RED}✗ Some checks failed. Please fix issues before testing.${NC}"
    echo ""
    echo "Do NOT disconnect Ethernet until all issues are resolved!"
    exit 1
fi

