"""
Regression tests for DisplayController._tick_plugin_updates_for_vegas().

PR #299 added logic to detect which plugins actually got fresh data on a
scheduled-update tick and notify Vegas mode via
vegas_coordinator.mark_plugin_updated(), so a live score change reaches the
scroll within seconds instead of waiting for a full cycle. PR #330's
multi-display sync refactor deleted this method (folding the callback back
to the plain _tick_plugin_updates(), which reports nothing), silently
orphaning VegasModeCoordinator.mark_plugin_updated() -- it has had zero
callers since.
"""

from unittest.mock import MagicMock

from src.display_controller import DisplayController


def _make_controller(plugin_last_update, vegas_coordinator=None):
    dc = object.__new__(DisplayController)
    dc.plugin_manager = MagicMock()
    dc.plugin_manager.plugin_last_update = dict(plugin_last_update)
    dc.vegas_coordinator = vegas_coordinator
    return dc


class TestTickPluginUpdatesForVegas:
    def test_marks_only_plugins_whose_timestamp_advanced(self):
        dc = _make_controller({"stock-news": 100.0, "odds-ticker": 100.0})
        vc = MagicMock()
        dc.vegas_coordinator = vc

        # Simulate run_scheduled_updates() advancing only stock-news.
        def fake_tick():
            dc.plugin_manager.plugin_last_update["stock-news"] = 200.0
        dc._tick_plugin_updates = fake_tick

        dc._tick_plugin_updates_for_vegas()

        vc.mark_plugin_updated.assert_called_once_with("stock-news")

    def test_no_advance_marks_nothing(self):
        dc = _make_controller({"stock-news": 100.0})
        vc = MagicMock()
        dc.vegas_coordinator = vc
        dc._tick_plugin_updates = lambda: None

        dc._tick_plugin_updates_for_vegas()

        vc.mark_plugin_updated.assert_not_called()

    def test_no_vegas_coordinator_does_not_raise(self):
        dc = _make_controller({"stock-news": 100.0}, vegas_coordinator=None)

        def fake_tick():
            dc.plugin_manager.plugin_last_update["stock-news"] = 200.0
        dc._tick_plugin_updates = fake_tick

        dc._tick_plugin_updates_for_vegas()  # must not raise

    def test_mark_plugin_updated_exception_does_not_propagate(self):
        """One plugin's mark_plugin_updated failing must not stop the tick
        or crash the update loop it runs in."""
        dc = _make_controller({"a": 1.0, "b": 1.0})
        vc = MagicMock()
        vc.mark_plugin_updated.side_effect = [RuntimeError("boom"), None]
        dc.vegas_coordinator = vc

        def fake_tick():
            dc.plugin_manager.plugin_last_update["a"] = 2.0
            dc.plugin_manager.plugin_last_update["b"] = 2.0
        dc._tick_plugin_updates = fake_tick

        dc._tick_plugin_updates_for_vegas()  # must not raise

        assert vc.mark_plugin_updated.call_count == 2
