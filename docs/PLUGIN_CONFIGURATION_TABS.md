# Plugin Configuration Tabs

## Overview

Each installed plugin now gets its own dedicated configuration tab in the web interface. This provides a clean, organized way to configure plugins without cluttering the main Plugins management tab.

## Features

- **Automatic Tab Generation**: When a plugin is installed, a new tab is automatically created in the web UI
- **JSON Schema-Based Forms**: Configuration forms are automatically generated based on each plugin's `config_schema.json`
- **Type-Safe Inputs**: Form inputs are created based on the JSON Schema type (boolean, number, string, array, enum)
- **Default Values**: All fields show current values or fallback to schema defaults
- **Reset Functionality**: Users can reset all settings to defaults with one click
- **Real-Time Validation**: Input constraints from JSON Schema are enforced (min, max, maxLength, etc.)

## User Experience

### Accessing Plugin Configuration

1. Navigate to the **Plugins** tab to see all installed plugins
2. Click the **Configure** button on any plugin card
3. You'll be automatically taken to that plugin's configuration tab
4. Alternatively, click directly on the plugin's tab button (marked with a puzzle piece icon)

### Configuring a Plugin

1. Open the plugin's configuration tab
2. Modify settings using the generated form
3. Click **Save Configuration**
4. Restart the display service to apply changes

### Plugin Management vs Configuration

- **Plugins Tab**: Used for plugin management (install, enable/disable, update, uninstall)
- **Plugin-Specific Tabs**: Used for configuring plugin behavior and settings

## For Plugin Developers

### Requirements

To enable automatic configuration tab generation, your plugin must:

1. Include a `config_schema.json` file
2. Reference it in your `manifest.json`:

```json
{
  "id": "your-plugin",
  "name": "Your Plugin",
  "icon": "fas fa-star",  // Optional: Custom tab icon
  ...
  "config_schema": "config_schema.json"
}
```

**Note:** You can optionally specify a custom `icon` for your plugin tab. See [Plugin Custom Icons Guide](PLUGIN_CUSTOM_ICONS.md) for details.

### Supported JSON Schema Types

The form generator supports the following JSON Schema types:

#### Boolean

```json
{
  "type": "boolean",
  "default": true,
  "description": "Enable or disable this feature"
}
```

Renders as: Toggle switch

#### Number / Integer

```json
{
  "type": "integer",
  "default": 60,
  "minimum": 1,
  "maximum": 300,
  "description": "Update interval in seconds"
}
```

Renders as: Number input with min/max constraints

#### String

```json
{
  "type": "string",
  "default": "Hello, World!",
  "minLength": 1,
  "maxLength": 50,
  "description": "The message to display"
}
```

Renders as: Text input with length constraints

#### Array

```json
{
  "type": "array",
  "items": {
    "type": "integer",
    "minimum": 0,
    "maximum": 255
  },
  "minItems": 3,
  "maxItems": 3,
  "default": [255, 255, 255],
  "description": "RGB color [R, G, B]"
}
```

Renders as: Text input (comma-separated values)  
Example input: `255, 128, 0`

#### Enum (Select)

```json
{
  "type": "string",
  "enum": ["small", "medium", "large"],
  "default": "medium",
  "description": "Display size"
}
```

Renders as: Dropdown select

### Example config_schema.json

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "title": "My Plugin Configuration",
  "description": "Configure my awesome plugin",
  "properties": {
    "enabled": {
      "type": "boolean",
      "default": true,
      "description": "Enable or disable this plugin"
    },
    "message": {
      "type": "string",
      "default": "Hello!",
      "minLength": 1,
      "maxLength": 50,
      "description": "The message to display"
    },
    "update_interval": {
      "type": "integer",
      "default": 60,
      "minimum": 1,
      "maximum": 3600,
      "description": "Update interval in seconds"
    },
    "color": {
      "type": "array",
      "items": {
        "type": "integer",
        "minimum": 0,
        "maximum": 255
      },
      "minItems": 3,
      "maxItems": 3,
      "default": [255, 255, 255],
      "description": "RGB color [R, G, B]"
    },
    "mode": {
      "type": "string",
      "enum": ["scroll", "static", "fade"],
      "default": "scroll",
      "description": "Display mode"
    }
  },
  "required": ["enabled"],
  "additionalProperties": false
}
```

### Best Practices

1. **Use Descriptive Labels**: The `description` field is shown as help text under each input
2. **Set Sensible Defaults**: Always provide default values that work out of the box
3. **Use Constraints**: Leverage min/max, minLength/maxLength to guide users
4. **Mark Required Fields**: Use the `required` array in your schema
5. **Organize Properties**: List properties in order of importance

### Form Generation Process

1. Web UI loads installed plugins via `/api/plugins/installed`
2. For each plugin, the backend loads its `config_schema.json`
3. Frontend generates a tab button with plugin name
4. Frontend generates a form based on the JSON Schema
5. Current config values from `config.json` are populated
6. When saved, each field is sent to `/api/plugins/config` endpoint

## Implementation Details

### Backend Changes

**File**: `web_interface_v2.py`

- Modified `/api/plugins/installed` endpoint to include `config_schema_data`
- Loads each plugin's `config_schema.json` if it exists
- Returns schema data along with plugin info

### Frontend Changes

**File**: `templates/index_v2.html`

New Functions:
- `generatePluginTabs(plugins)` - Creates tab buttons and content for each plugin
- `generatePluginConfigForm(plugin)` - Generates HTML form from JSON Schema
- `savePluginConfiguration(pluginId)` - Saves form data to backend
- `resetPluginConfig(pluginId)` - Resets all settings to defaults
- `configurePlugin(pluginId)` - Navigates to plugin's tab

### Data Flow

```
Page Load
  → refreshPlugins()
    → /api/plugins/installed
      → Returns plugins with config_schema_data
    → generatePluginTabs()
      → Creates tab buttons
      → Creates tab content
        → generatePluginConfigForm()
          → Reads JSON Schema
          → Creates form inputs
          → Populates current values

User Saves
  → savePluginConfiguration()
    → Reads form data
    → Converts types per schema
    → Sends to /api/plugins/config
      → Updates config.json
    → Shows success notification
```

## Troubleshooting

### Plugin Tab Not Appearing

- Ensure `config_schema.json` exists in plugin directory
- Verify `config_schema` field in `manifest.json`
- Check browser console for errors
- Try refreshing plugins (Plugins tab → Refresh button)

### Form Not Generating Correctly

- Validate your `config_schema.json` against JSON Schema Draft 07
- Check that all properties have a `type` field
- Ensure `default` values match the specified type
- Look for JavaScript errors in browser console

### Configuration Not Saving

- Ensure the plugin is properly installed
- Check that config keys match schema properties
- Verify backend API is accessible
- Check browser network tab for API errors
- Ensure display service is restarted after config changes

## Migration Guide

### For Existing Plugins

If your plugin already has a `config_schema.json`:

1. No changes needed! The tab will be automatically generated.
2. Test the generated form to ensure all fields render correctly.
3. Consider adding more descriptive `description` fields.

If your plugin doesn't have a config schema:

1. Create `config_schema.json` based on your current config structure
2. Add descriptions for each property
3. Set appropriate defaults
4. Add validation constraints (min, max, etc.)
5. Reference the schema in your `manifest.json`

### Backward Compatibility

- Plugins without `config_schema.json` still work normally
- They simply won't have a configuration tab
- Users can still edit config via the Raw JSON editor
- The Configure button will navigate to a tab with a friendly message

## Future Enhancements

Potential improvements for future versions:

- **Advanced Schema Features**: Support for nested objects, conditional fields
- **Visual Validation**: Real-time validation feedback as user types
- **Color Pickers**: Special input for RGB/color array types
- **File Uploads**: Support for image/asset uploads
- **Import/Export**: Save and share plugin configurations
- **Presets**: Quick-switch between saved configurations
- **Documentation Links**: Link schema fields to plugin documentation

## Example Plugins

See these plugins for examples of config schemas:

- `hello-world`: Simple plugin with basic types
- `clock-simple`: Plugin with enum and number types

## Support

For questions or issues:
- Check the main LEDMatrix wiki
- Review plugin documentation
- Open an issue on GitHub
- Join the community Discord

