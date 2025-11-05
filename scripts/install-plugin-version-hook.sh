#!/bin/bash
#
# Install version bumping git hook for all plugin repositories
#
# Usage:
#   ./scripts/install-plugin-version-hook.sh [plugin-dir]
#
# If plugin-dir is provided, installs for that plugin only.
# Otherwise, installs for all plugin repositories found in plugins/ directory.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK_SCRIPT="$SCRIPT_DIR/git-hooks/pre-push-plugin-version"
MAIN_REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ ! -f "$HOOK_SCRIPT" ]; then
    echo "Error: Hook script not found at $HOOK_SCRIPT"
    exit 1
fi

install_hook() {
    local plugin_dir="$1"
    local git_dir="$plugin_dir/.git"
    local hook_path="$git_dir/hooks/pre-push"
    
    if [ ! -d "$git_dir" ]; then
        echo "  ⚠️  Not a git repository, skipping"
        return 1
    fi
    
    if [ ! -f "$plugin_dir/manifest.json" ]; then
        echo "  ⚠️  No manifest.json found, skipping"
        return 1
    fi
    
    # Create hooks directory if it doesn't exist
    mkdir -p "$git_dir/hooks"
    
    # Copy the hook
    cp "$HOOK_SCRIPT" "$hook_path"
    chmod +x "$hook_path"
    
    echo "  ✓ Installed pre-push hook"
    return 0
}

if [ -n "$1" ]; then
    # Install for specific plugin
    PLUGIN_DIR="$1"
    if [ ! -d "$PLUGIN_DIR" ]; then
        echo "Error: Plugin directory not found: $PLUGIN_DIR"
        exit 1
    fi
    
    PLUGIN_NAME="$(basename "$PLUGIN_DIR")"
    echo "Installing version bump hook for: $PLUGIN_NAME"
    install_hook "$PLUGIN_DIR"
else
    # Install for all plugins
    echo "Installing version bump hooks for all plugins..."
    echo ""
    
    PLUGINS_DIR="$MAIN_REPO_ROOT/plugins"
    if [ ! -d "$PLUGINS_DIR" ]; then
        echo "Error: plugins directory not found: $PLUGINS_DIR"
        exit 1
    fi
    
    INSTALLED=0
    SKIPPED=0
    
    for plugin_dir in "$PLUGINS_DIR"/*; do
        if [ -d "$plugin_dir" ]; then
            PLUGIN_NAME="$(basename "$plugin_dir")"
            echo "Processing: $PLUGIN_NAME"
            
            if install_hook "$plugin_dir"; then
                INSTALLED=$((INSTALLED + 1))
            else
                SKIPPED=$((SKIPPED + 1))
            fi
            echo ""
        fi
    done
    
    echo "Summary:"
    echo "  Installed: $INSTALLED"
    echo "  Skipped: $SKIPPED"
fi

