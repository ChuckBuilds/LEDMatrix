# Starlark Apps Guide

## Overview

The Starlark Apps plugin for LEDMatrix enables you to run **Tidbyt/Tronbyte community apps** on your LED matrix display without modification. This integration allows you to access hundreds of pre-built widgets and apps from the vibrant Tidbyt community ecosystem.

## Important: Third-Party Content

**⚠️ Apps are NOT managed by the LEDMatrix project**

- Starlark apps are developed and maintained by the **Tidbyt/Tronbyte community**
- LEDMatrix provides the runtime environment but does **not** create, maintain, or support these apps
- All apps originate from the [Tronbyte Apps Repository](https://github.com/tronbyt/apps)
- App quality, functionality, and security are the responsibility of individual app authors
- LEDMatrix is not affiliated with Tidbyt Inc. or the Tronbyte project

## What is Starlark?

[Starlark](https://github.com/bazelbuild/starlark) is a Python-like language originally developed by Google for the Bazel build system. Tidbyt adopted Starlark for building LED display apps because it's:

- **Sandboxed**: Apps run in a safe, restricted environment
- **Simple**: Python-like syntax that's easy to learn
- **Deterministic**: Apps produce consistent output
- **Fast**: Compiled and optimized for performance

## How It Works

### Architecture

```text
┌─────────────────────────────────────────────────────────┐
│                    LEDMatrix System                      │
│  ┌────────────────────────────────────────────────────┐ │
│  │         Starlark Apps Plugin (manager.py)          │ │
│  │  • Manages app lifecycle (install/uninstall)       │ │
│  │  • Handles app configuration                       │ │
│  │  • Schedules app rendering                         │ │
│  └─────────────────┬──────────────────────────────────┘ │
│                    │                                     │
│  ┌─────────────────▼──────────────────────────────────┐ │
│  │      Pixlet Renderer (pixlet_renderer.py)          │ │
│  │  • Executes .star files using Pixlet CLI           │ │
│  │  • Extracts configuration schemas                  │ │
│  │  • Outputs WebP animations                         │ │
│  └─────────────────┬──────────────────────────────────┘ │
│                    │                                     │
│  ┌─────────────────▼──────────────────────────────────┐ │
│  │      Frame Extractor (frame_extractor.py)          │ │
│  │  • Decodes WebP animations into frames             │ │
│  │  • Scales/centers output for display size          │ │
│  │  • Manages frame timing                            │ │
│  └─────────────────┬──────────────────────────────────┘ │
│                    │                                     │
│  ┌─────────────────▼──────────────────────────────────┐ │
│  │            LED Matrix Display                       │ │
│  │  • Renders final output to physical display        │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘

                    ▲
                    │
         Downloads apps from
                    │
┌───────────────────┴─────────────────────────────────────┐
│         Tronbyte Apps Repository (GitHub)                │
│  • 974+ community-built apps                             │
│  • Weather, sports, stocks, games, clocks, etc.          │
│  • https://github.com/tronbyt/apps                       │
└──────────────────────────────────────────────────────────┘
```

### Rendering Pipeline

1. **User installs app** from the Tronbyte repository via web UI
2. **Plugin downloads** the `.star` file (and any assets like images/fonts)
3. **Schema extraction** parses configuration options from the `.star` source
4. **User configures** the app through the web UI (timezone, location, API keys, etc.)
5. **Pixlet renders** the app with user config → produces WebP animation
6. **Frame extraction** decodes WebP → individual PIL Image frames
7. **Display scaling** adapts 64x32 Tidbyt output to your matrix size
8. **Rotation** cycles through your installed apps based on schedule

## Getting Started

### 1. Install Pixlet

Pixlet is the rendering engine that executes Starlark apps. The plugin will attempt to use:

1. **Bundled binary** (recommended): Downloaded to `bin/pixlet/pixlet-{platform}-{arch}`
2. **System installation**: If `pixlet` is available in your PATH

#### Auto-Install via Web UI

Navigate to: **Plugins → Starlark Apps → Status → Install Pixlet**

This runs the bundled installation script which downloads the appropriate binary for your platform.

#### Manual Installation

```bash
cd /path/to/LEDMatrix
bash scripts/download_pixlet.sh
```

Verify installation:
```bash
./bin/pixlet/pixlet-linux-amd64 version
# Pixlet 0.50.2 (or later)
```

### 2. Enable the Starlark Apps Plugin

1. Open the web UI
2. Navigate to **Plugins**
3. Find **Starlark Apps** in the installed plugins list
4. Enable the plugin
5. Configure settings:
   - **Magnify**: Auto-calculated based on your display size (or set manually)
   - **Render Interval**: How often apps re-render (default: 300s)
   - **Display Duration**: How long each app shows (default: 15s)
   - **Cache Output**: Enable to reduce re-rendering (recommended)

### 3. Browse and Install Apps

1. Navigate to **Plugins → Starlark Apps → App Store**
2. Browse available apps (974+ options)
3. Filter by category: Weather, Sports, Finance, Games, Clocks, etc.
4. Click **Install** on desired apps
5. Configure each app:
   - Set location/timezone
   - Enter API keys if required
   - Customize display preferences

### 4. Configure Apps

Each app may have different configuration options:

#### Common Configuration Types

- **Location** (lat/lng/timezone): For weather, clocks, transit
- **API Keys**: For services like weather, stocks, sports scores
- **Display Preferences**: Colors, units, layouts
- **Dropdown Options**: Team selections, language, themes
- **Toggles**: Enable/disable features

Configuration is stored in `starlark-apps/{app-id}/config.json` and persists across app updates.

## App Sources and Categories

All apps are sourced from the [Tronbyte Apps Repository](https://github.com/tronbyt/apps). Popular categories include:

### 🌤️ Weather
- Analog Clock (with weather)
- Current Weather
- Weather Forecast
- Air Quality Index

### 🏈 Sports
- NFL Scores
- NBA Scores
- MLB Scores
- NHL Scores
- Soccer/Football Scores
- Formula 1 Results

### 💰 Finance
- Stock Tickers
- Cryptocurrency Prices
- Market Indices

### 🎮 Games & Fun
- Conway's Game of Life
- Pong
- Nyan Cat
- Retro Animations

### 🕐 Clocks
- Analog Clock
- Fuzzy Clock
- Binary Clock
- Word Clock

### 📰 Information
- News Headlines
- RSS Feeds
- GitHub Activity
- Reddit Feed

### 🚌 Transit & Travel
- Transit Arrivals
- Flight Tracker
- Train Schedules

## Display Size Compatibility

Tronbyte/Tidbyt apps are designed for **64×32 displays**. LEDMatrix automatically adapts content for different display sizes:

### Magnification

The plugin calculates optimal magnification based on your display:

```text
magnify = floor(min(display_width / 64, display_height / 32))
```

Examples:
- **64×32**: magnify = 1 (native, pixel-perfect)
- **128×64**: magnify = 2 (2x scaling, crisp)
- **192×64**: magnify = 2 (2x + horizontal centering)
- **256×64**: magnify = 2 (2x + centering)

### Scaling Modes

**Config → Starlark Apps → Scale Method:**
- `nearest` (default): Sharp pixels, retro look
- `bilinear`: Smooth scaling, slight blur
- `bicubic`: Higher quality smooth scaling
- `lanczos`: Best quality, most processing

**Center vs Scale:**
- `scale_output=true`: Stretch to fill display (may distort aspect ratio)
- `center_small_output=true`: Center output without stretching (preserves aspect ratio)

## Configuration Schema Extraction

LEDMatrix automatically extracts configuration schemas from Starlark apps by parsing the `get_schema()` function in the `.star` source code.

### Supported Field Types

| Starlark Type | Web UI Rendering |
|--------------|------------------|
| `schema.Location` | Lat/Lng/Timezone picker |
| `schema.Text` | Text input field |
| `schema.Toggle` | Checkbox/switch |
| `schema.Dropdown` | Select dropdown |
| `schema.Color` | Color picker |
| `schema.DateTime` | Date/time picker |
| `schema.OAuth2` | Warning message (not supported) |
| `schema.PhotoSelect` | Warning message (not supported) |
| `schema.LocationBased` | Text fallback with note |
| `schema.Typeahead` | Text fallback with note |

### Schema Coverage

- **90-95%** of apps: Full schema support
- **5%**: Partial extraction (complex/dynamic schemas)
- **<1%**: No schema (apps without configuration)

Apps without extracted schemas can still run with default settings.

## File Structure

```text
LEDMatrix/
├── plugin-repos/starlark-apps/     # Plugin source code
│   ├── manager.py                  # Main plugin logic
│   ├── pixlet_renderer.py          # Pixlet CLI wrapper
│   ├── frame_extractor.py          # WebP decoder
│   ├── tronbyte_repository.py      # GitHub API client
│   └── requirements.txt            # Python dependencies
│
├── starlark-apps/                  # Installed apps (user data)
│   ├── manifest.json               # App registry
│   │
│   └── analogclock/                # Example app
│       ├── analogclock.star        # Starlark source
│       ├── config.json             # User configuration
│       ├── schema.json             # Extracted schema
│       ├── cached_render.webp      # Rendered output cache
│       └── images/                 # App assets (if any)
│           ├── hour_hand.png
│           └── minute_hand.png
│
├── bin/pixlet/                     # Pixlet binaries
│   ├── pixlet-linux-amd64
│   ├── pixlet-linux-arm64
│   └── pixlet-darwin-arm64
│
└── scripts/
    └── download_pixlet.sh          # Pixlet installer
```

## API Keys and External Services

Many apps require API keys for external services:

### Common API Services

- **Weather**: OpenWeatherMap, Weather.gov, Dark Sky
- **Sports**: ESPN, The Sports DB, SportsData.io
- **Finance**: Alpha Vantage, CoinGecko, Yahoo Finance
- **Transit**: TransitLand, NextBus, local transit APIs
- **News**: NewsAPI, Reddit, RSS feeds

### Security Note

- API keys are stored in `config.json` files on disk
- The LEDMatrix web interface does NOT encrypt API keys
- Ensure your Raspberry Pi is on a trusted network
- Use read-only or limited-scope API keys when possible
- **Never commit `starlark-apps/*/config.json` to version control**

## Troubleshooting

### Pixlet Not Found

**Symptom**: "Pixlet binary not found" error

**Solutions**:
1. Run auto-installer: **Plugins → Starlark Apps → Install Pixlet**
2. Manual install: `bash scripts/download_pixlet.sh`
3. Check permissions: `chmod +x bin/pixlet/pixlet-*`
4. Verify architecture: `uname -m` matches binary name

### App Fails to Render

**Symptom**: "Rendering failed" error in logs

**Solutions**:
1. Check logs: `journalctl -u ledmatrix | grep -i pixlet`
2. Verify config: Ensure all required fields are filled
3. Test manually: `./bin/pixlet/pixlet-linux-amd64 render starlark-apps/{app-id}/{app-id}.star`
4. Missing assets: Some apps need images/fonts that may fail to download
5. API issues: Check API keys and rate limits

### Schema Not Extracted

**Symptom**: App installs but shows no configuration options

**Solutions**:
1. App may not have a `get_schema()` function (normal for some apps)
2. Schema extraction failed: Check logs for parse errors
3. Manual config: Edit `starlark-apps/{app-id}/config.json` directly
4. Report issue: File bug with app details at LEDMatrix GitHub

### Apps Show Distorted/Wrong Size

**Symptom**: Content appears stretched, squished, or cropped

**Solutions**:
1. Check magnify setting: **Plugins → Starlark Apps → Config**
2. Try `center_small_output=true` to preserve aspect ratio
3. Adjust `magnify` manually (1-8) for your display size
4. Some apps assume 64×32 - may not scale perfectly to all sizes

### App Shows Outdated Data

**Symptom**: Weather, sports scores, etc. don't update

**Solutions**:
1. Check render interval: **App Config → Render Interval** (300s default)
2. Force re-render: **Plugins → Starlark Apps → {App} → Render Now**
3. Clear cache: Restart LEDMatrix service
4. API rate limits: Some services throttle requests
5. Check app logs for API errors

## Performance Considerations

### Render Intervals

- Apps re-render on a schedule (default: 300s = 5 minutes)
- Lower intervals = more CPU/API usage
- Recommended minimums:
  - Static content (clocks): 30-60s
  - Weather: 300s (5min)
  - Sports scores: 60-120s
  - Stock tickers: 60s

### Caching

Enable caching to reduce CPU load:
- `cache_rendered_output=true` (recommended)
- `cache_ttl=300` (5 minutes)

Cached WebP files are stored in `starlark-apps/{app-id}/cached_render.webp`

### Display Rotation

Balance number of enabled apps with display duration:
- 5 apps × 15s = 75s full cycle
- 20 apps × 15s = 300s (5 min) cycle

Long cycles may cause apps to render before being displayed.

## Limitations

### Unsupported Features

- **OAuth2 Authentication**: Apps requiring OAuth login won't work
- **PhotoSelect**: Image upload from mobile device not supported
- **Push Notifications**: Apps can't receive real-time events
- **Background Jobs**: No persistent background tasks

### API Rate Limits

Many apps use free API tiers with rate limits:
- Rendering too frequently may exceed limits
- Use appropriate `render_interval` settings
- Consider paid API tiers for heavy usage

### Display Size Constraints

Apps designed for 64×32 may not utilize larger displays fully:
- Content may appear small on 128×64+ displays
- Magnification helps but doesn't add detail
- Some apps hard-code 64×32 dimensions

## Advanced Usage

### Manual App Installation

Upload custom `.star` files:
1. Navigate to **Starlark Apps → Upload**
2. Select `.star` file from disk
3. Configure app ID and metadata
4. Set render/display timing

### Custom App Development

While LEDMatrix runs Tronbyte apps, you can also create your own:

1. **Learn Starlark**: [Tidbyt Developer Docs](https://tidbyt.dev/)
2. **Write `.star` file**: Use Pixlet APIs for rendering
3. **Test locally**: `pixlet render myapp.star`
4. **Upload**: Use LEDMatrix web UI to install
5. **Share**: Contribute to [Tronbyte Apps](https://github.com/tronbyt/apps) repo

### Configuration Reference

**Plugin Config** (`config/config.json` → `plugins.starlark-apps`):

```json
{
  "enabled": true,
  "magnify": 0,                    // 0 = auto, 1-8 = manual
  "render_timeout": 30,            // Max seconds for Pixlet render
  "cache_rendered_output": true,   // Cache WebP files
  "cache_ttl": 300,                // Cache duration (seconds)
  "scale_output": true,            // Scale to display size
  "scale_method": "nearest",       // nearest|bilinear|bicubic|lanczos
  "center_small_output": false,    // Center instead of scale
  "default_frame_delay": 50,       // Frame timing (ms)
  "max_frames": null,              // Limit frames (null = unlimited)
  "auto_refresh_apps": true        // Auto re-render on interval
}
```

**App Config** (`starlark-apps/{app-id}/config.json`):

```json
{
  "location": "{\"lat\":\"40.7128\",\"lng\":\"-74.0060\",\"timezone\":\"America/New_York\"}",
  "units": "imperial",
  "api_key": "your-api-key-here",
  "render_interval": 300,          // App-specific override
  "display_duration": 15           // App-specific override
}
```

## Resources

### Official Documentation

- **Tidbyt Developer Docs**: https://tidbyt.dev/
- **Starlark Language**: https://github.com/bazelbuild/starlark
- **Pixlet Repository**: https://github.com/tidbyt/pixlet
- **Tronbyte Apps**: https://github.com/tronbyt/apps

### LEDMatrix Documentation

- [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md)
- [REST API Reference](REST_API_REFERENCE.md)
- [Troubleshooting Guide](TROUBLESHOOTING.md)

### Community

- **Tidbyt Community**: https://discuss.tidbyt.com/
- **Tronbyte Apps Issues**: https://github.com/tronbyt/apps/issues
- **LEDMatrix Issues**: https://github.com/ChuckBuilds/LEDMatrix/issues

## License and Legal

- **LEDMatrix**: MIT License (see project root)
- **Starlark Apps Plugin**: MIT License (part of LEDMatrix)
- **Pixlet**: Apache 2.0 License (Tidbyt Inc.)
- **Tronbyte Apps**: Various licenses (see individual app headers)
- **Starlark Language**: Apache 2.0 License (Google/Bazel)

**Disclaimer**: LEDMatrix is an independent project and is not affiliated with, endorsed by, or sponsored by Tidbyt Inc. The Starlark Apps plugin enables interoperability with Tidbyt's open-source ecosystem but does not imply any official relationship.

## Support

For issues with:
- **LEDMatrix integration**: File issues at [LEDMatrix GitHub](https://github.com/ChuckBuilds/LEDMatrix/issues)
- **Specific apps**: File issues at [Tronbyte Apps](https://github.com/tronbyt/apps/issues)
- **Pixlet rendering**: File issues at [Pixlet Repository](https://github.com/tidbyt/pixlet/issues)

---

**Ready to get started?** Install the Starlark Apps plugin and explore 974+ community apps! 🎨
