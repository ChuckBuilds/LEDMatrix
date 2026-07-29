"""
Geometry primitives for Vegas Mode.

Pure, side-effect-free measurements over PIL images. Two consumers:

- ``PluginAdapter`` trims the blank margins plugins bake into their content
  before it enters the ticker (see ``trim_to_content``).
- ``scripts/dev/vegas_audit.py`` reports how much of the composed ticker is
  dead space (see ``dead_window_stats``).

Keeping both on the same primitives means the number the audit reports is the
number the trimmer acted on.

All column scans go through numpy: a Python-level per-column loop over a
17,000px-wide ticker image takes seconds, which is far too slow for the render
path.
"""

from typing import NamedTuple, Optional, Tuple

import numpy as np
from PIL import Image

# A pixel counts as "ink" when any channel exceeds this. Chosen to ignore the
# 1-2/255 noise that JPEG-sourced logos and alpha compositing leave behind in
# nominally black areas, while still treating any deliberately drawn dark grey
# as real content.
DEFAULT_INK_THRESHOLD = 10

# A window counts as "dead" when this fraction of its columns carry no ink.
DEFAULT_DEAD_WINDOW_RATIO = 0.95


def column_has_ink(img: Image.Image, threshold: int = DEFAULT_INK_THRESHOLD) -> np.ndarray:
    """
    Return a boolean array, one entry per image column, True where the column
    contains at least one pixel brighter than ``threshold`` in any channel.

    Args:
        img: Image to scan (converted to RGB internally)
        threshold: Per-channel value a pixel must exceed to count as ink

    Returns:
        Bool array of shape (width,)
    """
    arr = np.asarray(img if img.mode == 'RGB' else img.convert('RGB'))
    if arr.ndim != 3:
        # Degenerate/empty image — treat every column as blank.
        return np.zeros(img.width, dtype=bool)
    # Collapse rows and channels: a column is ink if any pixel in it is bright.
    return arr.max(axis=(0, 2)) > threshold


def content_bounds(
    img: Image.Image, threshold: int = DEFAULT_INK_THRESHOLD
) -> Optional[Tuple[int, int]]:
    """
    Find the first and last columns containing ink.

    Args:
        img: Image to measure
        threshold: Ink threshold

    Returns:
        (first_col, last_col) inclusive, or None if the image is entirely blank
    """
    ink = column_has_ink(img, threshold)
    if not ink.any():
        return None
    first = int(ink.argmax())
    last = len(ink) - 1 - int(ink[::-1].argmax())
    return first, last


class TrimResult(NamedTuple):
    """Outcome of a ``trim_to_content`` call."""

    image: Optional[Image.Image]  # None when the source was entirely blank
    original_width: int
    trimmed_left: int
    trimmed_right: int

    @property
    def is_blank(self) -> bool:
        """True when the source image carried no ink at all."""
        return self.image is None

    @property
    def width(self) -> int:
        """Width after trimming (0 for a blank source)."""
        return 0 if self.image is None else self.image.width

    @property
    def removed(self) -> int:
        """Total columns removed."""
        return self.trimmed_left + self.trimmed_right


def trim_to_content(
    img: Image.Image,
    threshold: int = DEFAULT_INK_THRESHOLD,
    padding: int = 0,
) -> TrimResult:
    """
    Crop blank columns off the left and right edges of an image.

    Only the outer edges are considered. Blank columns *between* two pieces of
    content are deliberately preserved — those are the plugin's own layout
    (e.g. a logo on the left and a score on the right), and closing them up
    would corrupt the design rather than reclaim dead space.

    A plugin drawing on a non-black background is unaffected: every column of a
    filled background carries ink, so there is nothing to trim.

    Args:
        img: Image to trim
        threshold: Ink threshold
        padding: Columns of the original blank margin to keep on each side, as
            breathing room. Capped at what the margin actually contains, so
            this never widens the image beyond its original bounds.

    Returns:
        TrimResult. When the image is entirely blank, ``image`` is None and the
        caller decides whether to skip the plugin.
    """
    bounds = content_bounds(img, threshold)
    if bounds is None:
        return TrimResult(None, img.width, 0, 0)

    first, last = bounds
    pad = max(0, padding)
    left = max(0, first - pad)
    right = min(img.width, last + 1 + pad)

    if left == 0 and right == img.width:
        return TrimResult(img, img.width, 0, 0)

    cropped = img.crop((left, 0, right, img.height))
    return TrimResult(cropped, img.width, left, img.width - right)


def edge_blank(
    img: Image.Image, threshold: int = DEFAULT_INK_THRESHOLD
) -> Tuple[int, int]:
    """
    Blank column counts at the left and right edges of an image.

    Used to space items by *measured* separation rather than a flat added gap.
    A fixed gap gets this wrong in both directions at once: card-style content
    drawn flush to its own edges ends up nearly touching its neighbour, while
    content that already carries wide margins gets pushed even further apart.

    Args:
        img: Image to measure
        threshold: Ink threshold

    Returns:
        (left_blank, right_blank). For an entirely blank image both are the
        full width, since there is no ink to be close to.
    """
    bounds = content_bounds(img, threshold)
    if bounds is None:
        return img.width, img.width
    first, last = bounds
    return first, img.width - 1 - last


def separation_gap(
    left_img: Image.Image,
    right_img: Image.Image,
    target: int,
    minimum: int = 0,
    threshold: int = DEFAULT_INK_THRESHOLD,
) -> int:
    """
    Columns to insert between two images so their ink is ``target`` apart.

    Only the shortfall is added: if the two images already carry enough blank
    at the facing edges, nothing (beyond ``minimum``) is inserted.

    Args:
        left_img: Image on the left
        right_img: Image on the right
        target: Desired blank columns between the two pieces of ink
        minimum: Floor applied regardless of what the images already have
        threshold: Ink threshold

    Returns:
        Number of columns to insert, never negative
    """
    existing = edge_blank(left_img, threshold)[1] + edge_blank(right_img, threshold)[0]
    return max(minimum, target - existing, 0)


def find_blank_cut(
    img: Image.Image,
    target: int,
    search_radius: int,
    threshold: int = DEFAULT_INK_THRESHOLD,
) -> int:
    """
    Find a column near ``target`` that carries no ink, so an image can be cut
    there without slicing through a glyph or logo.

    Used when a single oversized segment has to be narrowed to fit a width
    budget. Cutting at an arbitrary column would leave half a character
    hanging at the panel edge; snapping to the nearest gap hides the cut.

    Args:
        img: Image to cut
        target: Preferred cut column
        search_radius: How far either side of ``target`` to look
        threshold: Ink threshold

    Returns:
        A blank column within the search window, or ``target`` clamped to the
        image bounds when the window contains no blank column at all.
    """
    width = img.width
    target = max(0, min(target, width))
    if search_radius <= 0 or width == 0:
        return target

    ink = column_has_ink(img, threshold)
    lo = max(0, target - search_radius)
    hi = min(width - 1, target + search_radius)

    # Walk outwards from target so the nearest gap wins.
    for offset in range(0, search_radius + 1):
        right = target + offset
        if right <= hi and not ink[right]:
            return right
        left = target - offset
        if left >= lo and not ink[left]:
            return left

    return target


class DeadWindowStats(NamedTuple):
    """How much of a composed ticker reads as blank to a viewer."""

    total_windows: int
    dead_windows: int
    longest_dead_run: int  # consecutive dead windows (i.e. scroll steps)

    @property
    def dead_ratio(self) -> float:
        """Fraction of viewport positions that are effectively blank."""
        if self.total_windows <= 0:
            return 0.0
        return self.dead_windows / self.total_windows


def dead_window_stats(
    img: Image.Image,
    viewport_width: int,
    threshold: int = DEFAULT_INK_THRESHOLD,
    dead_ratio: float = DEFAULT_DEAD_WINDOW_RATIO,
    step: int = 1,
) -> DeadWindowStats:
    """
    Slide a viewport across a composed ticker image and count how many
    positions are effectively blank.

    This models what the viewer actually experiences: the ticker is only ever
    seen ``viewport_width`` columns at a time, so a stretch of blank wider than
    the viewport becomes a period where the panel looks switched off. Measuring
    per-window rather than per-column is what makes the result correspond to
    perceived dead time.

    Args:
        img: Composed ticker image
        viewport_width: Display width in pixels
        threshold: Ink threshold
        dead_ratio: Fraction of blank columns for a window to count as dead
        step: Column stride between sampled windows. 1 is exact; larger values
            trade precision for speed on very wide images.

    Returns:
        DeadWindowStats. ``longest_dead_run`` is in units of ``step`` columns,
        so multiply by ``step`` for pixels.
    """
    if viewport_width <= 0 or img.width <= 0:
        return DeadWindowStats(0, 0, 0)

    ink = column_has_ink(img, threshold)
    step = max(1, step)

    # Prefix sum of ink counts lets each window be evaluated in constant time,
    # instead of re-summing viewport_width columns per position.
    prefix = np.concatenate(([0], np.cumsum(ink)))

    # Only whole windows are sampled; a partial tail window would report
    # artificially dead because it has fewer columns to draw ink from.
    last_start = img.width - viewport_width
    if last_start < 0:
        # Image narrower than the viewport — evaluate it as a single window.
        blank_cols = len(ink) - int(prefix[-1])
        is_dead = blank_cols >= dead_ratio * len(ink)
        return DeadWindowStats(1, 1 if is_dead else 0, 1 if is_dead else 0)

    starts = np.arange(0, last_start + 1, step)
    ink_counts = prefix[starts + viewport_width] - prefix[starts]
    blank_counts = viewport_width - ink_counts
    dead = blank_counts >= dead_ratio * viewport_width

    longest = _longest_true_run(dead)
    return DeadWindowStats(len(starts), int(dead.sum()), longest)


class CoverageStats(NamedTuple):
    """How well-filled the viewport stays as the ticker scrolls past."""

    total_windows: int
    mean_ink_ratio: float       # average fraction of the viewport carrying ink
    min_ink_ratio: float        # worst viewport position in the cycle
    sparse_windows: int         # positions below the "looks empty" threshold
    longest_sparse_run: int     # consecutive sparse positions, in steps

    @property
    def sparse_ratio(self) -> float:
        """Fraction of viewport positions that read as near-empty."""
        if self.total_windows <= 0:
            return 0.0
        return self.sparse_windows / self.total_windows


def window_coverage_stats(
    img: Image.Image,
    viewport_width: int,
    threshold: int = DEFAULT_INK_THRESHOLD,
    sparse_ink_ratio: float = 0.10,
    step: int = 1,
) -> CoverageStats:
    """
    Measure how full the viewport stays across a whole scroll cycle.

    ``dead_window_stats`` only catches viewport positions that are *entirely*
    blank. That misses the more common complaint: a position holding one narrow
    sliver of content at the very edge, with the other 90% black. Such a
    position is not "dead" by that definition but still looks switched off.
    This function grades every position by how much ink it carries, so
    "there is always something to see" becomes measurable.

    Args:
        img: Composed ticker image
        viewport_width: Display width in pixels
        threshold: Ink threshold
        sparse_ink_ratio: A position with less than this fraction of inked
            columns counts as reading near-empty
        step: Column stride between sampled positions

    Returns:
        CoverageStats
    """
    if viewport_width <= 0 or img.width <= 0:
        return CoverageStats(0, 0.0, 0.0, 0, 0)

    ink = column_has_ink(img, threshold)
    step = max(1, step)
    prefix = np.concatenate(([0], np.cumsum(ink)))

    last_start = img.width - viewport_width
    if last_start < 0:
        ratio = float(prefix[-1]) / viewport_width
        sparse = ratio < sparse_ink_ratio
        return CoverageStats(1, ratio, ratio, 1 if sparse else 0, 1 if sparse else 0)

    starts = np.arange(0, last_start + 1, step)
    ratios = (prefix[starts + viewport_width] - prefix[starts]) / viewport_width
    sparse_flags = ratios < sparse_ink_ratio

    return CoverageStats(
        total_windows=len(starts),
        mean_ink_ratio=float(ratios.mean()),
        min_ink_ratio=float(ratios.min()),
        sparse_windows=int(sparse_flags.sum()),
        longest_sparse_run=_longest_true_run(sparse_flags),
    )


def _longest_true_run(flags: np.ndarray) -> int:
    """Length of the longest consecutive run of True in a boolean array."""
    if flags.size == 0 or not flags.any():
        return 0
    # Reset a running counter at every False by subtracting the cumulative max
    # of the counter's value at the preceding False positions.
    idx = np.arange(len(flags))
    not_flag = ~flags
    # For each position, the index of the most recent False at or before it.
    last_false = np.maximum.accumulate(np.where(not_flag, idx, -1))
    run_lengths = idx - last_false
    return int(run_lengths[flags].max())
