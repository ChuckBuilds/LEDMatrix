# FontManager Usage Guide

## Overview

The enhanced FontManager provides comprehensive font management for the LEDMatrix application with support for:
- Manager font registration and detection
- Plugin font management
- Manual font overrides via web interface
- Performance monitoring and caching
- Dynamic font discovery

## Architecture

### Manager-Centric Design

Managers define their own fonts, but the FontManager:
1. **Loads and caches fonts** for performance
2. **Detects font usage** for visibility
3. **Allows manual overrides** when needed
4. **Supports plugin fonts** with namespacing

### Font Resolution Flow

```
Manager requests font → Check manual overrides → Apply manager choice → Cache & return
```

## For Manager Developers

### Basic Font Usage

```python
from src.font_manager import FontManager

class MyManager:
    def __init__(self, config, display_manager, cache_manager):
        self.font_manager = display_manager.font_manager  # Access shared FontManager
        self.manager_id = "my_manager"
        
    def display(self):
        # Define your font choices
        element_key = "my_manager.title"
        font_family = "press_start"
        font_size_px = 10
        color = (255, 255, 255)  # RGB white
        
        # Register your font choice (for detection and future overrides)
        self.font_manager.register_manager_font(
            manager_id=self.manager_id,
            element_key=element_key,
            family=font_family,
            size_px=font_size_px,
            color=color
        )
        
        # Get the font (checks for manual overrides automatically)
        font = self.font_manager.resolve_font(
            element_key=element_key,
            family=font_family,
            size_px=font_size_px
        )
        
        # Use the font for rendering
        self.display_manager.draw_text(
            "Hello World",
            x=10, y=10,
            color=color,
            font=font
        )
```

### Advanced Font Usage

```python
class AdvancedManager:
    def __init__(self, config, display_manager, cache_manager):
        self.font_manager = display_manager.font_manager
        self.manager_id = "advanced_manager"
        
        # Define your font specifications
        self.font_specs = {
            "title": {"family": "press_start", "size_px": 12, "color": (255, 255, 0)},
            "body": {"family": "four_by_six", "size_px": 8, "color": (255, 255, 255)},
            "footer": {"family": "five_by_seven", "size_px": 7, "color": (128, 128, 128)}
        }
        
        # Register all font specs
        for element_type, spec in self.font_specs.items():
            element_key = f"{self.manager_id}.{element_type}"
            self.font_manager.register_manager_font(
                manager_id=self.manager_id,
                element_key=element_key,
                family=spec["family"],
                size_px=spec["size_px"],
                color=spec["color"]
            )
    
    def get_font(self, element_type: str):
        """Helper method to get fonts with override support."""
        spec = self.font_specs[element_type]
        element_key = f"{self.manager_id}.{element_type}"
        
        return self.font_manager.resolve_font(
            element_key=element_key,
            family=spec["family"],
            size_px=spec["size_px"]
        )
    
    def display(self):
        # Get fonts (automatically checks for overrides)
        title_font = self.get_font("title")
        body_font = self.get_font("body")
        footer_font = self.get_font("footer")
        
        # Render with fonts
        self.display_manager.draw_text("Title", font=title_font, color=self.font_specs["title"]["color"])
        self.display_manager.draw_text("Body Text", font=body_font, color=self.font_specs["body"]["color"])
        self.display_manager.draw_text("Footer", font=footer_font, color=self.font_specs["footer"]["color"])
```

### Using Size Tokens

```python
# Get available size tokens
tokens = self.font_manager.get_size_tokens()
# Returns: {'xs': 6, 'sm': 8, 'md': 10, 'lg': 12, 'xl': 14, 'xxl': 16}

# Use token to get size
size_px = tokens.get('md', 10)  # 10px

# Then use in font resolution
font = self.font_manager.resolve_font(
    element_key="my_manager.text",
    family="press_start",
    size_px=size_px
)
```

## For Plugin Developers

### Plugin Font Registration

In your plugin's `manifest.json`:

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "fonts": {
    "fonts": [
      {
        "family": "custom_font",
        "source": "plugin://fonts/custom.ttf",
        "metadata": {
          "description": "Custom plugin font",
          "license": "MIT"
        }
      },
      {
        "family": "web_font",
        "source": "https://example.com/fonts/font.ttf",
        "metadata": {
          "description": "Downloaded font",
          "checksum": "sha256:abc123..."
        }
      }
    ]
  }
}
```

### Using Plugin Fonts

```python
class PluginManager:
    def __init__(self, config, display_manager, cache_manager, plugin_id):
        self.font_manager = display_manager.font_manager
        self.plugin_id = plugin_id
        
    def display(self):
        # Use plugin font (automatically namespaced)
        font = self.font_manager.resolve_font(
            element_key=f"{self.plugin_id}.text",
            family="custom_font",  # Will be resolved as "my-plugin::custom_font"
            size_px=10,
            plugin_id=self.plugin_id
        )
        
        self.display_manager.draw_text("Plugin Text", font=font)
```

## Manual Font Overrides

Users can override any font through the web interface:

1. Navigate to **Fonts** tab
2. View **Detected Manager Fonts** to see what's currently in use
3. In **Element Overrides** section:
   - Select the element (e.g., "nfl.live.score")
   - Choose a different font family
   - Choose a different size
   - Click **Add Override**

Overrides are stored in `config/font_overrides.json` and persist across restarts.

### Programmatic Overrides

```python
# Set override
font_manager.set_override(
    element_key="nfl.live.score",
    family="four_by_six",
    size_px=8
)

# Remove override
font_manager.remove_override("nfl.live.score")

# Get all overrides
overrides = font_manager.get_overrides()
```

## Font Discovery

### Available Fonts

The FontManager automatically scans `assets/fonts/` for TTF and BDF fonts:

```python
# Get all available fonts
fonts = font_manager.get_available_fonts()
# Returns: {'press_start': 'assets/fonts/PressStart2P-Regular.ttf', ...}

# Check if font exists
if "my_font" in fonts:
    font = font_manager.get_font("my_font", 10)
```

### Adding Custom Fonts

Place font files in `assets/fonts/` directory:
- Supported formats: `.ttf`, `.bdf`
- Font family name is derived from filename (without extension)
- Will be automatically discovered on next initialization

## Performance Monitoring

```python
# Get performance stats
stats = font_manager.get_performance_stats()

print(f"Cache hit rate: {stats['cache_hit_rate']*100:.1f}%")
print(f"Total fonts cached: {stats['total_fonts_cached']}")
print(f"Failed loads: {stats['failed_loads']}")
print(f"Manager fonts: {stats['manager_fonts']}")
print(f"Plugin fonts: {stats['plugin_fonts']}")
```

## Text Measurement

```python
# Measure text dimensions
width, height, baseline = font_manager.measure_text("Hello", font)

# Get font height
font_height = font_manager.get_font_height(font)
```

## Best Practices

### For Managers

1. **Register all fonts** you use for visibility
2. **Use consistent element keys** (e.g., `{manager_id}.{element_type}`)
3. **Cache font references** if using same font multiple times
4. **Use `resolve_font()`** not `get_font()` directly to support overrides
5. **Define sensible defaults** that work well on LED matrix

### For Plugins

1. **Use plugin-relative paths** (`plugin://fonts/...`)
2. **Include font metadata** (license, description)
3. **Provide fallback** fonts if custom fonts fail to load
4. **Test with different display sizes**

### General

1. **BDF fonts** are often better for small sizes on LED matrices
2. **TTF fonts** work well for larger sizes
3. **Monospace fonts** are easier to align
4. **Test on actual hardware** - what looks good on screen may not work on LED matrix

## Migration from Old System

### Old Way (Direct Font Loading)
```python
self.font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
```

### New Way (FontManager)
```python
element_key = f"{self.manager_id}.text"
self.font_manager.register_manager_font(
    manager_id=self.manager_id,
    element_key=element_key,
    family="pressstart2p-regular",
    size_px=8
)
self.font = self.font_manager.resolve_font(
    element_key=element_key,
    family="pressstart2p-regular",
    size_px=8
)
```

## Troubleshooting

### Font Not Found
- Check font file exists in `assets/fonts/`
- Verify font family name matches filename (without extension, lowercase)
- Check logs for font discovery errors

### Override Not Working
- Verify element key matches exactly what manager registered
- Check `config/font_overrides.json` for correct syntax
- Restart application to ensure overrides are loaded

### Performance Issues
- Check cache hit rate in performance stats
- Reduce number of unique font/size combinations
- Clear cache if it grows too large: `font_manager.clear_cache()`

### Plugin Fonts Not Loading
- Verify plugin manifest syntax
- Check plugin directory structure
- Review logs for download/registration errors
- Ensure font URLs are accessible

## API Reference

### FontManager Methods

- `register_manager_font(manager_id, element_key, family, size_px, color=None)` - Register font usage
- `resolve_font(element_key, family, size_px, plugin_id=None)` - Get font with override support
- `get_font(family, size_px)` - Get font directly (bypasses overrides)
- `measure_text(text, font)` - Measure text dimensions
- `get_font_height(font)` - Get font height
- `set_override(element_key, family=None, size_px=None)` - Set manual override
- `remove_override(element_key)` - Remove override
- `get_overrides()` - Get all overrides
- `get_detected_fonts()` - Get all detected font usage
- `get_manager_fonts(manager_id=None)` - Get fonts by manager
- `get_available_fonts()` - Get font catalog
- `get_size_tokens()` - Get size token definitions
- `get_performance_stats()` - Get performance metrics
- `clear_cache()` - Clear font cache
- `register_plugin_fonts(plugin_id, font_manifest)` - Register plugin fonts
- `unregister_plugin_fonts(plugin_id)` - Unregister plugin fonts

## Example: Complete Manager Implementation

See `test/font_manager_example.py` for a complete working example.

