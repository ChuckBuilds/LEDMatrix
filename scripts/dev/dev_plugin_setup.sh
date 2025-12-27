#!/bin/bash

# LEDMatrix Plugin Development Setup Script
# Manages symbolic links between plugin repositories and the plugins/ directory

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
PLUGINS_DIR="$PROJECT_ROOT/plugins"
CONFIG_FILE="$PROJECT_ROOT/dev_plugins.json"
DEFAULT_DEV_DIR="$HOME/.ledmatrix-dev-plugins"
GITHUB_USER="ChuckBuilds"
GITHUB_PATTERN="ledmatrix-"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
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

# Load configuration file
load_config() {
    if [[ -f "$CONFIG_FILE" ]]; then
        DEV_PLUGINS_DIR=$(jq -r '.dev_plugins_dir // "'"$DEFAULT_DEV_DIR"'"' "$CONFIG_FILE" 2>/dev/null || echo "$DEFAULT_DEV_DIR")
        # Expand ~ in path
        DEV_PLUGINS_DIR="${DEV_PLUGINS_DIR/#\~/$HOME}"
    else
        DEV_PLUGINS_DIR="$DEFAULT_DEV_DIR"
    fi
    mkdir -p "$DEV_PLUGINS_DIR"
}

# Validate plugin structure
validate_plugin() {
    local plugin_path="$1"
    if [[ ! -f "$plugin_path/manifest.json" ]]; then
        log_error "Plugin directory does not contain manifest.json: $plugin_path"
        return 1
    fi
    return 0
}

# Get plugin ID from manifest
get_plugin_id() {
    local plugin_path="$1"
    if [[ -f "$plugin_path/manifest.json" ]]; then
        jq -r '.id // empty' "$plugin_path/manifest.json" 2>/dev/null || echo ""
    fi
}

# Check if path is a symlink
is_symlink() {
    [[ -L "$1" ]]
}

# Check if plugin directory exists
plugin_exists() {
    [[ -e "$PLUGINS_DIR/$1" ]]
}

# Get symlink target
get_symlink_target() {
    if is_symlink "$PLUGINS_DIR/$1"; then
        readlink -f "$PLUGINS_DIR/$1"
    else
        echo ""
    fi
}

# Link a local plugin repository
link_plugin() {
    local plugin_name="$1"
    local repo_path="$2"
    
    if [[ -z "$plugin_name" ]] || [[ -z "$repo_path" ]]; then
        log_error "Usage: $0 link <plugin-name> <repo-path>"
        exit 1
    fi
    
    # Resolve absolute path
    if [[ ! "$repo_path" = /* ]]; then
        repo_path="$(cd "$(dirname "$repo_path")" && pwd)/$(basename "$repo_path")"
    fi
    
    if [[ ! -d "$repo_path" ]]; then
        log_error "Repository path does not exist: $repo_path"
        exit 1
    fi
    
    # Validate plugin structure
    if ! validate_plugin "$repo_path"; then
        exit 1
    fi
    
    # Check for existing plugin
    if plugin_exists "$plugin_name"; then
        if is_symlink "$PLUGINS_DIR/$plugin_name"; then
            local target=$(get_symlink_target "$plugin_name")
            if [[ "$target" == "$repo_path" ]]; then
                log_info "Plugin $plugin_name is already linked to $repo_path"
                return 0
            else
                log_warn "Plugin $plugin_name exists as symlink to $target"
                read -p "Replace existing symlink? (y/N): " -n 1 -r
                echo
                if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                    log_info "Aborted"
                    exit 0
                fi
                rm "$PLUGINS_DIR/$plugin_name"
            fi
        else
            log_warn "Plugin directory exists but is not a symlink: $PLUGINS_DIR/$plugin_name"
            read -p "Backup and replace? (y/N): " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                log_info "Aborted"
                exit 0
            fi
            mv "$PLUGINS_DIR/$plugin_name" "$PLUGINS_DIR/${plugin_name}.backup.$(date +%Y%m%d%H%M%S)"
        fi
    fi
    
    # Create symlink
    ln -s "$repo_path" "$PLUGINS_DIR/$plugin_name"
    
    local plugin_id=$(get_plugin_id "$repo_path")
    if [[ -n "$plugin_id" ]] && [[ "$plugin_id" != "$plugin_name" ]]; then
        log_warn "Plugin ID in manifest ($plugin_id) differs from directory name ($plugin_name)"
    fi
    
    log_success "Linked $plugin_name to $repo_path"
}

# Clone repository from GitHub
clone_from_github() {
    local repo_url="$1"
    local target_dir="$2"
    local branch="${3:-}"
    
    log_info "Cloning $repo_url to $target_dir"
    
    local clone_cmd=("git" "clone")
    
    if [[ -n "$branch" ]]; then
        clone_cmd+=("--branch" "$branch")
    fi
    
    clone_cmd+=("--depth" "1" "$repo_url" "$target_dir")
    
    if ! "${clone_cmd[@]}"; then
        log_error "Failed to clone repository"
        return 1
    fi
    
    log_success "Cloned repository successfully"
    return 0
}

# Link plugin from GitHub
link_github_plugin() {
    local plugin_name="$1"
    local repo_url="${2:-}"
    
    if [[ -z "$plugin_name" ]]; then
        log_error "Usage: $0 link-github <plugin-name> [repo-url]"
        exit 1
    fi
    
    load_config
    
    # Construct repo URL if not provided
    if [[ -z "$repo_url" ]]; then
        repo_url="https://github.com/${GITHUB_USER}/${GITHUB_PATTERN}${plugin_name}.git"
        log_info "Using default GitHub URL: $repo_url"
    fi
    
    # Determine target directory name from URL
    local repo_name=$(basename "$repo_url" .git)
    local target_dir="$DEV_PLUGINS_DIR/$repo_name"
    
    # Check if already cloned
    if [[ -d "$target_dir" ]]; then
        log_info "Repository already exists at $target_dir"
        if [[ -d "$target_dir/.git" ]]; then
            log_info "Updating repository..."
            (cd "$target_dir" && git pull --rebase || true)
        fi
    else
        # Clone the repository
        if ! clone_from_github "$repo_url" "$target_dir"; then
            exit 1
        fi
    fi
    
    # Validate plugin structure
    if ! validate_plugin "$target_dir"; then
        log_error "Cloned repository does not appear to be a valid plugin"
        exit 1
    fi
    
    # Link the plugin
    link_plugin "$plugin_name" "$target_dir"
}

# Unlink a plugin
unlink_plugin() {
    local plugin_name="$1"
    
    if [[ -z "$plugin_name" ]]; then
        log_error "Usage: $0 unlink <plugin-name>"
        exit 1
    fi
    
    if ! plugin_exists "$plugin_name"; then
        log_error "Plugin does not exist: $plugin_name"
        exit 1
    fi
    
    if ! is_symlink "$PLUGINS_DIR/$plugin_name"; then
        log_warn "Plugin $plugin_name is not a symlink. Cannot unlink."
        exit 1
    fi
    
    local target=$(get_symlink_target "$plugin_name")
    rm "$PLUGINS_DIR/$plugin_name"
    log_success "Unlinked $plugin_name (repository preserved at $target)"
}

# List all plugins
list_plugins() {
    if [[ ! -d "$PLUGINS_DIR" ]]; then
        log_error "Plugins directory does not exist: $PLUGINS_DIR"
        exit 1
    fi
    
    echo -e "${BLUE}Plugin Status:${NC}"
    echo "==============="
    echo
    
    local has_plugins=false
    
    for item in "$PLUGINS_DIR"/*; do
        [[ -e "$item" ]] || continue
        [[ -d "$item" ]] || continue
        
        local plugin_name=$(basename "$item")
        [[ "$plugin_name" =~ ^\.|^_ ]] && continue
        
        has_plugins=true
        
        if is_symlink "$item"; then
            local target=$(get_symlink_target "$plugin_name")
            echo -e "${GREEN}✓${NC} ${BLUE}$plugin_name${NC} (symlink)"
            echo "  → $target"
            
            # Check git status if it's a git repo
            if [[ -d "$target/.git" ]]; then
                local branch=$(cd "$target" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
                local status=$(cd "$target" && git status --porcelain 2>/dev/null | head -1)
                if [[ -n "$status" ]]; then
                    echo -e "  ${YELLOW}⚠ Git repo has uncommitted changes${NC} (branch: $branch)"
                else
                    echo -e "  ${GREEN}✓ Git repo is clean${NC} (branch: $branch)"
                fi
            fi
        else
            echo -e "${YELLOW}○${NC} ${BLUE}$plugin_name${NC} (regular directory)"
        fi
        echo
    done
    
    if [[ "$has_plugins" == false ]]; then
        log_info "No plugins found in $PLUGINS_DIR"
    fi
}

# Check status of all linked plugins
check_status() {
    if [[ ! -d "$PLUGINS_DIR" ]]; then
        log_error "Plugins directory does not exist: $PLUGINS_DIR"
        exit 1
    fi
    
    echo -e "${BLUE}Plugin Development Status:${NC}"
    echo "========================="
    echo
    
    local broken_count=0
    local clean_count=0
    local dirty_count=0
    
    for item in "$PLUGINS_DIR"/*; do
        [[ -e "$item" ]] || continue
        [[ -d "$item" ]] || continue
        
        local plugin_name=$(basename "$item")
        [[ "$plugin_name" =~ ^\.|^_ ]] && continue
        
        if is_symlink "$item"; then
            if [[ ! -e "$item" ]]; then
                echo -e "${RED}✗${NC} ${BLUE}$plugin_name${NC} - ${RED}BROKEN SYMLINK${NC}"
                broken_count=$((broken_count + 1))
                continue
            fi
            
            local target=$(get_symlink_target "$plugin_name")
            echo -e "${GREEN}✓${NC} ${BLUE}$plugin_name${NC}"
            echo "  Path: $target"
            
            if [[ -d "$target/.git" ]]; then
                local branch=$(cd "$target" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
                local remote=$(cd "$target" && git remote get-url origin 2>/dev/null || echo "no remote")
                local commits_behind=$(cd "$target" && git rev-list --count HEAD..@{upstream} 2>/dev/null || echo "0")
                local commits_ahead=$(cd "$target" && git rev-list --count @{upstream}..HEAD 2>/dev/null || echo "0")
                local status=$(cd "$target" && git status --porcelain 2>/dev/null)
                
                echo "  Branch: $branch"
                echo "  Remote: $remote"
                
                if [[ -n "$status" ]]; then
                    echo -e "  ${YELLOW}Status: Has uncommitted changes${NC}"
                    dirty_count=$((dirty_count + 1))
                elif [[ "$commits_behind" != "0" ]] || [[ "$commits_ahead" != "0" ]]; then
                    if [[ "$commits_behind" != "0" ]]; then
                        echo -e "  ${YELLOW}Status: $commits_behind commit(s) behind remote${NC}"
                    fi
                    if [[ "$commits_ahead" != "0" ]]; then
                        echo -e "  ${GREEN}Status: $commits_ahead commit(s) ahead of remote${NC}"
                    fi
                    dirty_count=$((dirty_count + 1))
                else
                    echo -e "  ${GREEN}Status: Clean and up to date${NC}"
                    clean_count=$((clean_count + 1))
                fi
            else
                echo "  (Not a git repository)"
            fi
            echo
        fi
    done
    
    echo "Summary:"
    echo "  ${GREEN}Clean: $clean_count${NC}"
    echo "  ${YELLOW}Needs attention: $dirty_count${NC}"
    [[ $broken_count -gt 0 ]] && echo -e "  ${RED}Broken: $broken_count${NC}"
}

# Update plugin(s)
update_plugins() {
    local plugin_name="${1:-}"
    
    load_config
    
    if [[ -n "$plugin_name" ]]; then
        # Update single plugin
        if ! plugin_exists "$plugin_name"; then
            log_error "Plugin does not exist: $plugin_name"
            exit 1
        fi
        
        if ! is_symlink "$PLUGINS_DIR/$plugin_name"; then
            log_error "Plugin $plugin_name is not a symlink"
            exit 1
        fi
        
        local target=$(get_symlink_target "$plugin_name")
        
        if [[ ! -d "$target/.git" ]]; then
            log_error "Plugin repository is not a git repository: $target"
            exit 1
        fi
        
        log_info "Updating $plugin_name from $target"
        (cd "$target" && git pull --rebase)
        log_success "Updated $plugin_name"
    else
        # Update all linked plugins
        log_info "Updating all linked plugins..."
        local updated=0
        local failed=0
        
        for item in "$PLUGINS_DIR"/*; do
            [[ -e "$item" ]] || continue
            [[ -d "$item" ]] || continue
            
            local name=$(basename "$item")
            [[ "$name" =~ ^\.|^_ ]] && continue
            
            if is_symlink "$item"; then
                local target=$(get_symlink_target "$name")
                if [[ -d "$target/.git" ]]; then
                    log_info "Updating $name..."
                    if (cd "$target" && git pull --rebase); then
                        log_success "Updated $name"
                        updated=$((updated + 1))
                    else
                        log_error "Failed to update $name"
                        failed=$((failed + 1))
                    fi
                fi
            fi
        done
        
        echo
        log_info "Update complete: $updated succeeded, $failed failed"
    fi
}

# Show usage
show_usage() {
    cat << EOF
LEDMatrix Plugin Development Setup

Usage: $0 <command> [options]

Commands:
  link <plugin-name> <repo-path>
      Link a local plugin repository to the plugins directory
    
  link-github <plugin-name> [repo-url]
      Clone and link a plugin from GitHub
      If repo-url is not provided, uses: https://github.com/${GITHUB_USER}/${GITHUB_PATTERN}<plugin-name>.git
    
  unlink <plugin-name>
      Remove symlink for a plugin (preserves repository)
    
  list
      List all plugins and their link status
    
  status
      Check status of all linked plugins (git status, branch, etc.)
    
  update [plugin-name]
      Update plugin(s) from git repository
      If plugin-name is omitted, updates all linked plugins
    
  help
      Show this help message

Examples:
  # Link a local plugin
  $0 link music ../ledmatrix-music
  
  # Link from GitHub (auto-detects URL)
  $0 link-github music
  
  # Link from GitHub with custom URL
  $0 link-github stocks https://github.com/ChuckBuilds/ledmatrix-stocks.git
  
  # Check status
  $0 status
  
  # Update all plugins
  $0 update

Configuration:
  Create dev_plugins.json in project root to customize:
  - dev_plugins_dir: Where to clone GitHub repos (default: ~/.ledmatrix-dev-plugins)
  - plugins: Plugin definitions (optional, for auto-discovery)

EOF
}

# Main command dispatcher
main() {
    # Ensure plugins directory exists
    mkdir -p "$PLUGINS_DIR"
    
    case "${1:-}" in
        link)
            shift
            link_plugin "$@"
            ;;
        link-github)
            shift
            link_github_plugin "$@"
            ;;
        unlink)
            shift
            unlink_plugin "$@"
            ;;
        list)
            list_plugins
            ;;
        status)
            check_status
            ;;
        update)
            shift
            update_plugins "$@"
            ;;
        help|--help|-h|"")
            show_usage
            ;;
        *)
            log_error "Unknown command: $1"
            echo
            show_usage
            exit 1
            ;;
    esac
}

main "$@"

