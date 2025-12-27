# LEDMatrix Plugin Architecture - Quick Reference

## Overview

Transform LEDMatrix into a modular, plugin-based system where users can create, share, and install custom displays via a GitHub-based store (similar to HACS for Home Assistant).

## Key Decisions

✅ **Gradual Migration**: Existing managers stay, plugins added alongside  
✅ **Migration Required**: Breaking changes in v3.0, tools provided  
✅ **GitHub Store**: Simple discovery, packages from repos  
✅ **Plugin Location**: `./plugins/` directory  

## File Structure

```
LEDMatrix/
├── src/
│   └── plugin_system/
│       ├── base_plugin.py          # Plugin interface
│       ├── plugin_manager.py       # Load/unload plugins
│       └── store_manager.py        # Install from GitHub
├── plugins/
│   ├── clock-simple/
│   │   ├── manifest.json           # Metadata
│   │   ├── manager.py              # Main plugin class
│   │   ├── requirements.txt        # Dependencies
│   │   ├── config_schema.json      # Validation
│   │   └── README.md
│   └── nhl-scores/
│       └── ... (same structure)
└── config/config.json               # Plugin configs
```

## Creating a Plugin

### 1. Minimal Plugin Structure

**manifest.json**:
```json
{
  "id": "my-plugin",
  "name": "My Display",
  "version": "1.0.0",
  "author": "YourName",
  "entry_point": "manager.py",
  "class_name": "MyPlugin",
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

```bash
# Create repo
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YourName/ledmatrix-my-plugin
git push -u origin main

# Tag release
git tag v1.0.0
git push origin v1.0.0

# Submit to registry (PR to ChuckBuilds/ledmatrix-plugin-registry)
```

## Using Plugins

### Web UI

1. **Browse Store**: Plugin Store tab → Search/filter
2. **Install**: Click "Install" button
3. **Configure**: Plugin Manager → Click ⚙️ Configure
4. **Enable/Disable**: Toggle switch
5. **Reorder**: Drag and drop in rotation list

### API

```python
# Install plugin
POST /api/plugins/install
{"plugin_id": "my-plugin"}

# Install from custom URL
POST /api/plugins/install-from-url
{"repo_url": "https://github.com/User/plugin"}

# List installed
GET /api/plugins/installed

# Toggle
POST /api/plugins/toggle
{"plugin_id": "my-plugin", "enabled": true}
```

### Command Line

```python
from src.plugin_system.store_manager import PluginStoreManager

store = PluginStoreManager()

# Install
store.install_plugin('nhl-scores')

# Install from URL
store.install_from_url('https://github.com/User/plugin')

# Update
store.update_plugin('nhl-scores')

# Uninstall
store.uninstall_plugin('nhl-scores')
```

## Migration Path

### Phase 1: v2.0.0 (Plugin Infrastructure)
- Plugin system alongside existing managers
- 100% backward compatible
- Web UI shows plugin store

### Phase 2: v2.1.0 (Example Plugins)
- Reference plugins created
- Migration examples
- Developer docs

### Phase 3: v2.2.0 (Migration Tools)
- Auto-migration script
- Config converter
- Testing tools

### Phase 4: v2.5.0 (Deprecation)
- Warnings on legacy managers
- Migration guide
- 95% backward compatible

### Phase 5: v3.0.0 (Plugin-Only)
- Legacy managers removed from core
- Packaged as official plugins
- **Breaking change - migration required**

## Quick Migration

```bash
# 1. Backup
cp config/config.json config/config.json.backup

# 2. Run migration
python3 scripts/migrate_to_plugins.py

# 3. Review
cat config/config.json.migrated

# 4. Apply
mv config/config.json.migrated config/config.json

# 5. Restart
sudo systemctl restart ledmatrix
```

## Plugin Registry Structure

**ChuckBuilds/ledmatrix-plugin-registry/plugins.json**:
```json
{
  "plugins": [
    {
      "id": "clock-simple",
      "name": "Simple Clock",
      "author": "ChuckBuilds",
      "category": "time",
      "repo": "https://github.com/ChuckBuilds/ledmatrix-clock-simple",
      "versions": [
        {
          "version": "1.0.0",
          "ledmatrix_min_version": "2.0.0",
          "download_url": "https://github.com/.../v1.0.0.zip"
        }
      ],
      "verified": true
    }
  ]
}
```

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

## What's Missing?

This specification covers the technical architecture. Additional considerations:

1. **Sandboxing**: Current design has no isolation (future enhancement)
2. **Resource Limits**: No CPU/memory limits per plugin (future)
3. **Plugin Ratings**: Registry needs rating/review system
4. **Auto-Updates**: Manual update only (could add auto-update)
5. **Dependency Conflicts**: No automatic resolution
6. **Version Pinning**: Limited version constraint checking
7. **Plugin Testing**: No automated testing framework
8. **Marketplace**: No paid plugins (all free/open source)

## Next Steps

1. ✅ Review this specification
2. Start Phase 1 implementation
3. Create first 3-4 example plugins
4. Set up plugin registry repo
5. Build web UI components
6. Test on Pi hardware
7. Release v2.0.0 alpha

## Questions to Resolve

Before implementing, consider:

1. Should we support plugin dependencies (plugin A requires plugin B)?
2. How to handle breaking changes in core display_manager API?
3. Should plugins be able to add new web UI pages?
4. What about plugins that need hardware beyond LED matrix?
5. How to prevent malicious plugins?
6. Should there be plugin quotas (max API calls, etc.)?
7. How to handle plugin conflicts (two clocks competing)?

---

**See PLUGIN_ARCHITECTURE_SPEC.md for full details**

