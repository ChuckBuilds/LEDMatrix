#!/bin/bash

# LED Matrix Web Interface V3 Runner
# This script runs the web interface using system Python

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "Starting LED Matrix Web Interface V3..."

# Run the web interface from project root
python3 web_interface/start.py

