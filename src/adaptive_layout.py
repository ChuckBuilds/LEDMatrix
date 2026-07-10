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
from collections import OrderedDict
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

    def offset(self, dx: int, dy: int) -> "Region":
        """Translate without resizing — the hook for user x/y-offset
        customization: compute regions first, then apply the user's
        configured offsets as a final translation."""
        return Region(self.x + dx, self.y + dy, self.w, self.h)

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


def measure_font_crispness(font: Any, sample_text: str = "Ay0",
                           canvas_size: Tuple[int, int] = (250, 60)) -> float:
    """Fraction of the rendered sample's ink-bbox pixels that are neither
    pure black nor pure white — i.e. antialiased.

    BDF (freetype.Face) glyphs are true bitmaps and always render at 0.0.
    "Pixel-style" TTFs (PressStart2P, and similar fonts bundled for
    plugins that draw through ImageDraw.text() and so can't take a BDF
    face) are NOT automatically crisp at arbitrary sizes — PIL antialiases
    TTF outlines by default, and a pixel-grid font only lands on whole
    pixels at specific sizes (for PressStart2P: exact multiples of 8).
    Requesting an unverified size silently produces soft/blurry glyphs on
    an LED panel, which reads as fuzzy compared to a true BDF rung.

    Use this to vet any custom FontLadder rung that mixes TTF fonts before
    shipping it — see test_adaptive_layout.py::test_ladder_is_crisp for the
    pattern. A rung should score 0.0 (or very close, to allow for the odd
    diagonal stroke) before it belongs in a "crisp" ladder.
    """
    if isinstance(font, freetype.Face):
        return 0.0
    from PIL import Image, ImageDraw
    img = Image.new("L", canvas_size, 0)
    ImageDraw.Draw(img).text((2, 2), sample_text, font=font, fill=255)
    bbox = img.getbbox()
    if bbox is None:
        return 0.0
    pixels = img.crop(bbox).tobytes()
    pure = sum(1 for p in pixels if p == 0 or p == 255)
    return (len(pixels) - pure) / len(pixels)


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
        # LRU-bounded (images are big, unlike text fits). Entries hold a
        # strong reference to the source image when keyed by id() so the id
        # can't be recycled out from under the cache.
        self._image_cache: "OrderedDict[Any, Tuple[Any, Any]]" = OrderedDict()

    _IMAGE_CACHE_MAX = 64

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
        result = self._walk_ladder(text, ladder, box_w, box_h, ellipsis)
        self._fit_cache[key] = result
        return result

    def fit_text_proportional(self, text: str, box: Union[Region, Tuple[int, int]],
                              base_size_px: int, ladder: FontLadder = LADDER_DEFAULT,
                              ellipsis: bool = True,
                              scale: Optional[float] = None) -> FitResult:
        """Ladder rung closest to (but not exceeding) ``base_size_px * scale``
        that still fits the box — proportional sizing instead of ``fit_text``'s
        "always maximize" behavior.

        Use this when several independently-fitted elements need to stay
        visually harmonious as the panel grows (e.g. a scoreboard's score,
        status, and detail text) — ``fit_text`` maximizes each one within
        its own region, which can make one element balloon out of
        proportion to its neighbors (a huge score overlapping logos it fit
        fine at the design size) even though every individual pick is
        independently "correct". ``base_size_px`` is the size that element
        renders at on the design size (``design_size``, typically 128x32)
        — commonly a plugin's existing classic/fixed font size for that
        element.

        ``scale`` defaults to ``self.scale`` (the same conservative
        min(width_ratio, height_ratio) factor ``px()`` uses — safe for
        content whose aspect ratio matters). Pass an explicit axis-specific
        value when the surrounding composition already scales that way —
        e.g. a scoreboard whose logos scale with height alone
        (``logo_slot = min(height, width // 2)``) should size its score
        text by ``height / design_height`` too, or its text will look
        under-scaled next to bigger logos on a panel that only grew taller.

        Falls back to the smallest rung when even that exceeds the target
        (a tiny scale factor), and to fit_text's ordinary smaller-rung
        fallback when the closest-to-target rung doesn't actually fit the
        box.
        """
        box_w, box_h = _box_dims(box)
        effective_scale = self.scale if scale is None else scale
        key = ("text_prop", text, box_w, box_h, ladder, base_size_px, ellipsis, effective_scale)
        cached = self._fit_cache.get(key)
        if cached is not None:
            return cached
        target = base_size_px * effective_scale
        eligible = [step for step in ladder if step.size_px <= target]
        candidates = eligible if eligible else (min(ladder, key=lambda s: s.size_px),)
        result = self._walk_ladder(text, candidates, box_w, box_h, ellipsis)
        self._fit_cache[key] = result
        return result

    def _walk_ladder(self, text: str, ladder: Sequence[FontStep],
                     box_w: int, box_h: int, ellipsis: bool) -> FitResult:
        """Shared by fit_text/fit_text_proportional: first ladder entry (in
        the order given) whose rendered text fits, ellipsizing the last one
        tried if none do."""
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

    # ---- images ---------------------------------------------------------

    def fit_image(self, img: Any, box: Union[Region, Tuple[int, int]], *,
                  mode: str = "contain", crop_to_ink: bool = False,
                  anchor: str = "center", resample: Any = None,
                  upscale: bool = True, cache_key: Any = None) -> Any:
        """Fit an image into a box (see src/adaptive_images.py for modes),
        cached per (image, box size, options) for this panel size.

        Prefer a stable ``cache_key`` (e.g. "logo:KC") for images that get
        reloaded — the default id()-based key is safe (the entry pins the
        source image) but misses across reloads of the same content.
        """
        from src.adaptive_images import fit_image as _fit_image

        box_w, box_h = _box_dims(box)
        resample_name = getattr(resample, "name", repr(resample)) if resample is not None else "default"
        identity = cache_key if cache_key is not None else ("id", id(img))
        key = ("image", identity, img.size, box_w, box_h, mode,
               crop_to_ink, anchor, resample_name, upscale)

        cached = self._image_cache.get(key)
        if cached is not None:
            self._image_cache.move_to_end(key)
            return cached[0]

        result = _fit_image(img, (box_w, box_h), mode=mode,
                            crop_to_ink=crop_to_ink, anchor=anchor,
                            resample=resample, upscale=upscale)
        # Pin the source only for id()-keyed entries (see docstring).
        self._image_cache[key] = (result, img if cache_key is None else None)
        while len(self._image_cache) > self._IMAGE_CACHE_MAX:
            self._image_cache.popitem(last=False)
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
        self._image_cache.clear()


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


# ---------------------------------------------------------------------------
# Composite layouts — the region arrangements repeated across plugins,
# expressed as Region math so migrated plugins stop hand-copying coordinate
# formulas. Deliberately tiny: these return Regions, they don't draw.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoreboardRegions:
    """The two-logos-plus-center-score card shared by the sports plugins."""
    bounds: Region
    logo_slot: int        # width of each logo slot: min(H, W // 2)
    away_slot: Region     # left logo slot
    home_slot: Region     # right logo slot
    center_col: Region    # column between the slots (0-wide on square panels)
    status_band: Region   # top band (replaces the magic y = 1)
    score_area: Region    # center column minus the bands (replaces y = H//2 - 3)
    detail_band: Region   # bottom band (replaces the magic y = H - 7)
    bottom_left: Region   # bottom corner: away records / timeouts
    bottom_right: Region  # bottom corner: home records / timeouts


def scoreboard_regions(bounds: Region, *, ctx: Optional["LayoutContext"] = None,
                       status_h: Optional[int] = None,
                       detail_h: Optional[int] = None) -> ScoreboardRegions:
    """Carve a game-card Region into the standard scoreboard arrangement.

    Encodes the invariant duplicated across the sports plugins:
    ``logo_slot = min(height, width // 2)`` (capped at half the card so the
    home slot never collapses), away logo centered in the left slot, home in
    the right. The text bands (status/score/detail) span the FULL card width
    and overlay the logo slots — exactly like the classic layouts, where
    outlined text is drawn over the logos; on square-ish panels the column
    between the slots can be zero-wide, so full-width bands are the only
    correct home for text. Band heights default to the classic 128x32
    values, scaled by the context's geometry factor when one is provided.
    Works on a full panel or on a scroll-mode card Region.
    """
    if status_h is None:
        status_h = ctx.px(9, minimum=7) if ctx else 9
    if detail_h is None:
        detail_h = ctx.px(8, minimum=7) if ctx else 8

    logo_slot = min(bounds.h, bounds.w // 2)
    away_slot = bounds.left_col(logo_slot)
    home_slot = bounds.right_col(logo_slot)
    center_col = Region(bounds.x + logo_slot, bounds.y,
                        bounds.w - 2 * logo_slot, bounds.h)
    status_band = bounds.top_band(status_h)
    detail_band = bounds.bottom_band(detail_h)
    score_area = bounds.middle(status_band.h, detail_band.h)
    bottom = bounds.bottom_band(detail_h)
    return ScoreboardRegions(
        bounds=bounds, logo_slot=logo_slot,
        away_slot=away_slot, home_slot=home_slot, center_col=center_col,
        status_band=status_band, score_area=score_area, detail_band=detail_band,
        bottom_left=bottom.left_col(logo_slot),
        bottom_right=bottom.right_col(logo_slot),
    )


@dataclass(frozen=True)
class MediaRow:
    """Art/icon on the left, text column on the right (music's idiom)."""
    art: Region
    body: Region


def media_row(bounds: Region, *, ctx: Optional["LayoutContext"] = None,
              square: bool = True, gap: Optional[int] = None) -> MediaRow:
    """Split a Region into an art slot and a body column.

    With ``square=True`` the art slot is bounds.h wide (album-art style);
    otherwise it takes the left half. The gap defaults to 2px scaled by the
    context's geometry factor.
    """
    if gap is None:
        gap = ctx.px(2, minimum=1) if ctx else 2
    art_w = bounds.h if square else bounds.w // 2
    art_w = min(art_w, bounds.w)
    art = bounds.left_col(art_w)
    body = Region(bounds.x + art_w + gap, bounds.y,
                  bounds.w - art_w - gap, bounds.h)
    return MediaRow(art=art, body=body)
