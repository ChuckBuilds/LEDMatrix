# Vegas Scroll Mode - Plugin Developer Guide

Vegas scroll mode displays content from multiple plugins in a continuous horizontal scroll, similar to the news tickers seen in Las Vegas casinos. This guide explains how to integrate your plugin with Vegas mode.

## Overview

When Vegas mode is enabled, the display controller composes content from all enabled plugins into a single continuous scroll. Each plugin can control how its content appears in the scroll using one of three **display modes**:

| Mode | Behavior | Best For |
|------|----------|----------|
| **SCROLL** | Content scrolls continuously within the stream | Multi-item plugins (sports scores, odds, news) |
| **FIXED_SEGMENT** | Fixed-width block that scrolls by | Static info (clock, weather, current temp) |
| **STATIC** | Scroll pauses, plugin displays for duration, then resumes | Important alerts, detailed views |

## Quick Start

### Minimal Integration (Zero Code Changes)

If you do nothing, your plugin will work with Vegas mode using these defaults:

- Plugins with `get_vegas_content_type() == 'multi'` use **SCROLL** mode
- Plugins with `get_vegas_content_type() == 'static'` use **FIXED_SEGMENT** mode
- Content is captured by calling your plugin's `display()` method

### Basic Integration

To provide optimized Vegas content, implement `get_vegas_content()`:

```python
from PIL import Image

class MyPlugin(BasePlugin):
    def get_vegas_content(self):
        """Return content for Vegas scroll mode."""
        # Return a single image for fixed content
        return self._render_current_view()

        # OR return multiple images for multi-item content
        # return [self._render_item(item) for item in self.items]
```

### Full Integration

For complete control over Vegas behavior, implement these methods:

```python
from src.plugin_system.base_plugin import BasePlugin, VegasDisplayMode

class MyPlugin(BasePlugin):
    def get_vegas_content_type(self) -> str:
        """Legacy method - determines default mode mapping."""
        return 'multi'  # or 'static' or 'none'

    def get_vegas_display_mode(self) -> VegasDisplayMode:
        """Specify how this plugin behaves in Vegas scroll."""
        return VegasDisplayMode.SCROLL

    def get_supported_vegas_modes(self) -> list:
        """Return list of modes users can configure."""
        return [VegasDisplayMode.SCROLL, VegasDisplayMode.FIXED_SEGMENT]

    def get_vegas_content(self):
        """Return PIL Image(s) for the scroll."""
        return [self._render_game(g) for g in self.games]

    def get_vegas_segment_width(self) -> int:
        """For FIXED_SEGMENT: width in panels (optional)."""
        return 2  # Use 2 panels width
```

## Display Modes Explained

### SCROLL Mode

Content scrolls continuously within the Vegas stream. Best for plugins with multiple items.

```python
def get_vegas_display_mode(self):
    return VegasDisplayMode.SCROLL

def get_vegas_content(self):
    # Return list of images - each scrolls individually
    images = []
    for game in self.games:
        img = Image.new('RGB', (200, 32))
        # ... render game info ...
        images.append(img)
    return images
```

**When to use:**
- Sports scores with multiple games
- Stock/odds tickers with multiple items
- News feeds with multiple headlines

### FIXED_SEGMENT Mode

Content is rendered as a fixed-width block that scrolls by with other content.

```python
def get_vegas_display_mode(self):
    return VegasDisplayMode.FIXED_SEGMENT

def get_vegas_content(self):
    # Return single image at your preferred width
    img = Image.new('RGB', (128, 32))  # 2 panels wide
    # ... render clock/weather/etc ...
    return img

def get_vegas_segment_width(self):
    # Optional: specify width in panels
    return 2
```

**When to use:**
- Clock display
- Current weather/temperature
- System status indicators
- Any "at a glance" information

### STATIC Mode

Scroll pauses completely, your plugin displays using its normal `display()` method for its configured duration, then scroll resumes.

```python
def get_vegas_display_mode(self):
    return VegasDisplayMode.STATIC

def get_display_duration(self):
    # How long to pause and show this plugin
    return 10.0  # 10 seconds
```

**When to use:**
- Important alerts that need attention
- Detailed information that's hard to read while scrolling
- Interactive or animated content
- Content that requires the full display

## User Configuration

Users can override the default display mode per-plugin in their config:

```json
{
  "my_plugin": {
    "enabled": true,
    "vegas_mode": "static",       // Override: "scroll", "fixed", or "static"
    "vegas_panel_count": 2,       // Width in panels for fixed mode
    "display_duration": 10        // Duration for static mode
  }
}
```

The `get_vegas_display_mode()` method checks config first, then falls back to your implementation.

## Content Rendering Guidelines

### Image Dimensions

- **Height**: Must match display height (typically 32 pixels)
- **Width**:
  - SCROLL: Any width, content will scroll
  - FIXED_SEGMENT: `panels × single_panel_width` (e.g., 2 × 64 = 128px)

### Color Mode

Always use RGB mode for images:

```python
img = Image.new('RGB', (width, 32), color=(0, 0, 0))
```

### Performance Tips

1. **Cache rendered images** - Don't re-render on every call
2. **Pre-render on update()** - Render images when data changes, not when Vegas requests them
3. **Keep images small** - Memory adds up with multiple plugins

```python
class MyPlugin(BasePlugin):
    def __init__(self, ...):
        super().__init__(...)
        self._cached_vegas_images = None
        self._cache_valid = False

    def update(self):
        # Fetch new data
        self.data = self._fetch_data()
        # Invalidate cache so next Vegas request re-renders
        self._cache_valid = False

    def get_vegas_content(self):
        if not self._cache_valid:
            self._cached_vegas_images = self._render_all_items()
            self._cache_valid = True
        return self._cached_vegas_images
```

## Fallback Behavior

If your plugin doesn't implement `get_vegas_content()`, Vegas mode will:

1. Create a temporary canvas matching display dimensions
2. Call your `display()` method
3. Capture the resulting image
4. Use that image in the scroll

This works but is less efficient than providing native Vegas content.

## Excluding from Vegas Mode

To exclude your plugin from Vegas scroll entirely:

```python
def get_vegas_content_type(self):
    return 'none'
```

Or users can exclude via config:

```json
{
  "display": {
    "vegas_scroll": {
      "excluded_plugins": ["my_plugin"]
    }
  }
}
```

## Complete Example

Here's a complete example of a weather plugin with full Vegas integration:

```python
from PIL import Image, ImageDraw
from src.plugin_system.base_plugin import BasePlugin, VegasDisplayMode

class WeatherPlugin(BasePlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.temperature = None
        self.conditions = None
        self._vegas_image = None

    def update(self):
        """Fetch weather data."""
        data = self._fetch_weather_api()
        self.temperature = data['temp']
        self.conditions = data['conditions']
        self._vegas_image = None  # Invalidate cache

    def display(self, force_clear=False):
        """Standard display for normal rotation."""
        if force_clear:
            self.display_manager.clear()

        # Full weather display with details
        self.display_manager.draw_text(
            f"{self.temperature}°F",
            x=10, y=8, color=(255, 255, 255)
        )
        self.display_manager.draw_text(
            self.conditions,
            x=10, y=20, color=(200, 200, 200)
        )
        self.display_manager.update_display()

    # --- Vegas Mode Integration ---

    def get_vegas_content_type(self):
        """Legacy compatibility."""
        return 'static'

    def get_vegas_display_mode(self):
        """Use FIXED_SEGMENT for compact weather display."""
        # Allow user override via config
        return super().get_vegas_display_mode()

    def get_supported_vegas_modes(self):
        """Weather can work as fixed or static."""
        return [VegasDisplayMode.FIXED_SEGMENT, VegasDisplayMode.STATIC]

    def get_vegas_segment_width(self):
        """Weather needs 2 panels to show clearly."""
        return self.config.get('vegas_panel_count', 2)

    def get_vegas_content(self):
        """Render compact weather for Vegas scroll."""
        if self._vegas_image is not None:
            return self._vegas_image

        # Create compact display (2 panels = 128px typical)
        panel_width = 64  # From display.hardware.cols
        panels = self.get_vegas_segment_width() or 2
        width = panel_width * panels
        height = 32

        img = Image.new('RGB', (width, height), color=(0, 0, 40))
        draw = ImageDraw.Draw(img)

        # Draw compact weather
        temp_text = f"{self.temperature}°"
        draw.text((10, 8), temp_text, fill=(255, 255, 255))
        draw.text((60, 8), self.conditions[:10], fill=(200, 200, 200))

        self._vegas_image = img
        return img
```

## API Reference

### VegasDisplayMode Enum

```python
from src.plugin_system.base_plugin import VegasDisplayMode

VegasDisplayMode.SCROLL        # "scroll" - continuous scrolling
VegasDisplayMode.FIXED_SEGMENT # "fixed" - fixed block in scroll
VegasDisplayMode.STATIC        # "static" - pause scroll to display
```

### BasePlugin Vegas Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `get_vegas_content()` | `Image` or `List[Image]` or `None` | Content for Vegas scroll |
| `get_vegas_content_type()` | `str` | Legacy: 'multi', 'static', or 'none' |
| `get_vegas_display_mode()` | `VegasDisplayMode` | How plugin behaves in Vegas |
| `get_supported_vegas_modes()` | `List[VegasDisplayMode]` | Modes available for user config |
| `get_vegas_segment_width()` | `int` or `None` | Width in panels for FIXED_SEGMENT |

### Configuration Options

**Per-plugin config:**
```json
{
  "plugin_id": {
    "vegas_mode": "scroll|fixed|static",
    "vegas_panel_count": 2,
    "display_duration": 15
  }
}
```

**Global Vegas config:**
```json
{
  "display": {
    "vegas_scroll": {
      "enabled": true,
      "scroll_speed": 50,
      "separator_width": 32,
      "plugin_order": ["clock", "weather", "sports"],
      "excluded_plugins": ["debug_plugin"],
      "target_fps": 125,
      "buffer_ahead": 2
    }
  }
}
```

## Troubleshooting

### Plugin not appearing in Vegas scroll

1. Check `get_vegas_content_type()` doesn't return `'none'`
2. Verify plugin is not in `excluded_plugins` list
3. Ensure plugin is enabled

### Content looks wrong in scroll

1. Verify image height matches display height (32px typical)
2. Check image mode is 'RGB'
3. Test with `get_vegas_content()` returning a simple test image

### STATIC mode not pausing

1. Verify `get_vegas_display_mode()` returns `VegasDisplayMode.STATIC`
2. Check user hasn't overridden with `vegas_mode` in config
3. Ensure `display()` method works correctly

### Performance issues

1. Implement image caching in `get_vegas_content()`
2. Pre-render images in `update()` instead of on-demand
3. Reduce image dimensions if possible
