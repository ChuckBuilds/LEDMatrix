# Horizontal Scroll Manager Usage Guide

This guide explains how to use the high-performance `HorizontalScrollManager` base class for creating smooth, flicker-free scrolling displays on LED matrices.

---

## Table of Contents
1. [Overview](#overview)
2. [Key Concepts](#key-concepts)
3. [Configuration](#configuration)
4. [Creating a Subclass](#creating-a-subclass)
5. [Performance Tuning](#performance-tuning)
6. [Examples](#examples)
7. [Migration Guide](#migration-guide)

---

## Overview

### What Problem Does This Solve?

**Old Approach (Problematic):**
```python
# Scroll speed = pixels per frame
# Frame rate controlled by sleep delay
for frame in range(frames):
    scroll_position += 2  # Move 2 pixels per frame
    display_image()
    time.sleep(0.01)  # 100ms delay = 10 fps
```

**Problems:**
- Low frame rate = flickering on LED matrices
- Scroll speed tied to frame rate
- Changing speed requires changing frame rate
- CPU time wasted on sleep

**New Approach (High Performance):**
```python
# Scroll speed = pixels per second
# Frame rate = as fast as possible (100-200+ fps)
while True:
    delta_time = time_since_last_frame()
    scroll_position += scroll_speed * delta_time  # Time-based
    display_image()
    # No sleep! Run at maximum speed for smoothness
```

**Benefits:**
- High frame rate (100-200+ fps) = smooth, flicker-free
- Scroll speed independent of frame rate
- Precise sub-pixel positioning
- Responsive and fluid animation

---

## Key Concepts

### 1. Time-Based Scrolling

Instead of moving a fixed number of pixels per frame, we move based on elapsed time:

```python
pixels_to_move = scroll_speed_pixels_per_second * delta_time
scroll_position += pixels_to_move
```

**Example:**
- Scroll speed: 50 px/s
- Frame rate: 200 fps (5ms per frame)
- Delta time: 0.005 seconds
- Pixels per frame: 50 * 0.005 = 0.25 pixels

This gives smooth, consistent scrolling regardless of frame rate variations.

### 2. Floating Point Scroll Position

We use `float` for scroll position to allow sub-pixel accuracy:

```python
self.scroll_position = 125.73  # Float, not int!
display_position = int(round(self.scroll_position))  # Round for display
```

This prevents stuttering and ensures smooth motion even at high frame rates.

### 3. Frame Rate vs Scroll Speed Independence

| Configuration | Effect |
|---------------|--------|
| `scroll_speed: 50` | Content moves at 50 pixels/second |
| `max_fps: 100` | Display updates up to 100 times per second |
| `max_fps: 200` | Display updates up to 200 times per second |

Changing `max_fps` makes animation smoother without changing scroll speed.

### 4. Loop Modes

Three loop modes are supported:

**`continuous`** - Explicit loop with reset:
```python
if scroll_position >= content_width:
    scroll_position = 0  # Jump back to start
```

**`modulo`** - Seamless wrapping (most efficient):
```python
scroll_position = scroll_position % content_width  # Automatic wrap
```

**`single`** - Stop at the end:
```python
if scroll_position >= max_position:
    scroll_position = max_position  # Stop scrolling
    is_scrolling = False
```

---

## Configuration

### Configuration Parameters

```json
{
  "my_scroll_manager": {
    "enabled": true,
    
    // ===== Scroll Speed =====
    "scroll_speed": 50.0,        // Pixels per SECOND (not per frame)
    
    // ===== Frame Rate =====
    "target_fps": 100.0,         // Target frame rate (for monitoring)
    "max_fps": 200.0,            // Maximum frame rate cap
    "enable_throttling": true,   // Limit to max_fps to save CPU
    
    // ===== Loop Behavior =====
    "loop_mode": "continuous",   // Options: continuous, single, modulo
    "enable_wrap_around": true,  // Seamless wrap for looping
    
    // ===== Dynamic Duration =====
    "dynamic_duration": true,    // Auto-calculate display time
    "min_duration": 30,          // Minimum duration (seconds)
    "max_duration": 300,         // Maximum duration (seconds)
    "duration_buffer": 0.1,      // 10% buffer time
    "fixed_duration": 60,        // Used when dynamic_duration=false
    
    // ===== Performance Monitoring =====
    "enable_fps_logging": true,  // Log FPS statistics
    "fps_log_interval": 10.0     // Log every N seconds
  }
}
```

### Recommended Settings by Use Case

**Maximum Smoothness (LED Matrix):**
```json
{
  "scroll_speed": 50.0,
  "max_fps": 200.0,
  "enable_throttling": false,  // Let it run as fast as possible
  "loop_mode": "modulo",
  "enable_wrap_around": true
}
```

**Balanced (Good performance, moderate CPU):**
```json
{
  "scroll_speed": 50.0,
  "max_fps": 100.0,
  "enable_throttling": true,   // Cap at 100fps
  "loop_mode": "continuous",
  "enable_wrap_around": true
}
```

**CPU Conscious (Lower performance, saves power):**
```json
{
  "scroll_speed": 40.0,
  "max_fps": 60.0,
  "enable_throttling": true,
  "loop_mode": "continuous",
  "enable_wrap_around": false
}
```

---

## Creating a Subclass

### Minimal Example

```python
from src.horizontal_scroll_manager import HorizontalScrollManager
from PIL import Image, ImageDraw, ImageFont
from typing import Dict, Any, Optional, List

class MyScrollingManager(HorizontalScrollManager):
    """Custom scrolling manager for my content."""
    
    def __init__(self, config: Dict[str, Any], display_manager):
        # Initialize base class with config key for this manager
        super().__init__(config, display_manager, config_key='my_scroll')
        
        # Your custom initialization
        self.my_config = config.get('my_scroll', {})
        self.is_enabled = self.my_config.get('enabled', False)
        self.my_data = []
        
        # Load fonts, etc.
        self.font = ImageFont.load_default()
    
    def _get_content_data(self) -> Optional[List]:
        """Return the data to display."""
        if not self.is_enabled:
            return None
        return self.my_data if self.my_data else None
    
    def _create_composite_image(self) -> Optional[Image.Image]:
        """Create the wide scrolling image."""
        if not self.my_data:
            return None
        
        # Get display dimensions
        display_width = self.display_manager.matrix.width
        display_height = self.display_manager.matrix.height
        
        # Calculate total width needed
        item_width = 150  # Width per item
        gap = 20  # Gap between items
        total_width = (item_width + gap) * len(self.my_data)
        
        # Create the wide image
        image = Image.new('RGB', (total_width, display_height), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Draw each item
        x = 0
        for item in self.my_data:
            # Draw item at x position
            text = str(item)
            draw.text((x, display_height // 2), text, 
                     fill=(255, 255, 255), font=self.font)
            x += item_width + gap
        
        # Store the actual content width
        self.total_content_width = total_width
        
        return image
    
    def _display_fallback_message(self) -> None:
        """Show message when no data."""
        # Create a simple fallback image
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        
        image = Image.new('RGB', (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.text((10, height // 2), "No data", 
                 fill=(255, 0, 0), font=self.font)
        
        self.display_manager.image = image
        self.display_manager.update_display()
    
    def update_data(self, new_data: List) -> None:
        """Update the data and invalidate cache."""
        self.my_data = new_data
        self.invalidate_image_cache()  # Force image regeneration
```

### Usage

```python
# Initialize
config = load_config()
display_manager = DisplayManager()
my_manager = MyScrollingManager(config, display_manager)

# Update data
my_manager.update_data(['Item 1', 'Item 2', 'Item 3'])

# Main loop - call display() every frame
while True:
    completed = my_manager.display()
    
    if completed:
        print("Scroll cycle completed!")
    
    # Check if duration expired
    if my_manager.get_elapsed_time() > my_manager.get_duration():
        # Switch to next mode
        break
```

---

## Performance Tuning

### Monitoring Performance

Enable FPS logging to monitor performance:

```python
# In config
{
  "enable_fps_logging": true,
  "fps_log_interval": 10.0
}
```

Output:
```
[MyScrollingManager] Performance stats:
  - Average FPS: 187.3 (target: 100.0)
  - FPS range: 165.2 - 201.4
  - Avg frame time: 5.34ms
  - Scroll position: 1523.4px / 3000px
```

### Troubleshooting Low FPS

**If FPS is below 100:**

1. **Check display update time:**
   - LED matrix update might be slow
   - Try reducing matrix refresh rate
   - Check if other processes are using CPU

2. **Reduce image complexity:**
   - Simplify composite image creation
   - Use smaller logos/icons
   - Reduce number of items

3. **Enable throttling:**
   ```json
   {
     "enable_throttling": true,
     "max_fps": 60  // Lower target
   }
   ```

### Adjusting Scroll Speed

**To make scrolling faster:**
```python
manager.set_scroll_speed(100.0)  # 100 pixels/second (was 50)
```

**To make scrolling slower:**
```python
manager.set_scroll_speed(25.0)  # 25 pixels/second (was 50)
```

**Relationship between speed and duration:**
- Faster scroll = shorter duration to complete
- Slower scroll = longer duration to complete
- Dynamic duration adjusts automatically

### CPU Usage Optimization

**High CPU Usage Solutions:**

1. **Enable throttling:**
   ```json
   {"enable_throttling": true, "max_fps": 100}
   ```

2. **Reduce target FPS:**
   ```json
   {"target_fps": 60, "max_fps": 80}
   ```

3. **Use modulo loop mode:**
   ```json
   {"loop_mode": "modulo"}  // Most efficient
   ```

---

## Examples

### Example 1: Stock Ticker

```python
class StockTickerScrollManager(HorizontalScrollManager):
    """Scrolling stock ticker with logos and prices."""
    
    def __init__(self, config, display_manager):
        super().__init__(config, display_manager, config_key='stocks')
        self.stocks_config = config.get('stocks', {})
        self.is_enabled = self.stocks_config.get('enabled', False)
        self.stock_data = {}
    
    def _get_content_data(self):
        return self.stock_data if self.stock_data else None
    
    def _create_composite_image(self):
        # Get stocks
        symbols = list(self.stock_data.keys())
        if not symbols:
            return None
        
        height = self.display_manager.matrix.height
        item_width = 200  # Width per stock
        gap = 30
        total_width = (item_width + gap) * len(symbols)
        
        image = Image.new('RGB', (total_width, height), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        x = 0
        for symbol in symbols:
            data = self.stock_data[symbol]
            
            # Draw logo
            logo = self._get_stock_logo(symbol)
            if logo:
                image.paste(logo, (x, 0), logo)
            
            # Draw symbol
            draw.text((x + 40, 5), symbol, fill=(255, 255, 255))
            
            # Draw price
            price_text = f"${data['price']:.2f}"
            draw.text((x + 40, 15), price_text, fill=(0, 255, 0))
            
            x += item_width + gap
        
        self.total_content_width = total_width
        return image
    
    def _display_fallback_message(self):
        # Show "Loading stocks..."
        pass
```

### Example 2: News Ticker

```python
class NewsTickerScrollManager(HorizontalScrollManager):
    """Scrolling news headlines."""
    
    def __init__(self, config, display_manager):
        super().__init__(config, display_manager, config_key='news')
        self.news_config = config.get('news', {})
        self.is_enabled = self.news_config.get('enabled', False)
        self.headlines = []
        self.font = ImageFont.truetype("fonts/arial.ttf", 12)
    
    def _get_content_data(self):
        return self.headlines if self.headlines else None
    
    def _create_composite_image(self):
        if not self.headlines:
            return None
        
        height = self.display_manager.matrix.height
        
        # Calculate width needed for all headlines
        draw_temp = ImageDraw.Draw(Image.new('RGB', (1, 1)))
        widths = []
        separator = " • "
        
        for headline in self.headlines:
            text_width = draw_temp.textlength(headline + separator, font=self.font)
            widths.append(text_width)
        
        total_width = int(sum(widths)) + 100  # Add padding
        
        # Create image
        image = Image.new('RGB', (total_width, height), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Draw headlines
        x = 50  # Start with padding
        for i, headline in enumerate(self.headlines):
            draw.text((x, height // 2 - 6), headline, 
                     fill=(255, 255, 0), font=self.font)
            x += widths[i]
            
            # Draw separator
            if i < len(self.headlines) - 1:
                draw.text((x - 30, height // 2 - 6), separator,
                         fill=(100, 100, 100), font=self.font)
        
        self.total_content_width = total_width
        return image
    
    def _display_fallback_message(self):
        # Show "No news available"
        pass
```

### Example 3: Sports Scores Ticker

```python
class SportsScoresScrollManager(HorizontalScrollManager):
    """Scrolling sports scores with team logos."""
    
    def __init__(self, config, display_manager):
        super().__init__(config, display_manager, config_key='sports_ticker')
        self.sports_config = config.get('sports_ticker', {})
        self.is_enabled = self.sports_config.get('enabled', False)
        self.games = []
    
    def _get_content_data(self):
        return self.games if self.games else None
    
    def _create_composite_image(self):
        if not self.games:
            return None
        
        height = self.display_manager.matrix.height
        game_width = 180  # Width per game
        gap = 40
        total_width = (game_width + gap) * len(self.games)
        
        image = Image.new('RGB', (total_width, height), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        x = 0
        for game in self.games:
            # Draw away team
            away_logo = self._get_team_logo(game['away_team'])
            if away_logo:
                image.paste(away_logo, (x, 2), away_logo)
            draw.text((x + 25, 5), str(game['away_score']), 
                     fill=(255, 255, 255))
            
            # Draw "vs" or "@"
            draw.text((x + 40, height // 2 - 3), "@", 
                     fill=(150, 150, 150))
            
            # Draw home team
            home_logo = self._get_team_logo(game['home_team'])
            if home_logo:
                image.paste(home_logo, (x + 55, 2), home_logo)
            draw.text((x + 80, 5), str(game['home_score']), 
                     fill=(255, 255, 255))
            
            x += game_width + gap
        
        self.total_content_width = total_width
        return image
    
    def _display_fallback_message(self):
        # Show "No games"
        pass
```

---

## Migration Guide

### Migrating from Old Stock Manager

**Old Code:**
```python
class StockManager:
    def __init__(self, config, display_manager):
        self.scroll_speed = config.get('scroll_speed', 1)  # pixels per frame
        self.scroll_delay = config.get('scroll_delay', 0.01)  # sleep time
        self.scroll_position = 0
        self.cached_text_image = None
    
    def display_stocks(self):
        # Update scroll
        self.scroll_position = (self.scroll_position + self.scroll_speed) % total_width
        
        # Crop visible portion
        visible = self.cached_text_image.crop((self.scroll_position, 0, ...))
        
        # Display
        self.display_manager.image = visible
        self.display_manager.update_display()
        
        # Sleep
        time.sleep(self.scroll_delay)
```

**New Code:**
```python
class StockManager(HorizontalScrollManager):
    def __init__(self, config, display_manager):
        # Base class handles all scrolling logic
        super().__init__(config, display_manager, config_key='stocks')
        self.is_enabled = config.get('stocks', {}).get('enabled', False)
        self.stock_data = {}
    
    def _get_content_data(self):
        return self.stock_data if self.stock_data else None
    
    def _create_composite_image(self):
        # Same image creation logic, but return the image
        # and set self.total_content_width
        ...
        return image
    
    def _display_fallback_message(self):
        # Same fallback logic
        ...
    
    # display() is provided by base class!
    # No need to implement scrolling logic
```

**Configuration Update:**
```json
// Old config
{
  "stocks": {
    "scroll_speed": 1,     // pixels per frame
    "scroll_delay": 0.01   // sleep time
  }
}

// New config
{
  "stocks": {
    "scroll_speed": 50.0,  // pixels per SECOND
    "max_fps": 100.0,      // target frame rate
    "loop_mode": "modulo"  // seamless loop
  }
}
```

### Converting Scroll Speed

To convert from old "pixels per frame" to new "pixels per second":

```python
# Old: 1 pixel per frame, 0.01s delay per frame
old_scroll_speed = 1  # px/frame
old_scroll_delay = 0.01  # seconds
old_fps = 1.0 / old_scroll_delay  # = 100 fps

# New: pixels per second
new_scroll_speed = old_scroll_speed * old_fps  # = 1 * 100 = 100 px/s
```

**Conversion table:**

| Old Config | Old FPS | New scroll_speed |
|------------|---------|------------------|
| speed=1, delay=0.01 | 100 | 100 px/s |
| speed=2, delay=0.01 | 100 | 200 px/s |
| speed=1, delay=0.02 | 50 | 50 px/s |
| speed=2, delay=0.05 | 20 | 40 px/s |

---

## Best Practices

### 1. Image Creation Efficiency

**Cache elements that don't change:**
```python
def _create_composite_image(self):
    # Good: Create logos once, reuse
    if not hasattr(self, '_logo_cache'):
        self._logo_cache = {}
    
    for item in self.data:
        if item.id not in self._logo_cache:
            self._logo_cache[item.id] = self._load_logo(item.id)
        
        logo = self._logo_cache[item.id]
        # Use cached logo
```

### 2. Content Updates

**Invalidate cache when data changes:**
```python
def update_data(self, new_data):
    self.my_data = new_data
    self.invalidate_image_cache()  # Important!
```

### 3. Loop Mode Selection

- **Use `modulo`** for continuous tickers (most efficient)
- **Use `continuous`** when you need explicit loop reset events
- **Use `single`** for one-time displays (e.g., welcome messages)

### 4. Scroll Speed Guidelines

- **Slow (25-40 px/s)**: Easy to read, good for long text
- **Medium (50-75 px/s)**: Balanced, works for most content
- **Fast (100+ px/s)**: Attention-grabbing, short messages

### 5. FPS vs Quality Trade-offs

| FPS | Quality | CPU Usage | Use Case |
|-----|---------|-----------|----------|
| 60 | Good | Low | Basic displays |
| 100 | Great | Medium | Most LED matrices |
| 150 | Excellent | High | High-quality displays |
| 200+ | Perfect | Very High | Professional installations |

---

## Troubleshooting

### Problem: Scrolling is too fast

**Solution:**
```python
manager.set_scroll_speed(30.0)  # Reduce from 50 to 30 px/s
```

### Problem: Scrolling is stuttering

**Possible causes:**
1. FPS too low - Check FPS logging
2. Image creation too slow - Profile `_create_composite_image()`
3. Display update slow - Check matrix refresh rate

**Solutions:**
- Reduce content complexity
- Enable throttling with moderate max_fps
- Use simpler fonts/graphics

### Problem: High CPU usage

**Solutions:**
```json
{
  "enable_throttling": true,
  "max_fps": 60,
  "loop_mode": "modulo"
}
```

### Problem: Content not updating

**Check:**
1. Is `invalidate_image_cache()` called after data update?
2. Is `_get_content_data()` returning new data?
3. Are error messages in logs?

---

## Summary

The `HorizontalScrollManager` provides:

✅ **High Performance**: 100-200+ fps for smooth, flicker-free scrolling  
✅ **Independent Control**: Scroll speed separate from frame rate  
✅ **Time-Based Animation**: Consistent motion regardless of system load  
✅ **Flexible Looping**: Multiple loop modes for different use cases  
✅ **Dynamic Duration**: Automatic timing based on content length  
✅ **Easy Integration**: Simple subclass implementation  
✅ **Performance Monitoring**: Built-in FPS tracking and logging  

Use this base class for all horizontally scrolling content to ensure consistent, high-quality displays across your LED matrix project.

