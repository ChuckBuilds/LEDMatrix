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
#   ./scripts/utils/pixlet_config_editor.sh            # list installed apps
#   ./scripts/utils/pixlet_config_editor.sh <app_id>   # edit
#
# Binds the LAN by default, matching the web interface, which already serves
# 0.0.0.0:5000 with no authentication -- anything that can reach this can
# already reconfigure the display there. `pixlet serve` has no authentication
# either, so treat both the same way: fine on a home network, not on an open
# one. Override the bind and the session length with:
#
#   PIXLET_EDITOR_HOST=127.0.0.1 ./scripts/utils/pixlet_config_editor.sh <app>
#   PIXLET_EDITOR_TIMEOUT=600    ./scripts/utils/pixlet_config_editor.sh <app>
#
# For loopback-only editing from another machine, forward the port instead:
#
#   ssh -L 8080:localhost:8080 pi@ledpi.local
#
# The session always ends by itself after PIXLET_EDITOR_TIMEOUT seconds
# (default 30 minutes). The display is stopped while editing, so a session
# left open would otherwise leave the panel dark indefinitely -- the timeout
# is what makes it safe to start one from the web interface.

set -eu

PROJECT_ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
APPS_DIR="$PROJECT_ROOT_DIR/starlark-apps"
PORT="${PIXLET_EDITOR_PORT:-8080}"
# LAN by default; see the header for why, and how to force loopback.
BIND_HOST="${PIXLET_EDITOR_HOST:-0.0.0.0}"
# Hard stop, so the display cannot be left off by a forgotten session.
EDITOR_TIMEOUT="${PIXLET_EDITOR_TIMEOUT:-1800}"

APP_ID="${1:-}"

list_apps() {
    if [ -d "$APPS_DIR" ]; then
        find "$APPS_DIR" -maxdepth 1 -mindepth 1 -type d -printf '  %f\n' 2>/dev/null | sort
    fi
}

if [ -z "$APP_ID" ]; then
    echo "Usage: $0 <app_id>"
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
    # Kill the serve child explicitly. `timeout` is started with --foreground so
    # it shares this script's process group (without that it makes its own, and
    # a group signal aimed at this script would orphan pixlet with the port
    # still bound). Belt and braces: signal the recorded pid too, because a
    # group signal only reaches it while the group is shared.
    if [ -n "${SERVE_PID:-}" ] && kill -0 "$SERVE_PID" 2>/dev/null; then
        kill -TERM "$SERVE_PID" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$SERVE_PID" 2>/dev/null || break
            sleep 0.3
        done
        kill -KILL "$SERVE_PID" 2>/dev/null || true
    fi
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

if [ "$BIND_HOST" = "0.0.0.0" ]; then
    REACH_HOST="$(hostname).local"
else
    REACH_HOST="localhost"
fi

echo ""
echo "Editing:  $APP_ID"
echo "App file: $STAR_FILE"
echo "URL:      http://$REACH_HOST:$PORT/"
echo ""
if [ "$BIND_HOST" = "0.0.0.0" ]; then
    echo "Reachable on the LAN, and pixlet serve has no authentication -- the"
    echo "same footing as the web interface on port 5000. Set"
    echo "PIXLET_EDITOR_HOST=127.0.0.1 to keep it to this machine."
else
    echo "Listening on $BIND_HOST only. From another machine, forward the port:"
    echo "  ssh -L $PORT:localhost:$PORT $(whoami)@$(hostname)"
fi
echo ""
echo "Changes save straight to the real config as you make them."
echo "Press Ctrl+C when finished - the display restarts automatically."
echo "This session stops on its own after ${EDITOR_TIMEOUT}s regardless."
echo ""

cd "$APP_DIR"
# `timeout` owns the hard stop rather than the caller: the trap above restarts
# the display however this exits, so a session that outlives the person who
# started it still gives the panel back. Exit 124 is timeout's own code for
# "expired", which is a normal end here, not a failure.
# --foreground: stay in this script's process group so one signal reaches the
# whole session. Backgrounded + `wait` so the EXIT trap can run while the child
# is still alive; a foreground child would leave bash waiting on it instead.
timeout --foreground "$EDITOR_TIMEOUT" "$PIXLET_BIN" serve "$(basename "$STAR_FILE")" \
    --host "$BIND_HOST" \
    --port "$PORT" \
    --no-browser \
    --saveconfig "$CONFIG_FILE" &
SERVE_PID=$!

status=0
wait "$SERVE_PID" || status=$?
if [ "$status" -eq 124 ]; then
    echo "Session reached its ${EDITOR_TIMEOUT}s limit."
    status=0
fi
exit "$status"
