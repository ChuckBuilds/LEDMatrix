#!/bin/bash

# LED Matrix One-Shot Installation Script
# This script provides a single-command installation experience
# Usage: curl -fsSL https://raw.githubusercontent.com/ChuckBuilds/LEDMatrix/main/scripts/install/one-shot-install.sh | bash
#
# A new install runs the newest release (the stable update channel). For the
# newest code from main instead (the beta channel), set LEDMATRIX_CHANNEL=beta:
#   curl -fsSL https://raw.githubusercontent.com/ChuckBuilds/LEDMatrix/main/scripts/install/one-shot-install.sh | LEDMATRIX_CHANNEL=beta bash

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
    local status
    while true; do
        # The condition of an if doesn't trip errexit, and in the else branch
        # $? is the command's own exit status. (This used to be `if ! "$@";
        # then status=$?`, where $? is the status of the negation -- always 0 --
        # so a failure never retried and was reported as success.)
        if "$@"; then
            return 0
        else
            status=$?
        fi
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

# Report available memory so the user knows what to expect before the wait.
#
# Informational only — first_time_install.sh does the real work of capping
# build parallelism and adding temporary swap. Never fatal: a low-RAM Pi is
# supported, it is just slower.
check_memory() {
    CURRENT_STEP="Memory check"
    if [ ! -r /proc/meminfo ]; then
        print_warning "Cannot read /proc/meminfo, skipping memory check"
        return 0
    fi

    TOTAL_RAM_MB=$(awk '/^MemTotal:/ {printf "%d\n", $2 / 1024; exit}' /proc/meminfo 2>/dev/null || echo 0)
    TOTAL_RAM_MB=${TOTAL_RAM_MB:-0}

    if [ "$TOTAL_RAM_MB" -eq 0 ]; then
        print_warning "Could not determine system memory, continuing"
    elif [ "$TOTAL_RAM_MB" -lt 2048 ]; then
        print_warning "Low memory: ${TOTAL_RAM_MB}MB RAM"
        echo "  The rpi-rgb-led-matrix C++ build needs more memory than this Pi has."
        echo "  The installer will compile with fewer parallel jobs and add a temporary"
        echo "  swapfile for the build, removing it afterwards. That step will take"
        echo "  15-25 minutes rather than the usual 2-5."
    else
        print_success "Memory sufficient: ${TOTAL_RAM_MB}MB RAM"
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

# --- release checkout helpers ------------------------------------------------
# Which version an install runs. The rules are web_interface/update_channel.py's,
# so the installer and the web interface's updates agree:
#   stable (default)  the newest vX.Y.Z tag by semantic version; pre-releases
#                     (v3.8.0-rc1), leading zeros and other tags are ignored
#   beta              main, the newest code
# Never backwards: an existing checkout moves to a release only when that
# release contains its current commit (git merge-base --is-ancestor).
# Never fatal: whatever goes wrong, the install carries on with the checkout
# as it is.

# Print "stable" or "beta": LEDMATRIX_CHANNEL when it is set, else the
# existing install's auto_update.channel (CONFIG_FILE), else stable.
_lm_channel() {
    local config_file="${1:-}" value
    value=$(printf '%s' "${LEDMATRIX_CHANNEL:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
    case "$value" in
        stable|beta) printf '%s\n' "$value"; return 0 ;;
        "") ;;
        *) print_warning "LEDMATRIX_CHANNEL=${LEDMATRIX_CHANNEL} is not stable or beta; using stable" >&2
           printf 'stable\n'; return 0 ;;
    esac
    if [ -n "$config_file" ] && [ -f "$config_file" ] && command -v python3 >/dev/null 2>&1; then
        value=$(python3 - "$config_file" 2>/dev/null <<'PY' || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        section = json.load(f).get("auto_update")
    value = section.get("channel") if isinstance(section, dict) else None
    print(value.strip().lower() if isinstance(value, str) else "")
except Exception:
    print("")
PY
)
        if [ "$value" = "beta" ]; then
            printf 'beta\n'
            return 0
        fi
    fi
    printf 'stable\n'
}

# Print the newest release tag of the repository in the current directory,
# or nothing when it has none.
_lm_newest_release_tag() {
    git tag --list 'v*' 2>/dev/null \
        | grep -E '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' \
        | sort -t. -k1.2,1n -k2,2n -k3,3n \
        | tail -n 1 || true
}

# A fresh clone (on main): move to the newest release unless beta was asked for.
_lm_checkout_release_after_clone() {
    local channel tag
    channel=$(_lm_channel "")
    if [ "$channel" = "beta" ]; then
        print_success "Beta channel: installing the newest code from main"
        return 0
    fi
    tag=$(_lm_newest_release_tag)
    if [ -z "$tag" ]; then
        print_warning "No release found; installing the newest code from main"
        return 0
    fi
    if git -c advice.detachedHead=false checkout --quiet --detach "${tag}^{commit}"; then
        print_success "Installing release $tag (stable channel)"
    else
        print_warning "Could not check out release $tag; installing the newest code from main"
    fi
    return 0
}

# An existing checkout: move it forward along its channel, never backwards.
# Returns 1 when it should be updated the way it always was (a fast-forward
# pull of its branch): beta, or stable on a branch newer than every release.
_lm_update_existing_checkout() {
    local channel tag head tag_sha
    channel=$(_lm_channel "config/config.json")
    if [ "$channel" = "beta" ]; then
        return 1
    fi
    if ! git fetch --quiet --tags --force origin >/dev/null 2>&1; then
        print_warning "Could not fetch release tags; keeping the current version"
        return 0
    fi
    tag=$(_lm_newest_release_tag)
    head=$(git rev-parse --verify --quiet HEAD 2>/dev/null || true)
    if [ -n "$tag" ] && [ -n "$head" ] && git merge-base --is-ancestor "$head" "$tag" 2>/dev/null; then
        tag_sha=$(git rev-parse --verify --quiet "${tag}^{commit}" 2>/dev/null || true)
        if [ "$head" = "$tag_sha" ]; then
            print_success "Already on the newest release, $tag"
        elif git -c advice.detachedHead=false checkout --quiet --detach "${tag}^{commit}"; then
            print_success "Updated to release $tag (stable channel)"
        else
            print_warning "Could not move to release $tag (local changes?); keeping the current version"
        fi
        return 0
    fi
    if git symbolic-ref --quiet HEAD >/dev/null 2>&1; then
        # Newer than the newest release (or no release yet): follow the branch
        # until a release includes this version, as updates do.
        return 1
    fi
    print_success "This checkout is newer than the newest release${tag:+ ($tag)}; leaving it as it is"
    return 0
}
# --- end release checkout helpers --------------------------------------------

# Main installation function
main() {
    print_step "LED Matrix One-Shot Installation"
    
    echo "This script will:"
    echo "  1. Check prerequisites (network, disk space, memory, sudo)"
    echo "  2. Install system dependencies (git, python3, build tools)"
    echo "  3. Clone the LEDMatrix repository"
    echo "  4. Run the first-time installation script"
    echo ""
    
    # Check prerequisites
    check_network
    check_disk_space
    check_memory
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
    
    # Update package list first. first_time_install.sh is told the lists are
    # already fresh so it does not repeat this a minute later.
    # A refresh that still fails after retries (say one unreachable mirror)
    # only warns: that is what this step effectively did before retry()
    # could report a failure, and making it fatal would stop installs that
    # work today.
    if [ "$EUID" -eq 0 ]; then
        retry apt-get update -qq || print_warning "apt-get update failed; continuing with the existing package lists"
    else
        retry sudo apt-get update -qq || print_warning "apt-get update failed; continuing with the existing package lists"
    fi
    export LEDMATRIX_APT_UPDATED=1
    
    # Install git and curl (needed for cloning and the script itself)
    if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
        print_warning "git or curl not found, installing..."
        # Not fatal here, for the same reason: without git the clone below
        # fails and stops the install with its own error.
        if [ "$EUID" -eq 0 ]; then
            retry apt-get install -y git curl || true
        else
            retry sudo apt-get install -y git curl || true
        fi
        if command -v git >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
            print_success "git and curl installed"
        else
            print_warning "Could not install git and curl"
        fi
    else
        print_success "git and curl already installed"
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
            # Stable: the newest release, if it contains this version.
            if _lm_update_existing_checkout; then
                PULL_SUCCESS=true
            elif git pull --ff-only origin "$CURRENT_BRANCH" >/dev/null 2>&1; then
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
            (cd "$REPO_DIR" && _lm_checkout_release_after_clone) || print_warning "Could not choose a release; installing the newest code from main"
        fi
    else
        print_success "Cloning repository to $REPO_DIR..."
        retry git clone "$REPO_URL" "$REPO_DIR"
        (cd "$REPO_DIR" && _lm_checkout_release_after_clone) || print_warning "Could not choose a release; installing the newest code from main"
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
    # Temporarily disable errexit AND the ERR trap to capture exit code instead of
    # exiting immediately. `set +e` alone does not suppress the ERR trap, so without
    # `trap '' ERR` a non-zero exit from first_time_install.sh would trigger on_error
    # here with the generic "Main installation" message instead of the detailed
    # if/else handling below.
    set +e
    trap '' ERR

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
        # LEDMATRIX_APT_UPDATED is passed explicitly rather than relying on
        # -E: a sudoers env_reset/env_keep policy can strip exported variables,
        # which would silently reinstate the duplicate apt update.
        sudo -E env TMPDIR=/tmp LEDMATRIX_ASSUME_YES=1 \
            LEDMATRIX_APT_UPDATED="${LEDMATRIX_APT_UPDATED:-0}" \
            LEDMATRIX_AUTO_UPDATE="${LEDMATRIX_AUTO_UPDATE:-}" \
            LEDMATRIX_CHANNEL="${LEDMATRIX_CHANNEL:-}" \
            bash ./first_time_install.sh -y </dev/null
    fi
    INSTALL_EXIT_CODE=$?
    trap 'on_error $LINENO' ERR  # Re-enable ERR trap
    set -e  # Re-enable errexit
    
    if [ $INSTALL_EXIT_CODE -eq 0 ]; then
        echo ""
        print_step "Installation Complete!"
        print_success "LED Matrix has been successfully installed!"
        echo ""
        # first_time_install.sh -y reboots as its last action, so by now the
        # reboot is under way (unless LEDMATRIX_SKIP_REBOOT_PROMPT=1 was set).
        if [ "${LEDMATRIX_SKIP_REBOOT_PROMPT:-0}" != "1" ]; then
            echo "The installer has just started a reboot to finish setup, so this"
            echo "session may disconnect now. Give the Pi a few minutes to come back, then:"
            echo ""
        fi
        echo "Next steps:"
        echo "  1. Configure your settings: sudo nano $REPO_DIR/config/config.json"
        if command -v hostname >/dev/null 2>&1; then
            # Get first usable IP address (filter out loopback, IPv6 loopback, and link-local)
            IP_ADDRESS=$(hostname -I 2>/dev/null | awk '{for(i=1;i<=NF;i++){ip=$i; if(ip!="127.0.0.1" && ip!="::1" && substr(ip,1,5)!="fe80:"){print ip; exit}}}' || echo "")
            if [ -n "$IP_ADDRESS" ]; then
                # Check if IPv6 address (contains colons but no periods)
                if [[ "$IP_ADDRESS" =~ .*:.* ]] && [[ ! "$IP_ADDRESS" =~ .*\..* ]]; then
                    # IPv6 addresses need brackets in URLs
                    echo "  2. Or use the web interface: http://[$IP_ADDRESS]:5000"
                else
                    # IPv4 address
                    echo "  2. Or use the web interface: http://$IP_ADDRESS:5000"
                fi
            else
                echo "  2. Or use the web interface: http://<your-pi-ip>:5000"
            fi
        else
            echo "  2. Or use the web interface: http://<your-pi-ip>:5000"
        fi
        echo "  3. The display service starts on boot; to start it by hand: sudo systemctl start ledmatrix.service"
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
