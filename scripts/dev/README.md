# Development Scripts

This directory contains scripts and utilities for development and testing.

## Scripts

- **`dev_plugin_setup.sh`** - Sets up plugin development environment by linking plugin repositories
- **`run_emulator.sh`** - Runs the LED Matrix display in emulator mode (for development without hardware)
- **`vegas_audit.py`** - Measures how much of the Vegas ticker strip actually shows content (dead-frame ratio)
- **`test_pillow_compat.py`** - Pillow API smoke test to run after upgrading Pillow (`python3 scripts/dev/test_pillow_compat.py`)

## Usage

### Plugin Development Setup
```bash
# Official plugin: clones ChuckBuilds/ledmatrix-plugins (once) and links
# its plugins/<plugin-name> into plugins/
./scripts/dev/dev_plugin_setup.sh link-github <plugin-name>

# Plugin with its own repository
./scripts/dev/dev_plugin_setup.sh link-github <plugin-name> <repo-url>
```

Set `plugin_system.plugins_directory` to `plugins` so the loader finds the
links. To use a fork or another clone location, copy
`dev_plugins.json.example` to `dev_plugins.json`. Details:
[docs/PLUGIN_DEVELOPMENT_GUIDE.md](../../docs/PLUGIN_DEVELOPMENT_GUIDE.md).

### Running Emulator
```bash
./scripts/dev/run_emulator.sh
```

