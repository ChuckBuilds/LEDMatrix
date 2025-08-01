#!/bin/bash

# LED Matrix Web Interface V2 Runner
# This script sets up a virtual environment and runs the web interface

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Setting up LED Matrix Web Interface V2..."

# Check if virtual environment exists
if [ ! -d "venv_web_v2" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv_web_v2
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv_web_v2/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements_web_v2.txt

# Install rgbmatrix module from local source
echo "Installing rgbmatrix module..."
pip install -e rpi-rgb-led-matrix-master/bindings/python

# Run the web interface
echo "Starting web interface on http://0.0.0.0:5001"
python web_interface_v2.py 