# Advanced Features Guide

This guide covers advanced LEDMatrix features for users and developers, including Vegas scroll mode, on-demand display, cache management, background services, and permission management.

---

## 1. Vegas Scroll Mode

### Overview

Vegas scroll mode displays content from multiple plugins in a continuous horizontal scroll, similar to news tickers seen in Las Vegas casinos. Plugins contribute content segments that flow across the display in a seamless ticker-style presentation.

### Display Modes

**SCROLL (Continuous Scrolling):**
- Content scrolls continuously left
- Smooth, fluid motion
- Best for news-ticker style displays

**FIXED_SEGMENT (Fixed-Width Block):**
- Plugin gets fixed-width block on display
- Content doesn't scroll out of its segment
- Multiple plugins can share the display simultaneously

**STATIC (Scroll Pauses):**
- Scrolling pauses when content is fully visible
- Displays for specified duration, then resumes scrolling
- Best for content that needs to be fully read

### Configuration

Enable Vegas mode in `config/config.json`:

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

**Configuration Options:**

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `false` | Enable Vegas scroll mode |
| `scroll_speed` | `50` | Pixels per second scroll speed |
| `separator_width` | `32` | Width between plugin segments (pixels) |
| `plugin_order` | `[]` | Plugin display order (empty = auto) |
| `excluded_plugins` | `[]` | Plugins to exclude from Vegas mode |
| `target_fps` | `125` | Target frame rate |
| `buffer_ahead` | `2` | Number of panels to render ahead |

### Per-Plugin Configuration

Override Vegas behavior for specific plugins:

```json
{
  "my_plugin": {
    "enabled": true,
    "vegas_mode": "scroll",
    "vegas_panel_count": 2,
    "display_duration": 10
  }
}
```

**Per-Plugin Options:**

| Setting | Values | Description |
|---------|--------|-------------|
| `vegas_mode` | `scroll`, `fixed`, `static` | Display mode for this plugin |
| `vegas_panel_count` | `1-10` | Width in panels (1 panel = display width) |
| `display_duration` | seconds | Pause duration for STATIC mode |

### Plugin Integration (Developer Guide)

**1. Implement Content Method:**

```python
def get_vegas_content(self):
    """
    Return PIL Image or list of Images for Vegas mode.

    Returns:
        PIL.Image or list[PIL.Image]: Content to display
        - Single image: fixed-width content
        - List of images: multiple segments
        - None: skip this cycle
    """
    # Example: Return single wide image
    img = Image.new('RGB', (256, 32))
    # ... render your content ...
    return img

    # Example: Return multiple segments
    return [image1, image2, image3]
```

**2. Specify Content Type:**

```python
def get_vegas_content_type(self):
    """
    Specify how content should be handled.

    Returns:
        str: 'multi' | 'static' | 'none'
    """
    return 'multi'  # Default for most plugins
```

**3. Optionally Specify Display Mode:**

```python
def get_vegas_display_mode(self):
    """
    Preferred display mode for this plugin.

    Returns:
        str: 'scroll' | 'fixed' | 'static'
    """
    return 'scroll'

def get_supported_vegas_modes(self):
    """
    List of supported modes.

    Returns:
        list: ['scroll', 'fixed', 'static']
    """
    return ['scroll', 'static']
```

### Content Rendering Guidelines

**Image Dimensions:**
- **Height:** Must match display height (typically 32 pixels)
- **Width:** Varies by mode:
  - SCROLL: Any width (recommended 64-512 pixels)
  - FIXED_SEGMENT: `panel_count * display_width`
  - STATIC: Any width, optimized for readability

**Color Mode:**
- Use RGB color mode
- 24-bit color (8 bits per channel)

**Performance Tips:**
1. **Cache rendered images** - Render in `update()`, not in `get_vegas_content()`
2. **Keep images small** - Larger images use more memory
3. **Pre-render on update** - Don't create images on-demand
4. **Reuse images** - Return same image if content unchanged

### Example Integration

Complete example for a weather plugin:

```python
class WeatherPlugin(BasePlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vegas_image = None

    def update(self):
        """Update data and pre-render Vegas image"""
        # Fetch weather data
        weather_data = self.fetch_weather()

        # Pre-render Vegas image
        self.vegas_image = self._render_vegas_content(weather_data)

    def _render_vegas_content(self, data):
        """Render weather content for Vegas mode"""
        img = Image.new('RGB', (384, 32))
        draw = ImageDraw.Draw(img)

        # Draw temperature
        draw.text((10, 0), f"{data['temp']}°F", fill=(255, 255, 255))

        # Draw condition
        draw.text((100, 0), data['condition'], fill=(200, 200, 200))

        # Draw icon
        icon = Image.open(f"assets/{data['icon']}.png")
        img.paste(icon, (250, 0))

        return img

    def get_vegas_content(self):
        """Return cached Vegas image"""
        return self.vegas_image

    def get_vegas_content_type(self):
        return 'multi'

    def get_vegas_display_mode(self):
        return 'scroll'

    def get_supported_vegas_modes(self):
        return ['scroll', 'static']
```

### Fallback Behavior

If a plugin doesn't implement Vegas methods:
- System calls the plugin's `display()` method
- Captures the rendered display as a static image
- Treats it as a fixed segment

This ensures all plugins work in Vegas mode, even without explicit support.

---

## 2. On-Demand Display

### Overview

On-demand display allows users to manually trigger specific plugins to show immediately on the LED matrix, overriding the normal rotation. This is useful for:
- Quick checks (weather, scores, time)
- Pinning important information
- Testing plugins during development
- Showing specific content to visitors

### Priority Hierarchy

On-demand display has the highest priority:

```
Priority Order (highest to lowest):
1. On-Demand Display (manual trigger)
2. Live Priority (games in progress)
3. Normal Rotation
```

When on-demand expires or is cleared, the display returns to the next highest priority (live priority or normal rotation).

### Web Interface Controls

**Access:** Navigate to Settings → Plugin Management

**Controls:**
- **Show Now Button** - Triggers plugin immediately
- **Duration Slider** - Set display time (0 = indefinite)
- **Pin Checkbox** - Keep showing until manually cleared
- **Stop Button** - Clear on-demand and return to rotation
- **Shift+Click Stop** - Stop the entire display service

**Status Card:**
- Real-time status updates
- Shows active plugin and remaining time
- Pin status indicator

### REST API Reference

#### Start On-Demand Display

```bash
POST /api/display/on-demand/start

# Body:
{
  "plugin_id": "weather",
  "duration": 30,        # Optional: seconds (0 = indefinite, null = default)
  "pinned": false        # Optional: keep until manually cleared
}

# Examples:
# 30-second preview
curl -X POST http://localhost:5050/api/display/on-demand/start \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "weather", "duration": 30}'

# Pin indefinitely
curl -X POST http://localhost:5050/api/display/on-demand/start \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "hockey-scores", "pinned": true}'
```

#### Stop On-Demand Display

```bash
POST /api/display/on-demand/stop

# Body:
{
  "stop_service": false  # Optional: also stop display service
}

# Examples:
# Clear on-demand
curl -X POST http://localhost:5050/api/display/on-demand/stop

# Stop service too
curl -X POST http://localhost:5050/api/display/on-demand/stop \
  -H "Content-Type: application/json" \
  -d '{"stop_service": true}'
```

#### Get On-Demand Status

```bash
GET /api/display/on-demand/status

# Example:
curl http://localhost:5050/api/display/on-demand/status

# Response:
{
  "active": true,
  "plugin_id": "weather",
  "mode": "weather",
  "remaining": 25.5,
  "pinned": false,
  "status": "active"
}
```

### Python API Methods

```python
from src.display_controller import DisplayController

controller = DisplayController()

# Show plugin for 30 seconds
controller.show_on_demand('weather', duration=30)

# Pin plugin until manually cleared
controller.show_on_demand('hockey-scores', pinned=True)

# Show indefinitely (not pinned, clears on expiry if duration set later)
controller.show_on_demand('weather', duration=0)

# Use plugin's default duration
controller.show_on_demand('weather')

# Clear on-demand
controller.clear_on_demand()

# Check status
is_active = controller.is_on_demand_active()

# Get detailed info
info = controller.get_on_demand_info()
# Returns: {'active': bool, 'mode': str, 'duration': float, 'remaining': float, 'pinned': bool}
```

### Duration Modes

| Duration | Pinned | Behavior |
|----------|--------|----------|
| `None` | `false` | Use plugin's default duration, auto-clear when expires |
| `0` | `false` | Indefinite, clears manually or on error |
| `> 0` | `false` | Timed display, auto-clear after N seconds |
| Any | `true` | Pin until manually cleared (ignores duration) |

### Use Case Examples

**Quick Check (30-second preview):**
```python
controller.show_on_demand('weather', duration=30)
```

**Pin Important Information:**
```python
controller.show_on_demand('game-score', pinned=True)
# ... later ...
controller.clear_on_demand()
```

**Indefinite Display:**
```python
controller.show_on_demand('welcome-message', duration=0)
```

**Testing Plugin:**
```python
controller.show_on_demand('my-new-plugin', duration=60)
```

### Best Practices

**For Users:**
1. Use timed display as default (prevents forgetting to clear)
2. Pin only when necessary
3. Clear when done to return to normal rotation

**For Developers:**
1. Validate plugin ID exists before calling
2. Provide visual feedback in UI (loading state, status updates)
3. Handle concurrent requests gracefully
4. Log on-demand activations for debugging

### Security Considerations

**Authentication:**
- Add authentication to API endpoints
- Restrict on-demand to authorized users

**Rate Limiting:**
- Prevent abuse from rapid requests
- Implement cooldown between activations

**Input Validation:**
- Sanitize plugin IDs
- Validate duration values
- Check plugin exists before activation

---

## 3. On-Demand Cache Management

### Overview

On-demand display uses Redis cache keys to manage state across service restarts and coordinate between web interface and display controller. Understanding these keys helps troubleshoot stuck states.

### Cache Keys

**1. display_on_demand_request** (TTL: 1 hour)
```json
{
  "request_id": "uuid-string",
  "action": "start|stop",
  "plugin_id": "plugin-name",
  "mode": "mode-name",
  "duration": 30.0,
  "pinned": true,
  "timestamp": 1234567890.123
}
```
**Purpose:** Communication from web interface to display controller
**When Set:** API endpoint receives request
**Auto-Cleared:** After processing or 1 hour TTL

**2. display_on_demand_config** (No TTL)
```json
{
  "mode": "mode-name",
  "duration": 30.0,
  "pinned": true
}
```
**Purpose:** Persistent configuration for display controller
**When Set:** Controller processes start request
**Auto-Cleared:** When on-demand stops

**3. display_on_demand_state** (Continuously updated)
```json
{
  "active": true,
  "mode": "mode-name",
  "remaining": 25.5,
  "pinned": true,
  "status": "active|idle|restarting|error"
}
```
**Purpose:** Real-time state for web interface status card
**When Set:** Every display loop iteration
**Auto-Cleared:** Never (continuously updated)

**4. display_on_demand_processed_id** (TTL: 5 minutes)
```
"uuid-string-of-last-processed-request"
```
**Purpose:** Prevents duplicate request processing
**When Set:** After processing request
**Auto-Cleared:** After 5 minutes TTL

### When Manual Clearing is Needed

**Scenario 1: Stuck in On-Demand State**
- Symptom: Display stays on one plugin, won't return to rotation
- Clear: `config`, `state`, `request`

**Scenario 2: Mode Switching Issues**
- Symptom: Can't change to different plugin
- Clear: `request`, `processed_id`, `state`

**Scenario 3: On-Demand Not Activating**
- Symptom: Button click does nothing
- Clear: `processed_id`, `request`

**Scenario 4: After Service Crash**
- Symptom: Strange behavior after crash/restart
- Clear: All four keys

### Manual Recovery Procedures

**Via Web Interface (Recommended):**
1. Navigate to Settings → Cache Management
2. Search for "on_demand" keys
3. Select keys to delete
4. Click "Delete Selected"
5. Restart display: `sudo systemctl restart ledmatrix`

**Via Command Line:**
```bash
# Clear specific key
redis-cli DEL display_on_demand_config

# Clear all on-demand keys
redis-cli KEYS "display_on_demand_*" | xargs redis-cli DEL

# Restart service
sudo systemctl restart ledmatrix
```

**Via Python:**
```python
from src.cache_manager import CacheManager

cache = CacheManager()
cache.delete('display_on_demand_config')
cache.delete('display_on_demand_state')
cache.delete('display_on_demand_request')
cache.delete('display_on_demand_processed_id')
```

### Cache Impact on Running Service

**IMPORTANT:** Clearing cache keys does NOT immediately affect the running controller in memory.

**To fully reset:**
1. Stop the service: `sudo systemctl stop ledmatrix`
2. Clear cache keys (web UI or redis-cli)
3. Clear systemd environment: `sudo systemctl daemon-reload`
4. Start the service: `sudo systemctl start ledmatrix`

### Automatic Cleanup

The display controller automatically handles cleanup:
- **Config key**: Cleared when on-demand stops
- **State key**: Updated every display loop iteration
- **Request key**: Expires after 1 hour TTL (or after processing)
- **Processed ID**: Expires after 5 minutes TTL

---

## 4. Background Data Service

### Overview

The Background Data Service enables non-blocking data fetching through background threading. This prevents the main display loop from freezing during slow API requests, maintaining smooth display rotation.

### Benefits

**Performance:**
- Display loop never freezes during API calls
- Immediate response with cached/partial data
- Complete data loads in background

**User Experience:**
- No "frozen" display during data updates
- Smooth transitions between plugins
- Faster perceived load times

**Architecture:**
```
Cache Check → Background Fetch → Partial Data → Completion → Cache
    (0.1s)         (async)            (<1s)         (10-30s)    (cache)
```

### Configuration

Enable background service per plugin in `config/config.json`:

```json
{
  "nfl_scoreboard": {
    "enabled": true,
    "background_service": {
      "enabled": true,
      "max_workers": 3,
      "request_timeout": 30,
      "max_retries": 3,
      "priority": 2
    }
  }
}
```

**Configuration Options:**

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `false` | Enable background service for this plugin |
| `max_workers` | `3` | Max concurrent background tasks |
| `request_timeout` | `30` | Timeout per API request (seconds) |
| `max_retries` | `3` | Retry attempts on failure |
| `priority` | `1` | Task priority (1=highest, 10=lowest) |

### Performance Impact

**First Request (Cache Empty):**
- Returns partial data: < 1 second
- Background completes: 10-30 seconds
- Subsequent requests use cache: < 0.1 seconds

**Subsequent Requests (Cache Hit):**
- Returns immediately: < 0.1 seconds
- Background refresh (if stale): async, no blocking

### Implementation Status

**Phase 1 (Complete):**
- ✅ NFL scoreboard implemented
- ✅ Background threading architecture
- ✅ Cache integration
- ✅ Error handling and retry logic

**Phase 2 (Planned):**
- ⏳ NCAAFB (college football)
- ⏳ NBA (basketball)
- ⏳ NHL (hockey)
- ⏳ MLB (baseball)

### Error Handling & Fallback

**Automatic Retry:**
- Exponential backoff (1s, 2s, 4s, 8s, ...)
- Maximum retry attempts configurable
- Logs all retry attempts

**Fallback Behavior:**
- If background service disabled: reverts to synchronous fetching
- If background fetch fails: returns cached data
- If no cache: returns empty/error state

### Testing

```bash
# Run background service test
python test_background_service.py

# Check logs for background operations
sudo journalctl -u ledmatrix -f | grep "background"
```

### Monitoring

**View Statistics:**
```python
from src.background_data_service import BackgroundDataService

service = BackgroundDataService()
stats = service.get_statistics()
print(f"Active tasks: {stats['active_tasks']}")
print(f"Completed: {stats['completed']}")
print(f"Failed: {stats['failed']}")
```

**Enable Debug Logging:**
```python
import logging
logging.getLogger('src.background_data_service').setLevel(logging.DEBUG)
```

---

## 5. Permission Management

### Overview

LEDMatrix uses a dual-user architecture: the display service runs as root (hardware access), while the web interface runs as a non-privileged user. Centralized permission management ensures both can access necessary files.

### Why It Matters

**Problem:**
- Root service creates files with root ownership
- Web user cannot read/write those files
- Results in `PermissionError` exceptions

**Solution:**
- Set group ownership to shared group
- Grant group write permissions
- Use setgid bit for automatic inheritance

### Permission Utilities

```python
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_config_file_mode,
    get_assets_file_mode,
    get_plugin_file_mode,
    get_cache_dir_mode
)

# Create directory with correct permissions
ensure_directory_permissions(Path("assets/sports"), get_assets_dir_mode())

# Set file permissions after writing
ensure_file_permissions(Path("config/config.json"), get_config_file_mode())
```

### When to Use Utilities

**Use permission utilities when:**
1. Creating new directories
2. Writing configuration files
3. Downloading/creating asset files (logos, fonts)
4. Creating plugin files
5. Writing cache files

**Don't use for:**
1. Reading files (permissions don't change)
2. Temporary files in `/tmp`
3. Files in already-managed directories (if parent has setgid)

### Permission Standards

**File Permissions:**

| File Type | Mode | Octal | Description |
|-----------|------|-------|-------------|
| Config (main) | `rw-r--r--` | `0o644` | Owner write, all read |
| Config (secrets) | `rw-r-----` | `0o640` | Owner write, group read |
| Assets | `rw-rw-r--` | `0o664` | Owner/group write, all read |
| Plugins | `rw-rw-r--` | `0o664` | Owner/group write, all read |
| Cache files | `rw-rw-r--` | `0o664` | Owner/group write, all read |

**Directory Permissions:**

| Directory Type | Mode | Octal | Description |
|----------------|------|-------|-------------|
| All directories | `rwxrwsr-x` | `0o2775` | With setgid bit for inheritance |

**Note:** The `s` in `rwxrwsr-x` is the setgid bit (2000), which makes new files inherit the directory's group ownership.

### Common Patterns

**Pattern 1: Creating Config Directory**
```python
from pathlib import Path
from src.common.permission_utils import ensure_directory_permissions, get_config_dir_mode

config_dir = Path("config/plugins")
ensure_directory_permissions(config_dir, get_config_dir_mode())
```

**Pattern 2: Saving Config File**
```python
from src.common.permission_utils import ensure_file_permissions, get_config_file_mode

config_path = Path("config/config.json")
with open(config_path, 'w') as f:
    json.dump(data, f)
ensure_file_permissions(config_path, get_config_file_mode())
```

**Pattern 3: Downloading Logo**
```python
from src.common.permission_utils import ensure_directory_permissions, ensure_file_permissions
from src.common.permission_utils import get_assets_dir_mode, get_assets_file_mode

logo_path = Path("assets/sports/nhl/logo.png")
ensure_directory_permissions(logo_path.parent, get_assets_dir_mode())
# ... download and save logo ...
ensure_file_permissions(logo_path, get_assets_file_mode())
```

**Pattern 4: Creating Plugin File**
```python
from src.common.permission_utils import ensure_file_permissions, get_plugin_file_mode

plugin_file = Path("plugins/my-plugin/data.json")
with open(plugin_file, 'w') as f:
    json.dump(data, f)
ensure_file_permissions(plugin_file, get_plugin_file_mode())
```

**Pattern 5: Cache Directory Setup**
```python
from src.common.permission_utils import ensure_directory_permissions, get_cache_dir_mode

cache_dir = Path("cache/plugin-name")
ensure_directory_permissions(cache_dir, get_cache_dir_mode())
```

### Integration with Core Utilities

These core utilities **already handle permissions** - you don't need to call permission utilities when using them:

- **ConfigManager** - Handles config file permissions
- **CacheManager** - Handles cache file permissions
- **LogoHelper** - Handles logo file permissions
- **PluginManager** - Handles plugin file permissions

### Manual Fixes

If you encounter permission issues:

```bash
# Fix all permissions at once
sudo ./scripts/fix_permissions.sh

# Fix specific directory
sudo chown -R ledpi:ledpi /home/ledpi/LEDMatrix/config
sudo chmod -R 2775 /home/ledpi/LEDMatrix/config
sudo find /home/ledpi/LEDMatrix/config -type f -exec chmod 664 {} \;

# Verify permissions
ls -la config/
ls -la assets/
```

### Verification

```bash
# Check directory has setgid bit
ls -ld assets/
# Should show: drwxrwsr-x (note the 's')

# Check file has correct group
ls -l assets/logo.png
# Should show group 'ledpi'

# Check file permissions
stat -c "%a %n" config/config.json
# Should show: 644 config/config.json
```

---

## Related Documentation

- [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md) - Creating plugins with Vegas/on-demand support
- [WEB_INTERFACE_GUIDE.md](WEB_INTERFACE_GUIDE.md) - Using on-demand controls in web UI
- [PLUGIN_API_REFERENCE.md](PLUGIN_API_REFERENCE.md) - Complete API documentation
- [DEVELOPMENT.md](DEVELOPMENT.md) - Development environment and testing
