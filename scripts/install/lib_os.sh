#!/bin/bash
# Which operating systems and Python versions LEDMatrix installs on.
#
# Sourced by first_time_install.sh and scripts/check_system_compatibility.sh,
# so the installer and the compatibility checker cannot disagree about what
# is supported. Pure functions: nothing here installs, changes or exits --
# the callers decide what to do with the answers.
#
# Supported (Lite, no desktop):
#   Raspberry Pi OS / Debian 12 "Bookworm" -- Python 3.11
#   Raspberry Pi OS / Debian 13 "Trixie"   -- Python 3.13
#
# Everything the installer asks apt for (python3-pip, python3-venv,
# python-dev-is-python3, python3-pil, python3-pil.imagetk, build-essential,
# python3-setuptools, python3-wheel, cmake, ninja-build, git, curl, wget,
# unzip, and hostapd, dnsmasq, network-manager for WiFi setup) has the same
# name on both releases. Both ship a pip (23.0.1 and 25.1.1) that is PEP 668
# "externally managed" and accepts --break-system-packages, and a cmake (3.25
# and 3.31) new enough for the rgbmatrix build (3.22). So no step needs a
# per-release branch today; if one ever does, the release name comes from
# lm_os_release below.

# Test hook: the os-release file to read.
LM_OS_RELEASE_FILE="${LM_OS_RELEASE_FILE:-/etc/os-release}"

# Oldest and newest python3 minor versions the installer accepts. 3.11 is
# Bookworm's, and also the floor of the rgbmatrix bindings (requires-python
# >=3.11 in rpi-rgb-led-matrix-master/pyproject.toml); 3.13 is Trixie's.
LM_PYTHON_MIN_MINOR=11
LM_PYTHON_MAX_MINOR=13

# lm_os_field KEY -- one value from os-release with its quotes removed; empty
# when the key or the file is missing. Parsed rather than sourced so that
# os-release's ID, VERSION and friends do not land in the caller's variables.
lm_os_field() {
    [ -r "$LM_OS_RELEASE_FILE" ] || return 0
    sed -n "/^$1=/{s/^$1=//;s/^[\"']//;s/[\"']\$//;p;q;}" "$LM_OS_RELEASE_FILE"
}

# lm_os_release -- print "bookworm" or "trixie" and succeed on a supported
# release; print nothing and fail on anything else. VERSION_ID decides; the
# codename is used only when VERSION_ID is missing.
lm_os_release() {
    local id version
    id=$(lm_os_field ID)
    version=$(lm_os_field VERSION_ID)
    [ -n "$version" ] || version=$(lm_os_field VERSION_CODENAME)
    case "$id" in
        raspbian|debian) ;;
        *) return 1 ;;
    esac
    case "$version" in
        12|bookworm) echo bookworm ;;
        13|trixie) echo trixie ;;
        *) return 1 ;;
    esac
}

# lm_release_label RELEASE -- how to name a release to a person.
lm_release_label() {
    case "$1" in
        bookworm) echo "Debian 12 (Bookworm)" ;;
        trixie) echo "Debian 13 (Trixie)" ;;
        *) echo "$1" ;;
    esac
}

# lm_release_python RELEASE -- the python3 version a release ships, e.g. 3.11.
lm_release_python() {
    case "$1" in
        bookworm) echo 3.11 ;;
        trixie) echo 3.13 ;;
        *) return 1 ;;
    esac
}

# lm_python_version [PYTHON] -- "3.11" and so on for python3 (or PYTHON);
# prints nothing and fails when it cannot be run.
lm_python_version() {
    "${1:-python3}" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null
}

# lm_python_check VERSION -- print "ok", "too-old", "too-new" or "unknown"
# for a version such as 3.11. Always succeeds, so it is safe under set -e.
lm_python_check() {
    local major minor
    major=${1%%.*}
    minor=${1#*.}
    minor=${minor%%.*}
    case "$major:$minor" in
        *[!0-9:]*|:*|*:) echo unknown; return 0 ;;
    esac
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt "$LM_PYTHON_MIN_MINOR" ]; }; then
        echo too-old
    elif [ "$major" -gt 3 ] || [ "$minor" -gt "$LM_PYTHON_MAX_MINOR" ]; then
        echo too-new
    else
        echo ok
    fi
}

# lm_network_stack -- which service runs the network: "networkmanager",
# "dhcpcd" or "unknown". Raspberry Pi OS uses NetworkManager on both Bookworm
# and Trixie; dhcpcd appears when someone switched back to it in raspi-config.
lm_network_stack() {
    if systemctl is-active --quiet NetworkManager 2>/dev/null; then
        echo networkmanager
    elif systemctl is-active --quiet dhcpcd 2>/dev/null; then
        echo dhcpcd
    else
        echo unknown
    fi
}

# lm_print_dhcpcd_advice -- the explanation for a Pi running dhcpcd. WiFi
# setup from the web page and the LEDMatrix-Setup hotspot both drive
# NetworkManager (nmcli). The installer does not switch the network stack
# itself: doing that over SSH can cut the connection it is running on.
lm_print_dhcpcd_advice() {
    echo "⚠ This Pi manages its network with dhcpcd, not NetworkManager."
    echo "  LEDMatrix installs and the display works, but choosing a WiFi network"
    echo "  from the web page and the LEDMatrix-Setup hotspot both need NetworkManager."
    echo "  To switch (with a keyboard and screen attached, or over Ethernet):"
    echo "    sudo raspi-config  ->  Advanced Options  ->  Network Config  ->  NetworkManager"
    echo "  then reboot."
}

# lm_print_supported_os_help -- what to do on an unsupported system.
lm_print_supported_os_help() {
    echo "LEDMatrix needs Raspberry Pi OS Lite: Trixie (Debian 13) or Bookworm (Debian 12)."
    echo ""
    echo "To install Raspberry Pi OS Lite:"
    echo "  1. Download Raspberry Pi Imager from: https://www.raspberrypi.com/software/"
    echo "  2. Choose 'Raspberry Pi OS Lite (64-bit)'. Trixie is the current version and"
    echo "     is recommended; Bookworm (listed as Legacy) also works"
    echo "  3. Flash it to the SD card"
    echo "  4. Boot the Pi and run this script again"
}
