#!/bin/bash
#
# The web interface's passwordless-sudo allow-list, /etc/sudoers.d/ledmatrix_web.
#
# Sourced by first_time_install.sh (Step 10) and
# scripts/install/configure_web_sudo.sh. Both used to carry their own copy of
# these rules, and the copies drifted: one granted safe_pip_install.sh and the
# other did not. Each caller still owns its own validate (visudo -c) / install /
# confirm flow; this file only prints the rules.
#
# Add or remove a grant here and nowhere else.

# web_sudoers_rules WEB_USER PROJECT_ROOT SYSTEMCTL_PATH BASH_PATH REBOOT_PATH POWEROFF_PATH JOURNALCTL_PATH
#
# Print the ledmatrix_web sudoers rules to stdout.
#
# SYSTEMCTL_PATH and BASH_PATH are required, and the caller must make sure they
# are not empty: `visudo -c` does not catch every such rule (with an empty
# BASH_PATH the helper rules still parse, granting the script itself).
# first_time_install.sh stops on a failed `which`; configure_web_sudo.sh checks
# them before calling this.
# REBOOT_PATH, POWEROFF_PATH and JOURNALCTL_PATH are optional: pass "" and
# their rules are left out.
web_sudoers_rules() {
    local WEB_USER="${1:-}"
    local PROJECT_ROOT="${2:-}"
    local SYSTEMCTL_PATH="${3:-}"
    local BASH_PATH="${4:-}"
    local REBOOT_PATH="${5:-}"
    local POWEROFF_PATH="${6:-}"
    local JOURNALCTL_PATH="${7:-}"

    cat << EOF
# LED Matrix Web Interface passwordless sudo configuration
# This allows the web interface user to run specific commands without a password

# Allow $WEB_USER to run specific commands without a password for the LED Matrix web interface
EOF
    if [ -n "$REBOOT_PATH" ]; then
        printf '%s\n' "$WEB_USER ALL=(ALL) NOPASSWD: $REBOOT_PATH"
    fi
    if [ -n "$POWEROFF_PATH" ]; then
        printf '%s\n' "$WEB_USER ALL=(ALL) NOPASSWD: $POWEROFF_PATH"
    fi
    cat << EOF
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH start ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH stop ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH restart ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH enable ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH disable ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH status ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH is-active ledmatrix
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH is-active ledmatrix.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH start ledmatrix-web.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH stop ledmatrix-web.service
$WEB_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH restart ledmatrix-web.service
$WEB_USER ALL=(ALL) NOPASSWD: $BASH_PATH $PROJECT_ROOT/scripts/fix_perms/safe_plugin_rm.sh *
# Install a requirements.txt as root via vetted helper, so packages are visible
# to root-run ledmatrix.service (not just the web interface's own user).
$WEB_USER ALL=(ALL) NOPASSWD: $BASH_PATH $PROJECT_ROOT/scripts/fix_perms/safe_pip_install.sh *
EOF
    if [ -n "$JOURNALCTL_PATH" ]; then
        cat << EOF
# NOEXEC, because these rules end in a wildcard and journalctl starts a pager
# when its output is a terminal. From that pager (less) a "!sh" is a root
# shell -- the standard journalctl escalation. The web interface always passes
# --no-pager, so nothing here needs it, but the rule cannot require a flag that
# sits in the middle of the command line. NOEXEC stops the command executing
# another program at all, which closes the hole without depending on wildcard
# matching subtleties.
$WEB_USER ALL=(ALL) NOPASSWD:NOEXEC: $JOURNALCTL_PATH -u ledmatrix.service *
$WEB_USER ALL=(ALL) NOPASSWD:NOEXEC: $JOURNALCTL_PATH -u ledmatrix *
$WEB_USER ALL=(ALL) NOPASSWD:NOEXEC: $JOURNALCTL_PATH -t ledmatrix *
EOF
    fi
}
