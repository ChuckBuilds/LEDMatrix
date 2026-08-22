"""
Skin API: the classes a skin author works with.

A skin is a directory under skins/<skin-id>/ containing a skin.json
manifest and a Python module exposing a ScoreboardSkin subclass. The
host (a sports scoreboard's base classes) builds a SkinContext per
render and calls render_live / render_recent / render_upcoming with the
game view model. The skin draws onto ctx.canvas and returns True; the
host composites the canvas onto the display. A skin never talks to the
display, the network, or the plugin directly.

Skin API Version: 1.0.0
View Model Version: 1.0
"""

from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple, Union

from PIL import Image, ImageDraw

try:
    import freetype
except ImportError:  # pragma: no cover - freetype ships with the project deps
    freetype = None

from src.adaptive_layout import FitResult, LayoutContext, Region

# Major must match a skin manifest's skin_api_version major or the skin
# is refused at load time (renames/removals bump major; additions minor).
SKIN_API_VERSION = "1.0.0"

# Version of the guaranteed `game` dict keys (see docs/CREATING_SKINS.md).
VIEW_MODEL_VERSION = "1.0"


def _draw_bdf_text_on(draw: ImageDraw.ImageDraw, text: str, x: int, y: int,
                      color: Tuple[int, int, int], face: Any,
                      clip_w: int, clip_h: int) -> None:
    """Render a freetype BDF face glyph-by-glyph onto an arbitrary canvas.

    DisplayManager._draw_bdf_text only draws onto the panel image; skins
    draw onto their own canvas, so the fitted-font path (fit_text can
    return freetype faces) needs this standalone equivalent.
    """
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
                if byte_index < len(bitmap.buffer) and \
                        bitmap.buffer[byte_index] & (1 << (7 - (j % 8))):
                    px = x + glyph_left + j
                    py = baseline_y - glyph_top + i
                    if 0 <= px < clip_w and 0 <= py < clip_h:
                        draw.point((px, py), fill=color)
        x += face.glyph.advance.x >> 6


@dataclass
class SkinContext:
    """Everything a skin may touch during one render call.

    The canvas is a fresh RGB image sized to the current display (or
    vegas card). Draw onto it via the helpers below or raw ``draw``;
    never call display/update methods — the host composites the canvas.
    """

    canvas: Image.Image
    draw: ImageDraw.ImageDraw
    layout: LayoutContext
    width: int
    height: int
    fonts: Dict[str, Any]
    options: Dict[str, Any]
    logger: Any
    sport: Optional[str] = None
    view_model_version: str = VIEW_MODEL_VERSION
    # load_logo("home") / load_logo("away") -> RGBA PIL image or None.
    # Bound to the current game; hits the host's logo cache (never loads
    # from disk twice), downloads missing logos like the built-in layout.
    load_logo: Callable[[str], Optional[Image.Image]] = field(default=lambda side: None)
    # draw_text_outlined(text, (x, y), font, fill=..., outline_color=...)
    # — the classic scorebug outlined text, drawn onto this canvas.
    # TTF fonts only (ctx.fonts values are TTF); for ladder-fitted fonts
    # use draw_fit / draw_text, which handle BDF faces too.
    draw_text_outlined: Callable[..., None] = field(default=lambda *a, **k: None)

    def draw_text(self, text: str, x: int, y: int,
                  color: Tuple[int, int, int] = (255, 255, 255),
                  font: Any = None) -> None:
        """Draw text at a top-left position, handling both PIL fonts and
        the freetype BDF faces that layout.fit_text can return."""
        if font is None:
            font = self.fonts.get('time')
        if freetype is not None and isinstance(font, freetype.Face):
            _draw_bdf_text_on(self.draw, text, int(x), int(y), color, font,
                              self.width, self.height)
        else:
            self.draw.text((int(x), int(y)), text, font=font, fill=color)

    def draw_fit(self, fit: FitResult, box: Union[Region, Tuple[int, int]],
                 color: Tuple[int, int, int] = (255, 255, 255),
                 align: str = "center", valign: str = "center") -> None:
        """Draw a layout.fit_text() result aligned within a Region — the
        canvas-local equivalent of adaptive_layout.draw_fitted_text."""
        region = box if isinstance(box, Region) else Region(0, 0, box[0], box[1])
        x, y = region.align_xy(fit.width, fit.height, align, valign)
        self.draw_text(fit.text, x, y - fit.y_offset, color=color, font=fit.font)

    def draw_image(self, img: Optional[Image.Image],
                   box: Union[Region, Tuple[int, int]], *,
                   mode: str = "contain", align: str = "center",
                   valign: str = "center", cache_key: Any = None) -> None:
        """Fit an image (a logo, art) into a Region and paste it, honoring
        alpha. Silently no-ops on None so `ctx.draw_image(ctx.load_logo(
        'home'), ...)` stays safe when a logo is missing."""
        if img is None:
            return
        region = box if isinstance(box, Region) else Region(0, 0, box[0], box[1])
        fitted = self.layout.fit_image(img, region, mode=mode,
                                       cache_key=cache_key)
        result = fitted.image  # fit_image returns an ImageFitResult (always RGBA)
        if result is None:
            return
        x, y = region.align_xy(result.width, result.height, align, valign)
        self.canvas.paste(result, (int(x), int(y)), result)


class ScoreboardSkin(ABC):
    """Base class for scoreboard skins.

    Override only the modes you want to restyle; any mode you leave
    unimplemented (or return False from) falls back to the plugin's
    built-in renderer, so a live-only skin still gets recent/upcoming
    screens for free.

    Skins should be stateless: three host instances (live, recent,
    upcoming) each hold their own skin instance, and a render must be
    derivable from (ctx, game) alone.
    """

    SKIN_API_VERSION = SKIN_API_VERSION

    def __init__(self, manifest: Dict[str, Any], options: Dict[str, Any]):
        self.manifest = manifest
        self.options = options or {}

    def render_live(self, ctx: SkinContext, game: Dict[str, Any]) -> bool:
        return False

    def render_recent(self, ctx: SkinContext, game: Dict[str, Any]) -> bool:
        return False

    def render_upcoming(self, ctx: SkinContext, game: Dict[str, Any]) -> bool:
        return False

    def render_vegas_card(self, ctx: SkinContext,
                          game: Dict[str, Any]) -> Optional[Image.Image]:
        """Render one vegas scroll card at ctx.width x ctx.height. Return
        the finished image, or None to let the host use its default vegas
        rendering (which captures the regular display output)."""
        return None
