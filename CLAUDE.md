# LEDMatrix — AI Assistant Reference

## Project Overview

LEDMatrix is a Raspberry Pi-based LED matrix display controller with a plugin architecture, web UI, and optional Vegas-style continuous scroll mode. It runs two services: a display controller (`run.py`) and a web interface (`web_interface/app.py`).

---

## Directory Structure

```text
LEDMatrix/
├── run.py                          # Main entry point (display controller)
├── display_controller.py           # Legacy top-level shim (do not modify)
├── requirements.txt                # Core Python dependencies
├── requirements-emulator.txt       # Emulator-only dependencies
├── pytest.ini                      # Test configuration
├── mypy.ini                        # Type checking configuration
├── config/
│   ├── config.json                 # Runtime config (gitignored, user-created)
│   ├── config.template.json        # Template to copy for new installations
│   └── config_secrets.json         # API keys (gitignored, user-created)
├── src/
│   ├── display_controller.py       # DisplayController class (core loop)
│   ├── display_manager.py          # DisplayManager (singleton, wraps rgbmatrix)
│   ├── config_manager.py           # ConfigManager (loads/saves config)
│   ├── config_manager_atomic.py    # Atomic write + backup/rollback support
│   ├── config_service.py           # ConfigService (hot-reload wrapper)
│   ├── cache_manager.py            # CacheManager (memory + disk cache)
│   ├── font_manager.py             # FontManager (TTF/BDF font loading)
│   ├── logging_config.py           # Centralized logging (get_logger)
│   ├── exceptions.py               # Custom exceptions (PluginError, CacheError, ...)
│   ├── startup_validator.py        # Startup configuration validation
│   ├── wifi_manager.py             # WiFi management
│   ├── layout_manager.py           # Layout helpers
│   ├── image_utils.py              # PIL image utilities
│   ├── vegas_mode/                 # Vegas scroll mode subsystem
│   │   ├── coordinator.py          # VegasModeCoordinator (main orchestrator)
│   │   ├── config.py               # VegasModeConfig dataclass
│   │   ├── plugin_adapter.py       # Adapts plugins for Vegas rendering
│   │   ├── render_pipeline.py      # High-FPS render loop
│   │   └── stream_manager.py       # Content stream management
│   ├── plugin_system/
│   │   ├── base_plugin.py          # BasePlugin ABC + VegasDisplayMode enum
│   │   ├── plugin_manager.py       # PluginManager (discovery, loading, lifecycle)
│   │   ├── plugin_loader.py        # Module-level loading + dep installation
│   │   ├── plugin_executor.py      # Isolated execution with timeouts
│   │   ├── plugin_state.py         # PluginState enum + PluginStateManager
│   │   ├── store_manager.py        # PluginStoreManager (install/update/remove)
│   │   ├── schema_manager.py       # JSON Schema validation for plugin configs
│   │   ├── operation_queue.py      # PluginOperationQueue (serialized ops)
│   │   ├── operation_types.py      # OperationType, OperationStatus enums
│   │   ├── operation_history.py    # Persistent operation history
│   │   ├── state_manager.py        # State manager for web UI
│   │   ├── state_reconciliation.py # Reconciles plugin state with config
│   │   ├── health_monitor.py       # Plugin health monitoring
│   │   ├── resource_monitor.py     # Resource usage tracking
│   │   ├── saved_repositories.py   # SavedRepositoriesManager (custom repos)
│   │   └── testing/
│   │       ├── mocks.py            # MockDisplayManager, MockCacheManager, etc.
│   │       └── plugin_test_base.py # PluginTestCase base class
│   ├── base_classes/               # Reusable base classes for sport plugins
│   │   ├── sports.py               # SportsCore ABC
│   │   ├── baseball.py / basketball.py / football.py / hockey.py
│   │   ├── api_extractors.py       # APIDataExtractor base
│   │   └── data_sources.py         # DataSource base
│   ├── common/                     # Shared utilities for plugins
│   │   ├── display_helper.py       # DisplayHelper (image layouts, compositing)
│   │   ├── scroll_helper.py        # ScrollHelper (smooth scrolling)
│   │   ├── text_helper.py          # TextHelper (text rendering, wrapping)
│   │   ├── logo_helper.py          # LogoHelper (team logos)
│   │   ├── game_helper.py          # GameHelper (sport game utilities)
│   │   ├── api_helper.py           # APIHelper (HTTP with retry)
│   │   ├── config_helper.py        # ConfigHelper (config access utilities)
│   │   ├── error_handler.py        # ErrorHandler (common error patterns)
│   │   ├── utils.py                # General utilities
│   │   └── permission_utils.py     # File permission utilities
│   ├── cache/                      # Cache subsystem components
│   │   ├── memory_cache.py         # In-memory LRU cache
│   │   ├── disk_cache.py           # Disk-backed cache
│   │   ├── cache_strategy.py       # TTL strategy per sport/source
│   │   └── cache_metrics.py        # Hit/miss metrics
│   └── web_interface/              # Web API helpers (not Flask app itself)
│       ├── api_helpers.py          # success_response(), error_response()
│       ├── validators.py           # Input validation + sanitization
│       ├── errors.py               # ErrorCode enum
│       └── logging_config.py       # Web-specific logging helpers
├── web_interface/                  # Flask web application
│   ├── app.py                      # Flask app factory + manager initialization
│   ├── start.py                    # WSGI entry point
│   ├── blueprints/
│   │   ├── api_v3.py               # REST API (base URL: /api/v3)
│   │   └── pages_v3.py             # Server-rendered HTML pages
│   ├── templates/v3/               # Jinja2 templates
│   │   ├── base.html / index.html
│   │   └── partials/               # HTMX partial templates
│   └── static/v3/
│       ├── app.js / app.css
│       └── js/
│           ├── widgets/            # Custom web components (Alpine.js based)
│           └── plugins/            # Plugin management JS modules
├── plugins/                        # Installed plugins (gitignored)
├── plugin-repos/                   # Dev symlinks to monorepo plugin dirs
│   └── web-ui-info/                # Built-in info plugin
├── assets/
│   ├── fonts/                      # BDF and TTF fonts
│   ├── broadcast_logos/            # Network logos (PNG)
│   ├── news_logos/                 # News channel logos
│   └── sports/                     # Team logos by sport (PNG)
├── schema/
│   └── manifest_schema.json        # JSON Schema for manifest.json validation
├── systemd/                        # systemd service templates
│   ├── ledmatrix.service           # Display controller service (runs as root)
│   └── ledmatrix-web.service       # Web interface service (runs as root)
├── scripts/
│   ├── dev/
│   │   ├── run_emulator.sh         # Launch with RGBMatrixEmulator
│   │   └── dev_plugin_setup.sh     # Set up plugin-repos symlinks
│   ├── install/                    # Installation scripts
│   └── fix_perms/                  # Permission fix utilities
├── test/                           # Test suite (pytest)
│   ├── conftest.py
│   ├── plugins/                    # Per-plugin test files
│   └── web_interface/              # Web interface tests
└── docs/                           # Extended documentation
```

---

## Running the Application

### Development (emulator mode)
```bash
python run.py --emulator          # Run with RGBMatrixEmulator (pygame)
python run.py --emulator --debug  # With verbose debug logging
```

### Production (Raspberry Pi)
```bash
python run.py                     # Hardware mode (requires root for GPIO)
sudo python run.py                # With root for GPIO access
```

### Web Interface
```bash
python web_interface/start.py     # Start web UI (port 5000)
# or
bash web_interface/run.sh
```

### Systemd Services
```bash
sudo systemctl start ledmatrix         # Display controller
sudo systemctl start ledmatrix-web     # Web interface
sudo journalctl -u ledmatrix -f        # Follow display logs
sudo journalctl -u ledmatrix-web -f    # Follow web logs
```

**Important**: The display service runs as `root` (GPIO requires it). The web service also runs as root but should be treated as a local-only application.

---

## Configuration

### Main Config: `config/config.json`
Copy from `config/config.template.json`. Key sections:

```json
{
  "timezone": "America/Chicago",
  "location": { "city": "Dallas", "state": "Texas", "country": "US" },
  "display": {
    "hardware": {
      "rows": 32, "cols": 64, "chain_length": 2, "brightness": 90,
      "hardware_mapping": "adafruit-hat-pwm"
    },
    "runtime": { "gpio_slowdown": 3 },
    "vegas_scroll": { "enabled": false, "scroll_speed": 50 }
  },
  "plugin_system": {
    "plugins_directory": "plugins",
    "auto_discover": true
  },
  "schedule": { "enabled": true, "start_time": "07:00", "end_time": "23:00" }
}
```

### Secrets Config: `config/config_secrets.json`
Copy from `config/config_secrets.template.json`. Contains API keys:
- `ledmatrix-weather.api_key` — OpenWeatherMap
- `music.SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`
- `github.api_token` — For private plugin repos / higher rate limits
- `youtube.api_key` / `channel_id`

Plugin configs are stored inside `config/config.json` under their `plugin_id` key, NOT in the plugin directories. This persists across reinstalls.

### Hot Reload
Config can be reloaded without restart. Set `LEDMATRIX_HOT_RELOAD=false` to disable.

---

## Plugin System

### Plugin Lifecycle
```text
UNLOADED → LOADED → ENABLED → RUNNING → (back to ENABLED)
                    ↓
                  ERROR
                    ↓
                DISABLED
```

### BasePlugin Contract
All plugins must inherit from `BasePlugin` in `src/plugin_system/base_plugin.py`:

```python
from src.plugin_system.base_plugin import BasePlugin

class MyPlugin(BasePlugin):
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        # self.logger is automatically configured via get_logger()
        # self.config, self.enabled, self.plugin_id are set by super()

    def update(self) -> None:
        """Called on update_interval. Fetch data, populate cache."""
        ...

    def display(self, force_clear: bool = False) -> None:
        """Called during rotation. Render to display_manager."""
        ...
```

**Required abstract methods**: `update()` and `display(force_clear=False)`

**Optional overrides** (see base_plugin.py for full list):
- `validate_config()` — Extra config validation
- `cleanup()` — Release resources on unload
- `on_config_change(new_config)` — Hot-reload support
- `has_live_content()` / `has_live_priority()` — Live priority takeover
- `get_vegas_content()` / `get_vegas_display_mode()` — Vegas mode integration
- `is_cycle_complete()` / `reset_cycle_state()` — Dynamic display duration
- `get_info()` — Web UI status display

### Plugin File Structure
```text
plugins/<plugin_id>/
├── manifest.json        # Plugin metadata (required)
├── config_schema.json   # JSON Schema Draft-7 for config (required)
├── manager.py           # Plugin class (required, entry_point in manifest)
└── requirements.txt     # Plugin-specific pip dependencies
```

### manifest.json Fields
```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "entry_point": "manager.py",
  "class_name": "MyPlugin",
  "category": "custom",
  "update_interval": 60,
  "default_duration": 15,
  "display_modes": ["my-plugin"],
  "min_ledmatrix_version": "2.0.0"
}
```

### config_schema.json
Use JSON Schema Draft-7. Standard properties every plugin should include:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "enabled": { "type": "boolean", "default": true },
    "display_duration": { "type": "number", "default": 15, "minimum": 1 },
    "live_priority": { "type": "boolean", "default": false }
  },
  "required": ["enabled"],
  "additionalProperties": false
}
```

### Display Dimensions
Always read dynamically — never hardcode matrix dimensions:
```python
width = self.display_manager.matrix.width   # e.g., 128 (64 * chain_length)
height = self.display_manager.matrix.height  # e.g., 32
```

### Caching in Plugins
```python
def update(self):
    cache_key = f"{self.plugin_id}_data"
    cached = self.cache_manager.get(cache_key, max_age=3600)
    if cached:
        self.data = cached
        return
    self.data = self._fetch_from_api()
    self.cache_manager.set(cache_key, self.data, ttl=3600)
    # For stale fallback on API failure:
    # self.cache_manager.get(cache_key, max_age=31536000)
```

---

## Plugin Store Architecture

- Official plugins live in the `ledmatrix-plugins` monorepo (not individual repos)
- Registry URL: `https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugins/main/plugins.json`
- `PluginStoreManager` (`src/plugin_system/store_manager.py`) handles all install/update/uninstall
- Monorepo plugins install via ZIP extraction — no `.git` directory present
- Update detection uses version comparison: manifest `version` vs registry `latest_version`
- Third-party plugins use their own GitHub repo URL with empty `plugin_path`
- Plugin configs in `config/config.json` under the plugin ID key — safe across reinstalls

**Monorepo development workflow**: When modifying a plugin in the monorepo, you MUST:
1. Bump `version` in `manifest.json`
2. Run `python update_registry.py` in the monorepo root
Skipping either step means users won't receive the update.

---

## Vegas Scroll Mode

A continuous horizontal scroll that combines all plugin content. Configured under `display.vegas_scroll` in `config.json`.

### Plugin Vegas Integration
Three display modes (set via `get_vegas_display_mode()` or config `vegas_mode`):
- `VegasDisplayMode.SCROLL` — Content scrolls continuously (sports scores, news tickers)
- `VegasDisplayMode.FIXED_SEGMENT` — Fixed-width block scrolls past (clock, weather)
- `VegasDisplayMode.STATIC` — Scroll pauses, plugin displays for its duration, resumes

```python
from src.plugin_system.base_plugin import VegasDisplayMode

def get_vegas_display_mode(self):
    return VegasDisplayMode.SCROLL

def get_vegas_content(self):
    # Return PIL Image or list of PIL Images, or None to capture display()
    return [self._render_game(game) for game in self.games]

def get_vegas_segment_width(self):
    # For FIXED_SEGMENT: number of panels to occupy
    return self.config.get("vegas_panel_count", 2)
```

---

## Web Interface

- Flask app at `web_interface/app.py`; REST API at `web_interface/blueprints/api_v3.py`
- Base URL: `http://<pi-ip>:5000/api/v3`
- Uses HTMX + Alpine.js for reactive UI without a full SPA framework
- All API responses follow the standard envelope:
  ```json
  { "status": "success" | "error", "data": {...}, "message": "..." }
  ```
- Use `src/web_interface/api_helpers.py`: `success_response()`, `error_response()`
- Plugin operations are serialized via `PluginOperationQueue` to prevent conflicts

### Key API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v3/config/main` | Read main config |
| POST | `/api/v3/config/main` | Save main config |
| GET | `/api/v3/plugins` | List all plugins |
| POST | `/api/v3/plugins/<id>/install` | Install plugin |
| POST | `/api/v3/plugins/<id>/uninstall` | Uninstall plugin |
| GET | `/api/v3/plugins/<id>/config` | Get plugin config |
| POST | `/api/v3/plugins/<id>/config` | Save plugin config |
| GET | `/api/v3/store/registry` | Browse plugin store |
| POST | `/api/v3/display/restart` | Restart display service |
| GET | `/api/v3/system/logs` | Get system logs |

---

## Logging

Always use `get_logger()` from `src.logging_config` — never `logging.getLogger()` directly in plugins or core src code.

```python
from src.logging_config import get_logger

# In a plugin (plugin_id context automatically added):
self.logger = get_logger(f"plugin.{plugin_id}", plugin_id=plugin_id)
# This is done automatically by BasePlugin.__init__

# In core src modules:
logger = get_logger(__name__)
```

Log level guidelines:
- `logger.info()` — Normal operations, status updates
- `logger.debug()` — Detailed troubleshooting info
- `logger.warning()` — Non-critical issues
- `logger.error()` — Problems requiring attention

Use consistent prefixes in messages: `[PluginName] message`, `[NHL Live] fetching data`

---

## Testing

### Running Tests
```bash
pytest                      # Full test suite with coverage
pytest test/plugins/        # Plugin tests only
pytest test/test_cache_manager.py  # Single file
pytest -k "test_update"     # Filter by name
pytest --no-cov             # Skip coverage (faster)
```

### Writing Plugin Tests
Use `PluginTestCase` from `src.plugin_system.testing.plugin_test_base`:

```python
from src.plugin_system.testing.plugin_test_base import PluginTestCase

class TestMyPlugin(PluginTestCase):
    def test_initialization(self):
        plugin = self.create_plugin_instance(MyPlugin)
        self.assertTrue(plugin.enabled)

    def test_update_uses_cache(self):
        plugin = self.create_plugin_instance(MyPlugin)
        self.cache_manager.set("my-plugin_data", {"key": "val"})
        plugin.update()
        # verify plugin.data was loaded from cache
```

Available mocks: `MockDisplayManager(width, height)`, `MockCacheManager`, `MockConfigManager`, `MockPluginManager`

### Test Markers
```python
@pytest.mark.unit        # Fast, isolated
@pytest.mark.integration # Slower, may need external services
@pytest.mark.hardware    # Requires actual Raspberry Pi hardware
@pytest.mark.plugin      # Plugin-related
```

---

## Coding Standards

### Naming
- Classes: `PascalCase` (e.g., `MyScoreboardPlugin`)
- Functions/variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private methods: `_leading_underscore`

### Python Patterns
- Type hints on all public function signatures
- Specific exception types — never bare `except:`
- Docstrings on all classes and non-trivial methods
- Provide sensible defaults in code, not config

### Manager/Plugin Pattern
```python
class MyPlugin(BasePlugin):
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(...)       # Always call super first
        # Load config values with defaults
        self.my_setting = config.get("my_setting", "default")

    def update(self):               # Fetch/process data
        ...

    def display(self, force_clear=False):  # Render to matrix
        ...
```

---

## Common Pitfalls

- **paho-mqtt 2.x**: Needs `callback_api_version=mqtt.CallbackAPIVersion.VERSION1` for v1 compat
- **BasePlugin logger**: Use `get_logger()` from `src.logging_config`, not `logging.getLogger()`
- **Monorepo plugin updates**: Must bump `manifest.json` version AND run `python update_registry.py`
- **Display dimensions**: Read from `self.display_manager.matrix.width/height` — never hardcode
- **`sys.dont_write_bytecode = True`** is set in `run.py`: root-owned `__pycache__` files block web service (non-root) from updating plugins
- **Config path**: ConfigManager defaults to `config/config.json` relative to CWD — must run from project root
- **Plugin configs**: Stored in `config/config.json` under the plugin ID key, NOT inside plugin directories
- **Operation serialization**: Plugin install/uninstall/update goes through `PluginOperationQueue` — don't call store manager directly from web handlers
- **DisplayManager is a singleton**: Don't create multiple instances; use the existing one passed to plugins
- **Secret keys**: Store in `config/config_secrets.json` (gitignored) — never commit API keys

---

## Development Workflow

### Creating a New Plugin
1. Copy `.cursor/plugin_templates/` into `plugin-repos/<plugin-id>/`
2. Fill in `manifest.json` (set `id`, `name`, `version`, `class_name`, `display_modes`)
3. Fill in `config_schema.json` with your plugin's settings
4. Implement `manager.py` inheriting from `BasePlugin`
5. Add deps to `requirements.txt`
6. Symlink for dev: `python scripts/setup_plugin_repos.py`
7. Test: `pytest test/plugins/test_<plugin_id>.py`

### Emulator Development (non-Pi)
```bash
pip install -r requirements-emulator.txt
python run.py --emulator
```

### Pre-commit Hooks
```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

### Type Checking
```bash
mypy src/ --config-file mypy.ini
```

---

## Key Source Files for Common Tasks

| Task | File |
|------|------|
| Add a new plugin | `src/plugin_system/base_plugin.py` (extend) |
| Change display rotation | `src/display_controller.py` |
| Add web API endpoint | `web_interface/blueprints/api_v3.py` |
| Add web UI page/partial | `web_interface/blueprints/pages_v3.py` + `templates/v3/` |
| Add a UI widget | `web_interface/static/v3/js/widgets/` |
| Modify config schema | `config/config.template.json` |
| Add a custom exception | `src/exceptions.py` |
| Change cache behavior | `src/cache/cache_strategy.py` |
| Vegas mode rendering | `src/vegas_mode/render_pipeline.py` |
| Plugin store operations | `src/plugin_system/store_manager.py` |
