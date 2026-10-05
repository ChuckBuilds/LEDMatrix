"""The web interface sees the display modes the display actually registered (#668).

A plugin may compute its modes from its config: soccer-scoreboard registers
``soccer_<league>_live/recent/upcoming`` for every league the user adds under
``custom_leagues``, and no manifest can list those ahead of time. The display
always rotated them -- DisplayController._register_loaded_plugin prefers
``plugin.modes`` -- but the web process reads plugins as files, so its mode
listing (/display/modes, the on-demand dialog) and find_plugin_for_mode
(/display/on-demand/start with a mode and no plugin_id) saw only manifests.

The display now records each plugin's registered modes in its plugin state,
the runtime snapshot carries them, and PluginCatalog prefers them while the
snapshot is live, falling back to the manifest when it is not.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cache_manager import CacheManager  # noqa: E402
from src.plugin_system import plugin_runtime as rt  # noqa: E402
from src.plugin_system.plugin_catalog import PluginCatalog  # noqa: E402
from src.plugin_system.plugin_runtime import (  # noqa: E402
    PluginRuntimePublisher, build_runtime_snapshot, read_plugin_runtime,
    view_from_snapshot,
)
from src.plugin_system.plugin_state import PluginState, PluginStateManager  # noqa: E402
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

DECLARED = ["soccer_eng.1_live", "soccer_eng.1_recent", "soccer_eng.1_upcoming"]
CUSTOM = ["soccer_sco.1_live", "soccer_sco.1_recent", "soccer_sco.1_upcoming"]
REGISTERED = DECLARED + CUSTOM


def _loaded_states(modes=None):
    states = PluginStateManager()
    states.set_state("soccer-scoreboard", PluginState.ENABLED)
    states.record_loaded("soccer-scoreboard", "2.24.1")
    if modes is not None:
        states.record_modes("soccer-scoreboard", modes)
    return states


@pytest.fixture
def shared_cache(tmp_path, monkeypatch):
    """Two cache managers over one directory: the display's and the web's."""
    monkeypatch.setattr(CacheManager, "_get_writable_cache_dir",
                        lambda self: str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir()
    display_cache, web_cache = CacheManager(), CacheManager()
    yield display_cache, web_cache
    display_cache.stop_cleanup_thread()
    web_cache.stop_cleanup_thread()


@pytest.fixture
def plugins_dir(tmp_path):
    root = tmp_path / "plugins"
    for plugin_id, modes in (("soccer-scoreboard", DECLARED), ("clock-simple", ["clock"])):
        (root / plugin_id).mkdir(parents=True)
        (root / plugin_id / "manifest.json").write_text(json.dumps({
            "id": plugin_id, "name": plugin_id, "version": "1.0.0",
            "class_name": "P", "display_modes": modes}), encoding="utf-8")
    return root


# --- The display records what it registered ---------------------------------

class TestStateManagerRecordsModes:
    def test_runtime_records_carry_them(self):
        assert _loaded_states(REGISTERED).runtime_records()[
            "soccer-scoreboard"]["modes"] == REGISTERED

    def test_none_until_registered(self):
        assert _loaded_states().runtime_records()["soccer-scoreboard"]["modes"] is None

    def test_a_new_list_is_a_change_the_same_one_is_not(self):
        """change_count drives the publisher: re-registering an unchanged
        plugin must not cost an SD-card write."""
        states = _loaded_states(DECLARED)
        before = states.change_count
        states.record_modes("soccer-scoreboard", list(DECLARED))
        assert states.change_count == before
        states.record_modes("soccer-scoreboard", REGISTERED)
        assert states.change_count == before + 1

    def test_ignored_for_a_plugin_that_is_not_loaded(self):
        states = PluginStateManager()
        states.record_modes("ghost", ["ghost"])
        assert "ghost" not in states.runtime_records()

    def test_unload_forgets_them(self):
        states = _loaded_states(REGISTERED)
        states.clear_state("soccer-scoreboard")
        assert "soccer-scoreboard" not in states.runtime_records()

    def test_a_reload_starts_without_them_until_registered_again(self):
        states = _loaded_states(REGISTERED)
        states.record_loaded("soccer-scoreboard", "2.25.0")
        assert states.runtime_records()["soccer-scoreboard"]["modes"] is None


class TestControllerRecordsOnRegistration:
    def test_plugin_modes_reach_the_state_manager(self, test_display_controller):
        """_register_loaded_plugin is the one path every load, enable and
        reload goes through."""
        c = test_display_controller
        states = _loaded_states()
        plugin = MagicMock()
        plugin.modes = list(REGISTERED)
        c.plugin_manager.state_manager = states
        c.plugin_manager.get_plugin = MagicMock(return_value=plugin)
        c.plugin_manager.plugin_manifests = {"soccer-scoreboard": {"display_modes": DECLARED}}

        c._register_loaded_plugin("soccer-scoreboard")

        assert states.runtime_records()["soccer-scoreboard"]["modes"] == REGISTERED

    def test_a_failing_state_manager_does_not_break_registration(self, test_display_controller):
        c = test_display_controller
        plugin = MagicMock()
        plugin.modes = ["clock"]
        c.plugin_manager.state_manager.record_modes = MagicMock(side_effect=RuntimeError("x"))
        c.plugin_manager.get_plugin = MagicMock(return_value=plugin)
        c.plugin_manager.plugin_manifests = {}

        assert c._register_loaded_plugin("clock-simple") == ["clock"]
        assert c.mode_to_plugin_id["clock"] == "clock-simple"


# --- The snapshot carries them; only a live view reports them ---------------

class TestSnapshotAndView:
    NOW = 1_800_000_000.0

    def _view(self, states, running=True, published_at=None):
        snapshot = build_runtime_snapshot(states, started_at=1.0, now=self.NOW,
                                          running=running)
        if published_at is not None:
            snapshot["published_at"] = published_at
        return view_from_snapshot(snapshot, now=self.NOW)

    def test_live_view_reports_the_registered_modes(self):
        assert self._view(_loaded_states(REGISTERED)).display_modes(
            "soccer-scoreboard") == REGISTERED

    def test_stale_and_stopped_views_report_nothing(self):
        states = _loaded_states(REGISTERED)
        assert self._view(states, published_at=self.NOW - 10_000).display_modes(
            "soccer-scoreboard") is None
        assert self._view(states, running=False).display_modes("soccer-scoreboard") is None

    def test_unregistered_or_unknown_plugins_report_nothing(self):
        view = self._view(_loaded_states())
        assert view.display_modes("soccer-scoreboard") is None
        assert view.display_modes("not-loaded") is None

    def test_a_runaway_list_is_bounded(self):
        modes = [f"m{i}" for i in range(1000)] + ["x" * 500]
        snapshot = build_runtime_snapshot(_loaded_states(modes), started_at=1.0, now=self.NOW)
        published = snapshot["plugins"]["soccer-scoreboard"]["modes"]
        assert len(published) == rt._MAX_MODES

    def test_non_strings_from_a_hand_made_snapshot_are_dropped(self):
        snapshot = {"schema": rt.SNAPSHOT_SCHEMA, "running": True,
                    "published_at": self.NOW, "plugins": {
                        "p": {"loaded": True, "modes": ["a", 3, None]}}}
        assert view_from_snapshot(snapshot, now=self.NOW).display_modes("p") == ["a"]


# --- The web's catalog prefers them -------------------------------------------

class TestCatalog:
    def _catalog(self, plugins_dir, web_cache):
        catalog = PluginCatalog(plugins_dir,
                                runtime_source=lambda: read_plugin_runtime(web_cache))
        catalog.discover_plugins()
        return catalog

    def test_live_display_modes_win_over_the_manifest(self, plugins_dir, shared_cache):
        display_cache, web_cache = shared_cache
        PluginRuntimePublisher(display_cache, _loaded_states(REGISTERED)).tick()
        catalog = self._catalog(plugins_dir, web_cache)
        assert catalog.get_plugin_display_modes("soccer-scoreboard") == REGISTERED

    def test_a_custom_league_mode_resolves_to_its_plugin(self, plugins_dir, shared_cache):
        """What /display/on-demand/start does with a mode and no plugin_id."""
        display_cache, web_cache = shared_cache
        PluginRuntimePublisher(display_cache, _loaded_states(REGISTERED)).tick()
        catalog = self._catalog(plugins_dir, web_cache)
        assert catalog.find_plugin_for_mode("SOCCER_SCO.1_LIVE") == "soccer-scoreboard"

    def test_a_plugin_the_display_has_not_loaded_falls_back_to_its_manifest(
            self, plugins_dir, shared_cache):
        display_cache, web_cache = shared_cache
        PluginRuntimePublisher(display_cache, _loaded_states(REGISTERED)).tick()
        catalog = self._catalog(plugins_dir, web_cache)
        assert catalog.get_plugin_display_modes("clock-simple") == ["clock"]
        assert catalog.find_plugin_for_mode("clock") == "clock-simple"

    def test_a_stopped_display_falls_back_to_manifests(self, plugins_dir, shared_cache):
        display_cache, web_cache = shared_cache
        publisher = PluginRuntimePublisher(display_cache, _loaded_states(REGISTERED))
        publisher.tick()
        publisher.stop()
        catalog = self._catalog(plugins_dir, web_cache)
        assert catalog.get_plugin_display_modes("soccer-scoreboard") == DECLARED
        assert catalog.find_plugin_for_mode("soccer_sco.1_live") is None

    def test_no_runtime_source_is_manifests_only(self, plugins_dir):
        catalog = PluginCatalog(plugins_dir)
        catalog.discover_plugins()
        assert catalog.get_plugin_display_modes("soccer-scoreboard") == DECLARED

    def test_a_failing_runtime_source_is_manifests_only(self, plugins_dir):
        def broken():
            raise OSError("cache gone")
        catalog = PluginCatalog(plugins_dir, runtime_source=broken)
        catalog.discover_plugins()
        assert catalog.get_plugin_display_modes("soccer-scoreboard") == DECLARED

    def test_one_listing_reads_the_view_once(self, plugins_dir):
        source = MagicMock(return_value=None)
        catalog = PluginCatalog(plugins_dir, runtime_source=source)
        catalog.discover_plugins()
        for _ in range(10):
            catalog.get_plugin_display_modes("soccer-scoreboard")
            catalog.find_plugin_for_mode("clock")
        assert source.call_count == 1


class TestDisplayModesRoute:
    def test_lists_the_custom_league_modes(self, api_v3_module, api_v3_client,  # noqa: F811
                                           plugins_dir, shared_cache):
        display_cache, web_cache = shared_cache
        PluginRuntimePublisher(display_cache, _loaded_states(REGISTERED)).tick()
        api = api_v3_module.api_v3
        api.plugin_catalog = PluginCatalog(
            plugins_dir, runtime_source=lambda: read_plugin_runtime(web_cache))
        api.config_manager.load_config = MagicMock(return_value={
            "soccer-scoreboard": {"enabled": True}})

        response = api_v3_client.get("/api/v3/display/modes")

        assert response.status_code == 200, response.get_data(as_text=True)
        modes = {m["mode"]: m for m in response.get_json()["data"]["modes"]}
        assert set(modes) == set(REGISTERED)
        assert modes["soccer_sco.1_live"]["plugin_id"] == "soccer-scoreboard"
