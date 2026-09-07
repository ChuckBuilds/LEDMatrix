#!/bin/bash
#
# Edit an installed Starlark app's config in Pixlet's own config UI.
#
# `pixlet serve` runs the app for real, so its form has working cascading
# dropdowns and option lists fetched live -- useful for an app whose choices
# only exist at runtime, or when you want to see the render change as you
# type. The LEDMatrix config form now reads the same runtime schema (see
# PixletRenderer.extract_schema_via_pixlet), so reach for this when you want
# Pixlet's live preview, not because the normal form is missing options.
#
# Deliberately a script you run and then Ctrl+C, not a service: it stops the
# display for the length of the session, and `pixlet serve` listens on a port
# with no authentication. Nothing here should be listening when you are not
# actually editing.
#
# Usage:
#   ./scripts/utils/pixlet_config_editor.sh                 # list installed apps
#   ./scripts/utils/pixlet_config_editor.sh <app_id>        # edit, on localhost
#   ./scripts/utils/pixlet_config_editor.sh <app_id> --lan  # reachable from the LAN
#
# On localhost, reach it from another machine over SSH instead of --lan:
#   ssh -L 8080:localhost:8080 pi@ledpi.local

set -eu

PROJECT_ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
APPS_DIR="$PROJECT_ROOT_DIR/starlark-apps"
PORT="${PIXLET_EDITOR_PORT:-8080}"
BIND_HOST="127.0.0.1"

APP_ID="${1:-}"
if [ "${2:-}" = "--lan" ]; then
    BIND_HOST="0.0.0.0"
fi

list_apps() {
    if [ -d "$APPS_DIR" ]; then
        find "$APPS_DIR" -maxdepth 1 -mindepth 1 -type d -printf '  %f\n' 2>/dev/null | sort
    fi
}

if [ -z "$APP_ID" ]; then
    echo "Usage: $0 <app_id> [--lan]"
    echo ""
    echo "Installed apps:"
    list_apps || true
    [ -n "$(list_apps)" ] || echo "  (none found in $APPS_DIR)"
    exit 1
fi

APP_DIR="$APPS_DIR/$APP_ID"
if [ ! -d "$APP_DIR" ]; then
    echo "No such app: $APP_ID"
    echo ""
    echo "Installed apps:"
    list_apps
    exit 1
fi

STAR_FILE=$(find "$APP_DIR" -maxdepth 1 -iname "*.star" | head -1)
if [ -z "$STAR_FILE" ]; then
    echo "No .star file found in $APP_DIR"
    exit 1
fi

# Same search order the plugin itself uses: the bundled binary for this
# architecture first, then PATH -- so this works on an install that never put
# pixlet on PATH.
find_pixlet() {
    local arch bundled
    case "$(uname -s)-$(uname -m)" in
        Linux-aarch64|Linux-arm64)  arch="pixlet-linux-arm64" ;;
        Linux-x86_64|Linux-amd64)   arch="pixlet-linux-amd64" ;;
        Darwin-arm64)               arch="pixlet-darwin-arm64" ;;
        Darwin-x86_64)              arch="pixlet-darwin-amd64" ;;
        *)                          arch="" ;;
    esac
    bundled="$PROJECT_ROOT_DIR/bin/pixlet/$arch"
    if [ -n "$arch" ] && [ -x "$bundled" ]; then
        echo "$bundled"
        return 0
    fi
    command -v pixlet 2>/dev/null || return 1
}

PIXLET_BIN=$(find_pixlet) || {
    echo "Pixlet not found. Install it with:"
    echo "  ./scripts/download_pixlet.sh"
    exit 1
}

CONFIG_FILE="$APP_DIR/config.json"
if [ -f "$CONFIG_FILE" ]; then
    cp "$CONFIG_FILE" "$CONFIG_FILE.backup"
    echo "Backed up existing config to $CONFIG_FILE.backup"
else
    echo "{}" > "$CONFIG_FILE"
fi

DISPLAY_WAS_RUNNING=false
if systemctl is-active --quiet ledmatrix 2>/dev/null; then
    DISPLAY_WAS_RUNNING=true
fi

# Restart the display however this exits -- Ctrl+C, an error, or pixlet
# dying on its own. Leaving the panel dark because the editor crashed is the
# failure worth guarding against.
cleanup() {
    echo ""
    if [ "$DISPLAY_WAS_RUNNING" = true ]; then
        echo "Restarting the display service..."
        sudo systemctl restart ledmatrix || echo "⚠ Could not restart ledmatrix - do it by hand"
    fi
    echo "Your config as it was before this session: $CONFIG_FILE.backup"
}
trap cleanup EXIT INT TERM

if [ "$DISPLAY_WAS_RUNNING" = true ]; then
    echo "Stopping the display service so it does not read config.json mid-write..."
    sudo systemctl stop ledmatrix
fi

echo ""
echo "Editing:  $APP_ID"
echo "App file: $STAR_FILE"
if [ "$BIND_HOST" = "0.0.0.0" ]; then
    echo "URL:      http://$(hostname):$PORT/"
    echo ""
    echo "⚠ Listening on all interfaces with no authentication. Anyone on this"
    echo "  network can change this app's config while the session is open."
else
    echo "URL:      http://localhost:$PORT/"
    echo ""
    echo "Listening on localhost only. From another machine, forward the port:"
    echo "  ssh -L $PORT:localhost:$PORT $(whoami)@$(hostname)"
fi
echo ""
echo "Changes save straight to the real config as you make them."
echo "Press Ctrl+C when finished - the display restarts automatically."
echo ""

cd "$APP_DIR"
"$PIXLET_BIN" serve "$(basename "$STAR_FILE")" \
    --host "$BIND_HOST" \
    --port "$PORT" \
    --no-browser \
    --saveconfig "$CONFIG_FILE"
