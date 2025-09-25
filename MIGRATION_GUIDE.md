# LED Matrix Scroll System Migration Guide

This guide shows how to migrate existing display managers to use the new standardized scroll system.

## 🎯 Migration Benefits

- **Frame-rate independent scrolling** - Consistent speed regardless of FPS
- **Unified configuration** - Same scroll settings across all managers
- **Performance monitoring** - Built-in FPS and timing metrics
- **Easy debugging** - Centralized scroll logic with detailed logging
- **Hardware optimization** - Automatic tuning for Raspberry Pi
- **Backward compatibility** - Legacy scroll settings still work

## 📋 Migration Checklist

### Before Migration
- [ ] Backup existing manager files
- [ ] Test current scroll performance
- [ ] Note current scroll settings in config
- [ ] Identify scroll-related code sections

### After Migration
- [ ] Test scroll performance on Pi
- [ ] Verify configuration works
- [ ] Check performance metrics
- [ ] Update documentation

## 🔧 Step-by-Step Migration Process

### 1. Import the ScrollMixin

```python
# Add this import at the top of your manager
from .base_classes.scroll_mixin import ScrollMixin, LegacyScrollAdapter
```

### 2. Inherit from ScrollMixin

```python
# Change your class declaration
class YourManager(ScrollMixin):  # Add ScrollMixin inheritance
    def __init__(self, config: Dict[str, Any], display_manager):
        # ... existing initialization code ...
        
        # Initialize scroll system
        self._scroll_config_prefix = "your_manager_name"  # e.g., "stocks", "news"
        self._init_scroll_system(config, display_manager)
        
        # Legacy adapter for backward compatibility
        self._legacy_adapter = LegacyScrollAdapter(
            old_scroll_speed=self.your_config.get('scroll_speed', 1),
            old_scroll_delay=self.your_config.get('scroll_delay', 0.01),
            new_pixels_per_second=self.your_config.get('scroll_pixels_per_second', 20.0),
            new_target_fps=self.your_config.get('scroll_target_fps', 100.0)
        )
        
        # Initialize scroll controller when we have content
        self._scroll_controller = None
        self._content_width = 0
        self._content_height = 0
```

### 3. Initialize Scroll System

```python
def _init_scroll_system(self, config: Dict[str, Any], display_manager):
    """Initialize the new scroll system."""
    scroll_config = {
        'pixels_per_second': self.your_config.get('scroll_pixels_per_second', 20.0),
        'target_fps': self.your_config.get('scroll_target_fps', 100.0),
        'mode': self.your_config.get('scroll_mode', 'continuous_loop'),
        'direction': self.your_config.get('scroll_direction', 'left'),
        'enable_metrics': self.your_config.get('enable_scroll_metrics', False)
    }
    
    self._scroll_config = scroll_config
    self._display_manager = display_manager

def _ensure_scroll_controller(self):
    """Ensure scroll controller is initialized with current content dimensions."""
    if self._scroll_controller is None and self._content_width > 0:
        self._init_scroll_controller(
            self._scroll_config, 
            self._display_manager, 
            self._content_width, 
            self._content_height
        )
```

### 4. Update Content Creation

```python
def create_content_image(self, data) -> Image.Image:
    """Create content image with proper dimensions for scrolling."""
    # Get display dimensions
    matrix_width = self.display_manager.matrix.width
    matrix_height = self.display_manager.matrix.height
    
    # Create content image (wider than display for scrolling)
    content_width = matrix_width * 3  # 3x display width for smooth scrolling
    content_height = matrix_height
    
    # Store dimensions for scroll controller
    self._content_width = content_width
    self._content_height = content_height
    self._ensure_scroll_controller()
    
    # Create your content image here...
    content_image = Image.new('RGB', (content_width, content_height), (0, 0, 0))
    # ... draw your content ...
    
    return content_image
```

### 5. Update the Main Update Loop

```python
def update(self) -> bool:
    """Update display with new scroll system."""
    current_time = time.time()
    
    # Your existing update logic...
    if self.needs_data_update():
        self.data = self.fetch_data()
        self.cached_image = self.create_content_image(self.data)
    
    # Update scroll using new system
    if self.cached_image:
        # Update scroll position
        scroll_metrics = self.update_scroll(current_time)
        
        # Get the visible portion using new system
        visible_image = self.crop_scrolled_image(self.cached_image)
        
        if visible_image:
            # Display the scrolled image
            self.display_manager.matrix.SetImage(visible_image.convert('RGB'))
            
            # Log performance metrics if enabled
            if self.your_config.get('enable_scroll_metrics', False) and scroll_metrics:
                logger.info(f"Scroll metrics: {scroll_metrics}")
            
            return True
    
    return False
```

### 6. Remove Old Scroll Code

Remove these old scroll-related variables and methods:
- `self.scroll_position`
- `self.scroll_speed`
- `self.scroll_delay`
- `self.last_scroll_time`
- Old scroll update logic in your update method

### 7. Add New Scroll Methods (Optional)

```python
def get_scroll_performance(self) -> Dict[str, Any]:
    """Get current scroll performance metrics."""
    if self._scroll_controller:
        return self._scroll_controller.get_metrics()
    return {}

def reset_scroll(self):
    """Reset scroll position to beginning."""
    if self._scroll_controller:
        self._scroll_controller.reset_scroll()

def set_scroll_speed(self, pixels_per_second: float):
    """Set new scroll speed."""
    if self._scroll_controller:
        self._scroll_controller.pixels_per_second = pixels_per_second

def is_scroll_complete(self) -> bool:
    """Check if scroll animation is complete."""
    if self._scroll_controller:
        return self._scroll_controller.is_complete()
    return False
```

## 📝 Configuration Updates

### Add to config.template.json

```json
{
  "your_manager": {
    "enabled": true,
    "update_interval": 600,
    
    // Legacy scroll settings (still work)
    "scroll_speed": 1,
    "scroll_delay": 0.01,
    
    // New scroll settings (recommended)
    "scroll_pixels_per_second": 20.0,
    "scroll_target_fps": 100.0,
    "scroll_mode": "continuous_loop",
    "scroll_direction": "left",
    "enable_scroll_metrics": false,
    
    // Your other settings...
  }
}
```

## 🧪 Testing the Migration

### 1. Basic Functionality Test

```python
# Test that the manager still works
manager = YourManager(config, display_manager)
assert manager.update() == True
```

### 2. Scroll Performance Test

```python
# Test scroll performance
performance = manager.get_scroll_performance()
print(f"FPS: {performance.get('fps', 0)}")
print(f"Efficiency: {performance.get('efficiency', 0)}")
```

### 3. Configuration Test

```python
# Test that new config works
manager.set_scroll_speed(25.0)
manager.set_target_fps(100.0)
```

## 🚀 Manager-Specific Migrations

### StockManager Migration

```python
# Key changes:
# 1. Inherit from ScrollMixin
# 2. Remove old scroll variables
# 3. Use new scroll system in update()
# 4. Add scroll performance methods
```

### NewsManager Migration

```python
# Key changes:
# 1. Handle wrap-around scrolling with new system
# 2. Use crop_scrolled_image() for efficient display
# 3. Maintain existing news formatting
```

### OddsTickerManager Migration

```python
# Key changes:
# 1. Migrate time-based scrolling to new system
# 2. Handle multiple sports leagues
# 3. Maintain logo positioning
```

### TextDisplay Migration

```python
# Key changes:
# 1. Handle both TTF and BDF fonts
# 2. Use cached images with new scroll system
# 3. Maintain text formatting
```

### LeaderboardManager Migration

```python
# Key changes:
# 1. Handle very wide images efficiently
# 2. Use new system for smooth scrolling
# 3. Maintain team/score formatting
```

## 🔍 Troubleshooting

### Common Issues

1. **Import Error**: Make sure `base_classes` is in your Python path
2. **Scroll Not Working**: Check that `_content_width` and `_content_height` are set
3. **Performance Issues**: Verify `target_fps` and `pixels_per_second` settings
4. **Configuration Issues**: Ensure new scroll settings are in your config

### Debug Commands

```python
# Check scroll controller status
if manager._scroll_controller:
    print("Scroll controller initialized")
    print(f"Position: {manager._scroll_controller.scroll_position}")
    print(f"Speed: {manager._scroll_controller.pixels_per_second}")
else:
    print("Scroll controller not initialized")

# Check performance metrics
metrics = manager.get_scroll_performance()
print(f"Metrics: {metrics}")
```

## 📊 Performance Comparison

### Before Migration
- Inconsistent scroll speeds across managers
- FPS-dependent scrolling behavior
- Difficult to debug scroll issues
- Manual performance optimization

### After Migration
- Consistent scroll speeds across all managers
- Frame-rate independent scrolling
- Built-in performance monitoring
- Automatic hardware optimization

## 🎯 Next Steps

1. **Test on Pi**: Run migrated managers on your Raspberry Pi
2. **Performance Tuning**: Adjust `pixels_per_second` and `target_fps` for optimal performance
3. **Configuration**: Update your config files with new scroll settings
4. **Monitoring**: Enable scroll metrics to monitor performance
5. **Documentation**: Update any documentation with new scroll settings

## 💡 Tips for Success

- **Start Simple**: Migrate one manager at a time
- **Test Thoroughly**: Verify performance on actual Pi hardware
- **Keep Legacy Support**: Maintain backward compatibility during transition
- **Monitor Performance**: Use built-in metrics to optimize settings
- **Document Changes**: Keep track of configuration changes

The new scroll system provides a solid foundation for smooth, consistent scrolling across all your LED matrix displays! 🚀
