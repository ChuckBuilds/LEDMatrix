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
the ladder, sets the helper to advance that entry's whole-pixel step on every
presented frame (`ScrollHelper.set_pixels_per_frame`), and reports the hold
that speed needs. It does **not** apply the hold: the hold belongs to a scroll, not to a plugin's lifetime, and plugins
share one display manager -- one set at construction is reset the moment any
other plugin finishes scrolling. Apply it yourself when the scroll starts:

```python
settings = scroll_config.configure(
    self.scroll_helper,
    plugin_config=self.config,
    global_config=self.global_config,
    display_manager=self.display_manager,   # supplies the panel refresh rate
)

# ...then, each time this plugin begins scrolling:
self.display_manager.set_scrolling_state(True, frame_hold=settings.frame_hold)
```

Passing `display_manager` only lets `configure` read the true refresh rate from
`display.hardware`, which a plugin config cannot see. Skipping the
`set_scrolling_state(True, frame_hold=...)` call is the mistake that matters.
The helper consults no clock in this mode -- it moves the fixed step once per
`update_scroll_position()` call, and `SwapOnVSync` is what paces those calls --
so without the hold the panel presents a new frame every refresh and the scroll
runs `frame_hold` times too fast: 50 px/s (hold 2) plays at 100 px/s.

Pass `snap_to_crisp=False` to keep an exact requested speed and accept the
artefacts. The helper then paces off elapsed time instead of stepping, and the
hold is 1.

The General tab's `target_fps` ("Scroll Frame Rate") plays no part in any of
this: frames are presented at the panel refresh divided by the hold.

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
    display_manager=self.display_manager,
    plugin_logger=self.logger,
)

# each frame of a scroll (or at least when it starts):
self.display_manager.set_scrolling_state(True, frame_hold=settings.frame_hold)
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

The flip side: a `scroll_pixels_per_second` you add by hand is ignored whenever
the plugin's config also carries the pair, which it does whenever the pair has
a schema default. Set the speed through the pair instead.

The sports scoreboards (`src.common.sports_scroll`) are the exception to all of
the above: they read `scroll_settings.scroll_speed` per league as px/s directly,
and their `scroll_delay` is kept for compatibility but ignored for pacing.

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

A crisp speed configured through `scroll_config` no longer consults a clock at
all. Once `SwapOnVSync` blocks until the panel has taken the frame, the frame
count is a truer clock than `time.time()`, so the helper advances a fixed whole
number of pixels per presented frame (`set_pixels_per_frame`) and the display
manager holds each frame for `frame_hold` refreshes. Every frame moves the eye
by the same amount.

The time-based path remains only for callers that set a speed directly or pass
`snap_to_crisp=False`. There, frame-based mode no longer steps either: it
advances by elapsed time at `scroll_speed / scroll_delay` px/s.

## Diagnosing a juddery scroller

**An average will lie to you.** A 2 ms duplicate frame and a 21 ms double-wait
mean exactly 10 ms, so a ticker stalling on half its frames still averages to a
healthy 100 fps. The stats line reports the tail for that reason — read the
percentiles, not the fps.

Every scroller emits one line every 5 seconds covering *every* frame in that
window, tagged with the plugin it came from:

```bash
journalctl -u ledmatrix --since "-10min" --no-pager | grep "Scroll frame stats"
```

```
[Plugin: news] Scroll frame stats - 100.0 fps over 501 frames | median 10.00ms
p95 10.11ms max 12.03ms min 7.98ms | stalls 0 (0.0%) skips 0 (0.0%)
```

Reading it, on a 100 Hz panel:

A healthy median is the refresh period times the scroll's frame hold: 10 ms
for a hold of 1 (100 px/s), **20 ms for 50 px/s** (hold 2), 30 ms for 33.3 px/s.
A 20 ms median on a 50 px/s scroll is the hold doing its job, not missed
refreshes. The `Scroll configured:` log line gives the hold (`1px every 2
refreshes`).

| you see | it means |
|---|---|
| median = refresh period × hold, p95 within ~0.5 ms of it | healthy — locked to the panel |
| p95 or max a whole refresh period or more above that median | frames missing refreshes — per-frame work is overrunning, or a background thread is holding the GIL |
| non-zero **skips**, or a median *below* the expected one | **duplicate frames** — the swap was skipped because the image did not change, so the frame never waited on vsync. The scroller is advancing less than one pixel per frame, which a crisp fixed-step scroll never does; look for a plugin pacing off time or not passing the hold. |
| non-zero **stalls** | frames past 1.5× the median, which is the measure of judder that survives averaging |

`stalls` and `skips` are both counted against that window's own median, so they
stay meaningful on a panel running at any refresh rate.

To rank every scroller at once rather than reading lines one at a time:

```bash
journalctl -u ledmatrix --since "-3h" --no-pager | grep "Scroll frame stats" \
  | sed -E 's/.*- (\S+) - (\[Plugin: [^]]+\] )?Scroll.*median ([0-9.]+)ms p95 ([0-9.]+)ms.*/\1 \3 \4/' \
  | awk '$2 < 1000 {n[$1]++; m[$1]+=$2; p[$1]+=$3} END {for (k in n)
        printf "%-28s %5d windows  median %6.2fms  p95 %6.2fms\n", k, n[k], m[k]/n[k], p[k]/n[k]}' \
  | sort -k7 -rn
```

The `$2 < 1000` guard drops windows whose median is a whole second or more.
Those are not frames. Until the idle-gap fix in `log_frame_rate()`, the first
frame of every scroll was timed against the end of the *previous* scroll, so
the gap between them was recorded as one enormous sample — it landed in the
`max` field of otherwise healthy windows and counted as one stall per scroll,
roughly 0.2% at 500 frames to a window, which is the same order as the real
stall rates it sat beside. Current builds emit none, but the guard costs
nothing and keeps the command honest against older journals.

A scroller whose p95 sits several times its median is the one to fix, and it is
usually the one doing the most per-frame work rather than the one configured
worst. Measured over 20 minutes with two scrollers set identically at 100 px/s,
the leaderboard held 10 ms flat while the odds ticker spent ~20% of its frames
on duplicates. Same settings, different render cost: odds does more per-frame
work, and more variably, so it is first to land a frame that advances less than
a whole pixel. Check the render path before the config.

Then confirm what the plugin actually loaded — config edits do not always reach
the running code:

```bash
journalctl -u ledmatrix --since "-5min" --no-pager | grep -iE "px/s|px/frame"
```

If a plugin logs its scroll config **twice** with different modes, the second
line is what is running.

## Measuring a rig

The journal lines above tell you how one scroller behaved while everything else
was also happening. `scripts/render_bench.py` answers the narrower question a
release has to answer per rig: *with nothing else in the way, can this hardware
present every frame on time?* It drives the production path -- a real
`DisplayManager`, a real `ScrollHelper`, the same `scroll_config` resolver every
ticker uses -- so a regression in any of them shows up here.

```bash
sudo systemctl stop ledmatrix          # the service owns the GPIO

sudo python3 scripts/render_bench.py                 # 60s at one pixel per refresh
sudo python3 scripts/render_bench.py --seconds 600   # the shipping gate
sudo python3 scripts/render_bench.py --speed 50      # a held (frame_hold 2) speed
sudo python3 scripts/render_bench.py --busy 2        # with threads imitating plugin updates
sudo python3 scripts/render_bench.py --json /tmp/pi4-512x64.json

sudo systemctl start ledmatrix
```

It never starts or stops the service itself, for the same reason
`scroll_speeds.py` does not: a crash in a script must not be able to leave the
panel dark. Exit status is 0 for a pass, 1 for a fail, and **2 when the run
could not be set up at all** -- no root, no panel, a fallback display -- so a
rig that was never measured can never be mistaken for one that passed.

### Reading the report

A two-minute run on a Pi 4 driving 512x64 at `pwm_bits` 8:

```
measuring the panel for 4s...
panel refreshes at 100.4Hz (cap is 120Hz)
asked for 100.4 px/s ->  100.4 px/s  (1px every 1 refresh  = 100.4 fps, smooth)
scrolling 512x64 for 120s ...

panel held 96.3Hz while rendering (4.1% below its 100.4Hz idle rate)
 95.44 fps presented over 11449 frames in 120.0s (expected 96.30 fps = 1 refresh of 96.3Hz)
  frame time  median  10.46ms  p95  10.55ms  p99  11.10ms  max  22.16ms  min   7.36ms (target 10.38ms)
  missed      8 (0.070%)  gate 0.100%
  refreshes   1x:11441 2x:8
  PASS
  restarts    5 (the strip was scrolled through 5 times)
```

The same rig with `--busy 2` -- two threads parsing JSON, resizing images and
compressing bytes throughout, to imitate plugins updating -- held the same
95.4 fps and missed 3 frames in 11,445 (0.026%). Competing for the GIL did not
cost this loop its pacing.

A **missed** frame is one whose interval rounds up to at least one more refresh
than its frame hold asked for: the panel showed the previous frame again. The
half-refresh rounding boundary is deliberate -- a frame 1 ms late on a 10 ms
refresh still presented on the refresh it was meant to, and counting it would
fail every rig for nothing.

**NOT LOCKED** is the verdict that matters more than the miss count. A loop
that never blocked on vsync -- an emulator, a fallback display, or the
dirty-tracking skip firing mid-scroll -- can report a beautiful zero misses
while presenting nothing at all. The check is that the typical frame is not
*shorter* than the panel could physically present, which a bucket count alone
cannot see: 8 ms frames on a 100 Hz panel all land in the one-refresh bucket
while running 25% too fast. A run that is not locked always fails.

### The panel is slower while you are rendering into it

The benchmark measures the refresh **twice**, and the two numbers differ:

| | Pi 4, 512x64, `pwm_bits` 8 |
|---|---|
| idle, timing bare swaps | 100.4 Hz |
| while scrolling | 96.3 Hz |

Both are real. Driving an LED matrix is bit-banging on the same machine, so
`SetImage` over a 512x64 chain contends with the refresh itself and slows it.
Grading a soak against the idle number reports 96.3 fps against an expected
100.4 and looks broken; once the gap passes half a refresh period, every single
frame is counted as a miss. The give-away that nothing is actually being missed
is that the intervals cluster tightly around 10.46 ms instead of splitting
between 9.96 ms and 19.92 ms, which is what missing every twenty-fifth vsync
would look like.

So `frame_pacing.refresh_from_intervals()` reads the period back out of the
frames -- swaps that block on vsync can only return on a refresh boundary, so
the low end of `interval / frame_hold` *is* the period -- and the run is graded
against that. The idle figure is still printed, because the gap between the two
is itself the measure of how expensive a frame is: **a rise in that gap is a
render-cost regression even when the miss count stays at zero.**

The practical consequence for config: set `limit_refresh_rate_hz` near the rate
the panel holds *while rendering*, not the idle rate and certainly not a cap it
can never reach. A cap well above the real rate makes `scroll_config` solve
speeds against a refresh that does not exist, which is where "3px every 4
refreshes" comes from.

### Other counters

| line | meaning |
|---|---|
| `duplicate` | frames that advanced no pixels. A crisp fixed-step scroll should show none; any at all means the loop is presenting faster than the strip is moving. |
| `blank` | frames with no visible slice to draw -- the helper had no content. Should be zero. |
| `restarts` | how many times the strip was scrolled through end to end. Informational: the benchmark restarts the strip where a plugin would hand over to the next one. |

`--json` writes all of it, plus the panel geometry and the speed that was
solved, so two rigs (or one rig before and after a change) can be compared
without re-reading a terminal.

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
