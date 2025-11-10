# LEDMatrix Plugin Development Guide

This guide provides comprehensive instructions for creating, running, and loading plugins in the LEDMatrix project.

## Table of Contents

1. [Plugin System Overview](#plugin-system-overview)
2. [Creating a New Plugin](#creating-a-new-plugin)
3. [Running Plugins](#running-plugins)
4. [Loading Plugins](#loading-plugins)
5. [Plugin Development Workflow](#plugin-development-workflow)
6. [Testing Plugins](#testing-plugins)
7. [Troubleshooting](#troubleshooting)

---

## Plugin System Overview

The LEDMatrix project uses a plugin-based architecture where all display functionality (except core calendar) is implemented as plugins. Plugins are dynamically loaded from the `plugins/` directory and integrated into the display rotation.

### Plugin Architecture

```
LEDMatrix Core
├── Plugin Manager (discovers, loads, manages plugins)
├── Display Manager (handles LED matrix rendering)
├── Cache Manager (data persistence)
├── Config Manager (configuration management)
└── Plugins/ (plugin directory)
    ├── plugin-1/
    ├── plugin-2/
    └── ...
```

### Plugin Lifecycle

1. **Discovery**: PluginManager scans `plugins/` for directories with `manifest.json`
2. **Loading**: Plugin module is imported and class is instantiated
3. **Configuration**: Plugin config is loaded from `config/config.json`
4. **Validation**: `validate_config()` is called to verify configuration
5. **Registration**: Plugin is added to available display modes
6. **Execution**: `update()` is called periodically, `display()` is called during rotation

---

## Creating a New Plugin

### Method 1: Using dev_plugin_setup.sh (Recommended)

This method is best for plugins stored in separate Git repositories.

#### From GitHub Repository

```bash
# Link a plugin from GitHub (auto-detects URL)
./dev_plugin_setup.sh link-github <plugin-name>

# Example: Link hockey-scoreboard plugin
./dev_plugin_setup.sh link-github hockey-scoreboard

# With custom URL
./dev_plugin_setup.sh link-github <plugin-name> https://github.com/user/repo.git
```

The script will:
- Clone the repository to `~/.ledmatrix-dev-plugins/` (or configured directory)
- Create a symlink in `plugins/<plugin-name>/` pointing to the cloned repo
- Validate the plugin structure

#### From Local Repository

```bash
# Link a local plugin repository
./dev_plugin_setup.sh link <plugin-name> <path-to-repo>

# Example: Link a local plugin
./dev_plugin_setup.sh link my-plugin ../ledmatrix-my-plugin
```

### Method 2: Manual Plugin Creation

1. **Create Plugin Directory**

```bash
mkdir -p plugins/my-plugin
cd plugins/my-plugin
```

2. **Create manifest.json**

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "author": "Your Name",
  "description": "Description of what this plugin does",
  "entry_point": "manager.py",
  "class_name": "MyPlugin",
  "category": "custom",
  "tags": ["custom", "example"],
  "display_modes": ["my_plugin"],
  "update_interval": 60,
  "default_duration": 15,
  "requires": {
    "python": ">=3.9"
  },
  "config_schema": "config_schema.json"
}
```

3. **Create manager.py**

```python
from src.plugin_system.base_plugin import BasePlugin
from PIL import Image
import logging

class MyPlugin(BasePlugin):
    """My custom plugin implementation."""
    
    def update(self):
        """Fetch/update data for this plugin."""
        # Fetch data from API, files, etc.
        # Use self.cache_manager for caching
        cache_key = f"{self.plugin_id}_data"
        cached = self.cache_manager.get(cache_key, max_age=3600)
        if cached:
            self.data = cached
            return
        
        # Fetch new data
        self.data = self._fetch_data()
        self.cache_manager.set(cache_key, self.data)
    
    def display(self, force_clear=False):
        """Render this plugin's display."""
        if force_clear:
            self.display_manager.clear()
        
        # Render content using display_manager
        self.display_manager.draw_text(
            "Hello, World!",
            x=10, y=15,
            color=(255, 255, 255)
        )
        
        self.display_manager.update_display()
    
    def _fetch_data(self):
        """Fetch data from external source."""
        # Implement your data fetching logic
        return {"message": "Hello, World!"}
    
    def validate_config(self):
        """Validate plugin configuration."""
        # Check required config fields
        if not super().validate_config():
            return False
        
        # Add custom validation
        required_fields = ['api_key']  # Example
        for field in required_fields:
            if field not in self.config:
                self.logger.error(f"Missing required field: {field}")
                return False
        
        return True
```

4. **Create config_schema.json**

```json
{
  "type": "object",
  "properties": {
    "enabled": {
      "type": "boolean",
      "default": true,
      "description": "Enable or disable this plugin"
    },
    "display_duration": {
      "type": "number",
      "default": 15,
      "minimum": 1,
      "description": "How long to display this plugin (seconds)"
    },
    "api_key": {
      "type": "string",
      "description": "API key for external service"
    }
  },
  "required": ["enabled"]
}
```

5. **Create requirements.txt** (if needed)

```
requests>=2.28.0
pillow>=9.0.0
```

6. **Create README.md**

Document your plugin's functionality, configuration options, and usage.

---

## Running Plugins

### Development Mode (Emulator)

Run the LEDMatrix system with emulator for plugin testing:

```bash
# Using run.py
python run.py --emulator

# Using emulator script
./run_emulator.sh
```

The emulator will:
- Load all enabled plugins
- Display plugin content in a window (simulating LED matrix)
- Show logs for plugin loading and execution
- Allow testing without Raspberry Pi hardware

### Production Mode (Raspberry Pi)

Run on actual Raspberry Pi hardware:

```bash
# Direct execution
python run.py

# As systemd service
sudo systemctl start ledmatrix
sudo systemctl status ledmatrix
sudo journalctl -u ledmatrix -f  # View logs
```

### Plugin-Specific Testing

Test individual plugin loading:

```python
# test_my_plugin.py
from src.plugin_system.plugin_manager import PluginManager
from src.config_manager import ConfigManager
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager

# Initialize managers
config_manager = ConfigManager()
config = config_manager.load_config()
display_manager = DisplayManager(config)
cache_manager = CacheManager()

# Initialize plugin manager
plugin_manager = PluginManager(
    plugins_dir="plugins",
    config_manager=config_manager,
    display_manager=display_manager,
    cache_manager=cache_manager
)

# Discover and load plugin
plugins = plugin_manager.discover_plugins()
print(f"Discovered plugins: {plugins}")

if "my-plugin" in plugins:
    if plugin_manager.load_plugin("my-plugin"):
        plugin = plugin_manager.get_plugin("my-plugin")
        plugin.update()
        plugin.display()
        print("Plugin loaded and displayed successfully!")
    else:
        print("Failed to load plugin")
```

---

## Loading Plugins

### Enabling Plugins

Plugins are enabled/disabled in `config/config.json`:

```json
{
  "my-plugin": {
    "enabled": true,
    "display_duration": 15,
    "api_key": "your-api-key-here"
  }
}
```

### Plugin Configuration Structure

Each plugin has its own section in `config/config.json`:

```json
{
  "<plugin-id>": {
    "enabled": true,                    // Enable/disable plugin
    "display_duration": 15,             // Display duration in seconds
    "live_priority": false,             // Enable live priority takeover
    "high_performance_transitions": false, // Use 120 FPS transitions
    "transition": {                     // Transition configuration
      "type": "redraw",                 // Transition type
      "speed": 2,                       // Transition speed
      "enabled": true                   // Enable transitions
    },
    // ... plugin-specific configuration
  }
}
```

### Secrets Management

Store sensitive data (API keys, tokens) in `config/config_secrets.json`:

```json
{
  "my-plugin": {
    "api_key": "secret-api-key-here"
  }
}
```

Reference secrets in main config:

```json
{
  "my-plugin": {
    "enabled": true,
    "config_secrets": {
      "api_key": "my-plugin.api_key"
    }
  }
}
```

### Plugin Discovery

Plugins are automatically discovered when:
- Directory exists in `plugins/`
- Directory contains `manifest.json`
- Manifest has required fields (`id`, `entry_point`, `class_name`)

Check discovered plugins:

```bash
# Using dev_plugin_setup.sh
./dev_plugin_setup.sh list

# Output shows:
# ✓ plugin-name (symlink)
#   → /path/to/repo
#   ✓ Git repo is clean (branch: main)
```

### Plugin Status

Check plugin status and git information:

```bash
./dev_plugin_setup.sh status

# Output shows:
# ✓ plugin-name
#   Path: /path/to/repo
#   Branch: main
#   Remote: https://github.com/user/repo.git
#   Status: Clean and up to date
```

---

## Plugin Development Workflow

### 1. Initial Setup

```bash
# Create or clone plugin repository
git clone https://github.com/user/ledmatrix-my-plugin.git
cd ledmatrix-my-plugin

# Link to LEDMatrix project
cd /path/to/LEDMatrix
./dev_plugin_setup.sh link my-plugin ../ledmatrix-my-plugin
```

### 2. Development Cycle

1. **Edit plugin code** in linked repository
2. **Test with emulator**: `python run.py --emulator`
3. **Check logs** for errors or warnings
4. **Update configuration** in `config/config.json` if needed
5. **Iterate** until plugin works correctly

### 3. Testing on Hardware

```bash
# Deploy to Raspberry Pi
rsync -avz plugins/my-plugin/ pi@raspberrypi:/path/to/LEDMatrix/plugins/my-plugin/

# Or if using git, pull on Pi
ssh pi@raspberrypi "cd /path/to/LEDMatrix/plugins/my-plugin && git pull"

# Restart service
ssh pi@raspberrypi "sudo systemctl restart ledmatrix"
```

### 4. Updating Plugins

```bash
# Update single plugin from git
./dev_plugin_setup.sh update my-plugin

# Update all linked plugins
./dev_plugin_setup.sh update
```

### 5. Unlinking Plugins

```bash
# Remove symlink (preserves repository)
./dev_plugin_setup.sh unlink my-plugin
```

---

## Testing Plugins

### Unit Testing

Create test files in plugin directory:

```python
# plugins/my-plugin/test_my_plugin.py
import unittest
from unittest.mock import Mock, MagicMock
from manager import MyPlugin

class TestMyPlugin(unittest.TestCase):
    def setUp(self):
        self.config = {"enabled": True}
        self.display_manager = Mock()
        self.cache_manager = Mock()
        self.plugin_manager = Mock()
        
        self.plugin = MyPlugin(
            plugin_id="my-plugin",
            config=self.config,
            display_manager=self.display_manager,
            cache_manager=self.cache_manager,
            plugin_manager=self.plugin_manager
        )
    
    def test_plugin_initialization(self):
        self.assertEqual(self.plugin.plugin_id, "my-plugin")
        self.assertTrue(self.plugin.enabled)
    
    def test_config_validation(self):
        self.assertTrue(self.plugin.validate_config())
    
    def test_update(self):
        self.cache_manager.get.return_value = None
        self.plugin.update()
        # Assert data was fetched and cached
    
    def test_display(self):
        self.plugin.display()
        self.display_manager.draw_text.assert_called()
        self.display_manager.update_display.assert_called()

if __name__ == '__main__':
    unittest.main()
```

Run tests:

```bash
cd plugins/my-plugin
python -m pytest test_my_plugin.py
# or
python test_my_plugin.py
```

### Integration Testing

Test plugin with actual managers:

```python
# test_plugin_integration.py
from src.plugin_system.plugin_manager import PluginManager
from src.config_manager import ConfigManager
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager

def test_plugin_loading():
    config_manager = ConfigManager()
    config = config_manager.load_config()
    display_manager = DisplayManager(config)
    cache_manager = CacheManager()
    
    plugin_manager = PluginManager(
        plugins_dir="plugins",
        config_manager=config_manager,
        display_manager=display_manager,
        cache_manager=cache_manager
    )
    
    plugins = plugin_manager.discover_plugins()
    assert "my-plugin" in plugins
    
    assert plugin_manager.load_plugin("my-plugin")
    plugin = plugin_manager.get_plugin("my-plugin")
    assert plugin is not None
    assert plugin.enabled
    
    plugin.update()
    plugin.display()
```

### Emulator Testing

Test plugin rendering visually:

```bash
# Run with emulator
python run.py --emulator

# Plugin should appear in display rotation
# Check logs for plugin loading and execution
```

### Hardware Testing

1. Deploy plugin to Raspberry Pi
2. Enable in `config/config.json`
3. Restart LEDMatrix service
4. Observe LED matrix display
5. Check logs: `journalctl -u ledmatrix -f`

---

## Troubleshooting

### Plugin Not Loading

**Symptoms**: Plugin doesn't appear in available modes, no logs about plugin

**Solutions**:
1. Check plugin directory exists: `ls plugins/my-plugin/`
2. Verify `manifest.json` exists and is valid JSON
3. Check manifest has required fields: `id`, `entry_point`, `class_name`
4. Verify entry_point file exists: `ls plugins/my-plugin/manager.py`
5. Check class name matches: `grep "class.*Plugin" plugins/my-plugin/manager.py`
6. Review logs for import errors

### Plugin Loading but Not Displaying

**Symptoms**: Plugin loads successfully but doesn't appear in rotation

**Solutions**:
1. Check plugin is enabled: `config/config.json` has `"enabled": true`
2. Verify display_modes in manifest match config
3. Check plugin is in rotation schedule
4. Review `display()` method for errors
5. Check logs for runtime errors

### Configuration Errors

**Symptoms**: Plugin fails to load, validation errors in logs

**Solutions**:
1. Validate config against `config_schema.json`
2. Check required fields are present
3. Verify data types match schema
4. Check for typos in config keys
5. Review `validate_config()` method

### Import Errors

**Symptoms**: ModuleNotFoundError or ImportError in logs

**Solutions**:
1. Install plugin dependencies: `pip install -r plugins/my-plugin/requirements.txt`
2. Check Python path includes plugin directory
3. Verify relative imports are correct
4. Check for circular import issues
5. Ensure all dependencies are in requirements.txt

### Display Issues

**Symptoms**: Plugin renders incorrectly or not at all

**Solutions**:
1. Check display dimensions: `display_manager.width`, `display_manager.height`
2. Verify coordinates are within display bounds
3. Check color values are valid (0-255)
4. Ensure `update_display()` is called after rendering
5. Test with emulator first to debug rendering

### Performance Issues

**Symptoms**: Slow display updates, high CPU usage

**Solutions**:
1. Use `cache_manager` to avoid excessive API calls
2. Implement background data fetching
3. Optimize rendering code
4. Consider using `high_performance_transitions`
5. Profile plugin code to identify bottlenecks

### Git/Symlink Issues

**Symptoms**: Plugin changes not appearing, broken symlinks

**Solutions**:
1. Check symlink: `ls -la plugins/my-plugin`
2. Verify target exists: `readlink -f plugins/my-plugin`
3. Update plugin: `./dev_plugin_setup.sh update my-plugin`
4. Re-link plugin if needed: `./dev_plugin_setup.sh unlink my-plugin && ./dev_plugin_setup.sh link my-plugin <path>`
5. Check git status: `cd plugins/my-plugin && git status`

---

## Best Practices

### Code Organization

- Keep plugin code in `plugins/<plugin-id>/` directory
- Use descriptive class and method names
- Follow existing plugin patterns
- Place shared utilities in `src/common/` if reusable

### Configuration

- Always use `config_schema.json` for validation
- Store secrets in `config_secrets.json`
- Provide sensible defaults
- Document all configuration options in README

### Error Handling

- Use plugin logger for all logging
- Handle API failures gracefully
- Provide fallback displays when data unavailable
- Cache data to avoid excessive requests

### Performance

- Cache API responses appropriately
- Use background data fetching for long operations
- Optimize rendering for Pi's limited resources
- Test performance on actual hardware

### Testing

- Write unit tests for core logic
- Test with emulator before hardware
- Test on Raspberry Pi before deploying
- Test with other plugins enabled

### Documentation

- Document plugin functionality in README
- Include configuration examples
- Document API requirements and rate limits
- Provide usage examples

---

## Resources

- **Plugin System Documentation**: `docs/PLUGIN_ARCHITECTURE_SPEC.md`
- **Base Plugin Class**: `src/plugin_system/base_plugin.py`
- **Plugin Manager**: `src/plugin_system/plugin_manager.py`
- **Example Plugins**: 
  - `plugins/hockey-scoreboard/` - Sports scoreboard example
  - `plugins/football-scoreboard/` - Complex multi-league example
  - `plugins/ledmatrix-music/` - Real-time data example
- **Development Setup**: `dev_plugin_setup.sh`
- **Example Config**: `dev_plugins.json.example`

---

## Quick Reference

### Common Commands

```bash
# Link plugin from GitHub
./dev_plugin_setup.sh link-github <name>

# Link local plugin
./dev_plugin_setup.sh link <name> <path>

# List all plugins
./dev_plugin_setup.sh list

# Check plugin status
./dev_plugin_setup.sh status

# Update plugin(s)
./dev_plugin_setup.sh update [name]

# Unlink plugin
./dev_plugin_setup.sh unlink <name>

# Run with emulator
python run.py --emulator

# Run on Pi
python run.py
```

### Plugin File Structure

```
plugins/my-plugin/
├── manifest.json          # Required: Plugin metadata
├── manager.py             # Required: Plugin class
├── config_schema.json     # Required: Config validation
├── requirements.txt       # Optional: Dependencies
├── README.md              # Optional: Documentation
└── ...                    # Plugin-specific files
```

### Required Manifest Fields

- `id`: Plugin identifier
- `entry_point`: Python file (usually "manager.py")
- `class_name`: Plugin class name
- `display_modes`: Array of mode names

