"""A plugin can ask to be polled faster while it has something live.

The manifest carries one static `update_interval`, and until now that was the
only thing the scheduler would look at. A sports scoreboard needs 15 seconds
while a game is in progress and 15 minutes when nothing is on, and no single
number expresses that.

Measured consequence, on a live rig during an NFL game in its fourth quarter:
football-scoreboard's manifest pins update_interval to 60, so ESPN was polled
exactly once a minute --

    23:21:49  23:22:50  23:23:50  23:24:50  23:25:50

-- while the plugin's own live_update_interval said 15. The clock and score on
the panel therefore lagged by up to a minute during a two-minute drill, which
reads to a viewer as a frozen display.

get_update_interval() lets the plugin say what it needs per tick. These tests
pin the parts that are easy to get wrong: that the hook wins, that "no opinion"
falls back cleanly, that a broken hook cannot stop a plugin updating, and that
the hook is not accidentally cached (which would defeat the whole point).
"""

from unittest.mock import MagicMock

import pytest

from src.plugin_system.plugin_manager import PluginManager


@pytest.fixture
def manager():
    pm = PluginManager.__new__(PluginManager)
    pm._update_interval_cache = {}
    pm.plugin_manifests = {"sports": {"update_interval": 60}}
    pm.config_manager = None
    pm.logger = MagicMock()
    return pm


class _Plugin:
    def __init__(self, wants=None, raises=False):
        self._wants = wants
        self._raises = raises
        self.calls = 0

    def get_update_interval(self):
        self.calls += 1
        if self._raises:
            raise RuntimeError("plugin is broken")
        return self._wants


def test_the_hook_overrides_the_manifest(manager):
    """The whole point: 15s while live, not the manifest's 60."""
    assert manager._get_plugin_update_interval("sports", _Plugin(wants=15)) == 15.0


def test_none_means_no_opinion_and_the_manifest_applies(manager):
    """A plugin with nothing live should not have to restate the default."""
    assert manager._get_plugin_update_interval("sports", _Plugin(wants=None)) == 60.0


def test_a_plugin_without_the_hook_is_unaffected(manager):
    """Every existing plugin predates this and must keep its manifest value."""
    assert manager._get_plugin_update_interval("sports", MagicMock(spec=[])) == 60.0


def test_the_hook_is_consulted_every_time(manager):
    """Caching the answer would make it exactly as static as the manifest.

    The static path *is* cached -- reading config is expensive -- so it would be
    an easy mistake to let the dynamic answer ride along in that cache.
    """
    plugin = _Plugin(wants=15)
    for _ in range(5):
        manager._get_plugin_update_interval("sports", plugin)
    assert plugin.calls == 5, f"hook called {plugin.calls} times in 5 lookups"


def test_the_cadence_can_change_between_ticks(manager):
    """A game going final must slow the polling back down without a reload."""
    plugin = _Plugin(wants=15)
    assert manager._get_plugin_update_interval("sports", plugin) == 15.0
    plugin._wants = None                      # game ended
    assert manager._get_plugin_update_interval("sports", plugin) == 60.0
    plugin._wants = 15                        # another game started
    assert manager._get_plugin_update_interval("sports", plugin) == 15.0


class TestABrokenHookCannotStopUpdates:
    """A scheduler that propagates a plugin's bug stops every other plugin too."""

    def test_a_raising_hook_falls_back_to_the_manifest(self, manager):
        assert manager._get_plugin_update_interval("sports", _Plugin(raises=True)) == 60.0

    @pytest.mark.parametrize("junk", ["soon", object(), [], {}])
    def test_a_non_numeric_hook_falls_back(self, manager, junk):
        assert manager._get_plugin_update_interval("sports", _Plugin(wants=junk)) == 60.0

    @pytest.mark.parametrize("junk", [float("nan"), float("inf")])
    def test_nan_and_infinity_fall_back(self, manager, junk):
        assert manager._get_plugin_update_interval("sports", _Plugin(wants=junk)) == 60.0


class TestTheFloor:
    """A plugin asking for 0 would be re-entered on every tick of the render
    loop, which is a busy-wait against whatever API it fetches."""

    @pytest.mark.parametrize("wants", [0, 0.5, -10])
    def test_small_and_negative_requests_are_clamped(self, manager, wants):
        got = manager._get_plugin_update_interval("sports", _Plugin(wants=wants))
        assert got == PluginManager.MIN_DYNAMIC_UPDATE_INTERVAL

    def test_a_reasonable_request_is_not_clamped(self, manager):
        assert manager._get_plugin_update_interval("sports", _Plugin(wants=15)) == 15.0


def test_the_static_path_still_prefers_the_manifest_over_config():
    """Deliberately unchanged, and not an oversight.

    Config values that have sat inert behind the manifest are stale by
    definition -- nothing has been honouring them. On one rig, football and
    baseball both carry update_interval 3600 in config against a manifest 60;
    making config win would slow those plugins by 60x. The dynamic hook is the
    supported way for a plugin to vary its own cadence, so flipping this is
    both risky and no longer necessary.
    """
    pm = PluginManager.__new__(PluginManager)
    pm._update_interval_cache = {}
    pm.plugin_manifests = {"sports": {"update_interval": 60}}
    cfg = MagicMock()
    cfg.get_config.return_value = {"sports": {"update_interval": 3600}}
    pm.config_manager = cfg
    pm.logger = MagicMock()
    assert pm._get_plugin_update_interval("sports", MagicMock(spec=[])) == 60.0


def test_config_is_still_used_when_the_manifest_is_silent():
    pm = PluginManager.__new__(PluginManager)
    pm._update_interval_cache = {}
    pm.plugin_manifests = {"sports": {}}
    cfg = MagicMock()
    cfg.get_config.return_value = {"sports": {"update_interval": 120}}
    pm.config_manager = cfg
    pm.logger = MagicMock()
    assert pm._get_plugin_update_interval("sports", MagicMock(spec=[])) == 120.0
