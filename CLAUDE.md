# LEDMatrix

## Project Structure
- `src/plugin_system/` — Plugin loader, manager, store manager, base plugin class
- `web_interface/` — Flask web UI (blueprints, templates, static JS)
- `config/config.json` — User plugin configuration (persists across plugin reinstalls)
- `plugins/` — Installed plugins directory (gitignored)
- `plugin-repos/` — Development symlinks to monorepo plugin dirs

## Plugin System
- Plugins inherit from `BasePlugin` in `src/plugin_system/base_plugin.py`
- Required abstract methods: `update()`, `display(force_clear=False)`
- Each plugin needs: `manifest.json`, `config_schema.json`, `manager.py`, `requirements.txt`
- Plugin instantiation args: `plugin_id, config, display_manager, cache_manager, plugin_manager`
- Config schemas use JSON Schema Draft-7
- Display dimensions: always read dynamically from `self.display_manager.matrix.width/height`

## Plugin Store Architecture
- Official plugins live in the `ledmatrix-plugins` monorepo (not individual repos)
- Plugin repo naming convention: `ledmatrix-<plugin-id>` (e.g., `ledmatrix-football-scoreboard`)
- `plugins.json` registry at `https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugins/main/plugins.json`
- Store manager (`src/plugin_system/store_manager.py`) handles install/update/uninstall
- Monorepo plugins are installed via ZIP extraction (no `.git` directory)
- Update detection for monorepo plugins uses version comparison (manifest version vs registry latest_version)
- Plugin configs stored in `config/config.json`, NOT in plugin directories — safe across reinstalls
- Third-party plugins can use their own repo URL with empty `plugin_path`

## Common Pitfalls
- paho-mqtt 2.x needs `callback_api_version=mqtt.CallbackAPIVersion.VERSION1` for v1 compat
- BasePlugin uses `get_logger()` from `src.logging_config`, not standard `logging.getLogger()`
- When modifying a plugin in the monorepo, you MUST bump `version` in its `manifest.json` and run `python update_registry.py` — otherwise users won't receive the update
