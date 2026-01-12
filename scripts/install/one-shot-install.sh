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
    echo "  - If /tmp permission error: sudo chmod 1777 /tmp" >&2
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
    echo "Please ensure your Raspberry Pi is connected to the internet and try again."
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
    # Ensure AVAILABLE_SPACE has a default value if empty (handles unexpected df output)
    AVAILABLE_SPACE=${AVAILABLE_SPACE:-0}
    
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

# Ensure sudo access
check_sudo() {
    CURRENT_STEP="Sudo access check"
    print_step "Checking sudo access..."
    
    # Check if running as root
    if [ "$EUID" -eq 0 ]; then
        print_success "Running as root"
        return 0
    fi
    
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

# Fix /tmp permissions if needed (common issue when running via curl | bash)
# Note: /tmp permission fixing is now done inline before running first_time_install.sh
# This function is kept for backward compatibility but not actively used
fix_tmp_permissions() {
    CURRENT_STEP="TMP directory check"
    # Only fix if /tmp is actually not writable (don't preemptively fix)
    if [ ! -w /tmp ]; then
        print_warning "/tmp is not writable, attempting to fix..."
        if [ "$EUID" -eq 0 ]; then
            chmod 1777 /tmp 2>/dev/null || true
        else
            sudo chmod 1777 /tmp 2>/dev/null || true
        fi
    fi
    
    # Ensure TMPDIR is set correctly
    if [ -z "${TMPDIR:-}" ] || [ ! -w "${TMPDIR:-/tmp}" ]; then
        export TMPDIR=/tmp
    fi
}

# Main installation function
main() {
    print_step "LED Matrix One-Shot Installation"
    
    echo "This script will:"
    echo "  1. Check prerequisites (network, disk space, sudo)"
    echo "  2. Install system dependencies (git, python3, build tools)"
    echo "  3. Clone the LEDMatrix repository"
    echo "  4. Run the first-time installation script"
    echo ""
    
    # Check prerequisites
    check_network
    check_disk_space
    check_sudo
    # Note: /tmp permissions are checked and fixed inline before running first_time_install.sh
    # (only if actually wrong, not preemptively)
    
    # Install basic system dependencies needed for cloning
    CURRENT_STEP="Installing system dependencies"
    print_step "Installing system dependencies..."
    
    # Validate HOME variable
    if [ -z "${HOME:-}" ]; then
        print_error "HOME environment variable is not set"
        echo "Please set HOME or run: export HOME=\$(eval echo ~\$(whoami))"
        exit 1
    fi
    
    # Update package list first
    if [ "$EUID" -eq 0 ]; then
        retry apt-get update -qq
    else
        retry sudo apt-get update -qq
    fi
    
    # Install git and curl (needed for cloning and the script itself)
    if ! command -v git >/dev/null 2>&1; then
        print_warning "git not found, installing..."
        if [ "$EUID" -eq 0 ]; then
            retry apt-get install -y git curl
        else
            retry sudo apt-get install -y git curl
        fi
        print_success "git and curl installed"
    else
        print_success "git already installed"
    fi
    
    # Determine repository location
    REPO_DIR="${HOME}/LEDMatrix"
    REPO_URL="https://github.com/ChuckBuilds/LEDMatrix.git"
    
    CURRENT_STEP="Repository setup"
    print_step "Setting up repository..."
    
    # Check if directory exists and handle accordingly
    if [ -d "$REPO_DIR" ]; then
        if [ -d "$REPO_DIR/.git" ]; then
            print_warning "Repository already exists at $REPO_DIR"
            print_warning "Pulling latest changes..."
            if ! cd "$REPO_DIR"; then
                print_error "Failed to change to directory: $REPO_DIR"
                exit 1
            fi
            
            # Detect current branch or try main/master
            CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
            if [ "$CURRENT_BRANCH" = "HEAD" ] || [ -z "$CURRENT_BRANCH" ]; then
                CURRENT_BRANCH="main"
            fi
            
            # Try to safely update current branch first (fast-forward only to avoid unintended merges)
            PULL_SUCCESS=false
            if git pull --ff-only origin "$CURRENT_BRANCH" >/dev/null 2>&1; then
                print_success "Repository updated successfully (branch: $CURRENT_BRANCH)"
                PULL_SUCCESS=true
            else
                # Current branch pull failed, check if other branches exist on remote
                # Fetch (don't merge) to verify remote branches exist
                for branch in "main" "master"; do
                    if [ "$branch" != "$CURRENT_BRANCH" ]; then
                        if git fetch origin "$branch" >/dev/null 2>&1; then
                            print_warning "Current branch ($CURRENT_BRANCH) could not be updated, but remote branch '$branch' exists"
                            print_warning "Consider switching branches or resolving conflicts"
                            break
                        fi
                    fi
                done
            fi
            
            if [ "$PULL_SUCCESS" = false ]; then
                print_warning "Git pull failed, but continuing with existing repository"
                print_warning "You may have local changes or the repository may be on a different branch"
            fi
        else
            print_warning "Directory exists but is not a git repository"
            print_warning "Removing and cloning fresh..."
            if ! cd "$HOME"; then
                print_error "Failed to change to home directory: $HOME"
                exit 1
            fi
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
    
    if ! cd "$REPO_DIR"; then
        print_error "Failed to change to repository directory: $REPO_DIR"
        exit 1
    fi
    
    # Make sure the script is executable
    chmod +x first_time_install.sh
    
    # Check if script exists
    if [ ! -f "first_time_install.sh" ]; then
        print_error "first_time_install.sh not found in $REPO_DIR"
        exit 1
    fi
    
    print_success "Starting main installation (this may take 10-30 minutes)..."
    echo ""
    
    # Execute with proper error handling and non-interactive mode
    # Temporarily disable errexit to capture exit code instead of exiting immediately
    set +e
    
    # Check /tmp permissions - only fix if actually wrong (common in automated scenarios)
    # When running manually, /tmp usually has correct permissions (1777)
    TMP_PERMS=$(stat -c '%a' /tmp 2>/dev/null || echo "unknown")
    if [ "$TMP_PERMS" != "1777" ] && [ "$TMP_PERMS" != "unknown" ]; then
        CURRENT_STEP="Fixing /tmp permissions"
        print_warning "/tmp has incorrect permissions ($TMP_PERMS), fixing to 1777..."
        if [ "$EUID" -eq 0 ]; then
            chmod 1777 /tmp 2>/dev/null || print_warning "Failed to fix /tmp permissions, continuing anyway..."
        else
            sudo chmod 1777 /tmp 2>/dev/null || print_warning "Failed to fix /tmp permissions, continuing anyway..."
        fi
    fi
    
    # Execute main installation script with non-interactive mode
    CURRENT_STEP="Main installation"
    export TMPDIR=/tmp
    if [ "$EUID" -eq 0 ]; then
        # Run in non-interactive mode with ASSUME_YES (both -y flag and env var for safety)
        export LEDMATRIX_ASSUME_YES=1
        bash ./first_time_install.sh -y
    else
        # Pass both -y flag AND environment variable for non-interactive mode
        # This ensures it works even if the script re-executes itself with sudo
        # Also ensure stdin is properly handled for non-interactive mode
        sudo -E env TMPDIR=/tmp LEDMATRIX_ASSUME_YES=1 bash ./first_time_install.sh -y </dev/null
    fi
    INSTALL_EXIT_CODE=$?
    set -e  # Re-enable errexit
    
    if [ $INSTALL_EXIT_CODE -eq 0 ]; then
        echo ""
        print_step "Installation Complete!"
        print_success "LED Matrix has been successfully installed!"
        echo ""
        echo "Next steps:"
        echo "  1. Configure your settings: sudo nano $REPO_DIR/config/config.json"
        if command -v hostname >/dev/null 2>&1; then
            IP_ADDRESS=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "")
            if [ -n "$IP_ADDRESS" ] && [ "$IP_ADDRESS" != "127.0.0.1" ]; then
                echo "  2. Or use the web interface: http://$IP_ADDRESS:5000"
            else
                echo "  2. Or use the web interface: http://<your-pi-ip>:5000"
            fi
        else
            echo "  2. Or use the web interface: http://<your-pi-ip>:5000"
        fi
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
