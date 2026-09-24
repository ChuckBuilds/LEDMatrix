# LEDMatrix Plugin Architecture - Quick Reference

## Overview

LEDMatrix is a modular, plugin-based system where users create, share,
and install custom displays via a GitHub-based store (similar in spirit
to HACS for Home Assistant). This page is a quick reference; for the
full design see [PLUGIN_ARCHITECTURE_SPEC.md](PLUGIN_ARCHITECTURE_SPEC.md)
and [PLUGIN_DEVELOPMENT_GUIDE.md](PLUGIN_DEVELOPMENT_GUIDE.md).

## Key Decisions

✅ **Plugin-First**: All display features (calendar excepted) are now plugins
✅ **GitHub Store**: Discovery from `ledmatrix-plugins` registry plus
   any GitHub URL
✅ **Plugin Location**: configured by `plugin_system.plugins_directory`
   in `config.json` (default `plugin-repos/`). Plugin discovery scans
   only this directory — there is no loader fallback to `plugins/`
   (only Plugin Store operations and schema lookup additionally probe
   `plugins/`)

## File Structure

```
LEDMatrix/
├── src/
│   └── plugin_system/
│       ├── base_plugin.py          # Plugin interface
│       ├── plugin_manager.py       # Load/unload plugins
│       ├── plugin_loader.py        # Discovery + dynamic import
│       └── store_manager.py        # Install from GitHub
├── plugin-repos/                   # Default plugin install location
│   ├── clock-simple/
│   │   ├── manifest.json           # Metadata
│   │   ├── manager.py              # Main plugin class
│   │   ├── requirements.txt        # Dependencies
│   │   ├── config_schema.json      # Validation
│   │   └── README.md
│   └── hockey-scoreboard/
│       └── ... (same structure)
└── config/config.json               # Plugin configs
```

## Creating a Plugin

### 1. Minimal Plugin Structure

**manifest.json** (the required fields are explained in
[PLUGIN_API_REFERENCE.md](PLUGIN_API_REFERENCE.md#manifest-required-fields)):
```json
{
  "id": "my-plugin",
  "name": "My Display",
  "version": "1.0.0",
  "author": "YourName",
  "entry_point": "manager.py",
  "class_name": "MyPlugin",
  "display_modes": ["my-plugin"],
  "compatible_versions": [">=2.0.0"],
  "category": "custom"
}
```

**manager.py**:
```python
from src.plugin_system.base_plugin import BasePlugin

class MyPlugin(BasePlugin):
    def update(self):
        # Fetch data
        pass
    
    def display(self, force_clear=False):
        # Render to display
        self.display_manager.draw_text("Hello!", x=5, y=15)
        self.display_manager.update_display()
```

### 2. Configuration

**config_schema.json**:
```json
{
  "type": "object",
  "properties": {
    "enabled": {"type": "boolean", "default": true},
    "message": {"type": "string", "default": "Hello"}
  }
}
```

**User's config.json**:
```json
{
  "my-plugin": {
    "enabled": true,
    "message": "Custom text",
    "display_duration": 15
  }
}
```

### 3. Publishing

Official plugins live in the
[ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins)
monorepo: add `plugins/<your-plugin-id>/`, bump `version` in its
`manifest.json` on every change, run `python update_registry.py` there and
open a pull request. A third-party plugin can stay in its own repository and
be installed by URL. Git tags and releases are not read by the store; see
[PLUGIN_REGISTRY_SETUP_GUIDE.md](PLUGIN_REGISTRY_SETUP_GUIDE.md).

## Using Plugins

### Web UI

1. **Browse Store**: Plugin Manager tab → Plugin Store section → Search/filter
2. **Install**: Click **Install** in the plugin's row
3. **Configure**: open the plugin's tab in the second nav row
4. **Enable/Disable**: toggle switch in the **Installed Plugins** list
5. **Reorder**: use the drag-and-drop **Rotation Order** list in the
   **Rotation** tab (saved to `display.plugin_rotation_order`)

### REST API

The API is mounted at `/api/v3` (the `api_v3` blueprint in
`web_interface/blueprints/api_v3/`, registered in `web_interface/app.py`).

```bash
# Install plugin from the registry
curl -X POST http://your-pi-ip:5000/api/v3/plugins/install \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "hockey-scoreboard"}'

# Install from custom URL
curl -X POST http://your-pi-ip:5000/api/v3/plugins/install-from-url \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/User/plugin"}'

# List installed
curl http://your-pi-ip:5000/api/v3/plugins/installed

# Toggle
curl -X POST http://your-pi-ip:5000/api/v3/plugins/toggle \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "hockey-scoreboard", "enabled": true}'
```

See [REST_API_REFERENCE.md](REST_API_REFERENCE.md) for the full list.

## Plugin Registry Structure

The official registry lives at
[`ChuckBuilds/ledmatrix-plugins`](https://github.com/ChuckBuilds/ledmatrix-plugins).
The Plugin Store reads `plugins.json` at the root of that repo, which
follows this shape:
```json
{
  "plugins": [
    {
      "id": "clock-simple",
      "name": "Simple Clock",
      "author": "ChuckBuilds",
      "category": "time",
      "repo": "https://github.com/ChuckBuilds/ledmatrix-plugins",
      "branch": "main",
      "plugin_path": "plugins/clock-simple",
      "latest_version": "1.0.0",
      "verified": true
    }
  ]
}
```

`plugin_path` is empty for a third-party plugin in its own repository. The
store offers an update when the installed manifest's `version` is older
than `latest_version`.

## Benefits

### For Users
- ✅ Install only what you need
- ✅ Easy discovery of new displays
- ✅ Simple updates
- ✅ Community-created content

### For Developers
- ✅ Lower barrier to contribute
- ✅ No need to fork core repo
- ✅ Faster iteration
- ✅ Clear plugin API

### For Maintainers
- ✅ Smaller core codebase
- ✅ Less merge conflicts
- ✅ Community handles custom displays
- ✅ Easier to review changes

## Known Limitations

The plugin system is shipped and stable, but some things are still
intentionally simple:

1. **Sandboxing**: plugins run in the same process as the display loop;
   there is no isolation. Review code before installing third-party
   plugins.
2. **Resource limits**: there's a resource monitor that warns about
   slow plugins, but no hard CPU/memory caps.
3. **Plugin ratings**: not yet — the Plugin Store shows version,
   author, and category but no community rating system.
4. **Auto-updates**: off by default. Update from the Plugin Manager tab
   (per plugin, or **Check & Update All**), or turn on weekly automatic
   updates in the General tab (`auto_update.enabled`,
   `web_interface/auto_update.py`), which update LEDMatrix and then the
   installed plugins.
5. **Dependency conflicts**: each plugin's `requirements.txt` is
   installed via pip; conflicting versions across plugins are not
   resolved automatically.
6. **Plugin testing framework**: see
   [HOW_TO_RUN_TESTS.md](HOW_TO_RUN_TESTS.md) and
   [DEV_PREVIEW.md](DEV_PREVIEW.md) — there are tools, but no
   mandatory test gate.

---

**See [PLUGIN_ARCHITECTURE_SPEC.md](PLUGIN_ARCHITECTURE_SPEC.md) for the
full architectural specification.**

