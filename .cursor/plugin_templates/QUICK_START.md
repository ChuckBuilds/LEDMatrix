# Quick Start: Creating a New Plugin

This guide will help you create a new plugin using the templates in `.cursor/plugin_templates/`.

## Step 1: Create Plugin Directory

```bash
cd /path/to/LEDMatrix
mkdir -p plugins/my-plugin
cd plugins/my-plugin
```

## Step 2: Copy Templates

```bash
# Copy all template files
cp ../../.cursor/plugin_templates/manifest.json.template ./manifest.json
cp ../../.cursor/plugin_templates/manager.py.template ./manager.py
cp ../../.cursor/plugin_templates/config_schema.json.template ./config_schema.json
cp ../../.cursor/plugin_templates/README.md.template ./README.md
cp ../../.cursor/plugin_templates/requirements.txt.template ./requirements.txt
```

## Step 3: Customize Files

### manifest.json

Replace placeholders:
- `PLUGIN_ID` → `my-plugin` (lowercase, use hyphens)
- `Plugin Name` → Your plugin's display name
- `PluginClassName` → `MyPlugin` (PascalCase)
- Update description, author, homepage, etc.

### manager.py

Replace placeholders:
- `PluginClassName` → `MyPlugin` (must match manifest)
- Implement `_fetch_data()` method
- Implement `_render_content()` method
- Add any custom validation in `validate_config()`

### config_schema.json

Customize:
- Update description
- Add/remove configuration properties
- Set default values
- Add validation rules

### README.md

Replace placeholders:
- `PLUGIN_ID` → `my-plugin`
- `Plugin Name` → Your plugin's name
- Fill in features, installation, configuration sections

### requirements.txt

Add your plugin's dependencies:
```txt
requests>=2.28.0
pillow>=9.0.0
```

## Step 4: Enable Plugin

Edit `config/config.json`:

```json
{
  "my-plugin": {
    "enabled": true,
    "display_duration": 15
  }
}
```

## Step 5: Test Plugin

### Test with Emulator

```bash
cd /path/to/LEDMatrix
python run.py --emulator
```

### Check Plugin Loading

Look for logs like:
```
[INFO] Discovered 1 plugin(s)
[INFO] Loaded plugin: my-plugin v1.0.0
[INFO] Added plugin mode: my-plugin
```

### Test Plugin Display

The plugin should appear in the display rotation. Check logs for any errors.

## Step 6: Develop and Iterate

1. Edit `manager.py` to implement your plugin logic
2. Test with emulator: `python run.py --emulator`
3. Check logs for errors
4. Iterate until working correctly

## Step 7: Test on Hardware (Optional)

When ready, test on Raspberry Pi:

```bash
# Deploy to Pi
rsync -avz plugins/my-plugin/ pi@raspberrypi:/path/to/LEDMatrix/plugins/my-plugin/

# Or if using git
ssh pi@raspberrypi "cd /path/to/LEDMatrix/plugins/my-plugin && git pull"

# Restart service
ssh pi@raspberrypi "sudo systemctl restart ledmatrix"
```

## Common Customizations

### Adding API Integration

1. Add API key to `config_schema.json`:
```json
{
  "api_key": {
    "type": "string",
    "description": "API key for service"
  }
}
```

2. Implement API call in `_fetch_data()`:
```python
import requests

def _fetch_data(self):
    response = requests.get(
        "https://api.example.com/data",
        headers={"Authorization": f"Bearer {self.api_key}"}
    )
    return response.json()
```

3. Store API key in `config/config_secrets.json`:
```json
{
  "my-plugin": {
    "api_key": "your-secret-key"
  }
}
```

### Adding Image Rendering

```python
def _render_content(self):
    # Load and render image
    image = Image.open("assets/logo.png")
    self.display_manager.draw_image(image, x=0, y=0)
    
    # Draw text overlay
    self.display_manager.draw_text(
        "Text",
        x=10, y=20,
        color=(255, 255, 255)
    )
```

### Adding Live Priority

1. Enable in config:
```json
{
  "my-plugin": {
    "live_priority": true
  }
}
```

2. Implement `has_live_content()`:
```python
def has_live_content(self) -> bool:
    return self.data and self.data.get("is_live", False)
```

3. Override `get_live_modes()` if needed:
```python
def get_live_modes(self) -> list:
    return ["my_plugin_live_mode"]
```

## Troubleshooting

### Plugin Not Loading

- Check `manifest.json` syntax (must be valid JSON)
- Verify `entry_point` file exists
- Ensure `class_name` matches class name in manager.py
- Check for import errors in logs

### Configuration Errors

- Validate config against `config_schema.json`
- Check required fields are present
- Verify data types match schema

### Display Issues

- Check display dimensions: `display_manager.width`, `display_manager.height`
- Verify coordinates are within bounds
- Ensure `update_display()` is called
- Test with emulator first

## Next Steps

- Review existing plugins for patterns:
  - `plugins/hockey-scoreboard/` - Sports scoreboard example
  - `plugins/ledmatrix-music/` - Real-time data example
  - `plugins/ledmatrix-stocks/` - Data display example

- Read full documentation:
  - `.cursor/plugins_guide.md` - Comprehensive guide
  - `docs/PLUGIN_ARCHITECTURE_SPEC.md` - Architecture details
  - `.cursorrules` - Development rules

- Check plugin system code:
  - `src/plugin_system/base_plugin.py` - Base class
  - `src/plugin_system/plugin_manager.py` - Plugin manager

