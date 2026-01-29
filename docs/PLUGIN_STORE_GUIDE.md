# Plugin Store Guide

## Overview

The LEDMatrix Plugin Store allows you to discover, install, and manage display plugins for your LED matrix. Install curated plugins from the official registry or add custom plugins directly from any GitHub repository.

---

## Quick Reference

### Install from Store
```bash
# Web UI: Plugin Store → Search → Click Install
# API:
curl -X POST http://your-pi-ip:5050/api/plugins/install \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "clock-simple"}'
```

### Install from GitHub URL
```bash
# Web UI: Plugin Store → "Install from URL" → Paste URL
# API:
curl -X POST http://your-pi-ip:5050/api/plugins/install-from-url \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/user/ledmatrix-plugin"}'
```

### Manage Plugins
```bash
# List installed
curl "http://your-pi-ip:5050/api/plugins/installed"

# Enable/disable
curl -X POST http://your-pi-ip:5050/api/plugins/toggle \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "clock-simple", "enabled": true}'

# Update
curl -X POST http://your-pi-ip:5050/api/plugins/update \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "clock-simple"}'

# Uninstall
curl -X POST http://your-pi-ip:5050/api/plugins/uninstall \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "clock-simple"}'
```

---

## Installation Methods

### Method 1: From Official Plugin Store (Recommended)

The official plugin store contains curated, verified plugins that have been reviewed by maintainers.

**Via Web Interface:**
1. Open the web interface at http://your-pi-ip:5050
2. Navigate to the "Plugin Store" tab
3. Browse or search for plugins
4. Click "Install" on the desired plugin
5. Wait for installation to complete
6. Restart the display to activate the plugin

**Via REST API:**
```bash
curl -X POST http://your-pi-ip:5050/api/plugins/install \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "clock-simple"}'
```

**Via Python:**
```python
from src.plugin_system.store_manager import PluginStoreManager

store = PluginStoreManager()
success = store.install_plugin('clock-simple')
if success:
    print("Plugin installed!")
```

### Method 2: From Custom GitHub URL

Install any plugin directly from a GitHub repository, even if it's not in the official store. This method is useful for:
- Testing your own plugins during development
- Installing community plugins before they're in the official store
- Using private plugins
- Sharing plugins with specific users

**Via Web Interface:**
1. Open the web interface
2. Navigate to the "Plugin Store" tab
3. Find the "Install from URL" section
4. Paste the GitHub repository URL (e.g., `https://github.com/user/ledmatrix-my-plugin`)
5. Click "Install from URL"
6. Review the warning about unverified plugins
7. Confirm installation
8. Wait for installation to complete
9. Restart the display

**Via REST API:**
```bash
curl -X POST http://your-pi-ip:5050/api/plugins/install-from-url \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/user/ledmatrix-my-plugin"}'
```

**Via Python:**
```python
from src.plugin_system.store_manager import PluginStoreManager

store = PluginStoreManager()
result = store.install_from_url('https://github.com/user/ledmatrix-my-plugin')

if result['success']:
    print(f"Installed: {result['plugin_id']}")
else:
    print(f"Error: {result['error']}")
```

---

## Searching for Plugins

**Via Web Interface:**
- Use the search bar to search by name, description, or author
- Filter by category (sports, weather, time, finance, etc.)
- Click on tags to filter by specific tags

**Via REST API:**
```bash
# Search by query
curl "http://your-pi-ip:5050/api/plugins/store/search?q=hockey"

# Filter by category
curl "http://your-pi-ip:5050/api/plugins/store/search?category=sports"

# Filter by tags
curl "http://your-pi-ip:5050/api/plugins/store/search?tags=nhl&tags=hockey"
```

**Via Python:**
```python
from src.plugin_system.store_manager import PluginStoreManager

store = PluginStoreManager()

# Search by query
results = store.search_plugins(query="hockey")

# Filter by category
results = store.search_plugins(category="sports")

# Filter by tags
results = store.search_plugins(tags=["nhl", "hockey"])
```

---

## Managing Installed Plugins

### List Installed Plugins

**Via Web Interface:**
- Navigate to the "Plugin Manager" tab
- View all installed plugins with their status

**Via REST API:**
```bash
curl "http://your-pi-ip:5050/api/plugins/installed"
```

**Via Python:**
```python
from src.plugin_system.store_manager import PluginStoreManager

store = PluginStoreManager()
installed = store.list_installed_plugins()

for plugin_id in installed:
    info = store.get_installed_plugin_info(plugin_id)
    print(f"{info['name']} (Last updated: {info.get('last_updated', 'unknown')})")
```

### Enable/Disable Plugins

**Via Web Interface:**
1. Navigate to the "Plugin Manager" tab
2. Use the toggle switch next to each plugin
3. Restart the display to apply changes

**Via REST API:**
```bash
curl -X POST http://your-pi-ip:5050/api/plugins/toggle \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "clock-simple", "enabled": true}'
```

### Update Plugins

**Via Web Interface:**
1. Navigate to the "Plugin Manager" tab
2. Click the "Update" button next to the plugin
3. Wait for the update to complete
4. Restart the display

**Via REST API:**
```bash
curl -X POST http://your-pi-ip:5050/api/plugins/update \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "clock-simple"}'
```

**Via Python:**
```python
from src.plugin_system.store_manager import PluginStoreManager

store = PluginStoreManager()
success = store.update_plugin('clock-simple')
```

### Uninstall Plugins

**Via Web Interface:**
1. Navigate to the "Plugin Manager" tab
2. Click the "Uninstall" button next to the plugin
3. Confirm removal
4. Restart the display

**Via REST API:**
```bash
curl -X POST http://your-pi-ip:5050/api/plugins/uninstall \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "clock-simple"}'
```

**Via Python:**
```python
from src.plugin_system.store_manager import PluginStoreManager

store = PluginStoreManager()
success = store.uninstall_plugin('clock-simple')
```

---

## Configuring Plugins

Each plugin can have its own configuration in `config/config.json`:

```json
{
  "clock-simple": {
    "enabled": true,
    "display_duration": 15,
    "color": [255, 255, 255],
    "time_format": "12h"
  },
  "nhl-scores": {
    "enabled": true,
    "favorite_teams": ["TBL", "FLA"],
    "show_favorite_teams_only": true
  }
}
```

**Via Web Interface:**
1. Navigate to the "Plugin Manager" tab
2. Click the Configure (⚙️) button next to the plugin
3. Edit the configuration in the form
4. Save changes
5. Restart the display to apply changes

---

## Safety and Security

### Verified vs Unverified Plugins

- **Verified Plugins**: Reviewed by maintainers, follow best practices, no known security issues
- **Unverified Plugins**: User-contributed, not reviewed, install at your own risk

When installing from a custom GitHub URL, you'll see a warning about installing an unverified plugin. The plugin will have access to your display manager, cache manager, configuration files, and network access.

### Best Practices

1. Only install plugins from trusted sources
2. Review plugin code before installing (click "View on GitHub")
3. Keep plugins updated for security patches
4. Report suspicious plugins to maintainers

---

## Troubleshooting

### Plugin Won't Install

**Problem:** Installation fails with "Failed to clone or download repository"

**Solutions:**
- Check that git is installed: `which git`
- Verify the GitHub URL is correct
- Check your internet connection
- The system will automatically try ZIP download as fallback

### Plugin Won't Load

**Problem:** Plugin installed but doesn't appear in rotation

**Solutions:**
1. Check that the plugin is enabled in config: `"enabled": true`
2. Verify manifest.json exists and is valid
3. Check logs for errors: `sudo journalctl -u ledmatrix -f`
4. Restart the display service: `sudo systemctl restart ledmatrix`

### Dependencies Failed

**Problem:** "Error installing dependencies" message

**Solutions:**
- Check that pip3 is installed
- Manually install: `pip3 install --break-system-packages -r plugins/plugin-id/requirements.txt`
- Check for conflicting package versions

### Plugin Shows Errors

**Problem:** Plugin loads but shows error message on display

**Solutions:**
1. Check that the plugin configuration is correct
2. Verify API keys are set (if the plugin requires them)
3. Check plugin logs: `sudo journalctl -u ledmatrix -f | grep plugin-id`
4. Report the issue to the plugin developer on GitHub

---

## API Reference

All API endpoints return JSON with this structure:

```json
{
  "status": "success" | "error",
  "message": "Human-readable message",
  "data": { ... }
}
```

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/plugins/store/list` | List all plugins in store |
| GET | `/api/plugins/store/search` | Search for plugins |
| GET | `/api/plugins/installed` | List installed plugins |
| POST | `/api/plugins/install` | Install from registry |
| POST | `/api/plugins/install-from-url` | Install from GitHub URL |
| POST | `/api/plugins/uninstall` | Uninstall plugin |
| POST | `/api/plugins/update` | Update plugin |
| POST | `/api/plugins/toggle` | Enable/disable plugin |
| POST | `/api/plugins/config` | Update plugin config |

---

## Examples

### Example 1: Install Clock Plugin

```bash
# Install
curl -X POST http://192.168.1.100:5050/api/plugins/install \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "clock-simple"}'

# Configure in config/config.json
{
  "clock-simple": {
    "enabled": true,
    "display_duration": 20,
    "time_format": "24h"
  }
}

# Restart display
sudo systemctl restart ledmatrix
```

### Example 2: Install Custom Plugin from GitHub

```bash
# Install your own plugin during development
curl -X POST http://192.168.1.100:5050/api/plugins/install-from-url \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/myusername/ledmatrix-my-custom-plugin"}'

# Enable it
curl -X POST http://192.168.1.100:5050/api/plugins/toggle \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "my-custom-plugin", "enabled": true}'

# Restart
sudo systemctl restart ledmatrix
```

### Example 3: Share Plugin with Others

As a plugin developer, you can share your plugin with others even before it's in the official store:

1. Push your plugin to GitHub: `https://github.com/yourusername/ledmatrix-awesome-plugin`
2. Share the URL with users
3. Users install via:
   - Open the LEDMatrix web interface
   - Click "Plugin Store" tab
   - Scroll to "Install from URL"
   - Paste the URL
   - Click "Install from URL"

---

## Command-Line Usage

For advanced users, manage plugins via command line:

```bash
# Install from registry
python3 -c "
from src.plugin_system.store_manager import PluginStoreManager
store = PluginStoreManager()
store.install_plugin('clock-simple')
"

# Install from URL
python3 -c "
from src.plugin_system.store_manager import PluginStoreManager
store = PluginStoreManager()
result = store.install_from_url('https://github.com/user/plugin')
print(result)
"

# List installed
python3 -c "
from src.plugin_system.store_manager import PluginStoreManager
store = PluginStoreManager()
for plugin_id in store.list_installed_plugins():
    info = store.get_installed_plugin_info(plugin_id)
    print(f'{plugin_id}: {info[\"name\"]} (Last updated: {info.get(\"last_updated\", \"unknown\")})')
"

# Uninstall
python3 -c "
from src.plugin_system.store_manager import PluginStoreManager
store = PluginStoreManager()
store.uninstall_plugin('clock-simple')
"
```

---

## FAQ

**Q: Do I need to restart the display after installing a plugin?**
A: Yes, plugins are loaded when the display controller starts.

**Q: Can I install plugins while the display is running?**
A: Yes, you can install anytime, but you must restart the display to load them.

**Q: What happens if I install a plugin with the same ID as an existing one?**
A: The existing copy will be replaced with the latest code from the repository.

**Q: Can I install multiple versions of the same plugin?**
A: No, each plugin ID maps to a single checkout of the repository's default branch.

**Q: How do I update all plugins at once?**
A: Currently, you need to update each plugin individually. Bulk update is planned for a future release.

**Q: Can plugins access my API keys from config_secrets.json?**
A: Yes, if a plugin needs API keys, it can access them like core managers do.

**Q: How much disk space do plugins use?**
A: Most plugins are small (1-5MB). Check individual plugin documentation for specific requirements.

**Q: Can I create my own plugin?**
A: Yes! See [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md) for instructions.

---

## Related Documentation

- [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md) - Create your own plugins
- [PLUGIN_API_REFERENCE.md](PLUGIN_API_REFERENCE.md) - Plugin API documentation
- [PLUGIN_ARCHITECTURE.md](PLUGIN_ARCHITECTURE.md) - Plugin system architecture
- [REST_API_REFERENCE.md](REST_API_REFERENCE.md) - Complete REST API reference
