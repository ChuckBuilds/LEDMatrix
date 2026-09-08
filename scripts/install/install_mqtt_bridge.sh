#!/bin/bash

# Home Assistant MQTT bridge installation script.
#
# Optional. Installs integrations/mqtt_bridge as a service so Home Assistant
# can force display modes, toggle power and set brightness over MQTT.
# See integrations/mqtt_bridge/README.md.

set -e

PROJECT_ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
BRIDGE_DIR="$PROJECT_ROOT_DIR/integrations/mqtt_bridge"
SERVICE_NAME="ledmatrix-mqtt-bridge"
UNIT_SRC="$PROJECT_ROOT_DIR/systemd/$SERVICE_NAME.service"
UNIT_DEST="/etc/systemd/system/$SERVICE_NAME.service"

if [ "$EUID" -eq 0 ]; then
    SYSTEMCTL_CMD="systemctl"
    SUDO=""
else
    SYSTEMCTL_CMD="sudo systemctl"
    SUDO="sudo"
fi

echo "Installing LED Matrix MQTT bridge"
echo "Project root directory: $PROJECT_ROOT_DIR"

if [ ! -f "$BRIDGE_DIR/bridge_config.json" ]; then
    cp "$BRIDGE_DIR/bridge_config.example.json" "$BRIDGE_DIR/bridge_config.json"
    chmod 600 "$BRIDGE_DIR/bridge_config.json"
    echo ""
    echo "⚠ Created $BRIDGE_DIR/bridge_config.json from the example."
    echo "  Edit it with your broker details, then re-run this script."
    echo "  The service will refuse to start until the placeholder password is replaced."
    echo ""
fi

echo "Installing Python dependencies..."
python3 -m pip install -r "$BRIDGE_DIR/requirements.txt" 2>/dev/null \
    || python3 -m pip install --break-system-packages -r "$BRIDGE_DIR/requirements.txt"

echo "Installing $UNIT_DEST..."
sed "s|__PROJECT_ROOT_DIR__|$PROJECT_ROOT_DIR|g" "$UNIT_SRC" \
    | $SUDO tee "$UNIT_DEST" > /dev/null

$SYSTEMCTL_CMD daemon-reload
$SYSTEMCTL_CMD enable "$SERVICE_NAME.service"
$SYSTEMCTL_CMD restart "$SERVICE_NAME.service" || true

echo ""
if $SYSTEMCTL_CMD is-active --quiet "$SERVICE_NAME.service" 2>/dev/null; then
    echo "✓ MQTT bridge is running"
    echo "  The matrix should appear in Home Assistant under Settings > Devices > MQTT."
else
    echo "⚠ MQTT bridge is not running. Check the logs:"
    echo "  sudo journalctl -u $SERVICE_NAME -n 50"
fi

echo ""
echo "Useful commands:"
echo "  sudo systemctl status $SERVICE_NAME"
echo "  sudo journalctl -u $SERVICE_NAME -f"
echo "  sudo systemctl disable --now $SERVICE_NAME   # Undo"
echo ""
