# LED Matrix Scroll System

## Overview

The LED Matrix project now includes a standardized scrolling system designed to provide smooth, high-performance, and easily configurable text scrolling across all display managers. This system addresses common issues with flickering, inconsistent speeds, and resource usage on Raspberry Pi devices.

## Key Features

- **High Frame Rate**: Optimized for 100fps+ with minimal CPU usage
- **Display-Size Independent**: Speed settings work consistently across different display sizes
- **Frame-Rate Independent**: Scrolling speed is based on pixels per second, not frame timing
- **Multiple Scroll Modes**: Continuous loop, one-shot, bounce, and static modes
- **Comprehensive Debugging**: Built-in performance metrics and diagnostic tools
- **Easy Migration**: Simple adapter for existing implementations
- **Smooth Animation**: Subpixel positioning and intelligent frame skipping

## Architecture

### Core Components

1. **BaseScrollController** (`scroll_base.py`): Core scrolling logic and state management
2. **ScrollMixin** (`scroll_mixin.py`): Easy integration mixin for existing display managers
3. **LegacyScrollAdapter**: Compatibility layer for migrating existing code

### Key Classes

#### BaseScrollController

The heart of the scroll system. Handles:
- Frame-rate independent scrolling using pixels per second
- Multiple scroll modes (loop, one-shot, bounce, static)
- Performance metrics and debugging
- Subpixel positioning for smooth animation
- Intelligent frame skipping to reduce CPU usage

#### ScrollMixin

A mixin class that adds scrolling capabilities to any display manager:

```python
class MyDisplayManager(ScrollMixin):
    def __init__(self, config, display_manager):
        self.config = config
        self.display_manager = display_manager
        
        # Initialize scrolling
        self.init_scroll_controller('MyDisplay')
    
    def display(self):
        # Update scroll position
        scroll_state = self.update_scroll()
        
        # Use scroll position for rendering
        if scroll_state['is_scrolling']:
            cropped_image = self.crop_scrolled_image(my_content_image)
            # Display cropped_image
```

## Configuration

### New Configuration Parameters

All scroll-enabled display managers now support these standardized parameters:

```json
{
  "display_name": {
    // Legacy parameters (still supported)
    "scroll_speed": 1,
    "scroll_delay": 0.01,
    
    // New standardized parameters
    "scroll_pixels_per_second": 20.0,    // Speed in pixels per second
    "scroll_target_fps": 100.0,          // Target frame rate
    "scroll_mode": "continuous_loop",     // Loop mode
    "scroll_direction": "left",           // Scroll direction
    "enable_scroll_metrics": false,       // Enable performance logging
    "scroll_loop_gap_pixels": 32,         // Gap for continuous loop mode
    "scroll_subpixel_positioning": true,  // Enable subpixel positioning
    "scroll_frame_skip_threshold": 0.001  // Skip frames shorter than 1ms
  }
}
```

### Scroll Modes

- **`continuous_loop`**: Content loops back to start when reaching the end
- **`one_shot`**: Scroll once and stop at the end
- **`bounce`**: Bounce back and forth between start and end
- **`static`**: No scrolling (content is centered)

### Scroll Directions

- **`left`**: Text moves left (standard ticker behavior)
- **`right`**: Text moves right
- **`up`**: Text moves up (vertical scrolling)
- **`down`**: Text moves down

## Migration Guide

### Quick Migration with ScrollMixin

For existing display managers, migration is straightforward:

1. **Add ScrollMixin inheritance**:
   ```python
   class MyManager(ScrollMixin):  # Add this
   ```

2. **Initialize scroll controller**:
   ```python
   def __init__(self, config, display_manager):
       # ... existing initialization ...
       self.init_scroll_controller('MyManager')
   ```

3. **Replace scroll logic**:
   ```python
   def display(self):
       # OLD: Complex manual scrolling
       # self.scroll_position += self.scroll_speed
       # if self.scroll_position >= content_width:
       #     self.scroll_position = 0
       # visible_portion = content.crop(...)
       
       # NEW: Simple standardized scrolling
       scroll_state = self.update_scroll()
       visible_portion = self.crop_scrolled_image(content)
   ```

### Legacy Configuration Conversion

The system automatically converts legacy scroll settings:

```python
# Legacy config
{
  "scroll_speed": 1,
  "scroll_delay": 0.01
}

# Automatically converted to:
{
  "scroll_pixels_per_second": 100.0,  # speed / delay
  "scroll_target_fps": 100.0,
  "scroll_mode": "continuous_loop"
}
```

## Performance Optimization

### Raspberry Pi Optimizations

The scroll system is specifically optimized for Raspberry Pi:

1. **Intelligent Frame Skipping**: Skips frames shorter than 1ms to reduce CPU overhead
2. **Efficient Memory Usage**: Reuses image buffers and minimizes allocations
3. **Optimized Crop Operations**: Fast image cropping with wrap-around support
4. **Configurable Target FPS**: Balance between smoothness and CPU usage

### Performance Monitoring

Enable metrics to monitor scroll performance:

```json
{
  "enable_scroll_metrics": true
}
```

This logs performance data every 30 seconds:
```
StockManager Metrics - FPS: 98.5 (avg: 99.2), Speed: 19.8px/s (target: 20.0), Frames: 12450, Position: 156.3/800
```

## Examples

### Basic Text Scrolling

```python
from src.base_classes.scroll_mixin import ScrollMixin

class SimpleTextScroller(ScrollMixin):
    def __init__(self, display_manager, text):
        self.display_manager = display_manager
        self.config = {
            'scroll_pixels_per_second': 25.0,
            'scroll_mode': 'continuous_loop'
        }
        
        # Create text image
        self.text_image = self.create_text_image(text)
        
        # Initialize scrolling
        self.init_scroll_controller(
            'SimpleText',
            content_width=self.text_image.width,
            content_height=self.text_image.height
        )
    
    def display(self):
        scroll_state = self.update_scroll()
        visible = self.crop_scrolled_image(self.text_image)
        self.display_manager.image = visible
        self.display_manager.update_display()
```

### Advanced Scrolling with Custom Logic

```python
class AdvancedScroller(ScrollMixin):
    def __init__(self, display_manager, config):
        self.display_manager = display_manager
        self.config = config
        self.init_scroll_controller('Advanced')
        
    def display(self):
        scroll_state = self.update_scroll()
        
        # Custom logic based on scroll state
        if scroll_state['needs_content_update']:
            self.refresh_content()
        
        # Use scroll position for custom rendering
        pos = scroll_state['scroll_position']
        self.render_at_position(pos)
        
        # Log performance if needed
        if scroll_state['fps'] < 90:
            logger.warning(f"Low FPS: {scroll_state['fps']:.1f}")
```

## Troubleshooting

### Common Issues

1. **Jerky Scrolling**:
   - Reduce `scroll_pixels_per_second`
   - Increase `scroll_target_fps`
   - Enable `scroll_subpixel_positioning`

2. **High CPU Usage**:
   - Reduce `scroll_target_fps`
   - Increase `scroll_frame_skip_threshold`
   - Disable `enable_scroll_metrics`

3. **Text Too Fast/Slow**:
   - Adjust `scroll_pixels_per_second` (this is display-size independent)
   - Check that legacy `scroll_speed`/`scroll_delay` aren't overriding new settings

### Debug Information

Get comprehensive debug info:

```python
debug_info = manager.get_scroll_debug_info()
print(json.dumps(debug_info, indent=2))
```

Output:
```json
{
  "debug_name": "StockManager",
  "scroll_position": 156.3,
  "content_size": "800x32",
  "display_size": "128x32",
  "pixels_per_second": 20.0,
  "mode": "continuous_loop",
  "direction": "left",
  "is_scrolling_active": true,
  "current_fps": 98.5,
  "target_fps": 100.0,
  "total_frames": 12450
}
```

## Best Practices

### Configuration

1. **Start with reasonable speeds**: 15-30 pixels per second works well for most content
2. **Use continuous_loop for tickers**: Stock tickers, news feeds, etc.
3. **Use one_shot for notifications**: Alerts, status messages
4. **Enable metrics during development**: Disable in production for better performance

### Implementation

1. **Cache content images**: Don't recreate images every frame
2. **Update content dimensions**: Call `update_scroll_content_size()` when content changes
3. **Handle wrap-around**: Use `crop_scrolled_image()` for automatic wrap-around
4. **Monitor performance**: Watch for FPS drops and adjust settings accordingly

### Raspberry Pi Specific

1. **Target 60-100 FPS**: Higher FPS improves smoothness but uses more CPU
2. **Use hardware acceleration**: Ensure display drivers are optimized
3. **Monitor system load**: Scrolling should use <10% CPU on Pi 4
4. **Test on actual hardware**: Emulator performance differs from real Pi

## Future Enhancements

Planned improvements:

1. **Easing Functions**: Smooth acceleration/deceleration
2. **Multi-line Scrolling**: Vertical scrolling for multiple lines
3. **Hardware Acceleration**: GPU-accelerated scrolling where available
4. **Dynamic Speed Adjustment**: Automatic speed adjustment based on content length
5. **Advanced Metrics**: Frame time histograms, jitter analysis

## Support

For issues or questions about the scroll system:

1. Check the debug output with `get_scroll_debug_info()`
2. Enable metrics temporarily to monitor performance
3. Review the migration examples in `src/examples/`
4. Test with different `scroll_pixels_per_second` values

The scroll system is designed to be robust and easy to use while providing professional-quality scrolling animation suitable for LED matrix displays.
