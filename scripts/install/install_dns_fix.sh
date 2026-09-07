#!/bin/bash

# DNS single-request fix installation script.
#
# Optional. Install this only if plugins that call external APIs (Starlark
# apps, weather, sports, music) are timing out or feel slow to first paint
# while the network is otherwise fine. See the header of
# scripts/utils/apply_dns_single_request.sh for what it changes and why.

set -e

PROJECT_ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
SERVICE_NAME="ledmatrix-dns-fix"
UNIT_SRC="$PROJECT_ROOT_DIR/systemd/$SERVICE_NAME.service"
UNIT_DEST="/etc/systemd/system/$SERVICE_NAME.service"

if [ "$EUID" -eq 0 ]; then
    SYSTEMCTL_CMD="systemctl"
    SUDO=""
else
    SYSTEMCTL_CMD="sudo systemctl"
    SUDO="sudo"
fi

echo "Installing LED Matrix DNS fix service"
echo "Project root directory: $PROJECT_ROOT_DIR"

if [ ! -f "$UNIT_SRC" ]; then
    echo "✗ Missing unit file: $UNIT_SRC"
    exit 1
fi

chmod +x "$PROJECT_ROOT_DIR/scripts/utils/apply_dns_single_request.sh"

echo "Installing $UNIT_DEST..."
sed "s|__PROJECT_ROOT_DIR__|$PROJECT_ROOT_DIR|g" "$UNIT_SRC" \
    | $SUDO tee "$UNIT_DEST" > /dev/null

$SYSTEMCTL_CMD daemon-reload
$SYSTEMCTL_CMD enable "$SERVICE_NAME.service"
$SYSTEMCTL_CMD start "$SERVICE_NAME.service" || echo "⚠ Failed to start service (will retry on reboot)"

echo ""
if grep -qs "^options single-request$" /etc/resolv.conf; then
    echo "✓ 'options single-request' is active in /etc/resolv.conf"
else
    echo "⚠ 'options single-request' is not in /etc/resolv.conf yet."
    echo "  Check what the service reported:"
    echo "    journalctl -u $SERVICE_NAME -n 20"
fi

echo ""
echo "DNS fix installation complete."
echo ""
echo "Useful commands:"
echo "  sudo systemctl status $SERVICE_NAME   # Check status"
echo "  sudo journalctl -u $SERVICE_NAME -n 50  # View logs"
echo "  sudo systemctl disable --now $SERVICE_NAME  # Undo (edit /etc/resolv.conf to remove the line)"
echo ""
