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
    WARNINGS=$((WARNINGS + 1))
}

print_error() {
    echo -e "${RED}✗${NC} $1"
    COMPATIBILITY_ISSUES=$((COMPATIBILITY_ISSUES + 1))
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

# Check OS version. The supported releases come from the same library the
# installer uses, so the two cannot disagree.
echo "2. Checking Operating System Version..."
echo "---------------------------------------"
OS_LIB="$(cd "$(dirname "$0")" && pwd)/install/lib_os.sh"
OS_LIB_LOADED=0
OS_RELEASE=""
if [ -f "$OS_LIB" ]; then
    # shellcheck source=scripts/install/lib_os.sh
    . "$OS_LIB"
    OS_LIB_LOADED=1
fi

if [ "$OS_LIB_LOADED" = "0" ]; then
    print_error "$OS_LIB is missing - download LEDMatrix again"
elif [ -r "$LM_OS_RELEASE_FILE" ]; then
    OS_ID=$(lm_os_field ID)
    OS_VERSION_ID=$(lm_os_field VERSION_ID)
    echo "OS: $(lm_os_field PRETTY_NAME)"
    echo "Version ID: ${OS_VERSION_ID:-unknown}"

    # first_time_install.sh refuses anything else, so this is an error here
    # too, not a warning.
    if OS_RELEASE=$(lm_os_release); then
        print_success "Detected $(lm_release_label "$OS_RELEASE") - supported"
    elif [[ "$OS_ID" == "raspbian" ]] || [[ "$OS_ID" == "debian" ]]; then
        print_error "Debian/Raspbian ${OS_VERSION_ID:-unknown} is not supported - the installer requires Raspberry Pi OS Lite, Trixie (Debian 13) or Bookworm (Debian 12)"
    else
        print_error "${OS_ID:-unknown} is not supported - the installer requires Raspberry Pi OS Lite, Trixie (Debian 13) or Bookworm (Debian 12)"
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
        print_success "Running a 6.12 LTS or newer kernel"
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
if [ "$OS_LIB_LOADED" = "1" ] && command -v python3 >/dev/null 2>&1; then
    PYTHON_VERSION=$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
    PYTHON_MINOR_VERSION=$(lm_python_version) || PYTHON_MINOR_VERSION=""
    PYTHON_RANGE="3.${LM_PYTHON_MIN_MINOR}-3.${LM_PYTHON_MAX_MINOR}"

    echo "Python: $PYTHON_VERSION"

    case "$(lm_python_check "$PYTHON_MINOR_VERSION")" in
        ok)
            print_success "Python version is supported ($PYTHON_RANGE)"
            ;;
        too-old)
            # The rgbmatrix bindings declare requires-python >=3.11, so the
            # display cannot be built on anything older.
            print_error "Python $PYTHON_MINOR_VERSION is too old - Python 3.${LM_PYTHON_MIN_MINOR}+ is required"
            ;;
        too-new)
            print_warning "Python $PYTHON_MINOR_VERSION is newer than LEDMatrix has been tested with ($PYTHON_RANGE)"
            ;;
        *)
            print_warning "Could not read the Python version"
            ;;
    esac
    if [ -n "$OS_RELEASE" ] && [ "$PYTHON_MINOR_VERSION" != "$(lm_release_python "$OS_RELEASE")" ]; then
        print_warning "$(lm_release_label "$OS_RELEASE") ships Python $(lm_release_python "$OS_RELEASE"), but python3 runs $PYTHON_MINOR_VERSION"
    fi
elif command -v python3 >/dev/null 2>&1; then
    print_warning "Cannot check the Python version without $OS_LIB"
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
    # dpkg-query rather than `dpkg -l | grep -q`: under pipefail, grep -q
    # exiting on its first match kills dpkg with SIGPIPE and fails the pipeline,
    # which reported installed packages as missing.
    if [ "$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null)" = "install ok installed" ]; then
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

# WiFi setup from the web page and the LEDMatrix-Setup hotspot drive
# NetworkManager, the default on both Bookworm and Trixie.
if [ "$OS_LIB_LOADED" = "1" ]; then
    case "$(lm_network_stack)" in
        networkmanager)
            print_success "NetworkManager manages the network (needed for WiFi setup)"
            ;;
        dhcpcd)
            print_warning "dhcpcd manages the network - WiFi setup from the web page and the setup hotspot need NetworkManager (sudo raspi-config -> Advanced Options -> Network Config)"
            ;;
        *)
            print_warning "Could not tell which service manages the network - WiFi setup from the web page needs NetworkManager"
            ;;
    esac
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

