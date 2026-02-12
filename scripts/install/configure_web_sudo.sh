#!/bin/bash

# LED Matrix Web Interface Sudo Configuration Script
# This script configures passwordless sudo access for the web interface user

set -e

echo "Configuring passwordless sudo access for LED Matrix Web Interface..."

# Get the current user (should be the user running the web interface)
WEB_USER=$(whoami)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PROJECT_DIR/../.." && pwd)"

echo "Detected web interface user: $WEB_USER"
echo "Project directory: $PROJECT_DIR"
echo "Project root: $PROJECT_ROOT"

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Error: This script should not be run as root."
    echo "Run it as the user that will be running the web interface."
    exit 1
fi

# Get the full paths to commands
PYTHON_PATH=$(which python3)
SYSTEMCTL_PATH=$(which systemctl)
REBOOT_PATH=$(which reboot)
POWEROFF_PATH=$(which poweroff)
BASH_PATH=$(which bash)
JOURNALCTL_PATH=$(which journalctl)
RM_PATH=$(which rm)
FIND_PATH=$(which find)

echo "Command paths:"
echo "  Python: $PYTHON_PATH"
echo "  Systemctl: $SYSTEMCTL_PATH"
echo "  Reboot: $REBOOT_PATH"
echo "  Poweroff: $POWEROFF_PATH"
echo "  Bash: $BASH_PATH"
echo "  Journalctl: $JOURNALCTL_PATH"
echo "  Rm: $RM_PATH"
echo "  Find: $FIND_PATH"

# Create a temporary sudoers file
TEMP_SUDOERS="/tmp/ledmatrix_web_sudoers_$$"

cat > "$TEMP_SUDOERS" << EOF
# LED Matrix Web Interface passwordless sudo configuration
# This allows the web interface user to run specific commands without a password

# Allow $WEB_USER to run specific commands without a password for the LED Matrix web interface
$WEB_USER ALL=(ALL) NOPASSWD: $REBOOT_PATH
$WEB_USER ALL=(ALL) NOPASSWD: $POWEROFF_PATH
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH start ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH stop ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH restart ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH enable ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH disable ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH status ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH is-active ledmatrix
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH is-active ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH start ledmatrix-web
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH stop ledmatrix-web
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH restart ledmatrix-web
$WEB_USER ALL=(ALL) NOPASSWD: $JOURNALCTL_PATH -u ledmatrix.service *
$WEB_USER ALL=(ALL) NOPASSWD: $JOURNALCTL_PATH -u ledmatrix *
$WEB_USER ALL=(ALL) NOPASSWD: $JOURNALCTL_PATH -t ledmatrix *
$WEB_USER ALL=(ALL) NOPASSWD: $PYTHON_PATH $PROJECT_DIR/display_controller.py
$WEB_USER ALL=(ALL) NOPASSWD: $BASH_PATH $PROJECT_DIR/start_display.sh
$WEB_USER ALL=(ALL) NOPASSWD: $BASH_PATH $PROJECT_DIR/stop_display.sh

# Allow web user to remove plugin directories (needed when root-owned __pycache__ blocks update/uninstall)
$WEB_USER ALL=(ALL) NOPASSWD: $RM_PATH -rf $PROJECT_ROOT/plugin-repos/*
$WEB_USER ALL=(ALL) NOPASSWD: $RM_PATH -rf $PROJECT_ROOT/plugins/*
$WEB_USER ALL=(ALL) NOPASSWD: $FIND_PATH $PROJECT_ROOT/plugin-repos -type d -name __pycache__ -user root -exec $RM_PATH -rf {} +
$WEB_USER ALL=(ALL) NOPASSWD: $FIND_PATH $PROJECT_ROOT/plugins -type d -name __pycache__ -user root -exec $RM_PATH -rf {} +
EOF

echo ""
echo "Generated sudoers configuration:"
echo "--------------------------------"
cat "$TEMP_SUDOERS"
echo "--------------------------------"

echo ""
echo "This configuration will allow the web interface to:"
echo "- Start/stop/restart the ledmatrix service"
echo "- Enable/disable the ledmatrix service"
echo "- Check service status"
echo "- View system logs via journalctl"
echo "- Run display_controller.py directly"
echo "- Execute start_display.sh and stop_display.sh"
echo "- Reboot and shutdown the system"
echo "- Remove plugin directories (for update/uninstall when root-owned files block deletion)"
echo ""

# Ask for confirmation
read -p "Do you want to apply this configuration? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Configuration cancelled."
    rm -f "$TEMP_SUDOERS"
    exit 0
fi

# Apply the configuration using visudo
echo "Applying sudoers configuration..."
if sudo cp "$TEMP_SUDOERS" /etc/sudoers.d/ledmatrix_web; then
    echo "Configuration applied successfully!"
    echo ""
    echo "Testing sudo access..."
    
    # Test a few commands
    if sudo -n systemctl status ledmatrix.service > /dev/null 2>&1; then
        echo "✓ systemctl status ledmatrix.service - OK"
    else
        echo "✗ systemctl status ledmatrix.service - Failed"
    fi
    
    if sudo -n test -f "$PROJECT_DIR/start_display.sh"; then
        echo "✓ File access test - OK"
    else
        echo "✗ File access test - Failed"
    fi
    
    echo ""
    echo "Configuration complete! The web interface should now be able to:"
    echo "- Execute system commands without password prompts"
    echo "- Start and stop the LED matrix display"
    echo "- Restart the system if needed"
    echo ""
    echo "You may need to restart the web interface service for changes to take effect:"
    echo "  sudo systemctl restart ledmatrix-web.service"
    
else
    echo "Error: Failed to apply sudoers configuration."
    echo "You may need to run this script with sudo privileges."
    rm -f "$TEMP_SUDOERS"
    exit 1
fi

# Clean up
rm -f "$TEMP_SUDOERS"

echo ""
echo "Configuration script completed successfully!"
