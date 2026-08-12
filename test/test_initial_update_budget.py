"""Tests that startup does not wait indefinitely for plugins to fetch data.

DisplayController.__init__ calls _update_modules() once, to populate plugin
data before the first frame. It walks every loaded plugin in turn, and each
update blocks the calling thread for up to the executor's 30s timeout, so the
uncapped total is the sum of every slow plugin on the system.

Profiled on a live rig with py-spy, the main thread sat 9.34s in

    display_controller._update_modules
      -> plugin_executor.execute_update
        -> execute_with_timeout -> threading.join

and the controller's own log put the full pass at 82 seconds on the worst
boot measured (55 and 26 on the two before). The panel shows nothing for all
of it.

Nothing is lost by stopping early: a plugin that has never updated is
immediately due, so run_scheduled_updates() collects it seconds later with the
display already running.
"""

import os
import time
from unittest.mock import Mock

import pytest

# display_controller imports display_manager, which binds the hardware
# rgbmatrix module unless EMULATOR=true is set before import (same convention
# as test_display_controller_vegas_tick.py).
os.environ.setdefault("EMULATOR", "true")

from src.display_controller import (  # noqa: E402
    DisplayController, _INITIAL_UPDATE_BUDGET_SECONDS,
    _MIN_INITIAL_UPDATE_TIMEOUT_SECONDS)


class FakeExecutor:
    """Records which plugins were updated, and can make some of them slow."""

    def __init__(self, cost=0.0, slow=()):
        self.updated = []
        self.cost = cost
        self.slow = set(slow)

    def execute_update(self, plugin, plugin_id, timeout=None):
        self.updated.append(plugin_id)
        if plugin_id in self.slow:
            time.sleep(self.cost)
        return True


@pytest.fixture
def tiny_floor(monkeypatch):
    """Shrink the "worth starting" floor so timing tests stay quick."""
    import src.display_controller as mod
    monkeypatch.setattr(mod, "_MIN_INITIAL_UPDATE_TIMEOUT_SECONDS", 0.01)


def _controller(plugin_ids, executor):
    c = DisplayController.__new__(DisplayController)
    c.plugin_manager = Mock()
    # Both attributes, because _update_modules reads
    # `loaded_plugins or plugins` and an empty dict is falsy.
    c.plugin_manager.loaded_plugins = {pid: Mock() for pid in plugin_ids}
    c.plugin_manager.plugins = dict(c.plugin_manager.loaded_plugins)
    c.plugin_manager.plugin_executor = executor
    c.plugin_manager.plugin_last_update = {}
    c.plugin_manager.health_tracker = None
    return c


class TestTheBudgetIsRespected:
    def test_without_a_deadline_every_plugin_is_updated(self):
        ex = FakeExecutor()
        _controller(['a', 'b', 'c'], ex)._update_modules()
        assert ex.updated == ['a', 'b', 'c']

    def test_a_passed_deadline_stops_the_pass(self):
        ex = FakeExecutor()
        _controller(['a', 'b', 'c'], ex)._update_modules(deadline=time.time() - 1)
        assert ex.updated == [], "updated %r after the deadline" % ex.updated

    def test_slow_plugins_do_not_drag_in_the_rest(self, tiny_floor):
        # One plugin burns the whole budget; the remainder must be left alone
        # rather than each adding its own wait.
        ex = FakeExecutor(cost=0.3, slow={'slow'})
        c = _controller(['slow'] + ['p%d' % i for i in range(20)], ex)
        started = time.time()
        c._update_modules(deadline=started + 0.2)
        elapsed = time.time() - started

        assert ex.updated == ['slow'], "updated %r" % ex.updated
        # Bounded by the one in-flight update, not by twenty more.
        assert elapsed < 1.0, "%.2fs" % elapsed

    def test_a_generous_deadline_still_gets_everything(self):
        ex = FakeExecutor()
        c = _controller(['a', 'b', 'c'], ex)
        c._update_modules(deadline=time.time() + 30)
        assert ex.updated == ['a', 'b', 'c']

    def test_the_deadline_is_checked_before_each_plugin(self, tiny_floor):
        # Not just once up front: the budget can be spent partway through.
        ex = FakeExecutor(cost=0.15, slow={'a', 'b', 'c', 'd'})
        c = _controller(['a', 'b', 'c', 'd'], ex)
        c._update_modules(deadline=time.time() + 0.2)
        assert 0 < len(ex.updated) < 4, "updated %r" % ex.updated


class TestThePassIsBoundedInPractice:
    def test_the_last_plugin_cannot_overrun_the_budget(self):
        # Checking the deadline before each plugin is not enough on its own:
        # one that starts with a moment left could still block for the
        # executor's full timeout. On the rig that turned a 20s budget into a
        # 31.8s pass, so the remaining budget is passed down as the timeout.
        seen = []

        class Executor:
            def execute_update(self, plugin, plugin_id, timeout=None):
                seen.append(timeout)
                return True

        c = _controller(['a', 'b', 'c'], Executor())
        deadline = time.time() + 5
        c._update_modules(deadline=deadline)

        assert seen and all(t is not None for t in seen), seen
        assert all(t <= 5.01 for t in seen), seen
        # The exact remainder, never clamped up: clamping would let the pass
        # run past its deadline. Anything below the floor is deferred instead,
        # so what does start always has a usable slot.
        assert all(t >= _MIN_INITIAL_UPDATE_TIMEOUT_SECONDS for t in seen), seen

    def test_without_a_deadline_the_executor_default_is_left_alone(self):
        seen = []

        class Executor:
            def execute_update(self, plugin, plugin_id, timeout=None):
                seen.append(timeout)
                return True

        _controller(['a'], Executor())._update_modules()
        assert seen == [None], seen


class TestTheBudgetItself:
    def test_it_is_short_enough_to_be_worth_having(self):
        # The measured uncapped worst case was 82s; a budget near that would
        # not bound anything.
        assert _INITIAL_UPDATE_BUDGET_SECONDS <= 30

    def test_it_is_long_enough_for_a_quick_plugin_or_two(self):
        assert _INITIAL_UPDATE_BUDGET_SECONDS >= 5


class TestNothingIsSilentlyDropped:
    def test_deferred_plugins_are_named_in_the_log(self, caplog):
        ex = FakeExecutor()
        c = _controller(['a', 'b'], ex)
        with caplog.at_level('INFO'):
            c._update_modules(deadline=time.time() - 1)
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert 'a' in text and 'b' in text, text
        assert 'budget' in text.lower(), text

    def test_nothing_is_logged_when_all_of_them_ran(self, caplog):
        ex = FakeExecutor()
        c = _controller(['a'], ex)
        with caplog.at_level('INFO'):
            c._update_modules(deadline=time.time() + 30)
        assert not any('budget' in r.getMessage().lower() for r in caplog.records)


class TestItDoesNotBreakTheOrdinaryPaths:
    def test_no_plugin_manager_is_harmless(self):
        c = DisplayController.__new__(DisplayController)
        c.plugin_manager = None
        c._update_modules(deadline=time.time() - 1)      # must not raise

    def test_an_empty_plugin_set_is_harmless(self):
        ex = FakeExecutor()
        _controller([], ex)._update_modules(deadline=time.time() + 5)
        assert ex.updated == []


class TestTooLittleBudgetDefersRatherThanClamps:
    def test_a_plugin_starting_below_the_floor_is_deferred(self):
        ex = FakeExecutor()
        c = _controller(['a'], ex)
        # Just under the floor: previously this was clamped up to the floor and
        # run anyway, which pushed the pass past its deadline.
        c._update_modules(
            deadline=time.time() + _MIN_INITIAL_UPDATE_TIMEOUT_SECONDS - 0.05)
        assert ex.updated == [], "started a plugin it could not give a slot to"

    def test_a_plugin_starting_above_the_floor_still_runs(self):
        ex = FakeExecutor()
        c = _controller(['a'], ex)
        c._update_modules(
            deadline=time.time() + _MIN_INITIAL_UPDATE_TIMEOUT_SECONDS + 1)
        assert ex.updated == ['a']

    def test_the_timeout_is_the_remainder_not_the_floor(self):
        seen = []

        class Executor:
            def execute_update(self, plugin, plugin_id, timeout=None):
                seen.append(timeout)
                return True

        c = _controller(['a'], Executor())
        c._update_modules(deadline=time.time() + 9)
        assert seen and 8.5 <= seen[0] <= 9.01, seen

    def test_the_pass_cannot_outlast_its_deadline(self, tiny_floor):
        # Every plugin sleeps well past the budget; the deferral keeps the
        # whole pass inside it rather than overrunning by a floor's worth.
        ex = FakeExecutor(cost=0.4, slow={'a', 'b', 'c', 'd', 'e'})
        c = _controller(['a', 'b', 'c', 'd', 'e'], ex)
        started = time.time()
        c._update_modules(deadline=started + 0.5)
        assert time.time() - started < 1.2, "%.2fs" % (time.time() - started)
