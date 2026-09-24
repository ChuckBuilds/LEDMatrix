# LEDMatrix

## Project Structure
- `src/plugin_system/` — Plugin loader, manager, store manager, base plugin class
- `web_interface/` — Flask web UI (blueprints, templates, static JS)
- `config/config.json` — User plugin configuration (persists across plugin reinstalls)
- `plugin-repos/` — **Default** plugin install directory used by the
  Plugin Store, set by `plugin_system.plugins_directory` in
  `config.json` (default per `config/config.template.json`).
  Not gitignored.
- `plugins/` — Legacy/dev plugin location. Gitignored (`plugins/*`).
  Used by `scripts/dev/dev_plugin_setup.sh` for symlinks. The plugin
  loader does NOT fall back to it — `PluginManager.discover_plugins()`
  (`src/plugin_system/plugin_manager.py`) scans only the configured
  directory. Fallbacks exist in two narrower places: store operations
  (`PluginStoreManager._find_plugin_path()` in `store_manager.py`, which
  searches `store_search_dirs()` from `plugin_dirs.py`) and schema lookup
  (`SchemaManager.get_schema_path()` in `schema_manager.py`, which probes
  `plugins/` *before* `plugin-repos/`).
- `src/plugin_system/plugin_dirs.py` — the one resolver for "which directory
  holds plugin X" (manifest `id` first, then `<id>` / `ledmatrix-<id>`)

## Plugin System
- Plugins inherit from `BasePlugin` in `src/plugin_system/base_plugin.py`
- Required abstract methods: `update()`, `display(force_clear=False)`
- Each plugin needs: `manifest.json`, `config_schema.json`, and the entry point (`manager.py` by default); `requirements.txt` if it has dependencies. Required manifest fields: `docs/PLUGIN_API_REFERENCE.md#manifest-required-fields`
- Plugin instantiation args: `plugin_id, config, display_manager, cache_manager, plugin_manager`
- Config schemas use JSON Schema Draft-7
- Display dimensions: always read dynamically from `self.display_manager.width/height` — not `display_manager.matrix.width/height`, because `matrix` is `None` when hardware init fails (the properties fall back to the canvas size)
- Secrets: namespaced by plugin id in `config/config_secrets.json`, declared
  via `"x-secret": true` in the plugin's config schema, and deep-merged into
  the plugin's config dict at load time — plugins read them with plain
  `config.get(...)`, never a separate accessor

## Dev Workflow
- Link a plugin for development: `./scripts/dev/dev_plugin_setup.sh link-github <name>` clones the `ledmatrix-plugins` monorepo into `~/.ledmatrix-dev-plugins/` and links its `plugins/<name>` under the manifest id (add a repo URL for a plugin with its own repo; or `link <name> <path>`); symlinks land in `plugins/` — set `plugin_system.plugins_directory` to `plugins` so discovery picks them up. Fork/location overrides: `dev_plugins.json` (from `dev_plugins.json.example`)
- Browser preview without the display loop: `python3 scripts/dev_server.py` → http://localhost:5001
- Full display in emulator mode: `python3 run.py -e` (or `EMULATOR=true python3 run.py`)
- Validate one plugin headlessly: `python3 scripts/check_plugin.py --plugin <id>`
- Soak a rig for frame timing (on the Pi, service running): `python3 scripts/frame_soak.py --preview` — late-frame rate across every scroller; see `docs/SCROLL_PERFORMANCE.md`

## Plugin Store Architecture
- Official plugins live in the `ledmatrix-plugins` monorepo (not individual repos)
- Plugin repo naming convention: `ledmatrix-<plugin-id>` (e.g., `ledmatrix-football-scoreboard`)
- `plugins.json` registry at `https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugins/main/plugins.json`
- Store manager (`PluginStoreManager` in `src/plugin_system/store_manager.py`) handles install/update/uninstall
- Monorepo plugins are installed without a `.git` directory: GitHub Trees API + raw downloads, falling back to ZIP extraction
- Update detection for monorepo plugins uses version comparison (manifest version vs registry latest_version)
- Plugin configs stored in `config/config.json`, NOT in plugin directories — safe across reinstalls
- Third-party plugins can use their own repo URL with empty `plugin_path`

## Common Pitfalls
- paho-mqtt 2.x requires a `CallbackAPIVersion` argument: `VERSION1` for code written against v1 callback signatures (the MQTT bridge uses `VERSION2`)
- BasePlugin uses `get_logger()` from `src.logging_config`, not standard `logging.getLogger()`
- `DisplayManager` has no `draw_image()` — paste onto the PIL image directly:
  `self.display_manager.image.paste(img, (x, y))` then `update_display()`
  (use a mask for transparency: `image.paste(rgba, (x, y), rgba)`)
- When modifying a plugin in the monorepo, you MUST bump `version` in its `manifest.json` and run `python update_registry.py` — otherwise users won't receive the update
- `src/pi5_matrix_support.py` hardcodes what the pinned `rpi-rgb-led-matrix-master` can drive on a Raspberry Pi 5 (`Rp1PioConfigSupported()` in `lib/rp1/rp1_pio_backend.cc`). Re-check it whenever the submodule is bumped: a stale rule blocks Pi 5 settings the new library supports, and a missing one lets the display service crash-loop. `src/matrix_support.py` holds the same kind of rules for every board (rows, chain length, mapping names, parallel per mapping) and needs the same re-check
