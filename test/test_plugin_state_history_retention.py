"""Retention is bounded by age first and by count second.

The cap added in the parent change is a flat entry count, and an entry count
answers the wrong question. What a reader wants from this history is "the last
couple of hours"; how many transitions that is depends entirely on the
plugin's update interval, which on a real board spans 2s to 3600s. A flat 200
entries is 4.2 days of history for the slowest plugin and 3.3 minutes for the
fastest -- so the plugin churning hardest, the one actually worth looking at,
keeps the least.

Trimming by age makes the retained window comparable whatever the cadence, and
the count then serves only as a memory ceiling for pollers fast enough to
produce thousands of transitions inside that window.
"""

import time
import pytest

from src.plugin_system.plugin_state import (
    PluginState,
    PluginStateManager,
    MAX_STATE_HISTORY_PER_PLUGIN,
    STATE_HISTORY_MAX_AGE_SECONDS,
)


class FakeClock:
    """A monotonic clock the test drives, so no test has to sleep."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr("src.plugin_system.plugin_state.time.monotonic", c)
    return c


def _cycle(manager, plugin_id, clock, interval, cycles):
    """One update cycle: RUNNING on reserve, ENABLED on finish."""
    for _ in range(cycles):
        manager.set_state(plugin_id, PluginState.RUNNING)
        manager.set_state(plugin_id, PluginState.ENABLED)
        clock.advance(interval)


def test_transitions_older_than_the_window_are_dropped(clock):
    m = PluginStateManager()
    _cycle(m, "clock", clock, interval=60, cycles=10)
    assert len(m.get_state_history("clock")) == 20

    # Nothing happens for longer than the window, then one more cycle.
    clock.advance(STATE_HISTORY_MAX_AGE_SECONDS + 1)
    _cycle(m, "clock", clock, interval=60, cycles=1)

    assert len(m.get_state_history("clock")) == 2, (
        "only the transitions inside the window should survive")


def test_every_plugin_keeps_the_same_WINDOW_not_the_same_COUNT(clock):
    """The point of the age policy, stated as the property that distinguishes it.

    Run both plugins for three times the retention window. Under a flat count
    cap the slow one would still be holding transitions from hours before the
    window, because it never produces enough entries to evict them. Under the
    age policy each plugin retains its own last two hours and no more --
    different entry counts, same span of time.
    """
    window = STATE_HISTORY_MAX_AGE_SECONDS
    m = PluginStateManager()

    _cycle(m, "slow", clock, interval=60, cycles=(3 * window) // 60)
    slow = len(m.get_state_history("slow"))

    # Assert the property directly rather than a derived count. The guarantee
    # is about the SPAN of retained history, not its age against the current
    # clock: trimming happens on append, so a plugin that has gone quiet keeps
    # its last window until it writes again. That is intentional -- it is
    # bounded either way, and a lazy trim costs nothing on the hot path.
    stamps = [stamp for stamp, _ in m._state_history["slow"]]
    assert stamps[-1] - stamps[0] <= window, (
        f"retained history spans {stamps[-1] - stamps[0]:.0f}s, "
        f"window is {window}s")
    assert slow < 2 * ((3 * window) // 60), (
        f"slow plugin kept {slow} entries -- three windows' worth was retained")

    clock.t = 1000.0
    _cycle(m, "fast", clock, interval=2, cycles=(3 * window) // 2)
    fast = len(m.get_state_history("fast"))

    # Different counts, and the fast poller keeps more of them -- under a flat
    # count cap these would be equal and the fast one would cover minutes.
    assert fast > slow, f"fast={fast} slow={slow}"


def test_the_count_ceiling_still_bounds_a_fast_poller(clock):
    """Age alone would let a 2s plugin hold 7,200 entries."""
    m = PluginStateManager()
    _cycle(m, "flights", clock, interval=2, cycles=STATE_HISTORY_MAX_AGE_SECONDS)
    assert len(m.get_state_history("flights")) <= MAX_STATE_HISTORY_PER_PLUGIN


def test_a_burst_inside_the_window_is_capped_not_kept(clock):
    """Transitions with no time between them still cannot grow without bound."""
    m = PluginStateManager()
    for _ in range(MAX_STATE_HISTORY_PER_PLUGIN * 3):
        m.set_state("flapping", PluginState.RUNNING)   # clock never advances
    assert len(m.get_state_history("flapping")) <= MAX_STATE_HISTORY_PER_PLUGIN


def test_ageing_out_does_not_disturb_the_lifetime_count(clock):
    m = PluginStateManager()
    _cycle(m, "clock", clock, interval=60, cycles=10)
    clock.advance(STATE_HISTORY_MAX_AGE_SECONDS + 1)
    _cycle(m, "clock", clock, interval=60, cycles=1)

    assert len(m.get_state_history("clock")) == 2
    assert m.get_state_info("clock")["state_history_count"] == 22, (
        "the lifetime total must survive trimming, it is the flap signal")


def test_the_surviving_entries_are_the_recent_ones(clock):
    m = PluginStateManager()
    _cycle(m, "clock", clock, interval=60, cycles=5)
    clock.advance(STATE_HISTORY_MAX_AGE_SECONDS + 1)
    m.set_state("clock", PluginState.ERROR)

    history = m.get_state_history("clock")
    assert [h["to"] for h in history] == ["error"]


def test_a_monotonic_clock_is_used_not_the_wall_clock(clock):
    """A DST shift or NTP step must not flush the history.

    The trim reads time.monotonic(); the human-readable datetime inside each
    transition is for display only.
    """
    m = PluginStateManager()
    _cycle(m, "clock", clock, interval=60, cycles=3)
    before = len(m.get_state_history("clock"))

    import datetime as real_datetime

    class ShiftedDatetime(real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.datetime(1999, 1, 1)   # clock jumps backwards

    import src.plugin_system.plugin_state as ps
    original = ps.datetime
    ps.datetime = ShiftedDatetime
    try:
        m.set_state("clock", PluginState.ENABLED)
    finally:
        ps.datetime = original

    assert len(m.get_state_history("clock")) == before + 1, (
        "a wall-clock jump must not trim anything")
