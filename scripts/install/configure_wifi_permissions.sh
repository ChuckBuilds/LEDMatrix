#!/bin/bash

# LED Matrix WiFi Management Permissions Configuration Script
# This script configures both sudo and PolicyKit permissions for WiFi management

set -e

# Cleanup function for temp files
cleanup() {
    rm -f "$TEMP_SUDOERS" "$TEMP_POLKIT" 2>/dev/null || true
}
trap cleanup EXIT

echo "Configuring WiFi management permissions for LED Matrix Web Interface..."

# Get the current user (should be the user running the web interface)
WEB_USER=$(whoami)

echo "Detected web interface user: $WEB_USER"

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Error: This script should not be run as root."
    echo "Run it as the user that will be running the web interface."
    exit 1
fi

# Resolve command paths against a fixed PATH, and check what we resolved.
#
# Every path found here is written into a sudoers file as a NOPASSWD grant, so
# whoever controls the binary at that path controls root. first_time_install.sh
# re-execs itself with `sudo -E`, which preserves the invoking user's
# environment -- PATH included -- so without pinning it, `which nmcli` can
# resolve to anything on that PATH: a writable directory early in it turns a
# compromise of the low-privilege web user into permanent root.
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

# A binary named in a sudoers rule must be root-owned and writable by nobody
# else, or the grant hands root to whoever can rewrite it.
require_trusted_binary() {
    local label="$1" path="$2"
    if [ ! -x "$path" ]; then
        echo "✗ $label: $path is not an executable file"
        exit 1
    fi
    local owner perms
    owner=$(stat -c '%u' "$path") || exit 1
    perms=$(stat -c '%a' "$path") || exit 1
    if [ "$owner" != "0" ]; then
        echo "✗ $label: $path is not owned by root (uid $owner); refusing to"
        echo "  grant it NOPASSWD sudo."
        exit 1
    fi
    # Group- or world-writable means someone other than root can replace it.
    case "$perms" in
        *[2367]) echo "✗ $label: $path is writable by group or other ($perms);"
                 echo "  refusing to grant it NOPASSWD sudo."
                 exit 1 ;;
    esac
}

# Get the full paths to commands
NMCLI_PATH=$(command -v nmcli || echo "/usr/bin/nmcli")
SYSTEMCTL_PATH=$(command -v systemctl)

echo "Command paths:"
echo "  nmcli: $NMCLI_PATH"
echo "  systemctl: $SYSTEMCTL_PATH"

# Step 1: Configure sudo permissions for nmcli
echo ""
echo "Step 1: Configuring sudo permissions for nmcli..."
SUDOERS_FILE="/etc/sudoers.d/ledmatrix_wifi"
SYSCTL_PATH=$(command -v sysctl || echo /usr/sbin/sysctl)
NFT_PATH=$(command -v nft || echo /usr/sbin/nft)
RFKILL_PATH=$(command -v rfkill || echo /usr/sbin/rfkill)
MKDIR_PATH=$(command -v mkdir || echo /usr/bin/mkdir)

# Checked before any of them reaches the sudoers file.
require_trusted_binary "nmcli"     "$NMCLI_PATH"
require_trusted_binary "systemctl" "$SYSTEMCTL_PATH"
require_trusted_binary "sysctl"    "$SYSCTL_PATH"
require_trusted_binary "nft"       "$NFT_PATH"
require_trusted_binary "rfkill"    "$RFKILL_PATH"
require_trusted_binary "mkdir"     "$MKDIR_PATH"

# Create a temporary sudoers file using mktemp (handles permissions better)
TEMP_SUDOERS=$(mktemp) || {
    echo "✗ Failed to create temporary file"
    exit 1
}

cat > "$TEMP_SUDOERS" << EOF
# LED Matrix WiFi Management passwordless sudo configuration
# This allows the web interface user to run nmcli commands without a password

# Allow $WEB_USER to run nmcli commands without a password for WiFi management
$WEB_USER ALL=(ALL) NOPASSWD: $NMCLI_PATH device wifi connect *
$WEB_USER ALL=(ALL) NOPASSWD: $NMCLI_PATH device wifi disconnect *
$WEB_USER ALL=(ALL) NOPASSWD: $NMCLI_PATH device disconnect *
$WEB_USER ALL=(ALL) NOPASSWD: $NMCLI_PATH device connect *
$WEB_USER ALL=(ALL) NOPASSWD: $NMCLI_PATH radio wifi on
$WEB_USER ALL=(ALL) NOPASSWD: $NMCLI_PATH radio wifi off
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH start hostapd
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH stop hostapd
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH restart hostapd
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH start dnsmasq
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH stop dnsmasq
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH restart dnsmasq
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH restart NetworkManager
# The captive portal turns IP forwarding on while the access point is up and
# restores the previous value when it comes down (wifi_manager._setup_iptables_
# redirect / _teardown_iptables_redirect). Without this rule that sudo call
# needs a password, so forwarding stays off and clients associate to the AP but
# cannot route. It goes unnoticed on a stock Raspberry Pi image, where
# /etc/sudoers.d/010_pi-nopasswd grants the default user blanket NOPASSWD and
# masks every gap in this file -- it only bites once that blanket rule is
# removed.
$WEB_USER ALL=(ALL) NOPASSWD: $SYSCTL_PATH -w net.ipv4.ip_forward=0
$WEB_USER ALL=(ALL) NOPASSWD: $SYSCTL_PATH -w net.ipv4.ip_forward=1
# The portal's redirect lives in its own nftables table, created when the AP
# comes up and deleted when it goes down, and the radio has to be unblocked
# before the AP can start at all. Same story as the sysctl rules above: called
# with sudo, never granted here, and invisible on a stock Pi image.
$WEB_USER ALL=(ALL) NOPASSWD: $NFT_PATH add table ip ledmatrix
$WEB_USER ALL=(ALL) NOPASSWD: $NFT_PATH delete table ip ledmatrix
$WEB_USER ALL=(ALL) NOPASSWD: $RFKILL_PATH unblock wifi
# NetworkManager's dnsmasq drop-in directory, exact path.
$WEB_USER ALL=(ALL) NOPASSWD: $MKDIR_PATH -p /etc/NetworkManager/dnsmasq-shared.d
#
# iptables is deliberately NOT granted here. Its rules are built from the live
# interface name and port, so a rule covering them needs a trailing wildcard --
# and `iptables --modprobe=/path/to/anything` runs that path as root, so
# `NOPASSWD: iptables *` is a root shell for the web user by another name. That
# is a worse outcome than the gap it would close, which today is masked anyway
# by the blanket NOPASSWD rule on stock Pi images.
#
# Closing it safely means a wrapper script that builds the rules itself and
# takes only an interface and a port, granted the way safe_plugin_rm.sh already
# is. That belongs in its own change rather than being smuggled into this one.

# Allow copying hostapd and dnsmasq config files into place
$WEB_USER ALL=(ALL) NOPASSWD: /usr/bin/cp /tmp/hostapd.conf /etc/hostapd/hostapd.conf
$WEB_USER ALL=(ALL) NOPASSWD: /usr/bin/cp /tmp/dnsmasq.conf /etc/dnsmasq.d/ledmatrix-captive.conf
$WEB_USER ALL=(ALL) NOPASSWD: /usr/bin/rm -f /etc/dnsmasq.d/ledmatrix-captive.conf
EOF

echo "Generated sudoers configuration:"
echo "--------------------------------"
cat "$TEMP_SUDOERS"
echo "--------------------------------"

# Apply the sudoers configuration
echo ""
echo "Applying sudoers configuration..."
if sudo cp "$TEMP_SUDOERS" "$SUDOERS_FILE"; then
    sudo chmod 440 "$SUDOERS_FILE"
    echo "✓ Sudoers configuration applied successfully!"
else
    echo "✗ Failed to apply sudoers configuration"
    rm -f "$TEMP_SUDOERS"
    exit 1
fi

rm -f "$TEMP_SUDOERS"

# Step 2: Configure PolicyKit permissions for NetworkManager
echo ""
echo "Step 2: Configuring PolicyKit permissions for NetworkManager..."

POLKIT_RULES_DIR="/etc/polkit-1/rules.d"
POLKIT_RULE_FILE="$POLKIT_RULES_DIR/10-ledmatrix-wifi.rules"

# Create PolicyKit rule using mktemp (handles permissions better)
TEMP_POLKIT=$(mktemp) || {
    echo "✗ Failed to create temporary file"
    exit 1
}

cat > "$TEMP_POLKIT" << EOF
// LED Matrix WiFi Management PolicyKit rules
// This allows the web interface user to control NetworkManager without authentication

polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") == 0 && 
        subject.user == "$WEB_USER") {
        return polkit.Result.YES;
    }
});
EOF

echo "Generated PolicyKit rule:"
echo "--------------------------------"
cat "$TEMP_POLKIT"
echo "--------------------------------"

# Apply the PolicyKit rule
echo ""
echo "Applying PolicyKit rule..."
if sudo cp "$TEMP_POLKIT" "$POLKIT_RULE_FILE"; then
    sudo chmod 644 "$POLKIT_RULE_FILE"
    echo "✓ PolicyKit rule applied successfully!"
else
    echo "✗ Failed to apply PolicyKit rule"
    rm -f "$TEMP_POLKIT"
    exit 1
fi

rm -f "$TEMP_POLKIT"

# Step 3: Test permissions
echo ""
echo "Step 3: Testing permissions..."

# Test sudo access
if sudo -n "$NMCLI_PATH" device status > /dev/null 2>&1; then
    echo "✓ nmcli device status - OK"
else
    echo "✗ nmcli device status - Failed (this is expected if not connected)"
fi

echo ""
echo "Configuration complete!"
echo ""
echo "The web interface user ($WEB_USER) now has:"
echo "- Passwordless sudo access to nmcli commands"
echo "- PolicyKit permissions to control NetworkManager"
echo ""
echo "You may need to restart the web interface service for changes to take effect:"
echo "  sudo systemctl restart ledmatrix-web.service"
echo ""
echo "Or if running manually, restart your Flask application."

