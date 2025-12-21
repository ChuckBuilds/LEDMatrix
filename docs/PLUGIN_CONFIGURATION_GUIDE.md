# Plugin Configuration Guide

## Overview

The LEDMatrix system uses a plugin-based architecture where each plugin manages its own configuration. This guide explains the configuration structure, how to configure plugins via the web interface, and advanced configuration options.

## Quick Start

1. **Install a plugin** from the Plugin Store in the web interface
2. **Navigate to the plugin's configuration tab** (automatically created when installed)
3. **Configure settings** using the auto-generated form
4. **Save configuration** and restart the display service

For detailed information, see the sections below.

## Configuration Structure

### Core System Configuration

The main configuration file (`config/config.json`) now contains only essential system settings:

```json
{
    "web_display_autostart": true,
    "schedule": {
        "enabled": true,
        "start_time": "07:00",
        "end_time": "23:00"
    },
    "timezone": "America/Chicago",
    "location": {
        "city": "Dallas",
        "state": "Texas",
        "country": "US"
    },
    "display": {
        "hardware": {
            "rows": 32,
            "cols": 64,
            "chain_length": 2,
            "parallel": 1,
            "brightness": 90,
            "hardware_mapping": "adafruit-hat",
            "scan_mode": 0,
            "pwm_bits": 9,
            "pwm_dither_bits": 1,
            "pwm_lsb_nanoseconds": 130,
            "disable_hardware_pulsing": false,
            "inverse_colors": false,
            "show_refresh_rate": false,
            "limit_refresh_rate_hz": 100
        },
        "runtime": {
            "gpio_slowdown": 3
        },
        "display_durations": {
            "calendar": 30
        },
        "use_short_date_format": true
    },
    "calendar": {
        "enabled": false,
        "update_interval": 3600,
        "max_events": 5,
        "show_all_day": true,
        "date_format": "%m/%d",
        "time_format": "%I:%M %p"
    },
    "plugin_system": {
        "plugins_directory": "plugin-repos",
        "auto_discover": true,
        "auto_load_enabled": true
    }
}
```

### Configuration Sections

#### 1. System Settings
- **web_display_autostart**: Enable web interface auto-start
- **schedule**: Display schedule settings
- **timezone**: System timezone
- **location**: Default location for location-based plugins

#### 2. Display Hardware
- **hardware**: LED matrix hardware configuration
- **runtime**: Runtime display settings
- **display_durations**: How long each display mode shows (in seconds)
- **use_short_date_format**: Use short date format

#### 3. Core Components
- **calendar**: Calendar manager settings (core system component)

#### 4. Plugin System
- **plugin_system**: Plugin system configuration
  - **plugins_directory**: Directory where plugins are stored
  - **auto_discover**: Automatically discover plugins
  - **auto_load_enabled**: Automatically load enabled plugins

## Plugin Configuration

### Plugin Discovery

Plugins are automatically discovered from the `plugin-repos` directory. Each plugin should have:
- `manifest.json`: Plugin metadata and configuration schema
- `manager.py`: Plugin implementation
- `requirements.txt`: Plugin dependencies

### Plugin Configuration in config.json

Plugins are configured by adding their plugin ID as a top-level key in the config:

```json
{
    "weather": {
        "enabled": true,
        "api_key": "your_api_key",
        "update_interval": 1800,
        "units": "imperial"
    },
    "stocks": {
        "enabled": true,
        "symbols": ["AAPL", "GOOGL", "MSFT"],
        "update_interval": 600
    }
}
```

### Plugin Display Durations

Add plugin display modes to the `display_durations` section:

```json
{
    "display": {
        "display_durations": {
            "calendar": 30,
            "weather": 30,
            "weather_forecast": 30,
            "stocks": 30,
            "stock_news": 20
        }
    }
}
```

## Migration from Old Configuration

### Removed Sections

The following configuration sections have been removed as they are now handled by plugins:

- All sports manager configurations (NHL, NBA, NFL, etc.)
- Weather manager configuration
- Stock manager configuration
- News manager configuration
- Music manager configuration
- All other content manager configurations

### What Remains

Only core system components remain in the main configuration:
- Display hardware settings
- Schedule settings
- Calendar manager (core component)
- Plugin system settings

## Plugin Development

### Plugin Structure

Each plugin should follow this structure:

```
plugin-repos/
└── my-plugin/
    ├── manifest.json
    ├── manager.py
    ├── requirements.txt
    └── README.md
```

### Plugin Manifest

```json
{
    "name": "My Plugin",
    "version": "1.0.0",
    "description": "Plugin description",
    "author": "Your Name",
    "display_modes": ["my_plugin"],
    "config_schema": {
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean", "default": false},
            "update_interval": {"type": "integer", "default": 3600}
        }
    }
}
```

### Plugin Manager Class

```python
from src.plugin_system.base_plugin import BasePlugin

class MyPluginManager(BasePlugin):
    def __init__(self, config, display_manager, cache_manager, font_manager):
        super().__init__(config, display_manager, cache_manager, font_manager)
        self.enabled = config.get('enabled', False)
    
    def update(self):
        """Update plugin data"""
        pass
    
    def display(self, force_clear=False):
        """Display plugin content"""
        pass
    
    def get_duration(self):
        """Get display duration for this plugin"""
        return self.config.get('duration', 30)
```

### Dynamic Duration Configuration

Plugins that render multi-step content (scrolling leaderboards, tickers, etc.) can opt-in to dynamic durations so the display controller waits for a full cycle.

```json
{
    "football-scoreboard": {
        "enabled": true,
        "dynamic_duration": {
            "enabled": true,
            "max_duration_seconds": 240
        }
    },
    "display": {
        "dynamic_duration": {
            "max_duration_seconds": 180
        }
    }
}
```

- Set `dynamic_duration.enabled` per plugin to toggle the behaviour.
- Optional `dynamic_duration.max_duration_seconds` on the plugin overrides the global cap (defined under `display.dynamic_duration.max_duration_seconds`, default 180s).
- Plugins should override `supports_dynamic_duration()`, `is_cycle_complete()`, and `reset_cycle_state()` (see `BasePlugin`) to control when a cycle completes.

## Configuration Tabs

Each installed plugin automatically gets its own dedicated configuration tab in the web interface. This provides a clean, organized way to configure plugins.

### Accessing Plugin Configuration

1. Navigate to the **Plugins** tab to see all installed plugins
2. Click the **Configure** button on any plugin card, or
3. Click directly on the plugin's tab button in the navigation bar

### Auto-Generated Forms

Configuration forms are automatically generated from each plugin's `config_schema.json`:

- **Boolean** → Toggle switch
- **Number/Integer** → Number input with min/max validation
- **String** → Text input with length constraints
- **Array** → Comma-separated input
- **Enum** → Dropdown menu

### Configuration Features

- **Type-safe inputs**: Form inputs match JSON Schema types
- **Default values**: Fields show current values or schema defaults
- **Real-time validation**: Input constraints enforced (min, max, maxLength, etc.)
- **Reset to defaults**: One-click reset to restore original settings
- **Help text**: Each field shows description from schema

For more details, see [Plugin Configuration Tabs](PLUGIN_CONFIGURATION_TABS.md).

## Schema Validation

The configuration system uses JSON Schema Draft-07 for validation:

- **Pre-save validation**: Invalid configurations are rejected before saving
- **Automatic defaults**: Default values extracted from schemas
- **Error messages**: Clear error messages show exactly what's wrong
- **Reliable loading**: Schema loading with caching and fallback paths

### Schema Structure

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "enabled": {
      "type": "boolean",
      "default": true,
      "description": "Enable or disable this plugin"
    },
    "update_interval": {
      "type": "integer",
      "default": 3600,
      "minimum": 60,
      "maximum": 86400,
      "description": "Update interval in seconds"
    }
  }
}
```

## Best Practices

1. **Keep main config minimal**: Only include core system settings
2. **Use plugin-specific configs**: Each plugin manages its own configuration
3. **Document plugin requirements**: Include clear documentation for each plugin
4. **Version control**: Keep plugin configurations in version control
5. **Testing**: Test plugins in emulator mode before hardware deployment
6. **Use schemas**: Always provide `config_schema.json` for your plugins
7. **Sensible defaults**: Ensure defaults work without additional configuration
8. **Add descriptions**: Help users understand each setting

## Troubleshooting

### Common Issues

1. **Plugin not loading**: Check plugin manifest and directory structure
2. **Configuration errors**: Validate plugin configuration against schema
3. **Display issues**: Check display durations and plugin display methods
4. **Performance**: Monitor plugin update intervals and resource usage
5. **Tab not showing**: Verify `config_schema.json` exists and is referenced in manifest
6. **Settings not saving**: Check validation errors and ensure all required fields are filled

### Debug Mode

Enable debug logging to troubleshoot plugin issues:

```json
{
    "plugin_system": {
        "debug": true,
        "log_level": "debug"
    }
}
```

## See Also

- [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md) - Complete development guide
- [Plugin Configuration Tabs](PLUGIN_CONFIGURATION_TABS.md) - Configuration tabs feature
- [Plugin API Reference](PLUGIN_API_REFERENCE.md) - API documentation
- [Main README](../README.md) - Project overview
