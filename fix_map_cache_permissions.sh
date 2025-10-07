#!/bin/bash

# LEDMatrix Map Cache Permissions Fix Script
# This script fixes permissions on the map tile cache directory

echo "Fixing LEDMatrix map tile cache permissions..."

# Get the real user (not root when running with sudo)
REAL_USER=${SUDO_USER:-$USER}
REAL_GROUP=$(id -gn "$REAL_USER")

echo "Real user: $REAL_USER"
echo "Real group: $REAL_GROUP"

# Check if the main cache directory exists
if [ -d "/var/cache/ledmatrix" ]; then
    echo "✓ Main cache directory exists: /var/cache/ledmatrix"
    CACHE_BASE="/var/cache/ledmatrix"
elif [ -d "$HOME/.ledmatrix_cache" ]; then
    echo "✓ User cache directory exists: $HOME/.ledmatrix_cache"
    CACHE_BASE="$HOME/.ledmatrix_cache"
else
    echo "⚠ No cache directory found. Creating user cache directory..."
    mkdir -p "$HOME/.ledmatrix_cache"
    CACHE_BASE="$HOME/.ledmatrix_cache"
fi

# Create and fix map tiles directory
MAP_CACHE_DIR="$CACHE_BASE/map_tiles"
echo ""
echo "Working with map tile cache directory: $MAP_CACHE_DIR"

# Create the directory if it doesn't exist
if [ ! -d "$MAP_CACHE_DIR" ]; then
    echo "Creating map tile cache directory..."
    mkdir -p "$MAP_CACHE_DIR"
else
    echo "Map tile cache directory already exists"
fi

# Fix ownership and permissions
echo "Fixing ownership and permissions..."

# Set ownership to the real user
if [ "$CACHE_BASE" = "/var/cache/ledmatrix" ]; then
    # For system cache, we need sudo
    sudo chown -R "$REAL_USER:$REAL_GROUP" "$MAP_CACHE_DIR"
    sudo chmod -R 755 "$MAP_CACHE_DIR"
    echo "✓ Set ownership to $REAL_USER:$REAL_GROUP"
    echo "✓ Set permissions to 755"
else
    # For user cache, we can do it directly
    chown -R "$REAL_USER:$REAL_GROUP" "$MAP_CACHE_DIR" 2>/dev/null || true
    chmod -R 755 "$MAP_CACHE_DIR"
    echo "✓ Set ownership to $REAL_USER:$REAL_GROUP"
    echo "✓ Set permissions to 755"
fi

# Test write access
echo ""
echo "Testing write access..."

# Test as current user
if [ -w "$MAP_CACHE_DIR" ]; then
    echo "✓ Directory is writable by current user"
    
    # Test creating a file
    TEST_FILE="$MAP_CACHE_DIR/.writetest"
    if echo "test" > "$TEST_FILE" 2>/dev/null; then
        echo "✓ Can create files in cache directory"
        rm -f "$TEST_FILE"
    else
        echo "✗ Cannot create files in cache directory"
    fi
else
    echo "✗ Directory is not writable by current user"
    
    # Try to fix with more permissive permissions
    echo "Trying to fix with more permissive permissions..."
    if [ "$CACHE_BASE" = "/var/cache/ledmatrix" ]; then
        sudo chmod 777 "$MAP_CACHE_DIR"
    else
        chmod 777 "$MAP_CACHE_DIR"
    fi
    
    # Test again
    if [ -w "$MAP_CACHE_DIR" ]; then
        echo "✓ Fixed - directory is now writable"
    else
        echo "✗ Still not writable - may need manual intervention"
    fi
fi

echo ""
echo "Map cache permissions fix complete!"
echo "Cache directory: $MAP_CACHE_DIR"
echo ""
echo "The flight tracker should now be able to cache map tiles properly."
