"""run_iteration() does no per-iteration static-mode bookkeeping.

Every iteration used to rebuild a set of STATIC-mode plugins -- asking each
plugin for its display mode and logging the result at INFO -- that nothing
ever read. The static pause is driven by _check_static_plugin_trigger(), which
looks at the next segment, not at that set.
"""

import logging
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.plugin_system.base_plugin import VegasDisplayMode
from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.coordinator import VegasModeCoordinator


def _coordinator(plugins):
    coord = VegasModeCoordinator.__new__(VegasModeCoordinator)
    coord.vegas_config = VegasModeConfig.from_config({'display': {'vegas_scroll': {
        'enabled': True, 'max_cycle_duration': 0}}})
    coord.render_pipeline = MagicMock()
    # The loop paces itself from these (#628); a MagicMock can't be compared.
    coord.render_pipeline.frame_interval = 0.0
    coord.render_pipeline.target_fps = 90
    coord.stream_manager = MagicMock()
    coord.display_manager = MagicMock()
    coord.plugin_manager = SimpleNamespace(plugins=plugins, get_plugin=plugins.get)
    coord._state_lock = threading.Lock()
    coord._is_active = True
    coord._is_paused = False
    coord._should_stop = False
    coord._fps_last_health_log = 0.0
    coord._fps_was_degraded = False
    coord._live_priority_check = None
    coord._live_priority_active = False
    coord._interrupt_check = None
    coord._interrupt_check_interval = 10
    coord._update_callback = None
    coord._update_tick_running = False
    coord._check_static_plugin_trigger = lambda: None
    coord.run_frame = lambda: True
    return coord


def test_an_iteration_does_not_poll_every_plugin_for_its_mode(caplog):
    static = MagicMock()
    static.get_vegas_display_mode.return_value = VegasDisplayMode.STATIC
    coord = _coordinator({'static-plugin': static})

    with caplog.at_level(logging.INFO, logger='src.vegas_mode.coordinator'):
        assert coord.run_iteration() is True

    static.get_vegas_display_mode.assert_not_called()
    assert not any('Static mode plugins' in r.getMessage() for r in caplog.records)


def _live_coordinator(live):
    """A coordinator running the real run_frame(), with a switchable live check.

    live_in_ticker off: these pin the full-screen takeover, which is no longer
    the default.
    """
    coord = _coordinator({})
    del coord.run_frame  # the real one: it is what refuses frames while paused
    coord.vegas_config = VegasModeConfig.from_config({'display': {'vegas_scroll': {
        'enabled': True, 'max_cycle_duration': 0, 'continuous_scroll': True,
        'live_in_ticker': False}}})
    coord.render_pipeline.has_deferred.return_value = False
    coord.render_pipeline.needs_extension.return_value = False
    coord.render_pipeline.render_frame.return_value = True
    coord._pending_config_update = False
    coord._last_live_check = float('-inf')
    coord._live_priority_check = lambda: live[0]
    return coord


def test_vegas_resumes_after_a_live_priority_pause():
    """The pause for live content was only ever lifted from inside run_frame(),
    which returns before reaching that check while paused -- so once live
    content had interrupted the ticker, it never came back until a restart.
    The display controller only calls run_iteration() when nothing preempts
    Vegas, so an iteration starting while paused for live content resumes."""
    live = ['nfl_live']
    coord = _live_coordinator(live)

    assert coord.run_iteration() is False
    assert coord._is_paused and coord._live_priority_active
    coord.render_pipeline.render_frame.assert_not_called()

    live[0] = None
    coord._last_live_check = float('-inf')
    assert coord.run_iteration() is True
    assert not coord._is_paused and not coord._live_priority_active
    coord.render_pipeline.render_frame.assert_called()


def test_stop_clears_a_live_priority_pause():
    coord = _live_coordinator(['nfl_live'])
    coord._restore_switch_interval = lambda: None
    coord._remove_render_gate = lambda: None
    coord.run_iteration()
    assert coord._is_paused

    coord.stop()
    assert not coord._is_paused and not coord._live_priority_active


def test_iteration_length_ignores_wall_clock_steps(monkeypatch):
    """No RTC: the wall clock jumps when NTP first syncs. A forward step used
    to end the iteration on its first frame."""
    from src.vegas_mode import coordinator as coordinator_module
    coord = _coordinator({})
    coord.vegas_config = VegasModeConfig.from_config({'display': {'vegas_scroll': {
        'enabled': True, 'max_cycle_duration': 10}}})
    frames = [0]

    def frame():
        frames[0] += 1
        return True
    coord.run_frame = frame
    clock = [1000.0]
    wall = [1000.0]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    def wall_time():
        wall[0] += 3600.0  # NTP stepping the wall clock forward
        return wall[0]
    monkeypatch.setattr(coordinator_module.time, 'monotonic', monotonic)
    monkeypatch.setattr(coordinator_module.time, 'time', wall_time)
    monkeypatch.setattr(coordinator_module.time, 'sleep', lambda s: None)

    assert coord.run_iteration() is True
    assert frames[0] > 1
