"""Runs one screen: the ScreenRunner of docs/RUN_LOOP_REDESIGN.md.

``ScreenRunner.run(plan, plugin)`` draws a screen's first frame, runs the
frame loop its plan's ``frame_policy`` picks (125 Hz or 1 Hz), makes up the
minimum duration when the loop ended early, and returns one
:class:`Outcome` saying why the screen ended. Everything that touches the
plugin, the panel or the controller's state goes through a
:class:`ScreenHost` (the DisplayController); everything that reads or waits
on the clock goes through an injected :class:`FrameClock`. The runner itself
holds no state between screens.

What can end a screen early is decided at the runner's service points: after
each frame, after the frame loop, and after the make-up dwell. At each one
the host gathers a snapshot and asks the Arbiter, once, whether a Source in
``plan.preemptible_by`` now wants the panel (:meth:`ScreenHost.check`). A yes
is ``ExitReason.PREEMPTED``: the next pass of the loop decides what shows,
and the rotation does not advance past the screen that was cut short.

The frame pacing is the loop that used to be inline in
``DisplayController.run()``, unchanged: the 125 Hz loop paces to an 8 ms
deadline from the start of each frame (sleeping at least 1 ms, so a frame
that overran still yields the GIL), and the 1 Hz loop sleeps a flat second
between frames, woken early by a control socket command.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, NamedTuple, Optional, Protocol, Tuple

from src.display_arbiter import FramePolicy, ScreenPlan, Source

__all__ = [
    "AFTER_COMPLETED_LOOP",
    "AFTER_LOOP",
    "Checkpoint",
    "DYNAMIC_GRACE",
    "ExitReason",
    "FINAL",
    "FRAME",
    "FirstFrame",
    "FrameClock",
    "HIGH_FPS_INTERVAL",
    "NoticeRead",
    "Outcome",
    "STATIC_INTERVAL",
    "Screen",
    "ScreenHost",
    "ScreenRunner",
    "after_dwell",
]

#: Seconds between frames in the high-FPS loop (125 Hz), for scrolling plugins.
HIGH_FPS_INTERVAL = 0.008

#: Seconds between frames in the static loop (1 Hz).
STATIC_INTERVAL = 1.0

#: A dynamic-duration screen ends on cycle completion only this long after its
#: minimum, so timing jitter around the minimum can't end it early.
DYNAMIC_GRACE = 0.5


class ExitReason(Enum):
    """Why a screen ended. The value is the golden traces' exit column where
    one exists (test/test_run_loop_golden.py)."""

    #: The screen ran its target duration.
    DURATION = "duration"
    #: A dynamic-duration plugin finished its cycle after its minimum.
    CYCLE_COMPLETE = "cycle-complete"
    #: The first frame had nothing to show (display() returned False or
    #: raised inside the executor), or no plugin draws the mode.
    EMPTY = "empty"
    #: The first frame's dispatch itself raised.
    ERROR = "error"
    #: A later frame returned False (a dynamic-duration screen on the 1 Hz
    #: loop keeps going instead).
    DISPLAY_FALSE = "display-false"
    #: Another Source took the panel, or an on-demand session ran out before
    #: the screen began. The rotation does not advance.
    PREEMPTED = "preempted"
    #: A plugin reload is waiting for the top of the loop. The screen is cut
    #: short but counts as shown: the rotation advances, and the next pass
    #: reloads before it draws.
    RELOAD = "reload"


@dataclass(frozen=True)
class Outcome:
    """How a screen ended.

    Attributes:
        exit_reason: Why it ended.
        elapsed: Seconds from the end of the first frame to the end.
        preempted_by: For PREEMPTED, the plan that took the panel when the
            Arbiter named one (None when the session simply ran out).
        on_demand_active: Filled in by the controller when the screen is
            over: an on-demand session was running at that moment.
        still_live: Filled in by the controller: the mode's plugin still had
            live content at that moment, which holds the rotation on it.
    """

    exit_reason: ExitReason
    elapsed: float = 0.0
    preempted_by: Optional[ScreenPlan] = None
    on_demand_active: bool = False
    still_live: bool = False


class FrameClock(Protocol):
    """The clocks the runner reads and the sleep it paces with.

    The shape of the ``time`` module, so production passes it (through an
    indirection that lets tests patch the module) and the golden traces pass
    their fake clock.
    """

    def time(self) -> float:
        """Wall-clock seconds: what screen durations are measured in."""

    def perf_counter(self) -> float:
        """A monotonic high-resolution clock: what the 8 ms pacing reads."""

    def sleep(self, seconds: float) -> None:
        """Block for ``seconds``."""


class NoticeRead(Enum):
    """When a service point reads the WiFi notice file.

    The read is throttled to once a second and deletes an expired file, so
    *when* it happens is behaviour: each service point reads it exactly
    when the loop always did.
    """

    #: Not at all.
    NEVER = "never"
    #: Only if nothing cheaper has already ended the screen: no on-demand
    #: session, the panel on, the mode unchanged and no live takeover.
    IF_UNDECIDED = "if-undecided"
    #: Whenever no on-demand session is running.
    ALWAYS = "always"


@dataclass(frozen=True)
class Checkpoint:
    """What one kind of service point considers.

    Attributes:
        name: For logs and tests.
        notice: When the WiFi notice is read (see NoticeRead).
        notice_counts: Whether a pending notice ends the screen here. After
            the make-up dwell it does only while the screen had time left.
        reload: Whether a pending plugin reload ends the screen here. Only
            between frames: once the frame loop is over the screen is too.
    """

    name: str
    notice: NoticeRead
    reload: bool
    notice_counts: bool = True


#: Between frames: the frame loops' check, and the socket wake in the 1 Hz wait.
FRAME = Checkpoint("frame", NoticeRead.IF_UNDECIDED, reload=True)
#: After a frame loop that ended early (display() returned False, a reload).
AFTER_LOOP = Checkpoint("after-loop", NoticeRead.IF_UNDECIDED, reload=False)
#: After a frame loop that ran its course: only a mode change or the schedule.
AFTER_COMPLETED_LOOP = Checkpoint("after-loop", NoticeRead.NEVER, reload=False)
#: The last look before the rotation advances.
FINAL = Checkpoint("final", NoticeRead.NEVER, reload=False)


def after_dwell(time_left: bool) -> Checkpoint:
    """After the make-up dwell: a notice that cut it short ends the screen,
    so the mode resumes after the notice instead of rotating past it."""
    return Checkpoint("after-dwell", NoticeRead.ALWAYS, reload=False,
                      notice_counts=time_left)


class FirstFrame(NamedTuple):
    """What the first frame's dispatch returned (see _dispatch_first_frame)."""

    shown: bool
    raised: bool
    accepts_display_mode: bool


@dataclass
class Screen:
    """One running screen: its completed plan, the plugin drawing it and
    when it started. Mutable only in that the runner owns it."""

    plan: ScreenPlan
    plugin: Any
    accepts_display_mode: bool
    start: float

    @property
    def mode(self) -> Optional[str]:
        return self.plan.mode


class ScreenHost(Protocol):
    """The controller's side of a screen. See DisplayController."""

    def first_frame(self, plan: ScreenPlan, plugin: Any) -> FirstFrame:
        """Draw the first frame through the plugin executor."""

    def complete_plan(self, plan: ScreenPlan, plugin: Any) -> Optional[ScreenPlan]:
        """The plan with the plugin's durations, dynamic flag and frame
        policy, read after the first frame. None when an on-demand session
        has no time left for it."""

    def draw(self, screen: Screen) -> Any:
        """One later frame: what display() returned."""

    def after_frame(self, screen: Screen) -> None:
        """After a frame that did not end the screen (the follower frame)."""

    def tick(self) -> None:
        """Plugin updates that have come due (throttled)."""

    def service(self, screen: Screen) -> Optional[Tuple[str, ...]]:
        """Apply pending changes (on-demand requests, schedule, brightness,
        finished reloads). Returns the live modes when a live-priority scan
        was due, else None."""

    def wait_frame(self, interval: float, screen: Screen) -> Optional[ScreenPlan]:
        """The 1 Hz loop's sleep between frames. The plan that takes the
        panel when a control socket command ended the screen, else None."""

    def check(self, screen: Screen, checkpoint: Checkpoint,
              live_scan: Optional[Tuple[str, ...]] = None) -> Optional[ScreenPlan]:
        """The service point: the plan that now takes the panel from this
        screen, or None while it holds. One Arbiter.decide() call."""

    def dwell(self, seconds: float) -> None:
        """Sleep up to ``seconds``, servicing changes; returns early on one."""

    def cycle_complete(self, screen: Screen) -> bool:
        """The plugin's dynamic-duration cycle is complete."""


class ScreenRunner:
    """Runs one screen at a time for a ScreenHost. See the module docstring."""

    def __init__(self, clock: FrameClock, host: ScreenHost,
                 log: Optional[logging.Logger] = None):
        self.clock = clock
        self.host = host
        # The controller passes its own logger, so these lines keep the
        # source they always had in the journal.
        self.log = log or logging.getLogger(__name__)

    # -- the screen ------------------------------------------------------

    def run(self, plan: ScreenPlan, plugin: Any) -> Outcome:
        """Run ``plan``'s screen, drawn by ``plugin`` (None: nothing draws it)."""
        if plugin is None:
            return Outcome(ExitReason.EMPTY)
        first = self.host.first_frame(plan, plugin)
        if not first.shown:
            return Outcome(ExitReason.ERROR if first.raised else ExitReason.EMPTY)
        completed = self.host.complete_plan(plan, plugin)
        if completed is None:
            return Outcome(ExitReason.PREEMPTED)
        screen = Screen(completed, plugin, first.accepts_display_mode,
                        start=self.clock.time())

        if completed.frame_policy is FramePolicy.HIGH_FPS:
            reason, by = self._high_fps_loop(screen)
        else:
            reason, by = self._static_loop(screen)
        if reason is ExitReason.PREEMPTED:
            # The service point that ended the loop has decided; looking
            # again now, at the same instant, gives the same answer.
            return self._outcome(screen, reason, by)

        loop_completed = reason in (ExitReason.DURATION, ExitReason.CYCLE_COMPLETE)
        # LOAD-BEARING: a change the frame loop did not end on (a dwell
        # inside it, a later frame returning False) must not fall into the
        # make-up dwell below. It can run for the rest of the screen's
        # duration, and a freshly requested on-demand mode would sit
        # invisible for that long -- or be clobbered by a queued stop.
        by = self.host.check(screen, AFTER_COMPLETED_LOOP if loop_completed else AFTER_LOOP)
        if by is not None:
            return self._outcome(screen, ExitReason.PREEMPTED, by)

        # Honour the minimum duration when a static, non-dynamic screen's
        # loop ended early. A screen cut short for a plugin reload is over:
        # the dwell returns at once and the rotation advances.
        if (not completed.dynamic and not loop_completed
                and completed.frame_policy is not FramePolicy.HIGH_FPS):
            elapsed = self.clock.time() - screen.start
            remaining = max(0.0, self._max(screen) - elapsed)
            if remaining > 0:
                self.host.dwell(remaining)
                time_left = self.clock.time() - screen.start < self._max(screen)
                by = self.host.check(screen, after_dwell(time_left))
                if by is not None:
                    return self._outcome(screen, ExitReason.PREEMPTED, by)

        if completed.dynamic:
            self._log_dynamic_end(screen)

        # The dwells above return early when a pending change (on-demand
        # started or stopped, the panel scheduled off) has already decided
        # what comes next; rotating now would skip it.
        by = self.host.check(screen, FINAL)
        if by is not None:
            return self._outcome(screen, ExitReason.PREEMPTED, by)
        return self._outcome(screen, reason, None)

    def _outcome(self, screen: Screen, reason: ExitReason,
                 by: Optional[ScreenPlan]) -> Outcome:
        return Outcome(reason, self.clock.time() - screen.start, preempted_by=by)

    @staticmethod
    def _max(screen: Screen) -> float:
        return float(screen.plan.max_duration or 0.0)

    @staticmethod
    def _min(screen: Screen) -> float:
        return float(screen.plan.min_duration or 0.0)

    @staticmethod
    def _ended_by(by: ScreenPlan) -> ExitReason:
        return ExitReason.RELOAD if by.source is Source.RELOAD else ExitReason.PREEMPTED

    # -- the frame loops ---------------------------------------------------

    def _high_fps_loop(self, screen: Screen) -> Tuple[ExitReason, Optional[ScreenPlan]]:
        """Ultra-smooth frames for scrolling plugins (8 ms = 125 FPS)."""
        clock, host, log = self.clock, self.host, self.log
        interval = HIGH_FPS_INTERVAL
        log.debug("Entering high-FPS loop for %s with display_interval=%.3fs (%.1f FPS)",
                  screen.mode, interval, 1.0 / interval)
        target = self._max(screen)
        while True:
            frame_start = clock.perf_counter()
            try:
                result = host.draw(screen)
                if isinstance(result, bool) and not result:
                    log.debug("Display returned False, breaking early")
                    return ExitReason.DISPLAY_FALSE, None
            except Exception:  # pylint: disable=broad-except
                log.exception("Error during display update")

            # Multi-display sync: send follower frame after each render
            host.after_frame(screen)
            host.tick()
            # Throttled: one clock compare between passes. A live-priority
            # scan, when one is due, happens here, before the sleep, as it
            # always has; the Arbiter weighs it after the sleep.
            live_scan = host.service(screen)

            # Pace to the frame deadline rather than sleeping a flat
            # interval on top of the work. display() has already blocked on
            # the panel's vsync by this point, so an unconditional sleep is
            # added to a wait that already happened. Measured on a 2x128x64
            # chain at limit_refresh_rate_hz=100: ~4ms of render plus a flat
            # 8ms put each iteration at ~12ms against a 10ms refresh grid, so
            # every swap missed a refresh and the loop settled at 50fps where
            # display_interval asks for 125 -- and with zero headroom, ~14% of
            # frames slipped a further refresh, which is what reads as scroll
            # stutter.
            remaining = interval - (clock.perf_counter() - frame_start)
            # Yield even when the frame overran its budget, so plugin update
            # threads and the web UI are not starved of the GIL.
            clock.sleep(remaining if remaining > 0 else 0.001)

            by = host.check(screen, FRAME, live_scan)
            if by is not None:
                log.debug("Mode changed during high-FPS loop, breaking early")
                return self._ended_by(by), by

            elapsed = clock.time() - screen.start
            if elapsed >= target:
                log.debug("Reached high-FPS target duration %.2fs for mode %s",
                          target, screen.mode)
                return ExitReason.DURATION, None
            if self._should_exit_dynamic(screen, elapsed):
                log.debug("Dynamic duration cycle complete for %s after %.2fs",
                          screen.mode, elapsed)
                return ExitReason.CYCLE_COMPLETE, None

    def _static_loop(self, screen: Screen) -> Tuple[ExitReason, Optional[ScreenPlan]]:
        """One frame a second for everything else."""
        clock, host, log = self.clock, self.host, self.log
        interval = STATIC_INTERVAL
        log.debug("Entering normal FPS loop for %s with display_interval=%.3fs",
                  screen.mode, interval)
        target = self._max(screen)
        dynamic = screen.plan.dynamic
        while True:
            # Wakes for a control socket command and applies it at once,
            # instead of up to a second later.
            by = host.wait_frame(interval, screen)
            if by is not None:
                log.info("Mode changed during display loop from %s to %s (%s), "
                         "breaking early", screen.mode, by.mode, by.source.value)
                return self._ended_by(by), by
            host.tick()

            elapsed = clock.time() - screen.start
            if elapsed >= target:
                log.debug("Reached standard target duration %.2fs for mode %s",
                          target, screen.mode)
                return ExitReason.DURATION, None

            try:
                result = host.draw(screen)
                if isinstance(result, bool) and not result:
                    # A dynamic-duration screen doesn't end on False: it
                    # keeps looping until its cycle completes or its maximum.
                    if not dynamic:
                        log.info("Display returned False for %s (no dynamic duration), "
                                 "breaking early", screen.mode)
                        return ExitReason.DISPLAY_FALSE, None
                    log.debug("Display returned False for %s (dynamic duration enabled), "
                              "continuing loop", screen.mode)
            except Exception:  # pylint: disable=broad-except
                log.exception("Error during display update")

            # Multi-display sync: send follower frame after each render
            host.after_frame(screen)

            live_scan = host.service(screen)
            by = host.check(screen, FRAME, live_scan)
            if by is not None:
                log.info("Mode changed during display loop from %s to %s (%s), "
                         "breaking early", screen.mode, by.mode, by.source.value)
                return self._ended_by(by), by

            if self._should_exit_dynamic(screen, elapsed):
                log.info("Dynamic duration cycle complete for %s after %.2fs",
                         screen.mode, elapsed)
                return ExitReason.CYCLE_COMPLETE, None

    # -- dynamic duration --------------------------------------------------

    def _should_exit_dynamic(self, screen: Screen, elapsed: float) -> bool:
        if not screen.plan.dynamic:
            return False
        minimum = self._min(screen)
        # A small grace period after min_duration prevents premature exits
        # due to timing issues.
        if elapsed < minimum + DYNAMIC_GRACE:
            self.log.debug(
                "_should_exit_dynamic: elapsed %.2fs < min_duration %.2fs + grace %.2fs, "
                "returning False", elapsed, minimum, DYNAMIC_GRACE)
            return False
        cycle_complete = self.host.cycle_complete(screen)
        self.log.debug(
            "_should_exit_dynamic: elapsed %.2fs >= min %.2fs, cycle_complete=%s, returning %s",
            elapsed, minimum + DYNAMIC_GRACE, cycle_complete, cycle_complete)
        if cycle_complete:
            self.log.debug("Cycle complete detected for %s after %.2fs (min: %.2fs, grace: %.2fs)",
                           screen.mode, elapsed, minimum, DYNAMIC_GRACE)
        return cycle_complete

    def _log_dynamic_end(self, screen: Screen) -> None:
        """How a dynamic-duration screen ended, for the log. Asks the plugin
        once more whether its cycle is complete, as the loop always did."""
        elapsed_total = self.clock.time() - screen.start
        cycle_done = self.host.cycle_complete(screen)
        minimum, maximum = self._min(screen), self._max(screen)
        if cycle_done:
            self.log.info(
                "Dynamic duration cycle completed for %s after %.2fs "
                "(target: %.2fs, min: %.2fs, max: %.2fs)",
                screen.mode, elapsed_total, maximum, minimum, maximum)
        elif elapsed_total >= maximum:
            self.log.info(
                "Dynamic duration cap reached before cycle completion for %s "
                "(%.2fs/%ds, min: %.2fs)",
                screen.mode, elapsed_total, int(maximum), minimum)
        else:
            self.log.debug(
                "Dynamic duration cycle in progress for %s: %.2fs elapsed "
                "(target: %.2fs, min: %.2fs, max: %.2fs)",
                screen.mode, elapsed_total, maximum, minimum, maximum)
