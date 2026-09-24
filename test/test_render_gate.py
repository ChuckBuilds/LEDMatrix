"""The render gate (src/common/render_gate.py) and its wiring into Vegas."""

import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("EMULATOR", "true")

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common import render_gate  # noqa: E402
from src.common.render_gate import RenderGate  # noqa: E402

PERIOD = 0.010


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _running(gate, clock, swaps=render_gate.MIN_SAMPLES + 1, hold=1):
    """Drive ``swaps`` on-time swaps through the gate, a refresh apart."""
    for _ in range(swaps):
        gate.before_swap(hold)
        clock.now += hold * PERIOD
        gate.after_swap(hold)


class TestWindow:
    def test_no_window_until_the_period_is_known(self):
        clock = Clock()
        gate = RenderGate(clock)
        _running(gate, clock, swaps=3)
        assert gate.refresh_period() is None
        gate.before_swap(1)
        assert gate._open_until == 0.0
        # ...and nothing parks meanwhile.
        assert not gate._should_park(sys._getframe(), clock.now + 0.009)

    def test_the_period_is_the_low_end_of_the_swap_gaps(self):
        clock = Clock()
        gate = RenderGate(clock)
        _running(gate, clock, swaps=20)
        gate.before_swap(1)
        clock.now += 0.030            # one late frame lengthens its gap only
        gate.after_swap(1)
        assert gate.refresh_period() == pytest.approx(PERIOD)

    def test_opens_until_just_before_the_refresh_the_swap_returns_on(self):
        clock = Clock()
        gate = RenderGate(clock)
        _running(gate, clock)
        last = clock.now
        clock.now += 0.003            # the render thread's own work
        gate.before_swap(1)
        assert gate._open_until == pytest.approx(
            last + PERIOD - render_gate.MARGIN_SECONDS)

    def test_a_held_frame_opens_for_the_whole_hold(self):
        clock = Clock()
        gate = RenderGate(clock)
        _running(gate, clock, hold=2)
        last = clock.now
        clock.now += 0.003
        gate.before_swap(2)
        assert gate._open_until == pytest.approx(
            last + 2 * PERIOD - render_gate.MARGIN_SECONDS)

    def test_a_late_frame_opens_until_the_next_boundary(self):
        clock = Clock()
        gate = RenderGate(clock)
        _running(gate, clock)
        last = clock.now
        clock.now += 0.013            # missed its refresh: returns on the next
        gate.before_swap(1)
        assert gate._open_until == pytest.approx(
            last + 2 * PERIOD - render_gate.MARGIN_SECONDS)

    def test_closed_the_moment_the_swap_returns(self):
        clock = Clock()
        gate = RenderGate(clock)
        _running(gate, clock)
        gate.before_swap(1)
        clock.now += PERIOD
        gate.after_swap(1)
        assert gate._open_until == 0.0


class TestWhenToPark:
    @pytest.fixture
    def gate(self):
        clock = Clock()
        gate = RenderGate(clock)
        _running(gate, clock)
        gate.before_swap(1)
        return gate

    def test_runs_inside_the_window(self, gate):
        assert not gate._should_park(sys._getframe(), gate._open_until - 0.001)

    def test_parks_once_it_has_closed(self, gate):
        assert gate._should_park(sys._getframe(), gate._open_until + 0.0001)

    def test_never_with_no_render_loop_to_protect(self, gate):
        stale = gate._last_return + render_gate.STALE_SECONDS + 0.001
        assert not gate._should_park(sys._getframe(), stale)

    def test_never_holding_a_guarded_lock(self, gate):
        rlock, lock = threading.RLock(), threading.Lock()
        gate.guard(rlock, lock, None)
        closed = gate._open_until + 0.0001
        with rlock:
            assert not gate._should_park(sys._getframe(), closed)
        with lock:
            assert not gate._should_park(sys._getframe(), closed)
        assert gate._should_park(sys._getframe(), closed)

    def test_an_rlock_held_by_another_thread_is_not_this_ones(self, gate):
        rlock = threading.RLock()
        gate.guard(rlock)
        taken, done = threading.Event(), threading.Event()

        def hold():
            with rlock:
                taken.set()
                done.wait(5)
        other = threading.Thread(target=hold)
        other.start()
        try:
            taken.wait(5)
            assert gate._should_park(sys._getframe(), gate._open_until + 0.0001)
        finally:
            done.set()
            other.join()

    @pytest.mark.parametrize("filename", [
        "/usr/lib/python3.11/logging/__init__.py",
        "/usr/lib/python3.11/threading.py",
        "<frozen importlib._bootstrap>",
        "/home/pi/LEDMatrix/src/cache/disk_cache.py",
        "/home/pi/LEDMatrix/src/cache_manager.py",
    ])
    def test_never_inside_code_that_takes_shared_locks(self, gate, filename):
        namespace = {}
        exec(compile("import sys\ndef here():\n    return sys._getframe()\n",
                     filename, "exec"), namespace)
        frame = namespace["here"]()
        assert render_gate._unsafe(frame, None)
        assert not gate._should_park(frame, gate._open_until + 0.0001)

    def test_what_lies_below_the_yielding_block_does_not_count(self):
        # A thread's stack always starts in threading.py; only frames above
        # the one that entered yielding() matter.
        namespace = {}
        exec(compile("def bootstrap(fn):\n    return fn()\n",
                     "/usr/lib/python3.11/threading.py", "exec"), namespace)

        def entered():
            base = sys._getframe()

            def work():
                top = sys._getframe()
                return render_gate._unsafe(top, None), render_gate._unsafe(top, base)
            return work()
        assert namespace["bootstrap"](entered) == (True, False)


class TestYielding:
    """A real background thread, parked and released by the gate."""

    def _worker(self, gate, stop):
        count = [0]

        def step():
            count[0] += 1

        def work():
            with gate.yielding():
                while not stop.is_set():
                    step()
        thread = threading.Thread(target=work, daemon=True)
        return thread, count

    def test_parks_while_closed_and_runs_while_open(self):
        clock = Clock()
        gate = RenderGate(clock)
        _running(gate, clock)
        gate.before_swap(1)
        stop = threading.Event()
        thread, count = self._worker(gate, stop)
        try:
            clock.now = gate._open_until + 0.001      # window closed
            thread.start()
            time.sleep(0.2)
            parked_steps = count[0]
            # At most one step per MAX_WAIT timeout while parked.
            assert parked_steps < 0.2 / render_gate.MAX_WAIT_SECONDS + 5
            assert gate.parks >= 1

            with gate._cond:                          # the next swap opens it
                gate._open_until = clock.now + 3600.0
                gate._generation += 1
                gate._cond.notify_all()
            time.sleep(0.1)
            assert count[0] > parked_steps + 1000
        finally:
            stop.set()
            with gate._cond:
                gate._open_until = float("inf")
                gate._generation += 1
                gate._cond.notify_all()
            thread.join(2)
        assert not thread.is_alive()

    def test_runs_freely_once_the_render_loop_stops(self):
        clock = Clock()
        gate = RenderGate(clock)
        _running(gate, clock)
        stop = threading.Event()
        thread, count = self._worker(gate, stop)
        clock.now += render_gate.STALE_SECONDS + 0.01
        thread.start()
        time.sleep(0.1)
        stop.set()
        thread.join(2)
        assert count[0] > 1000
        assert gate.parks == 0

    def test_the_hook_comes_off_when_the_block_ends(self):
        gate = RenderGate()
        before = sys.getprofile()
        with gate.yielding():
            assert sys.getprofile() == gate._hook
        assert sys.getprofile() is before


class TestDisplayManager:
    @pytest.fixture
    def dm(self):
        from src.display_manager import DisplayManager
        DisplayManager._instance = None
        DisplayManager._initialized = False
        manager = DisplayManager({"display": {
            "hardware": {"rows": 32, "cols": 64, "chain_length": 1, "parallel": 1},
            "runtime": {"gpio_slowdown": 0}}}, suppress_test_pattern=True)
        yield manager
        manager.render_gate = None
        manager.set_scrolling_state(False)
        DisplayManager._instance = None
        DisplayManager._initialized = False

    def test_opens_around_each_swap(self, dm):
        calls = []

        class Spy:
            def before_swap(self, hold):
                calls.append(("before", hold))

            def after_swap(self, hold):
                calls.append(("after", hold))

        real_swap = dm.matrix.SwapOnVSync

        def swap(canvas, *args, **kwargs):
            calls.append(("swap",))
            return real_swap(canvas, *args, **kwargs)
        dm.matrix.SwapOnVSync = swap
        dm.render_gate = Spy()
        dm.set_scrolling_state(True, 2)
        dm.draw.rectangle([0, 0, 3, 3], fill=(255, 0, 0))
        dm.update_display()
        assert calls == [("before", 2), ("swap",), ("after", 2)]

    def test_off_screen_drawing_never_touches_it(self, dm):
        class Boom:
            def before_swap(self, hold):
                raise AssertionError("an off-screen frame reached the gate")
            after_swap = before_swap

        dm.render_gate = Boom()
        with dm.offscreen():
            dm.draw.rectangle([0, 0, 3, 3], fill=(255, 0, 0))
            dm.update_display()


class TestVegasWiring:
    def _coordinator(self, **config):
        from src.vegas_mode.config import VegasModeConfig
        from src.vegas_mode.coordinator import VegasModeCoordinator

        class Holder:
            def __init__(self):
                self._buffer_lock = threading.RLock()
                self._prefetch_lock = threading.Lock()
                self._cache_lock = threading.Lock()

        c = VegasModeCoordinator.__new__(VegasModeCoordinator)
        c.vegas_config = VegasModeConfig(**config)
        c._state_lock = threading.Lock()
        c.stream_manager = Holder()
        c.render_pipeline = Holder()
        c.plugin_adapter = Holder()
        c.display_manager = type("DM", (), {"render_gate": None})()
        return c

    def test_off_by_default(self, monkeypatch):
        monkeypatch.setattr(render_gate, "swap_releases_gil", lambda: True)
        c = self._coordinator()
        c._install_render_gate()
        assert c.display_manager.render_gate is None

    def test_installed_for_the_run_and_removed_after(self, monkeypatch):
        monkeypatch.setattr(render_gate, "swap_releases_gil", lambda: True)
        c = self._coordinator(prefetch_gate=True)
        c._install_render_gate()
        gate = c.display_manager.render_gate
        assert isinstance(gate, RenderGate)
        assert c._state_lock in gate._guarded
        assert c.stream_manager._buffer_lock in gate._guarded
        assert c.plugin_adapter._cache_lock in gate._guarded
        c._remove_render_gate()
        assert c.display_manager.render_gate is None

    @pytest.mark.parametrize("releases", [False, None])
    def test_ignored_without_a_binding_that_releases_the_gil(self, monkeypatch, releases):
        monkeypatch.setattr(render_gate, "swap_releases_gil", lambda: releases)
        c = self._coordinator(prefetch_gate=True)
        c._install_render_gate()
        assert c.display_manager.render_gate is None

    def test_read_from_config(self):
        from src.vegas_mode.config import VegasModeConfig
        on = VegasModeConfig.from_config(
            {"display": {"vegas_scroll": {"prefetch_gate": True}}})
        assert on.prefetch_gate is True
        assert on.to_dict()["prefetch_gate"] is True
        assert VegasModeConfig.from_config(
            {"display": {"vegas_scroll": {}}}).prefetch_gate is False

    def test_the_prefetch_runs_inside_the_gate(self):
        from src.vegas_mode.render_pipeline import RenderPipeline

        gate = RenderGate()
        seen = []

        class Stream:
            def take_next_group(self, offscreen_only=False):
                seen.append((sys.getprofile() == gate._hook, offscreen_only))
                return ["segment"]

        pipeline = RenderPipeline.__new__(RenderPipeline)
        pipeline.config = type("C", (), {"continuous_scroll": True})()
        pipeline._prefetch_lock = threading.Lock()
        pipeline._prefetch_thread = None
        pipeline._prepared_group = None
        pipeline.stream_manager = Stream()
        pipeline.display_manager = type("DM", (), {"render_gate": gate})()
        pipeline.start_prefetch()
        pipeline._prefetch_thread.join(5)
        assert seen == [(True, True)]
        assert pipeline._prepared_group == ["segment"]
