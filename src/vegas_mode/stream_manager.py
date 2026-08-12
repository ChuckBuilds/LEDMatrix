"""
Stream Manager for Vegas Mode

Manages plugin content streaming with look-ahead buffering. Maintains a queue
of plugin content that's ready to be rendered, prefetching 1-2 plugins ahead
of the current scroll position.

Supports three display modes:
- SCROLL: Continuous scrolling content
- FIXED_SEGMENT: Fixed block that scrolls by
- STATIC: Pause scroll to display (marked for coordinator handling)
"""

import logging
import threading
import time
from typing import Optional, List, Dict, Any, Deque, Tuple, TYPE_CHECKING
from collections import deque
from dataclasses import dataclass, field
from PIL import Image

from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.plugin_adapter import PluginAdapter
from src.plugin_system.base_plugin import VegasDisplayMode

if TYPE_CHECKING:
    from src.plugin_system.plugin_manager import PluginManager

logger = logging.getLogger(__name__)


@dataclass
class ContentSegment:
    """Represents a segment of scrollable content from a plugin."""
    plugin_id: str
    images: List[Image.Image]
    total_width: int
    display_mode: VegasDisplayMode = field(default=VegasDisplayMode.FIXED_SEGMENT)
    fetched_at: float = field(default_factory=time.time)
    is_stale: bool = False

    @property
    def image_count(self) -> int:
        return len(self.images)

    @property
    def is_static(self) -> bool:
        """Check if this segment should trigger a static pause."""
        return self.display_mode == VegasDisplayMode.STATIC


class StreamManager:
    """
    Manages streaming of plugin content for Vegas scroll mode.

    Key responsibilities:
    - Maintain ordered list of plugins to stream
    - Prefetch content 1-2 plugins ahead of current position
    - Handle plugin data updates via double-buffer swap
    - Manage content lifecycle and staleness
    """

    def __init__(
        self,
        config: VegasModeConfig,
        plugin_manager: 'PluginManager',
        plugin_adapter: PluginAdapter
    ):
        """
        Initialize the stream manager.

        Args:
            config: Vegas mode configuration
            plugin_manager: Plugin manager for accessing plugins
            plugin_adapter: Adapter for getting plugin content
        """
        self.config = config
        self.plugin_manager = plugin_manager
        self.plugin_adapter = plugin_adapter

        # Content queue (double-buffered)
        self._active_buffer: Deque[ContentSegment] = deque()
        self._staging_buffer: Deque[ContentSegment] = deque()
        self._buffer_lock = threading.RLock()  # RLock for reentrant access

        # Plugin rotation state
        self._ordered_plugins: List[str] = []
        self._current_index: int = 0
        self._prefetch_index: int = 0

        # Update tracking
        self._pending_updates: Dict[str, bool] = {}
        self._last_refresh: float = 0.0
        self._refresh_interval: float = 30.0  # Refresh plugin list every 30s

        # Statistics
        self.stats = {
            'segments_fetched': 0,
            'segments_served': 0,
            'buffer_swaps': 0,
            'fetch_errors': 0,
        }

        logger.info("StreamManager initialized with buffer_ahead=%d", config.buffer_ahead)

    def initialize(self) -> bool:
        """
        Initialize the stream manager with current plugin list.

        Returns:
            True if initialized successfully with at least one plugin
        """
        self._refresh_plugin_list()

        if not self._ordered_plugins:
            logger.warning("No plugins available for Vegas scroll")
            return False

        # Fill the buffer to a whole cycle's worth of plugins. This used to be
        # buffer_ahead + 1, which conflated prefetch depth with cycle size and
        # meant a 20-plugin install only showed 3 plugins before recomposing.
        self._prefetch_content(
            count=min(self.config.plugins_per_cycle, len(self._ordered_plugins)))

        logger.info(
            "StreamManager initialized with %d plugins, %d segments buffered",
            len(self._ordered_plugins), len(self._active_buffer)
        )
        return len(self._active_buffer) > 0

    def get_next_segment(self) -> Optional[ContentSegment]:
        """
        Get the next content segment for rendering.

        Returns:
            ContentSegment or None if buffer is empty
        """
        with self._buffer_lock:
            if not self._active_buffer:
                # Try to fetch more content
                self._prefetch_content(count=1)
                if not self._active_buffer:
                    return None

            segment = self._active_buffer.popleft()
            self.stats['segments_served'] += 1

            # Trigger prefetch to maintain buffer
            self._ensure_buffer_filled()

            return segment

    def peek_next_segment(self) -> Optional[ContentSegment]:
        """
        Peek at the next segment without removing it.

        Returns:
            ContentSegment or None if buffer is empty
        """
        with self._buffer_lock:
            if self._active_buffer:
                return self._active_buffer[0]
            return None

    def get_buffer_status(self) -> Dict[str, Any]:
        """Get current buffer status for monitoring."""
        with self._buffer_lock:
            return {
                'active_count': len(self._active_buffer),
                'staging_count': len(self._staging_buffer),
                'total_plugins': len(self._ordered_plugins),
                'current_index': self._current_index,
                'prefetch_index': self._prefetch_index,
                'stats': self.stats.copy(),
            }

    def get_active_plugin_ids(self) -> List[str]:
        """
        Get list of plugin IDs currently in the active buffer.

        Thread-safe accessor for render pipeline.

        Returns:
            List of plugin IDs in buffer order
        """
        with self._buffer_lock:
            return [seg.plugin_id for seg in self._active_buffer]

    def mark_plugin_updated(self, plugin_id: str) -> None:
        """
        Mark a plugin as having updated data.

        Called when a plugin's data changes. Triggers content refresh
        for that plugin in the staging buffer.

        Args:
            plugin_id: Plugin that was updated
        """
        with self._buffer_lock:
            self._pending_updates[plugin_id] = True

        logger.debug("Plugin %s marked for update", plugin_id)

    def invalidate_pending_updates(self) -> List[str]:
        """
        Drop cached content for plugins whose data changed, without refetching.

        The continuous-scroll counterpart to :meth:`process_updates`. That method
        belongs to the swap path: it refetches immediately and merges into the
        active buffer, which continuous mode bypasses entirely, and doing that
        work on the render thread would hitch the scroll.

        Here it is enough to clear the caches and let the plugin come round in
        the rotation, which recomposes it from current data a moment later. Left
        uncalled, ``_pending_updates`` simply accumulates and no visual ever
        refreshes — a game that was live last night keeps being drawn as live.

        Returns:
            The plugin ids whose caches were dropped.
        """
        with self._buffer_lock:
            if not self._pending_updates:
                return []
            updated = list(self._pending_updates.keys())
            self._pending_updates.clear()

        plugins = getattr(self.plugin_manager, 'plugins', {})
        for plugin_id in updated:
            try:
                self.plugin_adapter.invalidate_cache(plugin_id)
                plugin = plugins.get(plugin_id)
                if plugin is not None:
                    self.plugin_adapter.invalidate_plugin_scroll_cache(
                        plugin, plugin_id)
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "[%s] Could not invalidate cached content", plugin_id)

        logger.info(
            "Vegas: dropped cached content for %d updated plugin(s): %s",
            len(updated), ', '.join(updated)
        )
        return updated

    def has_pending_updates(self) -> bool:
        """Check if any plugins have pending updates awaiting processing."""
        with self._buffer_lock:
            return len(self._pending_updates) > 0

    def has_pending_updates_for_visible_segments(self) -> bool:
        """Check if pending updates affect plugins currently in the active buffer."""
        with self._buffer_lock:
            if not self._pending_updates:
                return False
            active_ids = {
                seg.plugin_id for seg in self._active_buffer if seg.images
            }
            return bool(active_ids & self._pending_updates.keys())

    def process_updates(self) -> None:
        """
        Process pending plugin updates.

        Performs in-place update of segments in the active buffer,
        preserving non-updated plugins and their order.
        """
        with self._buffer_lock:
            if not self._pending_updates:
                return

            updated_plugins = list(self._pending_updates.keys())
            self._pending_updates.clear()

        # Fetch fresh content for each updated plugin (outside lock for slow ops)
        refreshed_segments = {}
        for plugin_id in updated_plugins:
            self.plugin_adapter.invalidate_cache(plugin_id)

            # Clear the plugin's scroll_helper cache so the visual is rebuilt
            # from fresh data (affects stocks, news, odds-ticker, etc.)
            plugin = None
            if hasattr(self.plugin_manager, 'plugins'):
                plugin = self.plugin_manager.plugins.get(plugin_id)
            if plugin:
                self.plugin_adapter.invalidate_plugin_scroll_cache(plugin, plugin_id)

            segment = self._fetch_plugin_content(plugin_id)
            if segment:
                refreshed_segments[plugin_id] = segment

        # In-place merge: replace segments in active buffer
        with self._buffer_lock:
            # Build new buffer preserving order, replacing updated segments
            new_buffer: Deque[ContentSegment] = deque()
            seen_plugins: set = set()

            for segment in self._active_buffer:
                if segment.plugin_id in refreshed_segments:
                    # Replace with refreshed segment (only once per plugin)
                    if segment.plugin_id not in seen_plugins:
                        new_buffer.append(refreshed_segments[segment.plugin_id])
                        seen_plugins.add(segment.plugin_id)
                    # Skip duplicate entries for same plugin
                else:
                    # Keep non-updated segment
                    new_buffer.append(segment)

            self._active_buffer = new_buffer

        logger.debug("Processed in-place updates for %d plugins", len(updated_plugins))

    def swap_buffers(self) -> None:
        """
        Swap active and staging buffers.

        Called when staging buffer has updated content ready.
        """
        with self._buffer_lock:
            if self._staging_buffer:
                # True swap: staging becomes active, old active is discarded
                self._active_buffer, self._staging_buffer = self._staging_buffer, deque()
                self.stats['buffer_swaps'] += 1
                logger.debug("Swapped buffers, active now has %d segments", len(self._active_buffer))

    def refresh(self) -> None:
        """
        Refresh the plugin list and content.

        Called periodically to pick up new plugins or config changes.
        """
        current_time = time.time()
        if current_time - self._last_refresh < self._refresh_interval:
            return

        self._last_refresh = current_time
        old_count = len(self._ordered_plugins)
        self._refresh_plugin_list()

        if len(self._ordered_plugins) != old_count:
            logger.info(
                "Plugin list refreshed: %d -> %d plugins",
                old_count, len(self._ordered_plugins)
            )

    def _refresh_plugin_list(self) -> None:
        """Refresh the ordered list of plugins from plugin manager."""
        logger.info("=" * 60)
        logger.info("REFRESHING PLUGIN LIST FOR VEGAS SCROLL")
        logger.info("=" * 60)

        # Get all enabled plugins
        available_plugins = []

        if hasattr(self.plugin_manager, 'plugins'):
            logger.info(
                "Checking %d loaded plugins for Vegas scroll",
                len(self.plugin_manager.plugins)
            )
            for plugin_id, plugin in self.plugin_manager.plugins.items():
                has_enabled = hasattr(plugin, 'enabled')
                is_enabled = getattr(plugin, 'enabled', False)
                logger.info(
                    "[%s] class=%s, has_enabled=%s, enabled=%s",
                    plugin_id, plugin.__class__.__name__, has_enabled, is_enabled
                )
                if has_enabled and is_enabled:
                    # Check vegas content type - skip 'none' unless in STATIC mode
                    content_type = self.plugin_adapter.get_content_type(plugin, plugin_id)

                    # Also check display mode - STATIC plugins should be included
                    # even if their content_type is 'none'
                    display_mode = VegasDisplayMode.FIXED_SEGMENT
                    try:
                        display_mode = plugin.get_vegas_display_mode()
                    except Exception:
                        # Plugin error should not abort refresh; use default mode
                        logger.exception(
                            "[%s] (%s) get_vegas_display_mode() failed, using default",
                            plugin_id, plugin.__class__.__name__
                        )

                    logger.info(
                        "[%s] content_type=%s, display_mode=%s",
                        plugin_id, content_type, display_mode.value
                    )

                    if content_type != 'none' or display_mode == VegasDisplayMode.STATIC:
                        available_plugins.append(plugin_id)
                        logger.info("[%s] --> INCLUDED in Vegas scroll", plugin_id)
                    else:
                        logger.info("[%s] --> EXCLUDED from Vegas scroll", plugin_id)
                else:
                    logger.info("[%s] --> SKIPPED (not enabled)", plugin_id)

        else:
            logger.warning(
                "plugin_manager does not have plugins attribute: %s",
                type(self.plugin_manager).__name__
            )

        # Apply ordering from config (outside lock for potentially slow operation)
        ordered_plugins = self.config.get_ordered_plugins(available_plugins)
        logger.info(
            "Vegas scroll plugin list: %d available -> %d ordered",
            len(available_plugins), len(ordered_plugins)
        )
        logger.info("Ordered plugins: %s", ordered_plugins)

        ordered_plugins = self._apply_priority_weights(ordered_plugins)

        # Atomically update shared state under lock to avoid races with prefetchers
        with self._buffer_lock:
            self._ordered_plugins = ordered_plugins
            # Reset indices if needed
            if self._current_index >= len(self._ordered_plugins):
                self._current_index = 0
            if self._prefetch_index >= len(self._ordered_plugins):
                self._prefetch_index = 0

        logger.info("=" * 60)

    def _plugin_weight(self, plugin_id: str) -> int:
        """Slots per cycle for one plugin.

        A plugin may answer for itself via get_vegas_priority_weight() -- the
        only way favorite-team awareness can reach here, since the core can see
        that a game is live but not whose. When it declines (returns None, the
        default), live content earns ``live_weight`` and everything else 1.
        """
        plugin = None
        try:
            plugin = self.plugin_manager.plugins.get(plugin_id)
        except (AttributeError, TypeError):
            return 1
        if plugin is None:
            return 1

        try:
            if hasattr(plugin, 'get_vegas_priority_weight'):
                declared = plugin.get_vegas_priority_weight()
                if declared is not None:
                    return max(1, min(10, int(declared)))
        except Exception:
            # Deliberately falls through to the core's own live check rather
            # than demoting to 1. The plugin's weight calculation is broken,
            # but has_live_priority() and has_live_content() are separate
            # methods guarded separately below -- a plugin that genuinely has
            # a live game should still get live_weight for it.
            logger.exception("[%s] get_vegas_priority_weight() failed", plugin_id)

        try:
            if (hasattr(plugin, 'has_live_priority')
                    and hasattr(plugin, 'has_live_content')
                    and plugin.has_live_priority()
                    and plugin.has_live_content()):
                return self.config.live_weight
        except Exception:
            logger.exception("[%s] live-content check failed", plugin_id)
        return 1

    def _apply_priority_weights(self, ordered: List[str]) -> List[str]:
        """Expand the rotation so weighted plugins take several turns per cycle.

        Smooth Weighted Round-Robin, the same scheduler the sports plugins use
        to rotate their own games: a plugin of weight N appears N times per
        cycle, and the repeats are spaced through the cycle rather than
        clumped, so a live score is never three-in-a-row followed by a long
        silence.

        Returns the input unchanged when nothing is weighted, which is both the
        common case and the pre-existing behaviour.
        """
        if not ordered or not self.config.live_in_ticker:
            return ordered

        weights = {pid: self._plugin_weight(pid) for pid in ordered}
        total = sum(weights.values())
        if total <= len(ordered):
            return ordered          # nothing boosted; plain round robin

        current = {pid: 0 for pid in ordered}
        schedule: List[str] = []
        for _ in range(total):
            for pid in ordered:
                current[pid] += weights[pid]
            picked = max(current, key=lambda p: current[p])
            current[picked] -= total
            schedule.append(picked)

        schedule = self._unclump_seam(schedule)

        boosted = {p: w for p, w in weights.items() if w > 1}
        logger.info(
            "Vegas rotation weighted: %d slots for %d plugins (boosted: %s)",
            len(schedule), len(ordered), boosted)
        return schedule

    @staticmethod
    def _unclump_seam(schedule: List[str]) -> List[str]:
        """Stop the heaviest plugin sitting on both ends of the cycle.

        Smooth Weighted Round-Robin spaces repeats well *within* a pass, but
        it schedules the heaviest item first and often last too. The strip
        loops, so those two are neighbours: the one place the marquee shows
        the same plugin twice running is the seam between cycles.

        Rotating the list cannot fix this. Rotation preserves the cyclic order
        exactly, so it only moves where the seam is drawn, not the adjacency
        itself. The trailing entry has to be swapped with one from the middle
        whose neighbours differ from it, which breaks the pair without
        creating another.

        Left alone when no such position exists -- a rotation short enough or
        lopsided enough to have none is one where the plugin is unavoidably
        adjacent to itself anyway.
        """
        if len(schedule) < 3 or schedule[0] != schedule[-1]:
            return schedule

        repeated = schedule[-1]
        size = len(schedule)
        elsewhere = [i for i, p in enumerate(schedule[:-1]) if p == repeated]

        def clearance(j: int) -> int:
            """Cyclic distance from j to the nearest other appearance."""
            return min(min((i - j) % size, (j - i) % size) for i in elsewhere)

        candidates = [
            j for j in range(1, size - 1)
            if schedule[j] != repeated
            and schedule[j - 1] != repeated
            and schedule[j + 1] != repeated
        ]
        if not candidates:
            return schedule

        # Drop it into the widest gap rather than the first slot that fits.
        # Taking the first one undoes the spacing this whole function exists
        # to protect: on a 28-slot rotation it moved a repeat from a gap of 7
        # to a gap of 2, which is more clumped than the seam ever was.
        best = max(candidates, key=clearance) if elsewhere else candidates[0]
        schedule = list(schedule)
        schedule[best], schedule[-1] = schedule[-1], schedule[best]
        return schedule

    def _prefetch_content(self, count: int = 1) -> None:
        """
        Prefetch content for upcoming plugins.

        Args:
            count: Number of plugins to prefetch
        """
        with self._buffer_lock:
            if not self._ordered_plugins:
                return

            for _ in range(count):
                if len(self._active_buffer) >= self.config.plugins_per_cycle:
                    break

                # Ensure index is valid (guard against empty list)
                num_plugins = len(self._ordered_plugins)
                if num_plugins == 0:
                    break

                plugin_id = self._ordered_plugins[self._prefetch_index]

                # Release lock for potentially slow content fetch
                self._buffer_lock.release()
                try:
                    segment = self._fetch_plugin_content(plugin_id)
                finally:
                    self._buffer_lock.acquire()

                if segment:
                    self._active_buffer.append(segment)

                # Revalidate num_plugins after reacquiring lock (may have changed)
                num_plugins = len(self._ordered_plugins)
                if num_plugins == 0:
                    break

                # Advance prefetch index (thread-safe within lock)
                self._prefetch_index = (self._prefetch_index + 1) % num_plugins

    def _fetch_plugin_content(self, plugin_id: str) -> Optional[ContentSegment]:
        """
        Fetch content from a specific plugin.

        Args:
            plugin_id: Plugin to fetch from

        Returns:
            ContentSegment or None if fetch failed
        """
        try:
            logger.info("=" * 60)
            logger.info("[%s] FETCHING CONTENT", plugin_id)
            logger.info("=" * 60)

            # Get plugin instance
            if not hasattr(self.plugin_manager, 'plugins'):
                logger.warning("[%s] plugin_manager has no plugins attribute", plugin_id)
                return None

            plugin = self.plugin_manager.plugins.get(plugin_id)
            if not plugin:
                logger.warning("[%s] Plugin not found in plugin_manager.plugins", plugin_id)
                return None

            logger.info(
                "[%s] Plugin found: class=%s, enabled=%s",
                plugin_id, plugin.__class__.__name__, getattr(plugin, 'enabled', 'N/A')
            )

            # Get display mode from plugin
            display_mode = VegasDisplayMode.FIXED_SEGMENT
            try:
                display_mode = plugin.get_vegas_display_mode()
                logger.info("[%s] Display mode: %s", plugin_id, display_mode.value)
            except (AttributeError, TypeError) as e:
                logger.info(
                    "[%s] get_vegas_display_mode() not available: %s (using FIXED_SEGMENT)",
                    plugin_id, e
                )

            # For STATIC mode, we create a placeholder segment
            # The actual content will be displayed by coordinator during pause
            if display_mode == VegasDisplayMode.STATIC:
                # Create minimal placeholder - coordinator handles actual display
                segment = ContentSegment(
                    plugin_id=plugin_id,
                    images=[],  # No images needed for static pause
                    total_width=0,
                    display_mode=display_mode
                )
                self.stats['segments_fetched'] += 1
                logger.info(
                    "[%s] Created STATIC placeholder (pause trigger)",
                    plugin_id
                )
                return segment

            # Get content via adapter for SCROLL/FIXED_SEGMENT modes
            logger.info("[%s] Calling plugin_adapter.get_content()...", plugin_id)
            images = self.plugin_adapter.get_content(plugin, plugin_id)
            if not images:
                logger.warning("[%s] NO CONTENT RETURNED from plugin_adapter", plugin_id)
                return None

            # Calculate total width
            total_width = sum(img.width for img in images)

            segment = ContentSegment(
                plugin_id=plugin_id,
                images=images,
                total_width=total_width,
                display_mode=display_mode
            )

            self.stats['segments_fetched'] += 1
            logger.info(
                "[%s] SEGMENT CREATED: %d images, %dpx total, mode=%s",
                plugin_id, len(images), total_width, display_mode.value
            )
            logger.info("=" * 60)

            return segment

        except Exception:
            logger.exception("[%s] ERROR fetching content", plugin_id)
            self.stats['fetch_errors'] += 1
            return None

    def _refresh_plugin_content(self, plugin_id: str) -> None:
        """
        Refresh content for a specific plugin into staging buffer.

        Args:
            plugin_id: Plugin to refresh
        """
        # Invalidate cached content
        self.plugin_adapter.invalidate_cache(plugin_id)

        # Fetch fresh content
        segment = self._fetch_plugin_content(plugin_id)

        if segment:
            with self._buffer_lock:
                self._staging_buffer.append(segment)
            logger.debug("Refreshed content for %s in staging buffer", plugin_id)

    def _ensure_buffer_filled(self) -> None:
        """
        Top the buffer back up after segments have been served.

        buffer_ahead is the low-water mark only; plugins_per_cycle is the
        ceiling and is enforced inside _prefetch_content.
        """
        low_water = min(self.config.buffer_ahead, self.config.plugins_per_cycle)
        if len(self._active_buffer) < low_water:
            self._prefetch_content(count=low_water - len(self._active_buffer))

    def get_all_content_for_composition(self) -> List[Image.Image]:
        """
        Get all buffered content as a flat list of images.

        Skips STATIC segments as they don't have images to compose.

        Prefer get_grouped_content_for_composition(): flattening loses the
        plugin boundaries, which is what tells the compositor where a
        separator belongs and where it does not.

        Returns:
            List of all images in buffer order
        """
        all_images = []
        for _plugin_id, images in self.get_grouped_content_for_composition():
            all_images.extend(images)
        return all_images

    def get_grouped_content_for_composition(self) -> List[Tuple[str, List[Image.Image]]]:
        """
        Get buffered content grouped by the plugin that produced it.

        The grouping matters: separator_width is meant to mark the handoff from
        one plugin to the next, not to sit between every row a single plugin
        contributes. A per-row ticker like the F1 scoreboard returns over a
        hundred images that it renders 4px apart internally, so flattening them
        into one list and applying a uniform gap forced 32px between each of
        its rows — both inconsistent with how the plugin looks standalone, and
        a large hidden addition to the width it occupies.

        Skips STATIC segments, which trigger a pause rather than contributing
        scroll content, and segments left with no images.

        Returns:
            List of (plugin_id, images) in buffer order
        """
        grouped: List[Tuple[str, List[Image.Image]]] = []
        with self._buffer_lock:
            for segment in self._active_buffer:
                if segment.display_mode == VegasDisplayMode.STATIC:
                    continue
                if not segment.images:
                    continue
                grouped.append((segment.plugin_id, list(segment.images)))
        return grouped

    def take_next_group(
        self, count: Optional[int] = None, offscreen_only: bool = False
    ) -> List[Tuple[str, Optional[List[Image.Image]]]]:
        """
        Fetch and hand over the next slice of the rotation.

        For continuous scrolling, where the strip is extended rather than
        replaced. Advances the rotation index so plugins come round in order
        across an unbroken strip, and bypasses the active buffer entirely — that
        buffer exists to stage a *replacement* cycle, which continuous mode has
        no use for.

        Args:
            count: Number of plugins to gather, defaulting to plugins_per_cycle
            offscreen_only: Only use content paths that avoid the shared display
                canvas, for use off the render thread

        Returns:
            Ordered list of (plugin_id, images). ``images`` is None when the
            plugin could not be served under ``offscreen_only``, so the caller
            can fetch just those on the render thread while keeping the order.
        """
        if count is None:
            count = self.config.plugins_per_cycle

        self.refresh()

        with self._buffer_lock:
            if not self._ordered_plugins:
                return []
            total = len(self._ordered_plugins)
            ids = []
            for _ in range(min(max(1, count), total)):
                ids.append(self._ordered_plugins[self._prefetch_index])
                self._prefetch_index = (self._prefetch_index + 1) % total

        plugins = getattr(self.plugin_manager, 'plugins', {})
        group: List[Tuple[str, Optional[List[Image.Image]]]] = []

        for plugin_id in ids:
            plugin = plugins.get(plugin_id)
            if not plugin:
                continue
            try:
                images = self.plugin_adapter.get_content(
                    plugin, plugin_id, offscreen_only=offscreen_only)
            except Exception:
                logger.exception("[%s] ERROR fetching content", plugin_id)
                self.stats['fetch_errors'] += 1
                continue
            if images:
                self.stats['segments_fetched'] += 1
            group.append((plugin_id, images if images else None))

        return group

    def advance_cycle(self) -> None:
        """
        Advance to next cycle by clearing the active buffer.

        Called when a scroll cycle completes to allow fresh content
        to be fetched for the next cycle. Does not reset indices,
        so prefetching continues from the current position in the
        plugin order.
        """
        with self._buffer_lock:
            consumed_count = len(self._active_buffer)
            self._active_buffer.clear()
            logger.debug("Advanced cycle, cleared %d segments", consumed_count)

    def reset(self) -> None:
        """Reset the stream manager state."""
        with self._buffer_lock:
            self._active_buffer.clear()
            self._staging_buffer.clear()
            self._current_index = 0
            self._prefetch_index = 0
            self._pending_updates.clear()

        self.plugin_adapter.invalidate_cache()
        logger.info("StreamManager reset")

    def cleanup(self) -> None:
        """Clean up resources."""
        self.reset()
        self.plugin_adapter.cleanup()
        logger.debug("StreamManager cleanup complete")
