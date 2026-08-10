"""
Plugin Adapter for Vegas Mode

Converts plugin content to scrollable images. Supports both plugins that
implement get_vegas_content() and fallback capture of display() output.
"""

import logging
import threading
import time
from contextlib import nullcontext
from typing import Optional, List, Any, Tuple, Union, TYPE_CHECKING
from PIL import Image

from src.vegas_mode.geometry import (
    blank_runs,
    separation_gap,
    trim_to_content,
)

if TYPE_CHECKING:
    from src.plugin_system.base_plugin import BasePlugin

logger = logging.getLogger(__name__)


class PluginAdapter:
    """
    Adapter for extracting scrollable content from plugins.

    Supports two modes:
    1. Native: Plugin implements get_vegas_content() returning PIL Image(s)
    2. Fallback: Capture display_manager.image after calling plugin.display()
    """

    def __init__(self, display_manager: Any, config: Optional[Any] = None):
        """
        Initialize the plugin adapter.

        Args:
            display_manager: DisplayManager instance for fallback capture
            config: VegasModeConfig controlling trim behaviour. When omitted,
                trimming runs with the dataclass defaults, so existing callers
                and tests keep working unchanged.
        """
        self.display_manager = display_manager
        if config is None:
            from src.vegas_mode.config import VegasModeConfig
            config = VegasModeConfig()
        self.config = config
        # Handle both property and method access patterns
        self.display_width = (
            display_manager.width() if callable(display_manager.width)
            else display_manager.width
        )
        self.display_height = (
            display_manager.height() if callable(display_manager.height)
            else display_manager.height
        )

        # Cache for recently fetched content (prevents redundant fetch)
        self._content_cache: dict = {}
        self._cache_lock = threading.Lock()
        self._cache_ttl = 5.0  # Cache for 5 seconds

        # Per-plugin rotation offset, so a plugin whose content exceeds its
        # width budget shows a different slice on each cycle rather than
        # always the same opening items.
        self._item_offsets: dict = {}

        # What the matching entry in _item_offsets is an offset *into*, as
        # (kind, size). An offset only means anything against the content it
        # was derived from, and there are three incompatible kinds:
        #
        #   ('rows', n)  index into a list of n images
        #   ('cuts', n)  index into the n item boundaries of one image
        #   ('cols', w)  pixel column in a w-wide image with no item boundaries
        #
        # Without this the offsets were reused across kinds — a plugin that
        # returned one wide image on one fetch and several rows on the next had
        # a pixel column of 1400 read back as a row index — and across content
        # changes, where a column recorded against a 9,793px news strip pointed
        # into unrelated headlines once the strip refreshed to 9,505px.
        self._offset_shapes: dict = {}

        logger.info(
            "PluginAdapter initialized: display=%dx%d",
            self.display_width, self.display_height
        )

    def get_content(self, plugin: 'BasePlugin', plugin_id: str,
                    offscreen_only: bool = False) -> Optional[List[Image.Image]]:
        """
        Get scrollable content from a plugin.

        Tries get_vegas_content() first, falls back to display capture.

        Args:
            plugin: Plugin instance to get content from
            plugin_id: Plugin identifier for logging
            offscreen_only: Skip every path that touches the shared display
                canvas, for callers running off the render thread. The canvas
                and the matrix proxy are process-wide mutable state, so
                narrowing or capturing through them from another thread would
                corrupt the frame the render loop is pushing. Returns None when
                the plugin can only be served that way, leaving the caller to
                fetch it on the render thread.

        Returns:
            List of PIL Images representing plugin content, or None if no content
        """
        logger.info(
            "[%s] Getting content (class=%s)",
            plugin_id, plugin.__class__.__name__
        )

        # Check cache first
        cached = self._get_cached(plugin_id)
        if cached is not None:
            total_width = sum(img.width for img in cached)
            logger.info(
                "[%s] Using cached content: %d images, %dpx total",
                plugin_id, len(cached), total_width
            )
            return cached

        # Try native Vegas content method first
        has_native = hasattr(plugin, 'get_vegas_content')
        logger.info("[%s] Has get_vegas_content: %s", plugin_id, has_native)
        if has_native:
            content = self._get_native_content(plugin, plugin_id, offscreen_only)
            if content:
                total_width = sum(img.width for img in content)
                logger.info(
                    "[%s] Native content SUCCESS: %d images, %dpx total",
                    plugin_id, len(content), total_width
                )
                return self._finalize(content, plugin_id, 'native', plugin)
            logger.info("[%s] Native content returned None", plugin_id)

        # Try to get scroll_helper's cached image (for scrolling plugins like stocks/odds)
        has_scroll_helper = hasattr(plugin, 'scroll_helper')
        logger.info("[%s] Has scroll_helper: %s", plugin_id, has_scroll_helper)
        content = self._get_scroll_helper_content(plugin, plugin_id, offscreen_only)
        if content:
            total_width = sum(img.width for img in content)
            logger.info(
                "[%s] ScrollHelper content SUCCESS: %d images, %dpx total",
                plugin_id, len(content), total_width
            )
            return self._finalize(content, plugin_id, 'scroll_helper', plugin)
        if has_scroll_helper:
            logger.info("[%s] ScrollHelper content returned None", plugin_id)

        if offscreen_only:
            # Display capture needs the shared canvas; leave it to the caller.
            logger.info(
                "[%s] Needs display capture, deferring to the render thread",
                plugin_id
            )
            return None

        # Fall back to display capture
        logger.info("[%s] Trying fallback display capture...", plugin_id)
        content = self._capture_display_content(plugin, plugin_id)
        if content:
            total_width = sum(img.width for img in content)
            logger.info(
                "[%s] Fallback capture SUCCESS: %d images, %dpx total",
                plugin_id, len(content), total_width
            )
            return self._finalize(content, plugin_id, 'fallback', plugin)

        logger.warning(
            "[%s] NO CONTENT from any method (native=%s, scroll_helper=%s, fallback=tried)",
            plugin_id, has_native, has_scroll_helper
        )
        return None

    def _finalize(
        self, images: List[Image.Image], plugin_id: str, source: str,
        plugin: Optional['BasePlugin'] = None
    ) -> Optional[List[Image.Image]]:
        """
        Trim dead space off a segment, then cache it.

        Every content path funnels through here so trimming is applied
        uniformly. Previously only the scroll_helper path had its margins
        stripped, which left plugins that render onto a full-display canvas
        contributing their entire blank canvas to the ticker.

        Each image is trimmed independently because compose_scroll_content()
        treats every image as its own item and inserts separator_width between
        them — so a per-image trim is what makes that separator the real gap.

        Args:
            images: Raw content from one of the fetch paths
            plugin_id: Plugin identifier for logging
            source: Which path produced the content, for logging

        Returns:
            Trimmed image list, or None if nothing worth showing remains
        """
        if not self.config.auto_trim:
            # Trimming is off, but the width budget is a separate concern —
            # turning off margin cropping should not let one plugin hold the
            # panel for minutes. Skipping it here previously let a 14,848px
            # segment through untouched.
            kept = self._apply_width_budget(list(images), plugin_id, plugin)
            self._cache_content(plugin_id, kept)
            return kept

        original_width = sum(img.width for img in images)
        kept: List[Image.Image] = []
        dropped_blank = 0

        for img in images:
            result = trim_to_content(
                img,
                threshold=self.config.trim_threshold,
                padding=self.config.content_padding,
            )
            if result.is_blank:
                dropped_blank += 1
                continue
            kept.append(result.image)

        if not kept:
            logger.info(
                "[%s] All %d image(s) from %s were blank — contributing nothing",
                plugin_id, len(images), source
            )
            return None

        trimmed_width = sum(img.width for img in kept)

        if trimmed_width < self.config.min_plugin_width:
            logger.info(
                "[%s] Trimmed content %dpx is below min_plugin_width %dpx — skipping",
                plugin_id, trimmed_width, self.config.min_plugin_width
            )
            return None

        if trimmed_width != original_width or dropped_blank:
            logger.info(
                "[%s] Trimmed %s content: %dpx -> %dpx (%.0f%% reclaimed), "
                "%d image(s) kept, %d blank dropped",
                plugin_id, source, original_width, trimmed_width,
                100.0 * (original_width - trimmed_width) / original_width
                if original_width else 0.0,
                len(kept), dropped_blank
            )

        kept = self._apply_width_budget(kept, plugin_id, plugin)

        self._cache_content(plugin_id, kept)
        return kept

    def _capture(self):
        """
        Context manager suppressing hardware writes while plugin render code runs.

        Degrades to a no-op when the display manager predates capture_mode. As
        with _render_at, losing the suppression risks a visible flash, whereas
        raising would be swallowed by the broad handlers upstream and drop the
        plugin's content entirely — much worse.
        """
        capture_mode = getattr(self.display_manager, 'capture_mode', None)
        if capture_mode is None:
            logger.debug(
                "display_manager has no capture_mode(); plugin writes during "
                "content capture may reach the panel"
            )
            return nullcontext()
        return capture_mode()

    def _render_at(self, width: int):
        """
        Context manager narrowing the plugin-facing canvas to ``width``.

        Degrades to a no-op when the display manager predates render_size (a
        third-party or older test harness). Losing the narrowing is a cosmetic
        regression; raising here would be caught by the broad handlers upstream
        and silently drop the plugin's content entirely.
        """
        render_size = getattr(self.display_manager, 'render_size', None)
        if render_size is None:
            logger.debug(
                "display_manager has no render_size(); Vegas width requests "
                "will be ignored"
            )
            return nullcontext()
        return render_size(width)

    def resolve_render_width(self, plugin: 'BasePlugin', plugin_id: str) -> int:
        """
        Width to tell a plugin it has while it renders for the ticker.

        Resolution order, most specific first:
          1. the plugin's own ``vegas_width_pct`` config value
          2. the global ``vegas_scroll.render_width_pct``
          3. the full panel width

        A percentage rather than an absolute width so one setting travels
        across panel sizes.

        Args:
            plugin: Plugin instance, consulted for a per-plugin override
            plugin_id: Plugin identifier for logging

        Returns:
            Target width in pixels, never wider than the panel
        """
        pct = self.config.render_width_pct

        plugin_cfg = getattr(plugin, 'config', None)
        if isinstance(plugin_cfg, dict):
            raw = plugin_cfg.get('vegas_width_pct')
            if raw not in (None, ''):
                try:
                    candidate = int(raw)
                except (TypeError, ValueError):
                    logger.warning(
                        "[%s] Invalid vegas_width_pct %r, ignoring", plugin_id, raw)
                else:
                    if 10 <= candidate <= 100:
                        pct = candidate
                    else:
                        logger.warning(
                            "[%s] vegas_width_pct %d out of range 10-100, ignoring",
                            plugin_id, candidate)

        if pct >= 100:
            return self.display_width
        return max(1, int(self.display_width * pct / 100))

    def _row_gap(self, left: Image.Image, right: Image.Image) -> int:
        """
        Gap the compositor will insert between two of a plugin's rows.

        Mirrors RenderPipeline._join_plugin_rows so the width budget measures
        what will actually be rendered.
        """
        return separation_gap(
            left, right,
            target=max(0, self.config.min_content_separation),
            minimum=max(0, self.config.intra_plugin_gap),
            threshold=self.config.trim_threshold,
        )

    def _plugin_setting(self, plugin: 'BasePlugin', key: str):
        """Read a per-plugin config override, or None if absent."""
        plugin_cfg = getattr(plugin, 'config', None)
        if not isinstance(plugin_cfg, dict):
            return None
        value = plugin_cfg.get(key)
        return None if value in (None, '') else value

    def resolve_overflow_mode(self, plugin: 'BasePlugin', plugin_id: str) -> str:
        """
        How to handle content that exceeds this plugin's width budget.

        'rotate' advances a window each cycle so everything is seen eventually,
        which suits interchangeable items. 'truncate' always shows the start,
        which suits ordered content — a league table that shows ranks 1-6 and
        then resumes at 7 two rotations later reads as out of order, and nobody
        needs rank 23 in a ticker anyway.

        Per-plugin ``vegas_overflow`` wins over the global ``overflow_mode``.
        """
        raw = self._plugin_setting(plugin, 'vegas_overflow')
        if raw is not None:
            candidate = str(raw).strip().lower()
            if candidate in ('rotate', 'truncate'):
                return candidate
            logger.warning(
                "[%s] Invalid vegas_overflow %r, expected 'rotate' or 'truncate'",
                plugin_id, raw
            )
        return self.config.overflow_mode

    def _width_budget(self, plugin: Optional['BasePlugin'] = None,
                      plugin_id: str = '') -> int:
        """
        Maximum columns one plugin may occupy in a cycle. 0 means unlimited.

        A per-plugin ``vegas_max_width_screens`` overrides the global ratio, so
        content that has to stay whole can be given room (or uncapped with 0)
        without lifting the cap on every ticker.
        """
        ratio = self.config.max_plugin_width_ratio

        if plugin is not None:
            raw = self._plugin_setting(plugin, 'vegas_max_width_screens')
            if raw is not None:
                try:
                    candidate = float(raw)
                except (TypeError, ValueError):
                    logger.warning(
                        "[%s] Invalid vegas_max_width_screens %r, ignoring",
                        plugin_id, raw
                    )
                else:
                    if candidate >= 0:
                        ratio = candidate
                    else:
                        logger.warning(
                            "[%s] vegas_max_width_screens must be >= 0, got %s",
                            plugin_id, candidate
                        )

        if ratio <= 0:
            return 0
        return int(self.display_width * ratio)

    def _resume_offset(self, plugin_id: str, shape: Tuple[str, int]) -> int:
        """
        The plugin's stored rotation offset, if it still applies.

        An offset is only meaningful against content shaped the way it was
        when the offset was recorded. When the shape has changed — a different
        number of rows, a re-rendered strip with different item boundaries —
        the stored value points somewhere arbitrary, so rotation restarts.

        Args:
            plugin_id: Plugin identifier
            shape: (kind, size) describing what an offset would index into now

        Returns:
            The stored offset, or 0 when it no longer applies
        """
        if self._offset_shapes.get(plugin_id) != shape:
            if plugin_id in self._item_offsets:
                logger.info(
                    "[%s] Content is %s now, was %s — restarting the rotation "
                    "rather than resuming at a position that no longer means "
                    "anything", plugin_id, shape,
                    self._offset_shapes.get(plugin_id))
            self._item_offsets.pop(plugin_id, None)
            self._offset_shapes[plugin_id] = shape
            return 0
        return self._item_offsets.get(plugin_id, 0)

    def _record_offset(
        self, plugin_id: str, offset: int, shape: Tuple[str, int]
    ) -> None:
        """Store where the next window should resume, with what it indexes."""
        if offset:
            self._item_offsets[plugin_id] = offset
            self._offset_shapes[plugin_id] = shape
        else:
            # A wrapped-to-zero rotation is the same as no state at all, and
            # keeping the key would report a window as active when the next
            # pass starts from the top anyway.
            self._item_offsets.pop(plugin_id, None)
            self._offset_shapes.pop(plugin_id, None)

    def _clear_offset(self, plugin_id: str) -> None:
        """Forget any rotation state for a plugin."""
        self._item_offsets.pop(plugin_id, None)
        self._offset_shapes.pop(plugin_id, None)

    def _merge_trailing_runt(self, end: int, width: int, budget: int) -> int:
        """
        Extend a window to the end of the content when what would be left over
        is too small to be worth its own pass.

        Windows were placed by walking forward from the last one, which makes
        the final window whatever happens to remain. Measured on a live panel
        that produced a 1,840px stocks ticker splitting 1,492 + 348 — the
        second pass showing seven seconds of content before cutting, which
        reads as the display failing rather than as a rotation.

        Absorbing the remainder overruns the budget by less than one window
        floor, which is a better trade than a fragment: the budget is a guard
        against one plugin holding the panel for minutes, not a hard limit.

        Args:
            end: Column the window would otherwise end at
            width: Full content width
            budget: Width budget being applied

        Returns:
            ``end``, or ``width`` when the remainder is below the floor
        """
        remainder = width - end
        # Measured against the budget rather than the panel: snapping to item
        # boundaries means an ordinary window already lands short of the budget
        # (a 512px budget over 182px-pitch items yields 348px windows), so an
        # absolute floor would merge windows that were never fragments. Half a
        # budget separates "a short last pass" from "a sliver", and caps the
        # overrun this can cause at 1.5 budgets.
        floor = budget // 2
        if 0 < remainder < floor:
            return width
        return end

    def _apply_width_budget(
        self, images: List[Image.Image], plugin_id: str,
        plugin: Optional['BasePlugin'] = None
    ) -> List[Image.Image]:
        """
        Hold one plugin to its share of a cycle.

        A ticker returning 7,000px would otherwise own the panel for over two
        minutes, which defeats the point of a rotation. Overflow is deferred
        rather than discarded: the starting offset advances each time this
        plugin is fetched, so later items appear on subsequent cycles instead
        of never being seen.

        Args:
            images: Trimmed images for this plugin
            plugin_id: Plugin identifier, used to track its rotation offset

        Returns:
            Images that fit the budget, starting from the plugin's current
            rotation offset.
        """
        budget = self._width_budget(plugin, plugin_id)
        mode = (self.resolve_overflow_mode(plugin, plugin_id)
                if plugin is not None else self.config.overflow_mode)

        # Count the gaps the compositor will actually insert, not just the
        # pixels of the rows — otherwise a plugin with many rows quietly
        # occupies far more of the panel than its budget allows. These must use
        # the same measured rule as RenderPipeline._join_plugin_rows; assuming
        # the flat intra_plugin_gap here under-counted by up to
        # (min_content_separation - intra_plugin_gap) per row.
        total = sum(img.width for img in images) + sum(
            self._row_gap(images[i], images[i + 1]) for i in range(len(images) - 1)
        )

        if not budget or total <= budget:
            # Fits, so reset rotation — the whole segment is being shown.
            self._clear_offset(plugin_id)
            return images

        if len(images) == 1:
            return [self._crop_to_budget(images[0], budget, plugin_id, mode)]

        shape = ('rows', len(images))
        if mode == 'truncate':
            # Ordered content: always show from the top. Deliberately does not
            # advance the offset, so the same opening items appear every time
            # rather than the viewer being shown the middle of a ranked list.
            start = 0
        else:
            start = self._resume_offset(plugin_id, shape) % len(images)
        selected: List[Image.Image] = []
        used = 0
        consumed = 0

        # Walk forward from the rotation offset, taking whole items only, so a
        # cut never lands in the middle of one.
        #
        # A window may overrun the budget while it is still shorter than the
        # runt floor, for the same reason _merge_trailing_runt exists on the
        # single-image path: a pass far shorter than its neighbours reads as
        # the display failing rather than as a rotation. Rows of 450, 450 and
        # 100 against a 512px budget used to give the 100 a pass of its own --
        # two seconds against nine. Wrapping does not prevent that, because it
        # only helps when the row wrapped to actually fits.
        floor = budget // 2
        for step in range(len(images)):
            img = images[(start + step) % len(images)]
            cost = img.width
            if selected:
                cost += self._row_gap(selected[-1], img)
            if selected and used + cost > budget:
                # Keep the overrun bounded at the same 1.5 budgets the
                # single-image path allows. A next row too wide to absorb
                # leaves a short window standing -- better than a window of
                # 1.9 budgets, and the same trade the always-take-the-first
                # rule below already makes.
                if used >= floor or used + cost > budget + floor:
                    break
            selected.append(img)
            used += cost
            consumed += 1

        if mode == 'truncate':
            logger.info(
                "[%s] Width budget %dpx: showing the first %d of %d row(s) "
                "(%dpx incl. gaps); the rest are not shown (overflow=truncate)",
                plugin_id, budget, len(selected), len(images), used
            )
        else:
            self._record_offset(
                plugin_id, (start + consumed) % len(images), shape)
            logger.info(
                "[%s] Width budget %dpx: showing %d of %d row(s) (%dpx incl. gaps) "
                "from offset %d; remainder deferred to a later cycle",
                plugin_id, budget, len(selected), len(images), used, start
            )
        return selected

    def _crop_to_budget(
        self, img: Image.Image, budget: int, plugin_id: str,
        mode: str = 'rotate'
    ) -> Image.Image:
        """
        Narrow a single oversized image to the budget, advancing a window
        through it across cycles.

        The cut is snapped to the nearest blank column so it does not slice
        through a glyph or logo and leave half a character at the panel edge.

        Rotation is tracked as an index into the strip's item boundaries rather
        than as a pixel column, because a ticker re-renders between fetches. A
        column recorded against one render points at unrelated content in the
        next as soon as anything ahead of it changes width — a digit in a
        price, a shorter headline. The Nth boundary stays the Nth boundary.
        """
        # Cut only where the plugin left a real gap between items. Snapping to
        # any blank column used to pick the single-column gaps between
        # characters, splitting a word and orphaning its tail into the next
        # cycle — a lone "y" from "Wednesday" floating between two unrelated
        # plugins. Overshooting the budget is the lesser evil.
        min_run = max(2, self.config.min_cut_gap)
        gaps = blank_runs(img, min_run, self.config.trim_threshold)

        if not gaps:
            # No internal gaps means continuous content — a map, a chart, a
            # photo — where any column is as good as any other, so cut to the
            # budget exactly. The gap rule exists to protect discrete items
            # (words, ticker entries); it would be wrong to let a solid image
            # escape the cap in its name.
            #
            # With no items to index, the offset here has to stay a column, so
            # it is only reusable while the image keeps its width.
            shape = ('cols', img.width)
            offset = 0 if mode == 'truncate' else self._resume_offset(
                plugin_id, shape)
            end = self._merge_trailing_runt(
                min(offset + budget, img.width), img.width, budget)
            if mode != 'truncate':
                self._record_offset(
                    plugin_id, 0 if end >= img.width else end, shape)
            logger.info(
                "[%s] Width budget %dpx: cropped continuous %dpx image to "
                "[%d:%d] (no item gaps of %dpx+ to align to)%s",
                plugin_id, budget, img.width, offset, end, min_run,
                "" if mode != 'truncate' else "; showing the start only"
            )
            return img.crop((offset, 0, end, img.height))

        # Cut mid-gap so the content either side keeps some breathing room.
        cuts = sorted({0, img.width} | {(a + b) // 2 for a, b in gaps})

        shape = ('cuts', len(cuts))
        index = 0 if mode == 'truncate' else self._resume_offset(
            plugin_id, shape)
        # Clamped rather than wrapped: a stale index past the end means the
        # strip shrank, and restarting reads better than landing near the end.
        start_index = index if 0 <= index < len(cuts) - 1 else 0
        start = cuts[start_index]

        later = cuts[start_index + 1:]
        if not later:
            end = img.width
        else:
            within = [c for c in later if c <= start + budget]
            # No boundary inside the budget: take the next one and overrun,
            # because the alternative is cutting through an item.
            end = max(within) if within else min(later)
        end = self._merge_trailing_runt(end, img.width, budget)
        # Every candidate for `end` came from `cuts` (which includes img.width),
        # so this always resolves; the fallback is defensive only.
        end_index = cuts.index(end) if end in cuts else len(cuts) - 1

        if mode != 'truncate':
            # Next cycle resumes at the boundary this one stopped on; wrap when
            # the strip ends.
            self._record_offset(
                plugin_id, 0 if end >= img.width else end_index, shape)

        logger.info(
            "[%s] Width budget %dpx: cropped single %dpx image to [%d:%d] "
            "(%dpx) at item boundaries %d-%d of %d, %s",
            plugin_id, budget, img.width, start, end, end - start,
            start_index, end_index, len(cuts) - 1,
            "showing the start only (overflow=truncate)"
            if mode == 'truncate' else "window advances next cycle"
        )
        return img.crop((start, 0, end, img.height))

    def _get_native_content(
        self, plugin: 'BasePlugin', plugin_id: str, offscreen_only: bool = False
    ) -> Optional[List[Image.Image]]:
        """
        Get content via plugin's native get_vegas_content() method.

        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier

        Returns:
            List of images or None
        """
        try:
            logger.info("[%s] Native: calling get_vegas_content()", plugin_id)

            # Tell the plugin how much width the ticker wants it to use, and
            # narrow the canvas for the duration of the call. A plugin that
            # sizes its own images from display_manager.matrix.width picks up
            # the narrower value with no changes of its own; one that wants to
            # be explicit can read get_vegas_render_width().
            render_width = self.resolve_render_width(plugin, plugin_id)
            if render_width != self.display_width:
                logger.info(
                    "[%s] Native: requesting %dpx instead of %dpx",
                    plugin_id, render_width, self.display_width
                )

            plugin._vegas_render_width = render_width
            try:
                # capture_mode unconditionally, even at full width. Building
                # Vegas content is an off-screen operation, but a plugin is free
                # to call update_display() while doing it — and outside
                # capture_mode that write lands on the hardware, flashing the
                # panel mid-scroll. The narrowing context is separate because it
                # is a no-op at full width.
                if offscreen_only:
                    # _render_at swaps the shared canvas, so it is unsafe here.
                    # _vegas_render_width is set regardless: a plugin reading
                    # get_vegas_render_width() still gets its narrow size, and
                    # one that only reads matrix.width renders full width and is
                    # trimmed instead.
                    with self._capture():
                        result = plugin.get_vegas_content()
                else:
                    with self._capture(), self._render_at(render_width):
                        result = plugin.get_vegas_content()
            finally:
                plugin._vegas_render_width = None

            if result is None:
                logger.info("[%s] Native: get_vegas_content() returned None", plugin_id)
                return None

            # Normalize to list
            if isinstance(result, Image.Image):
                images = [result]
                logger.info(
                    "[%s] Native: got single Image %dx%d",
                    plugin_id, result.width, result.height
                )
            elif isinstance(result, (list, tuple)):
                images = list(result)
                logger.info(
                    "[%s] Native: got %d items in list/tuple",
                    plugin_id, len(images)
                )
            else:
                logger.warning(
                    "[%s] Native: unexpected return type: %s",
                    plugin_id, type(result).__name__
                )
                return None

            # Validate images
            valid_images = []
            for i, img in enumerate(images):
                if not isinstance(img, Image.Image):
                    logger.warning(
                        "[%s] Native: item[%d] is not an Image: %s",
                        plugin_id, i, type(img).__name__
                    )
                    continue

                logger.info(
                    "[%s] Native: item[%d] is %dx%d, mode=%s",
                    plugin_id, i, img.width, img.height, img.mode
                )

                # Ensure correct height
                if img.height != self.display_height:
                    logger.info(
                        "[%s] Native: resizing item[%d]: %dx%d -> %dx%d",
                        plugin_id, i, img.width, img.height,
                        img.width, self.display_height
                    )
                    img = img.resize(
                        (img.width, self.display_height),
                        Image.Resampling.LANCZOS
                    )

                # Convert to RGB if needed
                if img.mode != 'RGB':
                    img = img.convert('RGB')

                valid_images.append(img)

            if valid_images:
                total_width = sum(img.width for img in valid_images)
                logger.info(
                    "[%s] Native: SUCCESS - %d images, %dpx total width",
                    plugin_id, len(valid_images), total_width
                )
                return valid_images

            logger.info("[%s] Native: no valid images after validation", plugin_id)
            return None

        except (AttributeError, TypeError, ValueError, OSError) as e:
            logger.exception(
                "[%s] Native: ERROR calling get_vegas_content(): %s",
                plugin_id, e
            )
            return None

    def _get_scroll_helper_content(
        self, plugin: 'BasePlugin', plugin_id: str, offscreen_only: bool = False
    ) -> Optional[List[Image.Image]]:
        """
        Get content from plugin's scroll_helper if available.

        Many scrolling plugins (stocks, odds) use a ScrollHelper that caches
        their full scrolling image. This method extracts that image for Vegas
        mode instead of falling back to single-frame capture.

        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier

        Returns:
            List with the cached scroll image, or None if not available
        """
        try:
            # Check for scroll_helper with cached_image
            scroll_helper = getattr(plugin, 'scroll_helper', None)
            if scroll_helper is None:
                logger.debug("[%s] No scroll_helper attribute", plugin_id)
                return None

            logger.info(
                "[%s] Found scroll_helper: %s",
                plugin_id, type(scroll_helper).__name__
            )

            cached_image = getattr(scroll_helper, 'cached_image', None)
            if cached_image is None:
                logger.info(
                    "[%s] scroll_helper.cached_image is None, triggering content generation",
                    plugin_id
                )
                if offscreen_only:
                    # Generating it calls display(), which needs the canvas.
                    logger.info(
                        "[%s] scroll_helper cache empty; deferring generation "
                        "to the render thread", plugin_id
                    )
                    return None
                # Try to trigger scroll content generation
                cached_image = self._trigger_scroll_content_generation(
                    plugin, plugin_id, scroll_helper
                )
                if cached_image is None:
                    return None

            if not isinstance(cached_image, Image.Image):
                logger.info(
                    "[%s] scroll_helper.cached_image is not an Image: %s",
                    plugin_id, type(cached_image).__name__
                )
                return None

            logger.info(
                "[%s] scroll_helper.cached_image found: %dx%d, mode=%s",
                plugin_id, cached_image.width, cached_image.height, cached_image.mode
            )

            # Copy the image to prevent modification
            img = cached_image.copy()

            # Plugins that build their own ticker image via this shared
            # ScrollHelper's create_scrolling_image() get a solid-black
            # leading margin exactly `display_width` columns wide baked in
            # (scroll_helper.py's "initial gap before first item"). Vegas mode
            # adds its own leading gap/separator around every item already,
            # so leaving this in stacks a second, uncontrolled blank margin on
            # top of vegas_scroll.separator_width — making this plugin's
            # transitions look inconsistent with plugins that provide content
            # via get_vegas_content() (which carries no such margin). Strip it
            # here so every plugin contributes only its real content and the
            # gap between items is governed solely by separator_width.
            img = self._strip_scroll_padding(img, scroll_helper, plugin_id)

            # Ensure correct height
            if img.height != self.display_height:
                logger.info(
                    "[%s] Resizing scroll_helper content: %dx%d -> %dx%d",
                    plugin_id, img.width, img.height,
                    img.width, self.display_height
                )
                img = img.resize(
                    (img.width, self.display_height),
                    Image.Resampling.LANCZOS
                )

            # Convert to RGB if needed
            if img.mode != 'RGB':
                img = img.convert('RGB')

            logger.info(
                "[%s] ScrollHelper content ready: %dx%d",
                plugin_id, img.width, img.height
            )

            return [img]

        except (AttributeError, TypeError, ValueError, OSError):
            logger.exception("[%s] Error getting scroll_helper content", plugin_id)
            return None

    def _strip_scroll_padding(
        self, img: Image.Image, scroll_helper: Any, plugin_id: str
    ) -> Image.Image:
        """
        Crop off a plugin's own leading/trailing blank margins, if present.

        create_scrolling_image() always pads the *start* of its cached image
        with exactly `scroll_helper.display_width` columns of solid black
        (0, 0, 0) ("initial gap before first item"). Some ticker-style plugins
        also pad the *end* of their own cached image (e.g. so their standalone
        display exits cleanly before looping). Vegas mode already adds its own
        gap/separator around every item, so either margin left in place stacks
        an extra, uncontrolled blank stretch on top of `separator_width` —
        only when running inside Vegas mode does this matter, since the
        plugin's own standalone display still wants that margin. Detect solid
        black margins up to `scroll_helper.display_width` wide on each edge and
        crop them here. Images built via set_scrolling_image() (no such
        margins) are left untouched.

        Args:
            img: Captured scroll_helper.cached_image (already copied)
            scroll_helper: The plugin's ScrollHelper instance
            plugin_id: Plugin identifier for logging

        Returns:
            img, cropped on whichever edge(s) had a matching blank margin
        """
        pad_width = getattr(scroll_helper, 'display_width', None)
        if not isinstance(pad_width, int) or pad_width <= 0 or pad_width >= img.width:
            return img

        def is_solid_black(strip: Image.Image) -> bool:
            return strip.convert('RGB').getextrema() == ((0, 0), (0, 0), (0, 0))

        left = pad_width if is_solid_black(img.crop((0, 0, pad_width, img.height))) else 0
        right = (
            pad_width
            if is_solid_black(img.crop((img.width - pad_width, 0, img.width, img.height)))
            else 0
        )

        if not left and not right:
            return img

        # Degenerate case (e.g. an all-black cached image): don't crop past
        # zero width, just leave the image as-is.
        if left + right >= img.width:
            return img

        cropped = img.crop((left, 0, img.width - right, img.height))

        # Both edges matching at once is a much stronger signal of genuine
        # baked-in padding than a single edge (which has a small chance of
        # coinciding with real all-black content, e.g. a dark logo touching
        # one boundary). Log that case at warning level so an unexpected
        # double-edge crop is easy to spot in the field.
        log = logger.warning if (left and right) else logger.info
        log(
            "[%s] Stripping scroll_helper padding (left=%dpx, right=%dpx): %dpx -> %dpx",
            plugin_id, left, right, img.width, cropped.width
        )
        return cropped

    def _trigger_scroll_content_generation(
        self, plugin: 'BasePlugin', plugin_id: str, scroll_helper: Any
    ) -> Optional[Image.Image]:
        """
        Trigger scroll content generation for plugins that haven't built it yet.

        Tries multiple approaches:
        1. _create_scrolling_display() - stocks plugin pattern
        2. display(force_clear=True) - general pattern that populates scroll cache

        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier
            scroll_helper: Plugin's scroll_helper instance

        Returns:
            The generated cached_image or None
        """
        original_image = None
        try:
            # Save display state to restore after
            original_image = self.display_manager.image.copy()

            with self._capture():
                # Method 1: Try _create_scrolling_display (stocks pattern)
                if hasattr(plugin, '_create_scrolling_display'):
                    logger.info(
                        "[%s] Triggering via _create_scrolling_display()",
                        plugin_id
                    )
                    try:
                        plugin._create_scrolling_display()
                        cached_image = getattr(scroll_helper, 'cached_image', None)
                        if cached_image is not None and isinstance(cached_image, Image.Image):
                            logger.info(
                                "[%s] _create_scrolling_display() SUCCESS: %dx%d",
                                plugin_id, cached_image.width, cached_image.height
                            )
                            return cached_image
                    except (AttributeError, TypeError, ValueError, OSError):
                        logger.exception(
                            "[%s] _create_scrolling_display() failed", plugin_id
                        )

                # Method 2: Try display(force_clear=True) which typically builds scroll content
                if hasattr(plugin, 'display'):
                    logger.info(
                        "[%s] Triggering via display(force_clear=True)",
                        plugin_id
                    )
                    try:
                        self.display_manager.clear()
                        plugin.display(force_clear=True)
                        cached_image = getattr(scroll_helper, 'cached_image', None)
                        if cached_image is not None and isinstance(cached_image, Image.Image):
                            logger.info(
                                "[%s] display(force_clear=True) SUCCESS: %dx%d",
                                plugin_id, cached_image.width, cached_image.height
                            )
                            return cached_image
                        logger.info(
                            "[%s] display(force_clear=True) did not populate cached_image",
                            plugin_id
                        )
                    except (AttributeError, TypeError, ValueError, OSError):
                        logger.exception(
                            "[%s] display(force_clear=True) failed", plugin_id
                        )

            logger.info(
                "[%s] Could not trigger scroll content generation",
                plugin_id
            )
            return None

        except (AttributeError, TypeError, ValueError, OSError):
            logger.exception("[%s] Error triggering scroll content", plugin_id)
            return None

        finally:
            # Restore original display state
            if original_image is not None:
                self.display_manager.image = original_image

    def _capture_display_content(
        self, plugin: 'BasePlugin', plugin_id: str
    ) -> Optional[List[Image.Image]]:
        """
        Capture content by calling plugin.display() and grabbing the frame.

        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier

        Returns:
            List with single captured image, or None
        """
        original_image = None
        try:
            # Save current display state
            original_image = self.display_manager.image.copy()
            logger.info("[%s] Fallback: saved original display state", plugin_id)

            # Ensure plugin has fresh data before capturing
            has_update_data = hasattr(plugin, 'update_data')
            logger.info("[%s] Fallback: has update_data=%s", plugin_id, has_update_data)
            if has_update_data:
                try:
                    plugin.update_data()
                    logger.info("[%s] Fallback: update_data() called", plugin_id)
                except (AttributeError, RuntimeError, OSError):
                    logger.exception("[%s] Fallback: update_data() failed", plugin_id)

            # Clear and call plugin display — use capture_mode to suppress hardware writes
            # that plugins may trigger internally via update_display().
            #
            # render_size narrows the canvas the plugin lays out against, so a
            # plugin that spreads across the whole panel produces a compact
            # arrangement rather than one that has to be cropped afterwards.
            render_width = self.resolve_render_width(plugin, plugin_id)
            if render_width != self.display_width:
                logger.info(
                    "[%s] Fallback: rendering at %dpx instead of %dpx",
                    plugin_id, render_width, self.display_width
                )

            with self._capture(), self._render_at(render_width):
                self.display_manager.clear()
                logger.info("[%s] Fallback: display cleared, calling display()", plugin_id)

                # First try without force_clear (some plugins behave better this way)
                try:
                    plugin.display()
                    logger.info("[%s] Fallback: display() called successfully", plugin_id)
                except TypeError:
                    # Plugin may require force_clear argument
                    logger.info("[%s] Fallback: display() failed, trying with force_clear=True", plugin_id)
                    plugin.display(force_clear=True)

                # Capture the result
                captured = self.display_manager.image.copy()

            logger.info(
                "[%s] Fallback: captured frame %dx%d, mode=%s",
                plugin_id, captured.width, captured.height, captured.mode
            )

            # Check if captured image has content (not all black)
            is_blank, bright_ratio = self._is_blank_image(captured, return_ratio=True)
            logger.info(
                "[%s] Fallback: brightness check - %.3f%% bright pixels (threshold=0.5%%)",
                plugin_id, bright_ratio * 100
            )

            if is_blank:
                logger.info(
                    "[%s] Fallback: first capture blank, retrying with force_clear",
                    plugin_id
                )
                # Try once more with force_clear=True
                with self._capture(), self._render_at(render_width):
                    self.display_manager.clear()
                    plugin.display(force_clear=True)
                    captured = self.display_manager.image.copy()

                is_blank, bright_ratio = self._is_blank_image(captured, return_ratio=True)
                logger.info(
                    "[%s] Fallback: retry brightness - %.3f%% bright pixels",
                    plugin_id, bright_ratio * 100
                )

                if is_blank:
                    logger.warning(
                        "[%s] Fallback: BLANK IMAGE after retry (%.3f%% bright, size=%dx%d)",
                        plugin_id, bright_ratio * 100,
                        captured.width, captured.height
                    )
                    return None

            # Convert to RGB if needed
            if captured.mode != 'RGB':
                captured = captured.convert('RGB')

            logger.info(
                "[%s] Fallback: SUCCESS - captured %dx%d",
                plugin_id, captured.width, captured.height
            )

            return [captured]

        except (AttributeError, TypeError, ValueError, OSError, RuntimeError) as e:
            logger.exception(
                "[%s] Fallback: ERROR capturing display: %s",
                plugin_id, e
            )
            return None

        finally:
            # Always restore original image to prevent display corruption
            if original_image is not None:
                self.display_manager.image = original_image
                logger.debug("[%s] Fallback: restored original display state", plugin_id)

    def _is_blank_image(
        self, img: Image.Image, return_ratio: bool = False
    ) -> Union[bool, Tuple[bool, float]]:
        """
        Check if an image is essentially blank (all black or nearly so).

        Uses histogram-based detection which is more reliable than
        point sampling for content that may be positioned anywhere.

        Args:
            img: Image to check
            return_ratio: If True, return tuple of (is_blank, bright_ratio)

        Returns:
            True if image is blank, or tuple (is_blank, bright_ratio) if return_ratio=True
        """
        # Convert to RGB for consistent checking
        if img.mode != 'RGB':
            img = img.convert('RGB')

        # Use histogram to check for any non-black content
        # This is more reliable than point sampling
        histogram = img.histogram()

        # RGB histogram: 256 values per channel
        # Check if there's any significant brightness in any channel
        total_bright_pixels = 0
        threshold = 15  # Minimum brightness to count as "content"

        for channel_offset in [0, 256, 512]:  # R, G, B
            for brightness in range(threshold, 256):
                total_bright_pixels += histogram[channel_offset + brightness]

        # If less than 0.5% of pixels have any brightness, consider blank
        total_pixels = img.width * img.height
        bright_ratio = total_bright_pixels / (total_pixels * 3)  # Normalize across channels

        is_blank = bright_ratio < 0.005  # Less than 0.5% bright pixels

        if return_ratio:
            return is_blank, bright_ratio
        return is_blank

    def _get_cached(self, plugin_id: str) -> Optional[List[Image.Image]]:
        """Get cached content if still valid."""
        with self._cache_lock:
            if plugin_id not in self._content_cache:
                return None

            cached_time, content = self._content_cache[plugin_id]
            if time.time() - cached_time > self._cache_ttl:
                del self._content_cache[plugin_id]
                return None

            return content

    def _cache_content(self, plugin_id: str, content: List[Image.Image]) -> None:
        """Cache content for a plugin."""
        # Make copies to prevent mutation (done outside lock to minimize hold time)
        cached_content = [img.copy() for img in content]

        with self._cache_lock:
            # Periodic cleanup of expired entries to prevent memory leak
            self._cleanup_expired_cache_locked()
            self._content_cache[plugin_id] = (time.time(), cached_content)

    def _cleanup_expired_cache_locked(self) -> None:
        """Remove expired entries from cache. Must be called with _cache_lock held."""
        current_time = time.time()
        expired_keys = [
            key for key, (cached_time, _) in self._content_cache.items()
            if current_time - cached_time > self._cache_ttl
        ]
        for key in expired_keys:
            del self._content_cache[key]

    def invalidate_cache(self, plugin_id: Optional[str] = None) -> None:
        """
        Invalidate cached content.

        Args:
            plugin_id: Specific plugin to invalidate, or None for all
        """
        with self._cache_lock:
            if plugin_id:
                self._content_cache.pop(plugin_id, None)
            else:
                self._content_cache.clear()

    def invalidate_plugin_scroll_cache(
        self, plugin: 'BasePlugin', plugin_id: str
    ) -> bool:
        """
        Drop a plugin's own cached scroll image so its visual is rebuilt.

        Invalidating only this adapter's cache is not enough. A plugin that
        composes a scroll strip hands back the *same* image every time until its
        own cache is cleared — the sports plugins' ``get_vegas_content()``
        regenerates only "if the cache is empty" — so without this a segment
        keeps rendering whatever data it was first built from. That is how a
        game that was live last night can still be displayed as live the next
        morning.

        Two layouts to cover: a helper directly on the plugin (stocks, news,
        odds-ticker) and one owned by a scroll-display manager (the sports
        scoreboards). ``cached_image`` and ``cached_array`` must be cleared
        together, since the array is the image's numpy mirror and code paths
        read whichever is convenient.

        Returns:
            True if a cache was found and cleared.
        """
        cleared = False
        for owner in (plugin, getattr(plugin, '_scroll_manager', None),
                      getattr(plugin, 'scroll_manager', None)):
            if owner is None:
                continue
            helper = getattr(owner, 'scroll_helper', None)
            if helper is None:
                continue
            try:
                if getattr(helper, 'cached_image', None) is not None:
                    helper.cached_image = None
                    cleared = True
                if getattr(helper, 'cached_array', None) is not None:
                    helper.cached_array = None
                    cleared = True
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "[%s] Could not clear scroll cache on %s",
                    plugin_id, type(owner).__name__
                )
        if cleared:
            logger.debug("[%s] Cleared plugin scroll cache", plugin_id)
        return cleared

    def get_content_type(self, plugin: 'BasePlugin', plugin_id: str) -> str:
        """
        Get the type of content a plugin provides.

        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier

        Returns:
            'multi' for multiple items, 'static' for single frame, 'none' for excluded
        """
        if hasattr(plugin, 'get_vegas_content_type'):
            try:
                return plugin.get_vegas_content_type()
            except (AttributeError, TypeError, ValueError):
                logger.exception(
                    "Error calling get_vegas_content_type() on %s",
                    plugin_id
                )

        # Default to static for plugins without explicit type
        return 'static'

    def cleanup(self) -> None:
        """Clean up resources."""
        with self._cache_lock:
            self._content_cache.clear()
        logger.debug("PluginAdapter cleanup complete")
