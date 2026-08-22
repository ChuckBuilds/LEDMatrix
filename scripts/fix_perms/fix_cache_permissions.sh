#!/bin/bash

# LEDMatrix Cache Permissions Fix Script
# This script fixes permissions on all known cache directories so they're writable by the daemon or current user
# Also sets up placeholder logo directories for sports managers

echo "Fixing LEDMatrix cache directory permissions..."

# Get the real user (not root when running with sudo)
REAL_USER=${SUDO_USER:-$USER}
# Resolve the home directory of the real user robustly
if command -v getent >/dev/null 2>&1; then
    REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
else
    REAL_HOME=$(eval echo ~"$REAL_USER")
fi
REAL_GROUP=$(id -gn "$REAL_USER")

# Known cache directories for LEDMatrix. Use the actual user's home instead of a hard-coded path.
CACHE_DIRS=(
    "/var/cache/ledmatrix"
    "$REAL_HOME/.ledmatrix_cache"
)

for CACHE_DIR in "${CACHE_DIRS[@]}"; do
    echo ""
    echo "Checking cache directory: $CACHE_DIR"
    if [ ! -d "$CACHE_DIR" ]; then
        echo "  - Directory does not exist. Creating it..."
        sudo mkdir -p "$CACHE_DIR"
    fi
    echo "  - Current permissions:"
    ls -ld "$CACHE_DIR"
    echo "  - Fixing permissions..."
    # Make directory writable by services regardless of user context
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
done

# Set up placeholder logos directory for sports managers
echo ""
echo "Setting up placeholder logos directory for sports managers..."

PLACEHOLDER_DIR="/var/cache/ledmatrix/placeholder_logos"
if [ ! -d "$PLACEHOLDER_DIR" ]; then
    echo "Creating placeholder logos directory: $PLACEHOLDER_DIR"
    sudo mkdir -p "$PLACEHOLDER_DIR"
    sudo chown "$REAL_USER":"$REAL_GROUP" "$PLACEHOLDER_DIR"
    sudo chmod 777 "$PLACEHOLDER_DIR"
else
    echo "Placeholder logos directory already exists: $PLACEHOLDER_DIR"
    sudo chmod 777 "$PLACEHOLDER_DIR"
    sudo chown "$REAL_USER":"$REAL_GROUP" "$PLACEHOLDER_DIR"
fi

echo "  - Current permissions:"
ls -ld "$PLACEHOLDER_DIR"
echo "  - Testing write access as $REAL_USER..."
if sudo -u "$REAL_USER" test -w "$PLACEHOLDER_DIR"; then
    echo "    ✓ Placeholder logos directory is writable by $REAL_USER"
else
    echo "    ✗ Placeholder logos directory is not writable by $REAL_USER"
fi

# Test with daemon user (which the system might run as)
if sudo -u daemon test -w "$PLACEHOLDER_DIR" 2>/dev/null; then
    echo "    ✓ Placeholder logos directory is writable by daemon user"
else
    echo "    ✗ Placeholder logos directory is not writable by daemon user"
fi

echo ""
echo "All cache directory permission fixes attempted."
echo "If you still see errors, check which user is running the LEDMatrix service and ensure it matches the owner above."
echo ""
echo "The system will now create placeholder logos in:"
echo "  $PLACEHOLDER_DIR"
echo "This should eliminate the permission denied warnings for sports logos." 