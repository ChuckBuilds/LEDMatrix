#!/bin/bash

# LEDMatrix Assets Permissions Fix Script
# This script fixes permissions on the assets directory so the application can download and save team logos

echo "Fixing LEDMatrix assets directory permissions..."

# Get the real user (not root when running with sudo)
REAL_USER=${SUDO_USER:-$USER}
# Resolve the home directory of the real user robustly
if command -v getent >/dev/null 2>&1; then
    REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
else
    REAL_HOME=$(eval echo ~"$REAL_USER")
fi
REAL_GROUP=$(id -gn "$REAL_USER")

# Get the project directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ASSETS_DIR="$PROJECT_DIR/assets"

echo "Project directory: $PROJECT_DIR"
echo "Assets directory: $ASSETS_DIR"
echo "Real user: $REAL_USER"
echo "Real group: $REAL_GROUP"

# Check if assets directory exists
if [ ! -d "$ASSETS_DIR" ]; then
    echo "Error: Assets directory does not exist at $ASSETS_DIR"
    exit 1
fi

echo ""
echo "Fixing permissions for assets directory and subdirectories..."

# Set ownership of the entire assets directory to the real user
echo "Setting ownership of assets directory..."
if sudo chown -R "$REAL_USER:$REAL_GROUP" "$ASSETS_DIR"; then
    echo "✓ Set assets directory ownership to $REAL_USER:$REAL_GROUP"
else
    echo "✗ Failed to set assets directory ownership"
    exit 1
fi

# Set permissions to allow read/write for owner, group, and others (for root service user)
# Note: 777 allows root (service user) to write, which is necessary when service runs as root
echo "Setting permissions for assets directory..."
if sudo chmod -R 777 "$ASSETS_DIR"; then
    echo "✓ Set assets directory permissions to 777 (writable by root service user)"
else
    echo "✗ Failed to set assets directory permissions"
    exit 1
fi

# Specifically ensure the sports logos directories are writable
SPORTS_DIRS=(
    "sports/ncaa_logos"
    "sports/nfl_logos"
    "sports/nba_logos"
    "sports/nhl_logos"
    "sports/mlb_logos"
    "sports/milb_logos"
    "sports/soccer_logos"
)

echo ""
echo "Ensuring sports logo directories are writable..."

for SPORTS_DIR in "${SPORTS_DIRS[@]}"; do
    FULL_PATH="$ASSETS_DIR/$SPORTS_DIR"
    echo ""
    echo "Checking directory: $FULL_PATH"
    
    if [ -d "$FULL_PATH" ]; then
        echo "  - Directory exists"
        echo "  - Current permissions:"
        ls -ld "$FULL_PATH"
        
        # Ensure the directory is writable by both the real user and root (service user)
        # Use 777 permissions to allow root (service) to write, or set group ownership
        sudo chmod 777 "$FULL_PATH"
        sudo chown "$REAL_USER:$REAL_GROUP" "$FULL_PATH"
        
        echo "  - Updated permissions:"
        ls -ld "$FULL_PATH"
        
        # Test write access for real user
        echo "  - Testing write access as $REAL_USER..."
        if sudo -u "$REAL_USER" test -w "$FULL_PATH"; then
            echo "    ✓ $FULL_PATH is writable by $REAL_USER"
        else
            echo "    ✗ $FULL_PATH is not writable by $REAL_USER"
        fi
        
        # Test write access for root (service user)
        echo "  - Testing write access as root (service user)..."
        if sudo test -w "$FULL_PATH"; then
            echo "    ✓ $FULL_PATH is writable by root"
        else
            echo "    ✗ $FULL_PATH is not writable by root"
        fi
    else
        echo "  - Directory does not exist, creating it..."
        sudo mkdir -p "$FULL_PATH"
        sudo chown "$REAL_USER:$REAL_GROUP" "$FULL_PATH"
        sudo chmod 777 "$FULL_PATH"
        echo "  - Created directory with proper permissions (writable by root and $REAL_USER)"
    fi
done

echo ""
echo "Testing write access to ncaa_logos directory specifically..."
NCAA_DIR="$ASSETS_DIR/sports/ncaa_logos"
if [ -d "$NCAA_DIR" ]; then
    # Create a test file to verify write access
    TEST_FILE="$NCAA_DIR/.permission_test"
    if sudo -u "$REAL_USER" touch "$TEST_FILE" 2>/dev/null; then
        echo "✓ Successfully created test file in ncaa_logos directory"
        sudo -u "$REAL_USER" rm -f "$TEST_FILE"
        echo "✓ Successfully removed test file"
    else
        echo "✗ Failed to create test file in ncaa_logos directory"
        echo "  This indicates the permission fix did not work properly"
    fi
else
    echo "✗ ncaa_logos directory does not exist"
fi

echo ""
echo "Assets permissions fix completed!"
echo ""
echo "The application should now be able to download and save team logos."
echo "If you still see permission errors, check which user is running the LEDMatrix service"
echo "and ensure it matches the owner above ($REAL_USER)."
echo ""
echo "You may need to restart the LEDMatrix service for the changes to take effect:"
echo "  sudo systemctl restart ledmatrix.service"
