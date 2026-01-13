#!/bin/bash
# Quick diagnostic script to check why first_time_install.sh is failing
# Run this on the Pi: bash debug_install.sh

echo "=== Diagnostic Script for Installation Failure ==="
echo ""

echo "1. Checking if running as root:"
if [ "$EUID" -eq 0 ]; then
    echo "   ✓ Running as root (EUID=$EUID)"
else
    echo "   ✗ NOT running as root (EUID=$EUID, user=$(whoami))"
fi
echo ""

echo "2. Checking if first_time_install.sh exists:"
if [ -f "./first_time_install.sh" ]; then
    echo "   ✓ Found ./first_time_install.sh"
    echo "   Checking if executable:"
    if [ -x "./first_time_install.sh" ]; then
        echo "     ✓ Is executable"
    else
        echo "     ✗ NOT executable (fix with: chmod +x first_time_install.sh)"
    fi
else
    echo "   ✗ NOT found in current directory"
    echo "   Current directory: $(pwd)"
fi
echo ""

echo "3. Testing argument passing with -y flag:"
echo "   Running: bash ./first_time_install.sh -y --help 2>&1 | head -20"
if [ -f "./first_time_install.sh" ]; then
    bash ./first_time_install.sh -y --help 2>&1 | head -20 || echo "   ✗ Script failed or not found"
else
    echo "   ✗ first_time_install.sh not found"
fi
echo ""

echo "4. Checking environment variable:"
echo "   LEDMATRIX_ASSUME_YES=${LEDMATRIX_ASSUME_YES:-not set}"
echo "   Testing with env: env LEDMATRIX_ASSUME_YES=1 bash -c 'echo ASSUME_YES would be set'"
env LEDMATRIX_ASSUME_YES=1 bash -c 'echo "   ASSUME_YES would be: ${LEDMATRIX_ASSUME_YES:-not set}"'
echo ""

echo "5. Testing sudo with arguments:"
echo "   Command: sudo -E env LEDMATRIX_ASSUME_YES=1 bash ./first_time_install.sh -y --help 2>&1 | head -20"
if [ -f "./first_time_install.sh" ]; then
    sudo -E env LEDMATRIX_ASSUME_YES=1 bash ./first_time_install.sh -y --help 2>&1 | head -20 || echo "   ✗ Sudo command failed"
else
    echo "   ✗ first_time_install.sh not found"
fi
echo ""

echo "6. Checking /tmp permissions:"
echo "   /tmp is writable: $([ -w /tmp ] && echo 'YES' || echo 'NO')"
echo "   /tmp permissions: $(stat -c '%a' /tmp 2>/dev/null || echo 'unknown')"
echo "   TMPDIR: ${TMPDIR:-not set}"
echo ""

echo "7. Checking stdin/TTY:"
if [ -t 0 ]; then
    echo "   ✓ stdin is a TTY (interactive)"
else
    echo "   ✗ stdin is NOT a TTY (non-interactive/pipe)"
    echo "   This is expected when running via curl | bash"
fi
echo ""

echo "8. Latest installation log:"
# Determine project root directory (parent of scripts/install/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$PROJECT_ROOT_DIR/logs"
LOG_FILE=$(ls -t "$LOG_DIR"/first_time_install_*.log 2>/dev/null | head -1)
if [ -n "$LOG_FILE" ]; then
    echo "   Found: $LOG_FILE"
    echo "   Last 30 lines:"
    tail -30 "$LOG_FILE" | sed 's/^/   /'
else
    echo "   No log files found in $LOG_DIR/"
fi
echo ""

echo "=== Diagnostic Complete ==="
