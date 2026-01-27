# Claude Code Project Memory

This file contains information that Claude should remember about this project.

## Project Overview

LEDMatrix is a plugin-based LED matrix display system designed to run on Raspberry Pi with LED matrix hardware. It uses a plugin architecture where all display functionality (except core calendar) is implemented as dynamically loaded plugins.

## Architecture

### Plugin System
- Plugins are stored in `plugins/<plugin-id>/` directories
- Each plugin requires: `manifest.json`, `manager.py`, `config_schema.json`, `requirements.txt`, `README.md`
- Plugin classes must inherit from `src.plugin_system.base_plugin.BasePlugin`
- Required methods: `update()` (data fetching), `display()` (rendering)
- Optional methods: `validate_config()`, `has_live_content()`, `get_live_modes()`, `cleanup()`, `on_config_change()`, `on_enable()`, `on_disable()`

### Key Components
- **BasePlugin**: `src/plugin_system/base_plugin.py`
- **PluginManager**: `src/plugin_system/plugin_manager.py`
- **DisplayManager**: `src/display_manager.py` - handles all drawing operations
- **CacheManager**: `src/cache_manager.py` - API response caching

### Configuration
- Main config: `config/config.json`
- Secrets: `config/config_secrets.json`
- Plugin config schema: `plugins/<plugin-id>/config_schema.json`

## Development Workflow

### Running the Project
- Emulator mode: `python run.py --emulator` or `./run_emulator.sh`
- On Pi as service: `journalctl -u ledmatrix -f` for logs

### Plugin Development
- Use `dev_plugin_setup.sh` to link plugins for development
- Plugins are typically separate repositories named `ledmatrix-<plugin-name>`
- Symlinks connect plugin repos to `plugins/` directory

### Version Management
- **Automatic**: Pre-push git hook handles version bumping automatically
- Hook bumps patch version and creates git tags
- Manual bumping only needed for major/minor versions or CI/CD edge cases
- Script for manual bumps: `scripts/bump_plugin_version.py`

## Key Resources
- Plugin Architecture Docs: `docs/PLUGIN_ARCHITECTURE_SPEC.md`
- Example Plugins: `plugins/hockey-scoreboard/`, `plugins/football-scoreboard/`
- Development Setup: `dev_plugin_setup.sh`
- Example Config: `dev_plugins.json.example`

## User Preferences

<!-- Add your preferences here -->

## Important Reminders

<!-- Add reminders here -->
