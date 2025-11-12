#!/bin/bash
# Diagnostic script for plugin directory permissions
# Run this on the Raspberry Pi to check and fix plugin directory permissions

set -e

echo "=========================================="
echo "Plugin Directory Permissions Diagnostic"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get the actual user
if [ -n "$SUDO_USER" ]; then
    ACTUAL_USER="$SUDO_USER"
else
    ACTUAL_USER=$(whoami)
fi

# Get project root (assume script is in scripts/ directory)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PLUGINS_DIR="$PROJECT_ROOT_DIR/plugins"
PLUGIN_REPOS_DIR="$PROJECT_ROOT_DIR/plugin-repos"

echo "Project root: $PROJECT_ROOT_DIR"
echo "User: $ACTUAL_USER"
echo "Running as: $(whoami)"
echo ""

# Check parent directory permissions
echo "=== Parent Directory Permissions ==="
echo "Checking /home/$ACTUAL_USER:"
if [ -d "/home/$ACTUAL_USER" ]; then
    PERMS=$(stat -c "%a %U:%G" "/home/$ACTUAL_USER")
    echo "  Permissions: $PERMS"
    if [ "$(stat -c "%a" "/home/$ACTUAL_USER")" != "755" ] && [ "$(stat -c "%a" "/home/$ACTUAL_USER")" != "750" ]; then
        echo -e "  ${YELLOW}⚠ Warning: Home directory has restrictive permissions${NC}"
        echo "  Root may not be able to access subdirectories"
    fi
else
    echo -e "  ${RED}✗ Home directory not found${NC}"
fi
echo ""

echo "Checking $PROJECT_ROOT_DIR:"
if [ -d "$PROJECT_ROOT_DIR" ]; then
    PERMS=$(stat -c "%a %U:%G" "$PROJECT_ROOT_DIR")
    echo "  Permissions: $PERMS"
    if [ "$(stat -c "%a" "$PROJECT_ROOT_DIR")" != "755" ] && [ "$(stat -c "%a" "$PROJECT_ROOT_DIR")" != "750" ]; then
        echo -e "  ${YELLOW}⚠ Warning: Project directory has restrictive permissions${NC}"
    fi
else
    echo -e "  ${RED}✗ Project directory not found${NC}"
fi
echo ""

# Check plugins directory
echo "=== Plugins Directory ==="
if [ -d "$PLUGINS_DIR" ]; then
    PERMS=$(stat -c "%a %U:%G" "$PLUGINS_DIR")
    echo "  Path: $PLUGINS_DIR"
    echo "  Permissions: $PERMS"
    
    OWNER=$(stat -c "%U" "$PLUGINS_DIR")
    GROUP=$(stat -c "%G" "$PLUGINS_DIR")
    PERM_BITS=$(stat -c "%a" "$PLUGINS_DIR")
    
    if [ "$OWNER" = "root" ] && [ "$GROUP" = "$ACTUAL_USER" ] && [ "$PERM_BITS" = "775" ]; then
        echo -e "  ${GREEN}✓ Correct permissions${NC}"
    else
        echo -e "  ${RED}✗ Incorrect permissions${NC}"
        echo "    Expected: root:$ACTUAL_USER 775"
        echo "    Actual: $OWNER:$GROUP $PERM_BITS"
    fi
    
    # Check if root can access
    if sudo -u root test -r "$PLUGINS_DIR" && sudo -u root test -w "$PLUGINS_DIR"; then
        echo -e "  ${GREEN}✓ Root can read/write${NC}"
    else
        echo -e "  ${RED}✗ Root cannot access${NC}"
    fi
else
    echo -e "  ${YELLOW}⚠ Directory does not exist${NC}"
fi
echo ""

# Check plugin-repos directory
echo "=== Plugin-Repos Directory ==="
if [ -d "$PLUGIN_REPOS_DIR" ]; then
    PERMS=$(stat -c "%a %U:%G" "$PLUGIN_REPOS_DIR")
    echo "  Path: $PLUGIN_REPOS_DIR"
    echo "  Permissions: $PERMS"
    
    OWNER=$(stat -c "%U" "$PLUGIN_REPOS_DIR")
    GROUP=$(stat -c "%G" "$PLUGIN_REPOS_DIR")
    PERM_BITS=$(stat -c "%a" "$PLUGIN_REPOS_DIR")
    
    if [ "$OWNER" = "root" ] && [ "$GROUP" = "$ACTUAL_USER" ] && [ "$PERM_BITS" = "775" ]; then
        echo -e "  ${GREEN}✓ Correct permissions${NC}"
    else
        echo -e "  ${RED}✗ Incorrect permissions${NC}"
        echo "    Expected: root:$ACTUAL_USER 775"
        echo "    Actual: $OWNER:$GROUP $PERM_BITS"
    fi
    
    # Check if root can access
    if sudo -u root test -r "$PLUGIN_REPOS_DIR" && sudo -u root test -w "$PLUGIN_REPOS_DIR"; then
        echo -e "  ${GREEN}✓ Root can read/write${NC}"
    else
        echo -e "  ${RED}✗ Root cannot access${NC}"
    fi
    
    # Try to list contents as root
    echo "  Testing root access:"
    if sudo -u root ls "$PLUGIN_REPOS_DIR" >/dev/null 2>&1; then
        echo -e "    ${GREEN}✓ Root can list directory${NC}"
    else
        echo -e "    ${RED}✗ Root cannot list directory${NC}"
        echo "    Error: $(sudo -u root ls "$PLUGIN_REPOS_DIR" 2>&1 | head -1)"
    fi
else
    echo -e "  ${YELLOW}⚠ Directory does not exist${NC}"
fi
echo ""

# Test if root can create files
echo "=== Testing Root Write Access ==="
TEST_FILE="$PLUGIN_REPOS_DIR/.permission_test_$$"
if sudo -u root touch "$TEST_FILE" 2>/dev/null; then
    echo -e "  ${GREEN}✓ Root can create files${NC}"
    sudo -u root rm -f "$TEST_FILE"
else
    echo -e "  ${RED}✗ Root cannot create files${NC}"
    echo "    Error: $(sudo -u root touch "$TEST_FILE" 2>&1)"
fi
echo ""

# Summary and fix recommendations
echo "=== Summary ==="
NEEDS_FIX=false

if [ ! -d "$PLUGIN_REPOS_DIR" ]; then
    echo -e "${YELLOW}⚠ plugin-repos directory does not exist${NC}"
    NEEDS_FIX=true
elif [ "$(stat -c "%U:%G" "$PLUGIN_REPOS_DIR" 2>/dev/null)" != "root:$ACTUAL_USER" ] || [ "$(stat -c "%a" "$PLUGIN_REPOS_DIR" 2>/dev/null)" != "775" ]; then
    echo -e "${RED}✗ plugin-repos has incorrect permissions${NC}"
    NEEDS_FIX=true
fi

if [ "$NEEDS_FIX" = true ]; then
    echo ""
    echo "=== Fix Commands ==="
    echo "Run these commands to fix permissions:"
    echo ""
    echo "sudo mkdir -p $PLUGIN_REPOS_DIR"
    echo "sudo chown root:$ACTUAL_USER $PLUGIN_REPOS_DIR"
    echo "sudo chmod 775 $PLUGIN_REPOS_DIR"
    echo ""
    echo "Or run the fix script:"
    echo "sudo bash $PROJECT_ROOT_DIR/scripts/fix_perms/fix_plugin_permissions.sh"
else
    echo -e "${GREEN}✓ All permissions look correct${NC}"
fi
echo ""

