# Plugin API Reference

Complete API reference for plugin developers. This document describes all methods and properties available to plugins through the Display Manager, Cache Manager, and Plugin Manager.

> **Adaptive layout:** every `BasePlugin` also exposes `self.layout`,
> `self.draw_fit(text, region)` and `self.draw_image(img, region, ...)` —
> the recommended way to render text and images that scale to any panel
> size. See [ADAPTIVE_LAYOUT.md](ADAPTIVE_LAYOUT.md).

## Table of Contents

- [Manifest Required Fields](#manifest-required-fields)
- [BasePlugin](#baseplugin)
- [Display Manager](#display-manager)
- [Cache Manager](#cache-manager)
- [Plugin Manager](#plugin-manager)
- [Fetching data](#fetching-data)
- [Deprecated APIs](#deprecated-apis)

---

## Manifest Required Fields

Three parts of core check `manifest.json`, each for a different set of
fields:

| Check | Fields | What happens when one is missing |
|---|---|---|
| JSON schema, [`schema/manifest_schema.json`](../schema/manifest_schema.json) | `id`, `name`, `version`, `author`, `entry_point`, `class_name`, `compatible_versions` | Install from URL logs a warning (`PluginStoreManager._validate_manifest_schema()`); nothing is refused |
| Plugin Store install, [`src/plugin_system/store_install.py`](../src/plugin_system/store_install.py) | `id`, `name`, `class_name`, `display_modes` | Install is refused. A registry install first tries to detect a missing `class_name` from the entry-point file |
| Plugin loader, [`src/plugin_system/plugin_loader.py`](../src/plugin_system/plugin_loader.py) | `class_name` | The plugin fails to load |

Defaults and other uses:

- `entry_point` defaults to `manager.py`; the store writes the default back
  into the manifest on install.
- `compatible_versions` (a list of semver ranges such as `">=2.0.0"`) is how
  the store decides whether a plugin can run on this core. An install is
  refused only when the field excludes the running version
  (`compatibility.check()` in
  [`src/plugin_system/compatibility.py`](../src/plugin_system/compatibility.py)).
- `version` is compared with the registry's `latest_version` to decide
  whether an update is available.
- If `display_modes` is empty at load time, the display controller uses the
  plugin id as the only mode.

**Set all eight:** `id`, `name`, `version`, `author`, `entry_point`,
`class_name`, `display_modes`, `compatible_versions`. That satisfies every
check. The schema lists the optional fields.

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "author": "YourName",
  "entry_point": "manager.py",
  "class_name": "MyPlugin",
  "display_modes": ["my-plugin"],
  "compatible_versions": [">=2.0.0"]
}
```

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

Fetch/update data for this plugin. Called on the plugin's update interval:
the value `get_update_interval()` returns when it returns a number, otherwise
the static interval: the `update_interval` in the plugin's manifest, else
`update_interval` in the plugin's section of `config.json`, else 60 seconds
(see [`get_update_interval()`](#get_update_interval---optionalfloat) below).

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

Called after the plugin's section of `config.json` changes -- a save in the
web UI, say. Every lifecycle hook runs in the display process, which is the
only process that runs plugins: the web interface writes `config.json`, and
the display's config watcher calls this with the prepared section. See
[ARCHITECTURE.md](ARCHITECTURE.md#web-and-display-processes-who-runs-plugins).

In the display service it runs on the config watcher thread while holding
the plugin's lock, so it never overlaps your `update()` or `display()`. If
the plugin stays busy for more than 5 seconds, the change is applied later
from the update thread: as soon as the plugin is free, and before its next
`update()` at the latest.

#### `on_enable() -> None`

Called when the display loads the plugin enabled: at startup, or when it is
switched on in the web UI.

#### `on_disable() -> None`

Called when the display unloads the plugin, e.g. when it is switched off in
the web UI.

#### `get_update_interval() -> Optional[float]`

How often this plugin wants `update()` called right now, in seconds. The
manifest's `update_interval` is one static number; override this when the
right cadence depends on state only the plugin knows, e.g. poll every 15s
while a game is live and fall back to the manifest value otherwise.

**Returns**: seconds as a number, or `None` (the default) for no opinion.

How `PluginManager` (`_get_plugin_update_interval` in
`src/plugin_system/plugin_manager.py`) resolves the interval on each
scheduling tick:

1. It calls `get_update_interval()`. A number wins over everything below.
   Values under `PluginManager.MIN_DYNAMIC_UPDATE_INTERVAL` (5 seconds) are
   raised to it.
2. If the hook returns `None`, raises, or returns something that isn't a
   finite number (a `bool`, a string, NaN, infinity), it is ignored and the
   static interval applies: the manifest's `update_interval`, else
   `update_interval` in the plugin's section of `config.json`, else 60
   seconds.

The static value is cached per plugin until the plugin is loaded or
unloaded again, so editing `update_interval` in config takes effect on the
next reload. The hook's return value is never cached: it is called on every
tick of the display loop, so keep it to attribute reads (no config lookups,
no I/O, no locks a fetch might hold) and don't let it raise.

**Example**:
```python
def get_update_interval(self):
    # Fast while something is live, manifest default otherwise.
    if any(m.live_games for m in self._live_managers):
        return self.config.get("live_update_interval", 15)
    return None
```

Added in core 3.4.0; older cores never call it, so a plugin that relies on
it should floor `ledmatrix_min_version` at `3.4.0`.

#### `get_display_duration() -> float`

Get display duration for this plugin. Can be overridden for dynamic durations.

**Returns**: Duration in seconds

#### `get_info() -> Dict[str, Any]`

Return plugin info for display in web UI. Override to provide additional state information.

### Dynamic-duration hooks

Plugins that render multi-step content (e.g. cycling through several games)
can extend their display time until they've shown everything. To opt in,
either set `dynamic_duration.enabled: true` in the plugin's config or
override `supports_dynamic_duration()`.

#### `supports_dynamic_duration() -> bool`

Return `True` if this plugin should use dynamic durations. Default reads
`config["dynamic_duration"]["enabled"]`.

#### `get_dynamic_duration_cap() -> Optional[float]`

Maximum number of seconds the controller will keep this plugin on screen
in dynamic mode. Default reads
`config["dynamic_duration"]["max_duration_seconds"]`.

#### `is_cycle_complete() -> bool`

Override this to return `True` only after the plugin has rendered all of
its content for the current rotation. Default returns `True` immediately,
which means a single `display()` call counts as a full cycle.

#### `reset_cycle_state() -> None`

Called by the controller before each new dynamic-duration session. Reset
internal counters/iterators here.

### Live priority hooks

Live priority lets a plugin temporarily take over the rotation when it has
urgent content (live games, breaking news). Enable by setting
`live_priority: true` in the plugin's config and overriding
`has_live_content()`.

#### `has_live_priority() -> bool`

Whether live priority is enabled in config (default reads
`config["live_priority"]`).

#### `has_live_content() -> bool`

Override to return `True` when the plugin currently has urgent content.
Default returns `False`.

#### `get_live_modes() -> List[str]`

List of display modes to show during a live takeover. Default returns the
plugin's `display_modes` from its manifest.

#### `get_vegas_priority_weight() -> Optional[int]`

How many slots per Vegas cycle this plugin should get. Default returns
`None`, which defers to the core.

The Vegas ticker is otherwise a strict round robin — every plugin appears
exactly once per cycle — so with a dozen plugins enabled a live score can be
minutes stale by the time it comes round. A weight of *N* gives the plugin
*N* slots per cycle, spread evenly through it rather than clumped.

**You usually do not need this.** When the hook returns `None`, the core
already gives a plugin `vegas_scroll.live_weight` whenever
`has_live_priority()` and `has_live_content()` are both true. Live sports get
extra turns with no code at all.

Implement it only when the plugin knows something the core cannot. The
motivating case is favorite teams — the core can see *that* a game is live,
but not *whose*:

```python
def get_vegas_priority_weight(self):
    if not (self.has_live_priority() and self.has_live_content()):
        return None                       # let the core decide
    vegas = self.global_config.get('display', {}).get('vegas_scroll', {})
    if self._favorite_is_live():
        return vegas.get('favorite_live_weight', 5)
    return vegas.get('live_weight', 3)
```

The weight is per *plugin*, not per game: a scoreboard showing four live games
still occupies one slot at a time and rotates its own games within it. Values
are clamped to 1–10 by the caller. An exception here is caught and logged, and
the core then falls back to its own live-content check — so a plugin whose
weight calculation is broken still gets `live_weight` for a game that really
is live, rather than being demoted to 1.

Only consulted while `vegas_scroll.live_in_ticker` is on (the default since
3.8.0). With it off live content preempts Vegas entirely and there is no
ticker to be weighted within. See
[ADVANCED_FEATURES.md](ADVANCED_FEATURES.md#live-content-in-the-ticker).

### Vegas scroll hooks

Vegas mode shows multiple plugins as a single continuous scroll instead of
rotating one at a time. Plugins control how their content appears via
these hooks. See [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) for the user
side of Vegas mode.

#### Vegas participation

Each plugin takes part in Vegas mode in one of three ways:

| Participation | What Vegas does |
|---|---|
| `'scroll'` | The plugin's content (`get_vegas_content()`) scrolls by with everything else |
| `'pause'` | The scroll stops when the plugin's turn comes round; its `display()` draws it full screen for `get_display_duration()` seconds, then the scroll resumes |
| `'exclude'` | The plugin is left out of Vegas mode |

Declare the plugin's default in `manifest.json`:

```json
{
  "id": "my-alerts",
  "vegas_participation": "pause"
}
```

The user can override it per plugin with `vegas_participation` in that
plugin's config section (it is one of the core-owned properties, see
[PLUGIN_CONFIG_CORE_PROPERTIES.md](PLUGIN_CONFIG_CORE_PROPERTIES.md)).
Vegas resolves it in this order:

1. the user's `vegas_participation` config value;
2. the plugin's `get_vegas_participation()` — the default implementation
   reads the manifest's `vegas_participation`, then derives a value from
   the legacy hooks below;
3. derived from the legacy hooks: `get_vegas_display_mode()` returning
   `VegasDisplayMode.STATIC` → `'pause'`; otherwise
   `get_vegas_content_type()` returning `'none'` → `'exclude'`; everything
   else → `'scroll'`.

Step 3 is exactly what Vegas did before participation existed, so a plugin
that declares nothing behaves as it always has. Manifest
`vegas_participation` is new in core 3.8.0; older cores ignore it and use
the legacy hooks.

#### `get_vegas_participation() -> str`

Returns `'scroll'`, `'pause'` or `'exclude'`. Override it only when the
answer depends on state — pause only while an alert is live, exclude while
there is nothing to show; for a fixed answer use the manifest. Vegas applies
the user's config value before calling an override, so an override does not
need to check it. A value that is not one of the three is ignored with a log
line and the legacy hooks decide.

```python
def get_vegas_participation(self):
    return 'pause' if self._alert_is_live() else 'scroll'
```

#### `get_vegas_content() -> Optional[PIL.Image | List[PIL.Image] | None]`

Return content to inject into the scroll. Multi-item plugins (sports,
odds, news) should return a *list* of PIL Images so each item scrolls
independently. Static plugins (clock, weather) can return a single image.
Returning `None` falls back to capturing whatever `display()` produces.

#### `get_vegas_render_width() -> int`

The width Vegas wants this plugin's content to occupy, from the plugin's
`vegas_width_pct` config value or the global
`display.vegas_scroll.render_width_pct`. Vegas also narrows
`display_manager` while it asks for content, so a plugin that sizes itself
from `display_manager.width` does not need to read this.

#### Live Vegas elements

*New in core 3.8.0.* Content from `get_vegas_content()` is baked into the
ticker's strip when the plugin's turn is prefetched, so a score drawn then
scrolls past with that score however many goals are scored while it crosses
the panel. A plugin that returns **live elements** instead gets them updated
in place: after its `update()` the ticker asks again, compares each element
with what the strip holds, and swaps the changed ones in between two frames
-- on screen included -- without anything next to them moving.

```python
try:
    from src.plugin_system.vegas_elements import VegasElement
except ImportError:          # core older than 3.8.0: the hook is never called
    VegasElement = None

class MyScoreboard(BasePlugin):
    def get_vegas_elements(self):
        if VegasElement is None:
            return None
        return [VegasElement(key=f"game:{g['id']}",
                             image=self._card(g),           # cache by fingerprint
                             version=self._fingerprint(g))  # changes iff pixels would
                for g in self.games]
```

`VegasElement(key, image, version=None, live=True, refresh_hz=0.0)`:

| Field | Meaning |
|---|---|
| `key` | Names the element across redraws; unique in the list, stable for the same logical item (`"game:nfl:401547417"`, `"map"`). |
| `image` | The element now, at the display's height. A live element's **width must not depend on its data**: a redraw at another width is never swapped in (it appears the next time the plugin comes round), because nothing on screen may move. |
| `version` | Anything hashable that changes exactly when the pixels would. Handed back with the **same image object** as last time, it lets the ticker skip converting the element; a new image is always converted and compared by its pixels, so a redraw for new settings is never missed. `None` means "compare pixels". |
| `live` | `False` places it as plain content (trimmed, never refreshed): separators, decoration. |
| `refresh_hz` | For content that changes with **time** rather than data (an aircraft moving between position reports): the ticker calls `redraw_vegas_element()` about this often while the element is on or near the screen, capped by `vegas_scroll.live_max_hz` and at 1 Hz without the rebuilt rgbmatrix binding. |

**`get_vegas_elements() -> Optional[List[VegasElement]]`** — called on the
ticker's background thread under the plugin's lock (never while `update()`
runs), on a canvas of its own and told its render width, exactly like
`get_vegas_content()`. It is called after every `update()` while any of the
plugin's elements is on or ahead of the screen, so it must be cheap when
nothing changed (cache images by version), idempotent, and must not fetch.
Return `None` to use `get_vegas_content()`, which a plugin must keep working
for older cores and for the paths that do not ask for elements (the ticker's
first strip, multi-display sync, the `live_refresh` switch).

**`redraw_vegas_element(key, width, height, at) -> Optional[PIL.Image]`** —
only for elements with `refresh_hz`. Called **without** the plugin's lock,
possibly while `update()` runs, so read only state `update()` replaces in one
assignment (an immutable snapshot), never state it mutates in place. `at` is
the `time.monotonic()` the pixels are expected on the panel: draw the element
as it should look then. Return exactly `width` x `height`, or `None` to skip
the tick.

**`notify_vegas_data_changed()`** — data that arrives outside `update()` (a
background thread, a push callback) calls this so the ticker redraws without
waiting for the next `update()`. Safe from any thread.

Live elements are never trimmed to their ink: the ticker pads each with
`content_padding` black columns either side, the margin trimming would have
left. A single element wider than the plugin's width budget
(`vegas_max_width_screens`, not counting that padding) is cropped like any
other content and scrolls by as plain, no longer live. The user can turn them off per plugin with `vegas_live: false` (a
core-owned property) or for the whole ticker with
`display.vegas_scroll.live_refresh: false`; they are always off under
multi-display sync.

`scripts/check_plugin.py` checks the contract for any plugin that implements
the hook (unique keys, height, width stable with no new data, redraw size,
slow calls) and prints a `vegas elements` row; the checks are in
`src/plugin_system/testing/vegas.py`. `test/fixtures/plugins/vegas-live-stub`
is a small working example.

#### Legacy: `get_vegas_content_type()` and `get_vegas_display_mode()`

Superseded by participation, and still read to derive it when neither the
user nor the manifest declares one (step 3 above). Only two answers ever
mattered: `get_vegas_content_type()` returning `'none'`, and
`get_vegas_display_mode()` returning `VegasDisplayMode.STATIC`.

- `get_vegas_content_type()` returns `'multi'`, `'static'` or `'none'`
  (default `'static'`).
- `get_vegas_display_mode()` returns a `VegasDisplayMode` member (not a
  string — the string `'static'` never paused anything). The default reads
  the plugin's `vegas_mode` config value (`"scroll"`, `"fixed"` or
  `"static"`), else maps content type `'multi'` to `SCROLL` and anything
  else to `FIXED_SEGMENT`.

`SCROLL` and `FIXED_SEGMENT` (and `vegas_mode` `"scroll"` and `"fixed"`)
have always behaved identically: both scroll. The distinction is deprecated
and goes away in 3.9.0 — see [Deprecated APIs](#deprecated-apis).

#### Deprecated: `get_supported_vegas_modes()` and `get_vegas_segment_width()`

Never read by core, and removed in 3.9.0: calling the `BasePlugin`
implementation logs a deprecation warning. A plugin's own override keeps
working for the plugin itself. `get_vegas_segment_width()` read the
`vegas_panel_count` config value, which has never affected Vegas — a card's
width comes from `get_vegas_content()` and `vegas_width_pct`.

> The full source for `BasePlugin` lives in
> `src/plugin_system/base_plugin.py`. If a method here disagrees with the
> source, the source wins — please open an issue or PR to fix the doc.

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

The display manager doesn't provide a dedicated `draw_image()` method.
Instead, plugins paste directly onto the underlying PIL Image
(`display_manager.image`), then call `update_display()` to push the buffer
to the matrix.

```python
from PIL import Image

logo = Image.open("assets/logo.png").convert("RGB")
self.display_manager.image.paste(logo, (10, 10))
self.display_manager.update_display()
```

For transparency support, paste using a mask:

```python
icon = Image.open("assets/icon.png").convert("RGBA")
self.display_manager.image.paste(icon, (5, 5), icon)
self.display_manager.update_display()
```

This is the canonical way to render arbitrary images.

### Scrolling State Management

For plugins that implement scrolling content, use these methods to coordinate with the display system.

#### `set_scrolling_state(is_scrolling: bool, frame_hold: int = 1) -> None`

Mark the display as scrolling or not scrolling, and set this scroll's frame
pacing. Call it when a scroll starts (calling it on every scroll frame is fine)
and with `False` when it stops.

**Parameters**:
- `is_scrolling` (bool): True if currently scrolling, False otherwise
- `frame_hold` (int, default 1): how many panel refreshes each pushed frame is
  held for (clamped to 1-255; ignored when `is_scrolling` is False, which
  resets it to 1). Pass the `frame_hold` of the settings
  `src.common.scroll_config.configure()` returned. Added in core 3.4.0.

**Why `frame_hold` matters**: `scroll_config.configure()` snaps the speed to
one the panel can show in whole pixels and sets the `ScrollHelper` to advance a
fixed number of pixels on every presented frame -- no clock is consulted. The
panel presents frames at its refresh rate divided by the hold, so the hold is
part of the speed. Omit it and a 50 px/s scroll (1px every 2nd refresh on a
100 Hz panel) runs at 100 px/s. The hold is not applied by `configure()`
because it must not outlive the scroll: plugins share one display manager.

**Example**:
```python
from src.common import scroll_config
from src.common.scroll_helper import ScrollHelper

def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.scroll_helper = ScrollHelper(
        self.display_manager.width, self.display_manager.height, self.logger)
    # ...later, hand it content with self.scroll_helper.set_scrolling_image(img)
    self.scroll_settings = scroll_config.configure(
        self.scroll_helper,
        plugin_config=self.config,
        global_config=self.global_config,
        display_manager=self.display_manager,
        plugin_logger=self.logger,
    )

def display(self, force_clear=False):
    self.display_manager.set_scrolling_state(
        True, frame_hold=self.scroll_settings.frame_hold)
    self.scroll_helper.update_scroll_position()
    self.display_manager.image = self.scroll_helper.get_visible_portion()
    self.display_manager.update_display()
    if self.scroll_helper.is_scroll_complete():
        self.display_manager.set_scrolling_state(False)
```

Don't pace the loop with `time.sleep()`: `update_display()` blocks on the
panel's vsync, which is what paces a scroll. See `docs/SCROLL_PERFORMANCE.md`
for choosing a speed.

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

### Available Fonts

The Display Manager provides several pre-loaded fonts:

```python
display_manager.regular_font      # Press Start 2P, size 8
display_manager.small_font        # Press Start 2P, size 8
display_manager.calendar_font     # 5x7 BDF font
display_manager.extra_small_font  # 4x6 TTF font, size 7 (6 snapped to its pixel grid)
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

#### `clear_cache(key: Optional[str] = None) -> None`

Remove a specific cache entry, or all cache entries when called without
arguments.

**Parameters**:
- `key` (str, optional): Cache key to delete. If omitted, every cached
  entry (memory + disk) is cleared.

**Example**:
```python
# Drop one stale entry
self.cache_manager.clear_cache("weather_data")

# Nuke everything (rare — typically only used by maintenance tooling)
self.cache_manager.clear_cache()
```

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

#### `get_data_type_from_key(key: str) -> str`

Extract data type from cache key to determine appropriate cache strategy.

**Parameters**:
- `key` (str): Cache key

**Returns**: Inferred data type string

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
weather = self.plugin_manager.plugins.get("weather")
if weather is not None and weather.enabled:
    pass
```

---

## Fetching data

Use the core helpers for HTTP rather than a `requests.Session` of your own:
`APIHelper` (`from src.common import APIHelper`) for JSON APIs, and
`fetch_espn_scoreboard()` (`src.common.espn_dates`) or
`BackgroundDataService` for ESPN scoreboards. Since the release after 3.7.0
these go through the core **fetch service** (`src/common/fetch_service.py`),
so a plugin that uses them gets the following with no code change. Return
values, exceptions and retries are what they were.

- **Shared connections.** Core sessions with the same retry policy share one
  connection pool per host, instead of one pool per helper.
- **Merged requests.** Identical GETs in flight at the same time (same URL
  and query, headers, timeout and retry policy) go to the network once, and
  every caller gets its own copy of the response, or the same exception.
- **Host budgets.** A host can have a token-bucket budget. A request past it
  waits for a token, but never longer than `max_wait_seconds` (2 s by
  default). Only ESPN hosts have one by default (20 requests a second, burst
  200), which normal use never reaches.
- **Conditional GET.** When a server sends `ETag` or `Last-Modified`, the
  next identical request revalidates, and a `304 Not Modified` comes back to
  your code as the original `200` with its body. ESPN currently sends
  neither, so this does nothing there.
- **Response cache.** A response whose server says `Cache-Control:
  max-age=N` answers an identical GET for those N seconds without a
  request (ESPN sends 1 to ~500 s). It never hands you a response older
  than you accept: pass `cache_max_age=<your TTL>` to `fetch_get()` or
  `fetch_espn_scoreboard()` (0 always asks the network); without it a
  response is reused for at most 30 seconds.
- **Counters.** Requests, merged requests, bytes, 304s, errors and time spent
  waiting, and requests answered without the network (`memo_hits` from the
  response cache, `cache_hits` from a shared scoreboard cache entry), are
  counted per plugin and per host, and published for the web UI
  at `GET /api/v3/plugins/fetch-stats` (see
  [REST_API_REFERENCE.md](REST_API_REFERENCE.md#get-fetch-statistics)). A
  request is counted against your plugin when it runs inside your
  `update()`/`display()`, your constructor or `on_enable()`, or anywhere in
  code under your plugin's directory, including threads you start.

What is not covered yet: requests a plugin makes with its own `requests.get()`
or `Session.get()` calls. They work as before but are invisible to the
budgets and counters.

### One cache key per ESPN scoreboard

Cache an ESPN scoreboard under `espn_scoreboard_cache_key(sport, league,
dates)` (`src.common.espn_dates`), not a key of your own, so every plugin
showing that league shares one fetch and one cached copy. `sport` and
`league` are ESPN's path segments (`football`, `college-football`), and
`dates` is what you send as `dates=` (`"20261004"`, `"202610"`,
`"20260925-20261016"`, a `date`, or `None` for the undated scoreboard).

```python
from src.common.espn_dates import get_espn_scoreboard

data = get_espn_scoreboard(
    self.session, "football", "nfl", "20261004",
    cache_manager=self.cache_manager,
    max_age=300,                       # your TTL: nothing older comes back
    legacy_keys=["my_old_key_20261004"],  # read once while upgrading
)
```

`get_espn_scoreboard` returns a cached copy at most `max_age` seconds old,
whoever wrote it, and otherwise fetches with `fetch_espn_scoreboard`
(`limit=500`, ranges split the way ESPN requires) and caches the result
without a ttl, so each reader applies its own age limit. `max_age=0` always
fetches but still leaves the copy for others. For a two-step read, use
`read_espn_scoreboard_cache()` and `store_espn_scoreboard_cache()` around
your own fetch. Scoreboards built on `SportsFetchMixin` get
`_schedule_cache_key(datestring)` and `_cached_schedule(key, legacy_keys)`
for their schedule windows. All of this is in the core release after 3.8.0.

The settings live in `config.json` under `fetch_service`, read when the
display starts and on a config reload:

```json
"fetch_service": {
    "enabled": true,
    "max_wait_seconds": 2,
    "rate_limits": {
        "*.espn.com": {"per_second": 20, "burst": 200},
        "api.example.com": {"per_second": 1, "burst": 5}
    }
}
```

`rate_limits` keys are a host or a `*.domain` pattern (which also matches
the bare domain); `"per_second": 0` removes a budget. `"enabled": false`
turns the whole service into a plain `session.get()`. Two further switches,
`"single_flight": false` and `"conditional_get": false`, turn off merging and
revalidation. `"response_cache": {"enabled": false}` turns off the response
cache; its `default_max_age` (30) is the limit for callers that pass no
`cache_max_age`.

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

3. **Handle scrolling state**: If your plugin scrolls, use scrolling state methods,
   passing the frame hold `scroll_config.configure()` returned
   ```python
   self.display_manager.set_scrolling_state(True, frame_hold=settings.frame_hold)
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

---

## Deprecated APIs

A deprecated method still works but logs a warning the first time it is
called (`journalctl -u ledmatrix` shows which one), until the release that
removes it. [DEPRECATIONS_3.8.md](DEPRECATIONS_3.8.md) is the usage scan
behind each removal: which of the deprecated methods the official plugins,
the registry's third-party plugins and core still call or override. Only
methods that scan reports unused are removed; the rest stay until their
callers migrate.

### Removed in 3.8.0

Deprecated in 3.5.0 with a warning on first call, and gone in 3.8.0:
the scan found no caller in any official or third-party plugin. Calling one
now raises `AttributeError`.

| Object | Methods | Instead |
|---|---|---|
| `cache_manager` | `update_cache` | `set()` |
| `cache_manager` | `get_background_cached_data`, `is_background_data_available` | `get()` |
| `cache_manager` | `has_data_changed`, `setup_persistent_cache`, `get_sport_live_interval`, `get_sport_key_from_cache_key`, `record_cache_hit`, `record_cache_miss`, `record_fetch_time`, `get_cache_metrics`, `log_cache_metrics`, `get_memory_cache_stats` | no replacement |
| `display_manager` | `draw_weather_icon`, `draw_sun`, `draw_cloud`, `draw_rain`, `draw_snow`, `draw_text_with_icons` | draw your own icons (the weather plugin ships `WeatherIcons`) |
| `display_manager` | `get_scrolling_stats` | no replacement |
| `font_manager` | `get_font_catalog`, `get_available_fonts` | read `font_catalog` |
| `font_manager` | `set_override`, `remove_override`, `get_overrides`, `add_font`, `remove_font`, `validate_font`, `get_size_tokens`, `get_performance_stats`, `get_manager_fonts`, `get_detected_fonts`, `get_plugin_fonts`, `unregister_plugin_fonts` | no replacement |
| `plugin_manager` | `get_enabled_plugins` | check `enabled` on the entries in `plugin_manager.plugins` |

### Removed in 3.9.0

The Vegas APIs that described a fixed-width segment, which Vegas never
implemented. Vegas participation (`'scroll'`, `'pause'`, `'exclude'`, see
[Vegas scroll hooks](#vegas-scroll-hooks)) replaces them. Calling one of the
methods, or setting `vegas_panel_count`, logs a warning once per process.
No official plugin calls them; calendar, olympics and blackjack override
`get_supported_vegas_modes()`, which keeps working for the plugin itself.

| What | Instead |
|---|---|
| `BasePlugin.get_supported_vegas_modes()` | declare `vegas_participation` in the manifest |
| `BasePlugin.get_vegas_segment_width()` and the `vegas_panel_count` config key | nothing: a card's width comes from `get_vegas_content()` and `vegas_width_pct` |
| `VegasDisplayMode.SCROLL` vs `FIXED_SEGMENT` (`vegas_mode` `"scroll"` vs `"fixed"`) | `'scroll'` participation; the two always behaved the same |
