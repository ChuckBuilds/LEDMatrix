# Plugin Configuration Tabs - Quick Start Guide

## 🚀 Quick Start (1 Minute)

### For Users

1. Open the web interface: `http://your-pi-ip:5000`
2. Open the **Plugin Manager** tab
3. Find a plugin in the **Plugin Store** section (e.g., "Hello World")
   and click **Install**
4. Notice a new tab appears in the second nav row with the plugin's name
5. Click that tab to configure the plugin
6. Modify settings and click **Save**
7. From **Overview**, click **Restart Display Service** to see changes

That's it! Each installed plugin automatically gets its own configuration tab.

## 🎯 What You Get

### Before This Feature
- All plugin settings mixed together in the Plugins tab
- Generic key-value inputs for configuration
- Hard to know what each setting does
- No validation or type safety

### After This Feature
- ✅ Each plugin has its own dedicated tab
- ✅ Configuration forms auto-generated from schema
- ✅ Proper input types (toggles, numbers, dropdowns)
- ✅ Help text explaining each setting
- ✅ Input validation (min/max, length, etc.)
- ✅ One-click reset to defaults

## 📋 Example Walkthrough

Let's configure the "Hello World" plugin:

### Step 1: Navigate to Configuration Tab

After installing the plugin, you'll see a new tab:

```
[Overview] [General] [...] [Plugins] [Hello World] ← New tab!
```

### Step 2: Configure Settings

The tab shows a form like this:

```
Hello World Configuration
A simple test plugin that displays a customizable message

✓ Enable or disable this plugin
  [Toggle Switch: ON]

Message
The greeting message to display
  [Hello, World!        ]

Show Time
Show the current time below the message
  [Toggle Switch: ON]

Color
RGB color for the message text [R, G, B]
  [255, 255, 255        ]

Display Duration
How long to display in seconds
  [10                   ]

[Save Configuration] [Back] [Reset to Defaults]
```

### Step 3: Save and Apply

1. Modify any settings
2. Click **Save Configuration**
3. See confirmation: "Configuration saved for hello-world. Restart display to apply changes."
4. Restart the display service

## 🛠️ For Plugin Developers

### Minimal Setup

Create `config_schema.json` in your plugin directory:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "enabled": {
      "type": "boolean",
      "default": true,
      "description": "Enable this plugin"
    },
    "message": {
      "type": "string",
      "default": "Hello!",
      "description": "Message to display"
    }
  }
}
```

Reference it in `manifest.json`:

```json
{
  "id": "my-plugin",
  "icon": "fas fa-star",              // Optional: add a custom icon!
  "config_schema": "config_schema.json"
}
```

**Done!** Your plugin now has a configuration tab.

**Bonus:** Add an `icon` field for a custom tab icon! Use Font Awesome icons (`fas fa-star`), emoji (⭐), or custom images. See [PLUGIN_CUSTOM_ICONS.md](PLUGIN_CUSTOM_ICONS.md) for the full guide.

## 🎨 Supported Input Types

### Boolean → Toggle Switch
```json
{
  "type": "boolean",
  "default": true
}
```

### Number → Number Input
```json
{
  "type": "integer",
  "default": 60,
  "minimum": 1,
  "maximum": 300
}
```

### String → Text Input
```json
{
  "type": "string",
  "default": "Hello",
  "maxLength": 50
}
```

### Array → Comma-Separated Input
```json
{
  "type": "array",
  "items": {"type": "integer"},
  "default": [255, 0, 0]
}
```
User enters: `255, 0, 0`

### Enum → Dropdown
```json
{
  "type": "string",
  "enum": ["small", "medium", "large"],
  "default": "medium"
}
```

## 💡 Pro Tips

### For Users

1. **Reset Anytime**: Use "Reset to Defaults" to restore original settings
2. **Navigate Back**: Switch to the **Plugin Manager** tab to see the
   full list of installed plugins
3. **Check Help Text**: Each field has a description explaining what it does
4. **Restart Required**: Remember to restart the display service from
   **Overview** after saving

### For Developers

1. **Add Descriptions**: Users see these as help text - be descriptive!
2. **Use Constraints**: Set min/max to guide users to valid values
3. **Sensible Defaults**: Make sure defaults work without configuration
4. **Test Your Schema**: Use a JSON Schema validator before deploying
5. **Order Matters**: Properties appear in the order you define them

## 🔧 Troubleshooting

### Tab Not Showing
- Check that `config_schema.json` exists
- Verify `config_schema` is in `manifest.json`
- Refresh the page
- Check browser console for errors

### Settings Not Saving
- Ensure plugin is properly installed
- Restart the display service after saving
- Check that all required fields are filled
- Look for validation errors in browser console

### Form Looks Wrong
- Validate your JSON Schema
- Check that types match your defaults
- Ensure descriptions are strings
- Look for JavaScript errors

## 📚 Next Steps

- Read the full documentation: [PLUGIN_CONFIGURATION_TABS.md](PLUGIN_CONFIGURATION_TABS.md)
- Check the configuration architecture: [PLUGIN_CONFIG_ARCHITECTURE.md](PLUGIN_CONFIG_ARCHITECTURE.md)
- Browse example plugins in the
  [ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins)
  repo, especially `plugins/hello-world/` and `plugins/clock-simple/`
- Join the community for help and suggestions

## 🎉 That's It!

You now have dynamic, type-safe configuration tabs for each plugin. No more manual JSON editing or cluttered interfaces - just clean, organized plugin configuration.

Enjoy! 🚀

