# Advanced Plugin Development

Advanced patterns, examples, and best practices for developing LEDMatrix plugins.

## Table of Contents

- [Using Weather Icons](#using-weather-icons)
- [Implementing Scrolling with Deferred Updates](#implementing-scrolling-with-deferred-updates)
- [Cache Strategy Patterns](#cache-strategy-patterns)
- [Font Management and Overrides](#font-management-and-overrides)
- [Error Handling Best Practices](#error-handling-best-practices)
- [Performance Optimization](#performance-optimization)
- [Testing Plugins with Mocks](#testing-plugins-with-mocks)
- [Inter-Plugin Communication](#inter-plugin-communication)
- [Live Priority Implementation](#live-priority-implementation)
- [Dynamic Duration Support](#dynamic-duration-support)

---

## Using Weather Icons

The Display Manager provides built-in weather icon drawing methods for easy visual representation of weather conditions.

### Basic Weather Icon Usage

```python
def display(self, force_clear=False):
    if force_clear:
        self.display_manager.clear()
    
    # Draw weather icon based on condition
    condition = self.data.get('condition', 'clear')
    self.display_manager.draw_weather_icon(condition, x=5, y=5, size=16)
    
    # Draw temperature next to icon
    temp = self.data.get('temp', 72)
    self.display_manager.draw_text(
        f"{temp}°F",
        x=25, y=10,
        color=(255, 255, 255)
    )
    
    self.display_manager.update_display()
```

### Supported Weather Conditions

The `draw_weather_icon()` method automatically maps condition strings to appropriate icons:

- `"clear"`, `"sunny"` → Sun icon
- `"clouds"`, `"cloudy"`, `"partly cloudy"` → Cloud icon
- `"rain"`, `"drizzle"`, `"shower"` → Rain icon
- `"snow"`, `"sleet"`, `"hail"` → Snow icon
- `"thunderstorm"`, `"storm"` → Storm icon

### Custom Weather Icons

For more control, use individual icon methods:

```python
# Draw specific icons
self.display_manager.draw_sun(x=10, y=10, size=16)
self.display_manager.draw_cloud(x=10, y=10, size=16, color=(150, 150, 150))
self.display_manager.draw_rain(x=10, y=10, size=16)
self.display_manager.draw_snow(x=10, y=10, size=16)
```

### Text with Weather Icons

Use `draw_text_with_icons()` to combine text and icons:

```python
icons = [
    ("sun", 5, 5),      # Sun icon at (5, 5)
    ("cloud", 100, 5)   # Cloud icon at (100, 5)
]

self.display_manager.draw_text_with_icons(
    "Weather: Sunny, Cloudy",
    icons=icons,
    x=10, y=20,
    color=(255, 255, 255)
)
```

---

## Implementing Scrolling with Deferred Updates

For plugins that scroll content (tickers, news feeds, etc.), use scrolling state management to coordinate with the display system.

### Basic Scrolling Implementation

```python
def display(self, force_clear=False):
    if force_clear:
        self.display_manager.clear()
    
    # Mark as scrolling
    self.display_manager.set_scrolling_state(True)
    
    try:
        # Scroll content
        text = "This is a long scrolling message that needs to scroll across the display..."
        text_width = self.display_manager.get_text_width(text, self.display_manager.regular_font)
        display_width = self.display_manager.width
        
        # Scroll from right to left
        for x in range(display_width, -text_width, -2):
            self.display_manager.clear()
            self.display_manager.draw_text(text, x=x, y=16, color=(255, 255, 255))
            self.display_manager.update_display()
            time.sleep(0.05)
            
            # Update scroll activity timestamp
            self.display_manager.set_scrolling_state(True)
    finally:
        # Always mark as not scrolling when done
        self.display_manager.set_scrolling_state(False)
```

### Deferred Updates During Scrolling

Use `defer_update()` to queue non-critical updates until scrolling completes:

```python
def update(self):
    # Critical update - do immediately
    self.fetch_latest_data()
    
    # Non-critical metadata update - defer until not scrolling
    self.display_manager.defer_update(
        lambda: self.update_cache_metadata(),
        priority=1
    )
    
    # Low priority cleanup - defer
    self.display_manager.defer_update(
        lambda: self.cleanup_old_data(),
        priority=2
    )
```

### Checking Scroll State

Check if currently scrolling before performing expensive operations:

```python
def update(self):
    # Only do expensive operations when not scrolling
    if not self.display_manager.is_currently_scrolling():
        self.perform_expensive_operation()
    else:
        # Defer until scrolling stops
        self.display_manager.defer_update(
            lambda: self.perform_expensive_operation(),
            priority=0
        )
```

---

## Cache Strategy Patterns

Use appropriate cache strategies for different data types to optimize performance and reduce API calls.

### Basic Caching Pattern

```python
def update(self):
    cache_key = f"{self.plugin_id}_data"
    
    # Try to get from cache first
    cached = self.cache_manager.get(cache_key, max_age=3600)
    if cached:
        self.data = cached
        self.logger.debug("Using cached data")
        return
    
    # Fetch from API if not cached
    try:
        self.data = self._fetch_from_api()
        self.cache_manager.set(cache_key, self.data)
        self.logger.info("Fetched and cached new data")
    except Exception as e:
        self.logger.error(f"Failed to fetch data: {e}")
        # Use stale cache if available
        if cached:
            self.data = cached
            self.logger.warning("Using stale cached data due to API error")
```

### Using Cache Strategies

For automatic TTL selection based on data type:

```python
def update(self):
    cache_key = f"{self.plugin_id}_weather"
    
    # Automatically uses appropriate cache duration for weather data
    cached = self.cache_manager.get_cached_data_with_strategy(
        cache_key,
        data_type="weather"
    )
    
    if cached:
        self.data = cached
        return
    
    # Fetch new data
    self.data = self._fetch_from_api()
    self.cache_manager.set(cache_key, self.data)
```

### Sport-Specific Caching

For sports plugins, use sport-specific cache strategies:

```python
def update(self):
    sport_key = "nhl"
    cache_key = f"{self.plugin_id}_{sport_key}_games"
    
    # Uses sport-specific live_update_interval from config
    cached = self.cache_manager.get_background_cached_data(
        cache_key,
        sport_key=sport_key
    )
    
    if cached:
        self.games = cached
        return
    
    # Fetch new games
    self.games = self._fetch_games(sport_key)
    self.cache_manager.set(cache_key, self.games)
```

### Cache Invalidation

Clear cache when needed:

```python
def on_config_change(self, new_config):
    # Clear cache when API key changes
    if new_config.get('api_key') != self.config.get('api_key'):
        self.cache_manager.clear_cache(f"{self.plugin_id}_data")
        self.logger.info("Cleared cache due to API key change")
    
    super().on_config_change(new_config)
```

---

## Font Management and Overrides

Use the Font Manager for advanced font handling and user customization.

### Using Different Fonts

```python
def display(self, force_clear=False):
    if force_clear:
        self.display_manager.clear()
    
    # Use regular font for title
    self.display_manager.draw_text(
        "Title",
        x=10, y=5,
        font=self.display_manager.regular_font,
        color=(255, 255, 255)
    )
    
    # Use small font for details
    self.display_manager.draw_text(
        "Details",
        x=10, y=20,
        font=self.display_manager.small_font,
        color=(200, 200, 200)
    )
    
    # Use calendar font for compact text
    self.display_manager.draw_text(
        "Compact",
        x=10, y=30,
        font=self.display_manager.calendar_font,
        color=(150, 150, 150)
    )
    
    self.display_manager.update_display()
```

### Measuring Text

Calculate text dimensions for layout:

```python
def display(self, force_clear=False):
    if force_clear:
        self.display_manager.clear()
    
    text = "Hello, World!"
    font = self.display_manager.regular_font
    
    # Get text dimensions
    text_width = self.display_manager.get_text_width(text, font)
    font_height = self.display_manager.get_font_height(font)
    
    # Center text horizontally
    x = (self.display_manager.width - text_width) // 2
    
    # Center text vertically
    y = (self.display_manager.height - font_height) // 2
    
    self.display_manager.draw_text(text, x=x, y=y, font=font)
    self.display_manager.update_display()
```

### Multi-line Text

Render multiple lines of text:

```python
def display(self, force_clear=False):
    if force_clear:
        self.display_manager.clear()
    
    lines = [
        "Line 1",
        "Line 2",
        "Line 3"
    ]
    
    font = self.display_manager.small_font
    font_height = self.display_manager.get_font_height(font)
    y = 5
    
    for line in lines:
        # Center each line
        text_width = self.display_manager.get_text_width(line, font)
        x = (self.display_manager.width - text_width) // 2
        
        self.display_manager.draw_text(line, x=x, y=y, font=font)
        y += font_height + 2  # Add spacing between lines
    
    self.display_manager.update_display()
```

---

## Error Handling Best Practices

Implement robust error handling to ensure plugins fail gracefully.

### API Error Handling

```python
def update(self):
    cache_key = f"{self.plugin_id}_data"
    
    try:
        # Try to fetch from API
        self.data = self._fetch_from_api()
        self.cache_manager.set(cache_key, self.data)
        self.logger.info("Successfully updated data")
    except requests.exceptions.Timeout:
        self.logger.warning("API request timed out, using cached data")
        cached = self.cache_manager.get(cache_key, max_age=7200)  # Use older cache
        if cached:
            self.data = cached
        else:
            self.data = None
    except requests.exceptions.RequestException as e:
        self.logger.error(f"API request failed: {e}")
        # Try to use cached data
        cached = self.cache_manager.get(cache_key, max_age=7200)
        if cached:
            self.data = cached
            self.logger.info("Using cached data due to API error")
        else:
            self.data = None
    except Exception as e:
        self.logger.error(f"Unexpected error in update(): {e}", exc_info=True)
        self.data = None
```

### Display Error Handling

```python
def display(self, force_clear=False):
    try:
        if force_clear:
            self.display_manager.clear()
        
        # Check if we have data
        if not self.data:
            self._display_no_data()
            return
        
        # Render main content
        self._render_content()
        self.display_manager.update_display()
        
    except Exception as e:
        self.logger.error(f"Error in display(): {e}", exc_info=True)
        # Show error message to user
        try:
            self.display_manager.clear()
            self.display_manager.draw_text(
                "Error",
                x=10, y=16,
                color=(255, 0, 0)
            )
            self.display_manager.update_display()
        except Exception:
            # If even error display fails, log and continue
            self.logger.critical("Failed to display error message")

def _display_no_data(self):
    """Display a message when no data is available."""
    self.display_manager.clear()
    self.display_manager.draw_text(
        "No data",
        x=10, y=16,
        color=(128, 128, 128)
    )
    self.display_manager.update_display()
```

### Validation Error Handling

```python
def validate_config(self) -> bool:
    """Validate plugin configuration."""
    try:
        # Check required fields
        required_fields = ['api_key', 'city']
        for field in required_fields:
            if field not in self.config or not self.config[field]:
                self.logger.error(f"Missing required field: {field}")
                return False
        
        # Validate field types
        if not isinstance(self.config.get('display_duration'), (int, float)):
            self.logger.error("display_duration must be a number")
            return False
        
        # Validate ranges
        duration = self.config.get('display_duration', 15)
        if duration < 1 or duration > 300:
            self.logger.error("display_duration must be between 1 and 300 seconds")
            return False
        
        return True
    except Exception as e:
        self.logger.error(f"Error validating config: {e}", exc_info=True)
        return False
```

---

## Performance Optimization

Optimize plugin performance for smooth operation on Raspberry Pi hardware.

### Efficient Data Fetching

```python
def update(self):
    # Only fetch if cache is stale
    cache_key = f"{self.plugin_id}_data"
    cached = self.cache_manager.get(cache_key, max_age=3600)
    
    if cached and self._is_data_fresh(cached):
        self.data = cached
        return
    
    # Fetch only what's needed
    try:
        # Use appropriate cache strategy
        self.data = self._fetch_minimal_data()
        self.cache_manager.set(cache_key, self.data)
    except Exception as e:
        self.logger.error(f"Update failed: {e}")
        if cached:
            self.data = cached
```

### Optimized Rendering

```python
def display(self, force_clear=False):
    # Only clear if necessary
    if force_clear:
        self.display_manager.clear()
    else:
        # Reuse existing canvas when possible
        pass
    
    # Batch drawing operations
    self._draw_background()
    self._draw_content()
    self._draw_overlay()
    
    # Single update call at the end
    self.display_manager.update_display()
```

### Memory Management

```python
def cleanup(self):
    """Clean up resources to free memory."""
    # Clear large data structures
    if hasattr(self, 'large_cache'):
        self.large_cache.clear()
    
    # Close connections
    if hasattr(self, 'api_client'):
        self.api_client.close()
    
    # Stop threads
    if hasattr(self, 'worker_thread'):
        self.worker_thread.stop()
    
    super().cleanup()
```

### Lazy Loading

```python
def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
    super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
    self._heavy_resource = None  # Load on demand

def _get_heavy_resource(self):
    """Lazy load expensive resource."""
    if self._heavy_resource is None:
        self._heavy_resource = self._load_expensive_resource()
    return self._heavy_resource
```

---

## Testing Plugins with Mocks

Use mock objects for testing plugins without hardware dependencies.

### Basic Mock Setup

```python
from src.plugin_system.testing.mocks import MockDisplayManager, MockCacheManager, MockPluginManager

def test_plugin_display():
    # Create mocks
    display_manager = MockDisplayManager()
    cache_manager = MockCacheManager()
    plugin_manager = MockPluginManager()
    
    # Create plugin instance
    config = {"enabled": True, "display_duration": 15}
    plugin = MyPlugin("my-plugin", config, display_manager, cache_manager, plugin_manager)
    
    # Test display
    plugin.display(force_clear=True)
    
    # Verify display calls
    assert len(display_manager.draw_calls) > 0
    assert display_manager.draw_calls[0]['text'] == "Hello"
```

### Testing Cache Behavior

```python
def test_plugin_caching():
    cache_manager = MockCacheManager()
    plugin = MyPlugin("my-plugin", config, display_manager, cache_manager, plugin_manager)
    
    # Test cache miss
    plugin.update()
    assert len(cache_manager.get_calls) > 0
    assert len(cache_manager.set_calls) > 0
    
    # Test cache hit
    cache_manager.set("my-plugin_data", {"test": "data"})
    plugin.update()
    # Verify no API call was made
```

### Testing Error Handling

```python
def test_error_handling():
    display_manager = MockDisplayManager()
    cache_manager = MockCacheManager()
    plugin = MyPlugin("my-plugin", config, display_manager, cache_manager, plugin_manager)
    
    # Simulate API error
    with patch('plugin._fetch_from_api', side_effect=Exception("API Error")):
        plugin.update()
        # Verify plugin handles error gracefully
        assert plugin.data is not None or hasattr(plugin, 'error_state')
```

---

## Inter-Plugin Communication

Plugins can communicate with each other through the Plugin Manager.

### Getting Data from Another Plugin

```python
def update(self):
    # Get weather data from weather plugin
    weather_plugin = self.plugin_manager.get_plugin("weather")
    if weather_plugin and hasattr(weather_plugin, 'current_temp'):
        self.weather_temp = weather_plugin.current_temp
        self.logger.info(f"Got temperature from weather plugin: {self.weather_temp}")
```

### Checking Plugin Status

```python
def update(self):
    # Check if another plugin is enabled
    enabled_plugins = self.plugin_manager.get_enabled_plugins()
    if "weather" in enabled_plugins:
        # Weather plugin is available
        weather_plugin = self.plugin_manager.get_plugin("weather")
        if weather_plugin:
            # Use weather data
            pass
```

### Sharing Data Between Plugins

```python
class MyPlugin(BasePlugin):
    def __init__(self, ...):
        super().__init__(...)
        self.shared_data = {}  # Data accessible to other plugins
    
    def update(self):
        self.shared_data['last_update'] = time.time()
        self.shared_data['status'] = 'active'

# In another plugin
def update(self):
    my_plugin = self.plugin_manager.get_plugin("my-plugin")
    if my_plugin and hasattr(my_plugin, 'shared_data'):
        status = my_plugin.shared_data.get('status')
        self.logger.info(f"MyPlugin status: {status}")
```

---

## Live Priority Implementation

Implement live priority to automatically take over the display when your plugin has urgent content.

### Basic Live Priority

```python
class MyPlugin(BasePlugin):
    def __init__(self, ...):
        super().__init__(...)
        # Enable live priority in config
        # "live_priority": true
    
    def has_live_content(self) -> bool:
        """Check if plugin has live content."""
        # Check for live games, breaking news, etc.
        return hasattr(self, 'live_items') and len(self.live_items) > 0
    
    def get_live_modes(self) -> List[str]:
        """Return modes to show during live priority."""
        return ['live_mode']  # Only show live mode, not other modes
```

### Sports Plugin Example

```python
class SportsPlugin(BasePlugin):
    def has_live_content(self) -> bool:
        """Check if there are any live games."""
        if not hasattr(self, 'games'):
            return False
        
        for game in self.games:
            if game.get('status') == 'live':
                return True
        return False
    
    def get_live_modes(self) -> List[str]:
        """Only show live game modes during live priority."""
        return ['nhl_live', 'nba_live']  # Exclude recent/upcoming modes
```

### News Plugin Example

```python
class NewsPlugin(BasePlugin):
    def has_live_content(self) -> bool:
        """Check for breaking news."""
        if not hasattr(self, 'headlines'):
            return False
        
        # Check for breaking news flag
        for headline in self.headlines:
            if headline.get('breaking', False):
                return True
        return False
    
    def get_live_modes(self) -> List[str]:
        """Show breaking news mode during live priority."""
        return ['breaking_news']
```

---

## Dynamic Duration Support

Implement dynamic duration to extend display time until content cycle completes.

### Basic Dynamic Duration

```python
class MyPlugin(BasePlugin):
    def __init__(self, ...):
        super().__init__(...)
        self.current_step = 0
        self.total_steps = 5
    
    def supports_dynamic_duration(self) -> bool:
        """Enable dynamic duration in config."""
        return self.config.get('dynamic_duration', {}).get('enabled', False)
    
    def is_cycle_complete(self) -> bool:
        """Return True when all content has been shown."""
        return self.current_step >= self.total_steps
    
    def reset_cycle_state(self) -> None:
        """Reset cycle tracking when starting new display session."""
        self.current_step = 0
    
    def display(self, force_clear=False):
        if force_clear:
            self.display_manager.clear()
            self.reset_cycle_state()
        
        # Display current step
        self._display_step(self.current_step)
        self.display_manager.update_display()
        
        # Advance to next step
        self.current_step += 1
```

### Scrolling Content Example

```python
class ScrollingPlugin(BasePlugin):
    def __init__(self, ...):
        super().__init__(...)
        self.scroll_position = 0
        self.scroll_complete = False
    
    def supports_dynamic_duration(self) -> bool:
        return True
    
    def is_cycle_complete(self) -> bool:
        """Return True when scrolling is complete."""
        return self.scroll_complete
    
    def reset_cycle_state(self) -> None:
        """Reset scroll state."""
        self.scroll_position = 0
        self.scroll_complete = False
    
    def display(self, force_clear=False):
        if force_clear:
            self.display_manager.clear()
            self.reset_cycle_state()
        
        # Scroll content
        text = "Long scrolling message..."
        text_width = self.display_manager.get_text_width(text, self.display_manager.regular_font)
        
        if self.scroll_position < -text_width:
            # Scrolling complete
            self.scroll_complete = True
        else:
            self.display_manager.clear()
            self.display_manager.draw_text(
                text,
                x=self.scroll_position,
                y=16
            )
            self.display_manager.update_display()
            self.scroll_position -= 2
```

---

## See Also

- [Plugin API Reference](PLUGIN_API_REFERENCE.md) - Complete API documentation
- [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md) - Development workflow
- [Plugin Architecture Spec](PLUGIN_ARCHITECTURE_SPEC.md) - System architecture
- [BasePlugin Source](../src/plugin_system/base_plugin.py) - Base class implementation

