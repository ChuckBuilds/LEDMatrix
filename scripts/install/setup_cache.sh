#!/bin/bash

# LEDMatrix Cache Setup Script
# This script sets up a persistent cache directory for LEDMatrix
# with proper group permissions for shared access between services

echo "Setting up LEDMatrix persistent cache directory..."

# Create the cache directory
sudo mkdir -p /var/cache/ledmatrix

# Create ledmatrix group if it doesn't exist
if ! getent group ledmatrix > /dev/null 2>&1; then
    sudo groupadd ledmatrix
    echo "Created ledmatrix group"
else
    echo "ledmatrix group already exists"
fi

# Get the real user (not root when running with sudo)
REAL_USER=${SUDO_USER:-$USER}

# Add current user to ledmatrix group
sudo usermod -a -G ledmatrix "$REAL_USER"
echo "Added $REAL_USER to ledmatrix group"

# Add daemon user to ledmatrix group (for main LEDMatrix service)
if id daemon > /dev/null 2>&1; then
    sudo usermod -a -G ledmatrix daemon
    echo "Added daemon user to ledmatrix group"
fi

# Set group ownership to ledmatrix
sudo chown -R :ledmatrix /var/cache/ledmatrix

# Set directory permissions: 775 (rwxrwxr-x) with setgid bit so new files inherit group
sudo find /var/cache/ledmatrix -type d -exec chmod 775 {} \;
sudo chmod g+s /var/cache/ledmatrix

# Set file permissions: 660 (rw-rw----) for group-readable cache files
sudo find /var/cache/ledmatrix -type f -exec chmod 660 {} \;

echo ""
echo "Cache directory created: /var/cache/ledmatrix"
echo "Group ownership: ledmatrix"
echo "Permissions: 775 with setgid (group-writable, new files inherit group)"

# Test if the directory is writable by the current user
if [ -w /var/cache/ledmatrix ]; then
    echo "✓ Cache directory is writable by current user ($REAL_USER)"
else
    echo "✗ Cache directory is not writable by current user"
    echo "  Note: You may need to log out and back in for group changes to take effect"
fi

# Test if the directory is writable by daemon user (which the system runs as)
if id daemon > /dev/null 2>&1; then
    if sudo -u daemon test -w /var/cache/ledmatrix; then
        echo "✓ Cache directory is writable by daemon user"
    else
        echo "✗ Cache directory is not writable by daemon user"
        echo "  This might cause issues when running with sudo"
    fi
fi

echo ""
echo "Setup complete! LEDMatrix will now use persistent caching."
echo "The cache will survive system restarts."
echo ""
echo "IMPORTANT: If you just added yourself to the ledmatrix group,"
echo "you may need to log out and back in (or run 'newgrp ledmatrix')"
echo "for the group membership to take effect."
echo ""
echo "If you see warnings about using temporary cache directory,"
echo "the system will automatically fall back to /tmp/ledmatrix_cache/" 