#!/bin/bash

# WiFi Monitor Service Installation Script
# Installs the WiFi monitor daemon service for LED Matrix

set -e

# Get the actual user who invoked sudo
if [ -n "$SUDO_USER" ]; then
    ACTUAL_USER="$SUDO_USER"
else
    ACTUAL_USER=$(whoami)
fi

# Get the home directory of the actual user
USER_HOME=$(eval echo ~$ACTUAL_USER)

# Determine the Project Root Directory (parent of scripts/install/)
PROJECT_ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)

echo "Installing LED Matrix WiFi Monitor Service for user: $ACTUAL_USER"
echo "Using home directory: $USER_HOME"
echo "Project root directory: $PROJECT_ROOT_DIR"

# Check if required packages are installed
echo ""
echo "Checking for required packages..."
MISSING_PACKAGES=()

if ! command -v hostapd >/dev/null 2>&1; then
    MISSING_PACKAGES+=("hostapd")
fi

if ! command -v dnsmasq >/dev/null 2>&1; then
    MISSING_PACKAGES+=("dnsmasq")
fi

if ! command -v nmcli >/dev/null 2>&1 && ! command -v iwlist >/dev/null 2>&1; then
    MISSING_PACKAGES+=("network-manager")
fi

if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    echo "⚠ The following packages are required for WiFi setup:"
    for pkg in "${MISSING_PACKAGES[@]}"; do
        echo "  - $pkg"
    done
    echo ""
    read -p "Install these packages now? (y/N): " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo apt update
        sudo apt install -y "${MISSING_PACKAGES[@]}"
        echo "✓ Packages installed"
    else
        echo "⚠ Skipping package installation. WiFi setup may not work correctly."
    fi
fi

# Create service file with correct paths
echo ""
echo "Creating systemd service file..."
SERVICE_FILE_CONTENT=$(cat <<EOF
[Unit]
Description=LED Matrix WiFi Monitor Daemon
After=network.target
Wants=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_ROOT_DIR
ExecStart=/usr/bin/python3 $PROJECT_ROOT_DIR/scripts/utils/wifi_monitor_daemon.py --interval 30
Restart=on-failure
RestartSec=10
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=ledmatrix-wifi-monitor

[Install]
WantedBy=multi-user.target
EOF
)

echo "$SERVICE_FILE_CONTENT" | sudo tee /etc/systemd/system/ledmatrix-wifi-monitor.service > /dev/null

# Reload systemd
echo "Reloading systemd..."
sudo systemctl daemon-reload

# Enable and start the service
echo "Enabling WiFi monitor service to start on boot..."
sudo systemctl enable ledmatrix-wifi-monitor.service

echo "Starting WiFi monitor service..."
sudo systemctl start ledmatrix-wifi-monitor.service

# Check service status
echo ""
echo "Checking service status..."
if sudo systemctl is-active --quiet ledmatrix-wifi-monitor.service; then
    echo "✓ WiFi monitor service is running"
else
    echo "⚠ WiFi monitor service failed to start. Check logs with:"
    echo "  sudo journalctl -u ledmatrix-wifi-monitor -n 50"
fi

echo ""
echo "WiFi Monitor Service installation complete!"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status ledmatrix-wifi-monitor  # Check status"
echo "  sudo systemctl restart ledmatrix-wifi-monitor  # Restart service"
echo "  sudo journalctl -u ledmatrix-wifi-monitor -f  # View logs"
echo ""

