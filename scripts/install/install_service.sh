#!/bin/bash

# Exit on error
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

# shellcheck source=scripts/install/lib_systemd_render.sh
source "$PROJECT_ROOT_DIR/scripts/install/lib_systemd_render.sh"

echo "Installing LED Matrix Display Service for user: $ACTUAL_USER"
echo "Using home directory: $USER_HOME"
echo "Project root directory: $PROJECT_ROOT_DIR"

# Render the main display unit from its template. The display service runs as
# root (it needs GPIO), so __USER__ is always root here -- unlike the web unit
# below, which runs as whoever installed it.
#
# A missing template or a failed render is fatal: falling through would leave
# whatever unit already sits at /etc/systemd/system/ledmatrix.service (from a
# previous install) untouched, and the enable/start step below would then
# silently reuse that stale unit instead of the one this run was asked to
# install.
if [ -f "$PROJECT_ROOT_DIR/systemd/ledmatrix.service" ]; then
    ESCAPED_PROJECT_ROOT_DIR=$(sed_escape_replacement "$PROJECT_ROOT_DIR")
    MAIN_UNIT_TMP=$(mktemp)
    trap 'rm -f "$MAIN_UNIT_TMP"' EXIT
    if ! sed "s|__PROJECT_ROOT_DIR__|$ESCAPED_PROJECT_ROOT_DIR|g; s|__USER__|root|g" \
        "$PROJECT_ROOT_DIR/systemd/ledmatrix.service" > "$MAIN_UNIT_TMP"; then
        echo "ERROR: failed to render ledmatrix.service from its template." >&2
        exit 1
    fi
    # Copy the service file to the systemd directory
    sudo cp "$MAIN_UNIT_TMP" /etc/systemd/system/ledmatrix.service
    # Clean up
    rm -f "$MAIN_UNIT_TMP"
    trap - EXIT
else
    echo "ERROR: ledmatrix.service template not found at $PROJECT_ROOT_DIR/systemd/ledmatrix.service." >&2
    exit 1
fi


# Reload systemd to recognize the new service (or modified service)
sudo systemctl daemon-reload

if [ -f "/etc/systemd/system/ledmatrix.service" ]; then
    echo "Enabling ledmatrix.service (main display) to start on boot..."
    sudo systemctl enable ledmatrix.service
    echo "Starting ledmatrix.service (main display)..."
    sudo systemctl start ledmatrix.service
else
    echo "Skipping enable/start for ledmatrix.service as it was not configured."
fi

# === LEDMatrix Web Interface service (ledmatrix-web.service) ===
echo "Installing LEDMatrix Web Interface service (ledmatrix-web.service)..."

# Rendered from systemd/ledmatrix-web.service, the same template
# install_web_service.sh uses. This was an inline heredoc until it drifted from
# the template: it had lost Wants=network-online.target, RestartSec,
# SyslogIdentifier, CacheDirectory and Environment=USE_THREADING. Because
# src/startup_validator.py compares the installed unit against the template,
# every boot warned "re-run install_service.sh" -- and doing so reinstalled the
# same stale copy, so the warning could never clear.
#
# As with the main unit above, a missing template or a failed render is
# fatal -- otherwise the enable/start check below would fall back to
# whatever unit (possibly stale) already exists at the destination path.
if [ -f "$PROJECT_ROOT_DIR/systemd/ledmatrix-web.service" ]; then
    ESCAPED_ACTUAL_USER=$(sed_escape_replacement "$ACTUAL_USER")
    WEB_UNIT_TMP=$(mktemp)
    trap 'rm -f "$WEB_UNIT_TMP"' EXIT
    if ! sed "s|__PROJECT_ROOT_DIR__|$ESCAPED_PROJECT_ROOT_DIR|g; s|__USER__|$ESCAPED_ACTUAL_USER|g" \
        "$PROJECT_ROOT_DIR/systemd/ledmatrix-web.service" > "$WEB_UNIT_TMP"; then
        echo "ERROR: failed to render ledmatrix-web.service from its template." >&2
        exit 1
    fi
    sudo cp "$WEB_UNIT_TMP" /etc/systemd/system/ledmatrix-web.service
    rm -f "$WEB_UNIT_TMP"
    trap - EXIT
else
    echo "ERROR: ledmatrix-web.service template not found at $PROJECT_ROOT_DIR/systemd/ledmatrix-web.service." >&2
    exit 1
fi

echo "Reloading systemd daemon for web service..."
sudo systemctl daemon-reload

if [ -f "/etc/systemd/system/ledmatrix-web.service" ]; then
    echo "Enabling ledmatrix-web.service to start on boot..."
    sudo systemctl enable ledmatrix-web.service

    echo "Starting ledmatrix-web.service..."
    sudo systemctl start ledmatrix-web.service

    echo "LEDMatrix Web Interface service (ledmatrix-web.service) installation complete."
    echo "It will start based on the 'web_display_autostart' setting in config/config.json."
else
    echo "Skipping enable/start for ledmatrix-web.service as it was not configured."
fi
# === End of LEDMatrix Web Interface service ===


# Check the status
echo "Service status for main display (ledmatrix.service):"
sudo systemctl status ledmatrix.service || echo "ledmatrix.service not found or failed to get status."
echo "Service status for web interface (ledmatrix-web.service):"
sudo systemctl status ledmatrix-web.service || echo "ledmatrix-web.service not found or failed to get status."

echo ""
echo "LED Matrix Services have been processed."
echo ""
echo "To stop the main display when you SSH in:"
echo "  sudo systemctl stop ledmatrix.service"
echo "To stop the web interface:"
echo "  sudo systemctl stop ledmatrix-web.service"

echo ""
echo "To check if the main display service is running:"
echo "  sudo systemctl status ledmatrix.service"
echo "To check if the web interface service is running:"
echo "  sudo systemctl status ledmatrix-web.service"

echo ""
echo "To restart the main display service:"
echo "  sudo systemctl restart ledmatrix.service"
echo "To restart the web interface service:"
echo "  sudo systemctl restart ledmatrix-web.service"

echo ""
echo "To view logs for the main display:"
echo "  journalctl -u ledmatrix.service"
echo "To view logs for the web interface:"
echo "  journalctl -u ledmatrix-web.service"

echo ""
echo "To disable autostart for the main display:"
echo "  sudo systemctl disable ledmatrix.service"
echo "To disable autostart for the web interface:"
echo "  sudo systemctl disable ledmatrix-web.service" 