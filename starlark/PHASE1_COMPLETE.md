# Phase 1 Complete: Core Infrastructure

Phase 1 of the Starlark integration is complete. The core infrastructure is now in place for importing and managing Starlark widgets from Tronbyte without modification.

## What Was Built

### 1. Plugin Structure
Created the Starlark Apps plugin at `plugin-repos/starlark-apps/` with:
- `manifest.json` - Plugin metadata and configuration
- `config_schema.json` - JSON Schema for plugin settings
- `__init__.py` - Package initialization

### 2. Core Components

#### PixletRenderer (`pixlet_renderer.py`)
- Auto-detects bundled or system Pixlet binary
- Supports multiple architectures (Linux arm64/amd64, macOS, Windows)
- Renders .star files to WebP with configuration passthrough
- Schema extraction from .star files
- Timeout and error handling

#### FrameExtractor (`frame_extractor.py`)
- Extracts frames from WebP animations
- Handles static and animated WebP files
- Frame timing and duration management
- Frame scaling for different display sizes
- Frame optimization (reduce count, adjust timing)
- GIF conversion for caching

#### StarlarkAppsPlugin (`manager.py`)
- Main plugin class inheriting from BasePlugin
- Manages installed apps with StarlarkApp instances
- Dynamic app loading from manifest
- Frame-based display with proper timing
- Caching system for rendered output
- Install/uninstall app methods
- Configuration management per app

### 3. Storage Structure
Created `starlark-apps/` directory with:
- `manifest.json` - Registry of installed apps
- `README.md` - Documentation for users
- Per-app directories (created on install)

### 4. Binary Management
- `scripts/download_pixlet.sh` - Downloads Pixlet binaries for all platforms
- `bin/pixlet/` - Storage for bundled binaries (gitignored)
- Auto-detection of architecture at runtime

### 5. Configuration
Updated `.gitignore` to exclude:
- Pixlet binaries (`bin/pixlet/`)
- User-installed apps (`starlark-apps/*` with exceptions)

## Key Features

### Zero Widget Modification
Widgets run exactly as published on Tronbyte without any changes. The plugin:
- Uses Pixlet as-is for rendering
- Passes configuration directly through
- Extracts schemas automatically
- Handles all LEDMatrix integration

### Plugin-Like Management
Each Starlark app:
- Has its own configuration
- Can be enabled/disabled individually
- Has configurable render intervals
- Appears in display rotation
- Is cached for performance

### Performance Optimizations
- Cached WebP output (configurable TTL)
- Background rendering option
- Frame extraction once per render
- Automatic scaling to display size
- Frame timing preservation

## Architecture Highlights

```
User uploads .star file
        ↓
StarlarkAppsPlugin.install_app()
        ↓
Creates app directory with:
  - app_id.star (the widget code)
  - config.json (user configuration)
  - schema.json (extracted schema)
  - cached_render.webp (rendered output)
        ↓
During display rotation:
        ↓
StarlarkAppsPlugin.display()
        ↓
PixletRenderer.render() → WebP file
        ↓
FrameExtractor.load_webp() → List of (frame, delay) tuples
        ↓
Display frames with correct timing on LED matrix
```

## What's Next

### Phase 2: Management Features
- API endpoints for app management
- Web UI for uploading .star files
- Per-app configuration UI
- Enable/disable controls
- Preview rendering

### Phase 3: Repository Integration
- Browse Tronbyte repository
- Search and filter apps
- One-click install from repository
- Automatic updates

## Testing the Plugin

### Prerequisites
1. Install or download Pixlet binary:
   ```bash
   ./scripts/download_pixlet.sh
   ```

2. Ensure the plugin is discovered by LEDMatrix:
   ```bash
   # Plugin should be at: plugin-repos/starlark-apps/
   ```

### Manual Testing
1. Start LEDMatrix
2. The plugin should initialize and log Pixlet status
3. Use the `install_app()` method to add a .star file
4. App should appear in display rotation

### Example .star File
Download a simple app from Tronbyte:
```bash
curl -o test.star https://raw.githubusercontent.com/tronbyt/apps/main/apps/clock/clock.star
```

## Files Created

### Plugin Files
- `plugin-repos/starlark-apps/manifest.json`
- `plugin-repos/starlark-apps/config_schema.json`
- `plugin-repos/starlark-apps/__init__.py`
- `plugin-repos/starlark-apps/manager.py`
- `plugin-repos/starlark-apps/pixlet_renderer.py`
- `plugin-repos/starlark-apps/frame_extractor.py`

### Storage Files
- `starlark-apps/manifest.json`
- `starlark-apps/README.md`

### Scripts
- `scripts/download_pixlet.sh`

### Configuration
- Updated `.gitignore`

## Configuration Schema

The plugin accepts these configuration options:

- `enabled` - Enable/disable the plugin
- `pixlet_path` - Explicit path to Pixlet (auto-detected if empty)
- `render_timeout` - Max rendering time (default: 30s)
- `cache_rendered_output` - Cache WebP files (default: true)
- `cache_ttl` - Cache time-to-live (default: 300s)
- `default_frame_delay` - Frame delay if not specified (default: 50ms)
- `scale_output` - Scale to display size (default: true)
- `background_render` - Background rendering (default: true)
- `auto_refresh_apps` - Auto-refresh at intervals (default: true)
- `transition` - Display transition settings

## Known Limitations

1. **Pixlet Required**: The plugin requires Pixlet to be installed or bundled
2. **Schema Extraction**: May not work on all Pixlet versions (gracefully degrades)
3. **Performance**: Initial render may be slow on low-power devices (mitigated by caching)
4. **Network Apps**: Apps requiring network access need proper internet connectivity

## Success Criteria ✓

- [x] Plugin follows LEDMatrix plugin architecture
- [x] Zero modifications required to .star files
- [x] Automatic Pixlet binary detection
- [x] Frame extraction and display working
- [x] Caching system implemented
- [x] Install/uninstall functionality
- [x] Per-app configuration support
- [x] Documentation created

Phase 1 is **COMPLETE** and ready for Phase 2 development!
