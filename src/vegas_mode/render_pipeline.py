"""
Render Pipeline for Vegas Mode

Handles high-FPS (125 FPS) rendering with double-buffering for smooth scrolling.
Uses the existing ScrollHelper for numpy-optimized scroll operations.
"""

import logging
import os
import time
import threading
from collections import deque
from typing import Optional, List, Any, Dict, Deque, TYPE_CHECKING
from PIL import Image

from src.common.scroll_helper import ScrollHelper
from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.geometry import separation_gap
from src.vegas_mode.stream_manager import StreamManager

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

    # Minimum gap between fetches of canvas-bound plugins, so their individual
    # stalls land in separate moments rather than one run of hitches.
    DEFERRED_DRAIN_INTERVAL = 2.0

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

        # Group prepared off the render thread, waiting to be appended.
        self._prepared_group = None
        # Plugins that need the shared canvas, appended one at a time.
        self._deferred_queue: List[str] = []
        self._last_drain_time = 0.0
        self._prefetch_thread: Optional[threading.Thread] = None
        self._prefetch_lock = threading.Lock()

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
            # Content grouped by plugin, so a separator can be placed at the
            # plugin boundaries only.
            grouped = self.stream_manager.get_grouped_content_for_composition()

            if not grouped:
                logger.warning("No content available for composition")
                return False

            # Collapse each plugin's rows into a single block, joined by
            # intra_plugin_gap. ScrollHelper applies one uniform gap between the
            # items it is given, so handing it one item per plugin is what makes
            # separator_width mean "between plugins" instead of "between every
            # row". Without this, a per-row ticker such as the F1 scoreboard got
            # the full separator between each of its ~116 rows.
            blocks = []
            total_rows = 0
            for plugin_id, images in grouped:
                total_rows += len(images)
                blocks.append(self._join_plugin_rows(images))

            # Create scrolling image via ScrollHelper.
            #
            # lead_gap is explicit because ScrollHelper otherwise prepends a
            # full display width of black — appropriate for a standalone ticker
            # scrolling in from off-screen, but in Vegas mode it is charged
            # once per cycle and reads as the panel switching off.
            self.scroll_helper.create_scrolling_image(
                content_items=blocks,
                item_gap=self.config.separator_width,
                element_gap=0,
                lead_gap=self.config.lead_in_width
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
                "Composed scroll image: %dx%d, %d plugin block(s), %d rows, "
                "separator=%dpx between plugins, rows spaced to %dpx of ink "
                "(min added %dpx)",
                self.scroll_helper.cached_image.width if self.scroll_helper.cached_image else 0,
                self.display_height,
                len(blocks),
                total_rows,
                self.config.separator_width,
                self.config.min_content_separation,
                self.config.intra_plugin_gap,
            )

            return True

        except (ValueError, TypeError, OSError, RuntimeError):
            # Expected errors from image operations, scroll helper, or bad data
            logger.exception("Error composing scroll content")
            return False

    def needs_extension(self) -> bool:
        """
        Whether the strip should be extended with the next group of plugins.

        Cheap enough to call every frame: it is arithmetic over cached state.
        """
        if not self.config.continuous_scroll or not self.scroll_helper.cached_image:
            return False
        threshold = int(self.display_width * self.config.extend_threshold_screens)
        return self.scroll_helper.remaining_unscrolled() <= threshold

    def start_prefetch(self) -> None:
        """
        Begin preparing the next group in the background, if not already doing so.

        This is what makes the join seamless rather than merely continuous:
        fetching a group costs 0.5-4.8s (rendering leaderboard and baseball cards
        dominates), and doing it on the render thread stalls the scroll for that
        long. Off the render thread there is a whole group's scroll time to work
        in, so by the time the strip needs extending the content is already sat
        waiting.

        Only paths that avoid the shared display canvas run here; anything
        needing it is marked and picked up on the render thread, where it is
        safe. Those are the cheap ones — display capture measured 12-14ms
        against seconds for the native renders.
        """
        if not self.config.continuous_scroll:
            return

        with self._prefetch_lock:
            if self._prefetch_thread is not None and self._prefetch_thread.is_alive():
                return
            if self._prepared_group is not None:
                return  # already have one waiting

            def _work():
                # Deprioritise against the render loop. Linux applies nice
                # per-thread, and the heavy lifting here is PIL and numpy work
                # that releases the GIL, so the scheduler can actually act on
                # it — without this the prefetch competes for the same cores and
                # costs frames.
                try:
                    os.nice(10)
                except (OSError, AttributeError):
                    pass
                try:
                    group = self.stream_manager.take_next_group(offscreen_only=True)
                except Exception:
                    logger.exception("Background prefetch failed")
                    group = []
                with self._prefetch_lock:
                    self._prepared_group = group

            self._prefetch_thread = threading.Thread(
                target=_work, daemon=True, name="vegas-strip-prefetch")
            self._prefetch_thread.start()

    def drain_deferred(self) -> bool:
        """
        Fetch one queued canvas-bound plugin and append it to the strip.

        Called once per frame. These plugins cannot be prepared off the render
        thread — display capture and scroll-content generation both need the
        shared canvas — so each costs roughly 290ms here. Doing one at a time
        spreads that out instead of stalling for the whole group at once, and the
        strip's lookahead means nothing runs dry while they arrive.

        The cost is that a deferred plugin appears slightly after the group it
        came with, which is a fair trade for a smooth scroll.

        Returns:
            True if a plugin was appended
        """
        if not self._deferred_queue:
            return False

        # Space the drains out. Each costs 40-600ms, and taking them back to
        # back turns one long stall into a train of short ones — barely better.
        # With a healthy lookahead there is no hurry, so wait a beat between
        # them; when the strip is actually running short, fetch immediately.
        threshold = int(self.display_width * self.config.extend_threshold_screens)
        urgent = self.scroll_helper.remaining_unscrolled() <= threshold
        if not urgent:
            now = time.time()
            if now - self._last_drain_time < self.DEFERRED_DRAIN_INTERVAL:
                return False
            self._last_drain_time = now
        else:
            self._last_drain_time = time.time()

        plugin_id = self._deferred_queue.pop(0)
        plugins = getattr(self.stream_manager.plugin_manager, 'plugins', {})
        plugin = plugins.get(plugin_id)
        if plugin is None:
            return False

        try:
            images = self.stream_manager.plugin_adapter.get_content(plugin, plugin_id)
        except Exception:
            logger.exception("[%s] Error fetching deferred content", plugin_id)
            return False

        if not images:
            return False

        appended = self.scroll_helper.append_content(
            content_items=[self._join_plugin_rows(images)],
            item_gap=self.config.separator_width,
            element_gap=0,
        )
        if appended:
            with self._buffer_lock:
                self._active_scroll_image = self.scroll_helper.cached_image
            logger.info(
                "[%s] Appended deferred content: strip now %dpx, %dpx ahead",
                plugin_id, self.scroll_helper.total_scroll_width,
                self.scroll_helper.remaining_unscrolled()
            )
        return appended

    def has_deferred(self) -> bool:
        """Whether any canvas-bound plugins are still queued."""
        return bool(self._deferred_queue)

    def _claim_prepared_group(self):
        """Take the prefetched group, if one is ready."""
        with self._prefetch_lock:
            group = self._prepared_group
            self._prepared_group = None
        return group

    def extend_scroll_content(self) -> bool:
        """
        Append the next group of plugins to the strip, without interrupting motion.

        This is what replaces the swap. Scroll position is untouched, so the new
        content simply arrives from the right; there is no substitution to see
        and no restart with the viewport already full.

        Consumed columns behind the viewport are then released, keeping the strip
        bounded however long Vegas runs.

        Returns:
            True if the strip was extended
        """
        try:
            grouped = self._claim_prepared_group()
            if grouped is None:
                # Nothing prepared (first extension, or prefetch still running).
                # Fetch inline; the scroll hitches, but content keeps flowing.
                logger.info("No prepared group ready; fetching inline")
                grouped = self.stream_manager.take_next_group()

            if not grouped:
                logger.warning("No content available to extend the scroll strip")
                return False

            # Plugins the background thread had to defer need the shared canvas,
            # so they can only be fetched here. Queue them rather than doing all
            # of them now: measured, six in one go held the render thread for
            # 1.75s. They are trickled in one per frame by drain_deferred(),
            # which the strip's lookahead comfortably absorbs.
            deferred = [pid for pid, images in grouped if images is None]
            if deferred:
                self._deferred_queue.extend(deferred)
                logger.info(
                    "Queued %d plugin(s) needing the render thread: %s",
                    len(deferred), ', '.join(deferred)
                )

            grouped = [(pid, imgs) for pid, imgs in grouped if imgs]

            if not grouped:
                # Everything in this group is queued; the queue will extend the
                # strip as it drains, so this is not a failure.
                logger.info("Whole group deferred; strip will extend as it drains")
                self.start_prefetch()
                return bool(deferred)

            blocks = []
            total_rows = 0
            for _plugin_id, images in grouped:
                total_rows += len(images)
                blocks.append(self._join_plugin_rows(images))

            appended = self.scroll_helper.append_content(
                content_items=blocks,
                item_gap=self.config.separator_width,
                element_gap=0,
            )
            if not appended:
                return False

            # Keep a screen's worth behind the viewport as a safety margin.
            self.scroll_helper.drop_scrolled_prefix(keep_before=self.display_width)

            with self._buffer_lock:
                self._active_scroll_image = self.scroll_helper.cached_image

            self._segments_in_scroll = [pid for pid, _ in grouped]
            self.stats['composition_count'] += 1
            self.stats['extensions'] = self.stats.get('extensions', 0) + 1

            logger.info(
                "Extended scroll strip with %d plugin block(s), %d rows: "
                "strip now %dpx, %dpx still ahead of the viewport",
                len(blocks), total_rows, self.scroll_helper.total_scroll_width,
                self.scroll_helper.remaining_unscrolled()
            )

            # Line up the group after this one straight away, so it is ready
            # well before the strip runs short again.
            self.start_prefetch()
            return True

        except (ValueError, TypeError, OSError, RuntimeError):
            logger.exception("Error extending scroll content")
            return False

    def _join_plugin_rows(self, images: List[Image.Image]) -> Image.Image:
        """
        Concatenate one plugin's images into a single block.

        Args:
            images: That plugin's content, in order

        Returns:
            A single image with the rows laid out left to right, separated by
            ``intra_plugin_gap``. Returned unchanged when there is only one row,
            which is the common case and avoids a pointless copy.
        """
        if len(images) == 1:
            return images[0]

        floor = max(0, self.config.intra_plugin_gap)
        target = max(0, self.config.min_content_separation)
        threshold = self.config.trim_threshold

        # Space by measured separation, not a flat gap. Rows drawn flush to
        # their own edges (sports score cards) would otherwise end up nearly
        # touching, while rows that already carry wide margins would be pushed
        # needlessly further apart.
        gaps = [
            separation_gap(images[i], images[i + 1], target, floor, threshold)
            for i in range(len(images) - 1)
        ]

        width = sum(img.width for img in images) + sum(gaps)
        height = max(img.height for img in images)

        block = Image.new('RGB', (width, height), (0, 0, 0))
        x = 0
        for i, img in enumerate(images):
            block.paste(img, (x, 0))
            x += img.width + (gaps[i] if i < len(gaps) else 0)
        return block

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
            # get_visible_portion wraps: once scroll_position + display_width
            # passes the end of the strip it fills the right-hand side of the
            # frame from the *head* of the same strip. So the last
            # display_width of travel shows the cycle's first plugin re-entering
            # on the right while its last plugin exits on the left, and the
            # recompose that follows then replaces both at once. That reads as
            # the ticker "switching mid-scroll".
            #
            # This used to be hidden because the strip began with a full
            # display_width of blank, so the wrapped-in region was black.
            # lead_in_width now defaults to 0 (that blank was 10s of dead panel
            # at 50px/s), which exposed the wrap — so the cycle has to end
            # before it, one display width earlier.
            #
            # A strip no wider than the display never wraps, and subtracting
            # would make the cycle complete instantly, so clamp in that case.
            # In continuous mode there is no cycle to complete: the strip is
            # extended before the scroll can reach its end, so the wrap is never
            # entered and motion never stops. The completion path below stays for
            # the swap behaviour and as a backstop if an extension fails.
            wrap_point = self.scroll_helper.total_scroll_width
            if wrap_point > self.display_width:
                wrap_point -= self.display_width

            at_wrap_point = (
                not self._cycle_complete and
                self.scroll_helper.total_distance_scrolled >= wrap_point
            )

            if at_wrap_point or self.scroll_helper.is_scroll_complete():
                if not self._cycle_complete:
                    self._cycle_complete = True
                    self.stats['scroll_cycles'] += 1
                    logger.info(
                        "Scroll cycle complete after %.1fs",
                        time.time() - self._cycle_start_time
                    )
                    # Deliberately leave the last rendered frame on the panel.
                    #
                    # This used to push a blank frame so no post-wrap content
                    # could be seen while the next cycle was composed. But
                    # recomposing is synchronous and fetches plugin content:
                    # measured 84ms at best and 4.8s at worst on a 512px panel,
                    # and every millisecond of it was black. Holding the last
                    # frame instead turns that into a brief freeze, which reads
                    # as far less broken than the display switching off. The
                    # frame is already past the end of the content, so there is
                    # no second-pass content to leak.
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
        - A plugin currently visible in the scroll has pending updated data
          (e.g. a live score changed) — standalone (non-sync) mode only
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

        # Trigger recompose when pending updates affect visible segments, so
        # live score/status changes reach the display within a few seconds
        # instead of waiting for the next full cycle.
        if self.stream_manager.has_pending_updates_for_visible_segments():
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
            # When sync is active, start the leader past the lead-in gap so it
            # immediately shows content, leaving the follower on the blank gap
            # for a clean transition rather than near-end content wrapping
            # around. This tracks lead_in_width rather than assuming a full
            # display width of gap, which is no longer the default.
            self.scroll_helper.scroll_position = float(self.config.lead_in_width)

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
