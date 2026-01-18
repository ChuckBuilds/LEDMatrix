# Multi-Root Workspace Setup Guide

This document explains how the LEDMatrix project uses a multi-root workspace to manage plugins as separate Git repositories.

## Overview

The LEDMatrix project has been migrated from a git submodule implementation to a **multi-root workspace** implementation for managing plugins. This allows:

- ✅ Plugins to exist as independent Git repositories
- ✅ Updates to plugins without modifying the LEDMatrix project
- ✅ Easy development workflow with all repos in one workspace
- ✅ Plugin system discovers plugins via symlinks in `plugin-repos/`

## Directory Structure

```
/home/chuck/Github/
├── LEDMatrix/                    # Main project
│   ├── plugin-repos/             # Symlinks to actual repos (managed automatically)
│   │   ├── ledmatrix-clock-simple -> ../../ledmatrix-clock-simple
│   │   ├── ledmatrix-weather -> ../../ledmatrix-weather
│   │   └── ...
│   ├── LEDMatrix.code-workspace  # Multi-root workspace configuration
│   └── ...
├── ledmatrix-clock-simple/       # Plugin repository (actual git repo)
├── ledmatrix-weather/            # Plugin repository (actual git repo)
├── ledmatrix-football-scoreboard/ # Plugin repository (actual git repo)
└── ...                           # Other plugin repos
```

## How It Works

### 1. Plugin Repositories

All plugin repositories are cloned to `/home/chuck/Github/` (parent directory of LEDMatrix) as regular Git repositories:

- `ledmatrix-clock-simple/`
- `ledmatrix-weather/`
- `ledmatrix-football-scoreboard/`
- etc.

### 2. Symlinks in plugin-repos/

The `LEDMatrix/plugin-repos/` directory contains symlinks pointing to the actual repositories in the parent directory. This allows the plugin system to discover plugins without modifying the project structure.

### 3. Multi-Root Workspace

The `LEDMatrix.code-workspace` file configures VS Code/Cursor to open all plugin repositories as separate workspace roots, allowing easy development across all repos.

## Setup Scripts

### Initial Setup

If you need to clone all plugin repositories:

```bash
cd /home/chuck/Github/LEDMatrix
python3 scripts/clone_plugin_repos.py  # Note: This script was removed, see below
```

However, if you already have repos cloned, use the setup script:

```bash
cd /home/chuck/Github/LEDMatrix
python3 scripts/setup_plugin_repos.py
```

This script:
- Reads the workspace configuration
- Creates symlinks in `plugin-repos/` pointing to actual repos
- Verifies all links are created correctly

### Updating Plugins

To update all plugin repositories:

```bash
cd /home/chuck/Github/LEDMatrix
python3 scripts/update_plugin_repos.py
```

This script:
- Finds all plugins in the workspace
- Runs `git pull` on each repository
- Reports which plugins were updated

## Configuration

The plugin system is configured in `config/config.json`:

```json
{
  "plugin_system": {
    "plugins_directory": "plugin-repos",
    "auto_discover": true,
    "auto_load_enabled": true
  }
}
```

The `plugins_directory` points to `plugin-repos/`, which contains symlinks to the actual repositories.

## Workflow

### Daily Development

1. **Open Workspace**: Open `LEDMatrix.code-workspace` in VS Code/Cursor
2. **All Repos Available**: All plugin repos appear as separate folders in the workspace
3. **Edit Plugins**: Edit plugin code directly in their repositories
4. **Update Plugins**: Run `update_plugin_repos.py` to pull latest changes

### Adding New Plugins

1. **Clone Repository**: Clone the new plugin repo to `/home/chuck/Github/`
2. **Add to Workspace**: Add the plugin folder to `LEDMatrix.code-workspace`
3. **Create Symlink**: Run `setup_plugin_repos.py` to create the symlink

### Updating Individual Plugins

Since plugins are regular Git repositories, you can update them individually:

```bash
cd /home/chuck/Github/ledmatrix-weather
git pull origin master
```

Or update all at once:

```bash
cd /home/chuck/Github/LEDMatrix
python3 scripts/update_plugin_repos.py
```

## Benefits

1. **No Submodule Hassle**: No need to update `.gitmodules` or run `git submodule update`
2. **Independent Updates**: Update plugins independently without touching LEDMatrix
3. **Clean Separation**: Each plugin is a separate repository with its own history
4. **Easy Development**: Multi-root workspace makes it easy to work across repos
5. **Automatic Discovery**: Plugin system automatically discovers plugins via symlinks

## Troubleshooting

### Symlinks Not Working

If plugins aren't being discovered:

```bash
cd /home/chuck/Github/LEDMatrix
python3 scripts/setup_plugin_repos.py
```

This will recreate all symlinks.

### Missing Plugins

If a plugin is in the workspace but not found:

1. Check if the repo exists in `/home/chuck/Github/`
2. Check if the symlink exists in `plugin-repos/`
3. Run `setup_plugin_repos.py` to recreate symlinks

### Plugin Updates Not Showing

If changes to plugins aren't appearing:

1. Verify the symlink points to the correct directory: `ls -la plugin-repos/ledmatrix-weather`
2. Check that you're editing in the actual repo, not a copy
3. Restart the LEDMatrix service if running

## Notes

- The `plugin-repos/` directory is tracked in git, but only contains symlinks
- Actual plugin code lives in `/home/chuck/Github/ledmatrix-*/`
- Each plugin repo can be updated independently via `git pull`
- The LEDMatrix project doesn't need to be updated when plugins change
