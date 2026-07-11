"""Tests for the harness fill / scale-up check (src/plugin_system/testing/harness.py)."""

from PIL import Image

from src.plugin_system.testing.harness import (
    RenderResult,
    check_scale_up,
    fill_metrics,
)


def _canvas(w, h):
    return Image.new("RGB", (w, h), (0, 0, 0))


def _with_block(w, h, bx, by, bw, bh, color=(255, 255, 255)):
    img = _canvas(w, h)
    img.paste(Image.new("RGB", (bw, bh), color), (bx, by))
    return img


def _result(w, h, image):
    return RenderResult("p", w, h, "mode", image=image)


class TestFillMetrics:
    def test_full_white(self):
        ex, ey, ink = fill_metrics(Image.new("RGB", (64, 32), (255, 255, 255)))
        assert (ex, ey, ink) == (1.0, 1.0, 1.0)

    def test_black_is_empty(self):
        assert fill_metrics(_canvas(64, 32)) == (0.0, 0.0, 0.0)

    def test_corner_dot(self):
        ex, ey, ink = fill_metrics(_with_block(100, 100, 0, 0, 10, 10))
        assert ex == 0.1 and ey == 0.1
        assert ink == 0.01

    def test_centered_half(self):
        ex, ey, _ = fill_metrics(_with_block(100, 100, 25, 25, 50, 50))
        assert ex == 0.5 and ey == 0.5

    def test_dim_pixels_ignored(self):
        img = _canvas(10, 10)
        img.putpixel((5, 5), (10, 10, 10))  # below the lit threshold
        assert fill_metrics(img) == (0.0, 0.0, 0.0)


class TestCheckScaleUp:
    def test_not_checked_below_2x(self):
        # 128x64 vs design 128x32: only height is 2x -> checked on y only;
        # 128x32 itself: not checked at all
        r = _result(128, 32, _with_block(128, 32, 0, 0, 10, 10))
        check_scale_up([r], design_size=(128, 32))
        assert not r.fill_checked

    def test_warn_mode_records_but_passes(self):
        # tiny corner content on a 256x128 (2x both axes)
        r = _result(256, 128, _with_block(256, 128, 0, 0, 20, 20))
        check_scale_up([r], design_size=(128, 32), strict=False)
        assert r.fill_checked
        assert r.fill_ok is None       # warn-only: not a failure
        assert r.ok                    # still passes
        assert r.fill_extent[0] < 0.5

    def test_strict_mode_fails_underfill(self):
        r = _result(256, 128, _with_block(256, 128, 0, 0, 20, 20))
        check_scale_up([r], design_size=(128, 32), strict=True)
        assert r.fill_ok is False
        assert not r.ok

    def test_well_filled_passes_strict(self):
        r = _result(256, 128, _with_block(256, 128, 10, 10, 200, 100))
        check_scale_up([r], design_size=(128, 32), strict=True)
        assert r.fill_ok is True and r.ok

    def test_axis_selection_wide_only(self):
        # 256x32 vs design 128x32: width is 2x, height is not -> only the
        # x-extent matters; content spanning full width but few rows passes
        r = _result(256, 32, _with_block(256, 32, 0, 12, 250, 8))
        check_scale_up([r], design_size=(128, 32), strict=True)
        assert r.fill_ok is True

    def test_axis_selection_wide_only_underfill(self):
        r = _result(256, 32, _with_block(256, 32, 0, 12, 60, 8))
        check_scale_up([r], design_size=(128, 32), strict=True)
        assert r.fill_ok is False

    def test_errored_render_skipped(self):
        r = RenderResult("p", 256, 128, "m", error="boom")
        check_scale_up([r], design_size=(128, 32), strict=True)
        assert not r.fill_checked

    def test_custom_design_size(self):
        # 128x64 with design 64x32 IS 2x both axes
        r = _result(128, 64, _with_block(128, 64, 0, 0, 10, 10))
        check_scale_up([r], design_size=(64, 32), strict=False)
        assert r.fill_checked
