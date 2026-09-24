"""Vegas scrolls in whole pixels locked to the panel refresh.

It used to advance by elapsed time, blend neighbouring columns, and pace itself
with a sleep to target_fps. On a 512x64 chain refreshing at 95Hz that ran at
73-89fps with p99 frames of 20-28ms: the sleep drifted against the refresh and
missed a vsync every few frames, and the blend shimmered on the panel.
"""
import sys
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.scroll_config import solve_crisp  # noqa: E402
from src.vegas_mode import render_pipeline as rp_module  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.render_pipeline import RenderPipeline  # noqa: E402

W, H = 128, 32


class FakeStream:
    def get_grouped_content_for_composition(self):
        return [('a', [Image.new('RGB', (4000, H), (255, 255, 255))])]

    def get_active_plugin_ids(self):
        return ['a']


class FakeDM:
    width = W
    height = H

    def __init__(self, refresh_hz=100.0, hardware=True):
        self.refresh_hz = refresh_hz
        self.matrix = object() if hardware else None
        self.image = Image.new('RGB', (W, H))
        self.holds = []

    def set_scrolling_state(self, is_scrolling, frame_hold=1):
        self.holds.append(frame_hold)

    def update_display(self):
        pass


def _pipeline(dm=None, **cfg):
    p = RenderPipeline(VegasModeConfig(lead_in_width=0, **cfg), dm or FakeDM(),
                       FakeStream())
    assert p.compose_scroll_content()
    return p


def test_default_steps_whole_pixels_and_holds_frames():
    dm = FakeDM(refresh_hz=100.0)
    p = _pipeline(dm, scroll_speed=50)
    want = solve_crisp(50, 100.0)
    assert p.scroll_helper.fixed_pixels_per_frame == want.pixels_per_frame
    assert not p.scroll_helper.sub_pixel_scrolling

    before = p.scroll_helper.scroll_position
    p.render_frame()
    assert p.scroll_helper.scroll_position - before == want.pixels_per_frame
    # The hold is what makes 1px every 2 refreshes 50px/s rather than 100.
    assert dm.holds[-1] == want.frame_hold == 2
    assert p.target_fps == want.frames_per_second


def test_sleep_floor_stays_below_the_refresh_period():
    # A floor at or above the real period accumulates until a frame misses.
    p = _pipeline(FakeDM(refresh_hz=100.0), scroll_speed=50)
    assert p.frame_interval < p._frame_hold / 100.0


def test_sub_pixel_blend_keeps_the_old_time_based_blend():
    dm = FakeDM()
    p = _pipeline(dm, sub_pixel_blend=True, target_fps=90)
    assert p.scroll_helper.fixed_pixels_per_frame is None
    assert p.scroll_helper.sub_pixel_scrolling
    p.render_frame()
    assert dm.holds[-1] == 1
    assert p.frame_interval == 1.0 / 90
    assert p.target_fps == 90


def _run_swaps(p, period, frames):
    """Render `frames` frames whose swaps are `period` seconds apart."""
    clock = [1000.0]

    def monotonic():
        return clock[0]

    with patch.object(rp_module.time, 'monotonic', monotonic):
        for _ in range(frames):
            p.render_frame()
            clock[0] += period


def test_a_panel_below_its_cap_is_measured_and_the_speed_re_solved():
    # 4x128x64 on one chain: capped at 120Hz, really 95Hz. Against the cap
    # 90px/s solves to 3px every 4 refreshes; against 95Hz, 1px every one.
    p = _pipeline(FakeDM(refresh_hz=120.0), scroll_speed=90)
    assert p._crisp.pixels_per_frame == 3

    frames = RenderPipeline.REFRESH_WARMUP_FRAMES + RenderPipeline.REFRESH_SAMPLES + 2
    _run_swaps(p, p._frame_hold / 95.0, frames)

    assert p._measured_hz == 95.0
    assert (p._crisp.pixels_per_frame, p._crisp.frame_hold) == (1, 1)
    # The floor still comes from the cap, not the measurement.
    assert p.frame_interval < 1 / 95.0


def test_a_panel_that_keeps_up_with_its_cap_is_left_alone():
    p = _pipeline(FakeDM(refresh_hz=100.0), scroll_speed=50)
    crisp = p._crisp
    frames = RenderPipeline.REFRESH_WARMUP_FRAMES + RenderPipeline.REFRESH_SAMPLES + 2
    _run_swaps(p, p._frame_hold / 99.5, frames)
    assert p._measured_hz == 100.0
    assert p._crisp == crisp


def test_no_measurement_without_hardware():
    # Nothing blocks in the emulator, so swap gaps say nothing about a panel.
    p = _pipeline(FakeDM(refresh_hz=120.0, hardware=False), scroll_speed=90)
    frames = RenderPipeline.REFRESH_WARMUP_FRAMES + RenderPipeline.REFRESH_SAMPLES + 2
    _run_swaps(p, 1 / 50.0, frames)
    assert p._measured_hz is None
