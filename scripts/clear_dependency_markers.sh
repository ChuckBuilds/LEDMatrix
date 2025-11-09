#!/bin/bash
# Clear all plugin dependency markers to force fresh dependency check
# Useful after updating plugins or troubleshooting dependency issues

echo "Clearing plugin dependency markers..."

# Check both possible cache locations
CACHE_DIRS=(
    "/var/cache/ledmatrix"
    "$HOME/.cache/ledmatrix"
)

for CACHE_DIR in "${CACHE_DIRS[@]}"; do
    if [ -d "$CACHE_DIR" ]; then
        echo "Checking $CACHE_DIR..."
        marker_count=$(find "$CACHE_DIR" -name "plugin_*_deps_installed" 2>/dev/null | wc -l)
        if [ "$marker_count" -gt 0 ]; then
            echo "Found $marker_count dependency marker(s) in $CACHE_DIR"
            find "$CACHE_DIR" -name "plugin_*_deps_installed" -delete
            echo "Cleared $marker_count marker(s)"
        else
            echo "No dependency markers found in $CACHE_DIR"
        fi
    fi
done

echo "Done! Dependency markers cleared."
echo "Next startup will check and install dependencies as needed."

