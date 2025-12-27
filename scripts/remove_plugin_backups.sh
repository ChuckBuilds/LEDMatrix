#!/bin/bash

# Script to safely remove plugin backup directories
# These were created during the plugin-to-submodule conversion

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLUGINS_DIR="$PROJECT_ROOT/plugins"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Verify submodules are working
verify_submodules() {
    log_info "Verifying submodules are working..."
    local issues=0
    
    for submod in football-scoreboard hockey-scoreboard ledmatrix-flights \
                  ledmatrix-leaderboard ledmatrix-stocks ledmatrix-weather \
                  mqtt-notifications; do
        if [ ! -d "$PLUGINS_DIR/$submod" ]; then
            log_error "Submodule directory missing: $submod"
            issues=$((issues + 1))
        elif [ ! -f "$PLUGINS_DIR/$submod/.git" ]; then
            log_error "Submodule .git file missing: $submod"
            issues=$((issues + 1))
        elif [ ! -f "$PLUGINS_DIR/$submod/manifest.json" ]; then
            log_warn "Submodule manifest missing: $submod (may be OK)"
        fi
    done
    
    if [ $issues -eq 0 ]; then
        log_info "All submodules verified ✓"
        return 0
    else
        log_error "Found $issues issues with submodules"
        return 1
    fi
}

# Remove backup directories
remove_backups() {
    log_info "Removing backup directories..."
    
    local removed=0
    local total_size=0
    
    for backup in "$PLUGINS_DIR"/*.backup*; do
        if [ -d "$backup" ]; then
            local name=$(basename "$backup")
            local size=$(du -sb "$backup" 2>/dev/null | awk '{print $1}')
            total_size=$((total_size + size))
            
            log_info "Removing: $name"
            rm -rf "$backup"
            removed=$((removed + 1))
        fi
    done
    
    if [ $removed -gt 0 ]; then
        log_info "Removed $removed backup directory(ies)"
        log_info "Freed approximately $(numfmt --to=iec-i --suffix=B $total_size 2>/dev/null || echo "$total_size bytes")"
    else
        log_info "No backup directories found"
    fi
}

# Main
main() {
    cd "$PROJECT_ROOT"
    
    echo "=== Plugin Backup Removal Script ==="
    echo
    
    # Verify submodules first
    if ! verify_submodules; then
        log_error "Submodule verification failed. Not removing backups."
        log_warn "Please fix submodule issues before removing backups."
        exit 1
    fi
    
    echo
    log_warn "This will permanently delete backup directories:"
    ls -1d "$PLUGINS_DIR"/*.backup* 2>/dev/null | sed 's|.*/|  - |' || echo "  (none found)"
    echo
    
    read -p "Continue? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Aborted"
        exit 0
    fi
    
    remove_backups
    
    log_info "Done!"
}

main "$@"

