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
echo "3. Make the project directory yours again, keeping the root-owned sudo"
echo "   helpers and config_secrets.json as the installer leaves them"
echo "   (sudoers rules are configure_web_sudo.sh's job, not this script's)"
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

# The chown above also takes back two kinds of file that first_time_install.sh
# deliberately keeps from the web user. Put them back the way the installer
# leaves them (its Steps 11 and 11.1), whether or not the chown succeeded.
#
# 1. The helpers /etc/sudoers.d/ledmatrix_web lets the web user run as root
#    (scripts/install/lib_sudoers.sh). A copy the web user owns is a root shell
#    for whoever can edit it, so they stay root-owned and writable by root only.
#    Keep this list in step with the installer's Step 11.1 loop;
#    test/test_web_sudoers_installers_agree.py checks both against the grants.
for helper in safe_plugin_rm.sh safe_pip_install.sh; do
    HELPER_PATH="$PROJECT_DIR/scripts/fix_perms/$helper"
    if [ -f "$HELPER_PATH" ]; then
        if sudo chown root:root "$HELPER_PATH" && sudo chmod 755 "$HELPER_PATH"; then
            echo "✓ $helper is root-owned again (sudo runs it as root)"
        else
            echo "⚠ Could not make $HELPER_PATH root-owned, mode 755."
            echo "  Fix it by hand: sudo chown root:root $HELPER_PATH && sudo chmod 755 $HELPER_PATH"
        fi
    fi
done

# 2. config_secrets.json: owned by the account ledmatrix-web.service runs as,
#    group ledmatrix, mode 640 -- the same owner, group and mode as the
#    installer's Step 11 gives it.
SECRETS_FILE="$PROJECT_DIR/config/config_secrets.json"
if [ -f "$SECRETS_FILE" ]; then
    SECRETS_OWNER=""
    if [ -f /etc/systemd/system/ledmatrix-web.service ]; then
        SECRETS_OWNER=$(grep -m1 "^User=" /etc/systemd/system/ledmatrix-web.service | cut -d'=' -f2 || true)
    fi
    SECRETS_OWNER="${SECRETS_OWNER:-$WEB_USER}"
    if getent group ledmatrix >/dev/null 2>&1; then
        SECRETS_OWNERSHIP="$SECRETS_OWNER:ledmatrix"
    else
        # No ledmatrix group means the installer never ran; keep the chown's group.
        SECRETS_OWNERSHIP="$SECRETS_OWNER"
    fi
    if sudo chown "$SECRETS_OWNERSHIP" "$SECRETS_FILE" && sudo chmod 640 "$SECRETS_FILE"; then
        echo "✓ config_secrets.json restored to $SECRETS_OWNERSHIP, mode 640"
    else
        echo "⚠ Could not restore $SECRETS_FILE to $SECRETS_OWNERSHIP, mode 640."
        echo "  Fix it by hand: sudo chown $SECRETS_OWNERSHIP $SECRETS_FILE && sudo chmod 640 $SECRETS_FILE"
    fi
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
    echo "⚠ Sudo access test failed - you may need to run scripts/install/configure_web_sudo.sh"
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
echo "If you still have sudo issues, run (as this user, without sudo):"
echo "  $PROJECT_DIR/scripts/install/configure_web_sudo.sh"
