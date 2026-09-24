#!/bin/bash

# LEDMatrix Cache Permissions Fix Script
#
# /var/cache/ledmatrix is shared by the display service (root) and the web
# interface (your user) through the ledmatrix group: root:ledmatrix, 2775,
# files 660. scripts/install/setup_cache.sh is what sets that up (the
# installer's Step 2 runs it, and install_web_service.sh keeps the group), so
# this script runs it rather than applying a model of its own. It used to set
# the directory 777 and re-group it to your own group, replacing the ledmatrix
# group everything else relies on.
#
# It also repairs ~/.ledmatrix_cache, the cache manager's fallback when
# /var/cache/ledmatrix is unusable.

echo "Fixing LEDMatrix cache directory permissions..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_CACHE="$SCRIPT_DIR/../install/setup_cache.sh"

# Get the real user (not root when running with sudo)
REAL_USER=${SUDO_USER:-$USER}
# Resolve the home directory of the real user robustly
if command -v getent >/dev/null 2>&1; then
    REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
else
    REAL_HOME=$(eval echo ~"$REAL_USER")
fi
REAL_GROUP=$(id -gn "$REAL_USER")

echo ""
echo "Checking cache directory: /var/cache/ledmatrix"
if [ -f "$SETUP_CACHE" ]; then
    bash "$SETUP_CACHE"
else
    echo "  ✗ $SETUP_CACHE not found; /var/cache/ledmatrix left unchanged."
fi

CACHE_DIR="$REAL_HOME/.ledmatrix_cache"
echo ""
echo "Checking cache directory: $CACHE_DIR"
if [ ! -d "$CACHE_DIR" ]; then
    echo "  - Directory does not exist. Creating it..."
    sudo mkdir -p "$CACHE_DIR"
fi
echo "  - Current permissions:"
ls -ld "$CACHE_DIR"
echo "  - Fixing permissions..."
sudo chmod 777 "$CACHE_DIR"
sudo chown "$REAL_USER":"$REAL_GROUP" "$CACHE_DIR"
echo "  - Updated permissions:"
ls -ld "$CACHE_DIR"
echo "  - Testing write access as $REAL_USER..."
if sudo -u "$REAL_USER" test -w "$CACHE_DIR"; then
    echo "    ✓ $CACHE_DIR is now writable by $REAL_USER"
else
    echo "    ✗ $CACHE_DIR is still not writable by $REAL_USER"
fi
echo "  - Permissions fix complete for $CACHE_DIR."

echo ""
echo "All cache directory permission fixes attempted."
echo "If you still see errors, check which user is running the LEDMatrix service and ensure it matches the owner above."
