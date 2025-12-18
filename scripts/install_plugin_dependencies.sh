#!/bin/bash
# Script to manually install plugin dependencies
# Use this if automatic dependency installation fails

set -e

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
PLUGINS_DIR="$LEDMATRIX_DIR/plugins"

echo "LEDMatrix directory: $LEDMATRIX_DIR"
echo "Plugins directory: $PLUGINS_DIR"
echo ""

# Check if plugins directory exists
if [ ! -d "$PLUGINS_DIR" ]; then
    echo -e "${RED}Error: Plugins directory not found at $PLUGINS_DIR${NC}"
    exit 1
fi

# Find all requirements.txt files in plugin directories
echo -e "${GREEN}Searching for plugin requirements...${NC}"
echo ""

PLUGINS_FOUND=0
PLUGINS_INSTALLED=0
PLUGINS_FAILED=0

for plugin_dir in "$PLUGINS_DIR"/*/ ; do
    if [ -d "$plugin_dir" ]; then
        plugin_name=$(basename "$plugin_dir")
        requirements_file="$plugin_dir/requirements.txt"
        
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

