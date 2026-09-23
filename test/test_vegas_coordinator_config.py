"""VegasModeCoordinator: settings updates and the per-frame live check.

- Settings saved in the web UI are queued with update_config() and applied
  between frames. run_frame() returns early once Vegas has stopped, so a
  disable followed by a re-enable used to be queued forever.
- The live-priority scan asks every plugin mode whether it is live. It ran on
  every frame (125fps); it now runs at most every _LIVE_PRIORITY_CHECK_INTERVAL.
"""

import threading
from unittest.mock import MagicMock

from src.vegas_mode import coordinator as coordinator_module
from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.coordinator import VegasModeCoordinator


def _coordinator(active=True):
    # Built without __init__ so no display, stream or render stack is needed.
    c = VegasModeCoordinator.__new__(VegasModeCoordinator)
    c.vegas_config = VegasModeConfig.from_config({'display': {'vegas_scroll': {'enabled': True}}})
    c.render_pipeline = MagicMock()
    c.render_pipeline.has_deferred.return_value = False
    c.render_pipeline.needs_extension.return_value = False
    c.render_pipeline.is_cycle_complete.return_value = False
    c.stream_manager = MagicMock()
    c.plugin_adapter = MagicMock()
    c.stats = {'cycles_completed': 0, 'config_updates': 0}
    c._state_lock = threading.Lock()
    c._is_active = active
    c._is_paused = False
    c._should_stop = False
    c._pending_config_update = False
    c._pending_config = None
    c._config_version = 0
    c._live_priority_check = None
    c._live_priority_active = False
    c._interrupt_check = None
    c.sync_manager = None
    return c


class TestConfigWhileStopped:
    def test_queued_config_applies_while_stopped(self):
        c = _coordinator(active=False)
        c.update_config({'display': {'vegas_scroll': {'enabled': True, 'scroll_speed': 90}}})
        c.start = MagicMock()
        c.apply_pending_config_if_idle()
        assert c.vegas_config.scroll_speed == 90
        assert c._pending_config_update is False

    def test_reenabling_after_a_disable_takes_effect(self):
        c = _coordinator(active=False)
        c.vegas_config = VegasModeConfig.from_config({})  # disabled
        c.start = MagicMock()
        c.update_config({'display': {'vegas_scroll': {'enabled': True}}})
        c.apply_pending_config_if_idle()
        assert c.is_enabled
        c.start.assert_called_once()

    def test_running_coordinator_waits_for_the_next_frame(self):
        c = _coordinator(active=True)
        c.update_config({'display': {'vegas_scroll': {'enabled': True, 'scroll_speed': 90}}})
        c.apply_pending_config_if_idle()
        assert c.vegas_config.scroll_speed != 90
        assert c._pending_config_update is True


class TestLivePriorityThrottle:
    def test_scan_runs_at_most_once_per_interval(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(coordinator_module.time, 'monotonic', lambda: now[0])
        c = _coordinator()
        c._live_priority_check = MagicMock(return_value=None)

        for _ in range(10):
            c.run_frame()
        assert c._live_priority_check.call_count == 1

        now[0] += coordinator_module._LIVE_PRIORITY_CHECK_INTERVAL
        c.run_frame()
        assert c._live_priority_check.call_count == 2

    def test_live_content_still_pauses_vegas(self, monkeypatch):
        monkeypatch.setattr(coordinator_module.time, 'monotonic', lambda: 1000.0)
        c = _coordinator()
        c._live_priority_check = MagicMock(return_value='nfl_live')
        c.pause = MagicMock()
        assert c.run_frame() is False
        c.pause.assert_called_once()
        c.render_pipeline.render_frame.assert_not_called()

