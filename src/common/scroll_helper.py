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
import time
from typing import Optional, Dict, Any
from PIL import Image
import numpy as np

# Try to import scipy for sub-pixel interpolation, fallback to simpler method if not available
try:
    from scipy.ndimage import shift
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


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
        
        # Sub-pixel scrolling settings (disabled - using high FPS integer scrolling instead)
        self.sub_pixel_scrolling = False  # Disabled - use high frame rate for smoothness
        self._last_integer_position = 0  # Cache for integer position to avoid repeated calculations
        
        # Frame-based scrolling settings
        self.frame_based_scrolling = False  # If True, use scroll_delay to throttle and move scroll_speed pixels
        self.last_step_time = 0.0  # Track last step time for frame-based throttling
        
        # Time tracking for scroll updates
        self.last_update_time: Optional[float] = None
        
        # High FPS settings
        self.target_fps = 120  # Target 120 FPS for smooth scrolling
        self.frame_time_target = 1.0 / self.target_fps
        
        # Dynamic duration settings
        self.dynamic_duration_enabled = True
        self.min_duration = 30
        self.max_duration = 300
        self.duration_buffer = 0.1
        self.calculated_duration = 60
        self.scroll_start_time: Optional[float] = None
        self.last_progress_log_time: Optional[float] = None
        self.progress_log_interval = 5.0  # seconds
        
        # Frame rate tracking
        self.frame_count = 0
        self.last_frame_time = time.time()
        self.last_fps_log_time = time.time()
        self.frame_times = []
        
        # Scrolling state management
        self.is_scrolling = False
        self.scroll_complete = False
        
    def create_scrolling_image(self, content_items: list, 
                             item_gap: int = 32,
                             element_gap: int = 16) -> Image.Image:
        """
        Create a wide image containing all content items for scrolling.
        
        Args:
            content_items: List of PIL Images to include in scroll
            item_gap: Gap between different items
            element_gap: Gap between elements within an item
            
        Returns:
            PIL Image containing all content arranged horizontally
        """
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
        total_width = sum(img.width for img in content_items)
        total_width += item_gap * (len(content_items) - 1)
        total_width += element_gap * (len(content_items) * 2 - 1)
        
        # Add initial gap before first item
        total_width += self.display_width
        
        # Create the full scrolling image
        full_image = Image.new('RGB', (total_width, self.display_height), (0, 0, 0))
        
        # Position items
        current_x = self.display_width  # Start with initial gap
        
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
        self.total_scroll_width = total_width
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
        
        self.logger.debug(f"Created scrolling image: {total_width}x{self.display_height}")
        return full_image
    
    def update_scroll_position(self) -> None:
        """
        Update scroll position with high FPS control and handle wrap-around.
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
        if self.frame_based_scrolling:
            # Frame-based: move fixed amount only when scroll_delay has passed
            # This replicates the "step" look of old tickers (high FPS integer scrolling)
            # Initialize last_step_time on first call to prevent huge initial jump
            if self.last_step_time == 0.0:
                self.last_step_time = current_time
            
            # Only move if enough time has passed (don't try to catch up on lag)
            if current_time - self.last_step_time >= self.scroll_delay:
                pixels_to_move = self.scroll_speed
                self.last_step_time = current_time
            else:
                pixels_to_move = 0.0
        else:
            # Time-based: move based on time delta (correct speed over time)
            # scroll_speed is pixels per second
            pixels_to_move = self.scroll_speed * delta_time
            
        self.scroll_position += pixels_to_move
        self.total_distance_scrolled += pixels_to_move
        
        # Calculate required total distance: just total_scroll_width
        # The image already includes display_width padding at the start, so we only need
        # to scroll total_scroll_width pixels to show all content once without looping
        required_total_distance = self.total_scroll_width
        
        # Check completion FIRST (before wrap-around) to prevent visual loop
        # When dynamic duration is enabled and cycle is complete, stop at end instead of wrapping
        is_complete = self.total_distance_scrolled >= required_total_distance
        
        if is_complete:
            # Only log completion once to avoid spam
            if not self.scroll_complete:
                elapsed = current_time - self.scroll_start_time
                self.logger.info(
                    "Scroll cycle COMPLETE: scrolled %.0f/%d px (elapsed %.2fs, target %.2fs)",
                    self.total_distance_scrolled,
                    required_total_distance,
                    elapsed,
                    self.calculated_duration,
                )
            self.scroll_complete = True
            
            # Clamp position to prevent wrap when complete
            if self.scroll_position >= self.total_scroll_width:
                self.scroll_position = self.total_scroll_width - 1
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
            # The image already includes display_width padding, so we only need total_scroll_width
            required_total_distance = self.total_scroll_width
            self.logger.info(
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
        
        # Use integer pixel positioning for high FPS scrolling (like stock ticker)
        start_x_int = int(self.scroll_position)
        end_x_int = start_x_int + self.display_width
        
        # Fast integer pixel path (no interpolation - high frame rate provides smoothness)
        return self._get_visible_portion_integer(start_x_int, end_x_int)
    
    def _get_visible_portion_integer(self, start_x: int, end_x: int) -> Image.Image:
        """Fast integer pixel extraction (no interpolation)."""
        # Fast numpy array slicing for normal case (no wrap-around)
        if end_x <= self.cached_image.width:
            # Normal case: single slice - fastest path
            frame_array = self.cached_array[:, start_x:end_x]
            # Convert to PIL Image (minimal overhead)
            return Image.fromarray(frame_array)
        else:
            # Wrap-around case: combine two slices using numpy
            width1 = self.cached_image.width - start_x
            if width1 > 0:
                # Use pre-allocated buffer for output
                if self._frame_buffer is None or self._frame_buffer.shape != (self.display_height, self.display_width, 3):
                    self._frame_buffer = np.zeros((self.display_height, self.display_width, 3), dtype=np.uint8)
                
                # First part from end of image (fast numpy slice)
                self._frame_buffer[:, :width1] = self.cached_array[:, start_x:]
                
                # Second part from beginning of image
                remaining_width = self.display_width - width1
                self._frame_buffer[:, width1:] = self.cached_array[:, :remaining_width]
                
                # Convert combined buffer to PIL Image
                return Image.fromarray(self._frame_buffer)
            else:
                # Edge case: start_x >= image width, wrap to beginning
                frame_array = self.cached_array[:, :self.display_width]
                return Image.fromarray(frame_array)
    
    def _get_visible_portion_subpixel(self, start_x_int: int, fractional: float) -> Image.Image:
        """
        Get visible portion with sub-pixel interpolation for smooth scrolling.
        Uses bilinear interpolation to blend between pixels.
        """
        # We need to extract a region that's 1 pixel wider to allow for interpolation
        start_x = start_x_int
        end_x = start_x_int + self.display_width + 1
        
        # Check if we need wrap-around
        if end_x <= self.cached_image.width:
            # Normal case: extract region with 1 extra pixel for interpolation
            source_region = self.cached_array[:, start_x:end_x]
            
            # Use bilinear interpolation for sub-pixel shifting
            if HAS_SCIPY:
                # Use scipy for high-quality sub-pixel shifting
                shifted = shift(source_region, (0, -fractional, 0), mode='nearest', order=1, prefilter=False)
                # Extract the display_width portion
                frame_array = shifted[:, :self.display_width].astype(np.uint8)
            else:
                # Fallback: simple linear interpolation using numpy
                # Blend between current and next pixel based on fractional part
                frame_array = self._interpolate_subpixel(source_region, fractional)
            
            return Image.fromarray(frame_array)
        else:
            # Wrap-around case with sub-pixel
            # Use pre-allocated buffer
            if self._frame_buffer is None or self._frame_buffer.shape != (self.display_height, self.display_width, 3):
                self._frame_buffer = np.zeros((self.display_height, self.display_width, 3), dtype=np.uint8)
            
            width1 = self.cached_image.width - start_x
            if width1 > 0:
                # First part from end of image
                # Need width1 + 1 pixels for interpolation
                source1_width = min(width1 + 1, self.cached_image.width - start_x)
                source1 = self.cached_array[:, start_x:start_x + source1_width]
                if HAS_SCIPY:
                    shifted1 = shift(source1, (0, -fractional, 0), mode='nearest', order=1, prefilter=False)
                    # Ensure we get exactly width1 pixels, padding if necessary
                    if shifted1.shape[1] >= width1:
                        self._frame_buffer[:, :width1] = shifted1[:, :width1].astype(np.uint8)
                    else:
                        # Shifted array is smaller - pad with zeros or repeat last pixel
                        actual_width = shifted1.shape[1]
                        self._frame_buffer[:, :actual_width] = shifted1.astype(np.uint8)
                        if actual_width < width1:
                            # Pad with last pixel
                            self._frame_buffer[:, actual_width:width1] = shifted1[:, -1:].astype(np.uint8)
                else:
                    interpolated1 = self._interpolate_subpixel(source1, fractional, output_width=width1)
                    # Ensure exact width match
                    if interpolated1.shape[1] == width1:
                        self._frame_buffer[:, :width1] = interpolated1
                    else:
                        # Handle size mismatch
                        copy_width = min(width1, interpolated1.shape[1])
                        self._frame_buffer[:, :copy_width] = interpolated1[:, :copy_width]
                        if copy_width < width1:
                            self._frame_buffer[:, copy_width:width1] = interpolated1[:, -1:]
                
                # Second part from beginning
                remaining_width = self.display_width - width1
                if remaining_width > 0:
                    source2 = self.cached_array[:, :remaining_width + 1]
                    if HAS_SCIPY:
                        shifted2 = shift(source2, (0, -fractional, 0), mode='nearest', order=1, prefilter=False)
                        # Ensure we get exactly remaining_width pixels
                        if shifted2.shape[1] >= remaining_width:
                            self._frame_buffer[:, width1:width1 + remaining_width] = shifted2[:, :remaining_width].astype(np.uint8)
                        else:
                            # Shifted array is smaller - pad if necessary
                            actual_width = shifted2.shape[1]
                            self._frame_buffer[:, width1:width1 + actual_width] = shifted2.astype(np.uint8)
                            if actual_width < remaining_width:
                                self._frame_buffer[:, width1 + actual_width:width1 + remaining_width] = shifted2[:, -1:].astype(np.uint8)
                    else:
                        interpolated2 = self._interpolate_subpixel(source2, fractional, output_width=remaining_width)
                        # Ensure exact width match
                        if interpolated2.shape[1] == remaining_width:
                            self._frame_buffer[:, width1:] = interpolated2
                        else:
                            copy_width = min(remaining_width, interpolated2.shape[1])
                            self._frame_buffer[:, width1:width1 + copy_width] = interpolated2[:, :copy_width]
                            if copy_width < remaining_width:
                                self._frame_buffer[:, width1 + copy_width:width1 + remaining_width] = interpolated2[:, -1:]
            else:
                # Edge case: wrap to beginning
                source = self.cached_array[:, :self.display_width + 1]
                if HAS_SCIPY:
                    shifted = shift(source, (0, -fractional, 0), mode='nearest', order=1, prefilter=False)
                    # Ensure we get exactly display_width pixels
                    if shifted.shape[1] >= self.display_width:
                        self._frame_buffer = shifted[:, :self.display_width].astype(np.uint8)
                    else:
                        # Shifted array is smaller - pad if necessary
                        actual_width = shifted.shape[1]
                        self._frame_buffer[:, :actual_width] = shifted.astype(np.uint8)
                        if actual_width < self.display_width:
                            self._frame_buffer[:, actual_width:] = shifted[:, -1:].astype(np.uint8)
                else:
                    interpolated = self._interpolate_subpixel(source, fractional, output_width=self.display_width)
                    # _interpolate_subpixel now always returns exact width, so this should work
                    self._frame_buffer = interpolated
            
            return Image.fromarray(self._frame_buffer)
    
    def _interpolate_subpixel(self, source: np.ndarray, fractional: float, output_width: Optional[int] = None) -> np.ndarray:
        """
        Simple linear interpolation for sub-pixel positioning.
        Blends between adjacent pixels based on fractional offset.
        
        Args:
            source: Source array to interpolate (width should be at least output_width + 1)
            fractional: Fractional part of scroll position (0.0-1.0)
            output_width: Desired output width (defaults to display_width)
        
        Returns:
            Interpolated array of shape (height, output_width, 3) - ALWAYS exactly output_width
        """
        if output_width is None:
            output_width = self.display_width
        
        # Always return exactly output_width pixels, padding if necessary
        result = np.zeros((source.shape[0], output_width, 3), dtype=np.uint8)
        
        # Ensure we have enough source pixels for interpolation
        if source.shape[1] < 2:
            # Very small source - just copy what we have and pad
            copy_width = min(source.shape[1], output_width)
            result[:, :copy_width] = source[:, :copy_width].astype(np.uint8)
            if copy_width < output_width:
                # Pad with last pixel
                result[:, copy_width:] = source[:, -1:].astype(np.uint8)
            return result
        
        # Calculate how many pixels we can actually interpolate
        # Need at least 2 pixels to interpolate, so max output is source.shape[1] - 1
        max_interpolated_width = source.shape[1] - 1
        interpolated_width = min(output_width, max_interpolated_width)
        
        if interpolated_width > 0:
            # Extract pixels at x and x+1 for interpolation
            pixels_x = source[:, :interpolated_width].astype(np.float32)
            pixels_x1 = source[:, 1:interpolated_width + 1].astype(np.float32)
            
            # Linear interpolation
            interpolated = pixels_x * (1.0 - fractional) + pixels_x1 * fractional
            
            # Clip and convert back to uint8
            interpolated = np.clip(interpolated, 0, 255).astype(np.uint8)
            
            # Copy interpolated portion to result
            result[:, :interpolated_width] = interpolated
        
        # If we need more pixels than we can interpolate, pad with last pixel
        if interpolated_width < output_width:
            result[:, interpolated_width:] = source[:, -1:].astype(np.uint8)
        
        return result
    
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
            # Calculate total scroll distance needed
            # The image already includes display_width padding at the start, so we only need
            # to scroll total_scroll_width pixels to show all content once without looping
            total_scroll_distance = self.total_scroll_width
            
            # Calculate time based on scroll speed (pixels per second)
            # scroll_speed is pixels per second
            total_time = total_scroll_distance / self.scroll_speed
            
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
            
            self.logger.debug("Dynamic duration calculation:")
            self.logger.debug("  Display width: %dpx", self.display_width)
            self.logger.debug("  Content width: %dpx", self.total_scroll_width)
            self.logger.debug("  Total scroll distance: %dpx", total_scroll_distance)
            self.logger.debug("  Scroll speed: %.1f px/second", self.scroll_speed)
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
        self.logger.debug("Scroll position reset")
    
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
        Set the scroll speed.
        
        In time-based mode: pixels per second (typically 10-200)
        In frame-based mode: pixels per frame (typically 0.5-5 for smooth scrolling)
        
        Args:
            speed: Scroll speed (interpretation depends on frame_based_scrolling mode)
        """
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
    
    def set_target_fps(self, fps: float) -> None:
        """
        Set the target frames per second for scrolling.
        
        Args:
            fps: Target FPS (typically 30-200, default 120)
        """
        self.target_fps = max(30.0, min(200.0, fps))
        self.frame_time_target = 1.0 / self.target_fps
        self.logger.debug(f"Target FPS set to: {self.target_fps} FPS (frame_time_target: {self.frame_time_target:.4f}s)")
    
    def set_sub_pixel_scrolling(self, enabled: bool) -> None:
        """
        Enable or disable sub-pixel scrolling for smoother movement.
        
        When enabled, uses interpolation to blend between pixels for fractional
        scroll positions, resulting in smooth scrolling even at slow speeds.
        When disabled, uses integer pixel positioning (faster but may skip pixels).
        
        Args:
            enabled: True to enable sub-pixel scrolling (default: True)
        """
        self.sub_pixel_scrolling = enabled
        self.logger.debug(f"Sub-pixel scrolling {'enabled' if enabled else 'disabled'}")

    def set_frame_based_scrolling(self, enabled: bool) -> None:
        """
        Enable or disable frame-based scrolling.
        
        When enabled, update_scroll_position() respects scroll_delay and moves
        scroll_speed pixels per step. This provides a "stepped" look similar to
        traditional tickers and can be visually smoother on LED matrices.
        
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
        
        # Calculate instantaneous frame time
        frame_time = current_time - self.last_frame_time
        self.frame_times.append(frame_time)
        
        # Keep only last 100 frames for average
        if len(self.frame_times) > 100:
            self.frame_times.pop(0)
        
        # Log FPS every 5 seconds to avoid spam
        if current_time - self.last_fps_log_time >= 5.0:
            avg_frame_time = sum(self.frame_times) / len(self.frame_times)
            avg_fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0
            instant_fps = 1.0 / frame_time if frame_time > 0 else 0
            
            self.logger.info(f"Scroll frame stats - Avg FPS: {avg_fps:.1f}, "
                           f"Current FPS: {instant_fps:.1f}, "
                           f"Frame time: {frame_time*1000:.2f}ms")
            self.last_fps_log_time = current_time
            self.frame_count = 0
        
        self.last_frame_time = current_time
        self.frame_count += 1
    
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
        # The image already includes display_width padding, so we only need total_scroll_width
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
