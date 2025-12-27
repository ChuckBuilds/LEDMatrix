# Plugin API Reference

Complete API reference for plugin developers. This document describes all methods and properties available to plugins through the Display Manager, Cache Manager, and Plugin Manager.

## Table of Contents

- [BasePlugin](#baseplugin)
- [Display Manager](#display-manager)
- [Cache Manager](#cache-manager)
- [Plugin Manager](#plugin-manager)

---

## BasePlugin

All plugins must inherit from `BasePlugin` and implement the required methods. The base class provides access to managers and common functionality.

### Available Properties

```python
self.plugin_id          # Plugin identifier (string)
self.config             # Plugin configuration dictionary
self.display_manager    # DisplayManager instance
self.cache_manager      # CacheManager instance
self.plugin_manager     # PluginManager instance
self.logger             # Plugin-specific logger
self.enabled            # Boolean enabled status
```

### Required Methods

#### `update() -> None`

Fetch/update data for this plugin. Called based on `update_interval` specified in the plugin's manifest.

**Example**:
```python
def update(self):
    cache_key = f"{self.plugin_id}_data"
    cached = self.cache_manager.get(cache_key, max_age=3600)
    if cached:
        self.data = cached
        return
    
    self.data = self._fetch_from_api()
    self.cache_manager.set(cache_key, self.data)
```

#### `display(force_clear: bool = False) -> None`

Render this plugin's display. Called during display rotation or when explicitly requested.

**Parameters**:
- `force_clear` (bool): If True, clear display before rendering

**Example**:
```python
def display(self, force_clear=False):
    if force_clear:
        self.display_manager.clear()
    
    self.display_manager.draw_text(
        "Hello, World!",
        x=5, y=15,
        color=(255, 255, 255)
    )
    
    self.display_manager.update_display()
```

### Optional Methods

#### `validate_config() -> bool`

Validate plugin configuration. Override to implement custom validation.

**Returns**: `True` if config is valid, `False` otherwise

#### `has_live_content() -> bool`

Check if plugin currently has live content. Override for live priority plugins.

**Returns**: `True` if plugin has live content

#### `get_live_modes() -> List[str]`

Get list of display modes to show during live priority takeover.

**Returns**: List of mode names

#### `cleanup() -> None`

Clean up resources when plugin is unloaded. Override to close connections, stop threads, etc.

#### `on_config_change(new_config: Dict[str, Any]) -> None`

Called after plugin configuration is updated via web API.

#### `on_enable() -> None`

Called when plugin is enabled.

#### `on_disable() -> None`

Called when plugin is disabled.

#### `get_display_duration() -> float`

Get display duration for this plugin. Can be overridden for dynamic durations.

**Returns**: Duration in seconds

#### `get_info() -> Dict[str, Any]`

Return plugin info for display in web UI. Override to provide additional state information.

---

## Display Manager

The Display Manager handles all rendering operations on the LED matrix. Available as `self.display_manager` in plugins.

### Properties

```python
display_manager.width    # Display width in pixels (int)
display_manager.height    # Display height in pixels (int)
```

### Core Methods

#### `clear() -> None`

Clear the display completely. Creates a new black image.

**Note**: Does not call `update_display()` automatically. Call `update_display()` after drawing new content.

**Example**:
```python
self.display_manager.clear()
# Draw new content...
self.display_manager.update_display()
```

#### `update_display() -> None`

Update the physical display using double buffering. Call this after drawing all content.

**Example**:
```python
self.display_manager.draw_text("Hello", x=10, y=10)
self.display_manager.update_display()  # Actually show on display
```

### Text Rendering

#### `draw_text(text: str, x: int = None, y: int = None, color: tuple = (255, 255, 255), small_font: bool = False, font: ImageFont = None, centered: bool = False) -> None`

Draw text on the canvas.

**Parameters**:
- `text` (str): Text to display
- `x` (int, optional): X position. If `None`, text is centered horizontally. If `centered=True`, x is treated as center point.
- `y` (int, optional): Y position (default: 0, top of display)
- `color` (tuple): RGB color tuple (default: white)
- `small_font` (bool): Use small font if True
- `font` (ImageFont, optional): Custom font object (overrides small_font)
- `centered` (bool): If True, x is treated as center point; if False, x is left edge

**Example**:
```python
# Centered text
self.display_manager.draw_text("Hello", color=(255, 255, 0))

# Left-aligned at specific position
self.display_manager.draw_text("World", x=10, y=20, color=(0, 255, 0))

# Centered at specific x position
self.display_manager.draw_text("Center", x=64, y=16, centered=True)
```

#### `get_text_width(text: str, font) -> int`

Get the width of text when rendered with the given font.

**Parameters**:
- `text` (str): Text to measure
- `font`: Font object (ImageFont or freetype.Face)

**Returns**: Width in pixels

**Example**:
```python
width = self.display_manager.get_text_width("Hello", self.display_manager.regular_font)
x = (self.display_manager.width - width) // 2  # Center text
```

#### `get_font_height(font) -> int`

Get the height of the given font for line spacing purposes.

**Parameters**:
- `font`: Font object (ImageFont or freetype.Face)

**Returns**: Height in pixels

**Example**:
```python
font_height = self.display_manager.get_font_height(self.display_manager.regular_font)
y = 10 + font_height  # Position next line
```

#### `format_date_with_ordinal(dt: datetime) -> str`

Format a datetime object into 'Mon Aug 30th' style with ordinal suffix.

**Parameters**:
- `dt`: datetime object

**Returns**: Formatted date string

**Example**:
```python
from datetime import datetime
date_str = self.display_manager.format_date_with_ordinal(datetime.now())
# Returns: "Jan 15th"
```

### Image Rendering

#### `draw_image(image: PIL.Image, x: int, y: int) -> None`

Draw a PIL Image object on the canvas.

**Parameters**:
- `image`: PIL Image object
- `x` (int): X position (left edge)
- `y` (int): Y position (top edge)

**Example**:
```python
from PIL import Image
logo = Image.open("assets/logo.png")
self.display_manager.draw_image(logo, x=10, y=10)
self.display_manager.update_display()
```

### Weather Icons

#### `draw_weather_icon(condition: str, x: int, y: int, size: int = 16) -> None`

Draw a weather icon based on the condition string.

**Parameters**:
- `condition` (str): Weather condition (e.g., "clear", "cloudy", "rain", "snow", "storm")
- `x` (int): X position
- `y` (int): Y position
- `size` (int): Icon size in pixels (default: 16)

**Supported Conditions**:
- `"clear"`, `"sunny"` → Sun icon
- `"clouds"`, `"cloudy"`, `"partly cloudy"` → Cloud icon
- `"rain"`, `"drizzle"`, `"shower"` → Rain icon
- `"snow"`, `"sleet"`, `"hail"` → Snow icon
- `"thunderstorm"`, `"storm"` → Storm icon

**Example**:
```python
self.display_manager.draw_weather_icon("rain", x=10, y=10, size=16)
```

#### `draw_sun(x: int, y: int, size: int = 16) -> None`

Draw a sun icon with rays.

**Parameters**:
- `x` (int): X position
- `y` (int): Y position
- `size` (int): Icon size (default: 16)

#### `draw_cloud(x: int, y: int, size: int = 16, color: tuple = (200, 200, 200)) -> None`

Draw a cloud icon.

**Parameters**:
- `x` (int): X position
- `y` (int): Y position
- `size` (int): Icon size (default: 16)
- `color` (tuple): RGB color (default: light gray)

#### `draw_rain(x: int, y: int, size: int = 16) -> None`

Draw rain icon with cloud and droplets.

#### `draw_snow(x: int, y: int, size: int = 16) -> None`

Draw snow icon with cloud and snowflakes.

#### `draw_text_with_icons(text: str, icons: List[tuple] = None, x: int = None, y: int = None, color: tuple = (255, 255, 255)) -> None`

Draw text with weather icons at specified positions.

**Parameters**:
- `text` (str): Text to display
- `icons` (List[tuple], optional): List of (icon_type, x, y) tuples
- `x` (int, optional): X position for text
- `y` (int, optional): Y position for text
- `color` (tuple): Text color

**Note**: Automatically calls `update_display()` after drawing.

**Example**:
```python
icons = [
    ("sun", 5, 5),
    ("cloud", 100, 5)
]
self.display_manager.draw_text_with_icons(
    "Weather: Sunny, Cloudy",
    icons=icons,
    x=10, y=20
)
```

### Scrolling State Management

For plugins that implement scrolling content, use these methods to coordinate with the display system.

#### `set_scrolling_state(is_scrolling: bool) -> None`

Mark the display as scrolling or not scrolling. Call when scrolling starts/stops.

**Parameters**:
- `is_scrolling` (bool): True if currently scrolling, False otherwise

**Example**:
```python
def display(self, force_clear=False):
    self.display_manager.set_scrolling_state(True)
    # Scroll content...
    self.display_manager.set_scrolling_state(False)
```

#### `is_currently_scrolling() -> bool`

Check if the display is currently in a scrolling state.

**Returns**: `True` if scrolling, `False` otherwise

#### `defer_update(update_func: Callable, priority: int = 0) -> None`

Defer an update function to be called when not scrolling. Useful for non-critical updates that should wait until scrolling completes.

**Parameters**:
- `update_func`: Function to call when not scrolling
- `priority` (int): Priority level (lower numbers = higher priority, default: 0)

**Example**:
```python
def update(self):
    # Critical update - do immediately
    self.fetch_data()
    
    # Non-critical update - defer until not scrolling
    self.display_manager.defer_update(
        lambda: self.update_cache_metadata(),
        priority=1
    )
```

#### `process_deferred_updates() -> None`

Process any deferred updates if not currently scrolling. Called automatically by the display controller, but can be called manually if needed.

**Note**: Plugins typically don't need to call this directly.

#### `get_scrolling_stats() -> dict`

Get current scrolling statistics for debugging.

**Returns**: Dictionary with scrolling state information

**Example**:
```python
stats = self.display_manager.get_scrolling_stats()
self.logger.debug(f"Scrolling: {stats['is_scrolling']}, Deferred: {stats['deferred_count']}")
```

### Available Fonts

The Display Manager provides several pre-loaded fonts:

```python
display_manager.regular_font      # Press Start 2P, size 8
display_manager.small_font        # Press Start 2P, size 8
display_manager.calendar_font     # 5x7 BDF font
display_manager.extra_small_font  # 4x6 TTF font, size 6
display_manager.bdf_5x7_font     # Alias for calendar_font
```

---

## Cache Manager

The Cache Manager handles data caching to reduce API calls and improve performance. Available as `self.cache_manager` in plugins.

### Basic Methods

#### `get(key: str, max_age: int = 300) -> Optional[Dict[str, Any]]`

Get data from cache if it exists and is not stale.

**Parameters**:
- `key` (str): Cache key
- `max_age` (int): Maximum age in seconds (default: 300)

**Returns**: Cached data dictionary, or `None` if not found or stale

**Example**:
```python
cached = self.cache_manager.get("weather_data", max_age=600)
if cached:
    return cached
```

#### `set(key: str, data: Dict[str, Any], ttl: Optional[int] = None) -> None`

Store data in cache with current timestamp.

**Parameters**:
- `key` (str): Cache key
- `data` (Dict): Data to cache
- `ttl` (int, optional): Time-to-live in seconds (for compatibility)

**Example**:
```python
self.cache_manager.set("weather_data", {
    "temp": 72,
    "condition": "sunny"
})
```

#### `delete(key: str) -> None`

Remove a specific cache entry.

**Parameters**:
- `key` (str): Cache key to delete

### Advanced Methods

#### `get_cached_data(key: str, max_age: int = 300, memory_ttl: Optional[int] = None) -> Optional[Dict[str, Any]]`

Get data from cache with separate memory and disk TTLs.

**Parameters**:
- `key` (str): Cache key
- `max_age` (int): TTL for persisted (on-disk) entry
- `memory_ttl` (int, optional): TTL for in-memory entry (defaults to max_age)

**Returns**: Cached data, or `None` if not found or stale

**Example**:
```python
# Use memory cache for 60 seconds, disk cache for 1 hour
data = self.cache_manager.get_cached_data(
    "api_response",
    max_age=3600,
    memory_ttl=60
)
```

#### `get_cached_data_with_strategy(key: str, data_type: str = 'default') -> Optional[Dict[str, Any]]`

Get data using data-type-specific cache strategy. Automatically selects appropriate TTL based on data type.

**Parameters**:
- `key` (str): Cache key
- `data_type` (str): Data type for strategy selection (e.g., 'weather', 'sports_live', 'stocks')

**Returns**: Cached data, or `None` if not found or stale

**Example**:
```python
# Automatically uses appropriate cache duration for weather data
weather = self.cache_manager.get_cached_data_with_strategy(
    "weather_current",
    data_type="weather"
)
```

#### `get_with_auto_strategy(key: str) -> Optional[Dict[str, Any]]`

Get data with automatic strategy detection from cache key.

**Parameters**:
- `key` (str): Cache key (strategy inferred from key name)

**Returns**: Cached data, or `None` if not found or stale

**Example**:
```python
# Strategy automatically detected from key name
data = self.cache_manager.get_with_auto_strategy("nhl_live_scores")
```

#### `get_background_cached_data(key: str, sport_key: Optional[str] = None) -> Optional[Dict[str, Any]]`

Get background service cached data with sport-specific intervals.

**Parameters**:
- `key` (str): Cache key
- `sport_key` (str, optional): Sport identifier (e.g., 'nhl', 'nba') for live interval lookup

**Returns**: Cached data, or `None` if not found or stale

**Example**:
```python
# Uses sport-specific live_update_interval from config
games = self.cache_manager.get_background_cached_data(
    "nhl_games",
    sport_key="nhl"
)
```

### Strategy Methods

#### `get_cache_strategy(data_type: str, sport_key: Optional[str] = None) -> Dict[str, Any]`

Get cache strategy configuration for a data type.

**Parameters**:
- `data_type` (str): Data type (e.g., 'weather', 'sports_live', 'stocks')
- `sport_key` (str, optional): Sport identifier for sport-specific strategies

**Returns**: Dictionary with strategy configuration (max_age, memory_ttl, etc.)

**Example**:
```python
strategy = self.cache_manager.get_cache_strategy("sports_live", sport_key="nhl")
max_age = strategy['max_age']  # Get configured max age
```

#### `get_sport_live_interval(sport_key: str) -> int`

Get the live_update_interval for a specific sport from config.

**Parameters**:
- `sport_key` (str): Sport identifier (e.g., 'nhl', 'nba')

**Returns**: Live update interval in seconds

**Example**:
```python
interval = self.cache_manager.get_sport_live_interval("nhl")
# Returns configured live_update_interval for NHL
```

#### `get_data_type_from_key(key: str) -> str`

Extract data type from cache key to determine appropriate cache strategy.

**Parameters**:
- `key` (str): Cache key

**Returns**: Inferred data type string

#### `get_sport_key_from_cache_key(key: str) -> Optional[str]`

Extract sport key from cache key for sport-specific strategies.

**Parameters**:
- `key` (str): Cache key

**Returns**: Sport identifier, or `None` if not found

### Utility Methods

#### `clear_cache(key: Optional[str] = None) -> None`

Clear cache for a specific key or all keys.

**Parameters**:
- `key` (str, optional): Specific key to clear. If `None`, clears all cache.

**Example**:
```python
# Clear specific key
self.cache_manager.clear_cache("weather_data")

# Clear all cache
self.cache_manager.clear_cache()
```

#### `get_cache_dir() -> Optional[str]`

Get the cache directory path.

**Returns**: Cache directory path string, or `None` if not available

#### `list_cache_files() -> List[Dict[str, Any]]`

List all cache files with metadata.

**Returns**: List of dictionaries with cache file information (key, age, size, path, etc.)

**Example**:
```python
files = self.cache_manager.list_cache_files()
for file_info in files:
    self.logger.info(f"Cache: {file_info['key']}, Age: {file_info['age_display']}")
```

### Metrics Methods

#### `get_cache_metrics() -> Dict[str, Any]`

Get cache performance metrics.

**Returns**: Dictionary with cache statistics (hits, misses, hit rate, etc.)

**Example**:
```python
metrics = self.cache_manager.get_cache_metrics()
self.logger.info(f"Cache hit rate: {metrics['hit_rate']:.2%}")
```

#### `get_memory_cache_stats() -> Dict[str, Any]`

Get memory cache statistics.

**Returns**: Dictionary with memory cache stats (size, max_size, etc.)

---

## Plugin Manager

The Plugin Manager provides access to other plugins and plugin system information. Available as `self.plugin_manager` in plugins.

### Methods

#### `get_plugin(plugin_id: str) -> Optional[Any]`

Get a plugin instance by ID.

**Parameters**:
- `plugin_id` (str): Plugin identifier

**Returns**: Plugin instance, or `None` if not found

**Example**:
```python
weather_plugin = self.plugin_manager.get_plugin("weather")
if weather_plugin:
    # Access weather plugin data
    pass
```

#### `get_all_plugins() -> Dict[str, Any]`

Get all loaded plugin instances.

**Returns**: Dictionary mapping plugin_id to plugin instance

**Example**:
```python
all_plugins = self.plugin_manager.get_all_plugins()
for plugin_id, plugin in all_plugins.items():
    self.logger.info(f"Plugin {plugin_id} is loaded")
```

#### `get_enabled_plugins() -> List[str]`

Get list of enabled plugin IDs.

**Returns**: List of plugin identifier strings

#### `get_plugin_info(plugin_id: str) -> Optional[Dict[str, Any]]`

Get plugin information including manifest and runtime info.

**Parameters**:
- `plugin_id` (str): Plugin identifier

**Returns**: Dictionary with plugin information, or `None` if not found

**Example**:
```python
info = self.plugin_manager.get_plugin_info("weather")
if info:
    self.logger.info(f"Plugin: {info['name']}, Version: {info.get('version')}")
```

#### `get_all_plugin_info() -> List[Dict[str, Any]]`

Get information for all plugins.

**Returns**: List of plugin information dictionaries

#### `get_plugin_directory(plugin_id: str) -> Optional[str]`

Get the directory path for a plugin.

**Parameters**:
- `plugin_id` (str): Plugin identifier

**Returns**: Directory path string, or `None` if not found

#### `get_plugin_display_modes(plugin_id: str) -> List[str]`

Get list of display modes for a plugin.

**Parameters**:
- `plugin_id` (str): Plugin identifier

**Returns**: List of display mode names

**Example**:
```python
modes = self.plugin_manager.get_plugin_display_modes("football-scoreboard")
# Returns: ['nfl_live', 'nfl_recent', 'nfl_upcoming', ...]
```

### Plugin Manifests

Access plugin manifests through `self.plugin_manager.plugin_manifests`:

```python
# Get manifest for a plugin
manifest = self.plugin_manager.plugin_manifests.get(self.plugin_id, {})

# Access manifest fields
display_modes = manifest.get('display_modes', [])
version = manifest.get('version')
```

### Inter-Plugin Communication

Plugins can communicate with each other through the Plugin Manager:

**Example - Getting data from another plugin**:
```python
def update(self):
    # Get weather plugin
    weather_plugin = self.plugin_manager.get_plugin("weather")
    if weather_plugin and hasattr(weather_plugin, 'current_temp'):
        self.temp = weather_plugin.current_temp
```

**Example - Checking if another plugin is enabled**:
```python
enabled_plugins = self.plugin_manager.get_enabled_plugins()
if "weather" in enabled_plugins:
    # Weather plugin is enabled
    pass
```

---

## Best Practices

### Caching

1. **Use appropriate cache keys**: Include plugin ID and data type in keys
   ```python
   cache_key = f"{self.plugin_id}_weather_current"
   ```

2. **Use cache strategies**: Prefer `get_cached_data_with_strategy()` for automatic TTL selection
   ```python
   data = self.cache_manager.get_cached_data_with_strategy(
       f"{self.plugin_id}_data",
       data_type="weather"
   )
   ```

3. **Handle cache misses**: Always check for `None` return values
   ```python
   cached = self.cache_manager.get(key, max_age=3600)
   if not cached:
       cached = self._fetch_from_api()
       self.cache_manager.set(key, cached)
   ```

### Display Rendering

1. **Always call update_display()**: After drawing content, call `update_display()`
   ```python
   self.display_manager.draw_text("Hello", x=10, y=10)
   self.display_manager.update_display()  # Required!
   ```

2. **Use clear() appropriately**: Only clear when necessary (e.g., `force_clear=True`)
   ```python
   def display(self, force_clear=False):
       if force_clear:
           self.display_manager.clear()
       # Draw content...
       self.display_manager.update_display()
   ```

3. **Handle scrolling state**: If your plugin scrolls, use scrolling state methods
   ```python
   self.display_manager.set_scrolling_state(True)
   # Scroll content...
   self.display_manager.set_scrolling_state(False)
   ```

### Error Handling

1. **Log errors appropriately**: Use `self.logger` for plugin-specific logging
   ```python
   try:
       data = self._fetch_data()
   except Exception as e:
       self.logger.error(f"Failed to fetch data: {e}")
       return
   ```

2. **Handle missing data gracefully**: Provide fallback displays when data is unavailable
   ```python
   if not self.data:
       self.display_manager.draw_text("No data available", x=10, y=16)
       self.display_manager.update_display()
       return
   ```

---

## See Also

- [BasePlugin Source](../src/plugin_system/base_plugin.py) - Base plugin implementation
- [Display Manager Source](../src/display_manager.py) - Display manager implementation
- [Cache Manager Source](../src/cache_manager.py) - Cache manager implementation
- [Plugin Manager Source](../src/plugin_system/plugin_manager.py) - Plugin manager implementation
- [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md) - Complete development guide
- [Advanced Plugin Development](ADVANCED_PLUGIN_DEVELOPMENT.md) - Advanced patterns and examples

