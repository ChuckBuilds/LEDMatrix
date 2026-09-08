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
DROPIN_DIR="/etc/systemd/system/ledmatrix.service.d"

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

# Order ledmatrix.service after the fix. `Before=` in the unit itself only
# orders units already in the same transaction, so a plain
# `systemctl restart ledmatrix` would not wait for it -- and since this fix is
# opt-in, ledmatrix.service cannot carry the dependency in the repo.
# Wants=, not Requires=: a DNS workaround failing should not stop the display.
echo "Installing the ledmatrix.service ordering drop-in..."
$SUDO mkdir -p "$DROPIN_DIR"
printf '[Unit]\nWants=%s.service\nAfter=%s.service\n' "$SERVICE_NAME" "$SERVICE_NAME" \
    | $SUDO tee "$DROPIN_DIR/10-dns-fix.conf" > /dev/null

$SYSTEMCTL_CMD daemon-reload
$SYSTEMCTL_CMD enable "$SERVICE_NAME.service"

# Do not mask a failure here. The unit exits non-zero when it could not apply
# the option -- a systemd-resolved host, an unwritable resolv.conf, a failed
# `resolvconf -u` -- and reporting "installation complete" over that would
# leave the operator believing a workaround is active when it is not.
START_STATUS=0
$SYSTEMCTL_CMD start "$SERVICE_NAME.service" || START_STATUS=$?

echo ""
if grep -qs "^options single-request$" /etc/resolv.conf; then
    echo "✓ 'options single-request' is active in /etc/resolv.conf"
elif [ "$START_STATUS" -ne 0 ]; then
    echo "✗ The DNS fix could not be applied on this host."
    echo "  The service reported why:"
    echo "    journalctl -u $SERVICE_NAME -n 20"
    echo ""
    echo "  The unit is installed and will try again on the next boot. Nothing"
    echo "  else about your install has changed."
    exit "$START_STATUS"
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
echo "  sudo systemctl disable --now $SERVICE_NAME  # Undo the service"
echo "  sudo rm $DROPIN_DIR/10-dns-fix.conf        # Undo the ordering drop-in"
echo "  # then remove the 'options single-request' line from /etc/resolv.conf"
echo ""
