#!/bin/bash
# Diagnostic script for Python dependency installation issues
# Run this if pip gets stuck on "Preparing metadata (pyproject.toml)"

set -e

echo "=========================================="
echo "LEDMatrix Dependency Diagnostic Tool"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Project directory: $PROJECT_ROOT_DIR"
echo ""

# Check system resources
echo "=== System Resources ==="
echo "Disk space:"
df -h / | tail -1
echo ""
echo "Memory:"
free -h
echo ""
echo "CPU info:"
grep -E "^model name|^Hardware|^Revision" /proc/cpuinfo | head -3 || echo "CPU info not available"
echo ""

# Check Python and pip versions
echo "=== Python Environment ==="
echo "Python version:"
python3 --version
echo ""
echo "Pip version:"
python3 -m pip --version || echo "pip not available"
echo ""

# Check if timeout command is available
echo "=== Available Tools ==="
if command -v timeout >/dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} timeout command available"
else
    echo -e "${YELLOW}⚠${NC} timeout command not available (install with: sudo apt install coreutils)"
fi

if command -v apt >/dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} apt available"
else
    echo -e "${RED}✗${NC} apt not available"
fi
echo ""

# Check installed build tools
echo "=== Build Tools ==="
BUILD_TOOLS=("gcc" "g++" "make" "python3-dev" "build-essential" "cython3")
for tool in "${BUILD_TOOLS[@]}"; do
    if dpkg -l | grep -q "^ii.*$tool"; then
        echo -e "${GREEN}✓${NC} $tool installed"
    else
        echo -e "${RED}✗${NC} $tool not installed"
    fi
done
echo ""

# Check pip cache
echo "=== Pip Cache ==="
PIP_CACHE_DIR=$(python3 -m pip cache dir 2>/dev/null || echo "unknown")
echo "Pip cache directory: $PIP_CACHE_DIR"
if [ -d "$PIP_CACHE_DIR" ]; then
    CACHE_SIZE=$(du -sh "$PIP_CACHE_DIR" 2>/dev/null | cut -f1 || echo "unknown")
    echo "Cache size: $CACHE_SIZE"
    echo "You can clear the cache with: python3 -m pip cache purge"
fi
echo ""

# Check requirements.txt
echo "=== Requirements File ==="
if [ -f "$PROJECT_ROOT_DIR/requirements.txt" ]; then
    echo -e "${GREEN}✓${NC} requirements.txt found"
    TOTAL_PACKAGES=$(grep -v '^#' "$PROJECT_ROOT_DIR/requirements.txt" | grep -v '^$' | wc -l)
    echo "Total packages: $TOTAL_PACKAGES"
    echo ""
    echo "Packages that may need building from source:"
    grep -v '^#' "$PROJECT_ROOT_DIR/requirements.txt" | grep -v '^$' | grep -E "(numpy|freetype|cython|scipy|pandas)" || echo "  (none detected)"
else
    echo -e "${RED}✗${NC} requirements.txt not found at $PROJECT_ROOT_DIR/requirements.txt"
fi
echo ""

# Test installing a simple package
echo "=== Test Installation ==="
echo "Testing pip with a simple package (setuptools)..."
if python3 -m pip install --break-system-packages --upgrade --quiet setuptools >/dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} Pip is working correctly"
else
    echo -e "${RED}✗${NC} Pip installation test failed"
    echo "Try: python3 -m pip install --break-system-packages --upgrade pip setuptools wheel"
fi
echo ""

# Check for common issues
echo "=== Common Issues Check ==="

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${YELLON}⚠${NC} Running as root - ensure --break-system-packages flag is used"
else
    echo -e "${GREEN}✓${NC} Not running as root (good for user installs)"
fi

# Check network connectivity
if ping -c 1 -W 3 pypi.org >/dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} Network connectivity to PyPI OK"
else
    echo -e "${RED}✗${NC} Cannot reach pypi.org - check network connection"
fi

# Check for proxy issues
if [ -n "${HTTP_PROXY:-}" ] || [ -n "${HTTPS_PROXY:-}" ]; then
    echo -e "${BLUE}ℹ${NC} Proxy configured: HTTP_PROXY=${HTTP_PROXY:-none}, HTTPS_PROXY=${HTTPS_PROXY:-none}"
else
    echo -e "${GREEN}✓${NC} No proxy configured"
fi
echo ""

# Recommendations
echo "=== Recommendations ==="
echo ""
echo "If pip gets stuck on 'Preparing metadata (pyproject.toml)':"
echo ""
echo "1. Install/upgrade build tools:"
echo "   sudo apt update && sudo apt install -y build-essential python3-dev python3-pip python3-setuptools python3-wheel cython3"
echo ""
echo "2. Upgrade pip and build tools:"
echo "   python3 -m pip install --break-system-packages --upgrade pip setuptools wheel"
echo ""
echo "3. Try installing packages one at a time with verbose output:"
echo "   python3 -m pip install --break-system-packages --no-cache-dir --verbose <package-name>"
echo ""
echo "4. For packages that build from source (like numpy), try:"
echo "   - Install pre-built wheels: python3 -m pip install --break-system-packages --only-binary :all: <package>"
echo "   - Or install via apt if available: sudo apt install python3-<package>"
echo ""
echo "5. Clear pip cache if corrupted:"
echo "   python3 -m pip cache purge"
echo ""
echo "6. Check disk space - building packages requires temporary space"
echo "   df -h"
echo ""
echo "7. For slow builds, increase swap space:"
echo "   sudo dphys-swapfile swapoff"
echo "   sudo nano /etc/dphys-swapfile  # Set CONF_SWAPSIZE=2048"
echo "   sudo dphys-swapfile setup"
echo "   sudo dphys-swapfile swapon"
echo ""
echo "8. Install packages with timeout to identify problematic ones:"
echo "   timeout 600 python3 -m pip install --break-system-packages --no-cache-dir --verbose <package>"
echo ""

# Check which packages are already installed
echo "=== Currently Installed Packages ==="
echo "Checking which requirements are already satisfied..."
if [ -f "$PROJECT_ROOT_DIR/requirements.txt" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        if [[ "$line" =~ ^#.*$ ]] || [[ -z "$line" ]]; then
            continue
        fi
        
        PACKAGE_NAME=$(echo "$line" | sed -E 's/[<>=!].*$//' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | tr '[:upper:]' '[:lower:]')
        
        # Try importing the package (basic check)
        if python3 -c "import $PACKAGE_NAME" >/dev/null 2>&1; then
            INSTALLED_VERSION=$(python3 -c "import $PACKAGE_NAME; print(getattr($PACKAGE_NAME, '__version__', 'unknown'))" 2>/dev/null || echo "unknown")
            echo -e "${GREEN}✓${NC} $PACKAGE_NAME ($INSTALLED_VERSION)"
        else
            echo -e "${RED}✗${NC} $PACKAGE_NAME (not installed or import failed)"
        fi
    done < "$PROJECT_ROOT_DIR/requirements.txt" | head -20
    echo ""
    echo "(Showing first 20 packages - run full check with: python3 -m pip check)"
fi
echo ""

echo "=========================================="
echo "Diagnostic complete!"
echo "=========================================="

