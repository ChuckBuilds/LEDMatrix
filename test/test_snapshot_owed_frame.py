"""A frame the preview throttle skipped still reaches the snapshot.

The preview snapshot (/api/v3/display/current, the web UI's live preview) is
only written from update_display(), at most once per write interval. A screen
that draws its card once and then holds it -- soccer's recent/upcoming cards
skip redundant redraws -- pushes exactly one frame. When that push lands inside
the interval, e.g. a few milliseconds after the on-demand start's clear wrote a
black frame, the throttle skips it and nothing ever writes it: on ledpi the
preview stayed black for soccer's whole 15 s screen while the panel showed the
card, and the next screen "rendered immediately".

Runs the real DisplayManager on the emulator, like test_display_dirty_tracking.
"""

import os
import sys
import types

os.environ["EMULATOR"] = "true"

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="module")
def dm(tmp_path_factory):
    from src.display_manager import DisplayManager
    DisplayManager._instance = None
    manager = DisplayManager({
        "display": {
            "hardware": {"rows": 32, "cols": 64, "chain_length": 2,
                         "parallel": 1, "brightness": 90},
            "runtime": {"gpio_slowdown": 0},
        },
    }, suppress_test_pattern=True)
    manager._snapshot_path = str(
        tmp_path_factory.mktemp("owed_snapshot") / "led_matrix_preview.png")
    yield manager
    DisplayManager._instance = None


@pytest.fixture
def viewer(dm, monkeypatch, tmp_path):
    """A preview is open (1 s write interval); fresh snapshot bookkeeping."""
    monkeypatch.setattr(dm, "_viewer_is_fresh", lambda now: True)
    dm._viewer_was_fresh = True
    dm._snapshot_path = str(tmp_path / "snap.png")
    dm._last_snapshot_ts = 0.0
    dm._last_snapshot_touch_ts = 0.0
    dm._last_snapshot_digest = None
    dm._saved_snapshot_digest = None
    dm._snapshot_owed = False
    dm.set_scrolling_state(False)
    return dm


def _lit(path):
    with Image.open(path) as img:
        return sum(1 for p in img.convert("RGB").getdata() if max(p) > 20)


def _age_last_write(dm, seconds=2.0):
    """As if `seconds` had passed since the last snapshot write."""
    dm._last_snapshot_ts -= seconds
    dm._last_snapshot_touch_ts -= seconds


def _clear_then_draw_card(dm):
    """The on-demand start's clear, then the card a few ms later."""
    dm.clear()
    dm.update_display()                       # black frame: written
    assert _lit(dm._snapshot_path) == 0
    dm.draw.rectangle([4, 4, 40, 20], fill=(255, 255, 0))
    dm.update_display()                       # the card: inside the interval


def _controller(dm):
    from src import display_controller as dc_module
    controller = dc_module.DisplayController.__new__(dc_module.DisplayController)
    controller.plugin_manager = None
    controller.display_manager = dm
    return controller


class _HoldingPlugin:
    """Already showing its card: display() returns True and draws nothing."""

    plugin_id = "holding"

    def __init__(self):
        self.calls = 0

    def display(self, display_mode=None, force_clear=False):
        self.calls += 1
        return True


def test_a_held_card_reaches_the_preview_on_the_next_frame(viewer):
    dm = viewer
    _clear_then_draw_card(dm)
    assert _lit(dm._snapshot_path) == 0       # the throttle skipped the card

    controller = _controller(dm)
    plugin = _HoldingPlugin()
    _age_last_write(dm)
    # The render loop's next frame: the plugin draws nothing and makes no
    # update_display() call, as soccer's switch cards do.
    assert controller._display_once(plugin, "soccer_eng.1_recent", True) is True
    assert plugin.calls == 1
    assert _lit(dm._snapshot_path) > 0


def test_the_owed_write_still_waits_out_the_interval(viewer, monkeypatch):
    dm = viewer
    _clear_then_draw_card(dm)
    saves = []
    monkeypatch.setattr(dm, "_save_snapshot", lambda image: saves.append(image))
    dm.write_owed_snapshot()                  # still inside the interval
    assert saves == []
    _age_last_write(dm)
    dm.write_owed_snapshot()
    assert len(saves) == 1
    # Written: nothing is owed, so later frames do no work and the unchanged
    # frame is not encoded again.
    assert dm._snapshot_owed is False
    _age_last_write(dm)
    dm.write_owed_snapshot()
    assert len(saves) == 1


def test_a_failed_owed_write_stays_owed_and_is_retried(viewer, monkeypatch):
    dm = viewer
    _clear_then_draw_card(dm)
    _age_last_write(dm)
    attempts = []

    def failing_save(image):
        attempts.append(image)
        raise OSError("disk full")

    monkeypatch.setattr(dm, "_save_snapshot", failing_save)
    dm.write_owed_snapshot()                  # the write fails
    assert len(attempts) == 1
    assert dm._snapshot_owed is True          # still owed: a held screen
    saves = []                                # makes no update_display()
    monkeypatch.setattr(dm, "_save_snapshot", lambda image: saves.append(image))
    dm.write_owed_snapshot()                  # retried on the next frame
    assert len(saves) == 1
    assert dm._snapshot_owed is False


def test_nothing_owed_after_a_frame_that_was_written(viewer, monkeypatch):
    dm = viewer
    dm.draw.rectangle([0, 0, 8, 8], fill=(0, 255, 0))
    dm.update_display()                       # due: written at once
    assert dm._snapshot_owed is False
    calls = []
    monkeypatch.setattr(dm, "_write_snapshot_if_due",
                        lambda *a, **k: calls.append(a))
    dm.write_owed_snapshot()
    assert calls == []


def test_an_unchanged_frame_inside_the_interval_is_not_owed(viewer):
    dm = viewer
    dm.draw.rectangle([0, 0, 8, 8], fill=(0, 0, 255))
    dm.update_display()
    dm.update_display()                       # same frame, inside the interval
    assert dm._snapshot_owed is False


def test_a_controller_without_the_hook_still_draws():
    """Controllers built without a display manager (tests) are unaffected."""
    from src import display_controller as dc_module
    controller = dc_module.DisplayController.__new__(dc_module.DisplayController)
    controller.plugin_manager = None
    plugin = _HoldingPlugin()
    assert controller._display_once(plugin, "x", True) is True
    controller.display_manager = types.SimpleNamespace()
    assert controller._display_once(plugin, "x", True) is True
    assert plugin.calls == 2
