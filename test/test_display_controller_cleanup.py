"""DisplayController.cleanup() must tear down Vegas mode.

VegasModeCoordinator.cleanup() stops the scroll, resets the render pipeline and
stream manager and drops the adapter's content cache, but the controller never
called it, so none of that teardown ran at shutdown.
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("EMULATOR", "true")

from src.display_controller import DisplayController


def _controller(vegas_coordinator):
    dc = object.__new__(DisplayController)
    dc.plugin_manager = MagicMock()
    dc.config_service = MagicMock()
    dc._font_usage_publisher = None
    dc.display_manager = MagicMock()
    dc.vegas_coordinator = vegas_coordinator
    return dc


def test_cleanup_tears_down_vegas():
    vegas = MagicMock()
    _controller(vegas).cleanup()
    vegas.cleanup.assert_called_once_with()


def test_vegas_is_torn_down_before_the_display_manager():
    # Stopping Vegas resets the display's scrolling state, so the display
    # manager has to still be there when it runs.
    order = []
    vegas = MagicMock()
    vegas.cleanup.side_effect = lambda: order.append('vegas')
    dc = _controller(vegas)
    dc.display_manager.cleanup.side_effect = lambda: order.append('display')

    dc.cleanup()

    assert order == ['vegas', 'display']


def test_cleanup_without_vegas_does_not_raise():
    dc = _controller(None)
    dc.cleanup()
    dc.display_manager.cleanup.assert_called_once_with()


def test_cleanup_stops_display_sync_after_vegas():
    # Stopping sync withdraws its status file; left behind, the web UI kept
    # reporting a connected peer after the display service had stopped.
    order = []
    vegas = MagicMock()
    vegas.cleanup.side_effect = lambda: order.append('vegas')
    dc = _controller(vegas)
    dc.sync_manager = MagicMock()
    dc.sync_manager.stop.side_effect = lambda: order.append('sync')

    dc.cleanup()

    assert order == ['vegas', 'sync']


def test_a_failing_sync_stop_does_not_stop_the_rest():
    dc = _controller(None)
    dc.sync_manager = MagicMock()
    dc.sync_manager.stop.side_effect = OSError('boom')

    dc.cleanup()

    dc.display_manager.cleanup.assert_called_once_with()


def test_a_failing_vegas_teardown_does_not_stop_the_rest():
    vegas = MagicMock()
    vegas.cleanup.side_effect = RuntimeError('boom')
    dc = _controller(vegas)

    dc.cleanup()

    dc.display_manager.cleanup.assert_called_once_with()
