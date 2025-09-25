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
echo "4. Install main LED Matrix service"
echo "5. Install Python project dependencies (requirements.txt)"
echo "6. Build and install rpi-rgb-led-matrix and test import"
echo "7. Install web interface dependencies"
echo "8. Install web interface service"
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
apt_install python3-pip python3-venv python3-dev python3-pil python3-pil.imagetk build-essential python3-setuptools python3-wheel cython3

# Install additional system dependencies that might be needed
echo "Installing additional system dependencies..."
apt_install git curl wget unzip

echo "✓ System dependencies installed"
echo ""

CURRENT_STEP="Fix cache permissions"
echo "Step 2: Fixing cache permissions..."
echo "----------------------------------"

# Run the cache permissions fix
if [ -f "$PROJECT_ROOT_DIR/fix_cache_permissions.sh" ]; then
    echo "Running cache permissions fix..."
    bash "$PROJECT_ROOT_DIR/fix_cache_permissions.sh"
    echo "✓ Cache permissions fixed"
else
    echo "⚠ Cache permissions script not found, creating cache directories manually..."
    mkdir -p /var/cache/ledmatrix
    chown "$ACTUAL_USER:$ACTUAL_USER" /var/cache/ledmatrix
    chmod 777 /var/cache/ledmatrix
    echo "✓ Cache directories created manually"
fi
echo ""

CURRENT_STEP="Fix assets directory permissions"
echo "Step 3: Fixing assets directory permissions..."
echo "--------------------------------------------"

# Run the assets permissions fix
if [ -f "$PROJECT_ROOT_DIR/fix_assets_permissions.sh" ]; then
    echo "Running assets permissions fix..."
    bash "$PROJECT_ROOT_DIR/fix_assets_permissions.sh"
    echo "✓ Assets permissions fixed"
else
    echo "⚠ Assets permissions script not found, fixing permissions manually..."
    
    # Set ownership of the entire assets directory to the real user
    echo "Setting ownership of assets directory..."
    chown -R "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_ROOT_DIR/assets"
    
    # Set permissions to allow read/write for owner and group, read for others
    echo "Setting permissions for assets directory..."
    chmod -R 775 "$PROJECT_ROOT_DIR/assets"
    
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
            chmod 775 "$FULL_PATH"
            chown "$ACTUAL_USER:$ACTUAL_USER" "$FULL_PATH"
        else
            echo "Creating directory: $FULL_PATH"
            mkdir -p "$FULL_PATH"
            chown "$ACTUAL_USER:$ACTUAL_USER" "$FULL_PATH"
            chmod 775 "$FULL_PATH"
        fi
    done
    
    echo "✓ Assets permissions fixed manually"
fi
echo ""

CURRENT_STEP="Install main LED Matrix service"
echo "Step 4: Installing main LED Matrix service..."
echo "---------------------------------------------"

# Run the main service installation (idempotent)
if [ -f "$PROJECT_ROOT_DIR/install_service.sh" ]; then
    echo "Running main service installation..."
    bash "$PROJECT_ROOT_DIR/install_service.sh"
    echo "✓ Main LED Matrix service installed"
else
    echo "✗ Main service installation script not found at $PROJECT_ROOT_DIR/install_service.sh"
    echo "Please ensure you are running this script from the project root: $PROJECT_ROOT_DIR"
    exit 1
fi
echo ""

CURRENT_STEP="Ensure configuration files exist"
echo "Step 4.1: Ensuring configuration files exist..."
echo "------------------------------------------------"

# Ensure config directory exists
mkdir -p "$PROJECT_ROOT_DIR/config"
chmod 755 "$PROJECT_ROOT_DIR/config" || true

# Create config.json from template if missing
if [ ! -f "$PROJECT_ROOT_DIR/config/config.json" ]; then
    if [ -f "$PROJECT_ROOT_DIR/config/config.template.json" ]; then
        echo "Creating config/config.json from template..."
        cp "$PROJECT_ROOT_DIR/config/config.template.json" "$PROJECT_ROOT_DIR/config/config.json"
        chown "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_ROOT_DIR/config/config.json" || true
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
        chown "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_ROOT_DIR/config/config.json" || true
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
        chown "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_ROOT_DIR/config/config_secrets.json" || true
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
        chown "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_ROOT_DIR/config/config_secrets.json" || true
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

# Install main project Python dependencies
cd "$PROJECT_ROOT_DIR"
if [ -f "$PROJECT_ROOT_DIR/requirements.txt" ]; then
    python3 -m pip install --break-system-packages -r "$PROJECT_ROOT_DIR/requirements.txt"
else
    echo "⚠ requirements.txt not found; skipping main dependency install"
fi

echo "✓ Project Python dependencies installed"
echo ""

CURRENT_STEP="Build and install rpi-rgb-led-matrix"
echo "Step 6: Building and installing rpi-rgb-led-matrix..."
echo "-----------------------------------------------------"

# If already installed and not forcing rebuild, skip expensive build
if python3 -c 'from rgbmatrix import RGBMatrix, RGBMatrixOptions' >/dev/null 2>&1 && [ "${RPI_RGB_FORCE_REBUILD:-0}" != "1" ]; then
    echo "rgbmatrix Python package already available; skipping build (set RPI_RGB_FORCE_REBUILD=1 to force rebuild)."
else
    # Build and install rpi-rgb-led-matrix Python bindings
    if [ -d "$PROJECT_ROOT_DIR/rpi-rgb-led-matrix-master" ]; then
        pushd "$PROJECT_ROOT_DIR/rpi-rgb-led-matrix-master" >/dev/null
        echo "Building rpi-rgb-led-matrix Python bindings..."
        make build-python PYTHON=$(which python3)
        cd bindings/python
        echo "Installing rpi-rgb-led-matrix Python package via pip..."
        python3 -m pip install --break-system-packages .
        popd >/dev/null
    else
        echo "✗ rpi-rgb-led-matrix-master directory not found at $PROJECT_ROOT_DIR"
        echo "You can clone it with: git submodule update --init --recursive (if applicable)"
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

if [ -f "$PROJECT_ROOT_DIR/install_web_service.sh" ]; then
    if [ ! -f "/etc/systemd/system/ledmatrix-web.service" ]; then
        bash "$PROJECT_ROOT_DIR/install_web_service.sh"
        # Ensure systemd sees any new/changed unit files
        systemctl daemon-reload || true
        echo "✓ Web interface service installed"
    else
        echo "ledmatrix-web.service already present; preserving existing configuration and skipping static installer"
    fi
else
    echo "⚠ install_web_service.sh not found; skipping web service installation"
fi
echo ""

CURRENT_STEP="Harden systemd unit file permissions"
echo "Step 8.1: Setting systemd unit file permissions..."
echo "-----------------------------------------------"
for unit in "/etc/systemd/system/ledmatrix.service" "/etc/systemd/system/ledmatrix-web.service"; do
    if [ -f "$unit" ]; then
        chown root:root "$unit" || true
        chmod 644 "$unit" || true
    fi
done
systemctl daemon-reload || true
echo "✓ Systemd unit file permissions set"
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
echo "Setting project file ownership..."
chown -R "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_ROOT_DIR"

# Set proper permissions for config files
if [ -f "$PROJECT_ROOT_DIR/config/config.json" ]; then
    chmod 644 "$PROJECT_ROOT_DIR/config/config.json"
    echo "✓ Config file permissions set"
fi

# Set proper permissions for secrets file (restrictive: owner rw, group r)
if [ -f "$PROJECT_ROOT_DIR/config/config_secrets.json" ]; then
    chown "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_ROOT_DIR/config/config_secrets.json" || true
    chmod 640 "$PROJECT_ROOT_DIR/config/config_secrets.json"
    echo "✓ Secrets file permissions set"
fi

# Set proper permissions for YTM auth file (readable by all users including root service)
if [ -f "$PROJECT_ROOT_DIR/config/ytm_auth.json" ]; then
    chown "$ACTUAL_USER:$ACTUAL_USER" "$PROJECT_ROOT_DIR/config/ytm_auth.json" || true
    chmod 644 "$PROJECT_ROOT_DIR/config/ytm_auth.json"
    echo "✓ YTM auth file permissions set"
fi

echo "✓ File ownership configured"
echo ""

CURRENT_STEP="Normalize project file permissions"
echo "Step 11.1: Normalizing project file and directory permissions..."
echo "--------------------------------------------------------------"

# Normalize directory permissions (exclude VCS metadata)
find "$PROJECT_ROOT_DIR" -path "*/.git*" -prune -o -type d -exec chmod 755 {} +

# Set default file permissions
find "$PROJECT_ROOT_DIR" -path "*/.git*" -prune -o -type f -exec chmod 644 {} +

# Ensure shell scripts are executable
find "$PROJECT_ROOT_DIR" -path "*/.git*" -prune -o -type f -name "*.sh" -exec chmod 755 {} +

# Explicitly ensure common helper scripts are executable (in case paths change)
chmod 755 "$PROJECT_ROOT_DIR/start_display.sh" "$PROJECT_ROOT_DIR/stop_display.sh" 2>/dev/null || true
chmod 755 "$PROJECT_ROOT_DIR/fix_cache_permissions.sh" "$PROJECT_ROOT_DIR/fix_web_permissions.sh" "$PROJECT_ROOT_DIR/fix_assets_permissions.sh" 2>/dev/null || true
chmod 755 "$PROJECT_ROOT_DIR/install_service.sh" "$PROJECT_ROOT_DIR/install_web_service.sh" 2>/dev/null || true

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
echo ""
echo "View logs:"
echo "  journalctl -u ledmatrix.service -f"
echo "  journalctl -u ledmatrix-web.service -f"
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
