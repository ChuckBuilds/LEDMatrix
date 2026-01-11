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
    echo "Installing required packages for WiFi setup:"
    for pkg in "${MISSING_PACKAGES[@]}"; do
        echo "  - $pkg"
    done
    echo ""
    
    # Install packages automatically (no prompt)
    # Use apt directly if running as root, otherwise use sudo
    if [ "$EUID" -eq 0 ]; then
        apt update || echo "⚠ apt update failed, continuing anyway..."
        apt install -y "${MISSING_PACKAGES[@]}" || {
            echo "⚠ Package installation failed, but continuing with WiFi monitor setup"
            echo "  You may need to install packages manually: apt install -y ${MISSING_PACKAGES[*]}"
        }
    else
        sudo apt update || echo "⚠ apt update failed, continuing anyway..."
        sudo apt install -y "${MISSING_PACKAGES[@]}" || {
            echo "⚠ Package installation failed, but continuing with WiFi monitor setup"
            echo "  You may need to install packages manually: sudo apt install -y ${MISSING_PACKAGES[*]}"
        }
    fi
    echo "✓ Package installation completed"
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

if [ "$EUID" -eq 0 ]; then
    echo "$SERVICE_FILE_CONTENT" | tee /etc/systemd/system/ledmatrix-wifi-monitor.service > /dev/null
else
    echo "$SERVICE_FILE_CONTENT" | sudo tee /etc/systemd/system/ledmatrix-wifi-monitor.service > /dev/null
fi

# Check WiFi connection status before enabling service
echo ""
echo "Checking WiFi connection status..."
WIFI_CONNECTED=false
ETHERNET_CONNECTED=false

# Check WiFi status
if command -v nmcli >/dev/null 2>&1; then
    # Check if WiFi is connected
    WIFI_STATUS=$(nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null | grep -i wifi || echo "")
    if echo "$WIFI_STATUS" | grep -q "connected"; then
        WIFI_CONNECTED=true
        SSID=$(nmcli -t -f active,ssid device wifi 2>/dev/null | grep "^yes:" | cut -d: -f2 | head -1)
        if [ -n "$SSID" ]; then
            echo "✓ WiFi is connected to: $SSID"
        else
            echo "✓ WiFi is connected"
        fi
    else
        echo "⚠ WiFi is not connected"
    fi
    
    # Check Ethernet status
    ETH_STATUS=$(nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null | grep -E "ethernet|eth" || echo "")
    if echo "$ETH_STATUS" | grep -q "connected"; then
        ETHERNET_CONNECTED=true
        echo "✓ Ethernet is connected"
    fi
elif command -v ip >/dev/null 2>&1; then
    # Fallback: check using ip command
    if ip addr show wlan0 2>/dev/null | grep -q "inet " && ! ip addr show wlan0 2>/dev/null | grep -q "192.168.4.1"; then
        WIFI_CONNECTED=true
        echo "✓ WiFi appears to be connected (has IP address)"
    else
        echo "⚠ WiFi does not appear to be connected"
    fi
    
    # Check Ethernet
    if ip addr show eth0 2>/dev/null | grep -q "inet " || ip addr show 2>/dev/null | grep -E "eth|enp" | grep -q "inet "; then
        ETHERNET_CONNECTED=true
        echo "✓ Ethernet appears to be connected (has IP address)"
    fi
else
    echo "⚠ Cannot check network status (nmcli and ip commands not available)"
fi

# Warn if neither WiFi nor Ethernet is connected
if [ "$WIFI_CONNECTED" = false ] && [ "$ETHERNET_CONNECTED" = false ]; then
    echo ""
    echo "⚠ WARNING: Neither WiFi nor Ethernet is connected!"
    echo "  The WiFi monitor service will automatically enable AP mode when no network"
    echo "  connection is detected. This will create a WiFi network named 'LEDMatrix-Setup'"
    echo "  that you can connect to for initial configuration."
    echo ""
    echo "  If you want to connect to WiFi first, you can:"
    echo "  1. Connect to WiFi using: sudo nmcli device wifi connect <SSID> password <password>"
    echo "  2. Or connect via Ethernet cable"
    echo "  3. Or proceed with installation - you can connect to LEDMatrix-Setup AP after reboot"
    echo ""
    echo "Proceeding with WiFi monitor installation..."
    echo "  (WiFi monitor will enable AP mode if no network connection is detected)"
fi

# Reload systemd
echo ""
echo "Reloading systemd..."
if [ "$EUID" -eq 0 ]; then
    systemctl daemon-reload
else
    sudo systemctl daemon-reload
fi

# Enable and start the service
echo "Enabling WiFi monitor service to start on boot..."
if [ "$EUID" -eq 0 ]; then
    systemctl enable ledmatrix-wifi-monitor.service
else
    sudo systemctl enable ledmatrix-wifi-monitor.service
fi

echo "Starting WiFi monitor service..."
if [ "$EUID" -eq 0 ]; then
    systemctl start ledmatrix-wifi-monitor.service || echo "⚠ Failed to start service (may start on reboot)"
else
    sudo systemctl start ledmatrix-wifi-monitor.service || echo "⚠ Failed to start service (may start on reboot)"
fi

# Check service status
echo ""
echo "Checking service status..."
if [ "$EUID" -eq 0 ]; then
    SYSTEMCTL_CMD="systemctl"
else
    SYSTEMCTL_CMD="sudo systemctl"
fi

if $SYSTEMCTL_CMD is-active --quiet ledmatrix-wifi-monitor.service 2>/dev/null; then
    echo "✓ WiFi monitor service is running"
else
    echo "⚠ WiFi monitor service failed to start. Check logs with:"
    if [ "$EUID" -eq 0 ]; then
        echo "  journalctl -u ledmatrix-wifi-monitor -n 50"
    else
        echo "  sudo journalctl -u ledmatrix-wifi-monitor -n 50"
    fi
fi

echo ""
echo "WiFi Monitor Service installation complete!"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status ledmatrix-wifi-monitor  # Check status"
echo "  sudo systemctl restart ledmatrix-wifi-monitor  # Restart service"
echo "  sudo journalctl -u ledmatrix-wifi-monitor -f  # View logs"
echo ""

