"""
Unit tests for the plugin safety harness primitives:
bounds detection, image comparison, and mode enumeration.

These don't load real plugins, so they run anywhere (including core CI where
plugin-repos is empty).
"""

import pytest
from PIL import Image

from src.plugin_system.testing.bounds_display_manager import BoundsCheckingDisplayManager
from src.plugin_system.testing.harness import (
    _TOLERATED_UPDATE_ERRORS, compare_images, list_modes,
)
from src.plugin_system.testing.sizes import (
    DEFAULT_TEST_SIZES, coerce_sizes, parse_size_token, resolve_test_sizes,
)


class TestBoundsDetection:
    def test_reports_declared_size_not_canvas_size(self):
        dm = BoundsCheckingDisplayManager(width=64, height=32)
        assert dm.width == 64 and dm.height == 32
        assert dm.matrix.width == 64 and dm.matrix.height == 32
        # Backing canvas is padded out past the declared panel so far-overshoot
        # coordinates land on-canvas and get flagged instead of clipped.
        canvas_w, canvas_h = dm.image.size
        assert canvas_w > 64 and canvas_h > 32

    def test_far_overshoot_on_small_panel_is_detected(self):
        # A coordinate meant for a wide build (x past 64) must still be caught
        # when the declared panel is only 64 wide.
        dm = BoundsCheckingDisplayManager(width=64, height=32)
        dm.draw.rectangle([200, 5, 210, 10], fill=(255, 0, 0))
        bbox = dm.check_overflow()
        assert bbox is not None
        assert bbox[0] >= 64

    def test_in_bounds_drawing_has_no_overflow(self):
        dm = BoundsCheckingDisplayManager(width=64, height=32)
        dm.draw.rectangle([0, 0, 63, 31], fill=(255, 255, 255))
        assert dm.check_overflow() is None

    def test_right_overflow_is_detected(self):
        dm = BoundsCheckingDisplayManager(width=64, height=32)
        # Draw a few pixels past the right edge.
        dm.draw.rectangle([60, 5, 70, 10], fill=(255, 0, 0))
        bbox = dm.check_overflow()
        assert bbox is not None
        assert bbox[0] >= 64  # overflow starts at or past the declared width

    def test_bottom_overflow_is_detected(self):
        dm = BoundsCheckingDisplayManager(width=64, height=32)
        dm.draw.rectangle([5, 30, 10, 40], fill=(0, 255, 0))
        bbox = dm.check_overflow()
        assert bbox is not None
        assert bbox[3] > 32  # overflow extends past the declared height

    def test_declared_image_is_cropped_to_panel(self):
        dm = BoundsCheckingDisplayManager(width=64, height=32)
        assert dm.get_image().size == (64, 32)

    def test_snapshot_saves_cropped_panel(self, tmp_path):
        dm = BoundsCheckingDisplayManager(width=128, height=32)
        out = tmp_path / "snap.png"
        dm.save_snapshot(str(out))
        with Image.open(out) as img:
            assert img.size == (128, 32)


class TestArbitraryPanelSizes:
    """The harness must handle any panel shape, not a fixed supported list."""

    def test_overflow_extent_pads_to_largest_in_run(self):
        # A wide run (extent 256) means content at x=200 on a 64-wide panel is
        # caught; the same draw with a small extent would be clipped (false pass).
        wide = BoundsCheckingDisplayManager(width=64, height=32, overflow_extent=(256, 32))
        wide.draw.rectangle([200, 5, 210, 10], fill=(255, 0, 0))
        assert wide.check_overflow() is not None

        tight = BoundsCheckingDisplayManager(width=64, height=32, overflow_extent=(64, 32))
        tight.draw.rectangle([200, 5, 210, 10], fill=(255, 0, 0))
        assert tight.check_overflow() is None  # clipped beyond the small canvas

    def test_unusual_shapes_report_their_declared_size(self):
        for w, h in [(8, 2), (6, 6), (200, 8), (64, 96)]:
            dm = BoundsCheckingDisplayManager(width=w, height=h)
            assert dm.width == w and dm.height == h
            assert dm.matrix.width == w and dm.matrix.height == h


class TestUpdateErrorClassification:
    """update() may fail for lack of network (tolerated) but a logic bug must
    not pass green just because display() survives."""

    def test_connectivity_errors_are_tolerated(self):
        import socket
        import urllib.error
        for exc in (ConnectionError("x"), TimeoutError("x"), socket.gaierror("x"),
                    urllib.error.URLError("x")):
            assert isinstance(exc, _TOLERATED_UPDATE_ERRORS)

    def test_logic_errors_are_not_tolerated(self):
        for exc in (ValueError("x"), KeyError("x"), AttributeError("x"), TypeError("x")):
            assert not isinstance(exc, _TOLERATED_UPDATE_ERRORS)


class TestSizeParsing:
    def test_parse_size_token_ok(self):
        assert parse_size_token(" 128X32 ") == (128, 32)

    def test_parse_size_token_rejects_garbage(self):
        with pytest.raises(ValueError):
            parse_size_token("128xabc")
        with pytest.raises(ValueError):
            parse_size_token("128-32")

    def test_coerce_sizes_from_string_and_pairs(self):
        assert coerce_sizes("8x16,64x64") == [(8, 16), (64, 64)]
        assert coerce_sizes([[8, 16], (64, 64)]) == [(8, 16), (64, 64)]
        assert coerce_sizes(None) is None
        assert coerce_sizes("") is None

    def test_resolve_precedence_env_then_spec_then_default(self, monkeypatch):
        monkeypatch.delenv("LEDMATRIX_TEST_SIZES", raising=False)
        assert resolve_test_sizes(None) == list(DEFAULT_TEST_SIZES)
        assert resolve_test_sizes([[8, 16]]) == [(8, 16)]
        monkeypatch.setenv("LEDMATRIX_TEST_SIZES", "5x5")
        # env wins over a per-plugin spec
        assert resolve_test_sizes([[8, 16]]) == [(5, 5)]


class TestCompareImages:
    def test_identical_images_match(self):
        a = Image.new("RGB", (16, 16), (10, 20, 30))
        b = a.copy()
        ok, diff_pixels, max_delta = compare_images(a, b)
        assert ok and diff_pixels == 0 and max_delta == 0

    def test_different_images_fail_at_zero_tolerance(self):
        a = Image.new("RGB", (16, 16), (0, 0, 0))
        b = a.copy()
        b.putpixel((1, 1), (255, 255, 255))
        ok, diff_pixels, max_delta = compare_images(a, b)
        assert not ok and diff_pixels == 1 and max_delta == 255

    def test_tolerance_absorbs_small_noise(self):
        a = Image.new("RGB", (16, 16), (100, 100, 100))
        b = a.copy()
        b.putpixel((2, 2), (103, 100, 100))  # delta 3
        ok, _, max_delta = compare_images(a, b, max_delta=5, max_diff_pixels=0)
        assert ok and max_delta == 3

    def test_size_mismatch_fails(self):
        a = Image.new("RGB", (16, 16))
        b = Image.new("RGB", (32, 16))
        ok, _, _ = compare_images(a, b)
        assert not ok


class TestListModes:
    def test_instance_modes_take_precedence(self):
        inst = type("P", (), {"modes": ["a", "b"]})()
        assert list_modes(inst, {"display_modes": ["x"]}, "pid") == ["a", "b"]

    def test_falls_back_to_manifest_display_modes(self):
        inst = type("P", (), {})()
        assert list_modes(inst, {"display_modes": ["x", "y"]}, "pid") == ["x", "y"]

    def test_falls_back_to_plugin_id(self):
        inst = type("P", (), {})()
        assert list_modes(inst, {}, "pid") == ["pid"]
