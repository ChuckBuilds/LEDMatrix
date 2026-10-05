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

1. `loop_pass()` (watchdog). Apply a pending plugin enable/disable, then
   any plugin reloads the control socket asked for
   (`_apply_pending_plugin_reloads`; a pending reload ends the screen
   before it, like a WiFi notice, as `Source.RELOAD` at the runner's
   service points). The static screen's frame sleep and the dwell wait on
   the socket's queue instead of sleeping (`_wait_frame_interval`,
   `_sleep_with_plugin_updates`); without a socket, as in the golden
   traces, they are the plain sleeps.
2. With no modes: dwell 1 s, next pass.
3. Poll on-demand requests and expiry, release plugins loaded only for
   on-demand, tick plugin updates, drop an expired WiFi notice, evaluate
   the schedule (an on-demand session overrides scheduled-off), apply the
   brightness target. Then gather the Arbiter's inputs
   (`_arbiter_inputs`) and call `Arbiter.decide()`.
4. **Scheduled off:** blank, dwell up to 60 s. `_blank_while_scheduled_off`
5. **Follower:** render one frame from the leader. `_run_follower_frame`
6. **WiFi notice** (unless on-demand): draw it, dwell 0.5 s. `_show_wifi_notice`.
   It also ends a running screen within about a second (the runner's
   service points), and a screen cut short resumes after it.
7. **The Sources below the notice:** read whether Vegas is on and make the
   live-priority scan (`_arbiter_inputs_below_wifi`, where run() always
   read them), and call `decide()` again. It answers OnDemand (the
   session's current mode), Live (the next live mode, round-robin; a game
   that goes live during a screen takes over at the next service point, at
   most once a second), Vegas (`LEGACY`) or Rotation. `_take_plan` applies
   the answer: a live claim or the resume when live priority ends, the
   on-demand index.
8. **Vegas** (`_run_vegas_iteration`): one iteration of up to
   `max_cycle_duration`. A completed iteration ends the pass, and so does
   one that yielded for the schedule, a reload or a WiFi notice. Any other
   interrupted one asks `decide()` once more (`vegas_yielded`): a game that
   stopped the ticker, or an on-demand session that started, shows next.
9. **One screen:** pick the plugin (`_plugin_for_mode`) and hand the plan
   to the `ScreenRunner` (`src/screen_runner.py`). It draws the first frame
   through the executor (`_dispatch_first_frame`), has the controller fill
   in the plugin's durations, dynamic flag and frame policy
   (`_complete_plan`), runs the 125 Hz or 1 Hz frame loop with a service
   point after each frame, makes up the minimum duration, and returns an
   `Outcome`. On `PREEMPTED` the pass ends without advancing. On no
   content, rotate at once (`_note_empty_pass`, `_skip_failed_plugin_modes`).
   Otherwise `ArbiterState.after()` picks the next mode
   (`_advance_after_screen`).

## Design

```python
def run(self):
    while True:
        inputs = self._arbiter_inputs()                     # schedule, follower, notice
        plan = Arbiter.decide(self._arbiter_state(), inputs, now)
        ...                                                 # off / follower / notice
        plan = self._take_plan(Arbiter.decide(state, self._arbiter_inputs_below_wifi(inputs), now))
        if plan.source is Source.LEGACY:                    # Vegas, until stage 4
            plan = self._run_vegas_iteration(...)
        outcome = runner.run(plan, plugin)                  # ExitReason + elapsed
        if outcome.exit_reason is not ExitReason.PREEMPTED:
            self._advance_after_screen(plan, outcome)       # ArbiterState.after
```

The controller's attributes (`current_display_mode`, `current_mode_index`,
`on_demand_*`, `_live_resume_index`) stay the record that the web UI, the
control socket and the on-demand cache read. `_arbiter_state()` snapshots
them into a frozen `ArbiterState`; the transitions are pure methods on it,
and the controller writes their result back (`_adopt_state`).

### Sources

Each kind of content is a Source. A Source looks at the state and the
inputs and either offers a screen or passes. The Arbiter asks them in this
order:

| Order | Source | Offers a screen when | Code |
|---|---|---|---|
| gate | ScheduledOff | the schedule is off and no on-demand session overrides it | `decide` |
| 1 | Follower | a sync leader is driving this panel | `decide` |
| 2 | OnDemand | a session is active (its mode list, index, expiry and pin) | `_on_demand_plan` |
| 3 | Wifi | a status message is pending and on-demand is not active | `decide` |
| 4 | Live | a live-priority plugin has live content (round-robin across several) | `live_pick` |
| 5 | Vegas | Vegas is enabled and nothing above wants the panel | `LEGACY`, run by `_run_vegas_iteration` |
| 6 | Rotation | always: the rotation's current mode | `rotation_plan` |

ScheduledOff is a gate in front of the Sources because that is how it works
today: a scheduled-off panel stays blank even for a follower, and only an
on-demand session overrides it.

The Rotation answers `state.current_mode`, not
`available_modes[current_mode_index]`: the two agree except where something
moved the panel off the list and the rotation carries on from there (a live
mode no rotation entry names, or None after a session ended with no enabled
mode to resume to), and `run()` always showed `current_display_mode`.

### Arbiter

```python
Arbiter.decide(state, inputs, now, running=None) -> ScreenPlan
```

`decide` is a pure function: it does no I/O, takes no locks and does not
sleep. It can be tested with plain tables of (state, inputs, now) mapped to
an expected plan.

- `ArbiterState`: the current mode; the rotation and its index; the
  on-demand session's modes, index, expiry and pin; the live resume point;
  whether a mid-screen takeover has not shown yet. Transitions:
  `next_on_demand`, `showing`, `claim_live`, `release_live`, `after`.
- `ArbiterInputs`: whether the schedule has the panel on, an on-demand
  session, a follower, the WiFi notice, the live modes (None where no scan
  was made), whether Vegas is on and keeps live content in its ticker,
  whether this pass's Vegas iteration has yielded, and (mid-screen) whether
  a plugin reload is waiting.
- `ScreenPlan`:

| Field | Meaning |
|---|---|
| `source` | which Source won |
| `mode`, `plugin` | what to draw (None for a blank or follower plan); the plugin id once resolved |
| `min_duration`, `max_duration` | from `_resolve_durations` and the on-demand bound (`on_demand_bound`), filled in after the first frame; an on-demand plan's `max_duration` is what is left of the session at `now` |
| `dynamic` | run until the plugin's cycle completes, between min and max |
| `frame_policy` | `HIGH_FPS` or `STATIC`, today `_needs_high_fps`; see stage 5 |
| `preemptible_by` | the Sources allowed to interrupt this plan mid-screen |
| `notice`, `deadline`, `ends_live` | the WiFi notice; the on-demand expiry for the bound; "live priority just ended, resume the rotation first" |

`decide` cannot ask a plugin anything, so the fields a plugin answers are
filled in by the controller after the first frame, where they were always
read (`_complete_plan`).

With `running`, `decide` answers the mid-screen question instead: `running`
itself while the screen holds, else the plan that ends it
(`_hold_or_preempt`), in the order the frame loops always checked:

1. Live: a game went live while a non-live screen runs. It is the one
   preemption that changes the state (the rotation moves to the live mode
   and remembers where it was), and it is claimed even when a WiFi notice
   is also pending; the next pass shows the notice, then the game.
2. The panel's mode moved under the screen (on-demand started, ended or
   changed mode; the rotation was rebuilt).
3. The schedule turned the panel off.
4. A WiFi notice (unless on-demand outranks it), compared with its expiry.
5. A plugin reload is waiting (between frames only).

Every screen is preemptible by the gate, OnDemand, Wifi, Live, Rotation and
a reload (`SCREEN_PREEMPTERS`), except that a live screen leaves Live out
(`LIVE_PREEMPTERS`): live games take turns between screens. A follower and
Vegas are looked at only between screens.

### ScreenRunner

```python
ScreenRunner(clock: FrameClock, host: ScreenHost).run(plan, plugin) -> Outcome
```

The ScreenRunner draws the first frame (`_dispatch_first_frame`), runs the
frame loop that the plan's frame policy selects, services pending changes
between frames, and returns one `ExitReason`:

| ExitReason | Golden-trace exit |
|---|---|
| `DURATION` | target duration reached (`duration`) |
| `CYCLE_COMPLETE` | dynamic plugin finished after its minimum (`cycle-complete`) |
| `EMPTY` | first frame returned False, or no plugin (`empty`; `raised` when display() raised inside the executor; `no-plugin`, `breaker`) |
| `ERROR` | the dispatch itself raised (`error`) |
| `DISPLAY_FALSE` | a later frame returned False (`display-false`) |
| `PREEMPTED` | another Source took the panel (`on-demand-*`, `schedule-off`, `live`, `wifi`, ...) |
| `RELOAD` | a plugin reload is waiting: the screen ends early but counts as shown, and the rotation advances |

`PREEMPTED` replaces the five `current_display_mode != active_mode` checks.
The runner asks its host at named service points (`Checkpoint`): `FRAME`
after each frame (and when a socket command wakes the 1 Hz wait),
`AFTER_LOOP` / `AFTER_COMPLETED_LOOP` when the frame loop ends,
`after_dwell` after the make-up dwell, and `FINAL` before the rotation
advances. Each is one `decide(..., running=plan)` call
(`DisplayController._screen_check`). The checkpoint says whether a pending
reload counts there and when the WiFi notice file is read (`NoticeRead`):
the read is throttled to once a second and deletes an expired file, so it
happens exactly where the loop always read it.

In the 125 Hz loop the live-priority scan is made before the frame's sleep
(`_screen_service`), at the moments it always was, and weighed by the
service point after the sleep, where the loop always decided to end the
screen.

`FrameClock` provides `time()`, `perf_counter()` and `sleep()`, the shape of
the `time` module. In production it is `_ModuleClock`, which looks up
`src.display_controller.time` on each call, so the golden traces' fake clock
drives the runner as it drove the inline loops. The runner's log lines use
the controller's logger, so they keep their source in the journal.

## Stages

| Stage | Change | Behaviour change | Verified by |
|---|---|---|---|
| 1 | Golden traces; extract helpers from `run()` | none | traces generated on main pass unchanged; mutation check |
| 2 | Arbiter with Follower and Wifi Sources | none | traces unchanged; Arbiter unit tables; ledpi smoke |
| 3 | ScreenRunner, FrameClock, ExitReason, `PREEMPTED`; OnDemand, Live, Rotation Sources | none | traces unchanged; ledpi frame soak A/B |
| 4 | Vegas as a Source driven by `run_frame()` | none intended | traces against the real coordinator; ledpi Vegas soak A/B |
| 5 | Plugins declare `frame_policy` | DEBUG instead of INFO for the FPS line | traces; soak on a static-heavy rotation |

### Stage 1 (#704)

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
    a restart, and one that cannot resume (its plugin did not load); a
    request naming a live mode the plugin's live check would drop
  - schedule off and dim, with an on-demand override during downtime
  - WiFi notice; sync follower
  - Vegas, with and without `live_in_ticker`
- Each trace row is `[start, mode, duration, exit_reason, frames,
  force_clear]`. The exit reason is the event that decided what came next.
- All 18 tests run in under a second. The goldens were generated from
  main's `run()` before any code moved.
- Vegas uses `FakeVegas`, which implements only the contract the controller
  depends on: `run_iteration()` returns True after its duration and False
  when the interrupt or live check asks it to yield, checking at the real
  coordinator's cadence. Running the real coordinator on the fake clock
  belongs to stage 4.
- Twelve helpers were extracted from `run()` (listed under "What `run()`
  does today"). Breaking any one of them fails at least one golden trace.

### Stage 2: Arbiter, starting with Follower and Wifi (done)

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

What shipped:

- `src/display_arbiter.py` (on the mypy ratchet) holds `Source`
  (`SCHEDULED_OFF`, `FOLLOWER`, `WIFI`, `LEGACY`), `ArbiterInputs`,
  `ArbiterState`, `WifiNotice`, `ScreenPlan` and `Arbiter.decide`.
  `ScreenPlan` has only the fields stage 2 uses: `source`, `max_duration`
  (60 s for the blank, 0.5 s for the notice, the constants `run()` used to
  hard-code) and `notice`. `mode`, `plugin`, the other durations,
  `frame_policy` and `preemptible_by` arrive with the Sources that need them.
- `ArbiterState` is empty: no stage-2 Source remembers anything between
  passes. `now` is passed but not read, because the top-of-pass WiFi check
  never compared the expiry and must not start (the table pins this).
- `ArbiterInputs` holds `schedule_on`, `on_demand_active`,
  `follower_active` and `wifi_notice`. `_arbiter_inputs` derives
  `schedule_on` as `is_display_active and not on_demand_schedule_override`,
  so the gate (blank when the schedule is off and no on-demand session
  overrides it) blanks exactly when `is_display_active` is False, as before,
  including #714's on-demand ending in off hours. It reads the WiFi notice
  only when the notice could win, because `_check_wifi_status_message` has
  side effects (its 1 Hz throttle, deleting an expired file) that those
  passes never had.
- The mid-screen rule is `wifi_notice_preempts(notice, on_demand, now)`,
  which `_wifi_notice_pending` calls; it does compare the expiry.
- `run()` still calls `_publish_current_mode_state_if_changed`,
  `_apply_pending_vegas_init` and `process_deferred_updates` at the same
  points relative to the branches, so the order of side effects in a pass
  is unchanged.
- `test/test_display_arbiter.py`: the 16-row table (every combination of
  the four inputs, written out), the mid-screen table, purity checks (no
  clock reads, nothing mutated, no I/O imports), and the controller's
  snapshot through an on-demand session that overrides the schedule and
  ends. A mutation run broke 23 pieces once each (the gate, the order, each
  Source, the dwells, the expiry comparison, the snapshot's reads, each
  dispatch in `run()`); every one failed a test.

### Stage 3: ScreenRunner and `PREEMPTED` (done; awaiting the ledpi soak)

The plan, from where stage 2 left off:

1. `ArbiterState` gains the rotation index, the on-demand mode list, index,
   expiry and pin, and the live resume point. `ArbiterInputs` gains the
   live modes and whether Vegas is enabled and keeps live content in the
   ticker.
2. OnDemand returns its current mode with the session's bound, reading
   `now` for the expiry. Live returns the next live mode (round-robin).
   Rotation returns the rotation's mode. `ScreenPlan` gains `mode`,
   `plugin`, `min_duration`, `max_duration`, `dynamic`, `frame_policy` and
   `preemptible_by`.
3. `ScreenRunner.run(plan)` returns an `ExitReason`; `state.after(outcome)`
   replaces `_advance_after_screen`'s step and the live-resume bookkeeping.
   Each mid-screen check asks `decide()` whether a Source in
   `plan.preemptible_by` now wins.
4. The control socket (`_drain_control_commands`, `_wait_for_control`) and
   state publishing stay where they are; the runner calls them at its
   service points.

What shipped, one commit each: the runner; then the OnDemand, Live and
Rotation Sources; then one `decide()` call at the service points.

- `src/screen_runner.py` (on the mypy ratchet): `ScreenRunner`,
  `FrameClock`, `ExitReason`, `Outcome`, `Checkpoint`, `NoticeRead`,
  `Screen` and the `ScreenHost` protocol, which `DisplayController`
  implements through `_ScreenHost` (one-line forwards to its own methods).
  The two frame loops, the make-up dwell and the dynamic-duration exit
  moved in unchanged, pacing included.
- `src/display_arbiter.py`: `Source` gains `ON_DEMAND`, `LIVE`, `ROTATION`
  and `RELOAD`; `LEGACY` means only Vegas. `FramePolicy`. `ArbiterState`
  and `ArbiterInputs` as listed under "Arbiter". The pure helpers
  `on_demand_bound` (`_clamp_to_on_demand`), `live_pick`
  (`_check_live_priority`'s pick), `live_takeover` (the mid-screen claim)
  and `rotation_plan`.
- A pass asks `decide()` twice: once with the inputs every pass reads, and
  once, only when nothing above the notice took the panel, with the Vegas
  check and the live scan, read where run() always read them (the scan
  asks every live-priority plugin, and the Vegas check applies queued
  Vegas config, so reading them earlier, on a follower or notice pass,
  would be a change). A Vegas iteration that yields asks a third time.
- `_resolve_active_mode`, `_clamp_to_on_demand` and `_screen_preempted` are
  gone. `_apply_live_priority`, `_check_live_priority`,
  `_check_live_takeover` and `_wifi_notice_pending` remain (Vegas, the
  dwell sleep and the tests call them), built on the same pure rules.
- `_sleep_with_plugin_updates` keeps its own break rules. It also serves
  the blank, the notice and the idle wait, which are not screens, and its
  rules are edge-triggered (an on-demand session starting on the mode
  already showing ends a dwell but not a frame loop); folding them into
  `decide()` would change behaviour.

Behaviour, checked three ways:

- Golden traces: unchanged, no regeneration.
- Every harness run in the suite (67: the goldens plus the live-takeover,
  WiFi+live, socket-wake, plugin-reload, schedule and tick tests) was
  captured with every sleep, `display()` call, WiFi read, live scan,
  publish, dwell and scroll-state call logged, and diffed against
  `origin/main`. Identical, except:
  - a Vegas pass used to scan the live plugins twice at the same instant
    (step 7, then step 8's "is anything live?"); it scans once;
  - `_apply_live_priority(None)` calls that changed nothing are not made;
  - throttled WiFi reads that returned the cached answer (no side effect)
    after a notice had already ended the screen are not made;
  - in the 125 Hz loop the live scan still runs before the frame's sleep,
    but the claim is made by the service point after it, so the "live"
    state change happens 8 ms later. The screen ends at the same frame as
    before.
- Tables: `test/test_display_arbiter.py` (OnDemand, Live, Vegas/Rotation,
  `after`, the 24-row mid-screen table, `live_takeover`) and
  `test/test_screen_runner.py` (the runner on a scripted host and fake
  clock; the controller's service point and the reads it makes). A
  mutation run broke each moved or new piece once; see the PR.

This stage touches frame pacing (the 8 ms deadline sleep, the 1 ms yield),
so it needs a frame soak on ledpi, A/B against main, before it merges.
Coordinate with whoever owns scroll performance (`docs/SCROLL_PERFORMANCE.md`).

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
and its per-screen INFO line drops to DEBUG. It is already read twice per
screen: once quietly before the first frame, so `_dispatch_first_frame` can
end the previous scroll for a screen that runs the 1 Hz loop
(`_start_screen_handover`), and once after it to pick the loop. A declared
policy answers both.

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

Stage 1 recorded six behaviours as they were, each to be fixed in its own
PR that updates the affected trace and explains why. All six are fixed:

- A WiFi notice was only checked between screens, and Vegas yielded to one
  and then showed a rotation screen instead. Notices now preempt within
  about a second, and Vegas yields straight to them (#712; `wifi_notice`,
  `vegas`).
- A live game only took over between screens, and Vegas yielded to one and
  then showed a rotation screen first. Games now take over within about a
  second, and Vegas yields straight to them (#713; `live_priority`,
  `vegas`).
- An on-demand session that ended during scheduled-off kept the panel on
  until the next minute, and a schedule window's end minute counted as on
  only sometimes. Windows are now half-open `[start, end)`, and the panel
  blanks as soon as on-demand ends in off hours (#714; `schedule`).

A new one found later goes the same way: record it here with the trace that
shows it, then fix it in its own PR, not inside a restructure stage.

Open:

- Vegas stops for a sync follower (its interrupt check includes
  `is_follower_active`), but the yield path never looks at a follower, so a
  full rotation screen (20 s in the test) runs before the next pass hands
  the panel to the leader. Found by stage 3's mutation run;
  `test_screen_runner.py::TestThroughRun::test_vegas_yielding_to_a_follower_shows_a_rotation_screen_first`
  pins it. Stage 4, which drops the interrupt callback, is the natural
  place to fix it.
