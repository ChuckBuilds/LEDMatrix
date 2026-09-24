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
    coord.stats = {'cycles_completed': 0, 'interruptions': 0}
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
