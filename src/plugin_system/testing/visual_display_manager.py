"""
Visual Test Display Manager for LEDMatrix.

A display manager that performs real pixel rendering using PIL,
without requiring hardware or the RGBMatrixEmulator. Used for:
- Local dev preview server
- CLI render script (AI visual feedback)
- Visual assertions in pytest

Unlike MockDisplayManager (which logs calls but doesn't render) or
MagicMock (which tracks nothing visual), this class creates a real
PIL Image canvas and draws text using the actual project fonts.
"""

import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


class _MatrixProxy:
    """Lightweight proxy so plugins can access display_manager.matrix.width/height."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height


class VisualTestDisplayManager:
    """
    Display manager that renders real pixels for testing and development.

    Implements the same interface that plugins expect from DisplayManager,
    but operates entirely in-memory with PIL — no hardware, no singleton,
    no emulator dependency.
    """

    # Weather icon color constants (same as DisplayManager)
    WEATHER_COLORS = {
        'sun': (255, 200, 0),
        'cloud': (200, 200, 200),
        'rain': (0, 100, 255),
        'snow': (220, 220, 255),
        'storm': (255, 255, 0),
    }

    def __init__(self, width: int = 128, height: int = 32):
        self._width = width
        self._height = height

        # Canvas
        self.image = Image.new('RGB', (width, height), (0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)

        # Matrix proxy (plugins access display_manager.matrix.width/height)
        self.matrix = _MatrixProxy(width, height)

        # Scrolling state (interface compat, no-op)
        self._scrolling_state = {
            'is_scrolling': False,
            'last_scroll_activity': 0,
            'scroll_inactivity_threshold': 2.0,
            'deferred_updates': [],
            'max_deferred_updates': 50,
            'deferred_update_ttl': 300.0,
        }

        # Call tracking (preserves MockDisplayManager capabilities)
        self.clear_called = False
        self.update_called = False
        self.draw_calls = []

        # Load fonts
        self._load_fonts()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height

    @property
    def display_width(self) -> int:
        return self.image.width

    @property
    def display_height(self) -> int:
        return self.image.height

    # ------------------------------------------------------------------
    # Font loading
    # ------------------------------------------------------------------

    def _find_project_root(self) -> Optional[Path]:
        """Walk up from this file to find the project root (contains assets/fonts)."""
        current = Path(__file__).resolve().parent
        for _ in range(10):
            if (current / 'assets' / 'fonts').exists():
                return current
            current = current.parent
        return None

    def _load_fonts(self):
        """Load fonts with graceful fallback, matching DisplayManager._load_fonts()."""
        project_root = self._find_project_root()

        try:
            if project_root is None:
                raise FileNotFoundError("Could not find project root with assets/fonts")

            fonts_dir = project_root / 'assets' / 'fonts'

            # Press Start 2P — regular and small (both 8px)
            ttf_path = str(fonts_dir / 'PressStart2P-Regular.ttf')
            self.regular_font = ImageFont.truetype(ttf_path, 8)
            self.small_font = ImageFont.truetype(ttf_path, 8)
            self.font = self.regular_font  # alias used by some code paths

            # 5x7 BDF font via freetype
            try:
                import freetype
                bdf_path = str(fonts_dir / '5x7.bdf')
                if not os.path.exists(bdf_path):
                    raise FileNotFoundError(f"BDF font not found: {bdf_path}")
                face = freetype.Face(bdf_path)
                self.calendar_font = face
                self.bdf_5x7_font = face
            except Exception:
                logger.debug("BDF font not available, using small_font as fallback")
                self.calendar_font = self.small_font
                self.bdf_5x7_font = self.small_font

            # 4x6 extra small TTF
            try:
                xs_path = str(fonts_dir / '4x6-font.ttf')
                self.extra_small_font = ImageFont.truetype(xs_path, 6)
            except Exception:
                self.extra_small_font = self.small_font

        except Exception as e:
            logger.debug(f"Font loading fallback: {e}")
            self.regular_font = ImageFont.load_default()
            self.small_font = self.regular_font
            self.font = self.regular_font
            self.calendar_font = self.regular_font
            self.bdf_5x7_font = self.regular_font
            self.extra_small_font = self.regular_font

    # ------------------------------------------------------------------
    # Core display methods
    # ------------------------------------------------------------------

    def clear(self):
        """Clear the display to black."""
        self.clear_called = True
        self.image = Image.new('RGB', (self._width, self._height), (0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)

    def update_display(self):
        """No-op for hardware; marks that display was updated."""
        self.update_called = True

    def draw_text(self, text: str, x: int = None, y: int = None,
                  color: tuple = (255, 255, 255), small_font: bool = False,
                  font=None, centered: bool = False):
        """Draw text on the canvas, matching DisplayManager.draw_text() signature."""
        # Track the call
        self.draw_calls.append({
            'type': 'text', 'text': text, 'x': x, 'y': y,
            'color': color, 'font': font,
        })

        try:
            # Normalize color to tuple (plugins may pass lists from JSON config)
            if isinstance(color, list):
                color = tuple(color)

            # Select font
            if font:
                current_font = font
            else:
                current_font = self.small_font if small_font else self.regular_font

            # Calculate x position
            if x is None:
                text_width = self.get_text_width(text, current_font)
                x = (self.width - text_width) // 2
            elif centered:
                text_width = self.get_text_width(text, current_font)
                x = x - (text_width // 2)

            if y is None:
                y = 0

            # Draw
            try:
                import freetype
                is_bdf = isinstance(current_font, freetype.Face)
            except ImportError:
                is_bdf = False

            if is_bdf:
                self._draw_bdf_text(text, x, y, color, current_font)
            else:
                self.draw.text((x, y), text, font=current_font, fill=color)
        except Exception as e:
            logger.debug(f"Error drawing text: {e}")

    def draw_image(self, image: Image.Image, x: int, y: int):
        """Draw an image on the display."""
        self.draw_calls.append({
            'type': 'image', 'image': image, 'x': x, 'y': y,
        })
        try:
            self.image.paste(image, (x, y))
        except Exception as e:
            logger.debug(f"Error drawing image: {e}")

    def _draw_bdf_text(self, text, x, y, color=(255, 255, 255), font=None):
        """Draw text using BDF font with proper bitmap handling.

        Replicated from DisplayManager._draw_bdf_text().
        """
        try:
            import freetype
            if isinstance(color, list):
                color = tuple(color)
            face = font if font else self.calendar_font

            # Compute baseline from font ascender
            try:
                ascender_px = face.size.ascender >> 6
            except Exception:
                ascender_px = 0
            baseline_y = y + ascender_px

            for char in text:
                face.load_char(char)
                bitmap = face.glyph.bitmap

                glyph_left = face.glyph.bitmap_left
                glyph_top = face.glyph.bitmap_top

                for i in range(bitmap.rows):
                    for j in range(bitmap.width):
                        byte_index = i * bitmap.pitch + (j // 8)
                        if byte_index < len(bitmap.buffer):
                            byte = bitmap.buffer[byte_index]
                            if byte & (1 << (7 - (j % 8))):
                                pixel_x = x + glyph_left + j
                                pixel_y = baseline_y - glyph_top + i
                                if 0 <= pixel_x < self.width and 0 <= pixel_y < self.height:
                                    self.draw.point((pixel_x, pixel_y), fill=color)

                x += face.glyph.advance.x >> 6
        except Exception as e:
            logger.debug(f"Error drawing BDF text: {e}")

    # ------------------------------------------------------------------
    # Text measurement
    # ------------------------------------------------------------------

    def get_text_width(self, text: str, font=None) -> int:
        """Get text width in pixels, matching DisplayManager.get_text_width()."""
        if font is None:
            font = self.regular_font
        try:
            try:
                import freetype
                is_bdf = isinstance(font, freetype.Face)
            except ImportError:
                is_bdf = False

            if is_bdf:
                width = 0
                for char in text:
                    font.load_char(char)
                    width += font.glyph.advance.x >> 6
                return width
            else:
                bbox = self.draw.textbbox((0, 0), text, font=font)
                return bbox[2] - bbox[0]
        except Exception:
            return 0

    def get_font_height(self, font=None) -> int:
        """Get font height in pixels, matching DisplayManager.get_font_height()."""
        if font is None:
            font = self.regular_font
        try:
            try:
                import freetype
                is_bdf = isinstance(font, freetype.Face)
            except ImportError:
                is_bdf = False

            if is_bdf:
                return font.size.height >> 6
            else:
                ascent, descent = font.getmetrics()
                return ascent + descent
        except Exception:
            if hasattr(font, 'size'):
                return font.size
            return 8

    # ------------------------------------------------------------------
    # Weather drawing helpers
    # ------------------------------------------------------------------

    def draw_sun(self, x: int, y: int, size: int = 16):
        """Draw a sun icon using yellow circles and lines."""
        center = (x + size // 2, y + size // 2)
        radius = size // 3
        self.draw.ellipse(
            [center[0] - radius, center[1] - radius,
             center[0] + radius, center[1] + radius],
            fill=(255, 255, 0),
        )
        ray_length = size // 4
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            start_x = center[0] + (radius * math.cos(rad))
            start_y = center[1] + (radius * math.sin(rad))
            end_x = center[0] + ((radius + ray_length) * math.cos(rad))
            end_y = center[1] + ((radius + ray_length) * math.sin(rad))
            self.draw.line([start_x, start_y, end_x, end_y], fill=(255, 255, 0), width=2)

    def draw_cloud(self, x: int, y: int, size: int = 16, color=(200, 200, 200)):
        """Draw a cloud icon."""
        self.draw.ellipse([x + size // 4, y + size // 3, x + size // 4 + size // 2, y + size // 3 + size // 2], fill=color)
        self.draw.ellipse([x + size // 2, y + size // 3, x + size // 2 + size // 2, y + size // 3 + size // 2], fill=color)
        self.draw.ellipse([x + size // 3, y + size // 6, x + size // 3 + size // 2, y + size // 6 + size // 2], fill=color)

    def draw_rain(self, x: int, y: int, size: int = 16):
        """Draw rain icon with cloud and droplets."""
        self.draw_cloud(x, y, size)
        drop_color = (0, 0, 255)
        drop_size = size // 6
        for i in range(3):
            drop_x = x + size // 4 + (i * size // 3)
            drop_y = y + size // 2
            self.draw.line([drop_x, drop_y, drop_x, drop_y + drop_size], fill=drop_color, width=2)

    def draw_snow(self, x: int, y: int, size: int = 16):
        """Draw snow icon with cloud and snowflakes."""
        self.draw_cloud(x, y, size)
        snow_color = (200, 200, 255)
        for i in range(3):
            center_x = x + size // 4 + (i * size // 3)
            center_y = y + size // 2 + size // 4
            for angle in range(0, 360, 60):
                rad = math.radians(angle)
                end_x = center_x + (size // 8 * math.cos(rad))
                end_y = center_y + (size // 8 * math.sin(rad))
                self.draw.line([center_x, center_y, end_x, end_y], fill=snow_color, width=1)

    def _draw_sun(self, x: int, y: int, size: int) -> None:
        """Draw a sun icon with rays (internal weather icon version)."""
        center_x, center_y = x + size // 2, y + size // 2
        radius = size // 4
        ray_length = size // 3
        self.draw.ellipse(
            [center_x - radius, center_y - radius,
             center_x + radius, center_y + radius],
            fill=self.WEATHER_COLORS['sun'],
        )
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            start_x = center_x + int((radius + 2) * math.cos(rad))
            start_y = center_y + int((radius + 2) * math.sin(rad))
            end_x = center_x + int((radius + ray_length) * math.cos(rad))
            end_y = center_y + int((radius + ray_length) * math.sin(rad))
            self.draw.line([start_x, start_y, end_x, end_y], fill=self.WEATHER_COLORS['sun'], width=2)

    def _draw_cloud(self, x: int, y: int, size: int) -> None:
        """Draw a cloud using multiple circles (internal weather icon version)."""
        cloud_color = self.WEATHER_COLORS['cloud']
        base_y = y + size // 2
        circle_radius = size // 4
        positions = [
            (x + size // 3, base_y),
            (x + size // 2, base_y - size // 6),
            (x + 2 * size // 3, base_y),
        ]
        for cx, cy in positions:
            self.draw.ellipse(
                [cx - circle_radius, cy - circle_radius,
                 cx + circle_radius, cy + circle_radius],
                fill=cloud_color,
            )

    def _draw_rain(self, x: int, y: int, size: int) -> None:
        """Draw rain drops falling from a cloud."""
        self._draw_cloud(x, y, size)
        rain_color = self.WEATHER_COLORS['rain']
        drop_size = size // 8
        drops = [
            (x + size // 4, y + 2 * size // 3),
            (x + size // 2, y + 3 * size // 4),
            (x + 3 * size // 4, y + 2 * size // 3),
        ]
        for dx, dy in drops:
            self.draw.line([dx, dy, dx - drop_size // 2, dy + drop_size], fill=rain_color, width=2)

    def _draw_snow(self, x: int, y: int, size: int) -> None:
        """Draw snowflakes falling from a cloud."""
        self._draw_cloud(x, y, size)
        snow_color = self.WEATHER_COLORS['snow']
        flake_size = size // 6
        flakes = [
            (x + size // 4, y + 2 * size // 3),
            (x + size // 2, y + 3 * size // 4),
            (x + 3 * size // 4, y + 2 * size // 3),
        ]
        for fx, fy in flakes:
            for angle in range(0, 360, 60):
                rad = math.radians(angle)
                end_x = fx + int(flake_size * math.cos(rad))
                end_y = fy + int(flake_size * math.sin(rad))
                self.draw.line([fx, fy, end_x, end_y], fill=snow_color, width=1)

    def _draw_storm(self, x: int, y: int, size: int) -> None:
        """Draw a storm cloud with lightning bolt."""
        self._draw_cloud(x, y, size)
        bolt_color = self.WEATHER_COLORS['storm']
        bolt_points = [
            (x + size // 2, y + size // 2),
            (x + 3 * size // 5, y + 2 * size // 3),
            (x + 2 * size // 5, y + 2 * size // 3),
            (x + size // 2, y + 5 * size // 6),
        ]
        self.draw.polygon(bolt_points, fill=bolt_color)

    def draw_weather_icon(self, condition: str, x: int, y: int, size: int = 16) -> None:
        """Draw a weather icon based on the condition."""
        cond = condition.lower()
        if cond in ('clear', 'sunny'):
            self._draw_sun(x, y, size)
        elif cond in ('clouds', 'cloudy', 'partly cloudy'):
            self._draw_cloud(x, y, size)
        elif cond in ('rain', 'drizzle', 'shower'):
            self._draw_rain(x, y, size)
        elif cond in ('snow', 'sleet', 'hail'):
            self._draw_snow(x, y, size)
        elif cond in ('thunderstorm', 'storm'):
            self._draw_storm(x, y, size)
        else:
            self._draw_sun(x, y, size)

    def draw_text_with_icons(self, text: str, icons: List[tuple] = None,
                             x: int = None, y: int = None,
                             color: tuple = (255, 255, 255)):
        """Draw text with weather icons at specified positions."""
        self.draw_text(text, x, y, color)
        if icons:
            for icon_type, icon_x, icon_y in icons:
                self.draw_weather_icon(icon_type, icon_x, icon_y)
        self.update_display()

    # ------------------------------------------------------------------
    # Scrolling state (no-op interface compat)
    # ------------------------------------------------------------------

    def set_scrolling_state(self, is_scrolling: bool):
        """Set the current scrolling state (no-op for testing)."""
        self._scrolling_state['is_scrolling'] = is_scrolling
        if is_scrolling:
            self._scrolling_state['last_scroll_activity'] = time.time()

    def is_currently_scrolling(self) -> bool:
        """Check if display is currently scrolling."""
        return self._scrolling_state['is_scrolling']

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def format_date_with_ordinal(self, dt):
        """Formats a datetime object into 'Mon Aug 30th' style."""
        day = dt.day
        if 11 <= day <= 13:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        return dt.strftime(f"%b %-d{suffix}")

    # ------------------------------------------------------------------
    # Snapshot / image capture
    # ------------------------------------------------------------------

    def save_snapshot(self, path: str) -> None:
        """Save the current display as a PNG image."""
        self.image.save(path, format='PNG')

    def get_image(self) -> Image.Image:
        """Return the current display image."""
        return self.image

    def get_image_base64(self) -> str:
        """Return the current display as a base64-encoded PNG string."""
        import base64
        import io
        buffer = io.BytesIO()
        self.image.save(buffer, format='PNG')
        return base64.b64encode(buffer.getvalue()).decode('utf-8')

    # ------------------------------------------------------------------
    # Cleanup / reset
    # ------------------------------------------------------------------

    def reset(self):
        """Reset all tracking state (for test reuse)."""
        self.clear_called = False
        self.update_called = False
        self.draw_calls = []
        self.image = Image.new('RGB', (self._width, self._height), (0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)

    def cleanup(self):
        """Clean up resources."""
        self.image = Image.new('RGB', (self._width, self._height), (0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)
