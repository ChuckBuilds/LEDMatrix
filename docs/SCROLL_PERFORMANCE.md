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

To check a whole rig rather than one scroller, soak it -- see *Soaking a rig*
below.

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

## Soaking a rig

The per-scroller lines above tell you *which* scroller misbehaves. The soak
answers the question a release has to answer for each rig: **over a long run,
how often did a moving frame reach the panel late?**

Every frame reaches the panel through `DisplayManager.update_display`, so it is
timed there once, whoever drew it -- Vegas, a ticker plugin, anything. The
render thread only appends a tuple; a worker thread aggregates and rewrites
`/dev/shm/ledmatrix_frame_stats.json` every 10 seconds (RAM, so no SD-card
wear). `src/common/frame_timing.py` has the details.

```bash
python3 scripts/frame_soak.py                 # 10 minutes, as the display is now
python3 scripts/frame_soak.py --preview       # with the web preview open
python3 scripts/frame_soak.py --show          # totals since the service started
python3 scripts/frame_soak.py --json a.json   # keep the report to compare later
```

It runs as any user next to the display service and stops nothing. It needs
something to *scroll* during the run: a live game holding a static scoreboard
on screen gives no verdict. `--preview` keeps the web preview's viewer marker
fresh, which puts the preview's PNG encoding at full rate -- run it as the web
service's user.

| line | what it tells you |
|---|---|
| **Late frames** | Frames presented one or more refreshes after they were due: the panel showed the previous frame again, a visible hitch. **The pass/fail number**, 0.1% by default (`--max-late-pct`). Only intervals between two scrolling frames count, and a frame held for `frame_hold` refreshes is due `frame_hold` refreshes after the last. |
| **Freezes** | Gaps of 250 ms or more inside a scroll: recomposes, plugin handovers, blocking calls on the render thread. Reported but not failed on, because some are handovers between plugins rather than faults. A gap still counts when the display's scroll state went missing across it (it expires after 2 s, and plugins clear it from their own `display()`), as long as the scroll carries on straight after. |
| **blit** | Copying the frame into the matrix canvas (`SetImage`). It grows with width × height × `pwm_bits`: ~5.5 ms at 512×64 with 8 bits on a Pi 4. It is the biggest fixed cost, and it sets the refresh rates a rig can hold one pixel per refresh at. |
| **wait** | Time blocked in `SwapOnVSync`, i.e. the slack left in each refresh. A p50 near zero means the rig has no headroom and anything extra lands a frame late. |
| **work** | Everything else between two frames: drawing, scrolling, and waiting for the GIL. A wide gap between its p50 and p99 is another thread getting in the way. |
| **Binding** | `STOCK` means the rgbmatrix binding holds the GIL through the vsync wait, which starves every other thread. See *Rebuilding the binding*. |

The refresh rate is estimated from the frames themselves (swaps that block on
vsync can only land on refresh boundaries). Cross-check it with
`scroll_speeds.py --measure` if it looks wrong. It can read high on a rig where
nothing ever presented at the full refresh rate.

A soak is only meaningful against a fixed workload. Compare runs with the same
content and `--preview` setting, and alternate which build goes first when you
A/B two of them. A live-API workload drifts over time.

The soak says how often; the service's log says why. A scroll that presents no
frame for 250 ms logs `Render stall:` with the stack of the render thread and
the top of every other thread's, and whether the whole interpreter was blocked
(C code holding the GIL) rather than one thread. To see what is behind the
shorter hitches, run the service with `LEDMATRIX_STALL_WATCHDOG_MS=30`, which
dumps at three refreshes late instead: its extra polling costs a little GIL
time of its own, so do that on a diagnostic run, not a soak you are grading.
`LEDMATRIX_STALL_WATCHDOG=0` turns it off.

### Results: hdpi, 2026-09-24

Pi 4, 4×128×64 on one chain (512×64), `gpio_slowdown` 3, cap 120 Hz, the
GIL-releasing binding. Vegas mode with live content, 8-minute soaks with
`--preview`, run in the order shown so each build went both first and last.

| run | build | pacing | pwm_bits | refresh | late | 1 | 2 | 3–5 | 6+ | freezes |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | main | time-based, blended, 90 px/s | 8 | 94.5 Hz | 6.33% | 2,542 | 74 | 19 | 4 | 0 |
| 2 | #628 | 1 px / refresh | 8 | 100.2 Hz | 0.66% | 238 | 32 | 30 | 5 | 2 |
| 3 | #628 | 1 px / refresh | 8 | 100.3 Hz | 0.70% | 252 | 38 | 26 | 6 | 2 |
| 4 | main | time-based, blended, 90 px/s | 8 | 94.5 Hz | 6.46% | 2,659 | 90 | 10 | 4 | 0 |
| 5 | #628 | 1 px / 2 refreshes (53 px/s) | **7** | 107.2 Hz | 0.32% | 68 | 7 | 4 | 2 | 1 |

- Blending cost the panel refresh rate as well as frames: 94.5 Hz against
  ~100 Hz for the same hardware under whole-pixel pacing.
- The freezes and the 3+ rows in the #628 runs line up with canvas-bound
  plugins fetched on the render thread (`drain_deferred`): `news` took ~320 ms
  and `hockey-scoreboard` ~660 ms there. Moving those
  fetches off the render thread is proposed separately (offscreen rendering).
- Run 5 changed two things at once: the speed, and `pwm_bits` (changed on the
  rig between runs). Its lower late rate cannot be credited to either alone.
- These soaks were taken before the recorder counted 1–2 s stalls as freezes,
  so a stall of that length would be missing from these rows.

### Without the service: `render_bench.py`

The soak measures the service as it really runs: live content, plugin
updates, the web preview. `scripts/render_bench.py` answers the narrower
question underneath: *with nothing else in the way, can this hardware present
every frame on time?* It scrolls a synthetic strip through the production path
-- a real `DisplayManager`, a real `ScrollHelper`, the same `scroll_config`
resolver every ticker uses -- on content that is identical every run, which
makes it the tool for comparing rigs (a Pi 3 against a Pi 4, one HAT against
another) and for A/B testing a change to the render path.

```bash
sudo systemctl stop ledmatrix          # the service owns the GPIO

sudo python3 scripts/render_bench.py                 # 60s at one pixel per refresh
sudo python3 scripts/render_bench.py --seconds 600   # the shipping gate
sudo python3 scripts/render_bench.py --speed 50      # a held (frame_hold 2) speed
sudo python3 scripts/render_bench.py --busy 2        # with threads imitating plugin updates
sudo python3 scripts/render_bench.py --json /tmp/pi4-512x64.json

sudo systemctl start ledmatrix
```

It never starts or stops the service itself, so a crash in it cannot leave
the panel dark. It grades with the same recorder as the soak and prints the
same report, with the same exit status, except that **2** also means the run
could not be set up at all (no root, no panel, a fallback display), so a rig
that was never measured cannot pass by accident.

Two differences from the soak matter:

- **It measures the panel first.** Before scrolling it times bare swaps for a
  few seconds to get the idle refresh rate, and seeds the recorder with it.
  That is what catches a loop that never locked to the panel at all. The first
  version of the bench announced its scrolling state once instead of every
  frame; the state expired, the dirty-tracking skip fired mid-scroll, and the
  loop free-ran at 827 fps. Graded against its own frames that looks perfectly
  steady; graded against the panel's measured rate every frame is early, and
  the run fails as NOT LOCKED. (The soak has no idle measurement, so it checks
  the rate against `limit_refresh_rate_hz` instead: a "refresh" faster than
  the cap cannot have been waiting for the panel.)
- **The stall watchdog prints to the terminal.** A frame held up for more than
  250 ms prints the stack of what held it up, in the middle of the run.

Measured with the first version of the bench on hdpi (Pi 4, 512x64,
`pwm_bits` 8), two-minute runs at one pixel per refresh: 8 of 11,449 frames
late (0.070%), and with `--busy 2` 3 of 11,445 (0.026%). The render path and
the hardware pass on their own. Compare the soak results above, from the same
rig with the service running, for how much of the late rate comes from
everything else.

### The panel is slower while you are rendering into it

The bench prints two refresh rates, and they differ:

| | Pi 4, 512x64, `pwm_bits` 8 |
|---|---|
| idle, timing bare swaps | 100.4 Hz |
| while scrolling | 96.3 Hz |

Both are real. Driving an LED matrix is bit-banging on the same machine, so
`SetImage` over a 512x64 chain contends with the refresh itself and slows it.
The recorder therefore reads the rendering rate back from the frames: swaps
that block on vsync can only return on a refresh boundary, so the low end of
`interval / frame_hold` is the period. The idle figure is still printed,
because the gap between the two is itself a measure of how expensive a frame
is: **a rise in that gap is a render-cost regression even when nothing is
late.**

The practical consequence for config: set `limit_refresh_rate_hz` near the rate
the panel holds *while rendering*, not the idle rate and certainly not a cap it
can never reach. A cap well above the real rate makes `scroll_config` solve
speeds against a refresh that does not exist, which is where "3px every 4
refreshes" comes from.

### Bench-only counters

| line | meaning |
|---|---|
| `duplicate` | frames that advanced no pixels. A crisp fixed-step scroll should show none; any at all means the loop is presenting faster than the strip is moving. |
| `blank` | frames with no visible slice to draw: the helper had no content. Should be zero. |
| `restarts` | how many times the strip was scrolled through end to end. Informational: the bench restarts the strip where a plugin would hand over to the next one. |

`--json` writes the full report plus the panel geometry, the solved speed and
these counters, so two rigs (or one rig before and after a change) can be
compared without re-reading a terminal.

---

## A tear across the middle on fast scrolls

**Symptom:** while text scrolls, the top and bottom halves of the panel look
shifted sideways against each other along a horizontal line at mid-height, and
the shift grows with scroll speed. It shows most in Vegas mode at high speed.

**It is the panel's scan, not the software.** The measured panel, like most
64-row panels, is multiplexed 1:32 (some panels of the same size scan
differently, so check yours): it lights two rows at a time, one from each half
(row 0 with row 32, row 1 with row 33, …), stepping down both halves together
once per refresh. So row 31,
the last row of the top half, lights almost a whole refresh period after row 32
right below it. Your eye follows moving text, and moving content that lights at
different times lands in different places, so the two rows meet with an offset
of roughly

```
offset ≈ scroll speed × refresh period
```

Each frame already reaches the panel whole (`SwapOnVSync` swaps complete frames
between refreshes), so there is nothing to fix in the render path; the shift is
created inside a single refresh. Other panel heights show it too, at the point
where their two scan halves meet.

On the 2×128×64 chain above, which refreshes at about 130 Hz flat out
(7.7 ms per pass):

| scroll speed | offset at the midline |
|---|---|
| 50 px/s (Vegas default) | ~0.4 px |
| 100 px/s | ~0.8 px |
| 150 px/s | ~1.2 px, plainly visible |

### What changes it

Only a shorter scan period (a faster refresh) or a slower scroll. Measure what
the panel actually achieves first. The library prints the rate with a carriage
return and no newline, so read it from the raw journal:

```bash
# set display.hardware.show_refresh_rate to true (web UI, Display tab), restart, then:
journalctl -u ledmatrix --since "-1min" --no-pager -o cat --all | grep -a -oE "[0-9.]+Hz" | tail -5
```

Turn it off again afterwards. Measured on that panel (Pi 4, single chain),
changing one setting at a time from `pwm_bits: 7`, `gpio_slowdown: 3`:

| change | refresh, uncapped | notes |
|---|---|---|
| none | ~130 Hz | the ceiling for this wiring |
| `pwm_bits: 6` | ~138 Hz | barely faster, and half the colour depth |
| `gpio_slowdown: 2` | ~130 Hz | no faster, **and visible glitching**; keep 3 |
| `limit_refresh_rate_hz: 0` | ~130 Hz | Vegas dropped from 100 to 72–95 fps as the refresh thread took more CPU |

None of these helps much, because the time goes into shifting each row's pixels
out: a 2×128 chain pushes 256 pixels per row down one output. What does help is
**fewer pixels per output**. On a bonnet with more than one output (the
`regular` and `classic` mappings have 3; `adafruit-hat` has 1), put each panel
on its own output and set `parallel` to the number of outputs used and
`chain_length` to the panels per output, for example `parallel: 2`,
`chain_length: 1` for two panels. Each refresh then shifts half the data, which
should roughly double the refresh rate and halve the offset. That is a cable
change, so measure again afterwards.

Short of rewiring, keep fast scrolls moderate: at the default 50 px/s the
offset is under half a pixel.

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
