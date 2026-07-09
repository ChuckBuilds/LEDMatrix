"""
Adaptive layout and font scaling helpers for plugins.

Generalizes the three size-adaptation patterns proven in the plugin
ecosystem into small composable core helpers, so plugins render legibly on
any panel size (64x32, 128x32, 96x48, 128x64, 256x64, ...) without
hand-tuned per-display layouts:

- Region: integer rect algebra (bands, columns, weighted splits, centering).
  Regions partition space, so text bands can't overlap by construction —
  replacing the magic ``y = 1`` / ``y = height - 7`` offsets tuned for 128x32.
- Font ladders: ordered (family, size) steps known to render crisply.
  Pixel fonts (BDF, PressStart2P) only look right at native/integer sizes,
  so fonts are never scaled continuously — fitting walks a ladder from the
  largest rung down until the measured text fits the target box. This is
  baseball-scoreboard's fallback-ladder pattern promoted to core.
- LayoutContext: per-(width, height) facts — breakpoint tiers
  (masters-tournament's pattern), a geometry scale factor vs. a declared
  design size (f1-scoreboard's pattern), and cached fit-text queries.

Everything is opt-in: plugins get a context via ``self.layout`` on
BasePlugin (or construct one directly) and existing plugins are unaffected.

Fonts are resolved through FontManager's catalog (family names are
lowercased file stems from assets/fonts, e.g. "9x15", "tom-thumb", plus
aliases like "press_start"). FitResult.font is a plain PIL font or
freetype.Face, so it drops straight into DisplayManager.draw_text().
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import freetype

logger = logging.getLogger(__name__)

# Height-based breakpoint tiers, smallest to largest. A 32px-tall panel is
# the ecosystem baseline ("sm"); 96x48 lands in "md"; 128x64 in "lg".
_HEIGHT_TIERS: Tuple[Tuple[str, int], ...] = (
    ("xs", 16), ("sm", 32), ("md", 48), ("lg", 64), ("xl", 10 ** 9),
)
TIER_ORDER: Tuple[str, ...] = tuple(name for name, _ in _HEIGHT_TIERS)

_WIDTH_TIERS: Tuple[Tuple[str, int], ...] = (
    ("narrow", 64), ("normal", 128), ("wide", 256), ("ultrawide", 10 ** 9),
)
WIDTH_TIER_ORDER: Tuple[str, ...] = tuple(name for name, _ in _WIDTH_TIERS)

# The panel size most existing plugins were authored against.
DEFAULT_DESIGN_SIZE: Tuple[int, int] = (128, 32)


@dataclass(frozen=True)
class Region:
    """An integer rectangle. Carving methods return sub-Regions clamped to
    non-negative dimensions, so degenerate panels never produce negative
    boxes — a band request larger than the region simply consumes it all."""

    x: int
    y: int
    w: int
    h: int

    def __post_init__(self):
        object.__setattr__(self, "w", max(0, int(self.w)))
        object.__setattr__(self, "h", max(0, int(self.h)))
        object.__setattr__(self, "x", int(self.x))
        object.__setattr__(self, "y", int(self.y))

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    # ---- carving -----------------------------------------------------

    def inset(self, dx: int, dy: Optional[int] = None) -> "Region":
        """Shrink by dx horizontally and dy (default dx) vertically, each side."""
        if dy is None:
            dy = dx
        return Region(self.x + dx, self.y + dy, self.w - 2 * dx, self.h - 2 * dy)

    def top_band(self, h: int) -> "Region":
        return Region(self.x, self.y, self.w, min(h, self.h))

    def bottom_band(self, h: int) -> "Region":
        h = min(h, self.h)
        return Region(self.x, self.bottom - h, self.w, h)

    def middle(self, top_h: int = 0, bottom_h: int = 0) -> "Region":
        """What remains between a top band and a bottom band."""
        return Region(self.x, self.y + top_h, self.w, self.h - top_h - bottom_h)

    def left_col(self, w: int) -> "Region":
        return Region(self.x, self.y, min(w, self.w), self.h)

    def right_col(self, w: int) -> "Region":
        w = min(w, self.w)
        return Region(self.right - w, self.y, w, self.h)

    def split_h(self, *weights: float, gap: int = 0) -> List["Region"]:
        """Side-by-side columns sized by weight; gaps between them."""
        sizes = _weighted_sizes(self.w, weights, gap)
        cols, cursor = [], self.x
        for size in sizes:
            cols.append(Region(cursor, self.y, size, self.h))
            cursor += size + gap
        return cols

    def split_v(self, *weights: float, gap: int = 0) -> List["Region"]:
        """Stacked rows sized by weight; gaps between them."""
        sizes = _weighted_sizes(self.h, weights, gap)
        rows, cursor = [], self.y
        for size in sizes:
            rows.append(Region(self.x, cursor, self.w, size))
            cursor += size + gap
        return rows

    # ---- placement ---------------------------------------------------

    def align_xy(self, w: int, h: int, align: str = "center",
                 valign: str = "center") -> Tuple[int, int]:
        """Top-left position for a w x h box aligned within this region.
        align: left|center|right; valign: top|center|bottom."""
        if align == "left":
            x = self.x
        elif align == "right":
            x = self.right - w
        else:
            x = self.x + (self.w - w) // 2
        if valign == "top":
            y = self.y
        elif valign == "bottom":
            y = self.bottom - h
        else:
            y = self.y + (self.h - h) // 2
        return (x, y)

    def center_xy(self, w: int, h: int) -> Tuple[int, int]:
        return self.align_xy(w, h)

    def contains(self, w: int, h: int) -> bool:
        return w <= self.w and h <= self.h


def _weighted_sizes(total: int, weights: Sequence[float], gap: int) -> List[int]:
    """Integer sizes proportional to weights, remainder spread left-to-right."""
    if not weights:
        return []
    usable = max(0, total - gap * (len(weights) - 1))
    weight_sum = sum(weights) or 1
    sizes = [int(usable * w / weight_sum) for w in weights]
    remainder = usable - sum(sizes)
    for i in range(remainder):
        sizes[i % len(sizes)] += 1
    return sizes


# ---------------------------------------------------------------------------
# Font ladders
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FontStep:
    """One rung: a FontManager catalog family at a size it renders crisply."""
    family: str
    size_px: int


FontLadder = Tuple[FontStep, ...]

# X11 BDF bitmap fonts at their native pixel sizes, largest to smallest —
# baseball-scoreboard's fallback ladder extended upward. Same-height rungs
# are ordered widest first so width-constrained text steps to a narrower
# face before dropping a size.
LADDER_GRID: FontLadder = (
    FontStep("10x20", 20),
    FontStep("9x18", 18),
    FontStep("9x15", 15),
    FontStep("8x13", 13),
    FontStep("7x13", 13),
    FontStep("6x13", 13),
    FontStep("6x12", 12),
    FontStep("6x10", 10),
    FontStep("6x9", 9),
    FontStep("5x8", 8),
    FontStep("5x7", 7),
    FontStep("4x6", 6),
    FontStep("tom-thumb", 6),
)

# PressStart2P at integer multiples of its 8px pixel grid only — fractional
# sizes blur a pixel font. For headline text (clocks, scores).
LADDER_ARCADE: FontLadder = (
    FontStep("press_start", 32),
    FontStep("press_start", 24),
    FontStep("press_start", 16),
    FontStep("press_start", 8),
)

LADDER_DEFAULT: FontLadder = LADDER_GRID

ELLIPSIS = "…"


@dataclass(frozen=True)
class FitResult:
    """A fitted font plus the ink metrics of the (possibly ellipsized) text.

    ``y_offset`` is the gap between the y passed to draw_text() and where
    ink actually starts; subtract it from the desired ink-top position when
    drawing (draw_fitted_text does this for you).
    """
    font: Any
    family: str
    size_px: int
    text: str
    width: int
    height: int
    baseline: int
    y_offset: int
    fits: bool
    line_height: int = 0


def measure_ink(text: str, font: Any) -> Tuple[int, int, int, int]:
    """Measure the ink box of text: (width, height, baseline, y_offset).

    y_offset is the distance from the y coordinate DisplayManager.draw_text()
    is given to the top of the actual ink — PIL draws TTF from the em-box
    top and _draw_bdf_text derives the baseline from y + ascender, so both
    leave a font-dependent gap that matters when centering in short bands.
    """
    if isinstance(font, freetype.Face):
        width = 0
        ascender = font.size.ascender >> 6
        ink_top, ink_bottom = None, None
        for char in text:
            font.load_char(char)
            width += font.glyph.advance.x >> 6
            rows = font.glyph.bitmap.rows
            if rows:
                top = ascender - font.glyph.bitmap_top
                ink_top = top if ink_top is None else min(ink_top, top)
                ink_bottom = top + rows if ink_bottom is None else max(ink_bottom, top + rows)
        if ink_top is None:
            ink_top, ink_bottom = 0, 0
        return (width, ink_bottom - ink_top, ascender, ink_top)
    bbox = font.getbbox(text)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1], -bbox[1], bbox[1])


def font_line_height(font: Any) -> int:
    """Recommended line spacing for a font (matches DisplayManager.get_font_height)."""
    if isinstance(font, freetype.Face):
        return font.size.height >> 6
    ascent, descent = font.getmetrics()
    return ascent + descent


class LayoutContext:
    """Per-render-size layout facts and fit-text queries for one panel size.

    Construct once per (width, height); BasePlugin.layout does this and
    rebuilds automatically when the logical display size changes.
    """

    def __init__(self, width: int, height: int, font_manager: Any,
                 design_size: Tuple[int, int] = DEFAULT_DESIGN_SIZE):
        self.width = int(width)
        self.height = int(height)
        self.font_manager = font_manager
        self.design_size = design_size
        self.bounds = Region(0, 0, self.width, self.height)
        self.aspect = self.width / max(1, self.height)
        self.tier = _pick_tier(_HEIGHT_TIERS, self.height)
        self.width_tier = _pick_tier(_WIDTH_TIERS, self.width)
        self.is_wide_short = self.aspect >= 2.5 and self.height <= 32
        design_w, design_h = design_size
        # Geometry scale only (gaps, icon/logo sizes) — never applied to
        # fonts, which step between crisp ladder rungs instead.
        self.scale = min(self.width / max(1, design_w),
                         self.height / max(1, design_h))
        self._fit_cache: Dict[Any, FitResult] = {}

    # ---- the three adaptation patterns --------------------------------

    def px(self, base: int, minimum: int = 1, maximum: Optional[int] = None) -> int:
        """Scale a design-size pixel measurement (f1's pattern): gaps,
        icon sizes, logo slots. Clamped to [minimum, maximum]."""
        value = max(minimum, round(base * self.scale))
        if maximum is not None:
            value = min(value, maximum)
        return value

    def by_tier(self, mapping: Dict[str, Any], default: Any = None) -> Any:
        """Pick the value for the nearest defined tier at-or-below the
        panel's height tier (masters' pattern). Falls forward to the
        smallest defined tier above, then to default.

        by_tier({"sm": 10, "lg": 18}) -> 10 on 128x32, 18 on 128x64.
        Keys may also use width tiers ("narrow", "wide", ...)."""
        order = TIER_ORDER if any(k in TIER_ORDER for k in mapping) else WIDTH_TIER_ORDER
        current = self.tier if order is TIER_ORDER else self.width_tier
        idx = order.index(current)
        for name in reversed(order[: idx + 1]):
            if name in mapping:
                return mapping[name]
        for name in order[idx + 1:]:
            if name in mapping:
                return mapping[name]
        return default

    def fit_text(self, text: str, box: Union[Region, Tuple[int, int]],
                 ladder: FontLadder = LADDER_DEFAULT,
                 ellipsis: bool = True) -> FitResult:
        """Largest ladder rung whose rendered text fits the box (baseball's
        pattern). If even the smallest rung is too wide, the text is
        ellipsized to fit (unless ellipsis=False); fits=False only when no
        acceptable rendering exists."""
        box_w, box_h = _box_dims(box)
        key = ("text", text, box_w, box_h, ladder, ellipsis)
        cached = self._fit_cache.get(key)
        if cached is not None:
            return cached

        result = None
        for step in ladder:
            font = self.font_manager.get_font(step.family, step.size_px)
            width, height, baseline, y_offset = measure_ink(text, font)
            result = FitResult(font, step.family, step.size_px, text,
                               width, height, baseline, y_offset,
                               fits=(width <= box_w and height <= box_h),
                               line_height=font_line_height(font))
            if result.fits:
                break

        if result is not None and not result.fits and ellipsis:
            short = self.ellipsize(text, result.font, box_w)
            width, height, baseline, y_offset = measure_ink(short, result.font)
            result = FitResult(result.font, result.family, result.size_px,
                               short, width, height, baseline, y_offset,
                               fits=(width <= box_w and height <= box_h),
                               line_height=result.line_height)

        self._fit_cache[key] = result
        return result

    def fit_lines(self, lines: Sequence[str], box: Union[Region, Tuple[int, int]],
                  ladder: FontLadder = LADDER_DEFAULT,
                  spacing: int = 1) -> FitResult:
        """Largest rung where every line fits the box width and the stacked
        lines (line_height + spacing apart) fit the box height. Measures the
        actual strings, so a long line pushes the ladder down a rung a short
        one wouldn't (baseball's multiline pattern). Text is the widest line."""
        box_w, box_h = _box_dims(box)
        key = ("lines", tuple(lines), box_w, box_h, ladder, spacing)
        cached = self._fit_cache.get(key)
        if cached is not None:
            return cached

        rows = max(1, len(lines))
        result = None
        for step in ladder:
            font = self.font_manager.get_font(step.family, step.size_px)
            line_h = font_line_height(font)
            widest, metrics = "", (0, 0, 0, 0)
            for line in lines:
                m = measure_ink(line, font)
                if m[0] >= metrics[0]:
                    widest, metrics = line, m
            total_h = rows * line_h + (rows - 1) * spacing
            result = FitResult(font, step.family, step.size_px, widest,
                               metrics[0], metrics[1], metrics[2], metrics[3],
                               fits=(metrics[0] <= box_w and total_h <= box_h),
                               line_height=line_h)
            if result.fits:
                break

        self._fit_cache[key] = result
        return result

    def font_for_rows(self, rows: int, box_h: int,
                      ladder: FontLadder = LADDER_GRID) -> FitResult:
        """Largest rung whose line height lets `rows` rows fit in box_h
        (baseball's traditional-scoreboard pattern). Measures a digit/cap
        sample rather than specific strings."""
        key = ("rows", rows, box_h, ladder)
        cached = self._fit_cache.get(key)
        if cached is not None:
            return cached

        sample = "0Ay"
        result = None
        for step in ladder:
            font = self.font_manager.get_font(step.family, step.size_px)
            line_h = font_line_height(font)
            width, height, baseline, y_offset = measure_ink(sample, font)
            result = FitResult(font, step.family, step.size_px, sample,
                               width, height, baseline, y_offset,
                               fits=(max(1, rows) * line_h <= box_h),
                               line_height=line_h)
            if result.fits:
                break

        self._fit_cache[key] = result
        return result

    # ---- text utilities ------------------------------------------------

    def ellipsize(self, text: str, font: Any, max_w: int) -> str:
        """Trim text to fit max_w, appending an ellipsis. Returns '' when
        not even the ellipsis fits."""
        if measure_ink(text, font)[0] <= max_w:
            return text
        for end in range(len(text) - 1, 0, -1):
            candidate = text[:end].rstrip() + ELLIPSIS
            if measure_ink(candidate, font)[0] <= max_w:
                return candidate
        return ELLIPSIS if measure_ink(ELLIPSIS, font)[0] <= max_w else ""

    def measure(self, text: str, font: Any) -> Tuple[int, int, int]:
        """Ink (width, height, baseline) of text — see measure_ink."""
        width, height, baseline, _ = measure_ink(text, font)
        return (width, height, baseline)

    def clear_cache(self) -> None:
        """Drop cached fit results (call after fonts are reloaded)."""
        self._fit_cache.clear()


def _pick_tier(tiers: Tuple[Tuple[str, int], ...], value: int) -> str:
    for name, limit in tiers:
        if value <= limit:
            return name
    return tiers[-1][0]


def _box_dims(box: Union[Region, Tuple[int, int]]) -> Tuple[int, int]:
    if isinstance(box, Region):
        return (box.w, box.h)
    w, h = box
    return (int(w), int(h))


def draw_fitted_text(display_manager: Any, fit: FitResult,
                     box: Union[Region, Tuple[int, int]],
                     color: Tuple[int, int, int] = (255, 255, 255),
                     align: str = "center", valign: str = "center") -> None:
    """Draw a FitResult's text aligned within a Region via
    DisplayManager.draw_text(), compensating for the font's ink offset so
    the ink (not the em box) is what gets aligned."""
    region = box if isinstance(box, Region) else Region(0, 0, box[0], box[1])
    x, y = region.align_xy(fit.width, fit.height, align, valign)
    display_manager.draw_text(fit.text, x=x, y=y - fit.y_offset,
                              color=color, font=fit.font)
