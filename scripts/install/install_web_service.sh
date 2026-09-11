#!/bin/bash

# LED Matrix Web Interface Service Installer
# This script installs and enables the web interface systemd service

set -e

echo "Installing LED Matrix Web Interface Service..."

# Get the actual user who invoked sudo
if [ -n "$SUDO_USER" ]; then
    ACTUAL_USER="$SUDO_USER"
else
    ACTUAL_USER=$(whoami)
fi

# Determine the Project Root Directory (parent of scripts/install/)
PROJECT_ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

# shellcheck source=scripts/install/lib_systemd_render.sh
source "$PROJECT_ROOT_DIR/scripts/install/lib_systemd_render.sh"

echo "Installing for user: $ACTUAL_USER"
echo "Project root directory: $PROJECT_ROOT_DIR"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (use sudo)"
    exit 1
fi

# Render the unit from systemd/ledmatrix-web.service. That template is the
# only description of the unit; this script used to carry its own heredoc copy,
# and install_service.sh a third, which is how the installed unit on real rigs
# ended up missing RestartSec, SyslogIdentifier and CacheDirectory while
# src/startup_validator.py warned about drift on every boot.
TEMPLATE="$PROJECT_ROOT_DIR/systemd/ledmatrix-web.service"
if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: unit template not found at $TEMPLATE"
    exit 1
fi

echo "Writing service file to /etc/systemd/system/ledmatrix-web.service"
ESCAPED_PROJECT_ROOT_DIR=$(sed_escape_replacement "$PROJECT_ROOT_DIR")
ESCAPED_ACTUAL_USER=$(sed_escape_replacement "$ACTUAL_USER")
sed "s|__PROJECT_ROOT_DIR__|$ESCAPED_PROJECT_ROOT_DIR|g; s|__USER__|$ESCAPED_ACTUAL_USER|g" \
    "$TEMPLATE" > /etc/systemd/system/ledmatrix-web.service

# Ensure cache directory exists with proper permissions
# This is a fallback for older systemd versions that don't support CacheDirectory
# Systemd 239+ will automatically create it via CacheDirectory directive
echo "Setting up cache directory..."
CACHE_DIR="/var/cache/ledmatrix"
if [ ! -d "$CACHE_DIR" ]; then
    mkdir -p "$CACHE_DIR"
    # Set group ownership to allow both root and web user access
    # Try to use ACTUAL_USER's group, fallback to root if that fails
    if getent group "$ACTUAL_USER" > /dev/null 2>&1; then
        chown root:"$ACTUAL_USER" "$CACHE_DIR" 2>/dev/null || chown root:root "$CACHE_DIR"
    else
        chown root:root "$CACHE_DIR"
    fi
    chmod 775 "$CACHE_DIR"
    echo "✓ Cache directory created: $CACHE_DIR"
else
    # Ensure permissions are correct
    chmod 775 "$CACHE_DIR" 2>/dev/null || true
    # Try to set group ownership if possible
    if getent group "$ACTUAL_USER" > /dev/null 2>&1; then
        chown root:"$ACTUAL_USER" "$CACHE_DIR" 2>/dev/null || true
    fi
    echo "✓ Cache directory exists: $CACHE_DIR"
fi

# Reload systemd to recognize the new service
echo "Reloading systemd..."
systemctl daemon-reload

# Enable the service to start on boot
echo "Enabling ledmatrix-web.service..."
systemctl enable ledmatrix-web.service

# Start the service
echo "Starting ledmatrix-web.service..."
systemctl start ledmatrix-web.service

# Check service status
echo "Checking service status..."
systemctl status ledmatrix-web.service --no-pager

echo ""
echo "Web interface service installed and started!"
echo "The web interface will now start automatically when:"
echo "1. The system boots"
echo "2. The 'web_display_autostart' setting is true in config/config.json"
echo ""
echo "To check the service status: systemctl status ledmatrix-web.service"
echo "To view logs: journalctl -u ledmatrix-web.service -f"
echo "To stop the service: systemctl stop ledmatrix-web.service"
echo "To disable autostart: systemctl disable ledmatrix-web.service"
