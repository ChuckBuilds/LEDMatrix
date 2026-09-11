#!/bin/bash

# Cleanup script to remove virtual environment if it exists
# This script removes the venv_web_v2 directory if it exists

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Cleaning up virtual environment..."

# Check if virtual environment exists and remove it
if [ -d "venv_web_v2" ]; then
    echo "Removing existing virtual environment..."
    rm -rf venv_web_v2
    echo "Virtual environment removed successfully"
else
    echo "No virtual environment found to remove"
fi

echo "Cleanup complete!"
