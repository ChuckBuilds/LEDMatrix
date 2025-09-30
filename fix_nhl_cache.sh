#!/bin/bash
"""
Script to fix NHL cache issues on Raspberry Pi.
This will clear the NHL cache and restart the display service.
"""

echo "=========================================="
echo "Fixing NHL Cache Issues"
echo "=========================================="

# Clear NHL cache
echo "Clearing NHL cache..."
python3 clear_nhl_cache.py

# Restart the display service to force fresh data fetch
echo "Restarting display service..."
sudo systemctl restart ledmatrix.service

echo "NHL cache cleared and service restarted!"
echo "NHL managers should now fetch fresh data from ESPN API."
echo "Check the logs to see if NHL games are now being displayed."
