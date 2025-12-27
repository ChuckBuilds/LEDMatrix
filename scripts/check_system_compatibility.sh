#!/bin/bash

# LEDMatrix System Compatibility Checker
# Verifies system compatibility with LEDMatrix project
# Tests for Raspbian OS version, Python version, and required packages

set -Eeuo pipefail

echo "=========================================="
echo "LEDMatrix System Compatibility Checker"
echo "=========================================="
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Track overall status
COMPATIBILITY_ISSUES=0
WARNINGS=0

# Helper functions
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
    ((WARNINGS++))
}

print_error() {
    echo -e "${RED}✗${NC} $1"
    ((COMPATIBILITY_ISSUES++))
}

# Check if running on Raspberry Pi
echo "1. Checking Raspberry Pi Hardware..."
echo "-----------------------------------"
if [ -r /proc/device-tree/model ]; then
    DEVICE_MODEL=$(tr -d '\0' </proc/device-tree/model)
    echo "Detected device: $DEVICE_MODEL"
    
    if [[ "$DEVICE_MODEL" == *"Raspberry Pi"* ]]; then
        print_success "Running on Raspberry Pi hardware"
    else
        print_warning "Not running on Raspberry Pi hardware - LED matrix functionality will not work"
    fi
else
    print_warning "Could not detect device model - ensure this is a Raspberry Pi"
fi
echo ""

# Check OS version
echo "2. Checking Operating System Version..."
echo "---------------------------------------"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "OS: $PRETTY_NAME"
    echo "Version ID: ${VERSION_ID:-unknown}"
    
    if [[ "$ID" == "raspbian" ]] || [[ "$ID" == "debian" ]]; then
        if [ "${VERSION_ID:-0}" -ge "12" ]; then
            print_success "Running compatible Debian/Raspbian version (${VERSION_ID})"
            
            if [ "${VERSION_ID:-0}" -eq "13" ]; then
                print_success "Detected Debian 13 Trixie - full compatibility expected"
            elif [ "${VERSION_ID:-0}" -eq "12" ]; then
                print_success "Detected Debian 12 Bookworm - full compatibility confirmed"
            fi
        else
            print_warning "Old Debian/Raspbian version (${VERSION_ID}) - upgrade recommended"
        fi
    else
        print_warning "Not running Debian/Raspbian - compatibility not guaranteed"
    fi
else
    print_error "Could not detect OS version"
fi
echo ""

# Check kernel version
echo "3. Checking Kernel Version..."
echo "-----------------------------"
KERNEL_VERSION=$(uname -r)
KERNEL_MAJOR=$(echo "$KERNEL_VERSION" | cut -d. -f1)
KERNEL_MINOR=$(echo "$KERNEL_VERSION" | cut -d. -f2)

echo "Kernel: $KERNEL_VERSION"

if [ "$KERNEL_MAJOR" -ge "6" ]; then
    print_success "Kernel version is compatible (6.x or newer)"
    
    if [ "$KERNEL_MAJOR" -eq "6" ] && [ "$KERNEL_MINOR" -ge "12" ]; then
        print_success "Running latest Trixie kernel (6.12 LTS)"
    fi
elif [ "$KERNEL_MAJOR" -eq "5" ] && [ "$KERNEL_MINOR" -ge "10" ]; then
    print_success "Kernel version is compatible (5.10+)"
else
    print_warning "Kernel version may be too old - upgrade recommended"
fi
echo ""

# Check Python version
echo "4. Checking Python Version..."
echo "-----------------------------"
if command -v python3 >/dev/null 2>&1; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
    PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
    PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
    
    echo "Python: $PYTHON_VERSION"
    
    if [ "$PYTHON_MAJOR" -eq "3" ]; then
        if [ "$PYTHON_MINOR" -ge "10" ] && [ "$PYTHON_MINOR" -le "12" ]; then
            print_success "Python version is fully supported (3.10-3.12)"
        elif [ "$PYTHON_MINOR" -eq "13" ]; then
            print_warning "Python 3.13 detected - most packages compatible, but some may have limited testing"
            print_warning "Please report any compatibility issues you encounter"
        elif [ "$PYTHON_MINOR" -ge "14" ]; then
            print_warning "Python 3.${PYTHON_MINOR} is very new - some packages may not be compatible yet"
        else
            print_warning "Python 3.${PYTHON_MINOR} is outdated - upgrade to 3.10+ recommended"
        fi
    else
        print_error "Python 2.x detected - Python 3.10+ is required"
    fi
else
    print_error "Python 3 not found - installation required"
fi
echo ""

# Check pip availability
echo "5. Checking pip..."
echo "-----------------"
if python3 -m pip --version >/dev/null 2>&1; then
    PIP_VERSION=$(python3 -m pip --version | awk '{print $2}')
    print_success "pip is available (version $PIP_VERSION)"
else
    print_error "pip not found - python3-pip installation required"
fi
echo ""

# Check essential system packages
echo "6. Checking Essential System Packages..."
echo "----------------------------------------"

# List of essential packages
ESSENTIAL_PACKAGES=(
    "python3-dev:Python development headers"
    "python3-pil:Python Imaging Library"
    "build-essential:Build tools"
    "git:Version control"
)

for pkg_info in "${ESSENTIAL_PACKAGES[@]}"; do
    IFS=':' read -r pkg desc <<< "$pkg_info"
    if dpkg -l | grep -q "^ii  $pkg "; then
        print_success "$desc ($pkg) is installed"
    else
        print_warning "$desc ($pkg) not installed - will be installed during setup"
    fi
done
echo ""

# Check for conflicting services
echo "7. Checking for Conflicting Services..."
echo "---------------------------------------"

# Check for services that can interfere with LED matrix
CONFLICTING_SERVICES=(
    "bluetooth:Bluetooth service"
    "bluez:Bluetooth stack"
)

for svc_info in "${CONFLICTING_SERVICES[@]}"; do
    IFS=':' read -r svc desc <<< "$svc_info"
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        print_warning "$desc ($svc) is running - may cause LED matrix timing issues"
    else
        print_success "$desc ($svc) is not running"
    fi
done
echo ""

# Check boot configuration
echo "8. Checking Boot Configuration..."
echo "---------------------------------"

# Check for cmdline.txt location
CMDLINE_FILE=""
if [ -f "/boot/firmware/cmdline.txt" ]; then
    CMDLINE_FILE="/boot/firmware/cmdline.txt"
elif [ -f "/boot/cmdline.txt" ]; then
    CMDLINE_FILE="/boot/cmdline.txt"
fi

if [ -n "$CMDLINE_FILE" ]; then
    print_success "Boot configuration found: $CMDLINE_FILE"
    
    if grep -q '\bisolcpus=3\b' "$CMDLINE_FILE"; then
        print_success "CPU isolation already configured (isolcpus=3)"
    else
        print_warning "CPU isolation not configured - will be set during installation"
    fi
else
    print_warning "Boot configuration file not found"
fi

# Check config.txt location
CONFIG_FILE=""
if [ -f "/boot/firmware/config.txt" ]; then
    CONFIG_FILE="/boot/firmware/config.txt"
elif [ -f "/boot/config.txt" ]; then
    CONFIG_FILE="/boot/config.txt"
fi

if [ -n "$CONFIG_FILE" ]; then
    if grep -q '^dtparam=audio=off' "$CONFIG_FILE"; then
        print_success "Onboard audio already disabled"
    else
        print_warning "Onboard audio not disabled - will be configured during installation"
    fi
fi
echo ""

# Check available memory
echo "9. Checking System Resources..."
echo "-------------------------------"
if command -v free >/dev/null 2>&1; then
    TOTAL_MEM=$(free -m | awk '/^Mem:/{print $2}')
    echo "Total RAM: ${TOTAL_MEM}MB"
    
    if [ "$TOTAL_MEM" -ge "1024" ]; then
        print_success "Sufficient memory available"
    elif [ "$TOTAL_MEM" -ge "512" ]; then
        print_warning "Limited memory (${TOTAL_MEM}MB) - may affect performance"
    else
        print_warning "Very limited memory (${TOTAL_MEM}MB) - performance issues likely"
    fi
fi

# Check disk space
if command -v df >/dev/null 2>&1; then
    AVAILABLE_SPACE=$(df -m / | awk 'NR==2{print $4}')
    echo "Available disk space: ${AVAILABLE_SPACE}MB"
    
    if [ "$AVAILABLE_SPACE" -ge "1024" ]; then
        print_success "Sufficient disk space available"
    elif [ "$AVAILABLE_SPACE" -ge "512" ]; then
        print_warning "Limited disk space (${AVAILABLE_SPACE}MB) - may need cleanup"
    else
        print_error "Very limited disk space (${AVAILABLE_SPACE}MB) - cleanup required"
    fi
fi
echo ""

# Check network connectivity
echo "10. Checking Network Connectivity..."
echo "------------------------------------"
if command -v ping >/dev/null 2>&1; then
    if ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1; then
        print_success "Internet connectivity available"
    else
        print_error "No internet connectivity - required for installation"
    fi
else
    print_warning "Ping command not available - cannot verify network"
fi
echo ""

# Print summary
echo "=========================================="
echo "Compatibility Check Summary"
echo "=========================================="
echo ""

if [ $COMPATIBILITY_ISSUES -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✓ System is fully compatible!${NC}"
    echo "You can proceed with the installation."
    exit 0
elif [ $COMPATIBILITY_ISSUES -eq 0 ]; then
    echo -e "${YELLOW}⚠ System is compatible with ${WARNINGS} warning(s)${NC}"
    echo "Installation can proceed, but review the warnings above."
    exit 0
else
    echo -e "${RED}✗ Found ${COMPATIBILITY_ISSUES} compatibility issue(s) and ${WARNINGS} warning(s)${NC}"
    echo "Please address the errors above before installation."
    exit 1
fi

