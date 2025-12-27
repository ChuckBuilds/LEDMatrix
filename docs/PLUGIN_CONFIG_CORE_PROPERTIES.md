# Core Plugin Properties

## Overview

The LEDMatrix plugin system automatically manages certain core properties that are common to all plugins. These properties are handled by the system and don't need to be explicitly defined in plugin schemas.

## Core Properties

The following properties are automatically managed by the system:

1. **`enabled`** (boolean)
   - Default: `true`
   - Description: Enable or disable the plugin
   - System-managed by PluginManager

2. **`display_duration`** (number)
   - Default: `15`
   - Range: 1-300 seconds
   - Description: How long to display the plugin in seconds
   - Can be overridden per-plugin

3. **`live_priority`** (boolean)
   - Default: `false`
   - Description: Enable live priority takeover when plugin has live content
   - Used by DisplayController for priority scheduling

## How Core Properties Work

### Schema Validation

During configuration validation:

1. **Automatic Injection**: Core properties are automatically injected into the validation schema if they're not already defined in the plugin's `config_schema.json`
2. **Removed from Required**: Core properties are automatically removed from the `required` array during validation, since they're system-managed
3. **Default Values Applied**: If core properties are missing from a config, defaults are applied automatically:
   - `enabled`: `true` (matches `BasePlugin.__init__`)
   - `display_duration`: `15` (matches `BasePlugin.get_display_duration()`)
   - `live_priority`: `false` (matches `BasePlugin.has_live_priority()`)

### Plugin Schema Files

Plugin schemas can optionally include these properties for documentation purposes, but they're not required:

```json
{
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
      "maximum": 300,
      "description": "Display duration in seconds"
    },
    "live_priority": {
      "type": "boolean",
      "default": false,
      "description": "Enable live priority takeover"
    }
  },
  "required": []  // Core properties should NOT be in required array
}
```

**Important**: Even if you include core properties in your schema, they should **NOT** be listed in the `required` array, as the system will automatically remove them during validation.

### Configuration Files

Core properties are stored in the main `config/config.json` file:

```json
{
  "my-plugin": {
    "enabled": true,
    "display_duration": 20,
    "live_priority": false,
    "plugin_specific_setting": "value"
  }
}
```

## Implementation Details

### SchemaManager

The `SchemaManager.validate_config_against_schema()` method:

1. Injects core properties into the schema `properties` if not present
2. Removes core properties from the `required` array
3. Validates the config against the enhanced schema
4. Applies defaults for missing core properties

### Default Merging

When generating default configurations or merging with defaults:

- Core properties get their system defaults if not in the schema
- User-provided values override system defaults
- Missing core properties are filled in automatically

## Best Practices

1. **Don't require core properties**: Never include `enabled`, `display_duration`, or `live_priority` in your schema's `required` array
2. **Optional inclusion**: You can include core properties in your schema for documentation, but it's optional
3. **Use system defaults**: Rely on system defaults unless your plugin needs specific values
4. **Document if included**: If you include core properties in your schema, use the same defaults as the system to avoid confusion

## Troubleshooting

### "Missing required property 'enabled'" Error

This error should not occur with the current implementation. If you see it:

1. Check that your schema doesn't have `enabled` in the `required` array
2. Ensure you're using the latest version of `SchemaManager`
3. Verify the schema is being loaded correctly

### Core Properties Not Working

If core properties aren't being applied:

1. Check that defaults are being merged (see `save_plugin_config()`)
2. Verify the schema manager is injecting core properties
3. Check plugin initialization to ensure defaults are applied


