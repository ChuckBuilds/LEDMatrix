#!/bin/bash

# Quick Web UI Verification Script
# Run this via SSH on your Raspberry Pi to verify the web interface is running

echo "=========================================="
echo "Web UI Verification"
echo "=========================================="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Check service status
echo "1. Checking service status..."
if systemctl is-active --quiet ledmatrix-web.service 2>/dev/null; then
    echo -e "${GREEN}✓${NC} ledmatrix-web.service is running"
    
    # Get detailed status
    echo ""
    echo "Service details:"
    systemctl status ledmatrix-web.service --no-pager -l | head -15
else
    echo -e "${RED}✗${NC} ledmatrix-web.service is NOT running"
    echo ""
    echo "To start the service:"
    echo "  sudo systemctl start ledmatrix-web.service"
    echo ""
fi
echo ""

# 2. Check if port 5001 is listening
echo "2. Checking if port 5001 is listening..."
if command -v ss >/dev/null 2>&1; then
    if ss -tuln 2>/dev/null | grep -q ":5001"; then
        echo -e "${GREEN}✓${NC} Port 5001 is listening"
        echo ""
        echo "Active connections on port 5001:"
        ss -tuln | grep ":5001"
    else
        echo -e "${RED}✗${NC} Port 5001 is NOT listening"
    fi
elif command -v netstat >/dev/null 2>&1; then
    if netstat -tuln 2>/dev/null | grep -q ":5001"; then
        echo -e "${GREEN}✓${NC} Port 5001 is listening"
        echo ""
        echo "Active connections on port 5001:"
        netstat -tuln | grep ":5001"
    else
        echo -e "${RED}✗${NC} Port 5001 is NOT listening"
    fi
else
    echo -e "${YELLOW}⚠${NC} Cannot check port (ss/netstat not available)"
fi
echo ""

# 3. Test HTTP connection
echo "3. Testing HTTP connection..."
if curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:5001 > /dev/null 2>&1; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:5001 2>/dev/null)
    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "302" ] || [ "$HTTP_CODE" = "301" ]; then
        echo -e "${GREEN}✓${NC} Web interface is responding (HTTP $HTTP_CODE)"
    else
        echo -e "${YELLOW}⚠${NC} Web interface responded with HTTP $HTTP_CODE"
    fi
else
    echo -e "${RED}✗${NC} Cannot connect to web interface on port 5001"
fi
echo ""

# 4. Get Pi's IP address
echo "4. Network information..."
IP_ADDRESSES=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -n "$IP_ADDRESSES" ]; then
    echo "Pi IP address(es): $IP_ADDRESSES"
    echo ""
    echo "Access web interface at:"
    for ip in $IP_ADDRESSES; do
        echo "  http://$ip:5001"
    done
else
    echo -e "${YELLOW}⚠${NC} Could not determine IP address"
fi
echo ""

# 5. Check recent logs
echo "5. Recent service logs (last 10 lines)..."
echo "----------------------------------------"
journalctl -u ledmatrix-web.service -n 10 --no-pager 2>/dev/null || echo "Could not retrieve logs"
echo ""

# 6. Check for errors in logs
echo "6. Checking for errors in logs..."
ERROR_COUNT=$(journalctl -u ledmatrix-web.service --since "5 minutes ago" --no-pager 2>/dev/null | grep -i "error\|exception\|failed\|traceback" | wc -l)
if [ "$ERROR_COUNT" -gt 0 ]; then
    echo -e "${YELLOW}⚠${NC} Found $ERROR_COUNT error(s) in last 5 minutes"
    echo ""
    echo "Recent errors:"
    journalctl -u ledmatrix-web.service --since "5 minutes ago" --no-pager 2>/dev/null | grep -i "error\|exception\|failed\|traceback" | tail -5
else
    echo -e "${GREEN}✓${NC} No errors in recent logs"
fi
echo ""

# Summary
echo "=========================================="
echo "Summary"
echo "=========================================="

SERVICE_RUNNING=false
PORT_LISTENING=false
HTTP_RESPONDING=false

if systemctl is-active --quiet ledmatrix-web.service 2>/dev/null; then
    SERVICE_RUNNING=true
fi

if (ss -tuln 2>/dev/null | grep -q ":5001") || (netstat -tuln 2>/dev/null | grep -q ":5001"); then
    PORT_LISTENING=true
fi

if curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:5001 > /dev/null 2>&1; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:5001 2>/dev/null)
    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "302" ] || [ "$HTTP_CODE" = "301" ]; then
        HTTP_RESPONDING=true
    fi
fi

if [ "$SERVICE_RUNNING" = true ] && [ "$PORT_LISTENING" = true ] && [ "$HTTP_RESPONDING" = true ]; then
    echo -e "${GREEN}✓ Web UI is running correctly${NC}"
    echo ""
    echo "You can access it at:"
    for ip in $IP_ADDRESSES; do
        echo "  http://$ip:5001"
    done
    exit 0
elif [ "$SERVICE_RUNNING" = false ]; then
    echo -e "${RED}✗ Web UI service is not running${NC}"
    echo ""
    echo "To start it:"
    echo "  sudo systemctl start ledmatrix-web.service"
    echo "  sudo systemctl enable ledmatrix-web.service  # to start on boot"
    exit 1
elif [ "$PORT_LISTENING" = false ]; then
    echo -e "${RED}✗ Service is running but port 5001 is not listening${NC}"
    echo ""
    echo "Check logs for errors:"
    echo "  sudo journalctl -u ledmatrix-web.service -f"
    exit 1
else
    echo -e "${YELLOW}⚠ Web UI may have issues${NC}"
    echo ""
    echo "Check logs for details:"
    echo "  sudo journalctl -u ledmatrix-web.service -f"
    exit 1
fi

