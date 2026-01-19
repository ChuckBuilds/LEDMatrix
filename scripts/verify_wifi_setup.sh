#!/bin/bash
# WiFi Setup Verification Script
# Comprehensive health check for WiFi management system

set -u  # Fail on undefined variables

echo "=========================================="
echo "WiFi Setup Verification"
echo "=========================================="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
PASSED=0
FAILED=0
WARNINGS=0

check_pass() {
    echo -e "${GREEN}✓${NC} $1"
    PASSED=$((PASSED + 1))
}

check_fail() {
    echo -e "${RED}✗${NC} $1"
    FAILED=$((FAILED + 1))
}

check_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
    WARNINGS=$((WARNINGS + 1))
}

info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

# Determine project root
if [ -f "run.py" ]; then
    PROJECT_ROOT="$(pwd)"
elif [ -f "../run.py" ]; then
    PROJECT_ROOT="$(cd .. && pwd)"
else
    echo "Error: Could not find project root. Please run this script from the LEDMatrix directory."
    exit 1
fi

echo "Project root: $PROJECT_ROOT"
echo ""

# 1. Check required packages
echo "=== Required Packages ==="
PACKAGES=("nmcli" "hostapd" "dnsmasq")
MISSING_PACKAGES=()

for pkg in "${PACKAGES[@]}"; do
    if command -v "$pkg" >/dev/null 2>&1; then
        check_pass "$pkg is installed"
    else
        check_fail "$pkg is NOT installed"
        MISSING_PACKAGES+=("$pkg")
    fi
done

if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    echo ""
    info "To install missing packages:"
    echo "  sudo apt update && sudo apt install -y ${MISSING_PACKAGES[*]}"
fi
echo ""

# 2. Check WiFi monitor service
echo "=== WiFi Monitor Service ==="
if systemctl list-unit-files | grep -q "ledmatrix-wifi-monitor.service"; then
    check_pass "WiFi monitor service is installed"
    
    if systemctl is-enabled --quiet ledmatrix-wifi-monitor.service 2>/dev/null; then
        check_pass "WiFi monitor service is enabled"
    else
        check_warn "WiFi monitor service is installed but not enabled"
        info "To enable: sudo systemctl enable ledmatrix-wifi-monitor.service"
    fi
    
    if systemctl is-active --quiet ledmatrix-wifi-monitor.service 2>/dev/null; then
        check_pass "WiFi monitor service is running"
    else
        check_warn "WiFi monitor service is not running"
        info "To start: sudo systemctl start ledmatrix-wifi-monitor.service"
        info "Check logs: sudo journalctl -u ledmatrix-wifi-monitor -n 50"
    fi
else
    check_fail "WiFi monitor service is NOT installed"
    info "To install: sudo $PROJECT_ROOT/scripts/install/install_wifi_monitor.sh"
fi
echo ""

# 3. Check WiFi configuration file
echo "=== Configuration Files ==="
WIFI_CONFIG="$PROJECT_ROOT/config/wifi_config.json"
if [ -f "$WIFI_CONFIG" ]; then
    check_pass "WiFi config file exists: $WIFI_CONFIG"
    
    # Check if JSON is valid
    if python3 -m json.tool "$WIFI_CONFIG" >/dev/null 2>&1; then
        check_pass "WiFi config file is valid JSON"
        
        # Check for required fields
        if grep -q "ap_ssid" "$WIFI_CONFIG"; then
            AP_SSID=$(python3 -c "import json; print(json.load(open('$WIFI_CONFIG')).get('ap_ssid', 'N/A'))" 2>/dev/null)
            info "AP SSID: $AP_SSID"
        else
            check_warn "ap_ssid not found in config"
        fi
        
        if grep -q "auto_enable_ap_mode" "$WIFI_CONFIG"; then
            AUTO_ENABLE=$(python3 -c "import json; print(json.load(open('$WIFI_CONFIG')).get('auto_enable_ap_mode', 'N/A'))" 2>/dev/null)
            info "Auto-enable AP mode: $AUTO_ENABLE"
        else
            check_warn "auto_enable_ap_mode not found in config"
        fi
    else
        check_fail "WiFi config file is NOT valid JSON"
    fi
else
    check_warn "WiFi config file does not exist (will be created on first use)"
fi
echo ""

# 4. Check WiFi permissions
echo "=== WiFi Permissions ==="
if [ -f "/etc/sudoers.d/ledmatrix_wifi" ]; then
    check_pass "WiFi sudoers file exists"
    
    # Check if file is readable
    if sudo -n test -r "/etc/sudoers.d/ledmatrix_wifi" 2>/dev/null; then
        check_pass "WiFi sudoers file is readable"
    else
        check_warn "WiFi sudoers file may not be readable"
    fi
else
    check_warn "WiFi sudoers file does not exist"
    info "To configure: $PROJECT_ROOT/scripts/install/configure_wifi_permissions.sh"
fi

if [ -f "/etc/polkit-1/rules.d/10-ledmatrix-wifi.rules" ]; then
    check_pass "WiFi PolicyKit rule exists"
else
    check_warn "WiFi PolicyKit rule does not exist"
    info "To configure: $PROJECT_ROOT/scripts/install/configure_wifi_permissions.sh"
fi
echo ""

# 5. Check WiFi interface
echo "=== WiFi Interface ==="
if ip link show wlan0 >/dev/null 2>&1; then
    check_pass "WiFi interface wlan0 exists"
    
    # Check if interface is up
    if ip link show wlan0 | grep -q "state UP"; then
        check_pass "WiFi interface wlan0 is UP"
    else
        check_warn "WiFi interface wlan0 is DOWN"
    fi
else
    check_fail "WiFi interface wlan0 does NOT exist"
    info "Check if WiFi adapter is connected (USB WiFi or built-in)"
fi
echo ""

# 6. Check WiFi radio status
echo "=== WiFi Radio Status ==="
if command -v nmcli >/dev/null 2>&1; then
    WIFI_RADIO=$(nmcli radio wifi 2>/dev/null || echo "unknown")
    if echo "$WIFI_RADIO" | grep -qi "enabled"; then
        check_pass "WiFi radio is enabled"
    elif echo "$WIFI_RADIO" | grep -qi "disabled"; then
        check_warn "WiFi radio is disabled"
        info "To enable: sudo nmcli radio wifi on"
    else
        check_warn "WiFi radio status unknown: $WIFI_RADIO"
    fi
elif command -v rfkill >/dev/null 2>&1; then
    RFKILL_WIFI=$(rfkill list wifi 2>/dev/null || echo "")
    if echo "$RFKILL_WIFI" | grep -q "Soft blocked: yes"; then
        check_warn "WiFi is soft-blocked"
        info "To unblock: sudo rfkill unblock wifi"
    elif echo "$RFKILL_WIFI" | grep -q "Hard blocked: yes"; then
        check_fail "WiFi is hard-blocked (hardware switch)"
    else
        check_pass "WiFi is not blocked"
    fi
else
    check_warn "Cannot check WiFi radio status (nmcli and rfkill not available)"
fi
echo ""

# 7. Check current WiFi connection
echo "=== Current WiFi Status ==="
if command -v nmcli >/dev/null 2>&1; then
    WIFI_STATUS=$(nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null | grep -E "wifi|wlan0" || echo "")
    if echo "$WIFI_STATUS" | grep -q "connected"; then
        SSID=$(nmcli -t -f active,ssid device wifi 2>/dev/null | grep "^yes:" | cut -d: -f2 | head -1)
        IP=$(nmcli -t -f IP4.ADDRESS device show wlan0 2>/dev/null | cut -d: -f2 | cut -d/ -f1 | head -1)
        SIGNAL=$(nmcli -t -f WIFI.SIGNAL device show wlan0 2>/dev/null | cut -d: -f2 | head -1)
        check_pass "WiFi is connected"
        info "SSID: $SSID"
        info "IP Address: $IP"
        info "Signal: $SIGNAL%"
    else
        check_warn "WiFi is not connected"
    fi
elif command -v iwconfig >/dev/null 2>&1; then
    if iwconfig wlan0 2>/dev/null | grep -q "ESSID:"; then
        SSID=$(iwconfig wlan0 2>/dev/null | grep -oP 'ESSID:"\K[^"]*')
        check_pass "WiFi is connected to: $SSID"
    else
        check_warn "WiFi is not connected"
    fi
else
    check_warn "Cannot check WiFi connection status"
fi
echo ""

# 8. Check Ethernet connection
echo "=== Ethernet Status ==="
ETH_CONNECTED=false
if command -v nmcli >/dev/null 2>&1; then
    ETH_STATUS=$(nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null | grep -E "ethernet|eth" | grep "connected" || echo "")
    if [ -n "$ETH_STATUS" ]; then
        ETH_CONNECTED=true
        check_pass "Ethernet is connected"
    else
        check_warn "Ethernet is not connected"
    fi
elif command -v ip >/dev/null 2>&1; then
    if ip addr show eth0 2>/dev/null | grep -q "inet " || ip addr show 2>/dev/null | grep -E "eth|enp" | grep -q "inet "; then
        ETH_CONNECTED=true
        check_pass "Ethernet appears to be connected"
    else
        check_warn "Ethernet does not appear to be connected"
    fi
fi
echo ""

# 9. Check AP mode status
echo "=== AP Mode Status ==="
AP_ACTIVE=false

# Check hostapd
if systemctl is-active --quiet hostapd 2>/dev/null; then
    AP_ACTIVE=true
    check_warn "AP mode is ACTIVE (hostapd running)"
    info "SSID: LEDMatrix-Setup (from config)"
    info "IP: 192.168.4.1"
elif systemctl is-active --quiet dnsmasq 2>/dev/null; then
    # dnsmasq might be running for other purposes, check if it's configured for AP
    if grep -q "interface=wlan0" /etc/dnsmasq.conf 2>/dev/null; then
        AP_ACTIVE=true
        check_warn "AP mode appears to be active (dnsmasq configured for wlan0)"
    fi
fi

# Check nmcli hotspot
if command -v nmcli >/dev/null 2>&1; then
    HOTSPOT=$(nmcli -t -f NAME,TYPE connection show --active 2>/dev/null | grep -i hotspot || echo "")
    if [ -n "$HOTSPOT" ]; then
        AP_ACTIVE=true
        CONN_NAME=$(echo "$HOTSPOT" | cut -d: -f1)
        check_warn "AP mode is ACTIVE (nmcli hotspot: $CONN_NAME)"
        info "IP: 192.168.4.1"
    fi
fi

if [ "$AP_ACTIVE" = false ]; then
    check_pass "AP mode is not active"
fi
echo ""

# 10. Check WiFi Manager Python module
echo "=== WiFi Manager Module ==="
if python3 -c "from src.wifi_manager import WiFiManager" 2>/dev/null; then
    check_pass "WiFi Manager module can be imported"
    
    # Try to instantiate (but don't fail if it errors - may need config)
    if python3 -c "import sys; sys.path.insert(0, '$PROJECT_ROOT'); from src.wifi_manager import WiFiManager; wm = WiFiManager(); print('OK')" 2>/dev/null; then
        check_pass "WiFi Manager can be instantiated"
    else
        check_warn "WiFi Manager instantiation failed (may be expected)"
    fi
else
    check_fail "WiFi Manager module cannot be imported"
fi
echo ""

# 11. Check web interface WiFi API
echo "=== Web Interface WiFi API ==="
if systemctl is-active --quiet ledmatrix-web.service 2>/dev/null; then
    # Try to test the WiFi status API endpoint
    if curl -s -f "http://localhost:5001/api/v3/wifi/status" >/dev/null 2>&1; then
        check_pass "WiFi status API endpoint is accessible"
    else
        check_warn "WiFi status API endpoint is not accessible (may be expected if web interface requires auth)"
    fi
else
    check_warn "Web interface service is not running (cannot test API)"
fi
echo ""

# Summary
echo "=========================================="
echo "Summary"
echo "=========================================="
echo -e "${GREEN}Passed: $PASSED${NC}"
echo -e "${YELLOW}Warnings: $WARNINGS${NC}"
echo -e "${RED}Failed: $FAILED${NC}"
echo ""

# Show connectivity summary
echo "=== Connectivity ==="
if [ "$ETH_CONNECTED" = true ]; then
    info "Ethernet: Connected"
else
    info "Ethernet: Not connected"
fi
if [ "$AP_ACTIVE" = true ]; then
    info "AP Mode: Active"
else
    info "AP Mode: Inactive"
fi
echo ""

if [ $FAILED -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✓ All checks passed! WiFi setup looks good.${NC}"
    exit 0
elif [ $FAILED -eq 0 ]; then
    echo -e "${YELLOW}⚠ Setup looks mostly good, but there are some warnings.${NC}"
    exit 0
else
    echo -e "${RED}✗ Some checks failed. Please review the issues above.${NC}"
    exit 1
fi
