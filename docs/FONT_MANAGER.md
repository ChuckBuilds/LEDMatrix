# FontManager Usage Guide

> **Picking a size automatically:** if you want the *largest font that fits
> a given area* rather than a fixed size, use the adaptive layout system's
> font ladders, which resolve through this FontManager. `BasePlugin`
> subclasses get this as `self.layout.fit_text(...)`; other code can build
> a `LayoutContext(width, height, font_manager)` directly — see
> [ADAPTIVE_LAYOUT.md](ADAPTIVE_LAYOUT.md).

## Overview

[`src/font_manager.py`](../src/font_manager.py) loads and caches the TTF and
BDF fonts in `assets/fonts/`, registers fonts that plugins ship, and records
which plugin uses which font so the web UI can show it.

Several methods are deprecated and will be removed in LEDMatrix 3.7.0; they
log a warning on first call. They are listed in
[Deprecated methods](#deprecated-methods) below, and the full set is pinned in
[`test/test_deprecation.py`](../test/test_deprecation.py).

## Getting the FontManager

There is one shared FontManager per display process. The display controller
creates it and hands it to the `PluginManager`, so a plugin reaches it
through its `plugin_manager`:

```python
class MyPlugin(BasePlugin):
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.font_manager = self._get_font_manager()
```

`BasePlugin._get_font_manager()` returns `plugin_manager.font_manager`, or a
standalone FontManager when none is available (test harnesses, mocks).
`DisplayManager` has **no** `font_manager` attribute —
`display_manager.font_manager` raises `AttributeError`.

## Resolving a font

```python
element_key = f"{self.plugin_id}.title"

# Register the choice so the web UI's Fonts tab can list it.
self.font_manager.register_manager_font(
    manager_id=self.plugin_id,
    element_key=element_key,
    family="press_start",
    size_px=10,
    color=(255, 255, 255),
)

font = self.font_manager.resolve_font(
    element_key=element_key,
    family="press_start",
    size_px=10,
)
self.display_manager.draw_text("Hello", x=10, y=10, font=font)
```

`resolve_font()` applies any entry for `element_key` in
`config/font_overrides.json`, maps a plugin-local family to its namespaced
name when `plugin_id` is passed, and then calls `get_font(family, size_px)`.
On error it returns a fallback font rather than raising.

`get_font(family, size_px)` looks the family up in `font_catalog` and loads
it (cached per family and size).

## Font families

At start-up the FontManager scans `assets/fonts/` for `.ttf` and `.bdf`
files. Each becomes a family named after the file, lower-cased and without
the extension (`PressStart2P-Regular.ttf` → `pressstart2p-regular`). Four
aliases are added on top:

| Alias | File |
|---|---|
| `press_start` | `assets/fonts/PressStart2P-Regular.ttf` |
| `four_by_six` | `assets/fonts/4x6-font.ttf` |
| `five_by_seven` | `assets/fonts/5x7.bdf` |
| `tom_thumb` | `assets/fonts/tom-thumb.bdf` |

Read the catalog directly: `font_manager.font_catalog` is a dict of family
name to file path. Files added later are picked up on the next start of the
display service.

## Plugin fonts

Plugins that ship their own fonts declare them in a `"fonts"` block in
`manifest.json`. The plugin manager calls
`FontManager.register_plugin_fonts()` during plugin load. `plugin://…`
sources are resolved relative to the plugin's install directory.

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "fonts": {
    "fonts": [
      {
        "family": "custom_font",
        "source": "plugin://fonts/custom.ttf",
        "metadata": {"description": "Custom plugin font", "license": "MIT"}
      },
      {
        "family": "web_font",
        "source": "https://example.com/fonts/font.ttf",
        "metadata": {"checksum": "sha256:abc123..."}
      }
    ]
  }
}
```

Registered families are namespaced as `<plugin_id>::<family>`. Pass
`plugin_id` to `resolve_font()` to use the short name:

```python
font = self.font_manager.resolve_font(
    element_key=f"{self.plugin_id}.text",
    family="custom_font",          # resolved as "my-plugin::custom_font"
    size_px=10,
    plugin_id=self.plugin_id,
)
```

## Overrides

`resolve_font()` still honours `config/font_overrides.json` (a map of
element key to `family` and/or `size_px`), which is read once at start-up.
The methods that edit it — `set_override()`, `remove_override()`,
`get_overrides()` — are deprecated, and there is no web UI or REST endpoint
for overrides (the override editor and `/api/v3/fonts/overrides` were
removed). To let users choose a font, add a field to your plugin's config
schema.

## Font usage in the web UI

The web UI's **Fonts** tab lists, uploads, previews and deletes the font
files in `assets/fonts/`. The web interface runs in its own process and has
no FontManager, so the display service publishes which plugin uses which
font ([`src/font_usage.py`](../src/font_usage.py)), and the tab's **Used by**
column reads it:

- **Source**: `register_manager_font()` registrations of the loaded
  plugins. `get_font()` and `resolve_font()` do not know the calling plugin
  and are not counted, and neither is a plugin that opens a font file
  directly with PIL — register the fonts your plugin draws with if you want
  them listed.
- **Names**: a family, alias or path is resolved through `font_catalog` to
  the file it loads and reported under that file's name without extension
  (`PressStart2P-Regular`, `4x6-font`, `5x7`, `tom-thumb`), which is how the
  Fonts tab keys its rows. Fonts outside `assets/fonts/` (a plugin's own
  `plugin_id::family` fonts) and families that resolve to nothing are left
  out.
- **When**: a daemon thread started once plugins have loaded checks every
  10 seconds and writes the `font_usage_snapshot` cache key only when the
  usage changed (and once a day, so the cache's cleanup never expires it).
  Unloading a plugin drops its registrations (`forget_manager_fonts`).
- **Unknown**: until the display service has published, the column reads
  "unknown" and `GET /api/v3/fonts/catalog` returns `used_by: null`.
- The tab warns before deleting a font that a loaded plugin registered.

## Text measurement

```python
width, height, baseline = font_manager.measure_text("Hello", font)
font_height = font_manager.get_font_height(font)
```

## Tips

- BDF fonts usually look better than TTF at small sizes on LED panels.
- Use `{plugin_id}.{element}` element keys.
- Register the fonts you draw with, so the Fonts tab can warn before one is
  deleted.
- Replace direct `ImageFont.truetype("assets/fonts/...", 8)` calls with
  `resolve_font()`: it caches, resolves paths against the install directory,
  and handles BDF files.

## Troubleshooting

**Font not found**
- Check the file exists in `assets/fonts/`.
- The family name is the filename without extension, lower-cased.
- Check the display service log for font discovery errors.

**Plugin fonts not loading**
- Check the manifest's `"fonts"` block.
- Check the log for download or registration errors, and that font URLs are
  reachable.

## API reference

Current methods:

| Method | Purpose |
|---|---|
| `register_manager_font(manager_id, element_key, family, size_px, color=None)` | Record a font choice (feeds the Fonts tab) |
| `forget_manager_fonts(manager_id)` | Drop a manager's registrations (core calls it when a plugin unloads) |
| `resolve_font(element_key, family, size_px, plugin_id=None)` | Get a font, applying overrides and plugin namespacing |
| `get_font(family, size_px)` | Get a font directly |
| `get_native_bdf_size(family)` | Native pixel size of a BDF family, or `None` |
| `measure_text(text, font)` | `(width, height, baseline)` |
| `get_font_height(font)` | Line height |
| `register_plugin_fonts(plugin_id, font_manifest)` | Register a plugin's fonts (core calls it at load) |
| `clear_cache()` | Drop cached fonts and metrics |
| `font_catalog` (attribute) | Family name → file path |

### Deprecated methods

Removed in 3.7.0. Each logs a warning on first call.

| Method | Use instead |
|---|---|
| `get_available_fonts()`, `get_font_catalog()` | read `font_catalog` |
| `get_size_tokens()` | pass a pixel size |
| `get_performance_stats()` | — |
| `set_override()`, `remove_override()`, `get_overrides()` | a font field in your plugin's config schema |
| `get_manager_fonts()`, `get_detected_fonts()` | — |
| `get_plugin_fonts()`, `unregister_plugin_fonts()` | — |
| `add_font()`, `remove_font()`, `validate_font()` | the web UI's Fonts tab |
