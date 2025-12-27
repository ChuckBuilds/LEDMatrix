#!/bin/bash

# Test script for captive portal functionality
# This script tests the captive portal from a device connected to the AP network

set -e

PI_IP="192.168.4.1"
PI_PORT="5000"
BASE_URL="http://${PI_IP}:${PI_PORT}"

echo "=========================================="
echo "Captive Portal Functionality Test"
echo "=========================================="
echo ""
echo "Make sure you're connected to 'LEDMatrix-Setup' network"
echo "Pi IP: ${PI_IP}"
echo "Web Interface Port: ${PI_PORT}"
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counter
PASSED=0
FAILED=0

test_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $2"
        ((PASSED++))
    else
        echo -e "${RED}✗${NC} $2"
        ((FAILED++))
    fi
}

# Test 1: Check if Pi is reachable
echo "1. Testing Pi connectivity..."
if ping -c 1 -W 2 ${PI_IP} > /dev/null 2>&1; then
    test_result 0 "Pi is reachable at ${PI_IP}"
else
    test_result 1 "Pi is NOT reachable at ${PI_IP}"
    echo "   Make sure you're connected to LEDMatrix-Setup network"
    exit 1
fi

# Test 2: DNS Redirection
echo ""
echo "2. Testing DNS redirection..."
DNS_RESULT=$(nslookup google.com 2>/dev/null | grep -i "address" | tail -1 | awk '{print $2}')
if [ "$DNS_RESULT" = "${PI_IP}" ]; then
    test_result 0 "DNS redirection works (google.com resolves to ${PI_IP})"
else
    test_result 1 "DNS redirection failed (got ${DNS_RESULT}, expected ${PI_IP})"
fi

# Test 3: HTTP Redirect
echo ""
echo "3. Testing HTTP redirect..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -L --max-time 5 "${BASE_URL}/google.com" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    test_result 0 "HTTP redirect works (got 200, redirected to setup page)"
else
    test_result 1 "HTTP redirect failed (got ${HTTP_CODE})"
fi

# Test 4: Captive Portal Detection Endpoints
echo ""
echo "4. Testing captive portal detection endpoints..."

# iOS/macOS
IOS_RESPONSE=$(curl -s --max-time 5 "${BASE_URL}/hotspot-detect.html" 2>/dev/null || echo "")
if echo "$IOS_RESPONSE" | grep -qi "success"; then
    test_result 0 "iOS/macOS endpoint works"
else
    test_result 1 "iOS/macOS endpoint failed"
fi

# Android
ANDROID_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "${BASE_URL}/generate_204" 2>/dev/null || echo "000")
if [ "$ANDROID_CODE" = "204" ]; then
    test_result 0 "Android endpoint works"
else
    test_result 1 "Android endpoint failed (got ${ANDROID_CODE})"
fi

# Windows
WIN_RESPONSE=$(curl -s --max-time 5 "${BASE_URL}/connecttest.txt" 2>/dev/null || echo "")
if echo "$WIN_RESPONSE" | grep -qi "microsoft"; then
    test_result 0 "Windows endpoint works"
else
    test_result 1 "Windows endpoint failed"
fi

# Firefox
FF_RESPONSE=$(curl -s --max-time 5 "${BASE_URL}/success.txt" 2>/dev/null || echo "")
if echo "$FF_RESPONSE" | grep -qi "success"; then
    test_result 0 "Firefox endpoint works"
else
    test_result 1 "Firefox endpoint failed"
fi

# Test 5: API Endpoints (should NOT redirect)
echo ""
echo "5. Testing API endpoints (should work normally)..."
API_RESPONSE=$(curl -s --max-time 5 "${BASE_URL}/api/v3/wifi/status" 2>/dev/null || echo "")
if echo "$API_RESPONSE" | grep -qi "status"; then
    test_result 0 "API endpoints work (not redirected)"
else
    test_result 1 "API endpoints failed or were redirected"
fi

# Test 6: Main Interface (should be accessible)
echo ""
echo "6. Testing main interface accessibility..."
MAIN_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "${BASE_URL}/v3" 2>/dev/null || echo "000")
if [ "$MAIN_CODE" = "200" ]; then
    test_result 0 "Main interface is accessible"
else
    test_result 1 "Main interface failed (got ${MAIN_CODE})"
fi

# Summary
echo ""
echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo -e "${GREEN}Passed: ${PASSED}${NC}"
echo -e "${RED}Failed: ${FAILED}${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed! Captive portal is working correctly.${NC}"
    exit 0
else
    echo -e "${YELLOW}Some tests failed. Check the output above for details.${NC}"
    echo ""
    echo "Troubleshooting tips:"
    echo "1. Verify AP mode is active: sudo systemctl status hostapd"
    echo "2. Check dnsmasq config: sudo cat /etc/dnsmasq.conf"
    echo "3. Check web interface logs: sudo journalctl -u ledmatrix-web -n 50"
    echo "4. Verify you're connected to LEDMatrix-Setup network"
    exit 1
fi

