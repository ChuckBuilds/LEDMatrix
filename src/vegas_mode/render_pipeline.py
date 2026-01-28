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

        # Display dimensions
        self.display_width = display_manager.width
        self.display_height = display_manager.height

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
        self.scroll_helper.set_scroll_speed(self.config.scroll_speed)
        self.scroll_helper.set_scroll_delay(self.config.scroll_delay)
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

            # Check if cycle is complete
            if self.scroll_helper.is_scroll_complete():
                if not self._cycle_complete:
                    self._cycle_complete = True
                    self.stats['scroll_cycles'] += 1
                    logger.info(
                        "Scroll cycle complete after %.1fs",
                        time.time() - self._cycle_start_time
                    )

            # Get visible portion
            visible_frame = self.scroll_helper.get_visible_portion()
            if not visible_frame:
                return False

            # Render to display
            self.display_manager.image = visible_frame
            self.display_manager.update_display()

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
            # Process any pending updates
            self.stream_manager.process_updates()
            self.stream_manager.swap_buffers()

            # Recompose with updated content
            if self.compose_scroll_content():
                self.stats['hot_swaps'] += 1
                logger.debug("Hot-swap completed")
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

        # Refresh stream content
        self.stream_manager.refresh()

        # Reinitialize stream if needed
        if not self.stream_manager.get_buffer_status()['active_count']:
            if not self.stream_manager.initialize():
                logger.warning("Failed to reinitialize stream for new cycle")
                return False

        # Compose new scroll content
        return self.compose_scroll_content()

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
