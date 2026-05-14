"""
Render Pipeline for Vegas Mode

Handles high-FPS (125 FPS) rendering with double-buffering for smooth scrolling.
Uses the existing ScrollHelper for numpy-optimized scroll operations.
"""

import logging
import time
import threading
from collections import deque
from typing import Optional, List, Any, Dict, Deque, TYPE_CHECKING
from PIL import Image
import numpy as np

from src.common.scroll_helper import ScrollHelper
from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.stream_manager import StreamManager, ContentSegment

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class RenderPipeline:
    """
    High-performance render pipeline for Vegas scroll mode.

    Key responsibilities:
    - Compose content segments into scrollable image
    - Manage scroll position and velocity
    - Handle 125 FPS rendering loop
    - Double-buffer for hot-swap during updates
    - Track scroll cycle completion
    """

    def __init__(
        self,
        config: VegasModeConfig,
        display_manager: Any,
        stream_manager: StreamManager
    ):
        """
        Initialize the render pipeline.

        Args:
            config: Vegas mode configuration
            display_manager: DisplayManager for rendering
            stream_manager: StreamManager for content
        """
        self.config = config
        self.display_manager = display_manager
        self.stream_manager = stream_manager
        self.sync_manager = None        # Optional DisplaySyncManager — set by coordinator
        self.sync_follower_left = True  # True = follower is LEFT of leader (default)
        self._sync_send_interval = 1.0 / 90  # raw bytes are cheap; 90fps > follower render rate
        self._last_sync_send = 0.0

        # Display dimensions (handle both property and method access patterns)
        self.display_width = (
            display_manager.width() if callable(display_manager.width)
            else display_manager.width
        )
        self.display_height = (
            display_manager.height() if callable(display_manager.height)
            else display_manager.height
        )

        # ScrollHelper for optimized scrolling
        self.scroll_helper = ScrollHelper(
            self.display_width,
            self.display_height,
            logger
        )

        # Configure scroll helper
        self._configure_scroll_helper()

        # Double-buffer for composed images
        self._active_scroll_image: Optional[Image.Image] = None
        self._staging_scroll_image: Optional[Image.Image] = None
        self._buffer_lock = threading.Lock()

        # Render state
        self._is_rendering = False
        self._cycle_complete = False
        self._segments_in_scroll: List[str] = []  # Plugin IDs in current scroll

        # Timing
        self._last_frame_time = 0.0
        self._frame_interval = config.get_frame_interval()
        self._cycle_start_time = 0.0

        # Statistics
        self.stats = {
            'frames_rendered': 0,
            'scroll_cycles': 0,
            'composition_count': 0,
            'hot_swaps': 0,
            'avg_frame_time_ms': 0.0,
        }
        self._frame_times: Deque[float] = deque(maxlen=100)  # Efficient fixed-size buffer

        logger.info(
            "RenderPipeline initialized: %dx%d @ %d FPS",
            self.display_width, self.display_height, config.target_fps
        )

    def _configure_scroll_helper(self) -> None:
        """Configure ScrollHelper with current settings."""
        self.scroll_helper.set_frame_based_scrolling(self.config.frame_based_scrolling)
        self.scroll_helper.set_scroll_delay(self.config.scroll_delay)

        # Config scroll_speed is always pixels per second, but ScrollHelper
        # interprets it differently based on frame_based_scrolling mode:
        # - Frame-based: pixels per frame step
        # - Time-based: pixels per second
        if self.config.frame_based_scrolling:
            # Convert pixels/second to pixels/frame
            # pixels_per_frame = pixels_per_second * seconds_per_frame
            pixels_per_frame = self.config.scroll_speed * self.config.scroll_delay
            self.scroll_helper.set_scroll_speed(pixels_per_frame)
        else:
            self.scroll_helper.set_scroll_speed(self.config.scroll_speed)
        self.scroll_helper.set_dynamic_duration_settings(
            enabled=self.config.dynamic_duration_enabled,
            min_duration=self.config.min_cycle_duration,
            max_duration=self.config.max_cycle_duration,
            buffer=0.1  # 10% buffer
        )

    def compose_scroll_content(self) -> bool:
        """
        Compose content from stream manager into scrollable image.

        Returns:
            True if composition successful
        """
        try:
            # Get all buffered content
            images = self.stream_manager.get_all_content_for_composition()

            if not images:
                logger.warning("No content available for composition")
                return False

            # Add separator gaps between images
            content_with_gaps = []
            for i, img in enumerate(images):
                content_with_gaps.append(img)

            # Create scrolling image via ScrollHelper
            self.scroll_helper.create_scrolling_image(
                content_items=content_with_gaps,
                item_gap=self.config.separator_width,
                element_gap=0
            )

            # Verify scroll image was created successfully
            if not self.scroll_helper.cached_image:
                logger.error("ScrollHelper failed to create cached image")
                return False

            # Store reference to composed image
            with self._buffer_lock:
                self._active_scroll_image = self.scroll_helper.cached_image

            # Track which plugins are in this scroll (get safely via buffer status)
            self._segments_in_scroll = self.stream_manager.get_active_plugin_ids()

            self.stats['composition_count'] += 1
            self._cycle_start_time = time.time()
            self._cycle_complete = False

            logger.info(
                "Composed scroll image: %dx%d, %d plugins, %d items",
                self.scroll_helper.cached_image.width if self.scroll_helper.cached_image else 0,
                self.display_height,
                len(self._segments_in_scroll),
                len(images)
            )

            return True

        except (ValueError, TypeError, OSError, RuntimeError):
            # Expected errors from image operations, scroll helper, or bad data
            logger.exception("Error composing scroll content")
            return False

    def render_frame(self) -> bool:
        """
        Render a single frame to the display.

        Should be called at ~125 FPS (8ms intervals).

        Returns:
            True if frame was rendered, False if no content
        """
        frame_start = time.time()

        try:
            if not self.scroll_helper.cached_image:
                return False

            # Update scroll position
            self.scroll_helper.update_scroll_position()

            # Determine if the cycle is done.
            #
            # scroll_helper considers a cycle complete only after
            # total_distance_scrolled >= total_scroll_width + display_width.
            # That extra display_width of travel causes a "wrap-around" phase
            # where scroll_position resets to ~0 and the first plugin's content
            # re-enters from the right — the user sees this 2-3 s of re-entry
            # as "a plugin partially displaying before the next one starts."
            #
            # We end the cycle as soon as total_distance_scrolled reaches
            # total_scroll_width (the wrap-around point), before any second-pass
            # content becomes visible.  The scroll_helper's own is_scroll_complete()
            # check is kept as a fallback for any edge-cases where that threshold
            # is never hit.
            at_wrap_point = (
                not self._cycle_complete and
                self.scroll_helper.total_distance_scrolled >= self.scroll_helper.total_scroll_width
            )

            if at_wrap_point or self.scroll_helper.is_scroll_complete():
                if not self._cycle_complete:
                    self._cycle_complete = True
                    self.stats['scroll_cycles'] += 1
                    logger.info(
                        "Scroll cycle complete after %.1fs",
                        time.time() - self._cycle_start_time
                    )
                    # Push blank immediately so the hardware never shows any
                    # post-wrap content while the coordinator recomposes the
                    # next cycle (~100 ms).
                    try:
                        from PIL import Image as _Image
                        blank = _Image.new('RGB', (self.display_width, self.display_height))
                        self.display_manager.image = blank
                        self.display_manager.update_display()
                    except Exception:
                        logger.exception("Failed to write blank frame to display at cycle end")
                return True  # Cycle done; coordinator starts new cycle next frame

            # Get visible portion
            visible_frame = self.scroll_helper.get_visible_portion()
            if not visible_frame:
                return False

            # Render to display
            self.display_manager.image = visible_frame
            self.display_manager.update_display()

            # Multi-display sync: send scroll position to follower.
            # The follower renders from its own cached_array (kept identical to the
            # leader's via TCP image transfer at each new_cycle) at scroll_x ± display_width.
            if self.sync_manager:
                now = time.time()
                if now - self._last_sync_send >= self._sync_send_interval:
                    self._last_sync_send = now
                    self.sync_manager.send_scroll_x(self.scroll_helper.scroll_position)

            # Update scrolling state
            self.display_manager.set_scrolling_state(True)

            # Track statistics
            self.stats['frames_rendered'] += 1
            frame_time = time.time() - frame_start
            self._track_frame_time(frame_time)

            return True

        except (ValueError, TypeError, OSError, RuntimeError):
            # Expected errors from scroll helper or display manager operations
            logger.exception("Error rendering frame")
            return False

    def _track_frame_time(self, frame_time: float) -> None:
        """Track frame timing for statistics."""
        self._frame_times.append(frame_time)  # deque with maxlen auto-removes old entries

        if self._frame_times:
            self.stats['avg_frame_time_ms'] = (
                sum(self._frame_times) / len(self._frame_times) * 1000
            )

    def is_cycle_complete(self) -> bool:
        """Check if current scroll cycle is complete."""
        return self._cycle_complete

    def should_recompose(self) -> bool:
        """
        Check if scroll content should be recomposed.

        Returns True when:
        - Cycle is complete and we should start fresh
        - Staging buffer has new content
        """
        if self._cycle_complete:
            return True

        # When multi-display sync is active, defer mid-cycle hot swaps until the
        # cycle ends naturally. Hot swaps block the render loop for 15-30ms while
        # the image is rebuilt, causing a freeze+jump that the follower perceives
        # as a speed-up. Deferring to cycle boundaries keeps transitions clean.
        # Staging buffer content is still pre-loaded; it just applies at cycle end.
        if self.sync_manager is not None:
            return False

        # Check if we need more content in the buffer
        buffer_status = self.stream_manager.get_buffer_status()
        if buffer_status['staging_count'] > 0:
            return True

        return False

    def hot_swap_content(self) -> bool:
        """
        Hot-swap to new composed content.

        Called when staging buffer has updated content.
        Swaps atomically to prevent visual glitches.

        Returns:
            True if swap occurred
        """
        try:
            # Snapshot position before swap so we can reposition after.
            # The new image has completely different content — if scroll_position
            # is left unchanged it lands at an arbitrary mid-content point in the
            # new image, causing a visible jump on both displays.
            old_width = self.scroll_helper.total_scroll_width
            old_pos = self.scroll_helper.scroll_position

            # Process any pending updates
            self.stream_manager.process_updates()
            self.stream_manager.swap_buffers()

            # Recompose with updated content
            if self.compose_scroll_content():
                # Map scroll position proportionally into the new image width so
                # we resume at the same relative progress through the content.
                # This keeps the visual tempo consistent and avoids the jump that
                # occurred when old scroll_position landed arbitrarily in new image.
                new_width = self.scroll_helper.total_scroll_width
                if old_width > 0 and new_width > 0:
                    ratio = (old_pos % old_width) / old_width
                    self.scroll_helper.scroll_position = ratio * new_width
                else:
                    self.scroll_helper.scroll_position = 0.0

                self.stats['hot_swaps'] += 1
                logger.debug(
                    "Hot-swap completed: scroll repositioned %.0f→%.0f (%.1f%% of new %dpx image)",
                    old_pos, self.scroll_helper.scroll_position,
                    (self.scroll_helper.scroll_position / new_width * 100) if new_width else 0,
                    new_width,
                )
                return True

            return False

        except (ValueError, TypeError, OSError, RuntimeError):
            # Expected errors from stream manager or composition operations
            logger.exception("Error during hot-swap")
            return False

    def start_new_cycle(self) -> bool:
        """
        Start a new scroll cycle.

        Fetches fresh content and recomposes.

        Returns:
            True if new cycle started successfully
        """
        # Reset scroll position
        self.scroll_helper.reset_scroll()
        self._cycle_complete = False

        # Clear buffer from previous cycle so new content is fetched
        self.stream_manager.advance_cycle()

        # Refresh stream content (picks up plugin list changes)
        self.stream_manager.refresh()

        # Reinitialize stream (fills buffer with fresh content)
        if not self.stream_manager.initialize():
            logger.warning("Failed to reinitialize stream for new cycle")
            return False

        # Compose new scroll content
        result = self.compose_scroll_content()

        if result and self.sync_manager:
            # When sync is active, start the leader at display_width instead of 0.
            # This skips the initial black gap so the leader immediately shows content.
            # The follower starts at position 0 (the gap) which looks like a clean
            # blank transition rather than near-end content wrapping around.
            self.scroll_helper.scroll_position = float(self.display_width)

        if result and self.sync_manager:
            # Signal follower that a new cycle started (triggers its own rebuild)
            self.sync_manager.send_new_cycle()
            # Push the actual scroll image over TCP so follower has identical pixels.
            # Done in a background thread to not block the render loop (~15ms transfer).
            if self.scroll_helper.cached_image is not None:
                import threading as _t
                _t.Thread(
                    target=self.sync_manager.send_scroll_image,
                    args=(self.scroll_helper.cached_image,),
                    daemon=True, name="sync-image-push"
                ).start()

        return result

    def get_current_scroll_info(self) -> Dict[str, Any]:
        """Get current scroll state information."""
        scroll_info = self.scroll_helper.get_scroll_info()
        return {
            **scroll_info,
            'cycle_complete': self._cycle_complete,
            'plugins_in_scroll': self._segments_in_scroll,
            'stats': self.stats.copy(),
        }

    def get_scroll_position(self) -> int:
        """
        Get current scroll position.

        Used by coordinator to save position before static pause.

        Returns:
            Current scroll position in pixels
        """
        return int(self.scroll_helper.scroll_position)

    def set_scroll_position(self, position: int) -> None:
        """
        Set scroll position.

        Used by coordinator to restore position after static pause.

        Args:
            position: Scroll position in pixels
        """
        self.scroll_helper.scroll_position = float(position)

    def update_config(self, new_config: VegasModeConfig) -> None:
        """
        Update render pipeline configuration.

        Args:
            new_config: New configuration to apply
        """
        old_fps = self.config.target_fps
        self.config = new_config
        self._frame_interval = new_config.get_frame_interval()

        # Reconfigure scroll helper
        self._configure_scroll_helper()

        if old_fps != new_config.target_fps:
            logger.info("FPS target updated: %d -> %d", old_fps, new_config.target_fps)

    def reset(self) -> None:
        """Reset the render pipeline state."""
        self.scroll_helper.reset_scroll()
        self.scroll_helper.clear_cache()

        with self._buffer_lock:
            self._active_scroll_image = None
            self._staging_scroll_image = None

        self._cycle_complete = False
        self._segments_in_scroll = []
        self._frame_times = deque(maxlen=100)

        self.display_manager.set_scrolling_state(False)

        logger.info("RenderPipeline reset")

    def cleanup(self) -> None:
        """Clean up resources."""
        self.reset()
        self.display_manager.set_scrolling_state(False)
        logger.debug("RenderPipeline cleanup complete")

    def get_dynamic_duration(self) -> float:
        """Get the calculated dynamic duration for current content."""
        return float(self.scroll_helper.get_dynamic_duration())
