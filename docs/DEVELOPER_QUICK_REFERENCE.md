# Developer Quick Reference

One-page quick reference for common LEDMatrix development tasks.

## REST API Endpoints

### Most Common Endpoints

```bash
# Get installed plugins
GET /api/v3/plugins/installed

# Get plugin configuration
GET /api/v3/plugins/config?plugin_id=<plugin_id>

# Save plugin configuration
POST /api/v3/plugins/config
{"plugin_id": "my-plugin", "config": {...}}

# Start on-demand display
POST /api/v3/display/on-demand/start
{"plugin_id": "my-plugin", "duration": 30}

# Get system status
GET /api/v3/system/status

# Execute system action
POST /api/v3/system/action
{"action": "start_display"}
```

**Base URL**: `http://your-pi-ip:5000/api/v3`

See [API_REFERENCE.md](API_REFERENCE.md) for complete documentation.

## Display Manager Quick Methods

```python
# Core operations
display_manager.clear()                    # Clear display
display_manager.update_display()           # Update physical display

# Text rendering
display_manager.draw_text("Hello", x=10, y=16, color=(255, 255, 255))
display_manager.draw_text("Centered", centered=True)  # Auto-center

# Utilities
width = display_manager.get_text_width("Text", font)
height = display_manager.get_font_height(font)

# Weather icons
display_manager.draw_weather_icon("rain", x=10, y=10, size=16)

# Scrolling state
display_manager.set_scrolling_state(True)
display_manager.defer_update(lambda: self.update_cache(), priority=0)
```

## Cache Manager Quick Methods

```python
# Basic caching
cached = cache_manager.get("key", max_age=3600)
cache_manager.set("key", data)
cache_manager.delete("key")

# Advanced caching
data = cache_manager.get_cached_data_with_strategy("key", data_type="weather")
data = cache_manager.get_background_cached_data("key", sport_key="nhl")

# Strategy
strategy = cache_manager.get_cache_strategy("weather")
interval = cache_manager.get_sport_live_interval("nhl")
```

## Plugin Manager Quick Methods

```python
# Get plugins
plugin = plugin_manager.get_plugin("plugin-id")
all_plugins = plugin_manager.get_all_plugins()
enabled = plugin_manager.get_enabled_plugins()

# Get info
info = plugin_manager.get_plugin_info("plugin-id")
modes = plugin_manager.get_plugin_display_modes("plugin-id")
```

## BasePlugin Quick Reference

```python
class MyPlugin(BasePlugin):
    def update(self):
        # Fetch data (called based on update_interval)
        cache_key = f"{self.plugin_id}_data"
        cached = self.cache_manager.get(cache_key, max_age=3600)
        if cached:
            self.data = cached
            return
        self.data = self._fetch_from_api()
        self.cache_manager.set(cache_key, self.data)
    
    def display(self, force_clear=False):
        # Render display
        if force_clear:
            self.display_manager.clear()
        self.display_manager.draw_text("Hello", x=10, y=16)
        self.display_manager.update_display()
    
    # Optional methods
    def has_live_content(self) -> bool:
        return len(self.live_items) > 0
    
    def validate_config(self) -> bool:
        return "api_key" in self.config
```

## Common Patterns

### Caching Pattern
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

### Error Handling Pattern
```python
def display(self, force_clear=False):
    try:
        if not self.data:
            self._display_no_data()
            return
        self._render_content()
        self.display_manager.update_display()
    except Exception as e:
        self.logger.error(f"Display error: {e}", exc_info=True)
        self._display_error()
```

### Scrolling Pattern
```python
def display(self, force_clear=False):
    self.display_manager.set_scrolling_state(True)
    try:
        # Scroll content...
        for x in range(width, -text_width, -2):
            self.display_manager.clear()
            self.display_manager.draw_text(text, x=x, y=16)
            self.display_manager.update_display()
            time.sleep(0.05)
    finally:
        self.display_manager.set_scrolling_state(False)
```

## Plugin Development Checklist

- [ ] Plugin inherits from `BasePlugin`
- [ ] Implements `update()` and `display()` methods
- [ ] `manifest.json` with required fields
- [ ] `config_schema.json` for web UI (recommended)
- [ ] `README.md` with documentation
- [ ] Error handling implemented
- [ ] Uses caching appropriately
- [ ] Tested on Raspberry Pi hardware
- [ ] Follows versioning best practices

## Common Errors & Solutions

| Error | Solution |
|-------|----------|
| Plugin not discovered | Check `manifest.json` exists and `id` matches directory name |
| Import errors | Check `requirements.txt` and dependencies |
| Config validation fails | Verify `config_schema.json` syntax |
| Display not updating | Call `update_display()` after drawing |
| Cache not working | Check cache directory permissions |

## File Locations

```
LEDMatrix/
├── plugins/              # Installed plugins
├── config/
│   ├── config.json      # Main configuration
│   └── config_secrets.json  # API keys and secrets
├── docs/                 # Documentation
│   ├── API_REFERENCE.md
│   ├── PLUGIN_API_REFERENCE.md
│   └── ...
└── src/
    ├── display_manager.py
    ├── cache_manager.py
    └── plugin_system/
        └── base_plugin.py
```

## Quick Links

- [Complete API Reference](API_REFERENCE.md)
- [Plugin API Reference](PLUGIN_API_REFERENCE.md)
- [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md)
- [Advanced Patterns](ADVANCED_PLUGIN_DEVELOPMENT.md)
- [Configuration Guide](PLUGIN_CONFIGURATION_GUIDE.md)

---

**Tip**: Bookmark this page for quick access to common methods and patterns!

