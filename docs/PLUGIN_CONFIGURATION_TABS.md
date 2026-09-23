# Plugin Configuration Tabs

## Overview

Each installed plugin now gets its own dedicated configuration tab in the web interface. This provides a clean, organized way to configure plugins without cluttering the **Plugin Manager** tab.

## Features

- **Automatic Tab Generation**: When a plugin is installed, a new tab is automatically created in the web UI
- **JSON Schema-Based Forms**: Configuration forms are automatically generated based on each plugin's `config_schema.json`
- **Type-Safe Inputs**: Form inputs are created based on the JSON Schema type (boolean, number, string, array, enum)
- **Default Values**: All fields show current values or fallback to schema defaults
- **Real-Time Validation**: Input constraints from JSON Schema are enforced (min, max, maxLength, etc.)

## User Experience

### Accessing Plugin Configuration

1. Navigate to the **Plugin Manager** tab to see all installed plugins
2. Click the **Configure** button on any plugin card
3. You'll be automatically taken to that plugin's configuration tab
4. Alternatively, click directly on the plugin's tab button in the second nav row

### Configuring a Plugin

1. Open the plugin's configuration tab
2. Modify settings using the generated form
3. Click **Save Configuration**. The settings apply to the running display
   without a restart: the display service reloads `config.json` when it
   changes and calls the plugin's `on_config_change()`

The tab also has **Refresh** (reload the form), **Update** (update the
plugin) and **Uninstall** buttons.

### Plugin Manager vs Per-Plugin Configuration

- **Plugin Manager tab** (second nav row): used for browsing the
  Plugin Store, installing plugins, toggling installed plugins on/off,
  and updating/uninstalling them
- **Per-plugin tabs** (one per installed plugin, also in the second
  nav row): used for configuring that specific plugin's behavior and
  settings via a form auto-generated from its `config_schema.json`

## For Plugin Developers

### Requirements

Every installed plugin gets a tab. To get a generated form in it, include a
`config_schema.json` file in the plugin's directory. The name is fixed: the
web interface finds the schema by that file name (`SchemaManager` in
`src/plugin_system/schema_manager.py`), and no manifest field points to it.

**Note:** You can optionally specify a Font Awesome `icon` class for your
plugin tab in `manifest.json`. See [Plugin Custom Icons Guide](PLUGIN_CUSTOM_ICONS.md) for details.

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

Forms are rendered on the server, not generated in the browser:

1. The web UI loads installed plugins via `/api/v3/plugins/installed` and adds
   a tab button for each one
2. Opening a tab loads `/v3/partials/plugin-config/<plugin_id>`
   (`web_interface/blueprints/pages_v3.py`), which loads the plugin's schema
   through `SchemaManager` and its current values from `config.json`
3. `web_interface/templates/v3/partials/plugin_config.html` renders the form
   from the schema (widgets named by `x-widget` are rendered by the scripts in
   `web_interface/static/v3/js/widgets/`)
4. **Save Configuration** posts the form to `/api/v3/plugins/config`
   (`web_interface/blueprints/api_v3/plugins.py`), which validates it against
   the schema, writes `config.json` (secret fields go to
   `config_secrets.json`) and shows a notification

## Troubleshooting

### Plugin Tab Not Appearing

- Check that the plugin is installed and appears in the **Plugin Manager** tab
- Check browser console for errors
- Reload the page

### Form Not Generating Correctly

- Ensure `config_schema.json` exists in the plugin directory
- Validate your `config_schema.json` against JSON Schema Draft 07
- Check that all properties have a `type` field
- Ensure `default` values match the specified type
- Look for JavaScript errors in browser console

### Configuration Not Saving

- Ensure the plugin is properly installed
- Check that config keys match schema properties
- Verify backend API is accessible
- Check browser network tab for API errors

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

### Backward Compatibility

- Plugins without `config_schema.json` still work normally
- Their tab shows plain text, number and checkbox inputs for the keys already
  in their `config.json` section, or "No configuration options available for
  this plugin." when there are none
- Users can still edit config via the Raw JSON editor

## Beyond the Basic Types

Nested objects (rendered as collapsible sections), `x-widget` widgets such as
`color-picker` and `file-upload`, and more are supported; see
[PLUGIN_CONFIGURATION_GUIDE.md](PLUGIN_CONFIGURATION_GUIDE.md) and
`web_interface/static/v3/js/widgets/README.md`.

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

