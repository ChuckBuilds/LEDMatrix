"""
Unit tests for the plugin safety harness primitives:
bounds detection, image comparison, and mode enumeration.

These don't load real plugins, so they run anywhere (including core CI where
plugin-repos is empty).
"""

from PIL import Image

from src.plugin_system.testing.bounds_display_manager import BoundsCheckingDisplayManager
from src.plugin_system.testing.harness import compare_images, list_modes


class TestBoundsDetection:
    def test_reports_declared_size_not_canvas_size(self):
        dm = BoundsCheckingDisplayManager(width=64, height=32)
        assert dm.width == 64 and dm.height == 32
        assert dm.matrix.width == 64 and dm.matrix.height == 32
        # backing canvas is padded
        assert dm.image.size == (64 + dm.MARGIN, 32 + dm.MARGIN)

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
