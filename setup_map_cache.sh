#!/bin/bash

# LEDMatrix Map Cache Setup Script
# This script sets up the map tile cache directory for the flight tracker

echo "Setting up LEDMatrix map tile cache directory..."

# Get the real user (not root when running with sudo)
REAL_USER=${SUDO_USER:-$USER}

# Check if the main cache directory exists
if [ -d "/var/cache/ledmatrix" ]; then
    echo "✓ Main cache directory exists: /var/cache/ledmatrix"
    CACHE_BASE="/var/cache/ledmatrix"
elif [ -d "$HOME/.ledmatrix_cache" ]; then
    echo "✓ User cache directory exists: $HOME/.ledmatrix_cache"
    CACHE_BASE="$HOME/.ledmatrix_cache"
else
    echo "⚠ No existing cache directory found. Creating user cache directory..."
    mkdir -p "$HOME/.ledmatrix_cache"
    CACHE_BASE="$HOME/.ledmatrix_cache"
fi

# Create map tiles subdirectory
MAP_CACHE_DIR="$CACHE_BASE/map_tiles"
echo "Creating map tile cache directory: $MAP_CACHE_DIR"

if [ ! -d "$MAP_CACHE_DIR" ]; then
    mkdir -p "$MAP_CACHE_DIR"
    echo "✓ Created map tile cache directory"
else
    echo "✓ Map tile cache directory already exists"
fi

# Set permissions
chmod 755 "$MAP_CACHE_DIR"
echo "✓ Set permissions on map tile cache directory"

# Test write access
if [ -w "$MAP_CACHE_DIR" ]; then
    echo "✓ Map tile cache directory is writable"
else
    echo "✗ Map tile cache directory is not writable"
    echo "Trying to fix permissions..."
    chmod 777 "$MAP_CACHE_DIR"
    if [ -w "$MAP_CACHE_DIR" ]; then
        echo "✓ Fixed permissions - directory is now writable"
    else
        echo "✗ Could not fix permissions"
        exit 1
    fi
fi

echo ""
echo "Map tile cache setup complete!"
echo "Cache directory: $MAP_CACHE_DIR"
echo ""
echo "The flight tracker will now cache map tiles to improve performance."
echo "Tiles will be automatically downloaded and cached as needed."
