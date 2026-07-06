#!/bin/bash
# safe_pip_install.sh — Install a requirements.txt as root after validating
# that the resolved path is the project's own requirements.txt or a plugin's
# requirements.txt under plugin-repos/ or plugins/.
#
# This script is intended to be called via sudo from the web interface, so
# that packages a plugin declares end up visible to ledmatrix.service (which
# runs as root) rather than only to whichever non-root user runs the web
# interface. Plugin code already runs as root once loaded, so installing its
# declared dependencies as root is not a new trust boundary.
#
# Usage: safe_pip_install.sh <requirements_txt_path>

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <requirements_txt_path>" >&2
    exit 1
fi

TARGET="$1"

# Determine the project root (parent of scripts/fix_perms/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Allowed locations (resolved, no trailing slash):
#   - the project's own requirements.txt
#   - any requirements.txt under plugin-repos/ or plugins/
ALLOWED_EXACT="$(realpath --canonicalize-missing "$PROJECT_ROOT/requirements.txt")"
ALLOWED_BASES=(
    "$(realpath --canonicalize-missing "$PROJECT_ROOT/plugin-repos")"
    "$(realpath --canonicalize-missing "$PROJECT_ROOT/plugins")"
)

# Resolve the target path (follow symlinks); works even if it doesn't exist.
RESOLVED_TARGET="$(realpath --canonicalize-missing "$TARGET")"

# Must be named requirements.txt — never install from an arbitrary file.
if [ "$(basename "$RESOLVED_TARGET")" != "requirements.txt" ]; then
    echo "DENIED: $RESOLVED_TARGET is not a requirements.txt file" >&2
    exit 2
fi

ALLOWED=false
if [ "$RESOLVED_TARGET" = "$ALLOWED_EXACT" ]; then
    ALLOWED=true
else
    for BASE in "${ALLOWED_BASES[@]}"; do
        if [[ "$RESOLVED_TARGET" == "$BASE/"* ]]; then
            ALLOWED=true
            break
        fi
    done
fi

if [ "$ALLOWED" = false ]; then
    echo "DENIED: $RESOLVED_TARGET is not an allowed requirements.txt location" >&2
    echo "Allowed: $ALLOWED_EXACT, or any requirements.txt under: ${ALLOWED_BASES[*]}" >&2
    exit 2
fi

if [ ! -f "$RESOLVED_TARGET" ]; then
    echo "ERROR: $RESOLVED_TARGET does not exist" >&2
    exit 3
fi

PYTHON_PATH="$(command -v python3)"
# --ignore-installed: root's site-packages often has apt/dpkg-managed copies
# of common libraries (requests, urllib3, ...) with no pip RECORD file, which
# pip refuses to uninstall in place ("Cannot uninstall: no RECORD file was
# found"). This tells pip to install the newer version alongside rather than
# aborting the whole requirements.txt install over one such conflict.
exec "$PYTHON_PATH" -m pip install --break-system-packages --ignore-installed -r "$RESOLVED_TARGET"
