#!/bin/bash
# Web Interface Diagnostic Script
# Run this on your Raspberry Pi to diagnose web interface issues

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║    LED Matrix Web Interface Diagnostic Tool                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory and project root (parent of scripts/)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

# Report web_display_autostart the way scripts/utils/start_web_conditionally.py
# (what ledmatrix-web.service runs) decides it: only an explicit false/off keeps
# the web interface down; a missing key or an unreadable config starts it.
# Prints "on <value>", "off <value>", "default" (key not set) or "unreadable".
web_autostart_state() {
    (cd "$1" && python3 - 2>/dev/null <<'PY'
import json, os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "scripts", "utils"))
try:
    from start_web_conditionally import autostart_enabled
except Exception:
    def autostart_enabled(config):
        value = config.get("web_display_autostart", True)
        if isinstance(value, str):
            return value.strip().lower() not in ("off", "false", "no", "0")
        return bool(value)
try:
    with open(os.path.join("config", "config.json"), encoding="utf-8") as f:
        config = json.load(f)
except Exception:
    config = None
if not isinstance(config, dict):
    print("unreadable")
elif "web_display_autostart" not in config:
    print("default")
else:
    raw = json.dumps(config["web_display_autostart"])
    print(("on " if autostart_enabled(config) else "off ") + raw)
PY
    ) || echo "unknown"
}

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}1. SERVICE STATUS${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if sudo systemctl is-active --quiet ledmatrix-web; then
    echo -e "${GREEN}✓ Service is RUNNING${NC}"
    sudo systemctl status ledmatrix-web --no-pager | head -n 15
else
    echo -e "${RED}✗ Service is NOT RUNNING${NC}"
    sudo systemctl status ledmatrix-web --no-pager | head -n 15
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}2. CONFIGURATION CHECK${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check if config file exists
if [ -f "$PROJECT_DIR/config/config.json" ]; then
    echo -e "${GREEN}✓ Config file found${NC}"
    
    # Check web_display_autostart setting
    AUTOSTART=$(web_autostart_state "$PROJECT_DIR")

    case "$AUTOSTART" in
        on\ *)
            echo -e "${GREEN}✓ web_display_autostart: ${AUTOSTART#on }${NC}"
            ;;
        off\ *)
            echo -e "${YELLOW}⚠ web_display_autostart: ${AUTOSTART#off }${NC}"
            echo -e "${YELLOW}  Web interface will not start with this value${NC}"
            ;;
        default)
            echo -e "${GREEN}✓ web_display_autostart: not set (defaults to on)${NC}"
            ;;
        unreadable)
            echo -e "${YELLOW}⚠ config.json could not be parsed (the web interface still starts so it can be repaired)${NC}"
            ;;
        *)
            echo -e "${YELLOW}⚠ web_display_autostart: could not be evaluated (python3 unavailable?)${NC}"
            ;;
    esac
else
    echo -e "${RED}✗ Config file not found at: $PROJECT_DIR/config/config.json${NC}"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}3. FILE STRUCTURE CHECK${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check critical files
declare -a REQUIRED_FILES=(
    "web_interface/app.py"
    "web_interface/start.py"
    "web_interface/requirements.txt"
    "web_interface/blueprints/api_v3/__init__.py"
    "web_interface/blueprints/pages_v3.py"
    "scripts/utils/start_web_conditionally.py"
)

ALL_FILES_OK=true
for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$PROJECT_DIR/$file" ]; then
        echo -e "${GREEN}✓${NC} $file"
    else
        echo -e "${RED}✗${NC} $file ${RED}(MISSING)${NC}"
        ALL_FILES_OK=false
    fi
done

if [ "$ALL_FILES_OK" = true ]; then
    echo -e "\n${GREEN}✓ All required files present${NC}"
else
    echo -e "\n${RED}✗ Some files are missing - reorganization may be incomplete${NC}"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}4. PYTHON IMPORT TEST${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Test Python imports
echo -n "Testing Flask app import... "
IMPORT_OUTPUT=$(python3 -c "import sys; sys.path.insert(0, '$PROJECT_DIR'); from web_interface.app import app; print('OK')" 2>&1)
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ SUCCESS${NC}"
else
    echo -e "${RED}✗ FAILED${NC}"
    echo -e "${RED}Error details:${NC}"
    echo "$IMPORT_OUTPUT"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}5. NETWORK STATUS${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check if port 5000 is in use
if sudo netstat -tlnp 2>/dev/null | grep -q ":5000 " || sudo ss -tlnp 2>/dev/null | grep -q ":5000 "; then
    echo -e "${GREEN}✓ Port 5000 is in use (web interface may be running)${NC}"
    if command -v netstat &> /dev/null; then
        sudo netstat -tlnp | grep ":5000 "
    else
        sudo ss -tlnp | grep ":5000 "
    fi
else
    echo -e "${YELLOW}⚠ Port 5000 is not in use (web interface not listening)${NC}"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}6. RECENT SERVICE LOGS (Last 30 lines)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
sudo journalctl -u ledmatrix-web -n 30 --no-pager

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}7. RECOMMENDATIONS${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Provide recommendations based on findings
if ! sudo systemctl is-active --quiet ledmatrix-web; then
    echo -e "${YELLOW}→ Service is not running. Try:${NC}"
    echo "   sudo systemctl start ledmatrix-web"
fi

if [ "${AUTOSTART%% *}" = "off" ]; then
    echo -e "${YELLOW}→ Set web_display_autostart to true in config/config.json (or remove it; missing means on)${NC}"
fi

if [ "$ALL_FILES_OK" = false ]; then
    echo -e "${YELLOW}→ Some files are missing. You may need to:${NC}"
    echo "   - Check git status: git status"
    echo "   - Restore files: git checkout ."
    echo "   - Or re-run the reorganization"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}QUICK COMMANDS:${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "View live logs:"
echo "  sudo journalctl -u ledmatrix-web -f"
echo ""
echo "Restart service:"
echo "  sudo systemctl restart ledmatrix-web"
echo ""
echo "Test manual startup:"
echo "  cd $PROJECT_DIR && python3 web_interface/start.py"
echo ""
echo "Check service status:"
echo "  sudo systemctl status ledmatrix-web"
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

