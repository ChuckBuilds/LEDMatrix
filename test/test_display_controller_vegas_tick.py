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

from typing import Dict, List, Optional
from unittest.mock import MagicMock

from src.display_controller import DisplayController


def _make_controller(updated: Optional[List[str]] = None, vegas_coordinator: Optional[MagicMock] = None) -> DisplayController:
    dc = object.__new__(DisplayController)
    dc.plugin_manager = MagicMock()
    dc.plugin_manager.run_scheduled_updates_with_changes.return_value = list(updated or [])
    dc.vegas_coordinator = vegas_coordinator
    return dc


class TestTickPluginUpdatesForVegas:
    def test_marks_only_plugins_whose_timestamp_advanced(self):
        vc = MagicMock()
        dc = _make_controller(updated=["stock-news"], vegas_coordinator=vc)

        dc._tick_plugin_updates_for_vegas()

        vc.mark_plugin_updated.assert_called_once_with("stock-news")

    def test_no_advance_marks_nothing(self):
        vc = MagicMock()
        dc = _make_controller(updated=[], vegas_coordinator=vc)

        dc._tick_plugin_updates_for_vegas()

        vc.mark_plugin_updated.assert_not_called()

    def test_no_vegas_coordinator_does_not_raise(self):
        dc = _make_controller(updated=["stock-news"], vegas_coordinator=None)

        dc._tick_plugin_updates_for_vegas()  # must not raise

    def test_mark_plugin_updated_exception_does_not_propagate(self):
        """One plugin's mark_plugin_updated failing must not stop the tick
        or crash the update loop it runs in."""
        vc = MagicMock()
        vc.mark_plugin_updated.side_effect = [RuntimeError("boom"), None]
        dc = _make_controller(updated=["a", "b"], vegas_coordinator=vc)

        dc._tick_plugin_updates_for_vegas()  # must not raise

        assert vc.mark_plugin_updated.call_count == 2


class TestVegasCoordinatorCallbackWiring:
    def test_initialize_wires_vegas_aware_tick_as_update_callback(self):
        """The Vegas coordinator must be given the Vegas-aware
        _tick_plugin_updates_for_vegas as its update callback, not the plain
        _tick_plugin_updates() -- that's the exact wiring PR #330 dropped."""
        dc = object.__new__(DisplayController)
        dc.config = {"display": {"vegas_scroll": {"enabled": True}}, "sync": {}}
        dc.display_manager = MagicMock()
        dc.plugin_manager = MagicMock()
        dc.sync_manager = MagicMock()
        dc._check_live_priority = MagicMock()
        dc._check_vegas_interrupt = MagicMock(return_value=False)

        fake_coordinator = MagicMock()

        import src.display_controller as dc_module
        original_imported = dc_module._vegas_mode_imported
        original_class = dc_module.VegasModeCoordinator
        try:
            dc_module._vegas_mode_imported = True
            dc_module.VegasModeCoordinator = MagicMock(return_value=fake_coordinator)
            dc._initialize_vegas_mode()
        finally:
            dc_module._vegas_mode_imported = original_imported
            dc_module.VegasModeCoordinator = original_class

        fake_coordinator.set_update_callback.assert_called_once_with(dc._tick_plugin_updates_for_vegas)
