"""ScreenRunner (src/screen_runner.py) on a scripted host and a fake clock.

The golden traces (test_run_loop_golden.py) run it inside the real
DisplayController; these pin down its own contract: which ExitReason each
way of ending gives, the order it calls its host in, how the 125 Hz loop
paces, and which service point asks what.
"""

from typing import Any, List, Optional, Tuple

import pytest

from src.display_arbiter import (
    RELOAD_PLAN, SCREEN_PREEMPTERS, FramePolicy, ScreenPlan, Source,
)
from src.screen_runner import (
    AFTER_COMPLETED_LOOP, AFTER_LOOP, DYNAMIC_GRACE, FINAL, FRAME, HIGH_FPS_INTERVAL,
    Checkpoint, ExitReason, FirstFrame, NoticeRead, Screen, ScreenRunner,
)

WIFI_PLAN = ScreenPlan(Source.WIFI)


class Clock:
    """time/perf_counter read ``now``; sleep advances it."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: List[float] = []

    def time(self) -> float:
        return self.now

    def perf_counter(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


class Host:
    """A ScreenHost whose answers are scripted; records every call."""

    def __init__(self, clock: Clock, *, shown=True, raised=False, minimum=10.0,
                 maximum=10.0, dynamic=False, policy=FramePolicy.STATIC,
                 completed=True, draws=None, checks=None, work=0.0,
                 cycle_complete_at=None, dwell_to=None):
        self.clock = clock
        self.calls: List[Tuple[Any, ...]] = []
        self.shown, self.raised = shown, raised
        self.minimum, self.maximum = minimum, maximum
        self.dynamic, self.policy, self.completed = dynamic, policy, completed
        #: display() results for the frames after the first, in order.
        self.draws = list(draws or [])
        #: (checkpoint name, frame number) -> plan, or a callable(screen, cp).
        self.checks = checks or {}
        self.work = work
        self.cycle_complete_at = cycle_complete_at
        self.dwell_to = dwell_to
        self.frames = 0

    def first_frame(self, plan, plugin):
        self.calls.append(("first", plan.mode))
        return FirstFrame(self.shown, self.raised, False)

    def complete_plan(self, plan, plugin):
        self.calls.append(("complete",))
        if not self.completed:
            return None
        return ScreenPlan(plan.source, mode=plan.mode, min_duration=self.minimum,
                          max_duration=self.maximum, dynamic=self.dynamic,
                          frame_policy=self.policy, preemptible_by=plan.preemptible_by)

    def draw(self, screen):
        self.frames += 1
        self.calls.append(("draw", self.frames))
        self.clock.now += self.work
        return self.draws.pop(0) if self.draws else True

    def after_frame(self, screen):
        self.calls.append(("after_frame",))

    def tick(self):
        self.calls.append(("tick",))

    def service(self, screen):
        self.calls.append(("service",))
        return ("scan", self.frames)

    def wait_frame(self, interval, screen):
        self.calls.append(("wait", interval))
        self.clock.sleep(interval)
        return self._scripted("wait", screen)

    def check(self, screen, checkpoint, live_scan=None):
        self.calls.append(("check", checkpoint.name, live_scan))
        return self._scripted(checkpoint.name, screen, checkpoint)

    def _scripted(self, name, screen, checkpoint=None):
        answer = self.checks.get((name, self.frames))
        if callable(answer):
            return answer(screen, checkpoint)
        return answer

    def dwell(self, seconds):
        self.calls.append(("dwell", round(seconds, 6)))
        self.clock.now = self.dwell_to if self.dwell_to is not None else self.clock.now + seconds

    def cycle_complete(self, screen):
        self.calls.append(("cycle?",))
        return self.cycle_complete_at is not None and self.clock.now >= self.cycle_complete_at


PLAN = ScreenPlan(Source.ROTATION, mode="clock", preemptible_by=SCREEN_PREEMPTERS)


def _run(**kwargs) -> Tuple[Any, Host, Clock]:
    clock = Clock()
    host = Host(clock, **kwargs)
    outcome = ScreenRunner(clock, host).run(PLAN, plugin=object())
    return outcome, host, clock


def _names(host, *kinds):
    return [c for c in host.calls if c[0] in kinds]


class TestFirstFrame:

    def test_no_plugin_is_empty_without_a_dispatch(self):
        clock = Clock()
        host = Host(clock)
        outcome = ScreenRunner(clock, host).run(PLAN, plugin=None)
        assert outcome.exit_reason is ExitReason.EMPTY
        assert host.calls == []

    @pytest.mark.parametrize("raised,reason", [(False, ExitReason.EMPTY),
                                               (True, ExitReason.ERROR)])
    def test_nothing_shown(self, raised, reason):
        outcome, host, _ = _run(shown=False, raised=raised)
        assert outcome.exit_reason is reason
        assert host.calls == [("first", "clock")]

    def test_an_on_demand_session_with_no_time_left(self):
        outcome, host, _ = _run(completed=False)
        assert outcome.exit_reason is ExitReason.PREEMPTED
        assert outcome.preempted_by is None
        assert host.calls == [("first", "clock"), ("complete",)]


class TestStaticLoop:

    def test_runs_its_duration_one_frame_a_second(self):
        outcome, host, clock = _run(maximum=5.0)
        assert outcome.exit_reason is ExitReason.DURATION
        assert outcome.elapsed == 5.0
        # Frames at 1..4 s; the wait that reaches 5 s ends it before a fifth.
        assert host.frames == 4
        assert clock.sleeps == [1.0] * 5
        # Each frame: wait, tick, draw, follower frame, service, the check.
        assert host.calls[2:9] == [("wait", 1.0), ("tick",), ("draw", 1), ("after_frame",),
                                   ("service",), ("check", "frame", ("scan", 1)), ("wait", 1.0)]
        # A completed loop: the after-loop look reads no notice, then FINAL.
        assert host.calls[-2:] == [("check", "after-loop", None), ("check", "final", None)]

    def test_preempted_between_frames(self):
        by = ScreenPlan(Source.ON_DEMAND, mode="x")
        outcome, host, _ = _run(maximum=30.0, checks={("frame", 3): by})
        assert outcome.exit_reason is ExitReason.PREEMPTED
        assert outcome.preempted_by is by
        assert outcome.elapsed == 3.0
        # Decided at the service point: no second look, no dwell.
        assert host.calls[-1] == ("check", "frame", ("scan", 3))

    def test_preempted_by_a_socket_command_in_the_frame_wait(self):
        outcome, host, _ = _run(maximum=30.0, checks={("wait", 2): WIFI_PLAN})
        assert outcome.exit_reason is ExitReason.PREEMPTED
        assert host.calls[-1] == ("wait", 1.0)

    def test_display_false_makes_up_the_minimum(self):
        outcome, host, _ = _run(maximum=12.0, draws=[True, False])
        assert outcome.exit_reason is ExitReason.DISPLAY_FALSE
        # The loop ended early: the after-loop look may read a notice, then
        # the dwell makes up the rest of the 12 s.
        assert _names(host, "check", "dwell")[-4:] == [
            ("check", "after-loop", None), ("dwell", 10.0),
            ("check", "after-dwell", None), ("check", "final", None)]
        assert outcome.elapsed == 12.0

    def test_a_notice_that_cuts_the_dwell_short_ends_the_screen(self):
        def notice(screen, checkpoint):
            assert checkpoint.notice is NoticeRead.ALWAYS
            return WIFI_PLAN if checkpoint.notice_counts else None
        outcome, _, _ = _run(maximum=12.0, draws=[False], dwell_to=6.0,
                             checks={("after-dwell", 1): notice})
        assert outcome.exit_reason is ExitReason.PREEMPTED

    def test_a_notice_after_a_full_dwell_does_not(self):
        def notice(screen, checkpoint):
            return WIFI_PLAN if checkpoint.notice_counts else None
        outcome, _, _ = _run(maximum=12.0, draws=[False],
                             checks={("after-dwell", 1): notice})
        assert outcome.exit_reason is ExitReason.DISPLAY_FALSE

    def test_a_reload_ends_the_loop_but_the_screen_counts(self):
        outcome, host, _ = _run(maximum=30.0, checks={("frame", 2): RELOAD_PLAN})
        assert outcome.exit_reason is ExitReason.RELOAD
        # Not decided at the service point: the after-loop look, the dwell
        # (which returns at once while a reload waits) and FINAL follow.
        assert ("check", "after-loop", None) in host.calls
        assert ("check", "final", None) in host.calls

    def test_a_change_after_the_loop_preempts(self):
        by = ScreenPlan(Source.ROTATION, mode="weather")
        outcome, _, _ = _run(maximum=3.0, checks={("final", 2): by})
        assert outcome.exit_reason is ExitReason.PREEMPTED
        assert outcome.preempted_by is by

    def test_a_dynamic_screen_keeps_going_on_false(self):
        outcome, host, _ = _run(minimum=3.0, maximum=8.0, dynamic=True,
                                draws=[False, False, False])
        assert outcome.exit_reason is ExitReason.DURATION
        assert host.frames == 7


class TestHighFpsLoop:

    def test_paces_to_the_deadline(self):
        outcome, host, clock = _run(maximum=0.05, policy=FramePolicy.HIGH_FPS, work=0.003)
        assert outcome.exit_reason is ExitReason.DURATION
        # 3 ms of drawing leaves 5 ms of an 8 ms frame to sleep.
        assert set(clock.sleeps) == {round(HIGH_FPS_INTERVAL - 0.003, 6)}

    def test_an_overrun_frame_still_yields(self):
        _, _, clock = _run(maximum=0.05, policy=FramePolicy.HIGH_FPS, work=0.02)
        assert set(clock.sleeps) == {0.001}

    def test_service_before_the_sleep_check_after(self):
        _, host, clock = _run(maximum=0.016, policy=FramePolicy.HIGH_FPS)
        first = host.calls[2:7]
        assert first == [("draw", 1), ("after_frame",), ("tick",), ("service",),
                         ("check", "frame", ("scan", 1))]
        assert clock.sleeps[0] == HIGH_FPS_INTERVAL

    def test_preempted_after_the_frames_sleep(self):
        outcome, _, clock = _run(maximum=30.0, policy=FramePolicy.HIGH_FPS,
                                 checks={("frame", 3): WIFI_PLAN})
        assert outcome.exit_reason is ExitReason.PREEMPTED
        assert clock.now == pytest.approx(3 * HIGH_FPS_INTERVAL)

    def test_display_false_has_no_make_up_dwell(self):
        outcome, host, _ = _run(maximum=30.0, policy=FramePolicy.HIGH_FPS,
                                draws=[True, False])
        assert outcome.exit_reason is ExitReason.DISPLAY_FALSE
        assert not _names(host, "dwell")


class TestDynamicDuration:

    def test_cycle_complete_after_the_minimum_and_grace(self):
        outcome, host, _ = _run(minimum=3.0, maximum=20.0, dynamic=True,
                                cycle_complete_at=1.0)
        assert outcome.exit_reason is ExitReason.CYCLE_COMPLETE
        # Not asked before minimum + grace (3.5 s): the 1 Hz loop's first
        # frame at or past it is the one at 4 s.
        assert outcome.elapsed == 4.0
        assert DYNAMIC_GRACE == 0.5

    def test_capped_at_the_maximum(self):
        outcome, host, _ = _run(minimum=3.0, maximum=6.0, dynamic=True)
        assert outcome.exit_reason is ExitReason.DURATION
        # The end-of-screen log asks the plugin once more, before FINAL.
        assert host.calls[-3:] == [("check", "after-loop", None), ("cycle?",),
                                   ("check", "final", None)]

    def test_high_fps_cycle_complete(self):
        outcome, _, _ = _run(minimum=0.02, maximum=1.0, dynamic=True,
                             policy=FramePolicy.HIGH_FPS, cycle_complete_at=0.0)
        assert outcome.exit_reason is ExitReason.CYCLE_COMPLETE
        assert outcome.elapsed >= 0.02 + DYNAMIC_GRACE


class TestCheckpoints:

    @pytest.mark.parametrize("checkpoint,notice,reload", [
        (FRAME, NoticeRead.IF_UNDECIDED, True),
        (AFTER_LOOP, NoticeRead.IF_UNDECIDED, False),
        (AFTER_COMPLETED_LOOP, NoticeRead.NEVER, False),
        (FINAL, NoticeRead.NEVER, False),
    ])
    def test_what_each_service_point_considers(self, checkpoint, notice, reload):
        """A reload counts only between frames; the notice file is read
        where the loop read it before stage 3."""
        assert isinstance(checkpoint, Checkpoint)
        assert (checkpoint.notice, checkpoint.reload) == (notice, reload)

    def test_the_screen_carries_the_completed_plan(self):
        seen: List[Optional[Screen]] = []

        def grab(screen, checkpoint):
            seen.append(screen)
        _run(maximum=2.0, checks={("frame", 1): grab})
        assert seen[0].plan.frame_policy is FramePolicy.STATIC
        assert seen[0].mode == "clock"


class TestControllerServicePoint:
    """DisplayController._screen_check: the reads it makes and what it claims,
    on a controller built by the run-loop harness."""

    @pytest.fixture
    def dc(self, tmp_path):
        import os
        os.environ.setdefault("EMULATOR", "true")
        from test._run_loop_harness import FakePlugin, RunLoopHarness
        h = RunLoopHarness(tmp_path, horizon=10)
        h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
        h.add_plugin(FakePlugin("sports", ["sports_live"], duration=20,
                                live=(0, 100), live_priority=True))
        dc = h.controller
        dc.current_display_mode = "clock"
        dc.current_mode_index = 0
        dc.reads = []

        def read():
            dc.reads.append(1)
            return {"message": "AP mode", "expires_at": 1e12}
        dc._check_wifi_status_message = read
        return dc

    @staticmethod
    def _screen(mode="clock"):
        from src.display_arbiter import ArbiterState, rotation_plan
        return Screen(rotation_plan(ArbiterState(current_mode=mode)), plugin=None,
                      accepts_display_mode=False, start=0.0)

    def test_a_pending_notice_is_read_and_ends_the_screen(self, dc):
        by = dc._screen_check(self._screen(), FRAME)
        assert by.source is Source.WIFI and len(dc.reads) == 1

    def test_not_read_once_the_mode_has_moved(self, dc):
        dc.current_display_mode = "sports_live"
        by = dc._screen_check(self._screen(), FRAME)
        assert by.source is Source.ROTATION and dc.reads == []

    def test_not_read_while_scheduled_off(self, dc):
        dc.is_display_active = False
        by = dc._screen_check(self._screen(), FRAME)
        assert by.source is Source.SCHEDULED_OFF and dc.reads == []

    def test_not_read_during_on_demand(self, dc):
        dc.on_demand_active = True
        dc.on_demand_schedule_override = True
        assert dc._screen_check(self._screen(), FRAME) is None
        assert dc.reads == []

    def test_a_live_takeover_is_claimed_before_the_notice(self, dc):
        by = dc._screen_check(self._screen(), FRAME, live_scan=("sports_live",))
        assert by.source is Source.LIVE and dc.reads == []
        assert dc.current_display_mode == "sports_live"
        assert dc._live_takeover_unshown is True and dc._live_resume_index == 0

    def test_after_a_completed_loop_the_notice_is_not_read(self, dc):
        assert dc._screen_check(self._screen(), AFTER_COMPLETED_LOOP) is None
        assert dc.reads == []

    def test_after_a_full_dwell_it_is_read_but_does_not_count(self, dc):
        from src.screen_runner import after_dwell
        assert dc._screen_check(self._screen(), after_dwell(False)) is None
        assert len(dc.reads) == 1

    def test_a_reload_counts_only_between_frames(self, dc):
        dc._check_wifi_status_message = lambda: None
        dc._pending_plugin_reloads = ("pending",)
        assert dc._screen_check(self._screen(), FRAME) is RELOAD_PLAN
        assert dc._screen_check(self._screen(), AFTER_LOOP) is None
