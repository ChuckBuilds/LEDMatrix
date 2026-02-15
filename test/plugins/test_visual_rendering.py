"""
Tests for VisualTestDisplayManager.

Verifies that the visual display manager actually renders pixels,
loads fonts, and can save snapshots.
"""

import pytest
from PIL import Image

from src.plugin_system.testing import VisualTestDisplayManager


class TestVisualDisplayManager:
    """Test VisualTestDisplayManager pixel rendering."""

    def test_creates_image_with_correct_dimensions(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        assert vdm.image.size == (128, 32)

    def test_creates_image_custom_dimensions(self):
        vdm = VisualTestDisplayManager(width=64, height=64)
        assert vdm.image.size == (64, 64)
        assert vdm.width == 64
        assert vdm.height == 64

    def test_draw_text_renders_pixels(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_text("Hello", x=0, y=0, color=(255, 255, 255))
        pixels = list(vdm.image.getdata())
        non_black = [p for p in pixels if p != (0, 0, 0)]
        assert len(non_black) > 0, "draw_text should render actual pixels"

    def test_draw_text_centered(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_text("Test", color=(255, 0, 0))  # x=None centers text
        pixels = list(vdm.image.getdata())
        non_black = [p for p in pixels if p != (0, 0, 0)]
        assert len(non_black) > 0

    def test_draw_text_with_centered_flag(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_text("X", x=64, y=10, centered=True, color=(0, 255, 0))
        pixels = list(vdm.image.getdata())
        non_black = [p for p in pixels if p != (0, 0, 0)]
        assert len(non_black) > 0

    def test_draw_text_tracks_calls(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_text("Hello", x=10, y=5, color=(255, 0, 0))
        assert len(vdm.draw_calls) == 1
        assert vdm.draw_calls[0]['type'] == 'text'
        assert vdm.draw_calls[0]['text'] == 'Hello'
        assert vdm.draw_calls[0]['x'] == 10
        assert vdm.draw_calls[0]['y'] == 5

    def test_clear_resets_canvas(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_text("Hello", x=0, y=0, color=(255, 255, 255))
        vdm.clear()
        pixels = list(vdm.image.getdata())
        non_black = [p for p in pixels if p != (0, 0, 0)]
        assert len(non_black) == 0, "clear() should reset all pixels to black"
        assert vdm.clear_called is True

    def test_update_display_sets_flag(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        assert vdm.update_called is False
        vdm.update_display()
        assert vdm.update_called is True

    def test_matrix_proxy(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        assert vdm.matrix.width == 128
        assert vdm.matrix.height == 32

    def test_width_height_properties(self):
        vdm = VisualTestDisplayManager(width=64, height=32)
        assert vdm.width == 64
        assert vdm.height == 32
        assert vdm.display_width == 64
        assert vdm.display_height == 32

    def test_save_snapshot(self, tmp_path):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_text("Test", x=10, y=10, color=(255, 0, 0))
        output = tmp_path / "test_render.png"
        vdm.save_snapshot(str(output))
        assert output.exists()
        saved_img = Image.open(str(output))
        assert saved_img.size == (128, 32)

    def test_get_image(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        img = vdm.get_image()
        assert isinstance(img, Image.Image)
        assert img.size == (128, 32)

    def test_get_image_base64(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_text("Hi", x=0, y=0, color=(255, 255, 255))
        b64 = vdm.get_image_base64()
        assert isinstance(b64, str)
        assert len(b64) > 0
        # Should be valid base64 PNG
        import base64
        decoded = base64.b64decode(b64)
        assert decoded[:4] == b'\x89PNG'

    def test_font_attributes_exist(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        assert hasattr(vdm, 'regular_font')
        assert hasattr(vdm, 'small_font')
        assert hasattr(vdm, 'extra_small_font')
        assert hasattr(vdm, 'calendar_font')
        assert hasattr(vdm, 'bdf_5x7_font')
        assert hasattr(vdm, 'font')

    def test_get_text_width(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        w = vdm.get_text_width("Hello", vdm.regular_font)
        assert isinstance(w, int)
        assert w > 0

    def test_get_font_height(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        h = vdm.get_font_height(vdm.regular_font)
        assert isinstance(h, int)
        assert h > 0

    def test_image_paste(self):
        """Verify plugins can paste images onto the display."""
        vdm = VisualTestDisplayManager(width=128, height=32)
        overlay = Image.new('RGB', (10, 10), (255, 0, 0))
        vdm.image.paste(overlay, (0, 0))
        pixel = vdm.image.getpixel((5, 5))
        assert pixel == (255, 0, 0)

    def test_image_assignment(self):
        """Verify plugins can assign a new image to display_manager.image."""
        vdm = VisualTestDisplayManager(width=128, height=32)
        new_img = Image.new('RGB', (128, 32), (0, 255, 0))
        vdm.image = new_img
        assert vdm.image.getpixel((0, 0)) == (0, 255, 0)

    def test_draw_image(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        overlay = Image.new('RGB', (10, 10), (0, 0, 255))
        vdm.draw_image(overlay, 5, 5)
        assert len(vdm.draw_calls) == 1
        assert vdm.draw_calls[0]['type'] == 'image'
        # Verify pixels were actually pasted
        pixel = vdm.image.getpixel((7, 7))
        assert pixel == (0, 0, 255)

    def test_reset(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_text("Hi", x=0, y=0)
        vdm.clear()
        vdm.update_display()
        vdm.reset()
        assert vdm.clear_called is False
        assert vdm.update_called is False
        assert len(vdm.draw_calls) == 0
        pixels = list(vdm.image.getdata())
        assert all(p == (0, 0, 0) for p in pixels)

    def test_scrolling_state(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        assert vdm.is_currently_scrolling() is False
        vdm.set_scrolling_state(True)
        assert vdm.is_currently_scrolling() is True
        vdm.set_scrolling_state(False)
        assert vdm.is_currently_scrolling() is False

    def test_format_date_with_ordinal(self):
        from datetime import datetime
        vdm = VisualTestDisplayManager(width=128, height=32)
        dt = datetime(2025, 8, 1)
        result = vdm.format_date_with_ordinal(dt)
        assert '1st' in result
        dt = datetime(2025, 8, 3)
        result = vdm.format_date_with_ordinal(dt)
        assert '3rd' in result
        dt = datetime(2025, 8, 11)
        result = vdm.format_date_with_ordinal(dt)
        assert '11th' in result


class TestWeatherDrawing:
    """Test weather icon rendering."""

    def test_draw_sun(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_sun(0, 0, 16)
        pixels = list(vdm.image.getdata())
        non_black = [p for p in pixels if p != (0, 0, 0)]
        assert len(non_black) > 0

    def test_draw_cloud(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_cloud(0, 0, 16)
        pixels = list(vdm.image.getdata())
        non_black = [p for p in pixels if p != (0, 0, 0)]
        assert len(non_black) > 0

    def test_draw_rain(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_rain(0, 0, 16)
        pixels = list(vdm.image.getdata())
        non_black = [p for p in pixels if p != (0, 0, 0)]
        assert len(non_black) > 0

    def test_draw_snow(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        vdm.draw_snow(0, 0, 16)
        pixels = list(vdm.image.getdata())
        non_black = [p for p in pixels if p != (0, 0, 0)]
        assert len(non_black) > 0

    def test_draw_weather_icon_dispatches(self):
        vdm = VisualTestDisplayManager(width=128, height=32)
        for condition in ['clear', 'cloudy', 'rain', 'snow', 'storm', 'unknown']:
            vdm.clear()
            vdm.draw_weather_icon(condition, 0, 0, 16)
            pixels = list(vdm.image.getdata())
            non_black = [p for p in pixels if p != (0, 0, 0)]
            assert len(non_black) > 0, f"draw_weather_icon('{condition}') should render pixels"
