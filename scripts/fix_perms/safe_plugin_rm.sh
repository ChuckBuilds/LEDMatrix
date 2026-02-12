#!/bin/bash
# safe_plugin_rm.sh — Safely remove a plugin directory after validating
# that the resolved path is inside an allowed base directory.
#
# This script is intended to be called via sudo from the web interface.
# It prevents path traversal attacks by resolving symlinks and verifying
# the target is a child of plugin-repos/ or plugins/.
#
# Usage: safe_plugin_rm.sh <target_path>

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <target_path>" >&2
    exit 1
fi

TARGET="$1"

# Determine the project root (parent of scripts/fix_perms/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Allowed base directories (resolved, no trailing slash)
# Use --canonicalize-missing so this works even if the dirs don't exist yet
ALLOWED_BASES=(
    "$(realpath --canonicalize-missing "$PROJECT_ROOT/plugin-repos")"
    "$(realpath --canonicalize-missing "$PROJECT_ROOT/plugins")"
)

# Resolve the target path (follow symlinks)
# Use realpath --canonicalize-missing so it works even if the path
# doesn't fully exist (e.g., partially deleted directory)
RESOLVED_TARGET="$(realpath --canonicalize-missing "$TARGET")"

# Validate: resolved target must be a strict child of an allowed base
# (must not BE the base itself — only children are allowed)
ALLOWED=false
for BASE in "${ALLOWED_BASES[@]}"; do
    if [[ "$RESOLVED_TARGET" == "$BASE/"* ]]; then
        ALLOWED=true
        break
    fi
done

if [ "$ALLOWED" = false ]; then
    echo "DENIED: $RESOLVED_TARGET is not inside an allowed plugin directory" >&2
    echo "Allowed bases: ${ALLOWED_BASES[*]}" >&2
    exit 2
fi

# Safety check: refuse to delete the base directories themselves
for BASE in "${ALLOWED_BASES[@]}"; do
    if [ "$RESOLVED_TARGET" = "$BASE" ]; then
        echo "DENIED: cannot remove plugin base directory itself: $BASE" >&2
        exit 2
    fi
done

# All checks passed — remove the target
rm -rf -- "$RESOLVED_TARGET"
