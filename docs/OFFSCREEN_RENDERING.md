# Offscreen Rendering

**Status (2026-09-30):** offscreen rendering is implemented
(`DisplayManager.offscreen()`, the adapter on the prefetch thread, the plugin
lock), and so are live elements, which grew out of steps 2 and 3 below: see
*Live elements*. The segment strip proposed as step 2 was not needed; *Why not
a SegmentStrip* says why.

First soak of step 1 on hdpi (50 px/s, `pwm_bits` 7, preview open, 8-minute
runs, A/B/B/A):

| build | late | by 1 | 2 | 3–5 | 6+ | freezes | render-thread fetches |
|---|---|---|---|---|---|---|---|
| #628 | 0.53% | 82 | 2 | 3 | 2 | 3 | 6 |
| step 1 | 0.63% | 78 | 63 | 17 | 2 | 1 | 0 |
| step 1 | 0.42% | 77 | 23 | 10 | 0 | 0 | 0 |
| #628 | 0.37% | 84 | 5 | 3 | 3 | 2 | 14 |

It does what it was built to: no plugin is fetched on the render thread, and
freezes fell from 5 to 1. But frames 2–5 refreshes late rose. The rendering
moved to the prefetch thread still needs the GIL, and the render thread waits
for it (risk 5 below). The late rate did not improve overall. The 1–2 s
freezes appear in both builds and have a separate, not yet identified cause.

The GIL fix, measured on hdpi (90 px/s, `pwm_bits` 8, preview open, 8-minute
runs after a 2-minute warm-up, order A B C C B A, 2026-09-24). Each arm pools
two runs, about 81,000 frames:

| arm | late | by 1 | 2 | 3–5 | 6+ | 2+ late per 10k frames | freezes |
|---|---|---|---|---|---|---|---|
| A: step 1 as is | 0.90% | 575 | 64 | 91 | 9 | 20.1 | 0 |
| B: `switch_interval_ms` 1 | 0.78% | 510 | 105 | 23 | 2 | 15.8 | 0 |
| C: `prefetch_gate` | **0.60%** | 471 | 11 | 7 | 2 | **2.5** | 0 |

The gate removes the frames the render thread spent waiting for the GIL, and
it costs the prefetch nothing that shows: it parked the thread for 3–6 s per
run, and the next group was ready at every strip extension in every arm.
`prefetch_gate` is therefore on by default; `switch_interval_ms` stays an
off-by-default experiment. What is left is almost all one refresh late, which
is the per-frame budget (a 6.75 ms p50 blit in a refresh the panel holds at
83–85 Hz while rendering), not contention.

The runs restart the service, so the hourly sports refresh never fell inside
one. That refresh is its own case: about twenty ESPN chunk-fetch threads at
once, which the gate does not cover (it gates only the prefetch thread).

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

## Live elements: content that changes while it scrolls

Offscreen rendering is also what makes fresh content possible. A plugin's
segment is drawn when its group is prefetched, and the strip carries
7,000-10,000 px of content ahead of the viewport, so at ~100 px/s a score drawn
then reaches the screen 70-100 seconds later -- and once in the strip it never
changed: when a plugin reported new data, Vegas only dropped its caches, so the
change appeared on the plugin's *next* turn, minutes later.

A plugin can now hand Vegas **live elements** instead of pictures
(`BasePlugin.get_vegas_elements()`, see "Live Vegas elements" in
[PLUGIN_API_REFERENCE.md](PLUGIN_API_REFERENCE.md#live-vegas-elements)):
named, fixed-width pieces of content -- one per game card, one for a map. Vegas
records where each lands in the strip and, when the plugin's data changes,
redraws just the changed ones off the render thread and copies their pixels
over the old ones between two frames. A card already crossing the panel
changes; nothing next to it moves.

### Why not redraw every frame

On a Pi the render thread has about 4 ms of slack per refresh at 512x64 after
the ~6 ms blit, and a scoreboard card is ~29 ms of Pillow work that holds the
GIL. Drawing on the render thread is out of the question at any rate, so the
render thread only ever *copies* pixels that are already drawn. Measured on a
Pi 4 (ledpi): writing a 35 KB card into a 20,000 px strip takes 8.5 µs, a
101 KB map 17 µs, four cards (the per-frame cap) 34 µs -- against 124 µs for
the viewport slice every frame already does.

### How an update reaches the screen

1. A plugin's `update()` completes. The update worker calls
   `PluginManager._note_update_completed`, which calls the update listeners
   (`add_update_listener`) there and then, with the plugin's lock still held.
   Vegas's listener moves the plugin's **epoch** on
   (`src/vegas_mode/elements.py`, `LiveEpochs`) and wakes the live worker.
2. The **live worker** (`src/vegas_mode/live_worker.py`), the one background
   thread that draws for the strip once it holds a live element, finds the
   plugin's elements whose recorded epoch is older than its current one,
   nearest the screen first, and calls `get_vegas_elements()` under the
   plugin's lock (0.25 s wait, then a 1 s backoff). Elements whose `version`
   is unchanged cost nothing; the rest are pinned and checksummed, and each
   whose pixels changed becomes a patch in a one-per-element slot (the latest
   wins).
3. Between two frames the render thread
   (`RenderPipeline.apply_live_patches`, from `coordinator.run_frame`) pops at
   most four patches or two screens of bytes and copies each into the strip
   with `ScrollHelper.patch_columns`. It takes no lock and draws nothing. A
   patch made for an older strip, for an element trimmed away or already
   behind the screen, or from older data than the strip shows, is dropped.

End to end, a new score reaches a card already on screen within one poll of
the data source (30 s for live games) plus about a second: the listener is
immediate, and while live elements exist the update tick that schedules
plugins runs every second instead of every four.

Elements that change with **time** rather than data (an aircraft moving
between position reports) ask for `refresh_hz`; the worker calls
`redraw_vegas_element()` -- without the plugin's lock, from state the plugin
publishes in one assignment -- that often while the element is on or within
`live_lead_screens` of the screen, capped by `live_max_hz` (5), at 1 Hz
without the render gate, and halved for an element whose redraws average over
50 ms.

### Geometry

A live element is never trimmed to its ink: the adapter pads it with
`content_padding` black columns either side and pins its width, and a redraw
at any other width is refused (it shows the next time the plugin comes round).
Records keep **absolute** strip columns -- the strip column plus everything
trimmed off the front since the strip was composed -- so a trim moves one
origin rather than every record. Nothing on screen is ever moved, inserted or
resized; a game added to a slate appears on the plugin's next turn.

### Why not a SegmentStrip

The proposal here was to replace the single strip with a list of segments.
In-place patching of the single strip meets every goal without that: the
patch is O(element) and the strip layout never changes. What a segment list
would still buy is cheaper extensions, and most of that came from making the
strip's PIL copy lazy instead (`ScrollHelper.cached_image`: an extension used
to rebuild it twice, 1.7-3.8 ms each on a Pi 4). The `extend` row of
`frame_soak.py`'s "after work" table says whether the rest is worth it.

### When it is off

- `display.vegas_scroll.live_refresh: false` (the kill switch; also in the
  web UI), or `vegas_live: false` in one plugin's section.
- Always under multi-display sync: the follower mirrors whole strips only, so
  a patch would never reach it. (Continuous-mode sync has a separate problem:
  the follower is not sent extensions or trims at all.)
- In swap mode (`continuous_scroll: false`) and with `offscreen_prefetch:
  false`.
- For plugins without the hook, which are drawn and placed exactly as before,
  and on the paths that fetch without the plugin's lock (the first strip of a
  run, the render thread's fallback fetch), which use `get_vegas_content()`.

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
   runs. Measure with the soak. A render process separate from plugin work
   is the structural answer (the "native presenter" step). Two experiments
   get most of the way first (results under Status, above):
   - `vegas_scroll.switch_interval_ms` lowers the switch interval for a Vegas
     run (1 ms is the obvious try), so the render thread waits at most that
     long behind bytecode. It does nothing for a C call that keeps the GIL.
   - `vegas_scroll.prefetch_gate` (`src/common/render_gate.py`) lets the
     prefetch thread run Python only while the render thread is blocked in
     `SwapOnVSync`, up to just before the refresh the swap returns on, and
     parks it the rest of the time. That covers C calls too, since the gate is
     checked before each one starts. It never parks the thread while it holds
     a lock the render thread takes, and never for more than 50 ms. It needs
     the rebuilt binding, which releases the GIL during the swap. On by
     default.

## What this does not fix

- **The blit.** Copying a 512×64 frame into the matrix (`SetImage`) is ~6 ms at
  8 PWM bits on a Pi 4, leaving ~4 ms of slack per refresh. That is the main
  source of the single-refresh late frames. Holding frames for two refreshes
  (≈50 px/s) doubles the budget. Cutting the blit itself is the native-presenter
  step.
- **Multi-display sync in continuous mode.** The follower is sent the whole
  strip only at a new cycle and on connect, never the extensions and trims of
  continuous mode, so it drifts from the leader after the first extension.
  Live elements stay off under sync for that reason.

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
- **Live elements** (`test/test_vegas_live_*.py`,
  `test/test_vegas_elements_*.py`, `test/test_scroll_helper_patch.py`): every
  record points at exactly its element's pixels through any sequence of
  compose, extend and trim; a patch changes only its element's columns (a
  property test against a twin strip that is never patched); the render
  thread's apply takes no lock and draws nothing; the worker's priorities,
  floors, backoff and hand-over; and, end to end on the emulator with the stub
  plugin (`test/fixtures/plugins/vegas-live-stub`), an update changes a card
  already in the strip and an animated element moves with no update at all.
- **Hardware:** an hdpi soak, A/B against the #628 build, alternating order.
  Targets: no freezes, an empty 6+ bucket, the 3–5 bucket near zero, and the late
  rate below 0.66%. Plus, for freshness: log each segment's age when it enters
  the viewport, and compare the median and max before and after.

## Rollout

1. **Offscreen rendering** (shipped): `offscreen()`, the adapter on the
   prefetch thread, and the plugin lock. Removed the render-thread pauses.
2. **Measurement and the lazy strip image:** late frames attributed to the
   render-thread work before them (`FrameTimingRecorder.note_op`, the "after
   work" table), and extensions no longer rebuilding the strip's PIL copy.
3. **Live elements:** the plugin API, the records, the worker and in-place
   patches, with the sports scoreboards and the flight map adopting it.

`display.vegas_scroll.offscreen_prefetch` (default `true`) restores the
deferred path when `false`, and `display.vegas_scroll.live_refresh` (default
`true`) turns live elements off. Keep both for one release, then delete the
old paths.

## Open questions

1. Multi-display sync: is there a two-Pi rig to test on? Replaying strip
   operations to the follower (append, trim, patch, in absolute columns) would
   fix continuous-mode sync and let live elements run under it.
2. Is the `extend` cost worth a segment list after the lazy image? The soak's
   "after work" table answers it per rig.
