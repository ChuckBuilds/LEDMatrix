# Plugin Font System Documentation

## Overview

The LEDMatrix font system has been enhanced to support plugin-specific fonts, allowing plugins to bundle and use their own custom fonts while maintaining isolation and compatibility with the global font system.

## Font Manifest Structure

Plugins that want to use custom fonts must include a `fonts` section in their `manifest.json` file. Here's the complete structure:

```json
{
    "name": "My Custom Plugin",
    "version": "1.0.0",
    "description": "A plugin that uses custom fonts",
    "fonts": {
        "fonts": [
            {
                "family": "custom_pixel_font",
                "source": "fonts/pixel_font.ttf",
                "description": "Custom pixel-style font for game scores",
                "category": "pixel",
                "style": "monospace",
                "weight": "regular",
                "depends_on": ["press_start"],
                "compatibility": {
                    "min_size": 8,
                    "max_size": 16,
                    "preferred_sizes": [8, 12, 16]
                },
                "metadata": {
                    "author": "Font Designer",
                    "license": "OFL",
                    "url": "https://example.com/font"
                }
            },
            {
                "family": "retro_display_font",
                "source": "https://example.com/fonts/retro.ttf",
                "description": "Retro-style display font",
                "category": "display",
                "style": "serif",
                "weight": "bold"
            }
        ]
    }
}
```

## Font Definition Properties

### Required Properties

| Property | Type | Description |
|----------|------|-------------|
| `family` | string | Font family name (must be unique within plugin) |
| `source` | string | Path to font file or URL to download |

### Optional Properties

| Property | Type | Description |
|----------|------|-------------|
| `description` | string | Human-readable description of the font |
| `category` | string | Font category (pixel, display, serif, sans-serif, monospace) |
| `style` | string | Font style (regular, italic, bold) |
| `weight` | string | Font weight (light, regular, bold, etc.) |
| `depends_on` | array | Array of font family names this font depends on |
| `compatibility` | object | Size compatibility information |
| `metadata` | object | Additional metadata (author, license, etc.) |

## Source Types

### Local Font Files

```json
{
    "family": "my_font",
    "source": "fonts/my_font.ttf"
}
```

The system will look for this file in:
- `plugins/{plugin_id}/fonts/my_font.ttf`
- `plugins/{plugin_id}/assets/fonts/my_font.ttf`
- `plugins/{plugin_id}/resources/fonts/my_font.ttf`

### Remote Font URLs

```json
{
    "family": "remote_font",
    "source": "https://example.com/fonts/remote_font.ttf"
}
```

Remote fonts are automatically downloaded and cached in a temporary directory.

### File Protocol

```json
{
    "family": "system_font",
    "source": "file:///usr/share/fonts/truetype/system_font.ttf"
}
```

Absolute file paths can be specified using the `file://` protocol.

## Font Categories

| Category | Description | Use Cases |
|----------|-------------|-----------|
| `pixel` | Pixel/bitmap style fonts | Game displays, retro interfaces |
| `display` | Decorative display fonts | Titles, headers |
| `serif` | Serif fonts | Body text, formal content |
| `sans-serif` | Sans-serif fonts | Clean, modern text |
| `monospace` | Fixed-width fonts | Code, data displays |

## Using Plugin Fonts in Code

### Basic Usage

```python
from src.display_manager import DisplayManager

display = DisplayManager(config)
display.register_plugin_fonts("my_plugin", plugin_manifest)

# Use plugin font
font = display.resolve_font_with_plugin_support(
    family="my_plugin::custom_pixel_font",
    size_token="lg"
)
```

### Advanced Usage with Element Keys

```python
# Define font mappings in plugin configuration
plugin_config = {
    "font_overrides": {
        "game.score": {
            "family": "my_plugin::pixel_font",
            "size_token": "xl"
        },
        "game.timer": {
            "family": "my_plugin::retro_font",
            "size_token": "md"
        }
    }
}

# Register fonts and configuration
display.register_plugin_fonts("game_plugin", font_manifest)

# Use in rendering
font = display.resolve_font_with_plugin_support(
    element_key="game.score",
    plugin_id="game_plugin"
)
```

## Font Resolution Priority

1. **Element-specific overrides** (highest priority)
2. **Plugin-specific smart defaults**
3. **Plugin font namespace** (`plugin_id::font_family`)
4. **Global font catalog**
5. **System fallback font** (lowest priority)

## Font Dependencies

Plugins can specify font dependencies:

```json
{
    "family": "styled_font",
    "source": "fonts/styled.ttf",
    "depends_on": ["base_font"],
    "fallback_chain": ["base_font", "press_start"]
}
```

Dependencies are checked before loading fonts. If a dependency is missing, the font loading fails gracefully.

## Best Practices

### Font File Organization

```
plugins/my_plugin/
├── fonts/
│   ├── game_font.ttf
│   └── ui_font.bdf
├── manifest.json
└── manager.py
```

### Font Naming Conventions

- Use descriptive, unique family names
- Avoid generic names like "font" or "default"
- Consider using plugin name as prefix: `my_plugin_game_font`

### Size Compatibility

```json
{
    "compatibility": {
        "min_size": 8,
        "max_size": 24,
        "preferred_sizes": [8, 12, 16, 20, 24]
    }
}
```

### Error Handling

```python
try:
    success = display.register_plugin_fonts("my_plugin", manifest)
    if not success:
        logger.error("Failed to register plugin fonts")
        # Fallback to default fonts
except Exception as e:
    logger.error(f"Error registering fonts: {e}")
```

## Migration Guide

### For Existing Plugins

1. **No immediate changes required** - existing plugins continue to work
2. **Optional enhancement** - add font manifest for custom fonts
3. **Recommended** - use namespaced font families (`plugin_id::font_name`)

### For New Plugins

1. Define font manifest in `manifest.json`
2. Place font files in appropriate directories
3. Use namespaced font families in code
4. Test font loading and fallback behavior

## Examples

### Complete Plugin Manifest

```json
{
    "name": "Sports Scoreboard Plugin",
    "version": "2.0.0",
    "description": "Advanced sports scoreboard with custom fonts",
    "fonts": {
        "fonts": [
            {
                "family": "scoreboard_digits",
                "source": "fonts/digital-7.ttf",
                "description": "Digital LCD-style font for scores",
                "category": "pixel",
                "style": "monospace",
                "weight": "regular",
                "compatibility": {
                    "min_size": 16,
                    "max_size": 48,
                    "preferred_sizes": [24, 32, 48]
                }
            },
            {
                "family": "team_names",
                "source": "https://fonts.example.com/team-font.ttf",
                "description": "Clean font for team names",
                "category": "sans-serif",
                "style": "regular",
                "weight": "regular"
            }
        ]
    }
}
```

### Usage in Plugin Code

```python
class SportsScoreboardPlugin:
    def __init__(self, display_manager, config):
        self.display = display_manager
        self.plugin_id = "sports_scoreboard"

        # Register fonts
        with open("manifest.json") as f:
            manifest = json.load(f)

        if "fonts" in manifest:
            self.display.register_plugin_fonts(self.plugin_id, manifest["fonts"])

    def render_score(self, home_score, away_score):
        # Use plugin font for scores
        score_font = self.display.resolve_font_with_plugin_support(
            family=f"{self.plugin_id}::scoreboard_digits",
            size_px=32
        )

        # Use plugin font for team names
        team_font = self.display.resolve_font_with_plugin_support(
            family=f"{self.plugin_id}::team_names",
            size_token="md"
        )

        # Render with custom fonts
        self.display.draw_text(str(home_score), font=score_font, x=10, y=10)
        self.display.draw_text(str(away_score), font=score_font, x=50, y=10)
```

## Troubleshooting

### Common Issues

1. **Font not found**: Check file paths and plugin directory structure
2. **Dependency errors**: Ensure all dependent fonts are available
3. **Loading failures**: Check file permissions and font file validity
4. **Cache issues**: Clear plugin font cache if fonts aren't loading

### Debugging Commands

```python
# List all available fonts
fonts = display.list_available_fonts()
print("Global fonts:", fonts["global"])
print("Plugin fonts:", fonts["plugins"])

# Check font metadata
metadata = display.get_font_metadata("my_plugin::custom_font")
print("Font metadata:", metadata)

# Clear caches
display.clear_plugin_font_cache("my_plugin")
```

## Future Enhancements

- Font preview in web interface
- Font performance metrics
- Automatic font optimization
- Font sharing between compatible plugins
- Advanced font fallback algorithms
