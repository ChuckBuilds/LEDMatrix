# Horizontal Scroll Manager - Quick Reference

Quick reference card for using the `HorizontalScrollManager` base class.

---

## TL;DR - Key Concepts

```python
# OLD WAY (Bad - coupled frame rate and speed)
scroll_speed = 2  # pixels per frame
scroll_delay = 0.01  # sleep between frames
# Result: 100 fps, jerky motion, can't change speed independently

# NEW WAY (Good - independent speed and frame rate)
scroll_speed = 50.0  # pixels per SECOND
max_fps = 200.0  # target frame rate
# Result: 200 fps smooth motion, speed adjustable independently
```

---

## Minimal Implementation

```python
from src.horizontal_scroll_manager import HorizontalScrollManager

class MyScroller(HorizontalScrollManager):
    def __init__(self, config, display_manager):
        super().__init__(config, display_manager, 'my_key')
        self.is_enabled = True
        self.data = []
    
    def _get_content_data(self):
        return self.data if self.data else None
    
    def _create_composite_image(self):
        # Create wide PIL Image with all content
        image = Image.new('RGB', (total_width, height))
        # ... draw content ...
        self.total_content_width = total_width
        return image
    
    def _display_fallback_message(self):
        # Show "No data" message
        pass

# Usage
manager = MyScroller(config, display)
while True:
    manager.display()  # Call every frame - handles everything!
```

---

## Configuration Template

```json
{
  "my_scroll": {
    "enabled": true,
    "scroll_speed": 50.0,        // pixels per SECOND
    "max_fps": 200.0,            // cap frame rate (0 = unlimited)
    "target_fps": 100.0,         // monitoring target
    "enable_throttling": true,   // limit to max_fps
    "loop_mode": "continuous",   // continuous|single|modulo
    "enable_wrap_around": true,  // seamless loop wrapping
    "dynamic_duration": true,    // auto-calculate time
    "min_duration": 30,          // seconds
    "max_duration": 300,         // seconds
    "duration_buffer": 0.1,      // 10% extra time
    "enable_fps_logging": true,  // log performance
    "fps_log_interval": 10.0     // log every N seconds
  }
}
```

---

## Common Patterns

### Pattern 1: Simple Text Ticker
```python
def _create_composite_image(self):
    messages = ['Msg 1', 'Msg 2', 'Msg 3']
    width_per_msg = 200
    total_width = len(messages) * width_per_msg
    
    image = Image.new('RGB', (total_width, height))
    draw = ImageDraw.Draw(image)
    
    x = 0
    for msg in messages:
        draw.text((x, height//2), msg, fill=(255,255,255))
        x += width_per_msg
    
    self.total_content_width = total_width
    return image
```

### Pattern 2: Logos + Text
```python
def _create_composite_image(self):
    items = [{'logo': logo1, 'text': 'Item 1'}, ...]
    item_width = 150
    total_width = len(items) * item_width
    
    image = Image.new('RGB', (total_width, height))
    
    x = 0
    for item in items:
        # Paste logo
        image.paste(item['logo'], (x, 0), item['logo'])
        # Draw text
        draw.text((x + 40, height//2), item['text'])
        x += item_width
    
    self.total_content_width = total_width
    return image
```

### Pattern 3: Update Data
```python
def update_data(self, new_data):
    self.data = new_data
    self.invalidate_image_cache()  # IMPORTANT!
    logger.info(f"Updated with {len(new_data)} items")
```

---

## Loop Modes Comparison

| Mode | Behavior | Best For | Efficiency |
|------|----------|----------|------------|
| `modulo` | Automatic wrap using `%` | Continuous tickers | ⭐⭐⭐⭐⭐ |
| `continuous` | Explicit reset to 0 | When you need events | ⭐⭐⭐⭐ |
| `single` | Stop at end | One-time displays | ⭐⭐⭐ |

**Recommendation:** Use `modulo` for most cases.

---

## Scroll Speed Guidelines

| Speed (px/s) | Visual Effect | Best For |
|--------------|---------------|----------|
| 20-30 | Very slow | Long text, detailed reading |
| 40-60 | Moderate | General purpose |
| 70-100 | Fast | Quick updates, short messages |
| 100+ | Very fast | Attention grabbing |

**Formula to convert old config:**
```python
new_scroll_speed = old_speed * (1.0 / old_delay)
# Example: speed=2, delay=0.01 → 2 * 100 = 200 px/s
```

---

## FPS Settings by Hardware

| LED Matrix Type | Recommended max_fps | enable_throttling |
|-----------------|---------------------|-------------------|
| Basic (Adafruit) | 60-100 | true |
| Standard RGB | 100-150 | true |
| High-end RGB | 150-200 | true |
| Professional | 200+ | false |

**Rule of thumb:** Start with 100 fps, increase if smooth, decrease if CPU high.

---

## Performance Troubleshooting

### Issue: Low FPS (below 60)

**Check:**
```python
# Enable logging
config['enable_fps_logging'] = True
config['fps_log_interval'] = 5.0

# Monitor output
# [Manager] Performance stats:
#   - Average FPS: 45.2 (target: 100.0)  ← TOO LOW!
```

**Solutions:**
1. Simplify `_create_composite_image()` - profile it
2. Reduce content complexity (fewer logos, smaller images)
3. Lower `max_fps` to realistic target
4. Check display update time

### Issue: Stuttering/Jerky Motion

**Causes:**
- Variable frame times
- Display update blocking
- Slow image creation

**Solutions:**
```json
{
  "enable_throttling": true,
  "max_fps": 100,
  "loop_mode": "modulo"
}
```

### Issue: High CPU Usage

**Solutions:**
```json
{
  "enable_throttling": true,  // ENABLE THIS
  "max_fps": 60,              // Lower target
  "loop_mode": "modulo"       // Most efficient
}
```

---

## Method Reference

### Abstract Methods (You Implement)
```python
_get_content_data() -> Any
    # Return data to display, or None if unavailable

_create_composite_image() -> Image
    # Create wide PIL Image with all content
    # Must set self.total_content_width!

_display_fallback_message() -> None
    # Show message when no data
```

### Provided Methods (Already Implemented)
```python
display(force_clear=False) -> bool
    # Main method - call every frame
    # Returns True when cycle completes

invalidate_image_cache() -> None
    # Force image recreation
    # Call when data changes!

set_scroll_speed(px_per_sec: float) -> None
    # Adjust speed dynamically

set_loop_mode(mode: str) -> None
    # Change loop mode at runtime

get_current_fps() -> float
    # Current average FPS

get_progress() -> (position, total_width)
    # Current scroll position info

get_duration() -> float
    # Calculated display duration

get_elapsed_time() -> float
    # Time since display started

get_remaining_time() -> float
    # Time until duration expires
```

---

## Common Mistakes

### ❌ Wrong: Sleep in your code
```python
def _create_composite_image(self):
    # ...
    time.sleep(0.01)  # NO! Slows down frame rate
```

### ✅ Right: No sleeps, base class handles timing
```python
def _create_composite_image(self):
    # ...
    return image  # Just return the image
```

---

### ❌ Wrong: Updating data without invalidating cache
```python
def update(self):
    self.data = fetch_new_data()
    # Forgot to invalidate cache!
```

### ✅ Right: Always invalidate after data changes
```python
def update(self):
    self.data = fetch_new_data()
    self.invalidate_image_cache()  # ✓
```

---

### ❌ Wrong: Forgetting to set total_content_width
```python
def _create_composite_image(self):
    image = Image.new('RGB', (1000, 32))
    # ... draw content ...
    return image  # total_content_width still 0!
```

### ✅ Right: Always set total_content_width
```python
def _create_composite_image(self):
    total_width = 1000
    image = Image.new('RGB', (total_width, 32))
    # ... draw content ...
    self.total_content_width = total_width  # ✓
    return image
```

---

### ❌ Wrong: Using pixels per frame
```json
{
  "scroll_speed": 2  // This is wrong context!
}
```

### ✅ Right: Use pixels per second
```json
{
  "scroll_speed": 50.0  // Pixels per SECOND
}
```

---

## Migration Checklist

When migrating existing manager to use `HorizontalScrollManager`:

- [ ] Change class to inherit from `HorizontalScrollManager`
- [ ] Update `__init__` to call `super().__init__(config, display_manager, 'config_key')`
- [ ] Implement `_get_content_data()` method
- [ ] Implement `_create_composite_image()` method
- [ ] Set `self.total_content_width` in `_create_composite_image()`
- [ ] Implement `_display_fallback_message()` method
- [ ] Remove old scrolling logic (it's now in base class)
- [ ] Remove `time.sleep()` calls
- [ ] Update configuration: `scroll_speed` → pixels/second
- [ ] Update configuration: Remove `scroll_delay`
- [ ] Add `max_fps` to configuration
- [ ] Add `loop_mode` to configuration
- [ ] Call `invalidate_image_cache()` when data updates
- [ ] Test scroll speed (may need to adjust from old values)
- [ ] Enable FPS logging to verify performance

---

## Quick Debug Commands

```python
# Check FPS
print(f"Current FPS: {manager.get_current_fps():.1f}")

# Check scroll progress
pos, total = manager.get_progress()
print(f"Progress: {pos:.0f}/{total}px ({pos/total*100:.1f}%)")

# Check timing
print(f"Elapsed: {manager.get_elapsed_time():.1f}s")
print(f"Duration: {manager.get_duration():.1f}s")
print(f"Remaining: {manager.get_remaining_time():.1f}s")

# Force restart
manager.display(force_clear=True)

# Change speed on the fly
manager.set_scroll_speed(75.0)  # Speed up!

# Change loop mode
manager.set_loop_mode('single')  # Stop at end
```

---

## Example Configurations

### For Stock Ticker
```json
{
  "stocks": {
    "scroll_speed": 60.0,
    "max_fps": 150.0,
    "loop_mode": "modulo",
    "enable_wrap_around": true,
    "dynamic_duration": true
  }
}
```

### For News Headlines
```json
{
  "news": {
    "scroll_speed": 40.0,
    "max_fps": 100.0,
    "loop_mode": "continuous",
    "enable_wrap_around": true,
    "dynamic_duration": true
  }
}
```

### For One-Time Message
```json
{
  "message": {
    "scroll_speed": 30.0,
    "max_fps": 100.0,
    "loop_mode": "single",
    "enable_wrap_around": false,
    "dynamic_duration": true
  }
}
```

---

## Need Help?

1. **Check logs:** Enable `enable_fps_logging` and watch for errors
2. **Profile:** Use FPS stats to identify bottlenecks
3. **Simplify:** Start with simple content, add complexity gradually
4. **Test:** Use the example manager to verify your setup

**Remember:** The base class handles all scrolling. Your job is just to create the content!

