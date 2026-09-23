# Multi-Root Workspace Setup Guide

This document explains how to work on LEDMatrix and the official plugins side
by side, with one editor workspace and the plugins loaded straight from your
plugin checkout.

## Overview

Official plugins live in a single repository,
[ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins), with one
directory per plugin under `plugins/`. There are no separate per-plugin
repositories. For development you clone that monorepo **next to** LEDMatrix
and symlink the plugin directories you are working on into LEDMatrix's
`plugins/` directory with `scripts/dev/dev_plugin_setup.sh`.

- ✅ Plugin code stays in the monorepo checkout, with its own git history
- ✅ LEDMatrix discovers the plugins through symlinks in `plugins/`
  (git-ignored), so the production `plugin-repos/` directory is untouched
- ✅ `LEDMatrix.code-workspace` opens both repositories in VS Code/Cursor

## Directory Structure

```text
~/Github/
├── LEDMatrix/                        # Main project
│   ├── plugins/                      # Dev plugin directory (git-ignored)
│   │   ├── clock-simple -> ~/Github/ledmatrix-plugins/plugins/clock-simple
│   │   ├── ledmatrix-weather -> ~/Github/ledmatrix-plugins/plugins/ledmatrix-weather
│   │   └── ...
│   ├── plugin-repos/                 # Default (Plugin Store) plugin directory
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
workspace file and `scripts/update_plugin_repos.py` look for
`../ledmatrix-plugins` relative to the LEDMatrix root):

```bash
cd ~/Github
git clone https://github.com/ChuckBuilds/ledmatrix-plugins.git
```

### 2. Symlinks in plugins/

`scripts/dev/dev_plugin_setup.sh link <name> <path>` creates
`LEDMatrix/plugins/<name>` as a symlink to a plugin directory. Use the
plugin's manifest `id` as the name: that is the name the loader and
`config.json` use, and the script warns when the two differ.

### 3. Multi-root workspace

`LEDMatrix.code-workspace` has two roots: LEDMatrix itself and
`../ledmatrix-plugins`.

## Setup

### Link plugins

```bash
cd ~/Github/LEDMatrix
./scripts/dev/dev_plugin_setup.sh link clock-simple ../ledmatrix-plugins/plugins/clock-simple
./scripts/dev/dev_plugin_setup.sh list      # show what is linked
```

If a real (non-symlink) directory of the same name already exists in
`plugins/`, the script offers to back it up and replace it.

Without a sibling checkout, `./scripts/dev/dev_plugin_setup.sh link-github
<name>` clones the monorepo into `~/.ledmatrix-dev-plugins/` instead and links
the plugin from there. See the
[Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md).

### Updating Plugins

```bash
cd ~/Github/LEDMatrix
python3 scripts/update_plugin_repos.py      # git pull in ../ledmatrix-plugins
# or
./scripts/dev/dev_plugin_setup.sh update    # git pull in every linked checkout
```

The symlinks pick up the new code; restart the display to load it.

## Configuration

The loader scans only `plugin_system.plugins_directory` in
`config/config.json` (default `plugin-repos`). Point it at `plugins` so it
finds the links:

```json
{
  "plugin_system": {
    "plugins_directory": "plugins"
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
2. Link it: `./scripts/dev/dev_plugin_setup.sh link <your-plugin-id> ../ledmatrix-plugins/plugins/<your-plugin-id>`

## Troubleshooting

### Plugins not discovered

```bash
cd ~/Github/LEDMatrix
ls -la plugins/                           # links present and not broken?
./scripts/dev/dev_plugin_setup.sh status  # link targets and git state
```

Also check that `plugin_system.plugins_directory` is `plugins`.

### Plugin updates not showing

1. Verify the link target: `ls -la plugins/<id>`
2. Check that you're editing the monorepo checkout, not a store-installed copy
3. Restart the LEDMatrix service (or `run.py`)

## Notes

- `plugins/` is git-ignored (except `plugins/.gitkeep`); the symlinks are
  never committed.
- When changing a plugin in the monorepo, bump its manifest `version` and run
  `python update_registry.py`, or users won't receive the update.
