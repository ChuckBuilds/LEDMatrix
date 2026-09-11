#!/bin/bash
# Clear Python cache files that might be causing import issues

echo "🧹 Clearing Python cache files..."

# Clear __pycache__ directories
find ~/LEDMatrix -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Clear .pyc files
find ~/LEDMatrix -name "*.pyc" -delete 2>/dev/null || true

# Clear Flask session cache if it exists
rm -rf ~/LEDMatrix/web_interface/.webassets-cache 2>/dev/null || true

echo "✅ Python cache cleared"

echo ""
echo "🔄 Now try running the web interface again:"
echo "cd ~/LEDMatrix"
echo "python3 web_interface/start.py"
