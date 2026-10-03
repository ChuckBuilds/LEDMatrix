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

MAINTENANCE WARNING: this class is a deliberate fork of
src/display_manager.py so it can run without hardware. It mirrors
these DisplayManager methods by name and behavior: _load_fonts,
get_font_height, get_text_width, draw_text, format_date_with_ordinal,
capture_mode, set_scrolling_state, is_currently_scrolling,
process_deferred_updates, update_display, render_size, offscreen. A behavior
change to any of those in DisplayManager must be mirrored here, or
plugin visual tests will pass against stale behavior.

BDF text is not mirrored: both classes load BDF faces and draw BDF glyphs
through src/common/bdf_font.py, so those pixels cannot drift.
"""

import os
import time
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
from src.common.bdf_font import draw_bdf_text, load_bdf_face
from src.common.font_layout import crisp_size, load_truetype

from src.logging_config import get_logger
from src.plugin_system.testing.mocks import DRAW_IMAGE_DEPRECATION

logger = get_logger(__name__)

_draw_image_warning_logged = False


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

    def __init__(self, width: int = 128, height: int = 32):
        self._width = width
        self._height = height

        # Canvas
        self.image = Image.new('RGB', (width, height), (0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)
        self.draw.fontmode = "1"  # Match production: 1-bit text, so goldens show what the panel shows.

        # Matrix proxy (plugins access display_manager.matrix.width/height)
        self.matrix = _MatrixProxy(width, height)

        # Set while inside capture_mode(); mirrors DisplayManager's flag.
        self._capture_mode_active = False

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
            press_start = 'PressStart2P-Regular.ttf'
            ttf_path = str(fonts_dir / press_start)
            self.regular_font = load_truetype(ttf_path, crisp_size(press_start, 8))
            self.small_font = load_truetype(ttf_path, crisp_size(press_start, 8))
            self.font = self.regular_font  # alias used by some code paths

            # 5x7 BDF font, loaded exactly as DisplayManager._load_fonts does
            # (same loader, same 7px request as its _CALENDAR_FONT_PX). A bare
            # freetype.Face has no active size, so its ascender reads 0 and
            # every line drew a baseline too high.
            try:
                bdf_path = str(fonts_dir / '5x7.bdf')
                if not os.path.exists(bdf_path):
                    raise FileNotFoundError(f"BDF font not found: {bdf_path}")
                face, _ = load_bdf_face(bdf_path, 7)
                self.calendar_font = face
                self.bdf_5x7_font = face
            except Exception as e:  # freetype missing or the file unloadable
                logger.debug("BDF font not available, using small_font as fallback: %s", e)
                self.calendar_font = self.small_font
                self.bdf_5x7_font = self.small_font

            # 4x6 extra small TTF, snapped to the face's 7px grid exactly as
            # DisplayManager._load_fonts does. Sizing this independently is how
            # the harness would render -- and bless goldens -- in a face the
            # panel never uses: at the off-grid 6 this asked for, every glyph
            # loses its fourth column under `draw.fontmode = "1"`.
            try:
                four_by_six = '4x6-font.ttf'
                xs_path = str(fonts_dir / four_by_six)
                self.extra_small_font = load_truetype(xs_path, crisp_size(four_by_six, 6))
            except (FileNotFoundError, OSError) as e:
                logger.debug("Extra small font not available, using fallback: %s", e)
                self.extra_small_font = self.small_font

        except (FileNotFoundError, OSError) as e:
            logger.debug("Font loading fallback: %s", e)
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
        self.draw.fontmode = "1"  # Match production: 1-bit text, so goldens show what the panel shows.

    def update_display(self):
        """No-op for hardware; marks that display was updated."""
        self.update_called = True

    @contextmanager
    def render_size(self, width: int, height: Optional[int] = None):
        """
        Interface parity with DisplayManager.render_size().

        Vegas mode narrows the canvas so plugins lay out compactly instead of
        being cropped. The harness must offer the same context or that path
        cannot be exercised offline — and because the adapter catches broadly,
        a missing method shows up as "no content" rather than an error.
        """
        prev_image = self.image
        prev_draw = self.draw
        prev_w, prev_h = self._width, self._height

        target_w = max(1, min(int(width), prev_w))
        target_h = max(1, min(int(height) if height else prev_h, prev_h))

        try:
            self._width, self._height = target_w, target_h
            self.matrix = _MatrixProxy(target_w, target_h)
            self.image = Image.new('RGB', (target_w, target_h), (0, 0, 0))
            self.draw = ImageDraw.Draw(self.image)
            self.draw.fontmode = "1"  # Match production: 1-bit text, so goldens show what the panel shows.
            yield
        finally:
            self._width, self._height = prev_w, prev_h
            self.matrix = _MatrixProxy(prev_w, prev_h)
            self.image = prev_image
            self.draw = prev_draw

    @contextmanager
    def capture_mode(self):
        """
        Interface parity with DisplayManager.capture_mode().

        There is no hardware to suppress here, but Vegas mode's PluginAdapter
        wraps every off-screen content fetch in this context, so the harness
        must provide it for that code path to be exercisable in tests.
        """
        was_active = self._capture_mode_active
        self._capture_mode_active = True
        try:
            yield
        finally:
            self._capture_mode_active = was_active

    @contextmanager
    def offscreen(self, width: Optional[int] = None, height: Optional[int] = None):
        """
        Interface parity with DisplayManager.offscreen().

        Vegas mode's PluginAdapter draws every plugin on a canvas of its own.
        The real display manager keeps that canvas per thread; the harness is
        single-threaded, so it swaps a fresh canvas in and restores the old one,
        which is all a test can observe.
        """
        prev = (self.image, self.draw, self._width, self._height,
                self.matrix, self._capture_mode_active)
        target_w = max(1, min(int(width), self._width)) if width else self._width
        target_h = max(1, min(int(height), self._height)) if height else self._height
        try:
            self._width, self._height = target_w, target_h
            self.matrix = _MatrixProxy(target_w, target_h)
            self.image = Image.new('RGB', (target_w, target_h), (0, 0, 0))
            self.draw = ImageDraw.Draw(self.image)
            # Match production: 1-bit text, so goldens show what the panel shows.
            self.draw.fontmode = "1"
            self._capture_mode_active = True
            yield self
        finally:
            (self.image, self.draw, self._width, self._height,
             self.matrix, self._capture_mode_active) = prev

    def draw_text(self, text: str, x: Optional[int] = None, y: Optional[int] = None,
                  color: Tuple[int, int, int] = (255, 255, 255), small_font: bool = False,
                  font: Optional[Any] = None, centered: bool = False) -> None:
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
            # WARNING, not DEBUG: the real DisplayManager logs this at ERROR,
            # and a test double that hides it lets a broken draw pass.
            logger.warning(f"Error drawing text: {e}")

    def draw_image(self, image: Image.Image, x: int, y: int):
        """Draw an image on the display. Deprecated: see DRAW_IMAGE_DEPRECATION."""
        warnings.warn(DRAW_IMAGE_DEPRECATION, DeprecationWarning, stacklevel=2)
        global _draw_image_warning_logged
        if not _draw_image_warning_logged:
            # Also logged once: the dev preview server drives this class
            # outside pytest, where DeprecationWarning is hidden by default.
            _draw_image_warning_logged = True
            logger.warning(DRAW_IMAGE_DEPRECATION)
        self.draw_calls.append({
            'type': 'image', 'image': image, 'x': x, 'y': y,
        })
        try:
            self.image.paste(image, (x, y))
        except Exception as e:
            logger.warning(f"Error drawing image: {e}")

    def _draw_bdf_text(self, text, x, y, color=(255, 255, 255), font=None):
        """Draw text in a BDF ``freetype.Face`` with (x, y) as its top-left.

        Not a copy: DisplayManager._draw_bdf_text calls the same
        :func:`src.common.bdf_font.draw_bdf_text`, so what this draws is
        what the panel draws.
        """
        try:
            if isinstance(color, list):
                color = tuple(color)
            face = font if font else self.calendar_font
            draw_bdf_text(self.draw, text, x, y, face, color,
                          clip=(self.width, self.height))
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
    # Scrolling state (no-op interface compat)
    # ------------------------------------------------------------------

    def set_scrolling_state(self, is_scrolling: bool, frame_hold: int = 1):
        """Set the current scrolling state (no-op for testing).

        ``frame_hold`` mirrors the DisplayManager signature this change adds.
        The two are kept in step deliberately: a double that accepts arguments
        production does not lets a call pass every harness run and then raise
        TypeError on the panel, and a double that lacks one production has
        fails every render of a plugin that legitimately paces its scroll.
        Plugins begin passing it in ledmatrix-plugins#462.
        """
        self._scrolling_state['is_scrolling'] = is_scrolling
        self._scrolling_state['frame_hold'] = frame_hold
        if is_scrolling:
            self._scrolling_state['last_scroll_activity'] = time.time()

    def is_currently_scrolling(self) -> bool:
        """Check if display is currently scrolling."""
        return self._scrolling_state['is_scrolling']

    def process_deferred_updates(self):
        """Process any deferred updates (no-op for testing).

        Several ticker-style plugins (news, odds-ticker, leaderboard,
        stock-news, stocks) call this unconditionally between
        set_scrolling_state() and their scroll-position update, mirroring the
        real display_manager's deferred-update queue. This double has no such
        queue, so there is nothing to process — the no-op just lets those
        plugins render under the harness instead of raising AttributeError.
        """
        pass

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
        self.draw.fontmode = "1"  # Match production: 1-bit text, so goldens show what the panel shows.
        self._scrolling_state = {
            'is_scrolling': False,
            'last_scroll_activity': 0,
            'scroll_inactivity_threshold': 2.0,
            'deferred_updates': [],
            'max_deferred_updates': 50,
            'deferred_update_ttl': 300.0,
        }

    def cleanup(self):
        """Clean up resources."""
        self.image = Image.new('RGB', (self._width, self._height), (0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)
        self.draw.fontmode = "1"  # Match production: 1-bit text, so goldens show what the panel shows.
