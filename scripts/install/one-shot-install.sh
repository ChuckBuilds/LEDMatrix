#!/bin/bash

# LED Matrix One-Shot Installation Script
# This script provides a single-command installation experience
# Usage: curl -fsSL https://raw.githubusercontent.com/ChuckBuilds/LEDMatrix/main/scripts/install/one-shot-install.sh | bash

set -Eeuo pipefail

# Global state for error tracking
CURRENT_STEP="initialization"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Error handler for explicit failures
on_error() {
    local exit_code=$?
    local line_no=${1:-unknown}
    echo "" >&2
    echo -e "${RED}✗ ERROR: Installation failed at step: $CURRENT_STEP${NC}" >&2
    echo -e "${RED}  Line: $line_no, Exit code: $exit_code${NC}" >&2
    echo "" >&2
    echo "Common fixes:" >&2
    echo "  - Check internet connectivity: ping -c1 8.8.8.8" >&2
    echo "  - Verify sudo access: sudo -v" >&2
    echo "  - Check disk space: df -h /" >&2
    echo "  - If APT lock error: sudo dpkg --configure -a" >&2
    echo "  - Wait a few minutes and try again" >&2
    echo "" >&2
    echo "This script is safe to run multiple times. You can re-run it to continue." >&2
    exit "$exit_code"
}
trap 'on_error $LINENO' ERR

# Helper functions for colored output
print_step() {
    echo ""
    echo -e "${BLUE}==========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}==========================================${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Retry function for network operations
retry() {
    local attempt=1
    local max_attempts=3
    local delay_seconds=5
    while true; do
        if "$@"; then
            return 0
        fi
        local status=$?
        if [ $attempt -ge $max_attempts ]; then
            print_error "Command failed after $attempt attempts: $*"
            return $status
        fi
        print_warning "Command failed (attempt $attempt/$max_attempts). Retrying in ${delay_seconds}s: $*"
        attempt=$((attempt+1))
        sleep "$delay_seconds"
    done
}

# Check network connectivity
check_network() {
    CURRENT_STEP="Network connectivity check"
    print_step "Checking network connectivity..."
    
    if command -v ping >/dev/null 2>&1; then
        if ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1; then
            print_success "Internet connectivity confirmed (ping test)"
            return 0
        fi
    fi
    
    if command -v curl >/dev/null 2>&1; then
        if curl -Is --max-time 5 http://deb.debian.org >/dev/null 2>&1; then
            print_success "Internet connectivity confirmed (curl test)"
            return 0
        fi
    fi
    
    if command -v wget >/dev/null 2>&1; then
        if wget --spider --timeout=5 http://deb.debian.org >/dev/null 2>&1; then
            print_success "Internet connectivity confirmed (wget test)"
            return 0
        fi
    fi
    
    print_error "No internet connectivity detected"
    echo ""
    echo "Please ensure your Raspberry Pi is connected to the internet:"
    echo "  1. Check WiFi/Ethernet connection"
    echo "  2. Test manually: ping -c1 8.8.8.8"
    echo "  3. Then re-run this installation script"
    exit 1
}

# Check disk space
check_disk_space() {
    CURRENT_STEP="Disk space check"
    if ! command -v df >/dev/null 2>&1; then
        print_warning "df command not available, skipping disk space check"
        return 0
    fi
    
    # Check available space in MB
    AVAILABLE_SPACE=$(df -m / | awk 'NR==2{print $4}' || echo "0")
    
    if [ "$AVAILABLE_SPACE" -lt 500 ]; then
        print_error "Insufficient disk space: ${AVAILABLE_SPACE}MB available (need at least 500MB)"
        echo ""
        echo "Please free up disk space before continuing:"
        echo "  - Remove unnecessary packages: sudo apt autoremove"
        echo "  - Clean APT cache: sudo apt clean"
        echo "  - Check large files: sudo du -sh /* | sort -h"
        exit 1
    elif [ "$AVAILABLE_SPACE" -lt 1024 ]; then
        print_warning "Limited disk space: ${AVAILABLE_SPACE}MB available (recommend at least 1GB)"
    else
        print_success "Disk space sufficient: ${AVAILABLE_SPACE}MB available"
    fi
}

# Check for curl or wget, install if missing
ensure_download_tool() {
    CURRENT_STEP="Download tool check"
    if command -v curl >/dev/null 2>&1; then
        print_success "curl is available"
        return 0
    fi
    
    if command -v wget >/dev/null 2>&1; then
        print_success "wget is available"
        return 0
    fi
    
    print_warning "Neither curl nor wget found, installing curl..."
    
    # Try to install curl (may fail if not sudo, but we'll check sudo next)
    if command -v apt-get >/dev/null 2>&1; then
        print_step "Installing curl..."
        if [ "$EUID" -eq 0 ]; then
            retry apt-get update
            retry apt-get install -y curl
            print_success "curl installed successfully"
        else
            print_error "Need sudo to install curl. Please run: sudo apt-get update && sudo apt-get install -y curl"
            echo "Then re-run this installation script."
            exit 1
        fi
    else
        print_error "Cannot install curl: apt-get not available"
        exit 1
    fi
}

# Check and elevate to sudo if needed
check_sudo() {
    CURRENT_STEP="Privilege check"
    if [ "$EUID" -eq 0 ]; then
        print_success "Running with root privileges"
        return 0
    fi
    
    print_warning "Script needs administrator privileges"
    
    # Check if sudo is available
    if ! command -v sudo >/dev/null 2>&1; then
        print_error "sudo is not available and script is not running as root"
        echo ""
        echo "Please either:"
        echo "  1. Run as root: sudo bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/ChuckBuilds/LEDMatrix/main/scripts/install/one-shot-install.sh)\""
        echo "  2. Or install sudo first"
        exit 1
    fi
    
    # Test sudo access
    if ! sudo -n true 2>/dev/null; then
        print_warning "Need sudo password - you may be prompted"
        if ! sudo -v; then
            print_error "Failed to obtain sudo privileges"
            exit 1
        fi
    fi
    
    print_success "Sudo access confirmed"
}

# Check if running on Raspberry Pi (warning only, don't fail)
check_raspberry_pi() {
    CURRENT_STEP="Hardware check"
    if [ -r /proc/device-tree/model ]; then
        DEVICE_MODEL=$(tr -d '\0' </proc/device-tree/model)
        if [[ "$DEVICE_MODEL" == *"Raspberry Pi"* ]]; then
            print_success "Detected Raspberry Pi: $DEVICE_MODEL"
        else
            print_warning "Not running on Raspberry Pi hardware: $DEVICE_MODEL"
            print_warning "LED matrix functionality requires Raspberry Pi hardware"
        fi
    else
        print_warning "Could not detect device model (continuing anyway)"
    fi
}

# Main installation function
main() {
    print_step "LED Matrix One-Shot Installation"
    
    echo "This script will:"
    echo "  1. Check system prerequisites"
    echo "  2. Install required system packages"
    echo "  3. Clone or update the LEDMatrix repository"
    echo "  4. Run the full installation script"
    echo ""
    
    # Prerequisites checks
    check_network
    check_disk_space
    check_raspberry_pi
    ensure_download_tool
    check_sudo
    
    # Install system prerequisites
    CURRENT_STEP="System package installation"
    print_step "Installing system prerequisites..."
    
    # Update package list
    print_success "Updating package list..."
    if [ "$EUID" -eq 0 ]; then
        retry apt-get update
    else
        retry sudo apt-get update
    fi
    
    # Install required packages
    PACKAGES=(
        "git"
        "python3-pip"
        "cython3"
        "build-essential"
        "python3-dev"
        "python3-pillow"
        "scons"
    )
    
    print_success "Installing required packages..."
    for pkg in "${PACKAGES[@]}"; do
        print_success "Installing $pkg..."
        if [ "$EUID" -eq 0 ]; then
            retry apt-get install -y "$pkg"
        else
            retry sudo apt-get install -y "$pkg"
        fi
    done
    
    # Repository cloning/updating
    CURRENT_STEP="Repository setup"
    print_step "Setting up LEDMatrix repository..."
    
    REPO_DIR="$HOME/LEDMatrix"
    REPO_URL="https://github.com/ChuckBuilds/LEDMatrix.git"
    
    if [ -d "$REPO_DIR" ]; then
        print_warning "Directory $REPO_DIR already exists"
        
        # Check if it's a valid git repository
        if [ -d "$REPO_DIR/.git" ]; then
            print_success "Valid git repository found, updating..."
            cd "$REPO_DIR"
            
            # Check if we can pull (may fail if there are local changes)
            if git fetch >/dev/null 2>&1 && git status >/dev/null 2>&1; then
                # Check for local modifications
                if [ -z "$(git status --porcelain)" ]; then
                    print_success "Pulling latest changes..."
                    retry git pull || print_warning "Could not pull latest changes (continuing with existing code)"
                else
                    print_warning "Repository has local modifications, skipping pull"
                    print_warning "Using existing repository state"
                fi
            else
                print_warning "Git repository appears corrupted or has issues"
                print_warning "Attempting to re-clone..."
                cd "$HOME"
                rm -rf "$REPO_DIR"
                print_success "Cloning fresh repository..."
                retry git clone "$REPO_URL" "$REPO_DIR"
            fi
        else
            print_warning "Directory exists but is not a git repository"
            print_warning "Removing and cloning fresh..."
            cd "$HOME"
            rm -rf "$REPO_DIR"
            print_success "Cloning repository..."
            retry git clone "$REPO_URL" "$REPO_DIR"
        fi
    else
        print_success "Cloning repository to $REPO_DIR..."
        retry git clone "$REPO_URL" "$REPO_DIR"
    fi
    
    # Verify repository is accessible
    if [ ! -d "$REPO_DIR" ] || [ ! -f "$REPO_DIR/first_time_install.sh" ]; then
        print_error "Repository setup failed: $REPO_DIR/first_time_install.sh not found"
        exit 1
    fi
    
    print_success "Repository ready at $REPO_DIR"
    
    # Execute main installation script
    CURRENT_STEP="Main installation"
    print_step "Running main installation script..."
    
    cd "$REPO_DIR"
    
    # Make sure the script is executable
    chmod +x first_time_install.sh
    
    # Check if script exists
    if [ ! -f "first_time_install.sh" ]; then
        print_error "first_time_install.sh not found in $REPO_DIR"
        exit 1
    fi
    
    print_success "Starting main installation (this may take 10-30 minutes)..."
    echo ""
    
    # Execute with proper error handling
    # Use sudo if we're not root, otherwise run directly
    if [ "$EUID" -eq 0 ]; then
        bash ./first_time_install.sh
    else
        sudo bash ./first_time_install.sh
    fi
    
    INSTALL_EXIT_CODE=$?
    
    if [ $INSTALL_EXIT_CODE -eq 0 ]; then
        echo ""
        print_step "Installation Complete!"
        print_success "LED Matrix has been successfully installed!"
        echo ""
        echo "Next steps:"
        echo "  1. Configure your settings: sudo nano $REPO_DIR/config/config.json"
        echo "  2. Or use the web interface: http://$(hostname -I | awk '{print $1}'):5000"
        echo "  3. Start the service: sudo systemctl start ledmatrix.service"
        echo ""
    else
        print_error "Main installation script exited with code $INSTALL_EXIT_CODE"
        echo ""
        echo "The installation may have partially completed."
        echo "You can:"
        echo "  1. Re-run this script to continue (it's safe to run multiple times)"
        echo "  2. Check logs in $REPO_DIR/logs/"
        echo "  3. Review the error messages above"
        exit $INSTALL_EXIT_CODE
    fi
}

# Run main function
main "$@"
