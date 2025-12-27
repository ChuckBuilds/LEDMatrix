#!/bin/bash

# LED Matrix Configuration Migration Script
# This script helps migrate existing config.json to the new template-based system

set -e

echo "=========================================="
echo "LED Matrix Configuration Migration Script"
echo "=========================================="
echo ""

# Check if we're in the right directory
if [ ! -f "config/config.template.json" ]; then
    echo "Error: config/config.template.json not found."
    echo "Please run this script from the LEDMatrix project root directory."
    exit 1
fi

# Check if config.json exists
if [ ! -f "config/config.json" ]; then
    echo "No existing config.json found. Creating from template..."
    cp config/config.template.json config/config.json
    echo "✓ Created config/config.json from template"
    echo ""
    echo "You can now edit config/config.json with your preferences."
    exit 0
fi

echo "Existing config.json found. The system will automatically handle migration."
echo ""
echo "What this means:"
echo "- Your current config.json will be preserved"
echo "- New configuration options will be automatically added with default values"
echo "- A backup will be created before any changes"
echo "- The system handles this automatically when it starts"
echo ""
echo "No manual migration is needed. The ConfigManager will handle everything automatically."
echo ""
echo "To see the latest configuration options, you can reference:"
echo "  config/config.template.json"
echo ""
echo "Migration complete!"
