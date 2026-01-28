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
    from src.plugin_system.base_plugin import BasePlugin
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

        # Prefetch initial content
        self._prefetch_content(count=min(self.config.buffer_ahead + 1, len(self._ordered_plugins)))

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
        # Get all enabled plugins
        available_plugins = []

        if hasattr(self.plugin_manager, 'plugins'):
            logger.debug(
                "Checking %d loaded plugins for Vegas scroll",
                len(self.plugin_manager.plugins)
            )
            for plugin_id, plugin in self.plugin_manager.plugins.items():
                has_enabled = hasattr(plugin, 'enabled')
                is_enabled = getattr(plugin, 'enabled', False)
                logger.debug(
                    "[%s] has_enabled=%s, enabled=%s",
                    plugin_id, has_enabled, is_enabled
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

                    if content_type != 'none' or display_mode == VegasDisplayMode.STATIC:
                        available_plugins.append(plugin_id)
                        logger.debug("[%s] Added to Vegas scroll", plugin_id)
                    else:
                        logger.debug(
                            "[%s] Excluded: content_type=%s, display_mode=%s",
                            plugin_id, content_type, display_mode
                        )

        else:
            logger.warning(
                "plugin_manager does not have plugins attribute: %s",
                type(self.plugin_manager).__name__
            )

        # Apply ordering from config (outside lock for potentially slow operation)
        ordered_plugins = self.config.get_ordered_plugins(available_plugins)
        logger.debug(
            "Vegas scroll: %d available -> %d ordered plugins",
            len(available_plugins), len(ordered_plugins)
        )

        # Atomically update shared state under lock to avoid races with prefetchers
        with self._buffer_lock:
            self._ordered_plugins = ordered_plugins
            # Reset indices if needed
            if self._current_index >= len(self._ordered_plugins):
                self._current_index = 0
            if self._prefetch_index >= len(self._ordered_plugins):
                self._prefetch_index = 0

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
                if len(self._active_buffer) >= self.config.buffer_ahead + 1:
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
            # Get plugin instance
            if not hasattr(self.plugin_manager, 'plugins'):
                return None

            plugin = self.plugin_manager.plugins.get(plugin_id)
            if not plugin:
                logger.warning("Plugin %s not found", plugin_id)
                return None

            # Get display mode from plugin
            display_mode = VegasDisplayMode.FIXED_SEGMENT
            try:
                display_mode = plugin.get_vegas_display_mode()
            except (AttributeError, TypeError) as e:
                logger.debug("[%s] get_vegas_display_mode() not available: %s", plugin_id, e)

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
                logger.debug(
                    "Created STATIC placeholder for %s (pause trigger)",
                    plugin_id
                )
                return segment

            # Get content via adapter for SCROLL/FIXED_SEGMENT modes
            images = self.plugin_adapter.get_content(plugin, plugin_id)
            if not images:
                logger.debug("No content from plugin %s", plugin_id)
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
            logger.debug(
                "Fetched segment from %s: %d images, %dpx total, mode=%s",
                plugin_id, len(images), total_width, display_mode.value
            )

            return segment

        except Exception:
            logger.exception("Error fetching content from %s", plugin_id)
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
        """Ensure buffer has enough content prefetched."""
        if len(self._active_buffer) < self.config.buffer_ahead:
            needed = self.config.buffer_ahead - len(self._active_buffer)
            self._prefetch_content(count=needed)

    def get_all_content_for_composition(self) -> List[Image.Image]:
        """
        Get all buffered content as a flat list of images.

        Used when composing the full scroll image.
        Skips STATIC segments as they don't have images to compose.

        Returns:
            List of all images in buffer order
        """
        all_images = []
        with self._buffer_lock:
            for segment in self._active_buffer:
                # Skip STATIC segments - they trigger pauses, not scroll content
                if segment.display_mode != VegasDisplayMode.STATIC:
                    all_images.extend(segment.images)
        return all_images

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
