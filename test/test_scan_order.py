"""Scan-order compensation (src/scan_order.py) and its use in update_display.

Confirmed on hdpi's panel before any of this was written: rotated 180, one
chain, 64 rows. Showing the upper half a refresh behind removed the 1px step
across the middle of the panel that a whole-pixel-per-refresh scroll showed;
see the module docstring for why.
"""

import os
import sys

os.environ.setdefault("EMULATOR", "true")

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import scan_order  # noqa: E402


def hw(**overrides):
    base = {"rows": 64, "cols": 128, "chain_length": 4, "parallel": 1,
            "multiplexing": 0, "scan_mode": 0, "pixel_mapper_config": "",
            "orientation": "normal"}
    base.update(overrides)
    return base


class TestWhichRowsLag:
    def test_hdpi_rotated_180_lags_the_upper_half(self):
        # The configuration the panel test confirmed.
        assert scan_order.scan_lag_bands(hw(orientation="180"), 64) == [(0, 32, 1)]

    def test_unrotated_lags_the_lower_half(self):
        assert scan_order.scan_lag_bands(hw(), 64) == [(32, 64, 1)]

    def test_a_32_row_panel_splits_at_16(self):
        assert scan_order.scan_lag_bands(hw(rows=32), 32) == [(16, 32, 1)]

    def test_a_48_row_panel_splits_at_24(self):
        assert scan_order.scan_lag_bands(hw(rows=48), 48) == [(24, 48, 1)]

    def test_stacked_parallel_chains_lag_one_more_per_half(self):
        # Chains are lit together, so every half boundary down the stack is
        # another start-after-end jump: a continuous lean, never a step.
        assert scan_order.scan_lag_bands(hw(parallel=2), 128) == [
            (32, 64, 1), (64, 96, 2), (96, 128, 3)]

    def test_stacked_and_rotated_mirrors_it(self):
        assert scan_order.scan_lag_bands(hw(parallel=2, orientation="180"), 128) == [
            (0, 32, 3), (32, 64, 2), (64, 96, 1)]

    @pytest.mark.parametrize("overrides", [
        {"pixel_mapper_config": "U-mapper"},
        {"orientation": "90"},
        {"multiplexing": 3},
        {"scan_mode": 1},
        {"rows": 7},
    ])
    def test_layouts_it_cannot_reason_about_are_left_alone(self, overrides):
        assert scan_order.scan_lag_bands(hw(**overrides), 64) is None

    def test_a_canvas_of_another_height_is_left_alone(self):
        # Something remapped it (double-sided, a mapper): row order unknown.
        assert scan_order.scan_lag_bands(hw(), 32) is None

    def test_it_can_be_switched_off(self):
        assert scan_order.scan_lag_bands(hw(), 64, setting="off") is None


def _frame(shade):
    return Image.new("RGB", (8, 64), (shade, shade, shade))


class TestCompose:
    def test_a_band_comes_from_the_frame_that_many_refreshes_back(self):
        now, previous = _frame(30), _frame(20)
        out = scan_order.compose(now, [previous], [(32, 64, 1)])
        assert out.getpixel((0, 0)) == (30, 30, 30)       # current half
        assert out.getpixel((0, 63)) == (20, 20, 20)      # lagging half
        assert now.getpixel((0, 63)) == (30, 30, 30)      # input untouched

    def test_deeper_bands_use_older_frames(self):
        out = scan_order.compose(_frame(40), [_frame(30), _frame(20)],
                                 [(10, 20, 1), (20, 30, 2)])
        assert [out.getpixel((0, y))[0] for y in (5, 15, 25)] == [40, 30, 20]

    def test_without_enough_history_the_frame_goes_out_as_is(self):
        now = _frame(30)
        assert scan_order.compose(now, [], [(32, 64, 1)]) is now


class TestUpdateDisplay:
    """The wiring: when the display manager applies it, and when it doesn't."""

    @pytest.fixture
    def dm(self):
        from src.display_manager import DisplayManager
        DisplayManager._instance = None
        DisplayManager._initialized = False
        manager = DisplayManager({"display": {
            "hardware": {"rows": 32, "cols": 64, "chain_length": 1, "parallel": 1},
            "runtime": {"gpio_slowdown": 0}}}, suppress_test_pattern=True)
        presented = []

        # update_display alternates between two canvases; watch both.
        for canvas in (manager.offscreen_canvas, manager.current_canvas):
            def capture(image, *args, _real=canvas.SetImage, **kwargs):
                presented.append(image.copy())
                return _real(image, *args, **kwargs)
            canvas.SetImage = capture
        manager._presented = presented
        yield manager
        manager.set_scrolling_state(False)
        DisplayManager._instance = None
        DisplayManager._initialized = False

    def _push(self, dm, shade):
        dm.draw.rectangle([0, 0, dm.width - 1, dm.height - 1], fill=(shade, 0, 0))
        dm.update_display()
        return dm._presented[-1]

    def test_off_in_the_emulator(self, dm):
        # No scan order there: lagging a half would add the step it removes.
        assert dm._scan_lag_bands is None

    def test_lags_mid_scroll_at_one_frame_per_refresh(self, dm):
        dm._scan_lag_bands = [(16, 32, 1)]
        dm.set_scrolling_state(True, 1)
        self._push(dm, 10)
        shown = self._push(dm, 20)
        assert shown.getpixel((0, 0)) == (20, 0, 0)
        assert shown.getpixel((0, 31)) == (10, 0, 0)

    def test_not_on_a_static_screen_or_a_held_frame(self, dm):
        dm._scan_lag_bands = [(16, 32, 1)]
        self._push(dm, 10)
        assert self._push(dm, 20).getpixel((0, 31)) == (20, 0, 0)   # not scrolling
        dm.set_scrolling_state(True, 2)
        self._push(dm, 30)
        assert self._push(dm, 40).getpixel((0, 31)) == (40, 0, 0)   # hold 2
