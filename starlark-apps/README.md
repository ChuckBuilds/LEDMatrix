# Starlark Apps Storage

This directory contains installed Starlark (.star) apps from the Tronbyte/Tidbyt community.

## Structure

Each app is stored in its own subdirectory:

```text
starlark-apps/
  manifest.json              # Registry of installed apps
  world_clock/
    world_clock.star         # The app code
    config.json              # User configuration
    schema.json              # Configuration schema (extracted from app)
    cached_render.webp       # Cached rendered output
  bitcoin/
    bitcoin.star
    config.json
    schema.json
    cached_render.webp
```

## Managing Apps

Apps are managed through the web UI or API:

- **Install**: Upload a .star file or install from Tronbyte repository
- **Configure**: Edit app-specific settings through generated UI forms
- **Enable/Disable**: Control which apps are shown in display rotation
- **Uninstall**: Remove apps and their data

## Compatibility

All apps from the [Tronbyte Apps Repository](https://github.com/tronbyt/apps) are compatible without modification. The LEDMatrix system uses Pixlet to render apps exactly as designed.

## Performance

- **Caching**: Rendered output is cached to reduce CPU usage
- **Background Rendering**: Apps are rendered in background at configurable intervals
- **Frame Optimization**: Animation frames are extracted and played efficiently
