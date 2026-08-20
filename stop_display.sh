#!/bin/bash

# Get the current user
CURRENT_USER=$(whoami)

echo "Stopping LED Matrix Display Service for user: $CURRENT_USER..."

# Stop the service
sudo systemctl stop ledmatrix.service

# Check the status
echo "Service status:"
sudo systemctl status ledmatrix.service

echo ""
echo "LED Matrix Display Service has been stopped."
echo ""
echo "To start the service again:"
echo "  sudo systemctl start ledmatrix.service" 