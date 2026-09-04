# Scroll Performance

How scrolling is paced on this hardware, what was wrong with it, and how to
configure a plugin so its marquee is smooth.

Measured on a Raspberry Pi 4 driving a 2×128×64 chain (256×64 logical) at
`limit_refresh_rate_hz: 100`. Numbers below come from that panel.

| | before | after |
|---|---|---|
| scroll frame rate | 44–46 fps | **100 fps, locked** |
| frames ≥ 45 ms | 14–17% | none observed |
| dominant frame time | 20 ms | **10 ms** |
| disk cache write (~1 MB) | 14.8 ms | **5.4 ms** |

---

## The one rule that matters

**Motion is smooth when the strip advances a whole number of pixels per panel
refresh.**

Advancing one pixel per refresh on a 100 Hz panel gives 100 px/s. Slower crisp
speeds come from holding each frame for several refreshes -- 50 px/s is one
pixel every second refresh -- which is covered under *Choosing a speed* below.
A speed that lands on no such combination has to do one of two bad things:

- **blend** two adjacent columns to render a half-step — on pixel-font text
  this alternates crisp and smeared frames and reads as shimmer, or as the
  text jumping a pixel ahead of itself;
- **repeat** a frame — the strip stands still, then jumps, which reads as
  judder.

Neither is tunable away. Pick a speed that divides evenly.

`src.common.scroll_config` solves this for you: `configure()` snaps a requested
speed to the nearest one the panel can actually show in whole pixels, and
`scripts/scroll_speeds.py` prints the full ladder for your hardware.

## Choosing a speed

The crisp speeds are not a fixed list -- they depend on how fast *your* panel
refreshes, which depends on its size, `pwm_bits`, `gpio_slowdown` and the Pi
model. A Pi Zero driving a long chain has a completely different set of good
speeds from a Pi 4 driving a short one.

```bash
# what can this panel do? (reads your configured refresh rate)
python3 scripts/scroll_speeds.py

# what does it ACTUALLY manage, rather than what is configured?
sudo systemctl stop ledmatrix
sudo python3 scripts/scroll_speeds.py --measure
sudo systemctl start ledmatrix

# highlight the closest option to the speed you want
python3 scripts/scroll_speeds.py --want 45

# try one on the panel
sudo systemctl stop ledmatrix
sudo python3 scripts/scroll_speeds.py --demo 50
sudo systemctl start ledmatrix
```

Sample ladder for a 100 Hz panel:

```
  20.0 px/s  (1px every 5 refreshes =  20.0 fps, slightly stepped)
  25.0 px/s  (1px every 4 refreshes =  25.0 fps, slightly stepped)
  33.3 px/s  (1px every 3 refreshes =  33.3 fps, smooth)
  50.0 px/s  (1px every 2 refreshes =  50.0 fps, smooth)
  66.7 px/s  (2px every 3 refreshes =  33.3 fps, smooth)
 100.0 px/s  (1px every 1 refresh  = 100.0 fps, smooth)
```

### How a slow speed stays crisp

`SwapOnVSync(canvas, framerate_fraction)` holds each frame for N panel
refreshes. **The panel keeps refreshing at its full rate either way**, so
holding a frame costs nothing in flicker -- it only changes how often a *new*
image is presented. That is what allows 50 px/s to be one whole pixel every
second refresh, instead of half a pixel every refresh (which has no good
rendering, only a choice between blur and judder).

`scroll_config.configure()` snaps the requested speed to the nearest entry on
the ladder and applies the hold, provided it is given the display manager:

```python
scroll_config.configure(
    self.scroll_helper,
    plugin_config=self.config,
    global_config=self.global_config,
    display_manager=self.display_manager,   # required for the hold to apply
)
```

Without `display_manager` a sub-refresh speed still resolves, but the hold is
never applied and the motion falls back to fractional pixels -- so `configure`
logs a warning rather than failing quietly. Pass `snap_to_crisp=False` to keep
an exact requested speed and accept the artefacts.

Speeds slower than about 20 px/s are stepped no matter what, because a 1-pixel
advance at 20 fps is simply a coarse increment. That is the pixel pitch, not a
software limit; the only way to move in smaller increments is sub-pixel
blending, which this display does not tolerate (see above).

## Configuring a plugin

Use the shared resolver rather than reading config keys yourself:

```python
from src.common import scroll_config

settings = scroll_config.configure(
    self.scroll_helper,
    plugin_config=self.config,
    global_config=self.global_config,
    refresh_hz=scroll_config.refresh_hz_from_config(self.global_config),
    plugin_logger=self.logger,
)
```

It resolves every config shape in one place, applies the speed, and returns
what it did. Precedence, highest first:

1. `display_options.scroll_speed` + `scroll_delay` — **the recommended form**
2. `display.scroll_speed` + `scroll_delay` — deprecated shape
3. `scroll_speed` + `scroll_delay` at the root — legacy flat
4. `scroll_pixels_per_second` — deprecated
5. the global `display` block
6. the built-in default (100 px/s)

`scroll_speed` is pixels per frame and `scroll_delay` is the frame period in
seconds, so the pair means `scroll_speed / scroll_delay` px/s. The recommended
config for a 100 Hz panel:

```json
"display_options": { "scroll_speed": 1.0, "scroll_delay": 0.01 }
```

### Why the deprecated key ranks below the explicit pair

Because some plugins give `scroll_pixels_per_second` a **schema default**, and
schema defaults are merged into plugin config. Ranking it above the pair means
it is always present and always wins, so the documented settings become
unreachable. That is a real, shipped bug — see
[ledmatrix-plugins#408](https://github.com/ChuckBuilds/ledmatrix-plugins/issues/408).

If you are writing a plugin: do not give a deprecated key a schema default.

## What was actually wrong

Four independent faults, each found by measurement.

### 1. The frame loop slept on top of a wait it had already done

`display_controller.py` ran the high-FPS loop as `render → SwapOnVSync (blocks
to the panel's refresh) → time.sleep(0.008) → plugin ticks`. The sleep was
unconditional and added to a wait that had already happened. Render work
measured ~4 ms, so each iteration cost ~12 ms against a 10 ms refresh grid —
every swap missed a refresh and landed on the next one. The loop settled at
exactly 50 fps while asking for 125, with no headroom, so ~14% of frames
slipped a further refresh.

Now the loop sleeps only the remainder of the frame budget, with a 1 ms floor
so plugin threads still get the GIL.

### 2. `SwapOnVSync` held the GIL while blocking

The rgbmatrix binding declares it without `nogil` (unlike `SetPixel`, `Clear`
and `Fill` immediately above it in `cppinc.pxd`), so the render thread held the
GIL for the entire vsync wait — most of every frame. Background threads were
starved into long uninterruptible bursts; a 1.5 MB API response costs ~17 ms to
parse and ~18 ms to re-encode for the cache, and `json.raw_decode` cannot be
preempted mid-document. Those bursts are what the render loop then waited on.

Fixed by rebuilding the binding: `scripts/build_rgbmatrix_nogil.sh`.

### 3. Sub-pixel blending was wrong for this display

Enabling it made things worse, not better — see the rule at the top. It is off
by default and only Vegas mode opts in via `set_sub_pixel_scrolling(True)`.

### 4. Frame-based stepping raced the vsync clock

Frame-based mode gated motion on a wall clock at `1/scroll_delay` steps per
second. Plugins set `scroll_delay` to the frame period, which puts that
comparison exactly on its own threshold: a frame arriving a hair early moved
zero pixels and rendered an identical frame, which dirty-tracking skipped, so
it returned in ~2 ms and the beat repeated. No `scroll_delay` value tunes this
out — a shorter delay just trades stalled frames for periodic double-steps.

`ScrollHelper` now accumulates elapsed time in both modes at the same
configured speed, so position stays proportional to real time.

## Diagnosing a juddery scroller

**`Avg FPS` will lie to you.** It is a 100-frame moving average, and a 2 ms
duplicate frame plus a 21 ms double-wait average to exactly 10 ms. A ticker
that is stalling on half its frames still reports a healthy `100.0`.

Look at the **distribution** instead:

```bash
journalctl -u ledmatrix --since "-10min" --no-pager \
  | grep -oE "Frame time: [0-9.]+ms" | awk '{print $3}' | sed 's/ms//' \
  | awk '{printf "%.0f\n", $1}' | sort -n | uniq -c
```

Reading it, on a 100 Hz panel:

| you see | it means |
|---|---|
| everything at 10 ms | healthy |
| a mode at ~2 ms | **duplicate frames** — the swap was skipped because the image did not change. The scroller is advancing less than one pixel per frame. |
| a mode at 20/30/50 ms | frames missing refreshes — per-frame work is overrunning, or a background thread is holding the GIL |
| `Avg FPS` above 100 | duplicates present, unless the scroll cycle has completed and is idling |

Then confirm what the plugin actually loaded — config edits do not always reach
the running code:

```bash
journalctl -u ledmatrix --since "-5min" --no-pager | grep -iE "px/s|px/frame"
```

If a plugin logs its scroll config **twice** with different modes, the second
line is what is running.

## Rebuilding the binding

```bash
bash scripts/build_rgbmatrix_nogil.sh              # build into a scratch dir
sudo bash scripts/build_rgbmatrix_nogil.sh --install
sudo bash scripts/build_rgbmatrix_nogil.sh --rollback
```

The build never touches the installed module. `--install` backs up the original
to `~/rgbmatrix-core.so.ORIGINAL` first, and rolls back automatically if the
service does not come back healthy. Requires `build-essential`; Cython is
installed into a cached venv under `~/.cache/ledmatrix-cython`.

Re-run it after upgrading `rpi-rgb-led-matrix`, since a library upgrade
replaces the patched binding.

## Faster JSON

`src/cache/disk_cache.py` uses `orjson` when it is importable and falls back to
the stdlib otherwise, so it is optional:

```bash
sudo pip3 install --break-system-packages orjson
```

Encoding is where it pays — about 7× on this hardware. Decoding gains far less
(~1.3× on large payloads) because the cost there is building Python objects,
not scanning text. That is also why moving parsing to a subprocess does not
help: `pickle.loads` of the same payload costs 8.1 ms against `json.loads` at
10.9 ms, so the work just moves rather than disappearing.
