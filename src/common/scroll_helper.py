"""
Scroll Helper

Handles scrolling text and image content for LED matrix displays.
Extracted from LEDMatrix core to provide reusable functionality for plugins.

Features:
- Pre-rendered scrolling image caching
- Scroll position management with wrap-around
- Dynamic duration calculation based on content width
- Frame rate tracking and logging
- Scrolling state management integration with display_manager
- Support for both continuous and bounded scrolling modes
"""

import logging
import time
from typing import Optional, Dict, Any
from PIL import Image


class ScrollHelper:
    """
    Helper class for scrolling text and image content on LED displays.
    
    Provides functionality for:
    - Creating and caching scrolling images
    - Managing scroll position with wrap-around
    - Calculating dynamic display duration
    - Frame rate tracking and performance monitoring
    - Integration with display manager scrolling state
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
        self.total_scroll_width = 0
        
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
            return Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        
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
        self.total_scroll_width = total_width
        self.scroll_position = 0.0
        self.total_distance_scrolled = 0.0
        self.scroll_complete = False
        
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
        if not hasattr(self, 'last_update_time'):
            self.last_update_time = current_time
        
        delta_time = current_time - self.last_update_time
        self.last_update_time = current_time

        if self.scroll_start_time is None:
            self.scroll_start_time = current_time
            self.last_progress_log_time = current_time
        
        # Update scroll position based on time delta for consistent speed
        # scroll_speed is now pixels per second, not per frame
        pixels_to_move = self.scroll_speed * delta_time
        self.scroll_position += pixels_to_move
        self.total_distance_scrolled += pixels_to_move
        
        # Calculate required total distance: display_width + total_scroll_width
        # This ensures all content scrolls fully off the left edge
        required_total_distance = self.display_width + self.total_scroll_width
        
        # Handle wrap-around - keep scrolling continuously
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
        
        # Mark complete only when we've scrolled the full required distance
        if self.total_distance_scrolled >= required_total_distance:
            elapsed = current_time - self.scroll_start_time
            self.scroll_complete = True
            self.logger.info(
                "Scroll cycle COMPLETE: scrolled %.0f/%d px (elapsed %.2fs, target %.2fs)",
                self.total_distance_scrolled,
                required_total_distance,
                elapsed,
                self.calculated_duration,
            )
        else:
            self.scroll_complete = False

        if (
            self.dynamic_duration_enabled
            and self.last_progress_log_time is not None
            and current_time - self.last_progress_log_time >= self.progress_log_interval
        ):
            elapsed_time = current_time - (self.scroll_start_time or current_time)
            required_total_distance = self.display_width + self.total_scroll_width
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
        Get the currently visible portion of the scrolling image.
        
        Returns:
            PIL Image showing the visible portion, or None if no cached image
        """
        if not self.cached_image:
            return None
        
        # Calculate visible region
        start_x = int(self.scroll_position)
        end_x = start_x + self.display_width
        
        # Handle wrap-around if needed
        if end_x <= self.cached_image.width:
            # Normal case: single crop
            return self.cached_image.crop((start_x, 0, end_x, self.display_height))
        else:
            # Wrap-around case: combine two crops
            width1 = self.cached_image.width - start_x
            if width1 > 0:
                # First part from end of image
                part1 = self.cached_image.crop((start_x, 0, self.cached_image.width, self.display_height))
                # Second part from beginning of image
                remaining_width = self.display_width - width1
                part2 = self.cached_image.crop((0, 0, remaining_width, self.display_height))
                
                # Combine the parts
                combined = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
                combined.paste(part1, (0, 0))
                combined.paste(part2, (width1, 0))
                return combined
            else:
                # Edge case: start_x >= image width
                return self.cached_image.crop((0, 0, self.display_width, self.display_height))
    
    def calculate_dynamic_duration(self) -> int:
        """
        Calculate display duration based on content width and scroll settings.
        
        Returns:
            Duration in seconds
        """
        if not self.dynamic_duration_enabled or not self.total_scroll_width:
            return self.min_duration
        
        try:
            # Calculate total scroll distance needed
            # Content needs to scroll from right edge to completely off left edge
            total_scroll_distance = self.display_width + self.total_scroll_width
            
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
        self.logger.debug("Scroll position reset")
    
    def set_scroll_speed(self, speed: float) -> None:
        """
        Set the scroll speed in pixels per second.
        
        Args:
            speed: Pixels to advance per second (typically 10-200)
        """
        self.scroll_speed = max(1.0, min(500.0, speed))
        self.logger.debug(f"Scroll speed set to: {self.scroll_speed} pixels/second")
    
    def set_scroll_delay(self, delay: float) -> None:
        """
        Set the delay between scroll frames.
        
        Args:
            delay: Delay in seconds (typically 0.001-0.1)
        """
        self.scroll_delay = max(0.001, min(1.0, delay))
        self.logger.debug(f"Scroll delay set to: {self.scroll_delay}")
    
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
        required_total_distance = self.display_width + self.total_scroll_width if self.total_scroll_width > 0 else 0
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
