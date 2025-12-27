#!/bin/bash
# LEDMatrix Web UI Diagnostic Script
# Run this on your Raspberry Pi to diagnose web UI startup issues

set -e

echo "=========================================="
echo "LEDMatrix Web UI Diagnostic Report"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then 
    echo -e "${YELLOW}Warning: Some checks require sudo. Running what we can...${NC}"
    SUDO=""
else
    SUDO=""
fi

PROJECT_DIR="${HOME}/LEDMatrix"

echo "1. Checking service status..."
echo "------------------------------"
if systemctl is-active --quiet ledmatrix-web 2>/dev/null || sudo systemctl is-active --quiet ledmatrix-web 2>/dev/null; then
    echo -e "${GREEN}✓ Service is ACTIVE${NC}"
    STATUS=$(sudo systemctl status ledmatrix-web --no-pager -l | head -n 3)
    echo "$STATUS"
else
    echo -e "${RED}✗ Service is NOT running${NC}"
    sudo systemctl status ledmatrix-web --no-pager -l | head -n 10 || echo "Service may not be installed"
fi
echo ""

echo "2. Checking if service is enabled..."
echo "------------------------------"
if systemctl is-enabled --quiet ledmatrix-web 2>/dev/null || sudo systemctl is-enabled --quiet ledmatrix-web 2>/dev/null; then
    echo -e "${GREEN}✓ Service is enabled to start on boot${NC}"
else
    echo -e "${YELLOW}⚠ Service is NOT enabled (won't start on boot)${NC}"
fi
echo ""

echo "3. Checking configuration file..."
echo "------------------------------"
if [ -f "${PROJECT_DIR}/config/config.json" ]; then
    echo -e "${GREEN}✓ Config file exists${NC}"
    AUTOSTART=$(grep -o '"web_display_autostart":\s*\(true\|false\)' "${PROJECT_DIR}/config/config.json" | grep -o '\(true\|false\)' || echo "not found")
    if [ "$AUTOSTART" = "true" ]; then
        echo -e "${GREEN}✓ web_display_autostart is set to TRUE${NC}"
    elif [ "$AUTOSTART" = "false" ]; then
        echo -e "${RED}✗ web_display_autostart is set to FALSE (web UI won't start!)${NC}"
        echo "   Fix: Edit config.json and set 'web_display_autostart': true"
    else
        echo -e "${YELLOW}⚠ web_display_autostart setting not found (defaults to false)${NC}"
        echo "   Fix: Add 'web_display_autostart': true to config.json"
    fi
else
    echo -e "${RED}✗ Config file NOT FOUND at ${PROJECT_DIR}/config/config.json${NC}"
fi
echo ""

echo "4. Checking recent service logs..."
echo "------------------------------"
RECENT_LOGS=$(sudo journalctl -u ledmatrix-web -n 30 --no-pager 2>/dev/null || echo "No logs available")
if [ -n "$RECENT_LOGS" ] && [ "$RECENT_LOGS" != "No logs available" ]; then
    echo "$RECENT_LOGS"
    echo ""
    echo "Key messages from logs:"
    echo "$RECENT_LOGS" | grep -i "web_display_autostart\|Configuration\|Launching\|will not\|Failed\|Error\|Starting" || echo "  (no key messages found)"
else
    echo -e "${YELLOW}⚠ No recent logs found${NC}"
fi
echo ""

echo "5. Checking web interface files..."
echo "------------------------------"
FILES_TO_CHECK=(
    "scripts/utils/start_web_conditionally.py"
    "web_interface/start.py"
    "web_interface/app.py"
    "web_interface/requirements.txt"
    "web_interface/blueprints/api_v3.py"
    "web_interface/blueprints/pages_v3.py"
)

for file in "${FILES_TO_CHECK[@]}"; do
    if [ -f "${PROJECT_DIR}/${file}" ]; then
        echo -e "${GREEN}✓ ${file}${NC}"
    else
        echo -e "${RED}✗ ${file} MISSING${NC}"
    fi
done
echo ""

echo "6. Checking Python import test..."
echo "------------------------------"
cd "${PROJECT_DIR}" 2>/dev/null || {
    echo -e "${RED}✗ Cannot access project directory: ${PROJECT_DIR}${NC}"
    echo ""
    exit 1
}

if python3 -c "from web_interface.app import app; print('OK')" 2>&1; then
    echo -e "${GREEN}✓ Flask app imports successfully${NC}"
else
    echo -e "${RED}✗ Flask app import FAILED${NC}"
    echo "   Error details:"
    python3 -c "from web_interface.app import app; print('OK')" 2>&1 | head -n 10
fi
echo ""

echo "7. Checking port 5000 availability..."
echo "------------------------------"
if command -v lsof &> /dev/null; then
    PORT_CHECK=$(sudo lsof -i :5000 2>/dev/null || echo "")
    if [ -z "$PORT_CHECK" ]; then
        echo -e "${GREEN}✓ Port 5000 is available${NC}"
    else
        echo -e "${YELLOW}⚠ Port 5000 is in use:${NC}"
        echo "$PORT_CHECK"
    fi
elif command -v ss &> /dev/null; then
    PORT_CHECK=$(sudo ss -tlnp | grep :5000 || echo "")
    if [ -z "$PORT_CHECK" ]; then
        echo -e "${GREEN}✓ Port 5000 is available${NC}"
    else
        echo -e "${YELLOW}⚠ Port 5000 is in use:${NC}"
        echo "$PORT_CHECK"
    fi
else
    echo -e "${YELLOW}⚠ Cannot check port (lsof/ss not available)${NC}"
fi
echo ""

echo "8. Checking service file..."
echo "------------------------------"
if [ -f "/etc/systemd/system/ledmatrix-web.service" ]; then
    echo -e "${GREEN}✓ Service file exists${NC}"
    echo "   Location: /etc/systemd/system/ledmatrix-web.service"
    echo "   WorkingDirectory: $(grep WorkingDirectory /etc/systemd/system/ledmatrix-web.service | cut -d'=' -f2 || echo 'not found')"
    echo "   ExecStart: $(grep ExecStart /etc/systemd/system/ledmatrix-web.service | cut -d'=' -f2 || echo 'not found')"
else
    echo -e "${RED}✗ Service file NOT FOUND${NC}"
    echo "   Run: sudo ./install_web_service.sh (if available)"
fi
echo ""

echo "9. Checking dependencies..."
echo "------------------------------"
if [ -f "${PROJECT_DIR}/web_interface/requirements.txt" ]; then
    echo "Checking if Flask is installed..."
    if python3 -c "import flask; print(f'Flask {flask.__version__}')" 2>/dev/null; then
        echo -e "${GREEN}✓ Flask is installed${NC}"
    else
        echo -e "${RED}✗ Flask is NOT installed${NC}"
        echo "   Run: pip3 install --break-system-packages -r web_interface/requirements.txt"
    fi
else
    echo -e "${YELLOW}⚠ requirements.txt not found${NC}"
fi
echo ""

echo "10. Manual startup test (dry run)..."
echo "------------------------------"
echo "To test manual startup, run:"
echo "  cd ${PROJECT_DIR}"
echo "  python3 web_interface/start.py"
echo ""

echo "=========================================="
echo "Diagnostic Summary"
echo "=========================================="
echo ""
echo "Most common issues:"
echo "  1. web_display_autostart is false or missing in config.json"
echo "  2. Service not enabled or not started"
echo "  3. Missing dependencies (Flask, etc.)"
echo "  4. Import errors in web_interface/app.py"
echo "  5. Port 5000 already in use"
echo ""
echo "Next steps:"
echo "  - Review the checks above for any RED ✗ marks"
echo "  - Check recent logs: sudo journalctl -u ledmatrix-web -n 50 --no-pager"
echo "  - Follow logs in real-time: sudo journalctl -u ledmatrix-web -f"
echo "  - Try manual start: cd ${PROJECT_DIR} && python3 web_interface/start.py"
echo ""
echo "=========================================="

