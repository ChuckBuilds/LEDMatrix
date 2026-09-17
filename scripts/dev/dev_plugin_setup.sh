#!/bin/bash

# LEDMatrix Plugin Development Setup Script
# Manages symbolic links between plugin repositories and the plugins/ directory

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PLUGINS_DIR="$PROJECT_ROOT/plugins"
CONFIG_FILE="$PROJECT_ROOT/dev_plugins.json"
DEFAULT_DEV_DIR="$HOME/.ledmatrix-dev-plugins"
# Official plugins live in one monorepo: <github_user>/<plugins_repo>, one
# directory per plugin under plugins/. Both can be overridden in
# dev_plugins.json (e.g. to work from a fork).
DEFAULT_GITHUB_USER="ChuckBuilds"
DEFAULT_PLUGINS_REPO="ledmatrix-plugins"
GITHUB_USER="$DEFAULT_GITHUB_USER"
PLUGINS_REPO="$DEFAULT_PLUGINS_REPO"
PLUGINS_BRANCH=""

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

# Print a top-level string field of a JSON file, or nothing if it is absent.
# Uses jq when installed, else python3.
json_field() {
    local file="$1"
    local key="$2"
    if command -v jq >/dev/null 2>&1; then
        jq -r --arg k "$key" '.[$k] // empty | select(type == "string")' "$file" 2>/dev/null || true
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "$file" "$key" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        value = json.load(f).get(sys.argv[2])
except Exception:
    value = None
if isinstance(value, str):
    print(value)
PY
    fi
}

# Load configuration file
load_config() {
    DEV_PLUGINS_DIR="$DEFAULT_DEV_DIR"
    if [[ -f "$CONFIG_FILE" ]]; then
        local value
        value=$(json_field "$CONFIG_FILE" dev_plugins_dir)
        [[ -n "$value" ]] && DEV_PLUGINS_DIR="$value"
        value=$(json_field "$CONFIG_FILE" github_user)
        [[ -n "$value" ]] && GITHUB_USER="$value"
        value=$(json_field "$CONFIG_FILE" plugins_repo)
        [[ -n "$value" ]] && PLUGINS_REPO="$value"
        value=$(json_field "$CONFIG_FILE" plugins_branch)
        [[ -n "$value" ]] && PLUGINS_BRANCH="$value"
        if [[ -n "$(json_field "$CONFIG_FILE" github_pattern)" ]]; then
            log_warn "dev_plugins.json: github_pattern is no longer used (official plugins are in the $PLUGINS_REPO monorepo)"
        fi
    fi
    # Expand ~ in path
    DEV_PLUGINS_DIR="${DEV_PLUGINS_DIR/#\~/$HOME}"
    mkdir -p "$DEV_PLUGINS_DIR"
}

# Top level of the git checkout containing a path, or nothing.
# A monorepo plugin is a subdirectory, so its .git is not in the plugin dir.
git_root_of() {
    git -C "$1" rev-parse --show-toplevel 2>/dev/null || true
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
        json_field "$plugin_path/manifest.json" id
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

# Clone a repository into DEV_PLUGINS_DIR, or update the existing clone.
# Prints the clone's path on stdout (log output goes to stderr).
ensure_clone() {
    local repo_url="$1"
    local branch="${2:-}"
    local repo_name
    repo_name=$(basename "$repo_url" .git)
    local target_dir="$DEV_PLUGINS_DIR/$repo_name"

    if [[ -d "$target_dir" ]]; then
        log_info "Repository already exists at $target_dir" >&2
        if [[ -d "$target_dir/.git" ]]; then
            log_info "Updating repository..." >&2
            (cd "$target_dir" && git pull --rebase) >&2 || true
        fi
    else
        if ! clone_from_github "$repo_url" "$target_dir" "$branch" >&2; then
            return 1
        fi
    fi
    echo "$target_dir"
}

# Find a plugin's directory inside a monorepo clone: plugins/<name>,
# plugins/ledmatrix-<name>, or the directory whose manifest id is <name>.
find_monorepo_plugin() {
    local repo_dir="$1"
    local name="$2"
    local candidate
    for candidate in "$repo_dir/plugins/$name" "$repo_dir/plugins/ledmatrix-$name"; do
        if [[ -f "$candidate/manifest.json" ]]; then
            echo "$candidate"
            return 0
        fi
    done
    for candidate in "$repo_dir"/plugins/*/; do
        candidate="${candidate%/}"
        [[ -f "$candidate/manifest.json" ]] || continue
        if [[ "$(get_plugin_id "$candidate")" == "$name" ]]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

# Link plugin from GitHub
link_github_plugin() {
    local plugin_name="${1:-}"
    local repo_url="${2:-}"

    if [[ -z "$plugin_name" ]]; then
        log_error "Usage: $0 link-github <plugin-name> [repo-url]"
        exit 1
    fi

    load_config

    if [[ -n "$repo_url" ]]; then
        # A plugin with its own repository (e.g. a third-party plugin): the
        # repository root is the plugin.
        local target_dir
        if ! target_dir=$(ensure_clone "$repo_url"); then
            exit 1
        fi
        if ! validate_plugin "$target_dir"; then
            log_error "Cloned repository does not appear to be a valid plugin"
            exit 1
        fi
        link_plugin "$plugin_name" "$target_dir"
        return
    fi

    # Official plugins: clone the monorepo once, link plugins/<dir> from it.
    repo_url="https://github.com/${GITHUB_USER}/${PLUGINS_REPO}.git"
    log_info "Using plugin monorepo: $repo_url"
    local repo_dir
    if ! repo_dir=$(ensure_clone "$repo_url" "$PLUGINS_BRANCH"); then
        exit 1
    fi

    local plugin_dir
    if ! plugin_dir=$(find_monorepo_plugin "$repo_dir" "$plugin_name"); then
        log_error "No plugin named '$plugin_name' in $repo_dir/plugins"
        log_info "Plugins are the directory names under $repo_dir/plugins, or their manifest ids"
        exit 1
    fi

    # Link under the manifest id: that is the name the plugin loader and
    # config.json use, and it can differ from the directory name
    # (plugins/ledmatrix-music has id ledmatrix-music, not music).
    local link_name
    link_name=$(get_plugin_id "$plugin_dir")
    [[ -n "$link_name" ]] || link_name=$(basename "$plugin_dir")
    if [[ "$link_name" != "$plugin_name" ]]; then
        log_info "Linking as '$link_name' (the plugin's manifest id)"
    fi
    link_plugin "$link_name" "$plugin_dir"
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
            if [[ -n "$(git_root_of "$target")" ]]; then
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
            
            if [[ -n "$(git_root_of "$target")" ]]; then
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
    echo -e "  ${GREEN}Clean: $clean_count${NC}"
    echo -e "  ${YELLOW}Needs attention: $dirty_count${NC}"
    # An if, not `[[ ]] &&`: as the function's last command a false test made
    # `status` exit 1 whenever nothing was broken.
    if [[ $broken_count -gt 0 ]]; then
        echo -e "  ${RED}Broken: $broken_count${NC}"
    fi
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
        local root
        root=$(git_root_of "$target")

        if [[ -z "$root" ]]; then
            log_error "Plugin repository is not a git repository: $target"
            exit 1
        fi

        log_info "Updating $plugin_name from $root"
        (cd "$root" && git pull --rebase)
        log_success "Updated $plugin_name"
    else
        # Update all linked plugins. Plugins linked from the monorepo share one
        # checkout, which is pulled once.
        log_info "Updating all linked plugins..."
        local updated=0
        local failed=0
        local pulled_roots=" "

        for item in "$PLUGINS_DIR"/*; do
            [[ -e "$item" ]] || continue
            [[ -d "$item" ]] || continue

            local name=$(basename "$item")
            [[ "$name" =~ ^\.|^_ ]] && continue

            if is_symlink "$item"; then
                local target=$(get_symlink_target "$name")
                local root
                root=$(git_root_of "$target")
                [[ -n "$root" ]] || continue
                [[ "$pulled_roots" == *" $root "* ]] && continue
                pulled_roots="$pulled_roots$root "
                log_info "Updating $root (for $name)..."
                if (cd "$root" && git pull --rebase); then
                    log_success "Updated $root"
                    updated=$((updated + 1))
                else
                    log_error "Failed to update $root"
                    failed=$((failed + 1))
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
      Without repo-url: clones (or updates) the official plugin monorepo,
      https://github.com/${DEFAULT_GITHUB_USER}/${DEFAULT_PLUGINS_REPO}.git, and links its
      plugins/<plugin-name> (or plugins/ledmatrix-<plugin-name>) under the
      plugin's manifest id
      With repo-url: clones a plugin that has its own repository and links
      the repository root
    
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
  # Link an official plugin from the monorepo
  $0 link-github football-scoreboard

  # Link a plugin from a local monorepo checkout
  $0 link hello-world ../ledmatrix-plugins/plugins/hello-world

  # Link a third-party plugin from its own repository
  $0 link-github my-plugin https://github.com/OtherUser/ledmatrix-my-plugin.git

  # Check status
  $0 status

  # Update all plugins
  $0 update

Configuration:
  Copy dev_plugins.json.example to dev_plugins.json (git-ignored) to customize:
  - dev_plugins_dir: Where to clone GitHub repos (default: ~/.ledmatrix-dev-plugins)
  - github_user:     Owner of the plugin monorepo, e.g. your fork (default: ${DEFAULT_GITHUB_USER})
  - plugins_repo:    Name of the plugin monorepo (default: ${DEFAULT_PLUGINS_REPO})
  - plugins_branch:  Branch to clone the monorepo at (default: its default branch)

  Symlinks are created in plugins/. Set plugin_system.plugins_directory to
  "plugins" in config/config.json so the plugin loader discovers them.

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

