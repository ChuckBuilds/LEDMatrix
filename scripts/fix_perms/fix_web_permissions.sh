#!/bin/bash

# LED Matrix Web Interface Permissions Fix Script
# This script fixes permissions for the web interface to access logs and system commands

set -e

echo "Fixing LED Matrix Web Interface permissions..."

# Get the current user (should be the user running the web interface)
WEB_USER=$(whoami)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Detected web interface user: $WEB_USER"
echo "Project directory: $PROJECT_DIR"

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Error: This script should not be run as root."
    echo "Run it as the user that will be running the web interface."
    exit 1
fi

echo ""
echo "This script will:"
echo "1. Add the web user to the 'systemd-journal' group for log access"
echo "2. Add the web user to the 'adm' group for additional system access"
echo "3. Configure sudoers for passwordless access to system commands"
echo "4. Set proper file permissions"
echo ""

# Ask for confirmation
read -p "Do you want to proceed? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Permission fix cancelled."
    exit 0
fi

echo ""
echo "Step 1: Adding user to systemd-journal group..."
if sudo usermod -a -G systemd-journal "$WEB_USER"; then
    echo "✓ Added $WEB_USER to systemd-journal group"
else
    echo "✗ Failed to add user to systemd-journal group"
fi

echo ""
echo "Step 2: Adding user to adm group..."
if sudo usermod -a -G adm "$WEB_USER"; then
    echo "✓ Added $WEB_USER to adm group"
else
    echo "✗ Failed to add user to adm group"
fi

echo ""
echo "Step 3: Setting proper file permissions..."
# Set ownership of project files to the web user
if sudo chown -R "$WEB_USER:$WEB_USER" "$PROJECT_DIR"; then
    echo "✓ Set project ownership to $WEB_USER"
else
    echo "✗ Failed to set project ownership"
fi

# Set proper permissions for config files
if sudo chmod 644 "$PROJECT_DIR/config/config.json" 2>/dev/null; then
    echo "✓ Set config file permissions"
else
    echo "⚠ Config file permissions not set (file may not exist)"
fi

echo ""
echo "Step 4: Testing journal access..."
# Test if the user can now access journal logs
if journalctl --user-unit=ledmatrix.service --no-pager --lines=1 > /dev/null 2>&1; then
    echo "✓ Journal access test passed"
elif sudo -u "$WEB_USER" journalctl --no-pager --lines=1 > /dev/null 2>&1; then
    echo "✓ Journal access test passed (with sudo)"
else
    echo "⚠ Journal access test failed - you may need to log out and back in"
fi

echo ""
echo "Step 5: Testing sudo access..."
# Test sudo access for system commands
if sudo -n systemctl status ledmatrix.service > /dev/null 2>&1; then
    echo "✓ Sudo access test passed"
else
    echo "⚠ Sudo access test failed - you may need to run configure_web_sudo.sh"
fi

echo ""
echo "Permission fix completed!"
echo ""
echo "IMPORTANT: For group changes to take effect, you need to:"
echo "1. Log out and log back in, OR"
echo "2. Run: newgrp systemd-journal"
echo "3. Restart the web interface service:"
echo "   sudo systemctl restart ledmatrix-web.service"
echo ""
echo "After logging back in, test journal access with:"
echo "  journalctl --no-pager --lines=5"
echo ""
echo "If you still have sudo issues, run:"
echo "  ./configure_web_sudo.sh"
