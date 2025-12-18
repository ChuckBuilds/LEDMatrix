#!/bin/bash

# Script to normalize all plugins as git submodules
# This ensures uniform plugin management across the repository

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLUGINS_DIR="$PROJECT_ROOT/plugins"
GITMODULES="$PROJECT_ROOT/.gitmodules"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if a plugin is in .gitmodules
is_in_gitmodules() {
    local plugin_path="$1"
    git config -f "$GITMODULES" --get-regexp "^submodule\." | grep -q "path = $plugin_path$" || return 1
}

# Get submodule URL from .gitmodules
get_submodule_url() {
    local plugin_path="$1"
    git config -f "$GITMODULES" "submodule.$plugin_path.url" 2>/dev/null || echo ""
}

# Check if directory is a git repo
is_git_repo() {
    [[ -d "$1/.git" ]]
}

# Get git remote URL
get_git_remote() {
    local plugin_dir="$1"
    if is_git_repo "$plugin_dir"; then
        (cd "$plugin_dir" && git remote get-url origin 2>/dev/null || echo "")
    else
        echo ""
    fi
}

# Check if directory is a symlink
is_symlink() {
    [[ -L "$1" ]]
}

# Check if plugin has GitHub repo
has_github_repo() {
    local plugin_name="$1"
    local url="https://github.com/ChuckBuilds/ledmatrix-$plugin_name"
    local status=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "0")
    [[ "$status" == "200" ]]
}

# Update .gitignore to allow a plugin submodule
update_gitignore() {
    local plugin_name="$1"
    local plugin_path="plugins/$plugin_name"
    local gitignore="$PROJECT_ROOT/.gitignore"
    
    # Check if already in .gitignore exceptions
    if grep -q "!plugins/$plugin_name$" "$gitignore" 2>/dev/null; then
        log_info "Plugin $plugin_name already in .gitignore exceptions"
        return 0
    fi
    
    # Find the line with the last plugin exception
    local last_line=$(grep -n "!plugins/" "$gitignore" | tail -1 | cut -d: -f1)
    
    if [[ -z "$last_line" ]]; then
        log_warn "Could not find plugin exceptions in .gitignore"
        return 1
    fi
    
    # Add exceptions after the last plugin exception
    log_info "Updating .gitignore to allow $plugin_name submodule"
    sed -i "${last_line}a!plugins/$plugin_name\n!plugins/$plugin_name/" "$gitignore"
    
    log_success "Updated .gitignore for $plugin_name"
}

# Re-initialize a submodule that appears as regular directory
reinit_submodule() {
    local plugin_name="$1"
    local plugin_path="plugins/$plugin_name"
    local plugin_dir="$PLUGINS_DIR/$plugin_name"
    
    log_info "Re-initializing submodule: $plugin_name"
    
    if ! is_in_gitmodules "$plugin_path"; then
        log_error "Plugin $plugin_name is not in .gitmodules"
        return 1
    fi
    
    local submodule_url=$(get_submodule_url "$plugin_path")
    if [[ -z "$submodule_url" ]]; then
        log_error "Could not find URL for $plugin_name in .gitmodules"
        return 1
    fi
    
    # If it's a symlink, remove it first
    if is_symlink "$plugin_dir"; then
        log_warn "Removing symlink: $plugin_dir"
        rm "$plugin_dir"
    fi
    
    # If it's a regular directory with .git, we need to handle it carefully
    if is_git_repo "$plugin_dir"; then
        local remote_url=$(get_git_remote "$plugin_dir")
        if [[ "$remote_url" == "$submodule_url" ]] || [[ "$remote_url" == "${submodule_url%.git}" ]] || [[ "${submodule_url%.git}" == "$remote_url" ]]; then
            log_info "Directory is already the correct git repo, re-initializing submodule..."
            # Remove from git index and re-add as submodule
            git rm --cached "$plugin_path" 2>/dev/null || true
            rm -rf "$plugin_dir"
        else
            log_warn "Directory has different remote ($remote_url vs $submodule_url)"
            log_warn "Backing up to ${plugin_dir}.backup"
            mv "$plugin_dir" "${plugin_dir}.backup"
        fi
    fi
    
    # Re-add as submodule (use -f to force if needed)
    if git submodule add -f "$submodule_url" "$plugin_path" 2>/dev/null; then
        log_info "Submodule added successfully"
    else
        log_info "Submodule already exists, updating..."
        git submodule update --init "$plugin_path"
    fi
    
    log_success "Re-initialized submodule: $plugin_name"
}

# Convert standalone git repo to submodule
convert_to_submodule() {
    local plugin_name="$1"
    local plugin_path="plugins/$plugin_name"
    local plugin_dir="$PLUGINS_DIR/$plugin_name"
    
    log_info "Converting to submodule: $plugin_name"
    
    if is_in_gitmodules "$plugin_path"; then
        log_warn "Plugin $plugin_name is already in .gitmodules, re-initializing instead"
        reinit_submodule "$plugin_name"
        return 0
    fi
    
    if ! is_git_repo "$plugin_dir"; then
        log_error "Plugin $plugin_name is not a git repository"
        return 1
    fi
    
    local remote_url=$(get_git_remote "$plugin_dir")
    if [[ -z "$remote_url" ]]; then
        log_error "Plugin $plugin_name has no remote URL"
        return 1
    fi
    
    # If it's a symlink, we need to handle it differently
    if is_symlink "$plugin_dir"; then
        local target=$(readlink -f "$plugin_dir")
        log_warn "Plugin is a symlink to $target"
        log_warn "Removing symlink and adding as submodule"
        rm "$plugin_dir"
        
        # Update .gitignore first
        update_gitignore "$plugin_name"
        
        # Add as submodule
        if git submodule add -f "$remote_url" "$plugin_path"; then
            log_success "Added submodule: $plugin_name"
            return 0
        else
            log_error "Failed to add submodule"
            return 1
        fi
    fi
    
    # Backup the directory
    log_info "Backing up existing directory to ${plugin_dir}.backup"
    mv "$plugin_dir" "${plugin_dir}.backup"
    
    # Remove from git index
    git rm --cached "$plugin_path" 2>/dev/null || true
    
    # Update .gitignore first
    update_gitignore "$plugin_name"
    
    # Add as submodule (use -f to force if .gitignore blocks it)
    if git submodule add -f "$remote_url" "$plugin_path"; then
        log_success "Converted to submodule: $plugin_name"
        log_warn "Backup saved at ${plugin_dir}.backup - you can remove it after verifying"
    else
        log_error "Failed to add submodule"
        log_warn "Restoring backup..."
        mv "${plugin_dir}.backup" "$plugin_dir"
        return 1
    fi
}

# Add new submodule for plugin with GitHub repo
add_new_submodule() {
    local plugin_name="$1"
    local plugin_path="plugins/$plugin_name"
    local plugin_dir="$PLUGINS_DIR/$plugin_name"
    local repo_url="https://github.com/ChuckBuilds/ledmatrix-$plugin_name.git"
    
    log_info "Adding new submodule: $plugin_name"
    
    if is_in_gitmodules "$plugin_path"; then
        log_warn "Plugin $plugin_name is already in .gitmodules"
        return 0
    fi
    
    if [[ -e "$plugin_dir" ]]; then
        if is_symlink "$plugin_dir"; then
            log_warn "Removing symlink: $plugin_dir"
            rm "$plugin_dir"
        elif is_git_repo "$plugin_dir"; then
            log_warn "Directory exists as git repo, converting instead"
            convert_to_submodule "$plugin_name"
            return 0
        else
            log_warn "Backing up existing directory to ${plugin_dir}.backup"
            mv "$plugin_dir" "${plugin_dir}.backup"
        fi
    fi
    
    # Remove from git index if it exists
    git rm --cached "$plugin_path" 2>/dev/null || true
    
    # Update .gitignore first
    update_gitignore "$plugin_name"
    
    # Add as submodule (use -f to force if .gitignore blocks it)
    if git submodule add -f "$repo_url" "$plugin_path"; then
        log_success "Added new submodule: $plugin_name"
    else
        log_error "Failed to add submodule"
        if [[ -e "${plugin_dir}.backup" ]]; then
            log_warn "Restoring backup..."
            mv "${plugin_dir}.backup" "$plugin_dir"
        fi
        return 1
    fi
}

# Main processing function
main() {
    cd "$PROJECT_ROOT"
    
    log_info "Normalizing all plugins as git submodules..."
    echo
    
    # Step 1: Re-initialize submodules that appear as regular directories
    log_info "Step 1: Re-initializing existing submodules..."
    for plugin in basketball-scoreboard calendar clock-simple odds-ticker olympics-countdown soccer-scoreboard text-display mqtt-notifications; do
        if [[ -d "$PLUGINS_DIR/$plugin" ]] && is_in_gitmodules "plugins/$plugin"; then
            if ! git submodule status "plugins/$plugin" >/dev/null 2>&1; then
                reinit_submodule "$plugin"
            else
                log_info "Submodule $plugin is already properly initialized"
            fi
        fi
    done
    echo
    
    # Step 2: Convert standalone git repos to submodules
    log_info "Step 2: Converting standalone git repos to submodules..."
    for plugin in baseball-scoreboard ledmatrix-stocks; do
        if [[ -d "$PLUGINS_DIR/$plugin" ]] && is_git_repo "$PLUGINS_DIR/$plugin"; then
            if ! is_in_gitmodules "plugins/$plugin"; then
                convert_to_submodule "$plugin"
            fi
        fi
    done
    echo
    
    # Step 2b: Convert symlinks to submodules
    log_info "Step 2b: Converting symlinks to submodules..."
    for plugin in christmas-countdown ledmatrix-music static-image; do
        if [[ -L "$PLUGINS_DIR/$plugin" ]]; then
            if ! is_in_gitmodules "plugins/$plugin"; then
                convert_to_submodule "$plugin"
            fi
        fi
    done
    echo
    
    # Step 3: Add new submodules for plugins with GitHub repos
    log_info "Step 3: Adding new submodules for plugins with GitHub repos..."
    for plugin in football-scoreboard hockey-scoreboard; do
        if [[ -d "$PLUGINS_DIR/$plugin" ]] && has_github_repo "$plugin"; then
            if ! is_in_gitmodules "plugins/$plugin"; then
                add_new_submodule "$plugin"
            fi
        fi
    done
    echo
    
    # Step 4: Report on plugins without GitHub repos
    log_info "Step 4: Checking plugins without GitHub repos..."
    for plugin in ledmatrix-flights ledmatrix-leaderboard ledmatrix-weather; do
        if [[ -d "$PLUGINS_DIR/$plugin" ]]; then
            if ! is_in_gitmodules "plugins/$plugin" && ! is_git_repo "$PLUGINS_DIR/$plugin"; then
                log_warn "Plugin $plugin has no GitHub repo and is not a git repo"
                log_warn "  This plugin may be local-only or needs a repository created"
            fi
        fi
    done
    echo
    
    # Final: Initialize all submodules
    log_info "Finalizing: Initializing all submodules..."
    git submodule update --init --recursive
    
    log_success "Plugin normalization complete!"
    log_info "Run 'git status' to see changes"
    log_info "Run 'git submodule status' to verify all submodules"
}

main "$@"

