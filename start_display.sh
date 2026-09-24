#!/bin/bash

echo "Starting LED Matrix Display Service..."

# Start the service
sudo systemctl start ledmatrix.service

# Check the status
echo "Service status:"
sudo systemctl status ledmatrix.service

echo ""
echo "LED Matrix Display Service has been started."
echo ""
echo "To stop the service:"
echo "  sudo systemctl stop ledmatrix.service" 