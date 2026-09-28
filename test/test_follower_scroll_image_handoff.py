"""Follower adoption of the leader's scroll image (src/display_controller.py).

The leader's image arrives on the sync TCP thread. That callback used to set
scroll_helper.cached_image, cached_array and total_scroll_width one after
another while the render thread sliced frames out of them, so a frame could
pair the new array with the old width. The callback now only queues the
image; the render thread swaps all three in at the start of a follower frame.
"""

import os
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("EMULATOR", "true")

from PIL import Image  # noqa: E402

from src.display_controller import DisplayController  # noqa: E402


def _wired_controller():
    dc = object.__new__(DisplayController)
    dc.config = {"display": {"vegas_scroll": {"enabled": True}}, "sync": {}}
    dc.display_manager = MagicMock()
    dc.plugin_manager = MagicMock()
    dc.sync_manager = MagicMock()
    dc._check_live_priority = MagicMock()
    dc._check_vegas_interrupt = MagicMock(return_value=False)
    dc._follower_pending_new_image = True
    dc._follower_incoming_image = deque(maxlen=1)

    old = Image.new("RGB", (100, 16))
    helper = SimpleNamespace(cached_image=old, cached_array="old-array",
                             total_scroll_width=100)
    coordinator = MagicMock()
    coordinator.render_pipeline = SimpleNamespace(scroll_helper=helper)
    with patch('src.vegas_mode.VegasModeCoordinator',
               MagicMock(return_value=coordinator)):
        dc._initialize_vegas_mode()
    on_image = dc.sync_manager.set_on_scroll_image.call_args[0][0]
    return dc, coordinator.render_pipeline, helper, old, on_image


def test_tcp_callback_does_not_touch_the_render_state():
    dc, _rp, helper, old, on_image = _wired_controller()
    on_image(Image.new("RGB", (300, 16)))
    # Nothing the render thread reads has changed yet.
    assert helper.cached_image is old
    assert helper.cached_array == "old-array"
    assert helper.total_scroll_width == 100
    assert dc._follower_pending_new_image is True


def test_render_thread_adopts_all_three_together():
    dc, rp, helper, _old, on_image = _wired_controller()
    new = Image.new("RGB", (300, 16), (255, 0, 0))
    on_image(new)
    dc._adopt_follower_scroll_image(rp)
    assert helper.cached_image is new
    assert helper.cached_array.shape == (16, 300, 3)
    assert helper.cached_array[0, 0].tolist() == [255, 0, 0]
    assert helper.total_scroll_width == 300
    assert dc._follower_pending_new_image is False
    # Consumed: a second frame changes nothing.
    helper.total_scroll_width = 7
    dc._adopt_follower_scroll_image(rp)
    assert helper.total_scroll_width == 7


def test_only_the_latest_image_is_adopted():
    dc, rp, helper, _old, on_image = _wired_controller()
    on_image(Image.new("RGB", (200, 16)))
    latest = Image.new("RGB", (400, 16))
    on_image(latest)
    dc._adopt_follower_scroll_image(rp)
    assert helper.cached_image is latest
    assert helper.total_scroll_width == 400
