#!/bin/bash
# Fix permissions for the plugins directory
# This script sets up proper permissions for both root service and web service access

echo "Fixing permissions for plugins directory..."

# Get the actual user who invoked sudo
if [ -n "$SUDO_USER" ]; then
    ACTUAL_USER="$SUDO_USER"
else
    ACTUAL_USER=$(whoami)
fi

# Get the project root directory (parent of scripts directory)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT_DIR="$( cd "$SCRIPT_DIR/../.." && pwd )"
PLUGINS_DIR="$PROJECT_ROOT_DIR/plugins"
PLUGIN_REPOS_DIR="$PROJECT_ROOT_DIR/plugin-repos"

echo "Project root directory: $PROJECT_ROOT_DIR"
echo "Plugins directory: $PLUGINS_DIR"
echo "Plugin-repos directory: $PLUGIN_REPOS_DIR"
echo "Actual user: $ACTUAL_USER"
echo ""

# Check and fix home directory permissions if needed
# Home directory needs at least 755 (or 750) so root can traverse it
USER_HOME=$(eval echo ~$ACTUAL_USER)
if [ -d "$USER_HOME" ]; then
    HOME_PERMS=$(stat -c "%a" "$USER_HOME")
    if [ "$HOME_PERMS" = "700" ]; then
        echo "⚠ Home directory has restrictive permissions (700)"
        echo "  Root cannot traverse /home/$ACTUAL_USER to access subdirectories"
        echo "  Fixing home directory permissions to 755..."
        chmod 755 "$USER_HOME"
        echo "✓ Home directory permissions fixed"
    else
        echo "✓ Home directory permissions OK ($HOME_PERMS)"
    fi
fi
echo ""

# Ensure plugins directory exists
if [ ! -d "$PLUGINS_DIR" ]; then
    echo "Creating plugins directory..."
    mkdir -p "$PLUGINS_DIR"
fi

# Set ownership to root:ACTUAL_USER for mixed access
# Root service can read/write, web service (ACTUAL_USER) can read/write
echo "Setting ownership to root:$ACTUAL_USER..."
sudo chown -R root:"$ACTUAL_USER" "$PLUGINS_DIR"

# Set directory permissions (775: rwxrwxr-x)
# Root: read/write/execute, Group (ACTUAL_USER): read/write/execute, Others: read/execute
echo "Setting directory permissions to 2775 (rwxrwxr-x + sticky bit)..."
find "$PLUGINS_DIR" -type d -exec sudo chmod 2775 {} \;

# Set file permissions (664: rw-rw-r--)
# Root: read/write, Group (ACTUAL_USER): read/write, Others: read
echo "Setting file permissions to 664..."
find "$PLUGINS_DIR" -type f -exec sudo chmod 664 {} \;

# Also ensure plugin-repos directory exists with proper permissions
# This is where plugins installed via the plugin store are stored
if [ ! -d "$PLUGIN_REPOS_DIR" ]; then
    echo "Creating plugin-repos directory..."
    mkdir -p "$PLUGIN_REPOS_DIR"
fi

echo "Setting ownership of plugin-repos to root:$ACTUAL_USER..."
sudo chown -R root:"$ACTUAL_USER" "$PLUGIN_REPOS_DIR"

echo "Setting plugin-repos directory permissions to 2775 (rwxrwxr-x + sticky bit)..."
find "$PLUGIN_REPOS_DIR" -type d -exec sudo chmod 2775 {} \;

echo "Setting plugin-repos file permissions to 664..."
find "$PLUGIN_REPOS_DIR" -type f -exec sudo chmod 664 {} \;

echo "Plugin permissions fixed successfully!"
echo ""
echo "Directory structure:"
echo "plugins/:"
ls -la "$PLUGINS_DIR" 2>/dev/null || echo "  (empty or not accessible)"
echo ""
echo "plugin-repos/:"
ls -la "$PLUGIN_REPOS_DIR" 2>/dev/null || echo "  (empty or not accessible)"
echo ""
echo "Permissions summary:"
echo "- Root service: Can read/write plugins (for PWM hardware access)"
echo "- Web service ($ACTUAL_USER): Can read/write plugins (for installation)"
echo "- Others: Can read plugins"

