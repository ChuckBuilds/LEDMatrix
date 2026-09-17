# Multi-Root Workspace Setup Guide

This document explains how to work on LEDMatrix and the official plugins side
by side, with one editor workspace and the plugins loaded straight from your
plugin checkout.

## Overview

Official plugins live in a single repository,
[ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins), with one
directory per plugin under `plugins/`. There are no separate per-plugin
repositories. For development you clone that monorepo **next to** LEDMatrix
and symlink its plugin directories into LEDMatrix's `plugin-repos/`, which is
where the plugin loader looks by default.

- ✅ Plugin code stays in the monorepo checkout, with its own git history
- ✅ LEDMatrix discovers the plugins through symlinks in `plugin-repos/`
- ✅ `LEDMatrix.code-workspace` opens both repositories in VS Code/Cursor

## Directory Structure

```text
~/Github/
├── LEDMatrix/                        # Main project
│   ├── plugin-repos/                 # Plugin directory the loader scans
│   │   ├── starlark-apps/            # Bundled with LEDMatrix (tracked in git)
│   │   ├── web-ui-info/              # Bundled with LEDMatrix (tracked in git)
│   │   ├── clock-simple -> ../../ledmatrix-plugins/plugins/clock-simple
│   │   ├── ledmatrix-weather -> ../../ledmatrix-plugins/plugins/ledmatrix-weather
│   │   └── ...
│   ├── LEDMatrix.code-workspace      # Opens LEDMatrix and ../ledmatrix-plugins
│   └── ...
└── ledmatrix-plugins/                # Plugin monorepo (git repo)
    ├── plugins/
    │   ├── clock-simple/
    │   ├── ledmatrix-weather/
    │   └── ...
    ├── plugins.json                  # Store registry
    └── update_registry.py
```

## How It Works

### 1. The plugin monorepo

Clone ledmatrix-plugins into the same parent directory as LEDMatrix (the
scripts below look for `../ledmatrix-plugins` relative to the LEDMatrix
root):

```bash
cd ~/Github
git clone https://github.com/ChuckBuilds/ledmatrix-plugins.git
```

### 2. Symlinks in plugin-repos/

`scripts/setup_plugin_repos.py` creates one symlink per plugin in
`LEDMatrix/plugin-repos/`, named after the plugin's manifest `id` and pointing
at `../ledmatrix-plugins/plugins/<dir>`.

### 3. Multi-root workspace

`LEDMatrix.code-workspace` has two roots: LEDMatrix itself and
`../ledmatrix-plugins`.

## Setup Scripts

### Initial Setup

```bash
cd ~/Github/LEDMatrix
python3 scripts/setup_plugin_repos.py
```

This script:
- Reads each `manifest.json` under `../ledmatrix-plugins/plugins/`
- Creates `plugin-repos/<id>` symlinks (relative) to those directories
- Leaves correct links alone, replaces links that point elsewhere, and skips
  (does not overwrite) a real directory of the same name — for example a
  plugin you installed from the Plugin Store. Remove that directory first if
  you want the linked copy.

### Updating Plugins

```bash
cd ~/Github/LEDMatrix
python3 scripts/update_plugin_repos.py
```

This runs `git pull` in `../ledmatrix-plugins` and prints the result. The
symlinks pick up the new code; restart the display to load it.

## Configuration

The loader reads plugins from `plugin_system.plugins_directory` in
`config/config.json`. The default is already right for this setup:

```json
{
  "plugin_system": {
    "plugins_directory": "plugin-repos"
  }
}
```

## Workflow

### Daily Development

1. **Open Workspace**: Open `LEDMatrix.code-workspace` in VS Code/Cursor
2. **Edit Plugins**: Edit code under `ledmatrix-plugins/plugins/<plugin>/`
3. **Test**: `python3 run.py -e` (emulator) or
   `python3 scripts/check_plugin.py --plugin <id>` from LEDMatrix
4. **Ship**: Bump `version` in the plugin's `manifest.json`, run
   `python update_registry.py` in ledmatrix-plugins, commit there

### Adding New Plugins

1. Create `plugins/<your-plugin-id>/` in the monorepo checkout
2. Run `python3 scripts/setup_plugin_repos.py` in LEDMatrix to link it

## Troubleshooting

### Plugins not discovered

```bash
cd ~/Github/LEDMatrix
ls -la plugin-repos/                  # links present and not broken?
python3 scripts/setup_plugin_repos.py # recreate them
```

Also check that `plugin_system.plugins_directory` is `plugin-repos`.

### "Monorepo plugins directory not found"

`setup_plugin_repos.py` expects the monorepo at `../ledmatrix-plugins`. Clone
it there (or symlink it there).

### Plugin updates not showing

1. Verify the link target: `ls -la plugin-repos/<id>`
2. Check that you're editing the monorepo checkout, not a store-installed copy
3. Restart the LEDMatrix service (or `run.py`)

## Notes

- `plugin-repos/` is tracked in git only for the bundled plugins
  (`starlark-apps`, `web-ui-info`). The symlinks you create are untracked
  files; don't commit them.
- For linking a single plugin into `plugins/` instead (without a sibling
  checkout), see `scripts/dev/dev_plugin_setup.sh` in the
  [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md).
- When changing a plugin in the monorepo, bump its manifest `version` and run
  `python update_registry.py`, or users won't receive the update.
