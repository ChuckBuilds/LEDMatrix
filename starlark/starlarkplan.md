Starlark plan.txt
# Plan: Tidbyt/Tronbyt .star App Integration for LEDMatrix

## Overview

Integrate Tidbyt/Tronbyt `.star` (Starlark) apps into LEDMatrix, enabling users to upload and run hundreds of community apps from the [tronbyt/apps](https://github.com/tronbyt/apps) repository.

## Background

**What are .star files?**
- Written in Starlark (Python-like language)
- Target 64x32 LED matrices (same as LEDMatrix)
- Entry point: `main(config)` returns `render.Root()` widget tree
- Support HTTP requests, caching, animations, and rich rendering widgets

**Render Widgets Available:** Root, Row, Column, Box, Stack, Text, Image, Marquee, Animation, Circle, PieChart, Plot, etc.

**Modules Available:** http, time, cache, json, base64, math, re, html, bsoup, humanize, sunrise, qrcode, etc.

---

## Recommended Approach: Pixlet External Renderer

**Why Pixlet (not native Starlark)?**
1. Reimplementing all Pixlet widgets and modules in Python would be a massive undertaking
2. Pixlet outputs standard WebP/GIF that's easy to display as frames
3. Instant compatibility with all 500+ Tronbyt community apps
4. Pixlet updates automatically benefit our integration

**How it works:**
1. User uploads `.star` file via web UI
2. LEDMatrix plugin calls `pixlet render app.star -o output.webp`
3. Plugin extracts WebP frames and displays them on the LED matrix
4. Configuration is passed via `pixlet render ... -config key=value`

---

## Architecture

```
Web UI                    StarlarkAppsPlugin           Pixlet CLI
  |                              |                         |
  |-- Upload .star file -------->|                         |
  |                              |-- pixlet render ------->|
  |                              |<-- WebP/GIF output -----|
  |                              |                         |
  |                              |-- Extract frames        |
  |                              |-- Display on matrix     |
  |                              |                         |
  |<-- Config UI ----------------|                         |
```

---

## Implementation Plan

### Phase 1: Core Infrastructure

#### 1.1 Create Starlark Apps Plugin
**Files to create:**
- `plugin-repos/starlark-apps/manifest.json`
- `plugin-repos/starlark-apps/config_schema.json`
- `plugin-repos/starlark-apps/manager.py` (StarlarkAppsPlugin class)

**Plugin responsibilities:**
- Manage installed .star apps in `starlark-apps/` directory
- Execute Pixlet to render apps
- Extract and play animation frames
- Register dynamic display modes (one per installed app)

#### 1.2 Pixlet Renderer Module
**File:** `plugin-repos/starlark-apps/pixlet_renderer.py`

```python
class PixletRenderer:
    def check_installed() -> bool
    def render(star_file, config) -> bytes  # Returns WebP
    def extract_schema(star_file) -> dict
```

#### 1.3 Frame Extractor Module
**File:** `plugin-repos/starlark-apps/frame_extractor.py`

```python
class FrameExtractor:
    def load_webp(data: bytes) -> List[Tuple[Image, int]]  # [(frame, delay_ms), ...]
```

Uses PIL to extract frames from WebP animations.

---

### Phase 2: Web UI Integration

#### 2.1 API Endpoints
**Add to:** `web_interface/blueprints/api_v3.py`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v3/starlark/apps` | GET | List installed .star apps |
| `/api/v3/starlark/upload` | POST | Upload a .star file |
| `/api/v3/starlark/apps/<id>` | DELETE | Uninstall an app |
| `/api/v3/starlark/apps/<id>/config` | GET/PUT | Get/update app config |
| `/api/v3/starlark/apps/<id>/preview` | GET | Get rendered preview |
| `/api/v3/starlark/status` | GET | Check Pixlet installation |
| `/api/v3/starlark/browse` | GET | Browse Tronbyt repo |
| `/api/v3/starlark/install-from-repo` | POST | Install from Tronbyt |

#### 2.2 Web UI Components
**Add to:** `web_interface/static/v3/plugins_manager.js` or new file

- Upload button for .star files
- Starlark apps section in plugin manager
- Configuration forms for each app
- Pixlet status indicator

#### 2.3 Tronbyt Repository Browser
**New feature:** Modal to browse and install apps from the Tronbyt community repository.

**Implementation:**
```
+------------------------------------------+
| Browse Tronbyt Apps                   [X] |
+------------------------------------------+
| Search: [________________] [Filter: All v]|
|                                          |
| +--------------------------------------+ |
| | [img] World Clock                    | |
| | Displays multiple world clocks       | |
| | Author: tidbyt  [Install]            | |
| +--------------------------------------+ |
| | [img] Bitcoin Tracker                | |
| | Shows current BTC price              | |
| | Author: community  [Install]         | |
| +--------------------------------------+ |
| | [img] Weather                        | |
| | Current weather conditions           | |
| | Author: tidbyt  [Install]            | |
| +--------------------------------------+ |
|                                          |
| < Prev  Page 1 of 20  Next >             |
+------------------------------------------+
```

**API for browser:**
- `GET /api/v3/starlark/browse?search=clock&category=tools&page=1`
  - Fetches from GitHub API: `https://api.github.com/repos/tronbyt/apps/contents/apps`
  - Parses each app's manifest.yaml for metadata
  - Returns paginated list with name, description, author, category

- `POST /api/v3/starlark/install-from-repo`
  - Body: `{"app_path": "apps/worldclock"}`
  - Downloads .star file and assets from GitHub
  - Extracts schema and creates local config
  - Adds to installed apps manifest

---

### Phase 3: Storage Structure

```
starlark-apps/
  manifest.json              # Registry of installed apps
  world_clock/
    world_clock.star         # The app code
    config.json              # User configuration
    schema.json              # Extracted schema (for UI)
    cached_render.webp       # Cached output
  bitcoin/
    bitcoin.star
    config.json
    schema.json
```

**manifest.json structure:**
```json
{
  "apps": {
    "world_clock": {
      "name": "World Clock",
      "star_file": "world_clock.star",
      "enabled": true,
      "render_interval": 60,
      "display_duration": 15
    }
  }
}
```

---

### Phase 4: Display Integration

#### 4.1 Dynamic Mode Registration
The StarlarkAppsPlugin will register display modes dynamically:
- Each installed app becomes a mode: `starlark_world_clock`, `starlark_bitcoin`, etc.
- These modes appear in the display rotation alongside regular plugins

#### 4.2 Frame Playback
- Extract frames from WebP with their delays
- Play frames at correct timing using display_manager.image
- Handle both static images and animations
- Scale output if display size differs from 64x32

---

## Critical Files to Modify

| File | Changes |
|------|---------|
| `web_interface/blueprints/api_v3.py` | Add starlark API endpoints |
| `web_interface/static/v3/plugins_manager.js` | Add starlark UI section |
| `src/display_controller.py` | Handle starlark display modes |

## New Files to Create

| File | Purpose |
|------|---------|
| `plugin-repos/starlark-apps/manifest.json` | Plugin manifest |
| `plugin-repos/starlark-apps/config_schema.json` | Plugin config schema |
| `plugin-repos/starlark-apps/manager.py` | Main plugin class |
| `plugin-repos/starlark-apps/pixlet_renderer.py` | Pixlet CLI wrapper |
| `plugin-repos/starlark-apps/frame_extractor.py` | WebP frame extraction |
| `starlark-apps/manifest.json` | Installed apps registry |

---

## Pixlet Installation: Bundled Binary

Pixlet will be bundled with LEDMatrix for seamless operation:

**Directory structure:**
```
bin/
  pixlet/
    pixlet-linux-arm64      # For Raspberry Pi
    pixlet-linux-amd64      # For x86_64
    pixlet-windows-amd64.exe # For Windows dev
```

**Implementation:**
1. Download Pixlet binaries from [Tronbyt releases](https://github.com/tronbyt/pixlet/releases) during build/release
2. Auto-detect architecture at runtime and use appropriate binary
3. Set executable permissions on first run if needed
4. Fall back to system PATH if bundled binary fails

**Build script addition:**
```bash
# scripts/download_pixlet.sh
PIXLET_VERSION="v0.33.6"  # Pin to tested version
for arch in linux-arm64 linux-amd64; do
  wget "https://github.com/tronbyt/pixlet/releases/download/${PIXLET_VERSION}/pixlet_${arch}.tar.gz"
  tar -xzf "pixlet_${arch}.tar.gz" -C bin/pixlet/
done
```

**Add to .gitignore:**
```
bin/pixlet/
```

---

## Potential Challenges & Mitigations

| Challenge | Mitigation |
|-----------|------------|
| Pixlet not available for all ARM variants | Bundle Tronbyt fork binaries; auto-detect architecture |
| Slow rendering on Raspberry Pi | Cache rendered output; background rendering; configurable intervals |
| Complex Pixlet schemas (location picker, OAuth) | Start with simple types; link to Tidbyt docs |
| Display size mismatch (128x32 vs 64x32) | Scale with nearest-neighbor; option for centered display |
| Network-dependent apps | Timeout handling; cache last successful render; error indicator |

---

## Verification Plan

1. **Pixlet Integration Test:**
   - Install Pixlet on test system
   - Verify `pixlet render sample.star -o test.webp` works
   - Verify frame extraction from output

2. **Upload Flow Test:**
   - Upload a simple .star file (e.g., hello_world)
   - Verify it appears in installed apps list
   - Verify it appears in display rotation

3. **Animation Test:**
   - Upload an animated app (e.g., analog_clock)
   - Verify frames play at correct timing
   - Verify smooth animation on LED matrix

4. **Configuration Test:**
   - Upload app with schema (e.g., world_clock with location)
   - Verify config UI renders correctly
   - Verify config changes affect rendered output

5. **Repository Browse Test:**
   - Open Tronbyt browse modal
   - Search for and install an app
   - Verify it works correctly

---

## Sources

- [Pixlet GitHub](https://github.com/tidbyt/pixlet)
- [Pixlet Widgets Documentation](https://github.com/tidbyt/pixlet/blob/main/docs/widgets.md)
- [Pixlet Modules Documentation](https://github.com/tidbyt/pixlet/blob/main/docs/modules.md)
- [Tronbyt Apps Repository](https://github.com/tronbyt/apps)
- [Tronbyt Pixlet Fork](https://github.com/tronbyt/pixlet)