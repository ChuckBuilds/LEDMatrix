#!/bin/bash
# Fix permissions for the plugins directory

echo "Fixing permissions for plugins directory..."

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Set ownership to ledpi user and group
echo "Setting ownership to ledpi:ledpi..."
sudo chown -R ledpi:ledpi "$SCRIPT_DIR/plugins"

# Set directory permissions (755: rwxr-xr-x)
echo "Setting directory permissions to 755..."
find "$SCRIPT_DIR/plugins" -type d -exec sudo chmod 755 {} \;

# Set file permissions (644: rw-r--r--)
echo "Setting file permissions to 644..."
find "$SCRIPT_DIR/plugins" -type f -exec sudo chmod 644 {} \;

echo "Plugin permissions fixed successfully!"
echo ""
echo "Directory structure:"
ls -la "$SCRIPT_DIR/plugins"
echo ""
echo "Hello-world plugin:"
ls -la "$SCRIPT_DIR/plugins/hello-world"

