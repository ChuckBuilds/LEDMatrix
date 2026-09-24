"""
Scroll Helper

Handles scrolling text and image content for LED matrix displays.
Extracted from LEDMatrix core to provide reusable functionality for plugins.

Features:
- Pre-rendered scrolling image caching with numpy array optimization
- Fast numpy-based image slicing for high-performance scrolling (100+ FPS)
- Scroll position management with wrap-around
- Dynamic duration calculation based on content width
- Frame rate tracking and logging
- Scrolling state management integration with display_manager
- Support for both continuous and bounded scrolling modes
- Pre-allocated buffers to minimize memory allocations
"""

import logging
import math
import time
from typing import Optional, Dict, Any
from PIL import Image
import numpy as np


# How often the frame-stats line is emitted, and therefore also the ceiling
# on a believable frame time: a scroll that renders at all cannot take this
# long over one frame, so a sample this large is an idle gap between scrolls.
FPS_LOG_INTERVAL = 5.0


def frame_stats(frame_times: list) -> Dict[str, Any]:
    """Summary statistics over one window of frame durations (seconds).

    Split out of log_frame_rate() so the arithmetic can be tested without a
    clock. Median and p95 are the real ones: the median takes both middle
    samples on an even window, and p95 is nearest-rank, so a 100-frame window
    reports the 95th sorted sample rather than the 96th. That matters twice
    over, because the median is also the threshold the stall and skip counts
    are measured against.
    """
    window = sorted(frame_times)
    n = len(window)
    median = (window[n // 2] if n % 2
              else (window[n // 2 - 1] + window[n // 2]) / 2.0)
    mean = sum(window) / n
    # Anything past 1.5x the median missed a panel refresh; anything under
    # half of it never reached the panel at all (dirty tracking skipped the
    # swap, so the frame did not wait for vsync).
    return {
        "frames": n,
        "fps": (1.0 / mean) if mean > 0 else 0.0,
        "median": median,
        "p95": window[max(0, math.ceil(0.95 * n) - 1)],
        "max": window[-1],
        "min": window[0],
        "stalls": sum(1 for f in window if f > median * 1.5),
        "skips": sum(1 for f in window if f < median * 0.5),
    }


def format_frame_stats(frame_times: list) -> str:
    """The one-line rendering of frame_stats(), in milliseconds."""
    s = frame_stats(frame_times)
    n = s["frames"]
    return (
        f"{s['fps']:.1f} fps over {n} frames | "
        f"median {s['median'] * 1000:.2f}ms p95 {s['p95'] * 1000:.2f}ms "
        f"max {s['max'] * 1000:.2f}ms min {s['min'] * 1000:.2f}ms | "
        f"stalls {s['stalls']} ({100.0 * s['stalls'] / n:.1f}%) "
        f"skips {s['skips']} ({100.0 * s['skips'] / n:.1f}%)"
    )


class ScrollHelper:
    """
    Helper class for scrolling text and image content on LED displays.
    
    Provides functionality for:
    - Creating and caching scrolling images (with numpy array optimization)
    - Fast numpy-based image slicing for high-performance scrolling
    - Managing scroll position with wrap-around
    - Calculating dynamic display duration
    - Frame rate tracking and performance monitoring
    - Integration with display manager scrolling state
    - Pre-allocated buffers for minimal memory allocations
    
    Performance optimizations:
    - Uses numpy arrays for fast array slicing instead of PIL crop operations
    - Pre-computes numpy array from PIL image to avoid repeated conversions
    - Reuses pre-allocated frame buffer to minimize allocations
    - Optimized for 100+ FPS scrolling performance
    """
    
    def __init__(self, display_width: int, display_height: int,
                 logger: Optional[logging.Logger] = None):
        """
        Initialize the ScrollHelper.
        
        Args:
            display_width: Width of the LED matrix display
            display_height: Height of the LED matrix display
            logger: Optional logger instance
        """
        self.display_width = display_width
        self.display_height = display_height
        self.logger = logger or logging.getLogger(__name__)
        
        # Scrolling state
        self.scroll_position = 0.0
        self.total_distance_scrolled = 0.0  # Track total distance including wrap-arounds
        self.scroll_speed = 1.0
        self.scroll_delay = 0.001  # Minimal delay for high FPS (1ms)
        self.cached_image: Optional[Image.Image] = None
        self.cached_array: Optional[np.ndarray] = None  # Numpy array cache for fast operations
        self.total_scroll_width = 0
        
        # Pre-allocated buffer for output frame (reused to avoid allocations)
        self._frame_buffer: Optional[np.ndarray] = None
        
        # Sub-pixel scrolling: OFF by default, and that is deliberate.
        # Blending renders a half-step by mixing two adjacent columns 50/50.
        # On a high-resolution screen that reads as smooth motion; on a coarse
        # LED matrix showing pixel-font text it does not. A one-pixel stroke
        # becomes two half-brightness pixels, so frames alternate between crisp
        # and smeared and the text appears to shimmer and jump a pixel ahead --
        # tested on a 2x128x64 panel and clearly worse than integer stepping.
        #
        # The rule this display obeys: motion is smooth when it advances a
        # whole number of pixels per refresh. Anything slower must either
        # blend (blur) or repeat frames (judder); blending is the worse of the
        # two here. Vegas mode still opts in via set_sub_pixel_scrolling().
        self.sub_pixel_scrolling = False

        # Frame-based scrolling settings
        self.frame_based_scrolling = False
        #: Whole pixels to advance per presented frame, or None to pace
        #: off elapsed time. See set_pixels_per_frame.
        self.fixed_pixels_per_frame = None
        self.last_step_time = 0.0  # Time of the last position update

        # Time tracking for scroll updates
        self.last_update_time: Optional[float] = None

        #: Informational only: the presentation rate scroll_config chose
        #: (panel refresh / frame hold). Nothing paces off it -- the helper
        #: steps per call and SwapOnVSync paces the calls. Kept because
        #: plugins and their tests read it back.
        self.target_fps = 120
        
        # Dynamic duration settings
        self.dynamic_duration_enabled = True
        self.min_duration = 30
        self.max_duration = 300
        self.duration_buffer = 0.1
        self.calculated_duration = 60
        self.scroll_start_time: Optional[float] = None
        self.last_progress_log_time: Optional[float] = None
        self.progress_log_interval = 5.0  # seconds
        
        # Frame rate tracking. last_frame_time is None until the first frame
        # of a scroll is rendered -- see log_frame_rate() for why timing from
        # construction (or from the end of the previous scroll) is wrong.
        self.frame_count = 0
        self.last_frame_time: Optional[float] = None
        self.last_fps_log_time = time.time()
        self.frame_times = []
        # Every frame time since the last stats line, so the 5s summary can
        # report the tail rather than one arbitrary sample. Cleared on log.
        self._window: list = []
        
        # Scrolling state management
        self.is_scrolling = False
        self.scroll_complete = False
        
    def create_scrolling_image(self, content_items: list,
                             item_gap: int = 32,
                             element_gap: int = 16,
                             lead_gap: Optional[int] = None) -> Image.Image:
        """
        Create a wide image containing all content items for scrolling.

        Args:
            content_items: List of PIL Images to include in scroll
            item_gap: Gap between different items
            element_gap: Gap between elements within an item
            lead_gap: Blank columns before the first item. Defaults to a full
                display width, which makes a standalone ticker scroll in from
                off-screen. Callers that loop many plugins back-to-back (Vegas
                mode) pass a smaller value, since a full display width of black
                reads as the panel being switched off at the start of every
                cycle.

        Returns:
            PIL Image containing all content arranged horizontally
        """
        if lead_gap is None:
            lead_gap = self.display_width
        lead_gap = max(0, int(lead_gap))
        if not content_items:
            # Create empty image if no content
            # Still set total_scroll_width to 0 to indicate no scrollable content
            self.total_scroll_width = 0
            self.cached_image = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            self.cached_array = np.array(self.cached_image)
            self.scroll_position = 0.0
            self.total_distance_scrolled = 0.0
            self.scroll_complete = False
            return self.cached_image
        
        # Calculate total width needed
        # Sum of all item widths
        total_width = sum(img.width for img in content_items)
        # Add item gaps between items (not after last item)
        total_width += item_gap * (len(content_items) - 1)
        # Add element_gap after each item (matches positioning logic)
        total_width += element_gap * len(content_items)
        
        # Add initial gap before first item
        total_width += lead_gap

        # Create the full scrolling image
        full_image = Image.new('RGB', (total_width, self.display_height), (0, 0, 0))

        # Position items
        current_x = lead_gap  # Start with initial gap
        
        for i, img in enumerate(content_items):
            # Paste the item image
            full_image.paste(img, (current_x, 0))
            current_x += img.width + element_gap
            
            # Add gap between items (except after last item)
            if i < len(content_items) - 1:
                current_x += item_gap
        
        # Store the image and update scroll width
        self.cached_image = full_image
        # Convert to numpy array for fast operations
        self.cached_array = np.array(full_image)
        actual_image_width = full_image.width
        self.total_scroll_width = actual_image_width
        
        self.scroll_position = 0.0
        self.total_distance_scrolled = 0.0
        self.scroll_complete = False
        
        # Pre-allocate frame buffer if needed
        if self._frame_buffer is None or self._frame_buffer.shape != (self.display_height, self.display_width, 3):
            self._frame_buffer = np.zeros((self.display_height, self.display_width, 3), dtype=np.uint8)
        
        # Calculate dynamic duration
        self._calculate_dynamic_duration()
        now = time.time()
        self.scroll_start_time = now
        self.last_progress_log_time = now
        self.logger.info(
            "Dynamic duration target set to %ds (min=%ds, max=%ds, buffer=%.2f)",
            self.calculated_duration,
            self.min_duration,
            self.max_duration,
            self.duration_buffer,
        )
        
        self.logger.info(
            "Created scrolling image: %dx%dpx (total_scroll_width=%dpx, %d items, item_gap=%d, element_gap=%d)",
            actual_image_width, self.display_height, self.total_scroll_width,
            len(content_items), item_gap, element_gap
        )
        return full_image
    
    def update_scroll_position(self) -> None:
        """
        Advance the scroll by one presented frame and handle wrap-around.

        With a fixed per-frame step (set_pixels_per_frame, which
        scroll_config.configure sets for a crisp speed) every call moves
        exactly that many pixels and no clock is read; the caller's
        vsync-blocking swap, held for ``frame_hold`` refreshes, sets the rate.
        Otherwise the position advances by elapsed time at the configured
        speed.
        """
        if not self.cached_image:
            return
        
        # Calculate frame time for consistent scroll speed regardless of FPS
        current_time = time.time()
        if self.last_update_time is None:
            self.last_update_time = current_time
        
        delta_time = current_time - self.last_update_time
        self.last_update_time = current_time

        if self.scroll_start_time is None:
            self.scroll_start_time = current_time
            self.last_progress_log_time = current_time
        
        # Update scroll position
        if self.fixed_pixels_per_frame:
            # One presented frame, one fixed whole-pixel step. No clock is
            # consulted, so no jitter reaches the position and every frame
            # moves the eye by the same amount. See set_pixels_per_frame.
            pixels_to_move = self.fixed_pixels_per_frame
            self.last_step_time = current_time
        elif self.frame_based_scrolling:
            # Frame-based: move fixed amount when scroll_delay has passed
            # This matches stock ticker behavior: move pixels, then wait scroll_delay
            # Initialize last_step_time on first call to prevent huge initial jump
            if self.last_step_time == 0.0:
                self.last_step_time = current_time
            
            # Frame-based mode advances by elapsed time, exactly like the
            # time-based branch below, at the same configured speed
            # (scroll_speed px per scroll_delay seconds).
            #
            # It used to step discretely: 0, 1 or 2 whole pixels depending on
            # whether a wall clock had passed scroll_delay. Plugins set
            # scroll_delay to the target frame period, so that comparison sits
            # exactly on its own threshold and the decision flips on sub-
            # millisecond jitter -- a frame a hair early moved nothing and
            # rendered an identical frame, a frame a hair late moved two
            # pixels. Rounding the step count fixed the stalls but still
            # discarded the remainder, so the error never corrected.
            #
            # Accumulating elapsed time keeps position exactly proportional to
            # real time: jitter shifts a pixel boundary by a fraction of a
            # frame instead of flipping a whole step, and nothing is lost or
            # gained. This is what the one visibly smooth scroller on the
            # hardware (the stock ticker) was already doing by virtue of never
            # enabling frame-based mode.
            # set_scroll_delay clamps scroll_delay to at least 0.001.
            pixels_per_second = self.scroll_speed / self.scroll_delay
            pixels_to_move = pixels_per_second * delta_time
            self.last_step_time = current_time
        else:
            # Time-based: move based on time delta (correct speed over time)
            # scroll_speed is pixels per second
            pixels_to_move = self.scroll_speed * delta_time
            
        self.scroll_position += pixels_to_move
        self.total_distance_scrolled += pixels_to_move
        
        # One pass is total_scroll_width. With the default lead_gap the strip
        # starts with display_width of blank, so by then the last item has
        # fully left the panel; a caller passing a smaller lead_gap (Vegas)
        # decides for itself where its cycle ends. Adding display_width here
        # caused 1-2 extra wrap-arounds on wide chains.
        required_total_distance = self.total_scroll_width

        # Guard: zero-width content has nothing to scroll — keep position at 0 and skip
        # completion/wrap logic to avoid producing an invalid -1 position.
        if required_total_distance == 0:
            self.scroll_position = 0
            return

        # Check completion FIRST (before wrap-around) to prevent visual loop
        # When dynamic duration is enabled and cycle is complete, stop at end instead of wrapping
        is_complete = self.total_distance_scrolled >= required_total_distance
        
        if is_complete:
            # Only log completion once to avoid spam
            if not self.scroll_complete:
                elapsed = current_time - (self.scroll_start_time or current_time)
                scroll_percent = (self.total_distance_scrolled / required_total_distance * 100) if required_total_distance > 0 else 0.0
                position_percent = (self.scroll_position / self.total_scroll_width * 100) if self.total_scroll_width > 0 else 0.0
                self.logger.info(
                    "Scroll cycle COMPLETE: scrolled %.0f/%d px (%.1f%%, position=%.0f/%.0f px, %.1f%%) - elapsed %.2fs, target %.2fs",
                    self.total_distance_scrolled,
                    required_total_distance,
                    scroll_percent,
                    self.scroll_position,
                    self.total_scroll_width,
                    position_percent,
                    elapsed,
                    self.calculated_duration,
                )
            self.scroll_complete = True
            
            # Clamp position to prevent wrap when complete
            if self.scroll_position >= self.total_scroll_width:
                self.scroll_position = self.total_scroll_width - 1
                self.logger.debug("Clamped scroll position to %d (max=%d)", self.scroll_position, self.total_scroll_width - 1)
        else:
            self.scroll_complete = False
            
            # Only wrap-around if cycle is not complete yet
            if self.scroll_position >= self.total_scroll_width:
                elapsed = current_time - self.scroll_start_time
                self.scroll_position = self.scroll_position - self.total_scroll_width
                self.logger.info(
                    "Scroll wrap-around detected: position reset, total_distance=%.0f/%d px (elapsed %.2fs, target %.2fs)",
                    self.total_distance_scrolled,
                    required_total_distance,
                    elapsed,
                    self.calculated_duration,
                )

        if (
            self.dynamic_duration_enabled
            and self.last_progress_log_time is not None
            and current_time - self.last_progress_log_time >= self.progress_log_interval
        ):
            elapsed_time = current_time - (self.scroll_start_time or current_time)
            required_total_distance = self.total_scroll_width
            # Progress telemetry, emitted every few seconds for the whole of
            # every scroll. It says how far along a marquee is, which is what
            # you turn debug on to watch and not something an operator needs
            # in the journal on a device that scrolls all day.
            self.logger.debug(
                "Scroll progress: elapsed=%.2fs, target=%.2fs, total_scrolled=%.0f/%d px (%.1f%%)",
                elapsed_time,
                self.calculated_duration,
                self.total_distance_scrolled,
                required_total_distance,
                (self.total_distance_scrolled / required_total_distance * 100) if required_total_distance > 0 else 0.0,
            )
            self.last_progress_log_time = current_time
    
    def get_visible_portion(self) -> Optional[Image.Image]:
        """
        Get the currently visible portion of the scrolling image using fast numpy operations.
        Uses integer pixel positioning for high-performance scrolling.
        
        Returns:
            PIL Image showing the visible portion, or None if no cached image
        """
        if not self.cached_image or self.cached_array is None:
            return None

        start_x_int = int(self.scroll_position)
        end_x_int = start_x_int + self.display_width

        # Integer positioning quantises motion to whole pixels, so the number of
        # distinct frames per second equals the scroll speed in px/s, no matter
        # how fast the loop renders. At 50px/s and 78fps that made 36% of frames
        # identical: the extra frames cost work and bought nothing. Blending
        # between the two neighbouring positions gives motion at the frame rate
        # instead of the step rate.
        if self.sub_pixel_scrolling:
            fractional = self.scroll_position - start_x_int
            if fractional > 0.0:
                return self._blend_visible_portion(start_x_int, fractional)

        return self._get_visible_portion_integer(start_x_int, end_x_int)

    def _blend_visible_portion(self, start_x: int, fractional: float) -> Image.Image:
        """
        Linear blend between the frames at ``start_x`` and ``start_x + 1``.

        Implemented with numpy rather than scipy.ndimage.shift, which is not
        installed on the target devices.

        Args:
            start_x: Left column of the earlier of the two frames
            fractional: How far between the two, in [0, 1)

        Returns:
            The blended frame
        """
        width = self.display_width
        strip_width = self.cached_array.shape[1]

        if start_x + width + 1 <= strip_width:
            # Slice the backing array directly. Going via
            # _get_visible_portion_integer would build two PIL images only for
            # them to be converted straight back to arrays, which measured 15x
            # the cost of the integer path.
            near = self.cached_array[:, start_x:start_x + width]
            far = self.cached_array[:, start_x + 1:start_x + 1 + width]
        else:
            # Close enough to the end that one of the slices wraps; let the
            # integer path handle that and pay the conversion. Continuous mode
            # extends the strip before reaching here, so this is the rare case.
            near = np.asarray(
                self._get_visible_portion_integer(start_x, start_x + width))
            far = np.asarray(
                self._get_visible_portion_integer(start_x + 1, start_x + 1 + width))

        # Fixed-point rather than float32: integer multiply-add on uint16 is
        # markedly faster than float maths on the Pi's ARM cores, and 8 bits of
        # weight is finer than the panel can show.
        weight = int(fractional * 256.0)
        blended = (
            (near.astype(np.uint16) * (256 - weight)
             + far.astype(np.uint16) * weight) >> 8
        ).astype(np.uint8)

        return Image.frombytes(
            'RGB', (width, self.display_height),
            np.ascontiguousarray(blended).tobytes()
        )
    
    def _get_visible_portion_integer(self, start_x: int, end_x: int) -> Image.Image:
        """Fast integer pixel extraction (no interpolation).

        Uses Image.frombytes instead of Image.fromarray: frombytes skips
        numpy's array-protocol overhead and is ~50% faster for the display-sized
        slices (128×32 = 12 KB) used here.
        """
        _size = (self.display_width, self.display_height)
        img_w = self.cached_image.width

        if end_x <= img_w:
            # Normal case: single contiguous slice (fastest path)
            frame_array = np.ascontiguousarray(self.cached_array[:, start_x:end_x])
            return Image.frombytes('RGB', _size, frame_array.tobytes())
        else:
            # Ensure frame buffer is allocated for all non-simple paths
            if self._frame_buffer is None or self._frame_buffer.shape != (self.display_height, self.display_width, 3):
                self._frame_buffer = np.zeros((self.display_height, self.display_width, 3), dtype=np.uint8)

            width1 = img_w - start_x
            if width1 > 0:
                # Wrap-around: tail of image + head of image
                self._frame_buffer[:, :width1] = self.cached_array[:, start_x:]
                remaining_width = self.display_width - width1
                self._frame_buffer[:, width1:] = self.cached_array[:, :remaining_width]
            else:
                # Edge case: start_x at or past image end — show from beginning,
                # clamped to available width (scroll_position should wrap before
                # reaching this state in normal operation).
                available = min(self.display_width, img_w)
                self._frame_buffer[:, :available] = self.cached_array[:, :available]
                if available < self.display_width:
                    self._frame_buffer[:, available:] = 0

            return Image.frombytes('RGB', _size, self._frame_buffer.tobytes())
    
    def calculate_dynamic_duration(self) -> int:
        """
        Calculate display duration based on content width and scroll settings.
        
        Returns:
            Duration in seconds
        """
        if not self.dynamic_duration_enabled:
            return self.min_duration
        
        # Validate total_scroll_width is set and valid
        if not self.total_scroll_width or self.total_scroll_width <= 0:
            if self.total_scroll_width == 0:
                self.logger.warning(
                    "Dynamic duration calculation skipped: total_scroll_width is 0. "
                    "Ensure create_scrolling_image() or set_scrolling_image() has been called. "
                    "Using minimum duration: %ds",
                    self.min_duration
                )
            else:
                self.logger.warning(
                    "Dynamic duration calculation skipped: total_scroll_width is invalid (%s). "
                    "Using minimum duration: %ds",
                    self.total_scroll_width,
                    self.min_duration
                )
            return self.min_duration
        
        try:
            # The strip's width plus one more screen, so the duration covers
            # the last item leaving the panel even when the strip has less
            # than display_width of lead-in blank (lead_gap).
            total_scroll_distance = self.total_scroll_width + self.display_width
            
            # Calculate effective pixels per second based on scrolling mode
            if self.frame_based_scrolling:
                # Frame-based mode: scroll_speed is pixels per scroll_delay
                # seconds, and set_scroll_delay keeps scroll_delay >= 0.001.
                pixels_per_second = self.scroll_speed / self.scroll_delay
                scroll_mode_str = "frame-based"
            else:
                # Time-based mode: scroll_speed is already pixels per second
                pixels_per_second = self.scroll_speed
                scroll_mode_str = "time-based"
            
            # Calculate time based on effective pixels per second
            total_time = total_scroll_distance / pixels_per_second
            
            # Add buffer time for smooth cycling
            buffer_time = total_time * self.duration_buffer
            calculated_duration = int(total_time + buffer_time)
            
            # Apply min/max limits
            if calculated_duration < self.min_duration:
                self.calculated_duration = self.min_duration
            elif calculated_duration > self.max_duration:
                self.calculated_duration = self.max_duration
            else:
                self.calculated_duration = calculated_duration
            
            self.logger.debug("Dynamic duration calculation (%s mode):", scroll_mode_str)
            self.logger.debug("  Display width: %dpx", self.display_width)
            self.logger.debug("  Content width: %dpx", self.total_scroll_width)
            self.logger.debug("  Total scroll distance: %dpx", total_scroll_distance)
            if self.frame_based_scrolling:
                self.logger.debug("  Scroll speed: %.2f px/frame, delay: %.3fs", self.scroll_speed, self.scroll_delay)
                self.logger.debug("  Effective speed: %.1f px/second", pixels_per_second)
            else:
                self.logger.debug("  Scroll speed: %.1f px/second", pixels_per_second)
            self.logger.debug("  Base time: %.2fs", total_time)
            self.logger.debug("  Buffer time: %.2fs", buffer_time)
            self.logger.debug("  Final duration: %ds", self.calculated_duration)
            
            return self.calculated_duration
            
        except (ValueError, ZeroDivisionError, TypeError) as e:
            self.logger.error("Error calculating dynamic duration: %s", e)
            return self.min_duration
    
    def is_scroll_complete(self) -> bool:
        """
        Check if the current scroll cycle is complete.
        
        Returns:
            True if scroll has wrapped around to the beginning
        """
        return self.scroll_complete
    
    def append_content(self, content_items: list,
                       item_gap: int = 32,
                       element_gap: int = 0) -> bool:
        """
        Append items to the right of the existing strip, preserving scroll state.

        Lets a caller keep one continuous strip instead of replacing it. Vegas
        mode uses this so the next group of plugins scrolls in from the right
        rather than the strip being swapped out underneath the viewer — a swap
        shows as a flash and a hard cut to already-full-screen content.

        ``scroll_position`` and ``total_distance_scrolled`` are untouched, so
        motion continues uninterrupted; only the strip gets longer. Because
        completion is measured against ``total_scroll_width``, extending the
        strip also defers completion, which is the intent.

        Args:
            content_items: Images to append, in order
            item_gap: Gap between appended items, and between the existing
                content and the first appended item
            element_gap: Extra gap after each item, mirroring
                create_scrolling_image

        Returns:
            True if content was appended
        """
        if not content_items:
            return False

        if self.cached_image is None or self.cached_array is None:
            # Nothing to extend yet — this is just the first build.
            self.create_scrolling_image(
                content_items, item_gap=item_gap, element_gap=element_gap, lead_gap=0)
            return True

        gap = max(0, item_gap)
        addition_width = (
            sum(img.width for img in content_items)
            + gap * len(content_items)          # one leading gap per item
            + element_gap * len(content_items)
        )

        addition = Image.new('RGB', (addition_width, self.display_height), (0, 0, 0))
        x = 0
        for img in content_items:
            x += gap                            # separate from whatever precedes
            addition.paste(img, (x, 0))
            x += img.width + element_gap

        # numpy concatenate then one conversion back, rather than allocating a
        # full-width PIL image and pasting twice: the strip can be tens of
        # thousands of columns wide and this runs on the render path.
        self.cached_array = np.concatenate(
            (self.cached_array, np.array(addition)), axis=1)
        self.cached_image = Image.fromarray(self.cached_array)
        self.total_scroll_width = self.cached_image.width
        self.scroll_complete = False

        self.logger.info(
            "Appended %d item(s) (%dpx) to scroll strip: now %dpx, position %.0f",
            len(content_items), addition_width, self.total_scroll_width,
            self.scroll_position
        )
        return True

    def drop_scrolled_prefix(self, keep_before: int = 0) -> int:
        """
        Discard columns that have already scrolled past, to bound memory.

        A continuously extended strip would otherwise grow without limit. All
        the positional state is shifted by the amount removed so the visible
        frame and the completion arithmetic are unchanged:
        ``total_distance_scrolled`` and ``total_scroll_width`` both shrink by the
        same amount, preserving their difference.

        Args:
            keep_before: Columns to retain behind the current position, as a
                safety margin against a caller reading slightly behind it

        Returns:
            Number of columns actually removed
        """
        if self.cached_image is None or self.cached_array is None:
            return 0

        # While the viewport wraps, get_visible_portion fills its right-hand side
        # from the *head* of the strip, so trimming the head would change what
        # is on screen. Continuous mode extends before ever reaching that state;
        # refusing here keeps "trimming is invisible" true unconditionally.
        if self.scroll_position + self.display_width > self.cached_image.width:
            return 0

        cut = int(self.scroll_position) - max(0, keep_before)
        if cut <= 0:
            return 0
        # Never trim so far that the remaining strip is narrower than the
        # viewport, or get_visible_portion has nothing to slice.
        cut = min(cut, max(0, self.cached_image.width - self.display_width))
        if cut <= 0:
            return 0

        # .copy() so the original buffer is released rather than kept alive by
        # a numpy view.
        self.cached_array = self.cached_array[:, cut:].copy()
        self.cached_image = Image.fromarray(self.cached_array)
        self.total_scroll_width = self.cached_image.width
        self.scroll_position -= cut
        self.total_distance_scrolled = max(0.0, self.total_distance_scrolled - cut)

        self.logger.debug(
            "Dropped %dpx of scrolled strip: now %dpx, position %.0f",
            cut, self.total_scroll_width, self.scroll_position
        )
        return cut

    def remaining_unscrolled(self) -> int:
        """Columns of strip still to the right of the viewport."""
        if self.cached_image is None:
            return 0
        return max(0, self.total_scroll_width - int(self.scroll_position)
                   - self.display_width)

    def reset_scroll(self) -> None:
        """
        Reset scroll position to beginning.
        """
        self.scroll_position = 0.0
        self.total_distance_scrolled = 0.0
        self.scroll_complete = False
        now = time.time()
        self.scroll_start_time = now
        self.last_progress_log_time = now
        self.last_step_time = now  # Reset step timer
        # Reset last_update_time to prevent large delta_time on next update
        # This ensures smooth scrolling after reset without jumping ahead
        self.last_update_time = now
        # Same reasoning for the frame-rate clock: the first frame after a
        # reset has no predecessor in this scroll, and timing it against the
        # last frame of the previous one measures the idle gap between them.
        self.last_frame_time = None
        self.logger.debug("Scroll position reset")

    def reset(self) -> None:
        """Alias for reset_scroll() for convenience."""
        self.reset_scroll()

    def set_scrolling_image(self, image: Image.Image) -> None:
        """
        Set a pre-rendered scrolling image and initialize all required state.
        
        This method should be used when plugins create their own scrolling image
        instead of using create_scrolling_image(). It properly initializes both
        cached_image and cached_array, and updates all related state.
        
        Args:
            image: PIL Image containing the scrolling content
        """
        if image is None:
            self.logger.warning("Attempted to set None as scrolling image, clearing cache instead")
            self.clear_cache()
            return
        
        # Set the cached image
        self.cached_image = image
        
        # Convert to numpy array for fast operations (required for get_visible_portion)
        self.cached_array = np.array(image)
        
        # Update scroll width
        self.total_scroll_width = image.width
        
        # Reset scroll position
        self.scroll_position = 0.0
        self.total_distance_scrolled = 0.0
        self.scroll_complete = False
        
        # Pre-allocate frame buffer if needed
        if self._frame_buffer is None or self._frame_buffer.shape != (self.display_height, self.display_width, 3):
            self._frame_buffer = np.zeros((self.display_height, self.display_width, 3), dtype=np.uint8)
        
        # Calculate dynamic duration
        self._calculate_dynamic_duration()
        
        # Reset timing
        now = time.time()
        self.scroll_start_time = now
        self.last_progress_log_time = now
        self.last_step_time = now  # Initialize step timer for frame-based scrolling
        
        self.logger.debug("Set scrolling image: %dx%d, total_scroll_width=%d", 
                         image.width, image.height, self.total_scroll_width)
    
    def set_scroll_speed(self, speed: float) -> None:
        """
        Set the scroll speed, and leave fixed-step mode.

        In time-based mode: pixels per second (clamped to 1-500).
        In frame-based mode: pixels per ``scroll_delay`` seconds (clamped to
        0.1-5), still applied by elapsed time as scroll_speed / scroll_delay
        px/s.

        Args:
            speed: Scroll speed (interpretation depends on frame_based_scrolling mode)
        """
        # A speed set directly is a request to pace off that speed, so drop any
        # fixed per-frame step left by an earlier configure(). scroll_config
        # calls this first and set_pixels_per_frame second, so the crisp path
        # is unaffected; what this protects is a legacy caller changing speed
        # on a helper that scroll_config had already put in fixed-step mode,
        # where the new speed would otherwise be silently ignored.
        self.fixed_pixels_per_frame = None
        if self.frame_based_scrolling:
            # In frame-based mode, clamp to reasonable pixels per frame (0.1-5)
            # Higher values cause visible jumps - 1-2 pixels/frame is ideal for smoothness
            self.scroll_speed = max(0.1, min(5.0, speed))
            self.logger.debug(f"Scroll speed set to: {self.scroll_speed} pixels/frame (frame-based mode)")
        else:
            # In time-based mode, clamp to pixels per second (1-500)
            self.scroll_speed = max(1.0, min(500.0, speed))
            self.logger.debug(f"Scroll speed set to: {self.scroll_speed} pixels/second (time-based mode)")
    
    def set_scroll_delay(self, delay: float) -> None:
        """
        Set the delay between scroll frames.
        
        Args:
            delay: Delay in seconds (typically 0.001-0.1)
        """
        self.scroll_delay = max(0.001, min(1.0, delay))
        self.logger.debug(f"Scroll delay set to: {self.scroll_delay}")
    
    def set_pixels_per_frame(self, pixels) -> None:
        """Advance exactly `pixels` per presented frame, ignoring the clock.

        Pass None to go back to pacing off elapsed time.

        Smooth motion is not a frame-rate property. The strip has to advance
        the same number of WHOLE pixels every frame, and deriving that from a
        wall clock cannot deliver it: the position accumulates
        ``speed * delta_time`` and is then truncated to a pixel, so any jitter
        in delta_time lands either side of an integer boundary. Measured on
        hardware at a rock-steady 100.0 fps, individual frames still ranged
        5.6ms to 15.2ms -- 0.57px to 1.44px of movement -- and 53% of frames
        advanced by something other than one pixel: about half moved nothing
        at all and then jumped two. That is the micro-stutter, and it survived
        every frame-timing fix because frame timing was never the problem.

        It is worst precisely at a crisp speed. At 100 px/s on a 100Hz panel
        the accumulator sits exactly on integer boundaries, so sub-millisecond
        jitter flips it either way and the motion beats at around 50Hz.

        Stepping per frame is only correct because SwapOnVSync blocks until
        the panel has taken the frame, which makes the frame count a truer
        clock than time.time(). Before the swap was locked to vsync this would
        have run at whatever speed the loop happened to spin at.
        """
        self.fixed_pixels_per_frame = int(pixels) if pixels else None
        self.logger.debug("Fixed step set to: %s px/frame",
                          self.fixed_pixels_per_frame)

    def set_target_fps(self, fps: float) -> None:
        """
        Record the presentation rate, for diagnostics only.

        Nothing paces off this value: with a fixed per-frame step the helper
        advances once per call, and without one it advances by elapsed time.
        The rate frames are shown at is the panel refresh divided by the frame
        hold passed to ``display_manager.set_scrolling_state``. scroll_config
        sets it to that rate so it can be read back.

        Args:
            fps: Frames per second (clamped to 30-200)
        """
        self.target_fps = max(30.0, min(200.0, fps))
        self.logger.debug("Target FPS recorded: %s FPS (informational)", self.target_fps)
    
    def set_sub_pixel_scrolling(self, enabled: bool) -> None:
        """
        Enable or disable sub-pixel scrolling for smoother movement.
        
        When enabled, uses interpolation to blend between pixels for fractional
        scroll positions, resulting in smooth scrolling even at slow speeds.
        When disabled, uses integer pixel positioning (faster but may skip pixels).
        
        Args:
            enabled: True to enable sub-pixel scrolling (default: False)
        """
        self.sub_pixel_scrolling = enabled
        self.logger.debug(f"Sub-pixel scrolling {'enabled' if enabled else 'disabled'}")

    def set_frame_based_scrolling(self, enabled: bool) -> None:
        """
        Enable or disable frame-based scrolling.
        
        This does not step. When enabled, ``scroll_speed`` is read as pixels
        per ``scroll_delay`` seconds (set_scroll_speed clamps it to 0.1-5), and
        update_scroll_position() still advances by elapsed time at
        ``scroll_speed / scroll_delay`` px/s. A fixed per-frame step set by
        set_pixels_per_frame() takes precedence over both modes.

        Args:
            enabled: True to enable frame-based scrolling (default: False)
        """
        self.frame_based_scrolling = enabled
        self.last_step_time = time.time()  # Reset step timer
        self.logger.debug(f"Frame-based scrolling {'enabled' if enabled else 'disabled'}")
    
    def set_dynamic_duration_settings(self, enabled: bool = True,
                                    min_duration: int = 30,
                                    max_duration: int = 300,
                                    buffer: float = 0.1) -> None:
        """
        Configure dynamic duration calculation.
        
        Args:
            enabled: Enable dynamic duration calculation
            min_duration: Minimum duration in seconds
            max_duration: Maximum duration in seconds
            buffer: Buffer percentage (0.0-1.0)
        """
        self.dynamic_duration_enabled = enabled
        self.min_duration = max(10, min_duration)
        self.max_duration = max(self.min_duration, max_duration)
        self.duration_buffer = max(0.0, min(1.0, buffer))
        
        self.logger.debug(f"Dynamic duration settings: enabled={enabled}, "
                         f"min={self.min_duration}s, max={self.max_duration}s, "
                         f"buffer={self.duration_buffer*100}%")
    
    def get_dynamic_duration(self) -> int:
        """
        Get the calculated dynamic duration.
        
        Returns:
            Duration in seconds
        """
        return self.calculated_duration
    
    def _calculate_dynamic_duration(self) -> None:
        """Internal method to calculate dynamic duration."""
        self.calculated_duration = self.calculate_dynamic_duration()
    
    def log_frame_rate(self) -> None:
        """
        Log frame rate statistics for performance monitoring.
        """
        current_time = time.time()

        # The first frame of a scroll has no predecessor, so it has no frame
        # time. Measuring one anyway records the whole idle gap since the last
        # scroll as a single frame: on hardware that produced windows reading
        # "0.0 fps over 1 frames | median 136776.02ms", and -- worse, because
        # it is not obviously wrong -- put that gap in the max field of
        # otherwise healthy windows and counted it as one stall per scroll.
        # At ~500 frames to a window that is ~0.2%, which is the same order as
        # the real stall rates being measured, so the number could not be
        # trusted at all. Seed the clock and take no sample.
        if self.last_frame_time is None:
            self._restart_stats_window(current_time)
            return

        # Calculate instantaneous frame time
        frame_time = current_time - self.last_frame_time

        # A caller that scrolls without ever calling reset_scroll() never arms
        # the sentinel above, so catch the same gap by its size. Nothing that
        # renders a scroll produces a frame longer than the log interval; a
        # sample that large is an idle period, not a frame. It starts a new
        # scroll exactly as the sentinel does, window timer included.
        if frame_time >= FPS_LOG_INTERVAL:
            self._restart_stats_window(current_time)
            return

        self.frame_times.append(frame_time)

        # Keep only last 100 frames for average
        if len(self.frame_times) > 100:
            self.frame_times.pop(0)

        # Every frame since the last log, not just the last 100 and not just
        # the one that happens to land on the 5s boundary. The old line
        # reported a single instantaneous sample -- roughly 1 frame in 500 --
        # which cannot see a stall that hits 1% of frames, and reported it
        # next to an average that hides the same stall by construction (a 2ms
        # duplicate and a 21ms double-wait mean exactly 10ms). Chasing scroll
        # judder needs the tail, so keep the window and report percentiles.
        self._window.append(frame_time)

        # Log FPS every 5 seconds to avoid spam
        if current_time - self.last_fps_log_time >= FPS_LOG_INTERVAL:
            # An empty window means every sample in this interval was dropped
            # as an idle gap. There is nothing to report, and reporting the
            # gap itself is the bug above.
            if self._window:
                self.logger.info(
                    "Scroll frame stats - %s",
                    format_frame_stats(self._window),
                )
            self.last_fps_log_time = current_time
            self.frame_count = 0
            self._window = []

        self.last_frame_time = current_time
        self.frame_count += 1

    def _restart_stats_window(self, current_time: float) -> None:
        """Seed the frame clock at the start of a scroll, taking no sample.

        The window timer restarts with it. Otherwise the 5s boundary is
        already long overdue when the next frame arrives, and the new scroll
        opens by reporting a window of exactly one frame.
        """
        self.last_frame_time = current_time
        self.last_fps_log_time = current_time

    def clear_cache(self) -> None:
        """
        Clear the cached scrolling image.
        """
        self.cached_image = None
        self.cached_array = None
        self.total_scroll_width = 0
        self.scroll_position = 0.0
        self.total_distance_scrolled = 0.0
        self.scroll_complete = False
        self.scroll_start_time = None
        self.last_progress_log_time = None
        self.logger.debug("Scroll cache cleared")
    
    def get_scroll_info(self) -> Dict[str, Any]:
        """
        Get current scroll state information.
        
        Returns:
            Dictionary with scroll state information
        """
        required_total_distance = self.total_scroll_width if self.total_scroll_width > 0 else 0
        return {
            'scroll_position': self.scroll_position,
            'total_distance_scrolled': self.total_distance_scrolled,
            'required_total_distance': required_total_distance,
            'scroll_speed': self.scroll_speed,
            'scroll_delay': self.scroll_delay,
            'total_width': self.total_scroll_width,
            'is_scrolling': self.is_scrolling,
            'scroll_complete': self.scroll_complete,
            'dynamic_duration': self.calculated_duration,
            'elapsed_time': (time.time() - self.scroll_start_time)
            if self.scroll_start_time
            else None,
            'cached_image_size': (self.cached_image.width, self.cached_image.height) if self.cached_image else None
        }
