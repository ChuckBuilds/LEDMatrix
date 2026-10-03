"""The render loop's scheduled-update pass, throttled per frame.

The frame loops called _tick_plugin_updates() after every frame, about 125
times a second on a scroller, and each call ran
PluginManager.run_scheduled_updates(): a copy of the plugin dict and several
locks per plugin, to find that nothing was due (no interval is shorter than
5 s). The frame loops and the dwell sleep now call
_tick_plugin_updates_if_due(), which runs it at most once per
PLUGIN_UPDATE_TICK_INTERVAL. The top of each loop pass still calls
_tick_plugin_updates() unthrottled, because a plugin loaded, reloaded or
enabled for on-demand there is due at once.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("EMULATOR", "true")

from src import display_controller as dc_mod  # noqa: E402
from src.display_controller import DisplayController  # noqa: E402
from test._run_loop_harness import FakePlugin, RunLoopHarness  # noqa: E402


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    fake = SimpleNamespace(monotonic=lambda: now["t"], time=lambda: now["t"])
    monkeypatch.setattr(dc_mod, "time", fake)
    return now


@pytest.fixture
def dc():
    controller = object.__new__(DisplayController)
    controller.plugin_manager = MagicMock()
    return controller


def _calls(dc):
    return dc.plugin_manager.run_scheduled_updates.call_count


class TestThrottle:
    def test_the_first_call_runs(self, dc, clock):
        dc._tick_plugin_updates_if_due()
        assert _calls(dc) == 1

    def test_calls_inside_the_window_are_skipped(self, dc, clock):
        dc._tick_plugin_updates_if_due()
        for _ in range(30):  # a scroller's frames, 8 ms apart
            clock["t"] += 0.008
            dc._tick_plugin_updates_if_due()
        assert clock["t"] - 1000.0 < dc.PLUGIN_UPDATE_TICK_INTERVAL
        assert _calls(dc) == 1

    def test_it_runs_again_once_the_window_has_passed(self, dc, clock):
        dc._tick_plugin_updates_if_due()
        clock["t"] += dc.PLUGIN_UPDATE_TICK_INTERVAL
        dc._tick_plugin_updates_if_due()
        assert _calls(dc) == 2

    def test_the_unthrottled_tick_always_runs_and_restarts_the_window(self, dc, clock):
        # The top of the loop pass: a plugin just (re)loaded is due now.
        dc._tick_plugin_updates_if_due()
        clock["t"] += 0.01
        dc._tick_plugin_updates()
        assert _calls(dc) == 2
        # ...and the frame right after it does not repeat the pass.
        clock["t"] += 0.01
        dc._tick_plugin_updates_if_due()
        assert _calls(dc) == 2

    def test_no_plugin_manager(self, clock):
        controller = object.__new__(DisplayController)
        controller.plugin_manager = None
        controller._tick_plugin_updates_if_due()  # must not raise
        controller._tick_plugin_updates()

    def test_a_failing_pass_is_contained_and_still_throttled(self, dc, clock):
        dc.plugin_manager.run_scheduled_updates.side_effect = RuntimeError("boom")
        dc._tick_plugin_updates_if_due()
        dc._tick_plugin_updates_if_due()
        assert _calls(dc) == 1

    def test_the_vegas_tick_is_not_throttled(self, dc, clock):
        # Vegas's own update thread calls the plugin manager directly.
        dc.vegas_coordinator = None
        dc.plugin_manager.run_scheduled_updates_with_changes.return_value = []
        dc._tick_plugin_updates()
        dc._tick_plugin_updates_for_vegas()
        dc._tick_plugin_updates_for_vegas()
        assert dc.plugin_manager.run_scheduled_updates_with_changes.call_count == 2


class TestInTheRunLoop:
    """The real run() on the golden-trace harness's fake clock."""

    @pytest.fixture
    def run(self, tmp_path):
        h = RunLoopHarness(tmp_path, horizon=40)
        h.add_plugin(FakePlugin("ticker", ["ticker"], duration=10, enable_scrolling=True))
        h.add_plugin(FakePlugin("clock", ["clock"], duration=10))
        ticks = []
        h.pm.run_scheduled_updates = lambda: ticks.append(round(h.clock.rel(), 3))
        h.run()
        passes = sorted({t for t, kind, _s, _d in h.events if kind == "pass"})
        frames = [t for t, kind, s, _d in h.events if kind in ("first", "frame") and s == "ticker"]
        return SimpleNamespace(ticks=ticks, passes=passes, frames=frames,
                               interval=h.controller.PLUGIN_UPDATE_TICK_INTERVAL)

    def test_a_scroller_ticks_a_few_times_a_second_not_every_frame(self, run):
        ticker_frames = len(run.frames)
        assert ticker_frames > 500  # 10 s screens at 125 Hz, twice
        # 40 s at four a second, plus one per pass.
        assert len(run.ticks) <= 40 / run.interval + len(run.passes) + 1

    def test_ticks_are_never_closer_than_the_floor_except_at_a_pass(self, run):
        passes = set(run.passes)
        for prev, cur in zip(run.ticks, run.ticks[1:]):
            if cur - prev < run.interval - 1e-6:
                assert cur in passes, (prev, cur)

    def test_every_pass_ticks_at_once(self, run):
        # Unthrottled: the frame loop ticked just before the screen ended, and
        # the pass ticks again anyway, so a plugin it just loaded is not kept
        # waiting.
        ticks = set(run.ticks)
        for t in run.passes:
            assert t in ticks, t
        assert any(cur - prev < run.interval
                   for prev, cur in zip(run.ticks, run.ticks[1:]))
