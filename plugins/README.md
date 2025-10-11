# Plugins Directory

This directory contains installed LEDMatrix plugins.

## Structure

Each plugin is in its own subdirectory:

```
plugins/
├── plugin-name/
│   ├── manifest.json      # Plugin metadata
│   ├── manager.py         # Plugin implementation
│   ├── requirements.txt   # Python dependencies (optional)
│   ├── config_schema.json # Configuration schema (optional)
│   ├── assets/            # Plugin assets (optional)
│   └── README.md          # Plugin documentation
```

## Installing Plugins

### Via Web UI (Recommended)
1. Navigate to the Plugin Store in the web interface
2. Browse or search for plugins
3. Click "Install" on the desired plugin

### Via Command Line
```bash
# Install from registry
python3 -c "from src.plugin_system.store_manager import PluginStoreManager; PluginStoreManager().install_plugin('plugin-id')"

# Install from GitHub URL
python3 -c "from src.plugin_system.store_manager import PluginStoreManager; PluginStoreManager().install_from_url('https://github.com/user/repo')"
```

## Creating Plugins

See the [Plugin Developer Guide](../docs/PLUGIN_DEVELOPER_GUIDE.md) for information on creating your own plugins.

## Plugin Discovery

Plugins in this directory are automatically discovered when the LEDMatrix system starts. A plugin must have a valid `manifest.json` file to be recognized.

## Configuration

Plugin configuration is stored in `config/config.json` under a key matching the plugin ID:

```json
{
  "plugin-name": {
    "enabled": true,
    "display_duration": 15,
    "custom_option": "value"
  }
}
```

## Support

For issues with specific plugins, contact the plugin author via their GitHub repository.

For issues with the plugin system itself, see the [main project repository](https://github.com/ChuckBuilds/LEDMatrix).

