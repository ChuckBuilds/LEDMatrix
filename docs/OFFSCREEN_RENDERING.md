# Offscreen Rendering

**Status: proposed (2026-09-24).** Nothing here is implemented yet. This is the
design for review. When it lands, this file becomes the reference for how
plugin content is rendered off the render thread.

## The problem

Vegas mode builds its ticker from every plugin's content. Most of that work
already happens on a background prefetch thread
(`RenderPipeline.start_prefetch`). But any plugin whose content needs the
**shared display canvas** is deferred to the render thread
(`RenderPipeline.drain_deferred`), one plugin every two seconds. The code's
own comments put each of those at 40–600 ms, and the render thread presents no
frames while one runs.

On hdpi (Pi 4, 512×64) most plugins take that path: geochron, tide-display,
news, hockey-scoreboard, ledmatrix-stocks, incoming-packages, clock-simple,
countdown, birdnet-go, ledmatrix-music and odds-ticker. They arrive in bursts
("Whole group deferred; strip will extend as it drains") every minute or so,
12 fetches in five minutes. That is the "occasional pause" a viewer sees.

An 8-minute soak (`scripts/frame_soak.py --preview`) of the #628 build on
hdpi:

| late by | frames |
|---|---|
| 1 refresh | 238 |
| 2 | 32 |
| 3–5 | 30 |
| 6+ | 5 |
| freezes ≥ 250 ms | 2 (0.97 s total) |

The 3+ rows and the freezes are the pauses. The single-refresh row is a
separate problem: the blit is 6 ms of a 10 ms refresh, so there is little
slack. It is covered under *What this does not fix*.

## Why a plugin is canvas-bound

The plugin-facing canvas is a set of shared attributes on `DisplayManager`:
`image`, `draw`, `matrix`, and the `width`/`height` properties that read from
`matrix`. Three adapter paths (`src/vegas_mode/plugin_adapter.py`) need them,
and each returns `None` under `offscreen_only=True` so the plugin is queued for
the render thread:

1. **Display capture** (`_capture_display_content`): clear the canvas, call
   `plugin.display()`, copy `display_manager.image`. Used by any plugin
   without `get_vegas_content()` or a populated `scroll_helper`.
2. **Scroll-content generation** (`_trigger_scroll_content_generation`): a
   ticker plugin whose `scroll_helper.cached_image` is empty is made to build
   it by calling `display(force_clear=True)` or `_create_scrolling_display()`.
   Both draw on the canvas.
3. **Narrowed rendering** (`DisplayManager.render_size`): swaps the shared
   `matrix`, `image` and `draw` for a narrower set so the plugin lays out for
   `render_width_pct`. The render thread would see the swap mid-frame.

The render thread keeps the canvas coherent only because nothing else touches
it at the same time. A background thread can't use it.

## The design: a per-thread render target

`capture_mode()` is already per-thread (#423 made its state a
`threading.local`, so a background capture no longer suppresses the render
loop's pushes). The same move applies to the canvas itself:

```python
with display_manager.offscreen(width=None, height=None) as surface:
    plugin.display(force_clear=True)
    content = surface.image.copy()
```

For the **calling thread only**, inside the block:

| accessor | resolves to |
|---|---|
| `display_manager.image`, `.draw` | the surface's own image and draw: a fresh black canvas, `fontmode = "1"` |
| `display_manager.matrix` | a logical proxy reporting the surface size, so `width`/`height` and plugins that read `matrix.width` follow it. Hardware calls through it (`SetImage`, `SwapOnVSync`, `Clear`, brightness writes) are inert. |
| `update_display()`, `clear()` | canvas-only: the block implies capture mode, which is already per-thread |
| `set_scrolling_state()`, `set_frame_hold()` | no-ops, so a plugin's `display()` cannot re-pace the live scroll. Today it can, when it is captured on the render thread. |

Every other thread sees the real canvas, unchanged. The render loop in
particular keeps presenting while a plugin draws elsewhere.

### Implementation sketch

- `image`, `draw` and `matrix` become properties over `_image`, `_draw` and
  `_matrix`, plus a thread-local current surface. The getter returns the
  surface's value when the calling thread has one, else the shared one; setters
  mirror that. That costs about 0.1 µs per access, and `update_display()` reads
  each a handful of times per frame. Every existing `self.image = ...` in
  `DisplayManager` (`clear()`, setup, fallback) keeps working and becomes
  thread-correct for free.
- `render_size()` is rebuilt on `offscreen()`: it creates or narrows the
  calling thread's surface instead of swapping shared state.
- `offscreen()` nests and always restores on exit, including when the plugin
  raises.
- `VisualDisplayManager` (the plugin test harness) gets the same method, for
  parity.

### Adapter changes

- `get_content(offscreen_only=True)` stops returning `None` for the three
  paths above. Each runs inside `display_manager.offscreen(render_width)`.
- `_capture_display_content` and `_trigger_scroll_content_generation` drop
  their "copy the shared image, restore it afterwards" bookkeeping, since the
  shared image is never touched.
- **Take the plugin's lock.** `PluginManager.get_plugin_lock()` keeps
  `update()` and `display()` mutually exclusive in normal rotation, but Vegas
  never takes it, so today's render-thread captures already race
  `update()`. Off the render thread the adapter can afford to wait: blocking
  acquire with a timeout (proposed 2 s). On timeout it keeps the cached segment
  and tries again next group.
- `drain_deferred()` and the deferred queue are deleted. The only render-thread
  fetch left is the inline fallback when no prepared group is ready, which in
  practice is the first extension. Prefetching at start removes that too.

## Keeping live content fresh

Offscreen rendering is also what makes fresh sports scores possible. Today a
plugin's segment is drawn when its group is prefetched, and the strip carries
7,000–10,000 px of content ahead of the viewport (hdpi logs: "7153px still
ahead", "9842px ahead"). At ~100 px/s, a score drawn now reaches the screen
70–100 seconds later. When a plugin reports new data, Vegas only drops its
cache (`invalidate_pending_updates`), so the change is drawn on the plugin's
*next* turn, several minutes later. A segment already in the strip scrolls by
with the data it was drawn with.

That was the right trade while every redraw of a canvas-bound plugin stalled
the scroll. Off the render thread a redraw costs the scroll nothing, so the
strip can afford three things.

### 1. Refresh at the gate

Before a segment enters the viewport, check whether its plugin has updated
since the segment was drawn. If it has, redraw it offscreen and replace it
while it is still out of sight. Width changes are fine here, because
everything from that segment onward is still invisible.

The gate sits `lead` pixels ahead of the viewport's right edge:
`lead = max(one screen, speed × (render time + margin))`. The render time is
the plugin's own, measured on each render (sports cards take the longest,
hundreds of ms up to seconds per the prefetch notes). A plugin whose render
does not finish before its segment reaches the viewport keeps the old segment.
The scroll never waits for it.

Content is then at most `lead / speed` seconds old when it appears, a few
seconds instead of minutes, without changing how far ahead the rotation
fetches.

### 2. Replace ahead of the screen

When a plugin reports new data (the Vegas update tick already names them), any
of its segments that are **anywhere ahead of the viewport** are redrawn and
replaced straight away, not only at the gate. That covers the long stretch of
strip between prefetch and the gate.

### 3. Update on screen

A segment that is already **visible** is patched in place when the redrawn
version has the same geometry: the same total width, and the same width for
each card (a sports plugin returns one image per game, joined with
`intra_plugin_gap`). Scoreboard cards keep a fixed layout, so a score change
patches in and the digits update as the card scrolls past. The patch is a
pixel copy of one card (a 150×64 card is ~29 KB) applied by the render thread
between frames, so a frame never shows half of a patch.

When the geometry differs (a game added or dropped, a card that grew), the
visible part cannot change without a jump. Only the cards not yet on screen
are replaced, and only if the geometry up to that point is unchanged. Otherwise
the segment keeps its snapshot until it has scrolled off.

### Avoiding wasted work

- **Change detection.** `run_scheduled_updates_with_changes()` names a plugin
  whenever its `update()` ran, not when its data changed. On hdpi
  `clock-simple` and `ledmatrix-music` are named on every 4-second tick. A
  redraw whose pixels hash the same as the segment's is discarded without a
  swap.
- **Rate limit.** A plugin is redrawn at most once per
  `vegas_scroll.refresh_min_interval` (proposed 5 s), and never while its
  previous redraw is still running.
- **One worker.** Redraws go through the same background worker as prefetch,
  one plugin at a time at `nice 10`, under the plugin's lock.

Data freshness is still bounded by each plugin's own fetch interval (how often
it polls live scores). Drawing faster cannot beat the data source.

### The strip becomes a list of segments

All three need the strip to be replaceable by segment. Today it is one
image (`ScrollHelper.cached_array`, 8,000–20,000 px wide, 1.5–3.8 MB), and
`append_content()` rebuilds the whole thing on the render thread for every
appended block. That is also a pause source.

Proposed `SegmentStrip`, used by Vegas in place of the single image:

- an ordered list of segments: plugin id, card boundaries, a pixel array, the
  render time, and the plugin data version it was drawn from, plus its
  x-offset in the strip;
- `visible(x, width)` assembles the viewport by slicing across at most a few
  segments: the same ~100 KB copy per frame that slicing the single image
  costs today;
- append and trim become O(block) list operations, not a copy of the strip;
- replace swaps one list entry and shifts the offsets of the segments after it
  (dozens at most). A same-geometry patch copies pixels into the existing array.

Every mutation is prepared off the render thread and applied by the render
thread at a frame boundary, so the strip the render loop reads is never
half-changed.

### Multi-display sync

The follower renders from its own copy of the strip, which the leader sends
whole at a new cycle (`send_scroll_image`), plus the scroll position every
frame. Replacements and patches would need a new message
(`send_segment_patch(offset, image)`) so the follower shows the same pixels.
Until that exists, fresh-content updates are disabled while sync is active,
and the follower keeps today's behaviour.

## Risks, and what was checked

1. **Plugins holding their own reference to the shared `draw` or `image`.**
   They would keep drawing into the shared canvas, and routing by thread can't
   redirect them. A grep of the 49 plugins installed on hdpi found none storing
   `display_manager.draw` or `.image` in an attribute (a pattern search, so
   indirect aliasing would slip past it). A plugin that did would
   draw into an image nobody displays, which trims to a blank segment. That is
   not corruption, and it is no worse than today.
2. **Plugins calling the matrix directly.** None in the audit. Inside
   `offscreen()` the proxy makes it inert anyway.
3. **Font thread-safety.** `FontManager` shares font objects across plugins.
   Measured on Pillow 12.3, two threads rendering text take 1.94× as long as
   one, so text rendering holds the GIL and FreeType is never entered
   concurrently. Re-check if Pillow changes that.
4. **Plugin thread-safety.** `display()` moves to the prefetch thread. The
   plugin lock makes it exclusive with `update()`, which is more protection
   than it has today. Threads a plugin starts itself are not covered, as today.
5. **The GIL.** Moving 40–600 ms of plugin rendering off the render thread
   removes the pauses, but the work still needs the GIL. Pillow drawing holds
   it, and a waiting thread only gets it back after the switch interval
   (default 5 ms). Expect some single-refresh late frames while a prefetch
   runs. Measure with the soak. Lowering `sys.setswitchinterval` during Vegas
   (e.g. 1 ms) is a one-line experiment, and a render process separate from
   plugin work is the structural answer (the "native presenter" step).

## What this does not fix

- **The blit.** Copying a 512×64 frame into the matrix (`SetImage`) is ~6 ms at
  8 PWM bits on a Pi 4, leaving ~4 ms of slack per refresh. That is the main
  source of the single-refresh late frames. Holding frames for two refreshes
  (≈50 px/s) doubles the budget. Cutting the blit itself is the native-presenter
  step.
- **Live refreshes pushed from `update()`.** Some sports plugins call
  `display()` and `update_display()` from inside `update()`, which runs on the
  update worker and can push to the panel mid-Vegas. That is a separate
  hazard. `offscreen()` gives a tool for it (run the update worker offscreen
  while Vegas owns the panel), but it is out of scope here.

## Test plan

- **Unit, `DisplayManager`:** one thread inside `offscreen()` draws while
  another reads `image`/`draw`/`matrix`/`width`/`height` and sees the real
  canvas. Also: `update_display()` and `set_scrolling_state()` are inert inside;
  `render_size()` narrows only the calling thread; nesting and exceptions
  restore state.
- **Unit, adapter:** a stub display-capture plugin and a stub scroll-helper
  plugin both return content with `offscreen_only=True`, and nothing is queued
  for the render thread. The plugin lock is taken, and a timeout keeps the cached
  segment.
- **Emulator integration:** a stub canvas-bound plugin whose `display()` sleeps
  300 ms. The Vegas render loop never goes a frame without presenting (frame
  timing recorder: zero freezes).
- **Unit, `SegmentStrip`:** the viewport assembled across segment boundaries
  matches slicing one concatenated image, pixel for pixel. Append, trim,
  replace-ahead and same-geometry patch each leave every other column
  unchanged. A geometry-changing patch of a visible segment is refused.
- **Freshness:** a stub sports plugin whose score changes every second. The
  score on screen is never older than `lead / speed` plus the plugin's fetch
  interval. A visible card's digits change without the frame-timing recorder
  seeing a late frame. An unchanged redraw is discarded.
- **Hardware:** an hdpi soak, A/B against the #628 build, alternating order.
  Targets: no freezes, an empty 6+ bucket, the 3–5 bucket near zero, and the late
  rate below 0.66%. Plus, for freshness: log each segment's age when it enters
  the viewport, and compare the median and max before and after.

## Rollout

Three changes, each soaked on hdpi before the next:

1. **Offscreen rendering:** `offscreen()`, the adapter on the prefetch thread,
   and the plugin lock. Removes the render-thread pauses.
2. **`SegmentStrip`:** Vegas's strip becomes a list of segments. Removes the
   whole-strip copy on append. No visible behaviour change.
3. **Fresh content:** refresh at the gate, replace ahead, patch on screen,
   with change detection and the rate limit.

`display.vegas_scroll.offscreen_prefetch` (default `true`) restores today's
deferred path when `false`, and `display.vegas_scroll.live_refresh` (default
`true`) turns off step 3. Keep both for one release, then delete the old paths.

## Open questions

1. Keep the kill switch, or ship without one?
2. Plugin lock timeout: skip the plugin and keep its cached segment (proposed),
   or wait longer?
3. `refresh_min_interval`: 5 s proposed. Sports plugins that poll every 15–30 s
   would mostly be redrawn once per fetch anyway.
4. Multi-display sync: ship fresh content with it disabled under sync
   (proposed), or build the follower patch message first?
