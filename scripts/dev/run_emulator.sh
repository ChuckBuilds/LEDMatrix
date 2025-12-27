#!/bin/bash
# LEDMatrix Emulator Runner
# This script runs the LEDMatrix system in emulator mode for development and testing

echo "Starting LEDMatrix Emulator..."
echo "Press Ctrl+C to stop"
echo ""

# Set emulator mode
export EMULATOR=true

# Run the main application
python3 run.py
