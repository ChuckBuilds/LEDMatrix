#!/bin/bash
# Script to manually install plugin dependencies
# Use this if automatic dependency installation fails

set -e
# A failed pip must fail the `pip ... | tee` pipeline below, not be hidden by tee.
set -o pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== LEDMatrix Plugin Dependency Installer ===${NC}"
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${GREEN}Running as root - will install system-wide${NC}"
    INSTALL_CMD="pip3 install --break-system-packages --no-cache-dir"
else
    echo -e "${YELLOW}Not running as root - will install to user directory${NC}"
    echo -e "${YELLOW}Note: User-installed packages won't be accessible to systemd service${NC}"
    INSTALL_CMD="pip3 install --user --break-system-packages --no-cache-dir"
fi

echo ""

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LEDMATRIX_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$LEDMATRIX_DIR/config/config.json"

# The Plugin Store installs into plugin_system.plugins_directory from
# config.json (default plugin-repos), resolved against the project root like
# the display and web services do. plugins/ is also scanned: it holds the
# symlinks scripts/dev/dev_plugin_setup.sh creates.
CONFIGURED_DIR="plugin-repos"
if [ -f "$CONFIG_FILE" ] && command -v python3 >/dev/null 2>&1; then
    CONFIGURED_DIR="$(LEDMATRIX_CONFIG_FILE="$CONFIG_FILE" python3 -c '
import json, os
try:
    with open(os.environ["LEDMATRIX_CONFIG_FILE"], encoding="utf-8") as f:
        value = (json.load(f).get("plugin_system") or {}).get("plugins_directory")
except Exception:
    value = None
print(value if isinstance(value, str) and value.strip() else "plugin-repos")
' 2>/dev/null)" || CONFIGURED_DIR="plugin-repos"
    [ -n "$CONFIGURED_DIR" ] || CONFIGURED_DIR="plugin-repos"
fi
case "$CONFIGURED_DIR" in
    /*) PLUGINS_DIR="$CONFIGURED_DIR" ;;
    *)  PLUGINS_DIR="$LEDMATRIX_DIR/$CONFIGURED_DIR" ;;
esac
DEV_PLUGINS_DIR="$LEDMATRIX_DIR/plugins"

echo "LEDMatrix directory: $LEDMATRIX_DIR"
echo "Plugins directory: $PLUGINS_DIR (plugin_system.plugins_directory)"

SCAN_DIRS=()
PLUGINS_DIR_REAL=""
if [ -d "$PLUGINS_DIR" ]; then
    SCAN_DIRS+=("$PLUGINS_DIR")
    PLUGINS_DIR_REAL="$(cd "$PLUGINS_DIR" && pwd -P)"
fi
if [ -d "$DEV_PLUGINS_DIR" ] && [ "$(cd "$DEV_PLUGINS_DIR" && pwd -P)" != "$PLUGINS_DIR_REAL" ]; then
    echo "Also scanning dev plugins: $DEV_PLUGINS_DIR"
    SCAN_DIRS+=("$DEV_PLUGINS_DIR")
fi
echo ""

# Check if a plugins directory exists
if [ ${#SCAN_DIRS[@]} -eq 0 ]; then
    echo -e "${RED}Error: Plugins directory not found at $PLUGINS_DIR${NC}"
    echo "Install a plugin from the Plugin Store first, or check plugin_system.plugins_directory in $CONFIG_FILE"
    exit 1
fi

# Find all requirements.txt files in plugin directories
echo -e "${GREEN}Searching for plugin requirements...${NC}"
echo ""

PLUGINS_FOUND=0
PLUGINS_INSTALLED=0
PLUGINS_FAILED=0
SEEN_PLUGIN_PATHS=" "

for scan_dir in "${SCAN_DIRS[@]}"; do
for plugin_dir in "$scan_dir"/*/ ; do
    if [ -d "$plugin_dir" ]; then
        plugin_name=$(basename "$plugin_dir")
        requirements_file="$plugin_dir/requirements.txt"

        # A dev symlink can point at a plugin already scanned; install it once.
        real_plugin_dir="$(cd "$plugin_dir" && pwd -P)"
        case "$SEEN_PLUGIN_PATHS" in
            *" $real_plugin_dir "*) continue ;;
        esac
        SEEN_PLUGIN_PATHS="$SEEN_PLUGIN_PATHS$real_plugin_dir "

        if [ -f "$requirements_file" ]; then
            PLUGINS_FOUND=$((PLUGINS_FOUND + 1))
            echo -e "${GREEN}Found plugin: ${plugin_name}${NC}"
            echo "  Requirements: $requirements_file"
            
            # Check if requirements file is empty
            if [ ! -s "$requirements_file" ]; then
                echo -e "  ${YELLOW}Skipping: requirements.txt is empty${NC}"
                echo ""
                continue
            fi
            
            # Install dependencies
            echo "  Installing dependencies..."
            if $INSTALL_CMD -r "$requirements_file" 2>&1 | tee /tmp/pip_install_$plugin_name.log; then
                echo -e "  ${GREEN}✓ Successfully installed dependencies for $plugin_name${NC}"
                PLUGINS_INSTALLED=$((PLUGINS_INSTALLED + 1))
            else
                echo -e "  ${RED}✗ Failed to install dependencies for $plugin_name${NC}"
                echo -e "  ${RED}  See /tmp/pip_install_$plugin_name.log for details${NC}"
                PLUGINS_FAILED=$((PLUGINS_FAILED + 1))
            fi
            echo ""
        fi
    fi
done
done

# Summary
echo ""
echo -e "${GREEN}=== Installation Summary ===${NC}"
echo "Plugins with requirements found: $PLUGINS_FOUND"
echo -e "${GREEN}Successfully installed: $PLUGINS_INSTALLED${NC}"
if [ $PLUGINS_FAILED -gt 0 ]; then
    echo -e "${RED}Failed: $PLUGINS_FAILED${NC}"
fi
echo ""

if [ $PLUGINS_FAILED -gt 0 ]; then
    echo -e "${YELLOW}Some plugins failed to install dependencies.${NC}"
    echo -e "${YELLOW}Common solutions:${NC}"
    echo "  1. Run this script as root: sudo $0"
    echo "  2. Check pip logs in /tmp/pip_install_*.log"
    echo "  3. Manually install with: sudo pip3 install --break-system-packages -r <requirements.txt>"
    echo ""
    exit 1
fi

if [ $PLUGINS_FOUND -eq 0 ]; then
    echo -e "${YELLOW}No plugins with requirements.txt found${NC}"
else
    echo -e "${GREEN}All plugin dependencies installed successfully!${NC}"
    echo ""
    echo "If you're using systemd service, restart it:"
    echo "  sudo systemctl restart ledmatrix"
fi

