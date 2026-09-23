# Plugin Configuration Tabs - Quick Start Guide

## 🚀 Quick Start (1 Minute)

### For Users

1. Open the web interface: `http://your-pi-ip:5000`
2. Open the **Plugin Manager** tab
3. Find a plugin in the **Plugin Store** section (e.g., "Hello World")
   and click **Install**
4. Notice a new tab appears in the second nav row with the plugin's name
5. Click that tab to configure the plugin
6. Modify settings and click **Save Configuration**. The running display
   picks the change up by itself; no restart is needed

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

## 📋 Example Walkthrough

Let's configure the "Hello World" plugin:

### Step 1: Navigate to Configuration Tab

After installing the plugin, you'll see a new tab:

```
[Plugin Manager] [Hello World] ← New tab! (second nav row)
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

[Refresh] [Update] [Uninstall] [Save Configuration]
```

### Step 3: Save and Apply

1. Modify any settings
2. Click **Save Configuration**
3. See the confirmation notification. Plugin settings apply live: the
   display service reloads `config.json` when it changes and passes the new
   settings to the plugin's `on_config_change()`

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

**Done!** The file name is fixed: the web interface looks for
`config_schema.json` in the plugin's directory; there is no manifest field
for it. Every installed plugin gets a tab; the schema is what turns it into a
form.

**Bonus:** an `icon` field in `manifest.json` names a Font Awesome class for
the tab (`"icon": "fas fa-star"`). See
[PLUGIN_CUSTOM_ICONS.md](PLUGIN_CUSTOM_ICONS.md).

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

1. **Navigate Back**: Switch to the **Plugin Manager** tab to see the
   full list of installed plugins
2. **Check Help Text**: Each field has a description explaining what it does
3. **No Restart Needed**: Saved plugin settings apply to the running display

### For Developers

1. **Add Descriptions**: Users see these as help text - be descriptive!
2. **Use Constraints**: Set min/max to guide users to valid values
3. **Sensible Defaults**: Make sure defaults work without configuration
4. **Test Your Schema**: Use a JSON Schema validator before deploying
5. **Order Matters**: Properties appear in the order you define them

## 🔧 Troubleshooting

### Tab Not Showing
- Check that the plugin is installed and listed under **Plugin Manager**
- Refresh the page
- Check browser console for errors

### Settings Not Saving
- Ensure plugin is properly installed
- Check that all required fields are filled
- Look for validation errors in browser console

### Form Looks Wrong
- Check that `config_schema.json` is in the plugin's directory
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

