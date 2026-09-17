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
# ended up missing RestartSec and SyslogIdentifier while
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

# Health check and rollback for the web UI's automatic updates. Its own unit so
# it survives the web service restart it performs; never enabled -- the web
# interface starts it after an update. Without it, automatic code updates
# stay paused rather than running with nothing to undo them.
for VERIFY_UNIT in ledmatrix-update-verify.service ledmatrix-update-verify.path; do
    VERIFY_TEMPLATE="$PROJECT_ROOT_DIR/systemd/$VERIFY_UNIT"
    if [ -f "$VERIFY_TEMPLATE" ]; then
        echo "Writing unit file to /etc/systemd/system/$VERIFY_UNIT"
        sed "s|__PROJECT_ROOT_DIR__|$ESCAPED_PROJECT_ROOT_DIR|g; s|__USER__|$ESCAPED_ACTUAL_USER|g" \
            "$VERIFY_TEMPLATE" > "/etc/systemd/system/$VERIFY_UNIT"
        chmod 644 "/etc/systemd/system/$VERIFY_UNIT"
    else
        echo "WARNING: $VERIFY_TEMPLATE not found; automatic code updates will stay paused"
    fi
done

# Shared cache directory. The display service (root) and this web service both
# write here and read each other's files, which are created 0660, so the two
# share it through the directory's group: ledmatrix when the installing user
# is in it (first_time_install.sh / setup_cache.sh set that up), otherwise the
# user's own group. setgid makes new files inherit that group.
#
# An existing directory keeps its group whenever the web user can read through
# it -- ledmatrix, or the user's own group where systemd's old CacheDirectory=
# left it -- because re-grouping a working directory strands every file already
# in it on the old group. Only a group the user is not in (root's, or ledmatrix
# for a user outside it) is replaced. This used to force the user's group on
# every run, replacing the ledmatrix group setup_cache.sh had set.
echo "Setting up cache directory..."
CACHE_DIR="/var/cache/ledmatrix"
USER_GROUPS=$(id -nG "$ACTUAL_USER" 2>/dev/null | tr ' ' '\n')
if printf '%s\n' "$USER_GROUPS" | grep -qx ledmatrix; then
    CACHE_GROUP="ledmatrix"
else
    CACHE_GROUP=$(id -gn "$ACTUAL_USER" 2>/dev/null || echo root)
fi
if [ ! -d "$CACHE_DIR" ]; then
    mkdir -p "$CACHE_DIR"
    chown root:"$CACHE_GROUP" "$CACHE_DIR" 2>/dev/null || true
    echo "✓ Cache directory created: $CACHE_DIR"
else
    DIR_GROUP=$(stat -c %G "$CACHE_DIR" 2>/dev/null)
    if ! printf '%s\n' "$USER_GROUPS" | grep -qx "$DIR_GROUP"; then
        if chgrp "$CACHE_GROUP" "$CACHE_DIR" 2>/dev/null; then
            echo "✓ Cache directory group changed from $DIR_GROUP to $CACHE_GROUP"
            # Files already there keep the old group. The display service
            # re-groups its own files when it starts (DiskCache.share_existing_files,
            # which refuses symlinks and hard links); a recursive chgrp here
            # would not. try-restart does nothing if the service is not running.
            if find "$CACHE_DIR" -maxdepth 1 -name '*.json' -user root ! -group "$CACHE_GROUP" -print -quit 2>/dev/null | grep -q .; then
                systemctl try-restart ledmatrix.service 2>/dev/null || true
            fi
        fi
    fi
    echo "✓ Cache directory exists: $CACHE_DIR"
fi
chmod 2775 "$CACHE_DIR" 2>/dev/null || true

# Reload systemd to recognize the new service
echo "Reloading systemd..."
systemctl daemon-reload

# Enable the service to start on boot
echo "Enabling ledmatrix-web.service..."
systemctl enable ledmatrix-web.service

# The path unit is what starts the health check after an automatic update.
if [ -f /etc/systemd/system/ledmatrix-update-verify.path ]; then
    echo "Enabling ledmatrix-update-verify.path..."
    systemctl enable --now ledmatrix-update-verify.path || \
        echo "WARNING: could not enable ledmatrix-update-verify.path; automatic code updates will stay paused"
fi

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
