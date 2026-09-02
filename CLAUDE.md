# LEDMatrix

## Project Structure
- `src/plugin_system/` — Plugin loader, manager, store manager, base plugin class
- `web_interface/` — Flask web UI (blueprints, templates, static JS)
- `config/config.json` — User plugin configuration (persists across plugin reinstalls)
- `plugin-repos/` — **Default** plugin install directory used by the
  Plugin Store, set by `plugin_system.plugins_directory` in
  `config.json` (default per `config/config.template.json:167`).
  Not gitignored.
- `plugins/` — Legacy/dev plugin location. Gitignored (`plugins/*`).
  Used by `scripts/dev/dev_plugin_setup.sh` for symlinks. The plugin
  loader does NOT fall back to it — `PluginManager.discover_plugins()`
  (`src/plugin_system/plugin_manager.py`) scans only the configured
  directory. Fallbacks exist in two narrower places: store operations
  (`StoreManager._find_plugin_path()` in `store_manager.py`) and schema
  lookup (`SchemaManager.get_schema_path()` in `schema_manager.py`,
  which probes `plugins/` *before* `plugin-repos/`).

## Plugin System
- Plugins inherit from `BasePlugin` in `src/plugin_system/base_plugin.py`
- Required abstract methods: `update()`, `display(force_clear=False)`
- Each plugin needs: `manifest.json`, `config_schema.json`, `manager.py`, `requirements.txt`
- Plugin instantiation args: `plugin_id, config, display_manager, cache_manager, plugin_manager`
- Config schemas use JSON Schema Draft-7
- Display dimensions: always read dynamically from `self.display_manager.matrix.width/height`
- Secrets: namespaced by plugin id in `config/config_secrets.json`, declared
  via `"x-secret": true` in the plugin's config schema, and deep-merged into
  the plugin's config dict at load time — plugins read them with plain
  `config.get(...)`, never a separate accessor

## Dev Workflow
- Link a plugin for development: `./scripts/dev/dev_plugin_setup.sh link-github <name>` (or `link <name> <path>`); symlinks land in `plugins/` — set `plugin_system.plugins_directory` to `plugins` so discovery picks them up
- Browser preview without the display loop: `python3 scripts/dev_server.py` → http://localhost:5001
- Full display in emulator mode: `python3 run.py -e` (or `EMULATOR=true python3 run.py`)
- Validate one plugin headlessly: `python3 scripts/check_plugin.py --plugin <id>`

## Plugin Store Architecture
- Official plugins live in the `ledmatrix-plugins` monorepo (not individual repos)
- Plugin repo naming convention: `ledmatrix-<plugin-id>` (e.g., `ledmatrix-football-scoreboard`)
- `plugins.json` registry at `https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugins/main/plugins.json`
- Store manager (`src/plugin_system/store_manager.py`) handles install/update/uninstall
- Monorepo plugins are installed via ZIP extraction (no `.git` directory)
- Update detection for monorepo plugins uses version comparison (manifest version vs registry latest_version)
- Plugin configs stored in `config/config.json`, NOT in plugin directories — safe across reinstalls
- Third-party plugins can use their own repo URL with empty `plugin_path`

## Skin System (visual overlays for sports scoreboards)
- Skins live in `skins/<skin-id>/` (skin.json + skin.py), NOT in plugin dirs — plugin reinstall deletes plugin dirs
- Core: `src/skin_system/` (ScoreboardSkin, SkinContext, runtime); hook: `SportsCore._render_game()` in `src/base_classes/sports/core.py`
- Skins render onto `ctx.canvas` only; fallback to built-in renderer on `False`/exception (3 strikes disables for session)
- View-model guaranteed keys are frozen (see `test/test_skin_system.py::TestViewModelContract`) — renaming keys in `_extract_game_details_common` or sport extractors breaks published skins
- Validate skins headlessly: `python scripts/validate_skin.py --skin <id>`; docs: `docs/SKIN_SYSTEM.md`, `docs/CREATING_SKINS.md`
- Skins are NOT monorepo plugins: no manifest bump / update_registry.py needed

## Common Pitfalls
- paho-mqtt 2.x needs `callback_api_version=mqtt.CallbackAPIVersion.VERSION1` for v1 compat
- BasePlugin uses `get_logger()` from `src.logging_config`, not standard `logging.getLogger()`
- `DisplayManager` has no `draw_image()` — paste onto the PIL image directly:
  `self.display_manager.image.paste(img, (x, y))` then `update_display()`
  (use a mask for transparency: `image.paste(rgba, (x, y), rgba)`)
- When modifying a plugin in the monorepo, you MUST bump `version` in its `manifest.json` and run `python update_registry.py` — otherwise users won't receive the update
