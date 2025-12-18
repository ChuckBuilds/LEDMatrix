#!/bin/bash

# LED Matrix First-Time Installation Script
# This script handles the complete setup for a new LED Matrix installation

set -Eeuo pipefail

# Global state for nicer error messages
CURRENT_STEP="initialization"

# Error handler for friendlier failures
on_error() {
    local exit_code=$?
    local line_no=${1:-unknown}
    echo "✗ An error occurred during: $CURRENT_STEP (line $line_no, exit $exit_code)" >&2
    if [ -n "${LOG_FILE:-}" ]; then
        echo "See the log for details: $LOG_FILE" >&2
        echo "-- Last 50 lines from log --" >&2
        tail -n 50 "$LOG_FILE" >&2 || true
    fi
    echo "\nCommon fixes:" >&2
    echo "- Ensure the Pi is online (try: ping -c1 8.8.8.8)." >&2
    echo "- If you saw an APT lock error: wait a minute, close other installers, then run: sudo dpkg --configure -a" >&2
    echo "- Re-run this script. It is safe to run multiple times." >&2
    exit "$exit_code"
}
trap 'on_error $LINENO' ERR

echo "=========================================="
echo "LED Matrix First-Time Installation Script"
echo "=========================================="
echo ""

# Show device model if available (helps users confirm they're on a Raspberry Pi)
if [ -r /proc/device-tree/model ]; then
    DEVICE_MODEL=$(tr -d '\0' </proc/device-tree/model)
    echo "Detected device: $DEVICE_MODEL"
else
    echo "⚠ Could not detect Raspberry Pi model (continuing anyway)"
fi

# Check OS version - must be Raspberry Pi OS Lite (Trixie)
echo ""
echo "Checking operating system requirements..."
echo "----------------------------------------"
OS_CHECK_FAILED=0

if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "Detected OS: $PRETTY_NAME"
    echo "Version ID: ${VERSION_ID:-unknown}"
    
    # Check if it's Raspberry Pi OS or Debian
    if [[ "$ID" != "raspbian" ]] && [[ "$ID" != "debian" ]]; then
        echo "✗ ERROR: This script requires Raspberry Pi OS (raspbian/debian)"
        echo "  Detected OS ID: $ID"
        OS_CHECK_FAILED=1
    fi
    
    # Check if it's Debian 13 (Trixie)
    if [ "${VERSION_ID:-0}" != "13" ]; then
        echo "✗ ERROR: This script requires Raspberry Pi OS Lite (Trixie) - Debian 13"
        echo "  Detected version: ${VERSION_ID:-unknown}"
        echo "  Please upgrade to Raspberry Pi OS Lite (Trixie) before continuing"
        OS_CHECK_FAILED=1
    else
        echo "✓ Debian 13 (Trixie) detected"
    fi
    
    # Check if it's the Lite version (no desktop environment)
    # Check for desktop packages or desktop services
    DESKTOP_DETECTED=0
    if dpkg -l | grep -qE "^ii.*raspberrypi-ui-mods|^ii.*lxde|^ii.*xfce|^ii.*gnome|^ii.*kde"; then
        DESKTOP_DETECTED=1
    fi
    if systemctl list-units --type=service --state=running 2>/dev/null | grep -qE "lightdm|gdm3|sddm|lxdm"; then
        DESKTOP_DETECTED=1
    fi
    if [ -d /usr/share/raspberrypi-ui-mods ] || [ -d /usr/share/xsessions ]; then
        DESKTOP_DETECTED=1
    fi
    
    if [ "$DESKTOP_DETECTED" -eq 1 ]; then
        echo "✗ ERROR: Desktop environment detected - this script requires Raspberry Pi OS Lite"
        echo "  Please use Raspberry Pi OS Lite (not the full desktop version)"
        OS_CHECK_FAILED=1
    else
        echo "✓ Lite version confirmed (no desktop environment)"
    fi
else
    echo "✗ ERROR: Could not detect OS version (/etc/os-release not found)"
    OS_CHECK_FAILED=1
fi

if [ "$OS_CHECK_FAILED" -eq 1 ]; then
    echo ""
    echo "Installation cannot continue. Please install Raspberry Pi OS Lite (Trixie) and try again."
    echo ""
    echo "To install Raspberry Pi OS Lite (Trixie):"
    echo "  1. Download from: https://www.raspberrypi.com/software/operating-systems/"
    echo "  2. Select 'Raspberry Pi OS Lite (64-bit)' with Debian 13 (Trixie)"
    echo "  3. Flash to SD card using Raspberry Pi Imager"
    echo "  4. Boot and run this script again"
    exit 1
fi

echo "✓ OS requirements met"
echo ""

# Get the actual user who invoked sudo (set after we ensure sudo below)
if [ -n "${SUDO_USER:-}" ]; then
    ACTUAL_USER="$SUDO_USER"
else
    ACTUAL_USER=$(whoami)
fi

# Get the home directory of the actual user
USER_HOME=$(eval echo ~$ACTUAL_USER)

# Determine the Project Root Directory (where this script is located)
PROJECT_ROOT_DIR=$(cd "$(dirname "$0")" && pwd)

echo "Detected user: $ACTUAL_USER"
echo "User home directory: $USER_HOME"
echo "Project directory: $PROJECT_ROOT_DIR"
echo ""

# Check if running as root; if not, try to elevate automatically for novices
if [ "$EUID" -ne 0 ]; then
    echo "This script needs administrator privileges. Attempting to re-run with sudo..."
    exec sudo -E env LEDMATRIX_ELEVATED=1 bash "$0" "$@"
fi
echo "✓ Running as root (required for installation)"

# Initialize logging
LOG_DIR="$PROJECT_ROOT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/first_time_install_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "Logging to: $LOG_FILE"

# Args and options (novice-friendly defaults)
ASSUME_YES=${LEDMATRIX_ASSUME_YES:-0}
SKIP_SOUND=${LEDMATRIX_SKIP_SOUND:-0}
SKIP_PERF=${LEDMATRIX_SKIP_PERF:-0}
SKIP_REBOOT_PROMPT=${LEDMATRIX_SKIP_REBOOT_PROMPT:-0}

usage() {
    cat <<USAGE
Usage: sudo ./first_time_install.sh [options]

Options:
  -y, --yes                 Proceed without interactive confirmations
      --force-rebuild       Force rebuild of rpi-rgb-led-matrix even if present
      --skip-sound          Skip sound module configuration
      --skip-perf           Skip performance tweaks (isolcpus/audio)
      --no-reboot-prompt    Do not prompt for reboot at the end
  -h, --help                Show this help message and exit

Environment variables (same effect as flags):
  LEDMATRIX_ASSUME_YES=1, RPI_RGB_FORCE_REBUILD=1, LEDMATRIX_SKIP_SOUND=1,
  LEDMATRIX_SKIP_PERF=1, LEDMATRIX_SKIP_REBOOT_PROMPT=1
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes) ASSUME_YES=1 ;;
        --force-rebuild) RPI_RGB_FORCE_REBUILD=1 ;;
        --skip-sound) SKIP_SOUND=1 ;;
        --skip-perf) SKIP_PERF=1 ;;
        --no-reboot-prompt) SKIP_REBOOT_PROMPT=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
    shift
done

# Helpers
retry() {
    local attempt=1
    local max_attempts=3
    local delay_seconds=5
    while true; do
        "$@" && return 0
        local status=$?
        if [ $attempt -ge $max_attempts ]; then
            echo "✗ Command failed after $attempt attempts: $*"
            return $status
        fi
        echo "⚠ Command failed (attempt $attempt/$max_attempts). Retrying in ${delay_seconds}s: $*"
        attempt=$((attempt+1))
        sleep "$delay_seconds"
    done
}

apt_update() { retry apt update; }
apt_install() { retry apt install -y "$@"; }
apt_remove() { apt-get remove -y "$@" || true; }

check_network() {
    if command -v ping >/dev/null 2>&1; then
        if ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1; then
            return 0
        fi
    fi
    if command -v curl >/dev/null 2>&1; then
        if curl -Is --max-time 5 http://deb.debian.org >/dev/null 2>&1; then
            return 0
        fi
    fi
    echo "✗ No internet connectivity detected."
    echo "Please connect your Raspberry Pi to the internet and re-run this script."
    exit 1
}

echo ""
echo "This script will perform the following steps:"
echo "1. Install system dependencies"
echo "2. Fix cache permissions"
echo "3. Fix assets directory permissions"
echo "3.1. Fix plugin directory permissions"
echo "4. Install main LED Matrix service"
echo "5. Install Python project dependencies (requirements.txt)"
echo "6. Build and install rpi-rgb-led-matrix and test import"
echo "7. Install web interface dependencies"
echo "8. Install web interface service"
echo "8.5. Install WiFi monitor service"
echo "9. Configure web interface permissions"
echo "10. Configure passwordless sudo access"
echo "11. Set up proper file ownership"
echo "12. Configure sound module to avoid conflicts"
echo "13. Apply performance optimizations"
echo "14. Test the installation"
echo ""

# Ask for confirmation
if [ "$ASSUME_YES" = "1" ]; then
    echo "Non-interactive mode: proceeding with installation."
else
    read -p "Do you want to proceed with the installation? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Installation cancelled."
        exit 0
    fi
fi

echo ""
CLEAR='
'
CURRENT_STEP="Install system dependencies"
echo "Step 1: Installing system dependencies..."
echo "----------------------------------------"

# Ensure network is available before APT operations
check_network

# Update package list
apt_update

# Install required system packages
echo "Installing Python packages and dependencies..."
apt_install python3-pip python3-venv python3-dev python3-pil python3-pil.imagetk python3-pillow build-essential python3-setuptools python3-wheel cython3 scons cmake ninja-build

# Install additional system dependencies that might be needed
echo "Installing additional system dependencies..."
apt_install git curl wget unzip

echo "✓ System dependencies installed"
echo ""

CURRENT_STEP="Fix cache permissions"
echo "Step 2: Fixing cache permissions..."
echo "----------------------------------"

# Run the cache setup script (uses proper group permissions)
if [ -f "$PROJECT_ROOT_DIR/scripts/install/setup_cache.sh" ]; then
    echo "Running cache setup script (proper group permissions)..."
    bash "$PROJECT_ROOT_DIR/scripts/install/setup_cache.sh"
    echo "✓ Cache permissions fixed with proper group setup"
elif [ -f "$PROJECT_ROOT_DIR/scripts/fix_perms/fix_cache_permissions.sh" ]; then
    echo "Running cache permissions fix (legacy script)..."
    bash "$PROJECT_ROOT_DIR/scripts/fix_perms/fix_cache_permissions.sh"
    echo "✓ Cache permissions fixed"
else
    echo "⚠ Cache setup scripts not found, setting up cache directory manually..."
    # Create ledmatrix group if it doesn't exist
    if ! getent group ledmatrix > /dev/null 2>&1; then
        groupadd ledmatrix
        echo "Created ledmatrix group"
    fi
    
    # Add users to ledmatrix group
    usermod -a -G ledmatrix "$ACTUAL_USER"
    if id daemon > /dev/null 2>&1; then
        usermod -a -G ledmatrix daemon
    fi
    
    # Create cache directory with proper permissions
    mkdir -p /var/cache/ledmatrix
    chown -R :ledmatrix /var/cache/ledmatrix
    # Set directory permissions: 775 with setgid for group inheritance
    find /var/cache/ledmatrix -type d -exec chmod 775 {} \;
    chmod g+s /var/cache/ledmatrix
    # Set file permissions: 660 for group-readable cache files
    find /var/cache/ledmatrix -type f -exec chmod 660 {} \;
    
    echo "✓ Cache directory created with proper group permissions"
    echo "  Note: You may need to log out and back in for group changes to take effect"
fi
echo ""

CURRENT_STEP="Fix assets directory permissions"
echo "Step 3: Fixing assets directory permissions..."
echo "--------------------------------------------"

# Run the assets permissions fix
if [ -f "$PROJECT_ROOT_DIR/scripts/fix_perms/fix_assets_permissions.sh" ]; then
    echo "Running assets permissions fix..."
    bash "$PROJECT_ROOT_DIR/scripts/fix_perms/fix_assets_permissions.sh"
    echo "✓ Assets permissions fixed"
else
    echo "⚠ Assets permissions script not found, fixing permissions manually..."
    
    # Set ownership of the entire assets directory to the real user
    echo "Setting ownership of assets directory..."
    chown -R "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_ROOT_DIR/assets"
    
    # Set permissions to allow read/write for owner, group, and others (for root service user)
    # Note: 777 allows root (service user) to write, which is necessary when service runs as root
    echo "Setting permissions for assets directory..."
    chmod -R 777 "$PROJECT_ROOT_DIR/assets"
    
    # Specifically ensure the sports logos directories are writable
    SPORTS_DIRS=(
        "sports/ncaa_logos"
        "sports/nfl_logos"
        "sports/nba_logos"
        "sports/nhl_logos"
        "sports/mlb_logos"
        "sports/milb_logos"
        "sports/soccer_logos"
    )
    
    echo "Ensuring sports logo directories are writable..."
    for SPORTS_DIR in "${SPORTS_DIRS[@]}"; do
        FULL_PATH="$PROJECT_ROOT_DIR/assets/$SPORTS_DIR"
        if [ -d "$FULL_PATH" ]; then
            chmod 777 "$FULL_PATH"
            chown "$ACTUAL_USER:$ACTUAL_USER" "$FULL_PATH"
        else
            echo "Creating directory: $FULL_PATH"
            mkdir -p "$FULL_PATH"
            chown "$ACTUAL_USER:$ACTUAL_USER" "$FULL_PATH"
            chmod 777 "$FULL_PATH"
        fi
    done
    
    echo "✓ Assets permissions fixed manually"
fi
echo ""

CURRENT_STEP="Fix plugin directory permissions"
echo "Step 3.1: Fixing plugin directory permissions..."
echo "----------------------------------------------"

# Ensure home directory is traversable by root (needed for service access)
USER_HOME=$(eval echo ~$ACTUAL_USER)
if [ -d "$USER_HOME" ]; then
    HOME_PERMS=$(stat -c "%a" "$USER_HOME" 2>/dev/null || echo "unknown")
    if [ "$HOME_PERMS" = "700" ]; then
        echo "Fixing home directory permissions (700 -> 755) so root service can access subdirectories..."
        chmod 755 "$USER_HOME"
        echo "✓ Home directory permissions fixed"
    fi
fi
echo ""

# Run the plugin permissions fix
if [ -f "$PROJECT_ROOT_DIR/scripts/fix_perms/fix_plugin_permissions.sh" ]; then
    echo "Running plugin permissions fix..."
    bash "$PROJECT_ROOT_DIR/scripts/fix_perms/fix_plugin_permissions.sh"
    echo "✓ Plugin permissions fixed"
else
    echo "⚠ Plugin permissions script not found, fixing permissions manually..."
    
    # Ensure plugins directory exists
    if [ ! -d "$PROJECT_ROOT_DIR/plugins" ]; then
        echo "Creating plugins directory..."
        mkdir -p "$PROJECT_ROOT_DIR/plugins"
    fi
    
    # Determine ownership based on web service user
    # Check if web service file exists and what user it runs as
    WEB_SERVICE_USER="root"
    if [ -f "/etc/systemd/system/ledmatrix-web.service" ]; then
        # Check actual installed service file (most accurate)
        WEB_SERVICE_USER=$(grep "^User=" /etc/systemd/system/ledmatrix-web.service | cut -d'=' -f2 || echo "root")
    elif [ -f "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh" ]; then
        # Check install_web_service.sh (used by first_time_install.sh)
        if grep -q "User=root" "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh"; then
            WEB_SERVICE_USER="root"
        elif grep -q "User=\${ACTUAL_USER}" "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh"; then
            WEB_SERVICE_USER="$ACTUAL_USER"
        fi
    elif [ -f "$PROJECT_ROOT_DIR/systemd/ledmatrix-web.service" ]; then
        # Check template file (may have placeholder)
        WEB_SERVICE_USER=$(grep "^User=" "$PROJECT_ROOT_DIR/systemd/ledmatrix-web.service" | cut -d'=' -f2 || echo "root")
        # If template has placeholder, check install script
        if [ "$WEB_SERVICE_USER" = "__USER__" ] || [ -z "$WEB_SERVICE_USER" ]; then
            # Check install_service.sh to see what user it uses
            if [ -f "$PROJECT_ROOT_DIR/scripts/install/install_service.sh" ] && grep -q "User=\${ACTUAL_USER}" "$PROJECT_ROOT_DIR/scripts/install/install_service.sh"; then
                WEB_SERVICE_USER="$ACTUAL_USER"
            fi
        fi
    elif [ -f "$PROJECT_ROOT_DIR/scripts/install/install_service.sh" ] && grep -q "User=\${ACTUAL_USER}" "$PROJECT_ROOT_DIR/scripts/install/install_service.sh"; then
        # Web service will be installed by install_service.sh as ACTUAL_USER
        WEB_SERVICE_USER="$ACTUAL_USER"
    fi
    
    # If web service runs as ACTUAL_USER (not root), set ownership to ACTUAL_USER
    # so the web service can change permissions. Root service can still access via group (775).
    # If web service runs as root, use root:ACTUAL_USER for mixed access.
    if [ "$WEB_SERVICE_USER" = "$ACTUAL_USER" ] || [ "$WEB_SERVICE_USER" != "root" ]; then
        echo "Web service runs as $WEB_SERVICE_USER, setting ownership to $ACTUAL_USER:$ACTUAL_USER..."
        echo "  (Root service can still access via group permissions)"
        chown -R "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_ROOT_DIR/plugins"
    else
        echo "Web service runs as root, setting ownership to root:$ACTUAL_USER..."
        chown -R root:"$ACTUAL_USER" "$PROJECT_ROOT_DIR/plugins"
    fi
    
    # Set directory permissions (775: rwxrwxr-x)
    echo "Setting directory permissions to 775..."
    find "$PROJECT_ROOT_DIR/plugins" -type d -exec chmod 775 {} \;
    
    # Set file permissions (664: rw-rw-r--)
    echo "Setting file permissions to 664..."
    find "$PROJECT_ROOT_DIR/plugins" -type f -exec chmod 664 {} \;
    
    echo "✓ Plugin permissions fixed manually"
fi

# Also ensure plugin-repos directory exists with proper permissions
# This is where plugins installed via the plugin store are stored
PLUGIN_REPOS_DIR="$PROJECT_ROOT_DIR/plugin-repos"
if [ ! -d "$PLUGIN_REPOS_DIR" ]; then
    echo "Creating plugin-repos directory..."
    mkdir -p "$PLUGIN_REPOS_DIR"
fi

# Determine ownership based on web service user
# Check if web service file exists and what user it runs as
WEB_SERVICE_USER="root"
if [ -f "/etc/systemd/system/ledmatrix-web.service" ]; then
    # Check actual installed service file (most accurate)
    WEB_SERVICE_USER=$(grep "^User=" /etc/systemd/system/ledmatrix-web.service | cut -d'=' -f2 || echo "root")
elif [ -f "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh" ]; then
    # Check install_web_service.sh (used by first_time_install.sh)
    if grep -q "User=root" "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh"; then
        WEB_SERVICE_USER="root"
    elif grep -q "User=\${ACTUAL_USER}" "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh"; then
        WEB_SERVICE_USER="$ACTUAL_USER"
    fi
elif [ -f "$PROJECT_ROOT_DIR/systemd/ledmatrix-web.service" ]; then
    # Check template file (may have placeholder)
    WEB_SERVICE_USER=$(grep "^User=" "$PROJECT_ROOT_DIR/systemd/ledmatrix-web.service" | cut -d'=' -f2 || echo "root")
    # If template has placeholder, check install script
    if [ "$WEB_SERVICE_USER" = "__USER__" ] || [ -z "$WEB_SERVICE_USER" ]; then
        # Check install_service.sh to see what user it uses
        if [ -f "$PROJECT_ROOT_DIR/scripts/install/install_service.sh" ] && grep -q "User=\${ACTUAL_USER}" "$PROJECT_ROOT_DIR/scripts/install/install_service.sh"; then
            WEB_SERVICE_USER="$ACTUAL_USER"
        fi
    fi
elif [ -f "$PROJECT_ROOT_DIR/scripts/install/install_service.sh" ] && grep -q "User=\${ACTUAL_USER}" "$PROJECT_ROOT_DIR/scripts/install/install_service.sh"; then
    # Web service will be installed by install_service.sh as ACTUAL_USER
    WEB_SERVICE_USER="$ACTUAL_USER"
fi

# If web service runs as ACTUAL_USER (not root), set ownership to ACTUAL_USER
# so the web service can change permissions. Root service can still access via group (775).
# If web service runs as root, use root:ACTUAL_USER for mixed access.
if [ "$WEB_SERVICE_USER" = "$ACTUAL_USER" ] || [ "$WEB_SERVICE_USER" != "root" ]; then
    echo "Web service runs as $WEB_SERVICE_USER, setting ownership to $ACTUAL_USER:$ACTUAL_USER..."
    echo "  (Root service can still access via group permissions)"
    chown -R "$ACTUAL_USER:$ACTUAL_USER" "$PLUGIN_REPOS_DIR"
else
    echo "Web service runs as root, setting ownership to root:$ACTUAL_USER..."
    chown -R root:"$ACTUAL_USER" "$PLUGIN_REPOS_DIR"
fi

# Set directory permissions (775: rwxrwxr-x)
echo "Setting plugin-repos directory permissions to 2775 (sticky bit)..."
find "$PLUGIN_REPOS_DIR" -type d -exec chmod 2775 {} \;

# Set file permissions (664: rw-rw-r--)
echo "Setting plugin-repos file permissions to 664..."
find "$PLUGIN_REPOS_DIR" -type f -exec chmod 664 {} \;

echo "✓ Plugin-repos directory permissions fixed"
echo ""

CURRENT_STEP="Install main LED Matrix service"
echo "Step 4: Installing main LED Matrix service..."
echo "---------------------------------------------"

# Run the main service installation (idempotent)
if [ -f "$PROJECT_ROOT_DIR/scripts/install/install_service.sh" ]; then
    echo "Running main service installation..."
    bash "$PROJECT_ROOT_DIR/scripts/install/install_service.sh"
    echo "✓ Main LED Matrix service installed"
else
    echo "✗ Main service installation script not found at $PROJECT_ROOT_DIR/scripts/install/install_service.sh"
    echo "Please ensure you are running this script from the project root: $PROJECT_ROOT_DIR"
    exit 1
fi

# Configure Python capabilities for hardware timing
echo "Configuring Python capabilities for hardware timing..."
if [ -f "/usr/bin/python3.13" ]; then
    sudo setcap 'cap_sys_nice=eip' /usr/bin/python3.13 2>/dev/null || echo "⚠ Could not set cap_sys_nice on python3.13 (may need manual setup)"
    echo "✓ Python3.13 capabilities configured"
elif [ -f "/usr/bin/python3" ]; then
    PYTHON_VER=$(python3 --version 2>&1 | grep -oP '(?<=Python )\d\.\d+' || echo "unknown")
    if command -v setcap >/dev/null 2>&1; then
        sudo setcap 'cap_sys_nice=eip' /usr/bin/python3 2>/dev/null || echo "⚠ Could not set cap_sys_nice on python3"
        echo "✓ Python3 capabilities configured (version: $PYTHON_VER)"
    else
        echo "⚠ setcap not found, skipping capability configuration"
    fi
else
    echo "⚠ Python3 not found, skipping capability configuration"
fi
echo ""

CURRENT_STEP="Ensure configuration files exist"
echo "Step 4.1: Ensuring configuration files exist..."
echo "------------------------------------------------"

# Ensure config directory exists
mkdir -p "$PROJECT_ROOT_DIR/config"
chmod 2775 "$PROJECT_ROOT_DIR/config" || true

# Create ledmatrix group if it doesn't exist (needed for shared access)
LEDMATRIX_GROUP="ledmatrix"
if ! getent group "$LEDMATRIX_GROUP" > /dev/null 2>&1; then
    groupadd "$LEDMATRIX_GROUP" || true
    echo "Created group: $LEDMATRIX_GROUP"
fi

# Add root to ledmatrix group so service can read config files
if ! id -nG root | grep -qw "$LEDMATRIX_GROUP" 2>/dev/null; then
    usermod -a -G "$LEDMATRIX_GROUP" root || true
    echo "Added root to group: $LEDMATRIX_GROUP"
fi

# Set config directory ownership to user:ledmatrix group
chown "$ACTUAL_USER:$LEDMATRIX_GROUP" "$PROJECT_ROOT_DIR/config" || true

# Create config.json from template if missing
if [ ! -f "$PROJECT_ROOT_DIR/config/config.json" ]; then
    if [ -f "$PROJECT_ROOT_DIR/config/config.template.json" ]; then
        echo "Creating config/config.json from template..."
        cp "$PROJECT_ROOT_DIR/config/config.template.json" "$PROJECT_ROOT_DIR/config/config.json"
        chown "$ACTUAL_USER:$LEDMATRIX_GROUP" "$PROJECT_ROOT_DIR/config/config.json" || true
        chmod 644 "$PROJECT_ROOT_DIR/config/config.json"
        echo "✓ Main config file created from template"
    else
        echo "⚠ Template config/config.template.json not found; creating a minimal config file"
        cat > "$PROJECT_ROOT_DIR/config/config.json" <<'EOF'
{
    "web_display_autostart": true,
    "timezone": "America/Chicago",
    "display": {
        "hardware": {
            "rows": 32,
            "cols": 64,
            "chain_length": 2,
            "parallel": 1,
            "brightness": 95,
            "hardware_mapping": "adafruit-hat-pwm"
        }
    },
    "clock": {
        "enabled": true,
        "format": "%I:%M %p"
    }
}
EOF
        chown "$ACTUAL_USER:$LEDMATRIX_GROUP" "$PROJECT_ROOT_DIR/config/config.json" || true
        chmod 644 "$PROJECT_ROOT_DIR/config/config.json"
        echo "✓ Minimal config file created"
    fi
else
    echo "✓ Main config file already exists"
fi

# Create config_secrets.json from template if missing
if [ ! -f "$PROJECT_ROOT_DIR/config/config_secrets.json" ]; then
    if [ -f "$PROJECT_ROOT_DIR/config/config_secrets.template.json" ]; then
        echo "Creating config/config_secrets.json from template..."
        cp "$PROJECT_ROOT_DIR/config/config_secrets.template.json" "$PROJECT_ROOT_DIR/config/config_secrets.json"
        # Check if service runs as root and set ownership accordingly
        SERVICE_USER="root"
        if [ -f "/etc/systemd/system/ledmatrix.service" ]; then
            SERVICE_USER=$(grep "^User=" /etc/systemd/system/ledmatrix.service | cut -d'=' -f2 || echo "root")
        elif [ -f "$PROJECT_ROOT_DIR/systemd/ledmatrix.service" ]; then
            SERVICE_USER=$(grep "^User=" "$PROJECT_ROOT_DIR/systemd/ledmatrix.service" | cut -d'=' -f2 || echo "root")
        fi
        
        if [ "$SERVICE_USER" = "root" ]; then
            chown "root:$LEDMATRIX_GROUP" "$PROJECT_ROOT_DIR/config/config_secrets.json" || true
        else
            chown "$ACTUAL_USER:$LEDMATRIX_GROUP" "$PROJECT_ROOT_DIR/config/config_secrets.json" || true
        fi
        chmod 640 "$PROJECT_ROOT_DIR/config/config_secrets.json"
        echo "✓ Secrets file created from template"
    else
        echo "⚠ Template config/config_secrets.template.json not found; creating a minimal secrets file"
        cat > "$PROJECT_ROOT_DIR/config/config_secrets.json" <<'EOF'
{
  "weather": {
    "api_key": "YOUR_OPENWEATHERMAP_API_KEY"
  }
}
EOF
        # Check if service runs as root and set ownership accordingly
        SERVICE_USER="root"
        if [ -f "/etc/systemd/system/ledmatrix.service" ]; then
            SERVICE_USER=$(grep "^User=" /etc/systemd/system/ledmatrix.service | cut -d'=' -f2 || echo "root")
        elif [ -f "$PROJECT_ROOT_DIR/systemd/ledmatrix.service" ]; then
            SERVICE_USER=$(grep "^User=" "$PROJECT_ROOT_DIR/systemd/ledmatrix.service" | cut -d'=' -f2 || echo "root")
        fi
        
        if [ "$SERVICE_USER" = "root" ]; then
            chown "root:$LEDMATRIX_GROUP" "$PROJECT_ROOT_DIR/config/config_secrets.json" || true
        else
            chown "$ACTUAL_USER:$LEDMATRIX_GROUP" "$PROJECT_ROOT_DIR/config/config_secrets.json" || true
        fi
        chmod 640 "$PROJECT_ROOT_DIR/config/config_secrets.json"
        echo "✓ Minimal secrets file created"
    fi
else
    echo "✓ Secrets file already exists"
fi
echo ""

CURRENT_STEP="Install project Python dependencies"
echo "Step 5: Installing Python project dependencies..."
echo "-----------------------------------------------"

# Install numpy via apt first (pre-built binary, much faster than building from source)
echo "Installing numpy via apt (pre-built binary for faster installation)..."
if ! python3 -c "import numpy" >/dev/null 2>&1; then
    apt_install python3-numpy
    echo "✓ numpy installed via apt"
else
    NUMPY_VERSION=$(python3 -c "import numpy; print(numpy.__version__)" 2>/dev/null || echo "unknown")
    echo "✓ numpy already installed (version: $NUMPY_VERSION)"
fi
echo ""

# Install main project Python dependencies
cd "$PROJECT_ROOT_DIR"
if [ -f "$PROJECT_ROOT_DIR/requirements.txt" ]; then
    echo "Reading requirements from: $PROJECT_ROOT_DIR/requirements.txt"
    
    # Check pip version and upgrade if needed
    echo "Checking pip version..."
    python3 -m pip --version
    
    # Upgrade pip, setuptools, and wheel for better compatibility
    echo "Upgrading pip, setuptools, and wheel..."
    python3 -m pip install --break-system-packages --upgrade pip setuptools wheel || {
        echo "⚠ Warning: Failed to upgrade pip/setuptools/wheel, continuing anyway..."
    }
    
    # Count total packages for progress
    TOTAL_PACKAGES=$(grep -v '^#' "$PROJECT_ROOT_DIR/requirements.txt" | grep -v '^$' | wc -l)
    echo "Found $TOTAL_PACKAGES package(s) to install"
    echo ""
    
    # Install packages one at a time for better diagnostics
    INSTALLED=0
    FAILED=0
    PACKAGE_NUM=0
    
    while IFS= read -r line || [ -n "$line" ]; do
        # Remove inline comments (everything after #) but preserve comment-only lines
        # First check if line starts with # (comment-only line)
        if [[ "$line" =~ ^[[:space:]]*# ]]; then
            continue
        fi
        
        # Remove inline comments and trim whitespace
        line=$(echo "$line" | sed 's/[[:space:]]*#.*$//' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        
        # Skip empty lines
        if [[ -z "$line" ]]; then
            continue
        fi
        
        PACKAGE_NUM=$((PACKAGE_NUM + 1))
        echo "[$PACKAGE_NUM/$TOTAL_PACKAGES] Installing: $line"
        
        # Check if package is already installed (basic check - may not catch all cases)
        PACKAGE_NAME=$(echo "$line" | sed -E 's/[<>=!].*$//' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        
        # Try installing with verbose output and timeout (if available)
        # Use --no-cache-dir to avoid cache issues, --verbose for diagnostics
        INSTALL_OUTPUT=$(mktemp)
        INSTALL_SUCCESS=false
        
        if command -v timeout >/dev/null 2>&1; then
            # Use timeout if available (10 minutes = 600 seconds)
            if timeout 600 python3 -m pip install --break-system-packages --no-cache-dir --verbose "$line" > "$INSTALL_OUTPUT" 2>&1; then
                INSTALL_SUCCESS=true
            else
                EXIT_CODE=$?
                if [ "$EXIT_CODE" -eq 124 ]; then
                    echo "✗ Timeout (10 minutes) installing: $line"
                    echo "  This package may require building from source, which can be slow on Raspberry Pi."
                    echo "  You can try installing it manually later with:"
                    echo "    python3 -m pip install --break-system-packages --no-cache-dir --verbose '$line'"
                else
                    echo "✗ Failed to install: $line (exit code: $EXIT_CODE)"
                fi
            fi
        else
            # No timeout command available, install without timeout
            echo "  Note: timeout command not available, installation may take a while..."
            if python3 -m pip install --break-system-packages --no-cache-dir --verbose "$line" > "$INSTALL_OUTPUT" 2>&1; then
                INSTALL_SUCCESS=true
            else
                EXIT_CODE=$?
                echo "✗ Failed to install: $line (exit code: $EXIT_CODE)"
            fi
        fi
        
        # Show relevant output (filtered for readability)
        if [ -f "$INSTALL_OUTPUT" ]; then
            echo "  Output:"
            grep -E "(Collecting|Installing|Successfully|Preparing metadata|Building|ERROR|WARNING|Using cached|Downloading)" "$INSTALL_OUTPUT" | head -15 | sed 's/^/    /' || true
            # Log full output to log file
            cat "$INSTALL_OUTPUT" >> "$LOG_FILE"
            rm -f "$INSTALL_OUTPUT"
        fi
        
        if [ "$INSTALL_SUCCESS" = true ]; then
            INSTALLED=$((INSTALLED + 1))
            echo "✓ Successfully installed: $line"
        else
            FAILED=$((FAILED + 1))
            
            # Ask if user wants to continue (unless in non-interactive mode)
            if [ "$ASSUME_YES" != "1" ]; then
                read -p "  Continue with remaining packages? (Y/n): " -n 1 -r
                echo
                if [[ $REPLY =~ ^[Nn]$ ]]; then
                    echo "Installation cancelled by user"
                    exit 1
                fi
            fi
        fi
        echo ""
    done < "$PROJECT_ROOT_DIR/requirements.txt"
    
    echo "-----------------------------------------------"
    echo "Installation summary:"
    echo "  Installed: $INSTALLED"
    echo "  Failed: $FAILED"
    echo "  Total: $TOTAL_PACKAGES"
    echo ""
    
    if [ "$FAILED" -gt 0 ]; then
        echo "⚠ Some packages failed to install. The installation will continue, but"
        echo "  you may need to install them manually later. Check the log for details:"
        echo "  $LOG_FILE"
        echo ""
        echo "Common fixes for 'Preparing metadata' issues:"
        echo "  1. Ensure you have enough disk space: df -h"
        echo "  2. Check available memory: free -h"
        echo "  3. Try installing failed packages individually with verbose output:"
        echo "     python3 -m pip install --break-system-packages --no-cache-dir --verbose <package>"
        echo "  4. For packages that build from source (like numpy), consider:"
        echo "     - Installing pre-built wheels: python3 -m pip install --only-binary :all: <package>"
        echo "     - Or installing via apt if available: sudo apt install python3-<package>"
        echo ""
    fi
    
    if [ "$INSTALLED" -gt 0 ]; then
        echo "✓ Project Python dependencies installed ($INSTALLED/$TOTAL_PACKAGES successful)"
    else
        echo "✗ No packages were successfully installed"
        echo "  Check the log file for details: $LOG_FILE"
        exit 1
    fi
else
    echo "⚠ requirements.txt not found; skipping main dependency install"
fi
echo ""

CURRENT_STEP="Build and install rpi-rgb-led-matrix"
echo "Step 6: Building and installing rpi-rgb-led-matrix..."
echo "-----------------------------------------------------"

# If already installed and not forcing rebuild, skip expensive build
if python3 -c 'from rgbmatrix import RGBMatrix, RGBMatrixOptions' >/dev/null 2>&1 && [ "${RPI_RGB_FORCE_REBUILD:-0}" != "1" ]; then
    echo "rgbmatrix Python package already available; skipping build (set RPI_RGB_FORCE_REBUILD=1 to force rebuild)."
else
    # Ensure rpi-rgb-led-matrix submodule is initialized
    if [ ! -d "$PROJECT_ROOT_DIR/rpi-rgb-led-matrix-master" ]; then
        echo "rpi-rgb-led-matrix-master not found. Initializing git submodule..."
        cd "$PROJECT_ROOT_DIR"
        
        # Try to initialize submodule if .gitmodules exists
        if [ -f "$PROJECT_ROOT_DIR/.gitmodules" ] && grep -q "rpi-rgb-led-matrix" "$PROJECT_ROOT_DIR/.gitmodules"; then
            echo "Initializing rpi-rgb-led-matrix submodule..."
            if ! git submodule update --init --recursive rpi-rgb-led-matrix-master 2>&1; then
                echo "⚠ Submodule init failed, cloning directly from GitHub..."
                git clone https://github.com/hzeller/rpi-rgb-led-matrix.git rpi-rgb-led-matrix-master
            fi
        else
            # Fallback: clone directly if submodule not configured
            echo "Submodule not configured, cloning directly from GitHub..."
            git clone https://github.com/hzeller/rpi-rgb-led-matrix.git rpi-rgb-led-matrix-master
        fi
    fi
    
    # Build and install rpi-rgb-led-matrix Python bindings
    if [ -d "$PROJECT_ROOT_DIR/rpi-rgb-led-matrix-master" ]; then
        # Check if submodule is properly initialized (not empty)
        if [ ! -f "$PROJECT_ROOT_DIR/rpi-rgb-led-matrix-master/Makefile" ]; then
            echo "⚠ Submodule appears empty, re-initializing..."
            cd "$PROJECT_ROOT_DIR"
            rm -rf rpi-rgb-led-matrix-master
            if [ -f "$PROJECT_ROOT_DIR/.gitmodules" ] && grep -q "rpi-rgb-led-matrix" "$PROJECT_ROOT_DIR/.gitmodules"; then
                git submodule update --init --recursive rpi-rgb-led-matrix-master
            else
                git clone https://github.com/hzeller/rpi-rgb-led-matrix.git rpi-rgb-led-matrix-master
            fi
        fi
        
        pushd "$PROJECT_ROOT_DIR/rpi-rgb-led-matrix-master" >/dev/null
        echo "Building rpi-rgb-led-matrix Python bindings..."
        # Build the library first, then Python bindings
        # The build-python target depends on the library being built
        if ! make build-python; then
            echo "✗ Failed to build rpi-rgb-led-matrix Python bindings"
            echo "  Make sure you have the required build tools installed:"
            echo "  sudo apt install -y build-essential python3-dev cython3 scons"
            popd >/dev/null
            exit 1
        fi
        cd bindings/python
        echo "Installing rpi-rgb-led-matrix Python package via pip..."
        if ! python3 -m pip install --break-system-packages .; then
            echo "✗ Failed to install rpi-rgb-led-matrix Python package"
            popd >/dev/null
            exit 1
        fi
        popd >/dev/null
    else
        echo "✗ rpi-rgb-led-matrix-master directory not found at $PROJECT_ROOT_DIR"
        echo "Failed to initialize submodule or clone repository"
        exit 1
    fi

    echo "Running rgbmatrix import test..."
    if python3 - <<'PY'
from importlib.metadata import version, PackageNotFoundError
try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
    try:
        print("Success! rgbmatrix version:", version('rgbmatrix'))
    except PackageNotFoundError:
        print("Success! rgbmatrix installed (version unknown)")
except Exception as e:
    raise SystemExit(f"rgbmatrix import failed: {e}")
PY
    then
        echo "✓ rpi-rgb-led-matrix installed and verified"
    else
        echo "✗ rpi-rgb-led-matrix import test failed"
        exit 1
    fi
fi
echo ""

CURRENT_STEP="Install web interface dependencies"
echo "Step 7: Installing web interface dependencies..."
echo "------------------------------------------------"

# Install web interface dependencies
echo "Installing Python dependencies for web interface..."
cd "$PROJECT_ROOT_DIR"

# Try to install dependencies using the smart installer if available
if [ -f "$PROJECT_ROOT_DIR/scripts/install_dependencies_apt.py" ]; then
    echo "Using smart dependency installer..."
    python3 "$PROJECT_ROOT_DIR/scripts/install_dependencies_apt.py"
else
    echo "Using pip to install dependencies..."
    if [ -f "$PROJECT_ROOT_DIR/requirements_web_v2.txt" ]; then
        python3 -m pip install --break-system-packages -r requirements_web_v2.txt
    else
        echo "⚠ requirements_web_v2.txt not found; skipping web dependency install"
    fi
fi

echo "✓ Web interface dependencies installed"
echo ""

CURRENT_STEP="Install web interface service"
echo "Step 8: Installing web interface service..."
echo "-------------------------------------------"

if [ -f "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh" ]; then
    # Check if service file exists and has old paths (needs update after reorganization)
    NEEDS_UPDATE=false
    if [ -f "/etc/systemd/system/ledmatrix-web.service" ]; then
        # Check if service file references old path (start_web_conditionally.py without scripts/utils/)
        if grep -q "start_web_conditionally.py" /etc/systemd/system/ledmatrix-web.service && ! grep -q "scripts/utils/start_web_conditionally.py" /etc/systemd/system/ledmatrix-web.service; then
            NEEDS_UPDATE=true
            echo "⚠ Service file has old paths, updating..."
        fi
    fi
    
    if [ ! -f "/etc/systemd/system/ledmatrix-web.service" ] || [ "$NEEDS_UPDATE" = true ]; then
        bash "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh"
        # Ensure systemd sees any new/changed unit files
        systemctl daemon-reload || true
        echo "✓ Web interface service installed/updated"
    else
        echo "✓ Web interface service already present with correct paths"
    fi
else
    echo "⚠ install_web_service.sh not found; skipping web service installation"
fi
echo ""

CURRENT_STEP="Harden systemd unit file permissions"
echo "Step 8.1: Setting systemd unit file permissions..."
echo "-----------------------------------------------"
for unit in "/etc/systemd/system/ledmatrix.service" "/etc/systemd/system/ledmatrix-web.service" "/etc/systemd/system/ledmatrix-wifi-monitor.service"; do
    if [ -f "$unit" ]; then
        chown root:root "$unit" || true
        chmod 644 "$unit" || true
    fi
done
systemctl daemon-reload || true
echo "✓ Systemd unit file permissions set"
echo ""

CURRENT_STEP="Install WiFi monitor service"
echo "Step 8.5: Installing WiFi monitor service..."
echo "---------------------------------------------"

# Install WiFi monitor service if script exists
if [ -f "$PROJECT_ROOT_DIR/scripts/install/install_wifi_monitor.sh" ]; then
    # Check if service file exists and has old paths (needs update after reorganization)
    NEEDS_UPDATE=false
    if [ -f "/etc/systemd/system/ledmatrix-wifi-monitor.service" ]; then
        # Check if service file references old path (wifi_monitor_daemon.py without scripts/utils/)
        if grep -q "wifi_monitor_daemon.py" /etc/systemd/system/ledmatrix-wifi-monitor.service && ! grep -q "scripts/utils/wifi_monitor_daemon.py" /etc/systemd/system/ledmatrix-wifi-monitor.service; then
            NEEDS_UPDATE=true
            echo "⚠ WiFi monitor service file has old paths, updating..."
        fi
    fi
    
    if [ ! -f "/etc/systemd/system/ledmatrix-wifi-monitor.service" ] || [ "$NEEDS_UPDATE" = true ]; then
        echo "Installing/updating WiFi monitor service..."
        bash "$PROJECT_ROOT_DIR/scripts/install/install_wifi_monitor.sh"
    
    # Harden service file permissions (if service was created)
    if [ -f "/etc/systemd/system/ledmatrix-wifi-monitor.service" ]; then
        chown root:root "/etc/systemd/system/ledmatrix-wifi-monitor.service" || true
        chmod 644 "/etc/systemd/system/ledmatrix-wifi-monitor.service" || true
        systemctl daemon-reload || true
    fi
    
    # Check if service was installed successfully
    if systemctl list-unit-files | grep -q "ledmatrix-wifi-monitor.service"; then
        echo "✓ WiFi monitor service installed"
        
        # Check if service is running
        if systemctl is-active --quiet ledmatrix-wifi-monitor.service 2>/dev/null; then
            echo "✓ WiFi monitor service is running"
        else
            echo "⚠ WiFi monitor service installed but not running (may need required packages)"
        fi
    else
        echo "⚠ WiFi monitor service installation may have failed"
    fi
else
    echo "⚠ install_wifi_monitor.sh not found; skipping WiFi monitor installation"
    echo "  You can install it later by running: sudo ./scripts/install/install_wifi_monitor.sh"
fi
echo ""

CURRENT_STEP="Configure web interface permissions"
echo "Step 9: Configuring web interface permissions..."
echo "------------------------------------------------"

# Add user to required groups (idempotent)
echo "Adding user to systemd-journal group..."
if id -nG "$ACTUAL_USER" | grep -qw systemd-journal; then
    echo "User $ACTUAL_USER already in systemd-journal"
else
    usermod -a -G systemd-journal "$ACTUAL_USER"
fi

echo "Adding user to adm group..."
if id -nG "$ACTUAL_USER" | grep -qw adm; then
    echo "User $ACTUAL_USER already in adm"
else
    usermod -a -G adm "$ACTUAL_USER"
fi

echo "✓ User added to required groups"
echo ""

CURRENT_STEP="Configure passwordless sudo access"
echo "Step 10: Configuring passwordless sudo access..."
echo "------------------------------------------------"

# Create sudoers configuration for the web interface
echo "Creating sudoers configuration..."
SUDOERS_FILE="/etc/sudoers.d/ledmatrix_web"

# Get command paths
PYTHON_PATH=$(which python3)
SYSTEMCTL_PATH=$(which systemctl)
REBOOT_PATH=$(which reboot)
POWEROFF_PATH=$(which poweroff)
BASH_PATH=$(which bash)

# Create sudoers content
cat > /tmp/ledmatrix_web_sudoers << EOF
# LED Matrix Web Interface passwordless sudo configuration
# This allows the web interface user to run specific commands without a password

# Allow $ACTUAL_USER to run specific commands without a password for the LED Matrix web interface
$ACTUAL_USER ALL=(ALL) NOPASSWD: $REBOOT_PATH
$ACTUAL_USER ALL=(ALL) NOPASSWD: $POWEROFF_PATH
$ACTUAL_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH start ledmatrix.service
$ACTUAL_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH stop ledmatrix.service
$ACTUAL_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH restart ledmatrix.service
$ACTUAL_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH enable ledmatrix.service
$ACTUAL_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH disable ledmatrix.service
$ACTUAL_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH status ledmatrix.service
$ACTUAL_USER ALL=(ALL) NOPASSWD: $PYTHON_PATH $PROJECT_ROOT_DIR/display_controller.py
$ACTUAL_USER ALL=(ALL) NOPASSWD: $BASH_PATH $PROJECT_ROOT_DIR/start_display.sh
$ACTUAL_USER ALL=(ALL) NOPASSWD: $BASH_PATH $PROJECT_ROOT_DIR/stop_display.sh
EOF

if [ -f "$SUDOERS_FILE" ] && cmp -s /tmp/ledmatrix_web_sudoers "$SUDOERS_FILE"; then
    echo "Sudoers configuration already up to date"
    rm /tmp/ledmatrix_web_sudoers
else
    echo "Installing/updating sudoers configuration..."
    cp /tmp/ledmatrix_web_sudoers "$SUDOERS_FILE"
    chmod 440 "$SUDOERS_FILE"
    rm /tmp/ledmatrix_web_sudoers
fi

echo "✓ Passwordless sudo access configured"
echo ""

CURRENT_STEP="Set proper file ownership"
echo "Step 11: Setting proper file ownership..."
echo "----------------------------------------"

# Set ownership of project files to the user
# Exclude plugin directories which need special permissions for root service access
echo "Setting project file ownership (excluding plugin directories)..."
find "$PROJECT_ROOT_DIR" \
    -path "$PROJECT_ROOT_DIR/plugins" -prune -o \
    -path "$PROJECT_ROOT_DIR/plugin-repos" -prune -o \
    -path "*/.git*" -prune -o \
    -exec chown "$ACTUAL_USER:$ACTUAL_USER" {} +

# Set proper permissions for config files
if [ -f "$PROJECT_ROOT_DIR/config/config.json" ]; then
    chmod 644 "$PROJECT_ROOT_DIR/config/config.json"
    echo "✓ Config file permissions set"
fi

# Set proper permissions for secrets file (restrictive: owner rw, group r)
# If service runs as root, set ownership to root so it can read as owner
# Otherwise, use ACTUAL_USER and rely on group membership
if [ -f "$PROJECT_ROOT_DIR/config/config_secrets.json" ]; then
    # Check if service runs as root (from service file or template)
    SERVICE_USER="root"
    if [ -f "/etc/systemd/system/ledmatrix.service" ]; then
        SERVICE_USER=$(grep "^User=" /etc/systemd/system/ledmatrix.service | cut -d'=' -f2 || echo "root")
    elif [ -f "$PROJECT_ROOT_DIR/systemd/ledmatrix.service" ]; then
        SERVICE_USER=$(grep "^User=" "$PROJECT_ROOT_DIR/systemd/ledmatrix.service" | cut -d'=' -f2 || echo "root")
    fi
    
    if [ "$SERVICE_USER" = "root" ]; then
        # Service runs as root - set ownership to root so it can read as owner
        chown "root:$LEDMATRIX_GROUP" "$PROJECT_ROOT_DIR/config/config_secrets.json" || true
        echo "✓ Secrets file permissions set (root:ledmatrix for root service)"
    else
        # Service runs as regular user - use ACTUAL_USER and rely on group membership
        chown "$ACTUAL_USER:$LEDMATRIX_GROUP" "$PROJECT_ROOT_DIR/config/config_secrets.json" || true
        echo "✓ Secrets file permissions set ($ACTUAL_USER:ledmatrix)"
    fi
    chmod 640 "$PROJECT_ROOT_DIR/config/config_secrets.json"
fi

# Set proper permissions for YTM auth file (readable by all users including root service)
if [ -f "$PROJECT_ROOT_DIR/config/ytm_auth.json" ]; then
    chown "$ACTUAL_USER:$LEDMATRIX_GROUP" "$PROJECT_ROOT_DIR/config/ytm_auth.json" || true
    chmod 644 "$PROJECT_ROOT_DIR/config/ytm_auth.json"
    echo "✓ YTM auth file permissions set"
fi

# Re-apply plugin directory permissions based on web service user
echo "Re-applying plugin directory permissions..."
# Determine web service user (check installed service, install scripts, or template)
WEB_SERVICE_USER="root"
if [ -f "/etc/systemd/system/ledmatrix-web.service" ]; then
    # Check actual installed service file (most accurate)
    WEB_SERVICE_USER=$(grep "^User=" /etc/systemd/system/ledmatrix-web.service | cut -d'=' -f2 || echo "root")
elif [ -f "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh" ]; then
    # Check install_web_service.sh (used by first_time_install.sh)
    if grep -q "User=root" "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh"; then
        WEB_SERVICE_USER="root"
    elif grep -q "User=\${ACTUAL_USER}" "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh"; then
        WEB_SERVICE_USER="$ACTUAL_USER"
    fi
elif [ -f "$PROJECT_ROOT_DIR/systemd/ledmatrix-web.service" ]; then
    WEB_SERVICE_USER=$(grep "^User=" "$PROJECT_ROOT_DIR/systemd/ledmatrix-web.service" | cut -d'=' -f2 || echo "root")
    if [ "$WEB_SERVICE_USER" = "__USER__" ] || [ -z "$WEB_SERVICE_USER" ]; then
        if [ -f "$PROJECT_ROOT_DIR/scripts/install/install_service.sh" ] && grep -q "User=\${ACTUAL_USER}" "$PROJECT_ROOT_DIR/scripts/install/install_service.sh"; then
            WEB_SERVICE_USER="$ACTUAL_USER"
        fi
    fi
elif [ -f "$PROJECT_ROOT_DIR/scripts/install/install_service.sh" ] && grep -q "User=\${ACTUAL_USER}" "$PROJECT_ROOT_DIR/scripts/install/install_service.sh"; then
    WEB_SERVICE_USER="$ACTUAL_USER"
fi

# Set ownership based on web service user
if [ "$WEB_SERVICE_USER" = "$ACTUAL_USER" ] || [ "$WEB_SERVICE_USER" != "root" ]; then
    PLUGIN_OWNER="$ACTUAL_USER:$ACTUAL_USER"
else
    PLUGIN_OWNER="root:$ACTUAL_USER"
fi

if [ -d "$PROJECT_ROOT_DIR/plugins" ]; then
    chown -R "$PLUGIN_OWNER" "$PROJECT_ROOT_DIR/plugins"
    find "$PROJECT_ROOT_DIR/plugins" -type d -exec chmod 2775 {} \;
    find "$PROJECT_ROOT_DIR/plugins" -type f -exec chmod 664 {} \;
fi
if [ -d "$PROJECT_ROOT_DIR/plugin-repos" ]; then
    chown -R "$PLUGIN_OWNER" "$PROJECT_ROOT_DIR/plugin-repos"
    find "$PROJECT_ROOT_DIR/plugin-repos" -type d -exec chmod 2775 {} \;
    find "$PROJECT_ROOT_DIR/plugin-repos" -type f -exec chmod 664 {} \;
fi

echo "✓ File ownership configured"
echo ""

CURRENT_STEP="Normalize project file permissions"
echo "Step 11.1: Normalizing project file and directory permissions..."
echo "--------------------------------------------------------------"

# Normalize directory permissions (exclude VCS metadata and plugin directories)
find "$PROJECT_ROOT_DIR" \
    -path "$PROJECT_ROOT_DIR/plugins" -prune -o \
    -path "$PROJECT_ROOT_DIR/plugin-repos" -prune -o \
    -path "*/.git*" -prune -o \
    -type d -exec chmod 755 {} +

# Set default file permissions (exclude plugin directories)
find "$PROJECT_ROOT_DIR" \
    -path "$PROJECT_ROOT_DIR/plugins" -prune -o \
    -path "$PROJECT_ROOT_DIR/plugin-repos" -prune -o \
    -path "*/.git*" -prune -o \
    -type f -exec chmod 644 {} +

# Ensure shell scripts are executable
find "$PROJECT_ROOT_DIR" -path "*/.git*" -prune -o -type f -name "*.sh" -exec chmod 755 {} +

# Explicitly ensure common helper scripts are executable (in case paths change)
chmod 755 "$PROJECT_ROOT_DIR/start_display.sh" "$PROJECT_ROOT_DIR/stop_display.sh" 2>/dev/null || true
chmod 755 "$PROJECT_ROOT_DIR/scripts/fix_perms/fix_cache_permissions.sh" "$PROJECT_ROOT_DIR/scripts/fix_perms/fix_web_permissions.sh" "$PROJECT_ROOT_DIR/scripts/fix_perms/fix_assets_permissions.sh" "$PROJECT_ROOT_DIR/scripts/fix_perms/fix_plugin_permissions.sh" 2>/dev/null || true
chmod 755 "$PROJECT_ROOT_DIR/scripts/install/install_service.sh" "$PROJECT_ROOT_DIR/scripts/install/install_web_service.sh" 2>/dev/null || true

# Re-apply special permissions for config directory (lost during normalization)
chmod 2775 "$PROJECT_ROOT_DIR/config" || true

echo "✓ Project file permissions normalized"
echo ""

CURRENT_STEP="Sound module configuration"
echo "Step 12: Sound module configuration..."
echo "-------------------------------------"

# Remove services that may interfere with LED matrix timing
echo "Removing potential conflicting services (bluetooth and others)..."
if [ "$SKIP_SOUND" = "1" ]; then
    echo "Skipping sound module configuration as requested (--skip-sound)."
elif apt_remove bluez bluez-firmware pi-bluetooth triggerhappy pigpio; then
    echo "✓ Unnecessary services removed (or not present)"
else
    echo "⚠ Some packages could not be removed; continuing"
fi

# Blacklist onboard sound module (idempotent)
BLACKLIST_FILE="/etc/modprobe.d/blacklist-rgb-matrix.conf"
if [ -f "$BLACKLIST_FILE" ] && grep -q '^blacklist snd_bcm2835\b' "$BLACKLIST_FILE"; then
    echo "snd_bcm2835 already blacklisted in $BLACKLIST_FILE"
else
    echo "Ensuring snd_bcm2835 is blacklisted in $BLACKLIST_FILE..."
    mkdir -p "/etc/modprobe.d"
    if [ -f "$BLACKLIST_FILE" ]; then
        cp "$BLACKLIST_FILE" "$BLACKLIST_FILE.bak" 2>/dev/null || true
    fi
    # Append once (don't clobber existing unrelated content)
    if [ -f "$BLACKLIST_FILE" ]; then
        echo "blacklist snd_bcm2835" >> "$BLACKLIST_FILE"
    else
        printf "blacklist snd_bcm2835\n" > "$BLACKLIST_FILE"
    fi
fi

# Update initramfs if available
if command -v update-initramfs >/dev/null 2>&1; then
    echo "Updating initramfs..."
    update-initramfs -u
else
    echo "update-initramfs not found; skipping"
fi

echo "✓ Sound module configuration applied"
echo ""

CURRENT_STEP="Apply performance optimizations"
echo "Step 13: Applying performance optimizations..."
echo "---------------------------------------------"

# Prefer /boot/firmware on newer Raspberry Pi OS, fall back to /boot on older
CMDLINE_FILE="/boot/firmware/cmdline.txt"
CONFIG_FILE="/boot/firmware/config.txt"
if [ ! -f "$CMDLINE_FILE" ]; then CMDLINE_FILE="/boot/cmdline.txt"; fi
if [ ! -f "$CONFIG_FILE" ]; then CONFIG_FILE="/boot/config.txt"; fi

# Append isolcpus=3 to cmdline if not present (idempotent)
if [ "$SKIP_PERF" = "1" ]; then
    echo "Skipping performance optimizations as requested (--skip-perf)."
elif [ -f "$CMDLINE_FILE" ]; then
    if grep -q '\bisolcpus=3\b' "$CMDLINE_FILE"; then
        echo "isolcpus=3 already present in $CMDLINE_FILE"
    else
        echo "Adding isolcpus=3 to $CMDLINE_FILE..."
        cp "$CMDLINE_FILE" "$CMDLINE_FILE.bak" 2>/dev/null || true
        # Ensure single-line cmdline gets the flag once, with a leading space
        sed -i '1 s/$/ isolcpus=3/' "$CMDLINE_FILE"
    fi
else
    echo "✗ $CMDLINE_FILE not found; skipping isolcpus optimization"
fi

# Ensure dtparam=audio=off in config.txt (idempotent)
if [ "$SKIP_PERF" = "1" ]; then
    : # skipped
elif [ -f "$CONFIG_FILE" ]; then
    if grep -q '^dtparam=audio=off\b' "$CONFIG_FILE"; then
        echo "Onboard audio already disabled in $CONFIG_FILE"
    elif grep -q '^dtparam=audio=on\b' "$CONFIG_FILE"; then
        echo "Disabling onboard audio in $CONFIG_FILE..."
        cp "$CONFIG_FILE" "$CONFIG_FILE.bak" 2>/dev/null || true
        sed -i 's/^dtparam=audio=on\b/dtparam=audio=off/' "$CONFIG_FILE"
    else
        echo "Adding dtparam=audio=off to $CONFIG_FILE..."
        cp "$CONFIG_FILE" "$CONFIG_FILE.bak" 2>/dev/null || true
        printf "\n# Disable onboard audio for LED matrix performance\n" >> "$CONFIG_FILE"
        echo "dtparam=audio=off" >> "$CONFIG_FILE"
    fi
else
    echo "✗ $CONFIG_FILE not found; skipping audio disable"
fi

echo "✓ Performance optimizations applied"
echo ""

CURRENT_STEP="Test the installation"
echo "Step 14: Testing the installation..."
echo "----------------------------------"

# Test sudo access
echo "Testing sudo access..."
if sudo -u "$ACTUAL_USER" sudo -n systemctl status ledmatrix.service > /dev/null 2>&1; then
    echo "✓ Sudo access test passed"
else
    echo "⚠ Sudo access test failed - may need to log out and back in"
fi

# Test journal access
echo "Testing journal access..."
if sudo -u "$ACTUAL_USER" journalctl --no-pager --lines=1 > /dev/null 2>&1; then
    echo "✓ Journal access test passed"
else
    echo "⚠ Journal access test failed - may need to log out and back in"
fi

# Check service status
echo "Checking service status..."
if systemctl is-active --quiet ledmatrix.service; then
    echo "✓ Main LED Matrix service is running"
else
    echo "⚠ Main LED Matrix service is not running"
fi

if systemctl is-active --quiet ledmatrix-web.service; then
    echo "✓ Web interface service is running"
else
    echo "⚠ Web interface service is not running"
fi

if systemctl list-unit-files | grep -q "ledmatrix-wifi-monitor.service"; then
    if systemctl is-active --quiet ledmatrix-wifi-monitor.service 2>/dev/null; then
        echo "✓ WiFi monitor service is running"
    else
        echo "⚠ WiFi monitor service is not running"
    fi
fi

echo ""
if [ "$SKIP_REBOOT_PROMPT" = "1" ]; then
    echo "Skipping reboot prompt as requested (--no-reboot-prompt)."
elif [ "$ASSUME_YES" = "1" ]; then
    echo "Non-interactive mode: rebooting now to apply changes..."
    reboot
else
    read -p "A reboot is recommended to apply kernel and audio changes. Reboot now? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Rebooting now..."
        reboot
    fi
fi

echo "=========================================="
echo "Installation Complete!"
echo "=========================================="
echo ""
echo "IMPORTANT: For group changes to take effect, you need to:"
echo "1. Log out and log back in to your SSH session, OR"
echo "2. Run: newgrp systemd-journal"
echo ""
echo "After logging back in, you can:"
echo ""
echo "Access the web interface at:"
echo "  http://your-pi-ip:5001"
echo ""
echo "Check service status:"
echo "  sudo systemctl status ledmatrix.service"
echo "  sudo systemctl status ledmatrix-web.service"
echo "  sudo systemctl status ledmatrix-wifi-monitor.service"
echo ""
echo "View logs:"
echo "  journalctl -u ledmatrix.service -f"
echo "  journalctl -u ledmatrix-web.service -f"
echo "  journalctl -u ledmatrix-wifi-monitor.service -f"
echo ""
echo "Control the display:"
echo "  sudo systemctl start ledmatrix.service"
echo "  sudo systemctl stop ledmatrix.service"
echo ""
echo "Enable/disable web interface autostart:"
echo "  Edit config/config.json and set 'web_display_autostart': true"
echo ""
echo "Configuration files:"
echo "  Main config: config/config.json (created from template automatically)"
echo "  Secrets: config/config_secrets.json (created from template automatically)"
echo "  Template: config/config.template.json (reference for new options)"
echo ""
echo "Enjoy your LED Matrix display!"
