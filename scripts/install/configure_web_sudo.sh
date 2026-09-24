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

# Get the full paths to commands and validate each one
MISSING_CMDS=()

PYTHON_PATH=$(command -v python3)   || true
SYSTEMCTL_PATH=$(command -v systemctl) || true
REBOOT_PATH=$(command -v reboot)    || true
POWEROFF_PATH=$(command -v poweroff)  || true
BASH_PATH=$(command -v bash)        || true
JOURNALCTL_PATH=$(command -v journalctl) || true
SAFE_RM_PATH="$PROJECT_ROOT/scripts/fix_perms/safe_plugin_rm.sh"
SAFE_PIP_INSTALL_PATH="$PROJECT_ROOT/scripts/fix_perms/safe_pip_install.sh"

# Validate required commands (systemctl, bash, python3 are essential)
for CMD_NAME in SYSTEMCTL_PATH BASH_PATH PYTHON_PATH; do
    CMD_VAL="${!CMD_NAME}"
    if [ -z "$CMD_VAL" ]; then
        MISSING_CMDS+=("$CMD_NAME")
    fi
done

if [ ${#MISSING_CMDS[@]} -gt 0 ]; then
    echo "Error: Required commands not found: ${MISSING_CMDS[*]}" >&2
    echo "Cannot generate valid sudoers configuration without these." >&2
    exit 1
fi

# Validate helper scripts exist
if [ ! -f "$SAFE_RM_PATH" ]; then
    echo "Error: Safe plugin removal helper not found: $SAFE_RM_PATH" >&2
    exit 1
fi
if [ ! -f "$SAFE_PIP_INSTALL_PATH" ]; then
    echo "Error: Safe pip install helper not found: $SAFE_PIP_INSTALL_PATH" >&2
    exit 1
fi

# The rules are shared with first_time_install.sh (Step 10) so the two cannot
# drift apart; add or remove a grant in lib_sudoers.sh, not here.
SUDOERS_LIB="$PROJECT_DIR/lib_sudoers.sh"
if [ ! -f "$SUDOERS_LIB" ]; then
    echo "Error: Sudoers rules library not found: $SUDOERS_LIB" >&2
    exit 1
fi
# shellcheck source=scripts/install/lib_sudoers.sh
. "$SUDOERS_LIB"

echo "Command paths:"
echo "  Python: $PYTHON_PATH"
echo "  Systemctl: $SYSTEMCTL_PATH"
echo "  Reboot: ${REBOOT_PATH:-(not found, skipping)}"
echo "  Poweroff: ${POWEROFF_PATH:-(not found, skipping)}"
echo "  Bash: $BASH_PATH"
echo "  Journalctl: ${JOURNALCTL_PATH:-(not found, skipping)}"
echo "  Safe plugin rm: $SAFE_RM_PATH"
echo "  Safe pip install: $SAFE_PIP_INSTALL_PATH"

# Create a temporary sudoers file
TEMP_SUDOERS="/tmp/ledmatrix_web_sudoers_$$"

web_sudoers_rules "$WEB_USER" "$PROJECT_ROOT" "$SYSTEMCTL_PATH" "$BASH_PATH" \
    "$REBOOT_PATH" "$POWEROFF_PATH" "$JOURNALCTL_PATH" > "$TEMP_SUDOERS"

# Never offer to install rules we have not parsed. A malformed drop-in in
# /etc/sudoers.d makes sudo refuse every command for every user.
if command -v visudo >/dev/null 2>&1; then
    if ! visudo -c -f "$TEMP_SUDOERS" >/dev/null 2>&1; then
        echo ""
        echo "✗ The generated sudoers rules did not parse:" >&2
        visudo -c -f "$TEMP_SUDOERS" >&2 || true
        echo "Nothing was changed." >&2
        rm -f "$TEMP_SUDOERS"
        exit 1
    fi
fi

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
echo "- Reboot and shutdown the system"
echo "- Remove plugin directories (for update/uninstall when root-owned files block deletion)"
echo "- Install plugin/base requirements.txt as root (so ledmatrix.service can see them)"
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
# Harden the helper script: root-owned, not writable by web user
echo "Hardening safe_plugin_rm.sh ownership..."
if ! sudo chown root:root "$SAFE_RM_PATH"; then
    echo "Warning: Could not set ownership on $SAFE_RM_PATH"
fi
if ! sudo chmod 755 "$SAFE_RM_PATH"; then
    echo "Warning: Could not set permissions on $SAFE_RM_PATH"
fi
echo "Hardening safe_pip_install.sh ownership..."
if ! sudo chown root:root "$SAFE_PIP_INSTALL_PATH"; then
    echo "Warning: Could not set ownership on $SAFE_PIP_INSTALL_PATH"
fi
if ! sudo chmod 755 "$SAFE_PIP_INSTALL_PATH"; then
    echo "Warning: Could not set permissions on $SAFE_PIP_INSTALL_PATH"
fi

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
    
    if sudo -n test -f "$PROJECT_ROOT/start_display.sh"; then
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
