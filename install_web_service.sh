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

# Get the home directory of the actual user
USER_HOME=$(eval echo ~$ACTUAL_USER)

# Determine the Project Root Directory (where this script is located)
PROJECT_ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "Installing for user: $ACTUAL_USER"
echo "Project root directory: $PROJECT_ROOT_DIR"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (use sudo)"
    exit 1
fi

# Generate the service file dynamically with the correct paths
echo "Generating service file with dynamic paths..."
WEB_SERVICE_FILE_CONTENT=$(cat <<EOF
[Unit]
Description=LED Matrix Web Interface Service
After=network.target

[Service]
Type=simple
User=${ACTUAL_USER}
WorkingDirectory=${PROJECT_ROOT_DIR}
Environment=USE_THREADING=1
ExecStart=/usr/bin/python3 ${PROJECT_ROOT_DIR}/start_web_conditionally.py
Restart=on-failure
RestartSec=10
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=ledmatrix-web

[Install]
WantedBy=multi-user.target
EOF
)

# Write the service file to systemd directory
echo "Writing service file to /etc/systemd/system/ledmatrix-web.service"
echo "$WEB_SERVICE_FILE_CONTENT" > /etc/systemd/system/ledmatrix-web.service

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
