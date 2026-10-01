# Restructuring `DisplayController.run()`

`run()` in [`src/display_controller.py`](../src/display_controller.py) decides
what the panel shows and runs it. This document is the plan for turning it
from one long loop into three parts with clear jobs: an **Arbiter** that
decides, a **ScreenRunner** that runs one screen, and **Sources** that each
know about one kind of content. It covers the target design, the stages that
get there, and how each stage is checked.

The goal is to change how the control flow is organised, not to move code
into more files. Each stage ships as its own PR, and none of them changes
what the panel shows unless that PR says so and updates the golden traces
on purpose.

## Why

- **The priority order is written in branch order, twice.** It is
  Follower, on-demand, WiFi notice, live priority, Vegas, rotation. In
  `run()` that order exists only as the order of `if` blocks. Vegas
  repeats part of it in its interrupt callback (`_check_vegas_interrupt`).
- **Preemption is found by re-checking.** A screen ends early when
  something else changed `current_display_mode` or `is_display_active`
  underneath it. `run()` notices with five separate
  `current_display_mode != active_mode` checks: after an empty pass, in each
  of the two frame loops, after the frame loops, and before rotating.
- **Most recent fixes were ordering bugs** between these branches (#618,
  #644, #649, #652): a lost mode switch, rotating past an on-demand request,
  spinning when every mode is empty.
- **It could not be tested** without threads, real sleeps and stopping the
  loop by raising from a patched method.

## What `run()` does today

Each pass, in order:

1. `loop_pass()` (watchdog). Apply a pending plugin enable/disable.
2. With no modes: dwell 1 s, next pass.
3. Poll on-demand requests and expiry, release plugins loaded only for
   on-demand, tick plugin updates, drop an expired WiFi notice, evaluate
   the schedule (an on-demand session overrides scheduled-off), apply the
   brightness target.
4. **Scheduled off:** blank, dwell up to 60 s. `_blank_while_scheduled_off`
5. **Follower:** render one frame from the leader. `_run_follower_frame`
6. **WiFi notice** (unless on-demand): draw it, dwell 0.5 s. `_show_wifi_notice`.
   It is also polled mid-screen (`_wifi_notice_pending`): the frame loops,
   the dwell sleep and an interrupted Vegas iteration end within about a
   second when one arrives, and a screen cut short resumes after it.
7. **Live priority** (unless on-demand, or Vegas keeps live content in the
   ticker): switch to the next live mode, or resume the rotation. A game
   that goes live during a screen is caught sooner, by
   `_check_live_takeover` in the frame loops and the dwell sleep (at most
   once a second, and not while a live mode is showing).
8. **Vegas** (unless on-demand, or live content preempts it): run one
   iteration of up to `max_cycle_duration`. A completed iteration ends the
   pass, and so does one that yielded for a WiFi notice or the schedule.
   Any other interrupted one falls through to step 9 in the same pass.
9. **One screen:** pick the mode (`_resolve_active_mode`), the plugin
   (`_plugin_for_mode`), draw the first frame through the executor
   (`_dispatch_first_frame`). On no content, rotate at once
   (`_note_empty_pass`, `_skip_failed_plugin_modes`). Otherwise work out the
   bounds (`_track_dynamic_cycle`, `_resolve_durations`,
   `_clamp_to_on_demand`) and the frame rate (`_needs_high_fps`), run the
   125 Hz or 1 Hz frame loop, make up the minimum duration, then pick the
   next mode (`_advance_after_screen`).

The helpers named above were extracted in stage 1 without changing
behaviour. The frame loops, the Vegas branch and every early exit are still
inline in `run()`.

## Target design

```python
def run(self):
    while True:
        inputs = self._drain_inputs()                 # requests, schedule, config, sync
        plan = self.arbiter.decide(self.state, inputs, clock.now())
        outcome = self.runner.run(plan)               # ExitReason + elapsed
        self.state = self.state.after(plan, outcome)  # rotation, on-demand index, live resume
```

### Sources

Each kind of content is a Source. A Source looks at the state and the
inputs and either offers a screen or passes. The Arbiter asks them in this
order:

| Order | Source | Offers a screen when | Today |
|---|---|---|---|
| gate | ScheduledOff | the schedule is off and no on-demand session overrides it | step 4 |
| 1 | Follower | a sync leader is driving this panel | step 5 |
| 2 | OnDemand | a session is active (its mode list, index, expiry and pin) | `_resolve_active_mode` |
| 3 | Wifi | a status message is pending and on-demand is not active | step 6 |
| 4 | Live | a live-priority plugin has live content (round-robin across several) | step 7 |
| 5 | Vegas | Vegas is enabled and nothing above wants the panel | step 8 |
| 6 | Rotation | always: `available_modes[current_mode_index]` | step 9 |

ScheduledOff is a gate in front of the Sources because that is how it works
today: a scheduled-off panel stays blank even for a follower, and only an
on-demand session overrides it.

### Arbiter

```python
Arbiter.decide(state, inputs, now) -> ScreenPlan
```

`decide` is a pure function: it does no I/O, takes no locks and does not
sleep. It can be tested with plain tables of (state, inputs, now) mapped to
an expected plan. It returns a `ScreenPlan`:

| Field | Meaning |
|---|---|
| `source` | which Source won |
| `mode`, `plugin` | what to draw (None for a blank or follower plan) |
| `min_duration`, `max_duration` | from `_resolve_durations` and `_clamp_to_on_demand` |
| `dynamic` | run until the plugin's cycle completes, between min and max |
| `frame_policy` | today `_needs_high_fps` (125 Hz or 1 Hz); see stage 5 |
| `preemptible_by` | the Sources allowed to interrupt this plan mid-screen |

### ScreenRunner

```python
ScreenRunner(clock: FrameClock).run(plan) -> Outcome(exit_reason, elapsed)
```

The ScreenRunner draws the first frame (`_dispatch_first_frame`), runs the
frame loop that the plan's frame policy selects, services pending changes
between frames, and returns one `ExitReason`:

| ExitReason | Today's equivalent (golden-trace exit) |
|---|---|
| `DURATION` | target duration reached (`duration`) |
| `CYCLE_COMPLETE` | dynamic plugin finished after its minimum (`cycle-complete`) |
| `EMPTY` | first frame returned False (`empty`; `raised` when display() raised inside the executor) |
| `ERROR` | the dispatch itself raised (`error`) |
| `DISPLAY_FALSE` | a later frame returned False (`display-false`) |
| `PREEMPTED` | another Source took the panel (`on-demand-*`, `schedule-off`, `vegas-interrupt`, ...) |

`PREEMPTED` replaces the five `current_display_mode != active_mode` checks.
The runner asks the Arbiter, at the throttled service points it already has,
whether a Source in `plan.preemptible_by` now wants the panel.

`FrameClock` provides `now()` and `sleep()`. In production it is
`time.monotonic`/`time.sleep`. In the golden traces it is the fake clock
that the harness patches in today.

## Stages

| Stage | Change | Behaviour change | Verified by |
|---|---|---|---|
| 1 | Golden traces; extract helpers from `run()` | none | traces generated on main pass unchanged; mutation check |
| 2 | Arbiter with Follower and Wifi Sources | none | traces unchanged; Arbiter unit tables; ledpi smoke |
| 3 | ScreenRunner, FrameClock, ExitReason, `PREEMPTED`; OnDemand, Live, Rotation Sources | none | traces unchanged; ledpi frame soak A/B |
| 4 | Vegas as a Source driven by `run_frame()` | none intended | traces against the real coordinator; ledpi Vegas soak A/B |
| 5 | Plugins declare `frame_policy` | DEBUG instead of INFO for the FPS line | traces; soak on a static-heavy rotation |

### Stage 1 (this PR)

- `test/_run_loop_harness.py` builds a real `DisplayController` through
  `__init__` on in-memory fakes (plugins, cache, config service, plugin
  manager, sync manager, display manager). It swaps the module's `time` and
  `datetime` for one fake clock and runs the real `run()` until a horizon.
  The first frame of each screen still goes through the real
  `PluginExecutor` and the per-plugin locks.
- `test/test_run_loop_golden.py` has 15 scenarios, each compared with
  `test/fixtures/run_loop_golden/<scenario>.json`:
  - plain rotation (display_durations override, a high-FPS scroller, a
    plugin whose `display()` takes no `display_mode`)
  - empty modes and a mode with no plugin; an all-empty rotation (the 1 s
    pause)
  - plugin errors and the circuit breaker
  - dynamic duration (cycle complete, plugin cap, global cap)
  - live priority taking over and handing back; live round-robin
  - on-demand start/stop/expiry; pinned on-demand; a session resumed after
    a restart
  - schedule off and dim, with an on-demand override during downtime
  - WiFi notice; sync follower
  - Vegas, with and without `live_in_ticker`
- Each trace row is `[start, mode, duration, exit_reason, frames,
  force_clear]`. The exit reason is the event that decided what came next.
- All 16 tests run in under a second. The goldens were generated from
  main's `run()` before any code moved.
- Vegas uses `FakeVegas`, which implements only the contract the controller
  depends on: `run_iteration()` returns True after its duration and False
  when the interrupt or live check asks it to yield, checking at the real
  coordinator's cadence. Running the real coordinator on the fake clock
  belongs to stage 4.
- Twelve helpers were extracted from `run()` (listed under "What `run()`
  does today"). Breaking any one of them fails at least one golden trace.

### Stage 2: Arbiter, starting with Follower and Wifi

1. Add `ScreenPlan` and an `Arbiter` with the ScheduledOff gate, Follower
   and Wifi. Every other case returns a `LEGACY` plan, which means "carry on
   with the existing code" (steps 7-9).
2. `run()` calls `decide()` after the bookkeeping in step 3 and dispatches
   on `plan.source`: blank, `_run_follower_frame()`, the WiFi notice, or the
   existing path. Inputs that Sources read (follower active, the pending
   WiFi message, schedule state) are collected first, so `decide()` stays
   pure.
3. Unit-test `decide()` with tables. The golden traces must not change.
   The Wifi Source must keep the mid-screen preemption described in step 6
   of "What `run()` does today".

Follower and Wifi go first because each is one self-contained branch that
ends the pass. They prove the plumbing without touching the frame loops.

### Stage 3: ScreenRunner and `PREEMPTED`

Move the two frame loops, the make-up dwell and the dynamic-duration exit
into `ScreenRunner.run(plan)` with an injected `FrameClock`. Replace the
five re-checks with `PREEMPTED`. Add the OnDemand, Live and Rotation Sources
so `LEGACY` is left meaning only Vegas.

This stage touches frame pacing (the 8 ms deadline sleep, the 1 ms yield),
so it needs a frame soak on ledpi, A/B against main. Coordinate with
whoever owns scroll performance (`docs/SCROLL_PERFORMANCE.md`).

### Stage 4: Vegas as a Source

The controller calls `coordinator.run_frame()` once per frame from the
ScreenRunner instead of handing over to `run_iteration()` for up to
`max_cycle_duration`. The interrupt callback and the second copy of the
priority order go away, because preemption becomes `PREEMPTED`. The
`vegas-plugin-tick` thread that is spawned every 4 s becomes the
controller's normal update tick. Extend the harness to drive the real
coordinator on the fake clock, which means patching its `time` and running
its prefetch inline. Verify with a Vegas soak on ledpi, A/B.

### Stage 5: `frame_policy`

Plugins declare `frame_policy` (STATIC, PERIODIC(hz), ANIMATED(fps),
SCROLL). `_needs_high_fps` becomes the mapping for legacy plugins
(`needs_high_fps`, the `static-image` special case, `enable_scrolling`),
and its per-screen INFO line drops to DEBUG.

## How each stage is verified

- **Golden traces.** Run `python -m pytest test/test_run_loop_golden.py`;
  it takes about a second. A refactoring stage must leave every trace
  unchanged. A deliberate behaviour change regenerates them with
  `LEDMATRIX_REGEN_GOLDEN=1` in its own commit, and the commit message
  explains each changed row. A new scenario's golden is generated against
  main's `run()` first, then checked against the branch.
- **Mutation check.** Break each moved or new piece once, for example take
  `max` of the caps instead of `min`, or skip the live hold. At least one
  trace must fail each time. Stage 1 did this for all twelve helpers.
- **Full suite.** Diff the FAILED/ERROR ids against a baseline run of main
  in a separate worktree. The Windows host has a stable set of
  pre-existing failures, so never compare against zero.
- **ledpi soak** (stages 2-5). With the service running the branch:
  `python3 scripts/frame_soak.py --preview` for 10 minutes on a scrolling
  rotation, and on Vegas for stages 3-4. Alternate which build goes first.
  Compare late-frame rate and freezes with main. Also check by hand that
  on-demand start, stop and expiry, a live game taking over and handing
  back, and the schedule turning the panel off and on all behave as before.

## Behaviour the traces pin down that may be wrong

These are recorded as they are today. Each one should be fixed in its own
PR, which updates the affected trace and explains why. None of them is
changed by the restructure.

1. **An on-demand session that expires during scheduled-off keeps the panel
   on** until the next minute boundary, because the schedule check runs at
   most once a minute (`schedule`, t=190-210).
2. **A schedule window's end minute is inclusive**, and whether the panel
   turns off at the start of that minute or the end depends on when in the
   minute the first check runs.
