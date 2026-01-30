# Plugin Error Handling Guide

This guide covers best practices for error handling in LEDMatrix plugins.

## Custom Exception Hierarchy

LEDMatrix provides typed exceptions for different error categories. Use these instead of generic `Exception`:

```python
from src.exceptions import PluginError, ConfigError, CacheError, DisplayError

# Plugin-related errors
raise PluginError("Failed to fetch data", plugin_id=self.plugin_id, context={"api": "ESPN"})

# Configuration errors
raise ConfigError("Invalid API key format", field="api_key")

# Cache errors
raise CacheError("Cache write failed", cache_key="game_data")

# Display errors
raise DisplayError("Failed to render", display_mode="live")
```

### Exception Context

All LEDMatrix exceptions support a `context` dict for additional debugging info:

```python
raise PluginError(
    "API request failed",
    plugin_id=self.plugin_id,
    context={
        "url": api_url,
        "status_code": response.status_code,
        "retry_count": 3
    }
)
```

## Logging Best Practices

### Use the Plugin Logger

Every plugin has access to `self.logger`:

```python
class MyPlugin(BasePlugin):
    def update(self):
        self.logger.info("Starting data fetch")
        self.logger.debug("API URL: %s", api_url)
        self.logger.warning("Rate limit approaching")
        self.logger.error("API request failed", exc_info=True)
```

### Log Levels

- **DEBUG**: Detailed info for troubleshooting (API URLs, parsed data)
- **INFO**: Normal operation milestones (plugin loaded, data fetched)
- **WARNING**: Recoverable issues (rate limits, cache miss, fallback used)
- **ERROR**: Failures that need attention (API down, display error)

### Include exc_info for Exceptions

```python
try:
    response = requests.get(url)
except requests.RequestException as e:
    self.logger.error("API request failed: %s", e, exc_info=True)
```

## Error Handling Patterns

### Never Use Bare except

```python
# BAD - swallows all errors including KeyboardInterrupt
try:
    self.fetch_data()
except:
    pass

# GOOD - catch specific exceptions
try:
    self.fetch_data()
except requests.RequestException as e:
    self.logger.warning("Network error, using cached data: %s", e)
    self.data = self.get_cached_data()
```

### Graceful Degradation

```python
def update(self):
    try:
        self.data = self.fetch_live_data()
    except requests.RequestException as e:
        self.logger.warning("Live data unavailable: %s", e)
        # Fall back to cache
        cached = self.cache_manager.get(self.cache_key)
        if cached:
            self.logger.info("Using cached data")
            self.data = cached
        else:
            self.logger.error("No cached data available")
            self.data = None
```

### Validate Configuration Early

```python
def validate_config(self) -> bool:
    """Validate configuration at load time."""
    api_key = self.config.get("api_key")
    if not api_key:
        self.logger.error("api_key is required but not configured")
        return False

    if not isinstance(api_key, str) or len(api_key) < 10:
        self.logger.error("api_key appears to be invalid")
        return False

    return True
```

### Handle Display Errors

```python
def display(self, force_clear: bool = False) -> bool:
    if not self.data:
        if force_clear:
            self.display_manager.clear()
            self.display_manager.update_display()
        return False

    try:
        self._render_content()
        return True
    except Exception as e:
        self.logger.error("Display error: %s", e, exc_info=True)
        # Clear display on error to prevent stale content
        self.display_manager.clear()
        self.display_manager.update_display()
        return False
```

## Error Aggregation

LEDMatrix automatically tracks plugin errors. Access error data via the API:

```bash
# Get error summary
curl http://localhost:5000/api/v3/errors/summary

# Get plugin-specific health
curl http://localhost:5000/api/v3/errors/plugin/my-plugin

# Clear old errors
curl -X POST http://localhost:5000/api/v3/errors/clear
```

### Error Patterns

When the same error occurs repeatedly (5+ times in 60 minutes), it's detected as a pattern and logged as a warning. This helps identify systemic issues.

## Common Error Scenarios

### API Rate Limiting

```python
def fetch_data(self):
    try:
        response = requests.get(self.api_url)
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            self.logger.warning("Rate limited, retry after %ds", retry_after)
            self._rate_limited_until = time.time() + retry_after
            return None
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        self.logger.error("API error: %s", e)
        return None
```

### Timeout Handling

```python
def fetch_data(self):
    try:
        response = requests.get(self.api_url, timeout=10)
        return response.json()
    except requests.Timeout:
        self.logger.warning("Request timed out, will retry next update")
        return None
    except requests.RequestException as e:
        self.logger.error("Request failed: %s", e)
        return None
```

### Missing Data Gracefully

```python
def get_team_logo(self, team_id):
    logo_path = self.logos_dir / f"{team_id}.png"
    if not logo_path.exists():
        self.logger.debug("Logo not found for team %s, using default", team_id)
        return self.default_logo
    return Image.open(logo_path)
```

## Testing Error Handling

```python
def test_handles_api_error(mock_requests):
    """Test plugin handles API errors gracefully."""
    mock_requests.get.side_effect = requests.RequestException("Network error")

    plugin = MyPlugin(...)
    plugin.update()

    # Should not raise, should log warning, should have no data
    assert plugin.data is None

def test_handles_invalid_json(mock_requests):
    """Test plugin handles invalid JSON response."""
    mock_requests.get.return_value.json.side_effect = ValueError("Invalid JSON")

    plugin = MyPlugin(...)
    plugin.update()

    assert plugin.data is None
```

## Checklist

- [ ] No bare `except:` clauses
- [ ] All exceptions logged with appropriate level
- [ ] `exc_info=True` for error-level logs
- [ ] Graceful degradation with cache fallbacks
- [ ] Configuration validated in `validate_config()`
- [ ] Display clears on error to prevent stale content
- [ ] Timeouts configured for all network requests
