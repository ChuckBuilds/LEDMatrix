#!/bin/bash

# LED Matrix Installation Verification Script
# This script verifies that the installation was successful

set -e

echo "=========================================="
echo "LED Matrix Installation Verification"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Track overall status
ALL_PASSED=true

check_pass() {
    echo -e "${GREEN}✓${NC} $1"
}

check_fail() {
    echo -e "${RED}✗${NC} $1"
    ALL_PASSED=false
}

check_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
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

# 1. Check systemd services
echo "=== Systemd Services ==="
services=("ledmatrix.service" "ledmatrix-web.service" "ledmatrix-wifi-monitor.service")
for service in "${services[@]}"; do
    if systemctl list-unit-files | grep -q "$service"; then
        if systemctl is-active --quiet "$service" 2>/dev/null; then
            check_pass "$service is installed and running"
        elif systemctl is-enabled --quiet "$service" 2>/dev/null; then
            check_warn "$service is installed and enabled but not running"
        else
            check_warn "$service is installed but not enabled"
        fi
    else
        check_fail "$service is not installed"
    fi
done
echo ""

# 2. Check Python dependencies
echo "=== Python Dependencies ==="
if python3 -c "from rgbmatrix import RGBMatrix, RGBMatrixOptions" 2>/dev/null; then
    check_pass "rpi-rgb-led-matrix is installed"
else
    check_fail "rpi-rgb-led-matrix is not installed"
fi

# Check key Python packages
packages=("flask" "requests" "PIL" "numpy")
for pkg in "${packages[@]}"; do
    if python3 -c "import $pkg" 2>/dev/null; then
        check_pass "Python package '$pkg' is installed"
    else
        check_fail "Python package '$pkg' is not installed"
    fi
done
echo ""

# 3. Check configuration files
echo "=== Configuration Files ==="
if [ -f "$PROJECT_ROOT/config/config.json" ]; then
    check_pass "config/config.json exists"
else
    check_fail "config/config.json is missing"
fi

if [ -f "$PROJECT_ROOT/config/config_secrets.json" ]; then
    check_pass "config/config_secrets.json exists"
else
    check_warn "config/config_secrets.json is missing (may be optional)"
fi
echo ""

# 4. Check file permissions
echo "=== File Permissions ==="
if [ -d "$PROJECT_ROOT/assets" ]; then
    if [ -w "$PROJECT_ROOT/assets" ]; then
        check_pass "assets directory is writable"
    else
        check_warn "assets directory may not be writable"
    fi
else
    check_fail "assets directory is missing"
fi

if [ -d "/var/cache/ledmatrix" ]; then
    if [ -w "/var/cache/ledmatrix" ]; then
        check_pass "cache directory exists and is writable"
    else
        check_warn "cache directory exists but may not be writable"
    fi
else
    check_warn "cache directory is missing (will be created on first run)"
fi
echo ""

# 5. Check web interface
echo "=== Web Interface ==="
if [ -f "$PROJECT_ROOT/web_interface_v2.py" ]; then
    check_pass "web_interface_v2.py exists"
else
    check_fail "web_interface_v2.py is missing"
fi

# Check if web service is listening
if systemctl is-active --quiet ledmatrix-web.service 2>/dev/null; then
    if netstat -tuln 2>/dev/null | grep -q ":5001" || ss -tuln 2>/dev/null | grep -q ":5001"; then
        check_pass "Web interface is listening on port 5001"
    else
        check_warn "Web service is running but port 5001 may not be listening"
    fi
else
    check_warn "Web service is not running (cannot check port)"
fi
echo ""

# 6. Check network connectivity
echo "=== Network Status ==="
if ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1; then
    check_pass "Internet connectivity is available"
else
    check_warn "Internet connectivity check failed (may be normal if WiFi is disconnected)"
fi

# Check WiFi status
if command -v nmcli >/dev/null 2>&1; then
    WIFI_STATUS=$(nmcli device status 2>/dev/null | grep wlan0 || echo "")
    if echo "$WIFI_STATUS" | grep -q "connected"; then
        SSID=$(nmcli -t -f active,ssid dev wifi | grep '^yes:' | cut -d: -f2 | head -1)
        check_pass "WiFi is connected to: $SSID"
    else
        check_warn "WiFi is not connected (AP mode may be active)"
    fi
elif command -v iwconfig >/dev/null 2>&1; then
    if iwconfig wlan0 2>/dev/null | grep -q "ESSID"; then
        SSID=$(iwconfig wlan0 2>/dev/null | grep -oP 'ESSID:"\K[^"]*')
        check_pass "WiFi is connected to: $SSID"
    else
        check_warn "WiFi connection status unknown"
    fi
fi

# Check AP mode
if systemctl is-active --quiet hostapd 2>/dev/null; then
    check_warn "AP mode is active (hostapd running) - SSH may be unavailable via WiFi"
elif systemctl is-active --quiet ledmatrix-wifi-monitor.service 2>/dev/null; then
    # Check if AP mode might be active via WiFi manager
    check_warn "WiFi monitor is running - may enable AP mode if WiFi disconnects"
fi
echo ""

# 7. Check sudoers configuration
echo "=== Sudo Configuration ==="
if [ -f "/etc/sudoers.d/ledmatrix_web" ]; then
    check_pass "Web interface sudoers file exists"
else
    check_warn "Web interface sudoers file is missing (web interface may require passwords)"
fi
echo ""

# 8. Check service logs for errors
echo "=== Recent Service Logs ==="
for service in "${services[@]}"; do
    if systemctl list-unit-files | grep -q "$service"; then
        ERRORS=$(journalctl -u "$service" --since "10 minutes ago" --no-pager 2>/dev/null | grep -i "error\|failed\|exception" | wc -l || echo "0")
        if [ "$ERRORS" -gt 0 ]; then
            check_warn "$service has $ERRORS error(s) in last 10 minutes (check logs)"
        else
            check_pass "$service logs show no recent errors"
        fi
    fi
done
echo ""

# Summary
echo "=========================================="
if [ "$ALL_PASSED" = true ]; then
    echo -e "${GREEN}Installation verification PASSED${NC}"
    echo ""
    echo "Next steps:"
    echo "1. Access the web interface at: http://$(hostname -I | awk '{print $1}'):5001"
    echo "2. Check service status: sudo systemctl status ledmatrix.service"
    echo "3. View logs: journalctl -u ledmatrix.service -f"
    exit 0
else
    echo -e "${YELLOW}Installation verification completed with warnings${NC}"
    echo ""
    echo "Some checks failed. Please review the output above."
    echo "Common fixes:"
    echo "- Re-run first_time_install.sh if services are missing"
    echo "- Check service logs: journalctl -u <service-name> -f"
    echo "- Ensure you're logged in after group changes: newgrp systemd-journal"
    exit 1
fi

