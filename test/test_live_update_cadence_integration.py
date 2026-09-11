"""The scheduler actually polls faster while a plugin reports live content.

test_plugin_dynamic_update_interval.py proves _get_plugin_update_interval()
returns the plugin's requested number. That is not the same claim as "the
plugin gets updated more often", and the gap between those two is exactly where
the original bug lived: the plugin knew it wanted 15s, said so in
live_update_interval, and nothing downstream acted on it.

So this drives the real run_scheduled_updates() over a simulated hour of ticks
and counts dispatches. It is the test that would have caught the reported bug:

    "the football plugin with live games only updates the live game in
     progress if I restart the display"

Measured on a rig before the fix, during an NFL fourth quarter, ESPN was polled
at 23:21:49 / 23:22:50 / 23:23:50 / 23:24:50 -- exactly the manifest's 60s,
never the configured 15.
"""

from unittest.mock import MagicMock

import pytest

from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.plugin_state import PluginState


class _StateManager:
    """Enough of the real state machine for reservation to behave."""

    def __init__(self):
        self.states = {}

    def can_execute(self, plugin_id):
        return self.states.get(plugin_id, PluginState.ENABLED) != PluginState.RUNNING

    def set_state(self, plugin_id, state):
        self.states[plugin_id] = state


class _Plugin:
    """A sports plugin: fast while something is live, quiet otherwise."""

    enabled = True

    def __init__(self, live_interval=15):
        self.live = False
        self._live_interval = live_interval

    def get_update_interval(self):
        return self._live_interval if self.live else None

    def update(self):
        pass


@pytest.fixture
def scheduler():
    pm = PluginManager.__new__(PluginManager)
    import threading
    pm._update_interval_cache = {}
    pm.plugin_manifests = {"sports": {"update_interval": 60}}
    pm.config_manager = None
    pm.logger = MagicMock()
    pm.state_manager = _StateManager()
    pm.health_tracker = None
    pm._synchronous_updates = False
    pm._reservation_lock = threading.Lock()
    pm._plugin_last_update_lock = threading.Lock()
    pm.plugin_last_update = {}
    pm.dispatched = []

    # Stand in for the background worker: record the dispatch and complete it,
    # so the next tick can reserve the plugin again.
    def _enqueue(plugin_id, scheduled_time):
        pm.dispatched.append(scheduled_time)
        pm.plugin_last_update[plugin_id] = scheduled_time
        pm.state_manager.set_state(plugin_id, PluginState.ENABLED)

    pm._enqueue_update = _enqueue
    return pm


def _run_for(pm, plugin, seconds, start=1_000_000.0, step=1.0):
    """Tick the real scheduler once a second for `seconds`."""
    pm.plugins = {"sports": plugin}
    t = start
    end = start + seconds
    while t < end:
        pm.run_scheduled_updates(current_time=t)
        t += step
    return len(pm.dispatched)


class TestTheReportedBug:
    def test_a_live_game_is_polled_at_the_live_interval(self, scheduler):
        """15s while live, not the manifest's 60s. This is the fix."""
        plugin = _Plugin(live_interval=15)
        plugin.live = True
        count = _run_for(scheduler, plugin, seconds=600)   # ten minutes
        # 600s / 15s = 40, allowing one for the first tick's free run.
        assert 38 <= count <= 41, (
            f"{count} updates in 10 minutes of a live game; expected ~40 at 15s. "
            f"At the manifest's 60s it would be ~10 -- the reported bug.")

    def test_the_same_window_without_the_hook_is_the_old_behaviour(self, scheduler):
        """Pin what the bug actually looked like, so the contrast is asserted."""
        plugin = _Plugin()
        plugin.live = True
        plugin.get_update_interval = lambda: None      # pre-fix: no opinion
        count = _run_for(scheduler, plugin, seconds=600)
        assert 9 <= count <= 11, (
            f"{count} updates in 10 minutes; expected ~10 at the manifest's 60s")

    def test_idle_polling_is_unchanged(self, scheduler):
        """The regression that would be worse than the bug.

        Asking for 15s year-round would poll ESPN four times a minute all
        summer. Idle must stay exactly on the manifest.
        """
        plugin = _Plugin()
        plugin.live = False
        count = _run_for(scheduler, plugin, seconds=3600)  # a full hour idle
        assert 59 <= count <= 61, (
            f"{count} updates in an idle hour; expected ~60 at the manifest's 60s")


class TestTheCadenceTracksTheGame:
    def test_it_speeds_up_when_a_game_starts_and_slows_when_it_ends(self, scheduler):
        """No reload, no restart -- the whole point of a per-tick hook."""
        plugin = _Plugin(live_interval=15)
        pm = scheduler

        _run_for(pm, plugin, seconds=300, start=1_000_000.0)
        idle_before = len(pm.dispatched)

        plugin.live = True
        _run_for(pm, plugin, seconds=300, start=1_000_300.0)
        during = len(pm.dispatched) - idle_before

        plugin.live = False
        _run_for(pm, plugin, seconds=300, start=1_000_600.0)
        idle_after = len(pm.dispatched) - idle_before - during

        assert during > idle_before * 3, (
            f"live window got {during} updates vs {idle_before} idle; "
            "the hook did not speed anything up")
        assert idle_after <= idle_before + 1, (
            f"{idle_after} updates after the game ended vs {idle_before} before; "
            "the fast cadence leaked past the live window")


def test_a_broken_hook_does_not_stop_the_plugin_updating(scheduler):
    """A scheduler that propagates one plugin's bug stops every other plugin."""
    plugin = _Plugin()
    plugin.get_update_interval = MagicMock(side_effect=RuntimeError("boom"))
    count = _run_for(scheduler, plugin, seconds=600)
    assert 9 <= count <= 11, (
        f"{count} updates; a raising hook should fall back to the manifest's 60s")
