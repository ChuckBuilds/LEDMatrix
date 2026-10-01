"""
Text Helper

Handles text rendering with outlines, fonts, and positioning for LED matrix displays.
Extracted from LEDMatrix core to provide reusable functionality for plugins.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from PIL import Image, ImageDraw, ImageFont
from src.common.font_layout import load_truetype, resolve_asset_path

# Shared throwaway draw surface for measuring text without a target canvas.
_measure_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

#: A one-pixel outline on all eight sides, in the order the scoreboards have
#: always drawn it (dx outer, dy inner). The order matters only for a
#: translucent outline, where it is kept anyway.
OUTLINE_SQUARE: Tuple[Tuple[int, int], ...] = (
    (-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))

#: A one-pixel outline on the four edge sides only, leaving the diagonal
#: corners open: the thinner outline ufc's fight card draws.
OUTLINE_CROSS: Tuple[Tuple[int, int], ...] = ((-1, 0), (1, 0), (0, -1), (0, 1))

# What the stamping path in draw_text_outlined is proven pixel-identical for
# (test/test_text_helper.py compares it with the draw.text loop across every
# combination). Anything else takes the loop. Compared with ``in`` on tuples
# rather than sets so an unhashable fontmode falls back instead of raising.
_STAMP_DRAW_MODES = ("RGB", "RGBA", "L")
_STAMP_FONT_MODES = ("1", "L")

# ImageDraw.text as Pillow defines it, which the stamping path stands in for.
# A draw whose text has been replaced since -- on the class or the instance,
# as a test recording the strings drawn does -- takes the loop, so the
# replacement still sees every call.
_PILLOW_DRAW_TEXT = ImageDraw.ImageDraw.text


def draw_text_outlined(draw: ImageDraw.ImageDraw, xy: Sequence[Any], text: Any,
                       font: Any, fill: Any,
                       outline_color: Any = (0, 0, 0),
                       offsets: Iterable[Sequence[Any]] = OUTLINE_SQUARE) -> None:
    """Draw ``text`` in ``outline_color`` at each of ``offsets``, then in ``fill`` on top.

    The result is pixel-identical to the loop every outlined draw used to be::

        x, y = xy
        for dx, dy in offsets:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        draw.text((x, y), text, font=font, fill=fill)

    but each ``draw.text`` rasterizes the whole string through FreeType again,
    so the default nine draws did the same glyph work nine times, and on a
    scoreboard card that text work is much of the render. Here the string is
    rasterized once and the one mask is stamped at every offset, which is
    what ``draw.text`` itself does with the mask, so the pixels are the same.

    That holds only where it has been checked: a plain ``ImageDraw`` whose
    ``text`` is Pillow's, a ``FreeTypeFont``, one line of ``str``, whole-pixel
    ``xy`` (an int, or a float with nothing after the point, which is what
    centring on a measured ``textlength`` with ``// 2`` gives) and int
    offsets, and the image and font modes in ``_STAMP_DRAW_MODES`` /
    ``_STAMP_FONT_MODES``. Fractional coordinates change the raster itself
    (Pillow rasterizes at the sub-pixel start), and multiline text is laid
    out line by line. Every other case, and anything
    the stamping path cannot prepare, runs the loop above unchanged, so it
    behaves exactly as before, errors included.

    Args:
        draw: The ``ImageDraw`` to draw on.
        xy: Top-left (x, y) of the text, as for ``draw.text``.
        text: The text.
        font: The font, as for ``draw.text``.
        fill: Colour of the text itself, drawn last.
        outline_color: Colour of the outline.
        offsets: (dx, dy) of each outline draw, in drawing order.
            :data:`OUTLINE_SQUARE` (the default) or :data:`OUTLINE_CROSS`.
    """
    x, y = xy
    # Read once: the loop below may have to start over after the stamping
    # path looked at them.
    offsets = tuple(offsets)
    if _can_stamp(draw, x, y, text, font, offsets):
        if _stamp_outlined(draw, int(x), int(y), text, font, fill,
                           outline_color, offsets):
            return
    for dx, dy in offsets:
        draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
    draw.text((x, y), text, font=font, fill=fill)


def _can_stamp(draw: Any, x: Any, y: Any, text: Any, font: Any,
               offsets: Tuple[Any, ...]) -> bool:
    """Whether draw_text_outlined may stamp one mask instead of drawing N times.

    Exact types for the draw and the font, and Pillow's own ``draw.text``: a
    subclass may override ``text`` or ``getmask2``, or a test may replace
    ``draw.text`` to record what is drawn, and stamping would skip either.
    """
    return (
        type(draw) is ImageDraw.ImageDraw
        and ImageDraw.ImageDraw.text is _PILLOW_DRAW_TEXT
        and "text" not in vars(draw)
        and type(font) is ImageFont.FreeTypeFont
        and isinstance(text, str)
        and "\n" not in text
        and "\r" not in text
        and _whole_pixel(x)
        and _whole_pixel(y)
        and all(isinstance(o, (tuple, list)) and len(o) == 2
                and isinstance(o[0], int) and isinstance(o[1], int)
                for o in offsets)
        and draw.mode in _STAMP_DRAW_MODES
        and draw.fontmode in _STAMP_FONT_MODES
    )


def _whole_pixel(v: Any) -> bool:
    """An int, or a float on a whole pixel, as a draw.text coordinate.

    For those, draw.text's ``int(x + dx)`` is ``int(x) + dx`` and its
    sub-pixel start is 0 (or -0.0, which renders the same), so one mask fits
    every offset. Floats are held well inside the range where ``x + dx`` is
    exact; Pillow cannot draw past 2**31 anyway.
    """
    if isinstance(v, int):
        return True
    return isinstance(v, float) and v.is_integer() and -2**31 < v < 2**31


def _text_ink(draw: ImageDraw.ImageDraw, color: Any) -> Any:
    """The ink ``ImageDraw.text`` resolves ``color`` to (its inner getink)."""
    ink, fill_ink = draw._getink(color)
    return fill_ink if ink is None else ink


def _stamp_outlined(draw: ImageDraw.ImageDraw, x: int, y: int, text: str,
                    font: ImageFont.FreeTypeFont, fill: Any, outline_color: Any,
                    offsets: Tuple[Sequence[Any], ...]) -> bool:
    """Rasterize once and stamp; False, with nothing drawn, to take the loop.

    Replays what ``ImageDraw.text`` does for one line at an integer position
    (Pillow 11 and 12): ``font.getmask2`` with these arguments, then
    ``draw.draw.draw_bitmap`` at the position plus the mask's offset.
    ``draw.draw`` and ``draw._getink`` are Pillow internals, so everything up
    to the first pixel is guarded: if anything fails before then, nothing has
    been drawn and the loop runs instead, which then fails (or not) exactly
    as it always did -- a bad fill colour still raises after the outline is
    drawn, as it did from the last ``draw.text``.
    """
    try:
        outline_ink = _text_ink(draw, outline_color)
        text_ink = _text_ink(draw, fill)
        # What draw.text passes for a single line with no anchor at a whole
        # pixel position, by keyword so a getmask2 with another parameter
        # order cannot shift them. ink only matters to an RGBA (colour-glyph)
        # mask, which the font modes allowed here never produce.
        mask, (ox, oy) = font.getmask2(
            text, draw.fontmode, direction=None, features=None,
            language=None, stroke_width=0, anchor="la", ink=text_ink,
            start=(0.0, 0.0), stroke_filled=True)
        draw_bitmap = draw.draw.draw_bitmap
    except Exception:
        return False
    stamped = False
    try:
        # draw.text returns without drawing when its ink resolves to None.
        if outline_ink is not None:
            for dx, dy in offsets:
                draw_bitmap((x + dx + ox, y + dy + oy), mask, outline_ink)
                stamped = True
        if text_ink is not None:
            draw_bitmap((x + ox, y + oy), mask, text_ink)
    except Exception:
        # A rejected call draws nothing, but one that got through has: never
        # draw the outline twice (a translucent one would darken).
        if stamped:
            raise
        return False
    return True


class TextHelper:
    """
    Font loading, outlined text and text measurement for plugins.

    - :meth:`load_fonts` loads TrueType fonts from ``font_dir`` (the install's
      assets/fonts by default) with the layout engine pinned
      (font_layout.load_truetype). Each (file, size) is loaded once per helper
      and reused; a missing or unloadable file becomes PIL's default font.
    - :meth:`draw_text_with_outline` and friends draw onto a caller's
      ``ImageDraw``; the measuring methods need no canvas.
    """
    
    def __init__(self, font_dir: Optional[Union[str, Path]] = None, 
                 logger: Optional[logging.Logger] = None):
        """
        Initialize the TextHelper.
        
        Args:
            font_dir: Directory containing font files. Defaults to the
                install's assets/fonts, whatever the process cwd is.
            logger: Optional logger instance
        """
        self.logger = logger or logging.getLogger(__name__)
        self.font_dir = Path(font_dir) if font_dir else Path(resolve_asset_path("assets/fonts"))
        # "<path>:<size>" -> loaded font; see load_fonts.
        self._font_cache: Dict[str, ImageFont.ImageFont] = {}
    
    def load_fonts(self, font_config: Optional[Dict[str, Dict]] = None) -> Dict[str, ImageFont.ImageFont]:
        """
        Load fonts for different text elements.

        Args:
            font_config: ``{name: {"file": <file in font_dir>, "size": <px>}}``;
                defaults to the scoreboard set in _get_default_font_config.

        Returns:
            Dictionary mapping font names to PIL ImageFont objects. A font
            already loaded by this helper at the same size is reused.
        """
        if font_config is None:
            font_config = self._get_default_font_config()
        
        fonts = {}
        
        for font_name, config in font_config.items():
            try:
                font_path = self.font_dir / config['file']
                size = config['size']
                
                if font_path.exists():
                    cache_key = f"{font_path}:{size}"
                    font = self._font_cache.get(cache_key)
                    if font is None:
                        font = load_truetype(str(font_path), size)
                        self._font_cache[cache_key] = font
                        self.logger.debug(f"Loaded font: {font_name} ({font_path}, size {size})")
                    fonts[font_name] = font
                else:
                    # Fallback to default font
                    font = ImageFont.load_default()
                    fonts[font_name] = font
                    self.logger.warning(f"Font file not found: {font_path}, using default")
                    
            except Exception as e:
                self.logger.error(f"Error loading font {font_name}: {e}")
                fonts[font_name] = ImageFont.load_default()
        
        return fonts
    
    def draw_text_with_outline(self, draw: ImageDraw.ImageDraw, text: str, 
                              position: Tuple[int, int], font: ImageFont.ImageFont,
                              fill: Tuple[int, int, int] = (255, 255, 255),
                              outline_color: Tuple[int, int, int] = (0, 0, 0),
                              outline_width: int = 1) -> None:
        """
        Draw text with an outline for better readability on LED displays.
        
        Args:
            draw: PIL ImageDraw object
            text: Text to draw
            position: (x, y) position tuple
            font: PIL ImageFont object
            fill: Text color (R, G, B)
            outline_color: Outline color (R, G, B)
            outline_width: Width of outline in pixels
        """
        x, y = position

        # Outline: every offset up to outline_width away on each axis, centre
        # skipped, in the order this has always drawn them (OUTLINE_SQUARE at
        # width 1). The main text is drawn last, on top.
        offsets = [(dx, dy)
                   for dx in range(-outline_width, outline_width + 1)
                   for dy in range(-outline_width, outline_width + 1)
                   if dx != 0 or dy != 0]
        draw_text_outlined(draw, (x, y), text, font, fill, outline_color, offsets)
    
    def get_text_width(self, text: str, font: ImageFont.ImageFont) -> int:
        """
        Get the width of text when rendered with the given font.
        
        Args:
            text: Text to measure
            font: PIL ImageFont object
            
        Returns:
            Width in pixels
        """
        return int(_measure_draw.textlength(text, font=font))
    
    def get_text_height(self, text: str, font: ImageFont.ImageFont) -> int:
        """
        Get the height of text when rendered with the given font.
        
        Args:
            text: Text to measure
            font: PIL ImageFont object
            
        Returns:
            Height in pixels
        """
        bbox = _measure_draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]
    
    def get_text_dimensions(self, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
        """
        Get both width and height of text.
        
        Args:
            text: Text to measure
            font: PIL ImageFont object
            
        Returns:
            (width, height) tuple
        """
        return (self.get_text_width(text, font), self.get_text_height(text, font))
    
    def center_text(self, text: str, font: ImageFont.ImageFont, 
                   container_width: int, container_height: int) -> Tuple[int, int]:
        """
        Calculate position to center text within a container.
        
        Args:
            text: Text to center
            font: PIL ImageFont object
            container_width: Width of container
            container_height: Height of container
            
        Returns:
            (x, y) position tuple for centered text
        """
        text_width, text_height = self.get_text_dimensions(text, font)
        x = (container_width - text_width) // 2
        y = (container_height - text_height) // 2
        return (x, y)
    
    def wrap_text(self, text: str, font: ImageFont.ImageFont, 
                  max_width: int, max_lines: Optional[int] = None) -> List[str]:
        """
        Wrap text to fit within specified width.
        
        Args:
            text: Text to wrap
            font: PIL ImageFont object
            max_width: Maximum width in pixels
            max_lines: Maximum number of lines (None for unlimited)
            
        Returns:
            List of text lines
        """
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            # Test if adding this word would exceed width
            test_line = ' '.join(current_line + [word])
            if self.get_text_width(test_line, font) <= max_width:
                current_line.append(word)
            else:
                # Start new line
                if current_line:
                    lines.append(' '.join(current_line))
                    current_line = [word]
                else:
                    # Single word is too long, add it anyway
                    lines.append(word)
        
        # Add remaining words
        if current_line:
            lines.append(' '.join(current_line))
        
        # Limit lines if specified
        if max_lines is not None:
            lines = lines[:max_lines]
        
        return lines
    
    def draw_multiline_text(self, draw: ImageDraw.ImageDraw, text: str,
                           position: Tuple[int, int], font: ImageFont.ImageFont,
                           line_spacing: int = 2, **kwargs) -> None:
        """
        Draw multiline text with proper spacing.
        
        Args:
            draw: PIL ImageDraw object
            text: Text to draw (can contain newlines)
            position: Starting (x, y) position
            font: PIL ImageFont object
            line_spacing: Pixels between lines
            **kwargs: Additional arguments for draw_text_with_outline
        """
        x, y = position
        lines = text.split('\n')
        
        for line in lines:
            if line.strip():  # Skip empty lines
                self.draw_text_with_outline(draw, line, (x, y), font, **kwargs)
            y += self.get_text_height(line, font) + line_spacing
    
    def create_text_image(self, text: str, font: ImageFont.ImageFont,
                         background_color: Tuple[int, int, int] = (0, 0, 0),
                         text_color: Tuple[int, int, int] = (255, 255, 255),
                         padding: int = 5) -> Image.Image:
        """
        Create an image containing only the specified text.
        
        Args:
            text: Text to render
            font: PIL ImageFont object
            background_color: Background color (R, G, B)
            text_color: Text color (R, G, B)
            padding: Padding around text in pixels
            
        Returns:
            PIL Image containing the text
        """
        # Calculate dimensions
        text_width, text_height = self.get_text_dimensions(text, font)
        img_width = text_width + (padding * 2)
        img_height = text_height + (padding * 2)
        
        # Create image
        img = Image.new('RGB', (img_width, img_height), background_color)
        draw = ImageDraw.Draw(img)
        
        # Draw text
        self.draw_text_with_outline(draw, text, (padding, padding), font, 
                                   fill=text_color)
        
        return img
    
    def _get_default_font_config(self) -> Dict[str, Dict]:
        """Get default font configuration."""
        return {
            'score': {
                'file': 'PressStart2P-Regular.ttf',
                'size': 10
            },
            'time': {
                'file': 'PressStart2P-Regular.ttf', 
                'size': 8
            },
            'team': {
                'file': 'PressStart2P-Regular.ttf',
                'size': 8
            },
            'status': {
                'file': '4x6-font.ttf',
                'size': 6
            },
            'detail': {
                'file': '4x6-font.ttf',
                'size': 6
            },
            'rank': {
                'file': 'PressStart2P-Regular.ttf',
                'size': 10
            }
        }
    
    def clear_font_cache(self) -> None:
        """Clear the font cache."""
        self._font_cache.clear()
        self.logger.debug("Font cache cleared")
    
    def get_font_cache_stats(self) -> Dict[str, int]:
        """
        Get font cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        return {
            'cached_fonts': len(self._font_cache)
        }
