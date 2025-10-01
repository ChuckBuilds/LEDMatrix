"""
Horizontal Scroll Manager Base Class

High-performance scrolling implementation designed for LED matrices capable of 100-200+ fps.
Decouples frame rate from scroll speed for maximum smoothness and control.

Key Design Principles:
- Frame rate independent: Scroll speed measured in pixels/second, not pixels/frame
- High FPS: Designed to run at 100-200+ fps for smooth, flicker-free scrolling
- Time-based animation: Uses delta time for consistent speed regardless of frame rate
- Flexible looping: Supports continuous loop, single pass, or modulo-based wrapping
- Dynamic duration: Automatically calculates display time based on content width
"""

import time
import logging
import gc
from typing import Dict, Any, Optional, Tuple
from PIL import Image, ImageDraw
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class HorizontalScrollManager(ABC):
    """
    Base class for high-performance horizontal scrolling displays.
    
    This class handles all scrolling logic, timing, and rendering while allowing
    subclasses to focus on content creation.
    
    Architecture:
    - Scroll speed is in pixels/second (independent of frame rate)
    - Frame updates happen as fast as possible (100-200+ fps)
    - Floating point scroll position for sub-pixel accuracy
    - Time-based delta calculation for smooth, consistent motion
    """
    
    def __init__(self, config: Dict[str, Any], display_manager, config_key: str = 'scroll'):
        """
        Initialize the horizontal scroll manager.
        
        Args:
            config: Full configuration dictionary
            display_manager: Display manager instance
            config_key: Key in config dict for this manager's settings
        """
        self.config = config
        self.display_manager = display_manager
        self.scroll_config = config.get(config_key, {})
        
        # ===== Scroll Speed Configuration =====
        # scroll_speed is in pixels per SECOND (not per frame)
        # This allows independent control of scroll speed and frame rate
        self.scroll_speed_pixels_per_second = self.scroll_config.get('scroll_speed', 50.0)
        
        # Target FPS for monitoring (actual FPS will vary based on system performance)
        self.target_fps = self.scroll_config.get('target_fps', 100.0)
        
        # Optional maximum FPS cap to prevent excessive CPU usage
        # 0 = unlimited, >0 = soft limit (skips frames if too fast)
        self.max_fps = self.scroll_config.get('max_fps', 100.0)
        
        # Minimum frame time based on max FPS (for soft limiting without sleep)
        self.min_frame_time = 1.0 / self.max_fps if self.max_fps > 0 else 0
        self.last_frame_start_time = 0.0  # Track frame start for soft limiting
        
        # ===== Scroll Position & Timing =====
        # Use floating point for sub-pixel accuracy (prevents stuttering)
        self.scroll_position = 0.0  # Current scroll position in pixels (float)
        self.last_update_time = 0.0  # Timestamp of last scroll update
        self.last_frame_time = 0.0  # Timestamp of last frame (for FPS calculation)
        
        # Delta time smoothing to handle FPS variance
        self.enable_delta_smoothing = self.scroll_config.get('enable_delta_smoothing', True)
        self.delta_smoothing_window = self.scroll_config.get('delta_smoothing_window', 5)
        self.delta_times = []  # Rolling window of delta times for smoothing
        
        # Max delta clamp to prevent large jumps when frames take too long
        # This prevents "catch-up" behavior that causes visible jumping
        self.max_delta_time = self.scroll_config.get('max_delta_time', 0.033)  # 30fps minimum (33ms max)
        
        # ===== Loop Configuration =====
        self.loop_mode = self.scroll_config.get('loop_mode', 'continuous')
        # Options:
        #   'continuous' - Loop back to start when reaching end
        #   'single' - Stop at the end
        #   'modulo' - Use modulo operator for seamless wrapping (most efficient)
        
        # ===== Image & Content State =====
        self.composite_image: Optional[Image.Image] = None  # The wide scrolling image
        self.total_content_width = 0  # Total width of content in pixels
        self._image_cache_valid = False  # Flag to track if image needs regeneration
        
        # ===== Dynamic Duration Settings =====
        self.dynamic_duration_enabled = self.scroll_config.get('dynamic_duration', True)
        self.min_duration = self.scroll_config.get('min_duration', 30)
        self.max_duration = self.scroll_config.get('max_duration', 300)
        self.duration_buffer = self.scroll_config.get('duration_buffer', 0.1)  # 10% buffer
        self.calculated_duration = 60.0  # Calculated display duration in seconds
        
        # ===== Display State =====
        self._display_start_time = 0.0  # When current display session started
        self._is_scrolling = False  # Current scrolling state
        self._scroll_completed = False  # True when scroll cycle completes (for single pass)
        
        # ===== Performance Monitoring =====
        self.enable_fps_logging = self.scroll_config.get('enable_fps_logging', False)
        self.fps_log_interval = self.scroll_config.get('fps_log_interval', 10.0)  # Log every N seconds
        self.last_fps_log_time = 0.0
        self.frame_times = []  # Rolling window of frame times for FPS calculation
        self.max_frame_samples = 100  # Keep last 100 frames for averaging
        
        # ===== Performance Optimization =====
        # Update display every N frames to reduce LED matrix overhead
        self.display_update_interval = self.scroll_config.get('display_update_interval', 1)
        # 1 = every frame (smoothest but highest CPU)
        # 2 = every other frame (still smooth, much lower CPU)
        # 3 = every 3rd frame (acceptable smoothness, lowest CPU)
        self.frame_counter = 0
        
        # Pre-allocate reusable objects to reduce GC pressure
        self._reusable_image = None  # Reuse for visible portion
        self._last_crop_region = None  # Cache last crop to detect changes
        
        # Optimize garbage collection to reduce pauses during scrolling
        # Increase GC thresholds to reduce collection frequency (fewer interruptions)
        gc_optimization = self.scroll_config.get('gc_optimization', True)
        if gc_optimization:
            # Get current thresholds
            gen0, gen1, gen2 = gc.get_threshold()
            # Increase thresholds to delay GC (reduce pauses during scrolling)
            gc.set_threshold(gen0 * 3, gen1 * 3, gen2 * 3)
            logger.debug(f"  - GC thresholds increased: {gc.get_threshold()} (from {gen0}, {gen1}, {gen2})")
            # Collect now to start fresh
            gc.collect()
        
        # ===== Wrap-Around Rendering =====
        self.enable_wrap_around = self.scroll_config.get('enable_wrap_around', True)
        # When enabled, seamlessly wraps content when looping (no gap)
        
        # ===== Throttling (Optional - NOT RECOMMENDED) =====
        # WARNING: Throttling uses time.sleep() which causes jitter!
        # Better to control speed via scroll_speed_pixels_per_second and let FPS run free
        self.enable_throttling = self.scroll_config.get('enable_throttling', False)  # Disabled by default
        # When enabled, limits frame rate to max_fps (causes jitter, not recommended)
        
        logger.info(f"[{self.__class__.__name__}] Initialized with:")
        logger.info(f"  - Scroll speed: {self.scroll_speed_pixels_per_second} px/s")
        logger.info(f"  - Target FPS: {self.target_fps}")
        logger.info(f"  - Max FPS: {self.max_fps}")
        logger.info(f"  - Loop mode: {self.loop_mode}")
        logger.info(f"  - Dynamic duration: {self.dynamic_duration_enabled}")
        logger.info(f"  - Delta smoothing: {self.enable_delta_smoothing} (window: {self.delta_smoothing_window})")
        logger.info(f"  - Display update interval: {self.display_update_interval} (every {self.display_update_interval} frame(s))")
        
        if self.display_update_interval > 1:
            effective_display_fps = self.target_fps / self.display_update_interval
            logger.info(f"  - Effective display FPS: ~{effective_display_fps:.0f} (scroll calculation: {self.target_fps})")
    
    # ===== Abstract Methods (Must be implemented by subclasses) =====
    
    @abstractmethod
    def _create_composite_image(self) -> Optional[Image.Image]:
        """
        Create the wide composite image containing all scrolling content.
        
        This method should:
        1. Calculate the total width needed for all content
        2. Create a PIL Image of size (total_width, display_height)
        3. Draw all content into the image horizontally
        4. Set self.total_content_width to the actual content width
        5. Return the created image
        
        Returns:
            PIL Image containing all content, or None on error
        """
        raise NotImplementedError("Subclass must implement _create_composite_image()")
    
    @abstractmethod
    def _get_content_data(self) -> Any:
        """
        Get the content data to be displayed.
        
        Returns:
            Content data (format depends on subclass), or None if no data available
        """
        raise NotImplementedError("Subclass must implement _get_content_data()")
    
    @abstractmethod
    def _display_fallback_message(self) -> None:
        """
        Display a fallback message when no content is available.
        
        This method should render a simple message to inform the user that
        content is not available (e.g., "No data", "Loading...", etc.)
        """
        raise NotImplementedError("Subclass must implement _display_fallback_message()")
    
    # ===== Scroll Position Management =====
    
    def update_scroll_position(self, delta_time: float) -> bool:
        """
        Update scroll position based on elapsed time.
        
        Uses time-based scrolling for consistent speed regardless of frame rate.
        Applies delta time smoothing and clamping to handle FPS variance and prevent stuttering.
        
        Args:
            delta_time: Time elapsed since last update (in seconds)
            
        Returns:
            True if scroll completed a full cycle, False otherwise
        """
        if not self._is_scrolling or self.total_content_width == 0:
            return False
        
        # Clamp delta time to prevent large jumps when frames take too long
        # This prevents "catch-up" behavior that causes visible jumping
        clamped_delta = min(delta_time, self.max_delta_time)
        
        # Apply delta time smoothing to handle FPS variance
        if self.enable_delta_smoothing:
            smoothed_delta = self._get_smoothed_delta_time(clamped_delta)
        else:
            smoothed_delta = clamped_delta
        
        # Calculate pixels to move based on smoothed time elapsed
        pixels_to_move = self.scroll_speed_pixels_per_second * smoothed_delta
        
        # Update position (floating point for sub-pixel accuracy)
        self.scroll_position += pixels_to_move
        
        # Handle boundaries based on loop mode
        return self._handle_boundaries()
    
    def _get_smoothed_delta_time(self, delta_time: float) -> float:
        """
        Smooth delta time over a rolling window to handle FPS variance.
        
        This prevents stuttering caused by inconsistent frame times by averaging
        delta_time over several frames.
        
        Args:
            delta_time: Raw time elapsed since last frame
            
        Returns:
            Smoothed delta time
        """
        # Add current delta to window
        self.delta_times.append(delta_time)
        
        # Keep only recent samples
        if len(self.delta_times) > self.delta_smoothing_window:
            self.delta_times.pop(0)
        
        # Return average of window
        if len(self.delta_times) > 0:
            return sum(self.delta_times) / len(self.delta_times)
        else:
            return delta_time
    
    def _handle_boundaries(self) -> bool:
        """
        Handle scroll boundaries based on loop mode.
        
        Returns:
            True if scroll cycle completed, False otherwise
        """
        display_width = self.display_manager.matrix.width
        
        if self.loop_mode == 'modulo':
            # Use modulo for seamless wrapping (most efficient)
            if self.scroll_position >= self.total_content_width:
                self.scroll_position = self.scroll_position % self.total_content_width
                return True  # Completed one cycle
            return False
        
        elif self.loop_mode == 'continuous':
            # Explicit loop back to start
            if self.scroll_position >= self.total_content_width:
                self.scroll_position = 0.0
                logger.debug(f"[{self.__class__.__name__}] Loop reset - starting new cycle")
                return True  # Completed one cycle
            return False
        
        elif self.loop_mode == 'single':
            # Stop at the end
            max_scroll = max(0, self.total_content_width - display_width)
            if self.scroll_position >= max_scroll:
                self.scroll_position = max_scroll
                if not self._scroll_completed:
                    self._scroll_completed = True
                    self._is_scrolling = False
                    self.display_manager.set_scrolling_state(False)
                    logger.info(f"[{self.__class__.__name__}] Reached end of content")
                return True  # Reached end
            return False
        
        else:
            logger.warning(f"[{self.__class__.__name__}] Unknown loop mode: {self.loop_mode}")
            return False
    
    def reset_scroll_position(self) -> None:
        """Reset scroll position to the beginning."""
        self.scroll_position = 0.0
        self._scroll_completed = False
        self._display_start_time = time.time()
        logger.debug(f"[{self.__class__.__name__}] Scroll position reset")
    
    # ===== Image Rendering =====
    
    def extract_visible_portion(self) -> Optional[Image.Image]:
        """
        Extract the currently visible portion of the composite image.
        
        Handles wrap-around rendering for seamless looping when enabled.
        Optimized to reuse image objects and reduce allocations.
        
        Returns:
            PIL Image of the visible portion, or None on error
        """
        if self.composite_image is None:
            return None
        
        display_width = self.display_manager.matrix.width
        display_height = self.display_manager.matrix.height
        
        # Round scroll position to nearest pixel for display
        scroll_pos_int = int(round(self.scroll_position))
        
        # Ensure scroll position is within valid range
        scroll_pos_int = max(0, min(scroll_pos_int, self.total_content_width))
        
        # Check if we need wrap-around rendering
        needs_wrap = (scroll_pos_int + display_width > self.total_content_width)
        
        if needs_wrap and self.enable_wrap_around and self.loop_mode in ['continuous', 'modulo']:
            # Seamless wrap-around rendering
            # Reuse image object to reduce allocations
            if self._reusable_image is None or self._reusable_image.size != (display_width, display_height):
                self._reusable_image = Image.new('RGB', (display_width, display_height), (0, 0, 0))
            else:
                # Clear the reusable image by pasting black rectangle
                draw = ImageDraw.Draw(self._reusable_image)
                draw.rectangle([(0, 0), (display_width, display_height)], fill=(0, 0, 0))
            
            # Paste main portion (from scroll position to end of content)
            main_width = self.total_content_width - scroll_pos_int
            if main_width > 0:
                main_portion = self.composite_image.crop((
                    scroll_pos_int, 0,
                    self.total_content_width, display_height
                ))
                self._reusable_image.paste(main_portion, (0, 0))
            
            # Paste wrapped portion (from beginning of content)
            wrap_width = display_width - main_width
            if wrap_width > 0:
                wrap_portion = self.composite_image.crop((
                    0, 0,
                    min(wrap_width, self.total_content_width), display_height
                ))
                self._reusable_image.paste(wrap_portion, (main_width, 0))
            
            return self._reusable_image
        
        else:
            # Simple crop (no wrap-around needed)
            # This is the fast path - just return a view of the composite image
            crop_end = min(scroll_pos_int + display_width, self.total_content_width)
            
            # Use crop which returns a view, not a copy (very fast!)
            visible_image = self.composite_image.crop((
                scroll_pos_int, 0,
                crop_end, display_height
            ))
            
            # If cropped image is smaller than display (at the end), pad with black
            if visible_image.width < display_width:
                # Reuse padding image
                if self._reusable_image is None or self._reusable_image.size != (display_width, display_height):
                    self._reusable_image = Image.new('RGB', (display_width, display_height), (0, 0, 0))
                else:
                    # Clear for reuse
                    draw = ImageDraw.Draw(self._reusable_image)
                    draw.rectangle([(0, 0), (display_width, display_height)], fill=(0, 0, 0))
                
                self._reusable_image.paste(visible_image, (0, 0))
                return self._reusable_image
            
            return visible_image
    
    # ===== Dynamic Duration Calculation =====
    
    def calculate_dynamic_duration(self) -> float:
        """
        Calculate the display duration needed to show all content.
        
        Returns:
            Calculated duration in seconds
        """
        if not self.dynamic_duration_enabled:
            fixed_duration = self.scroll_config.get('fixed_duration', 60.0)
            self.calculated_duration = fixed_duration
            logger.debug(f"[{self.__class__.__name__}] Using fixed duration: {fixed_duration}s")
            return fixed_duration
        
        if self.total_content_width == 0:
            self.calculated_duration = self.min_duration
            logger.debug(f"[{self.__class__.__name__}] No content, using min duration: {self.min_duration}s")
            return self.min_duration
        
        display_width = self.display_manager.matrix.width
        
        # Calculate scroll distance needed
        if self.loop_mode in ['continuous', 'modulo']:
            # For looping: need to scroll entire content width
            scroll_distance = self.total_content_width
        else:
            # For single pass: scroll until last content is visible
            scroll_distance = max(0, self.total_content_width - display_width)
        
        # Calculate base time: distance / speed
        base_time = scroll_distance / self.scroll_speed_pixels_per_second
        
        # Add buffer time (configurable percentage)
        buffer_time = base_time * self.duration_buffer
        calculated = base_time + buffer_time
        
        # Apply min/max constraints
        final_duration = max(self.min_duration, min(calculated, self.max_duration))
        
        self.calculated_duration = final_duration
        
        logger.info(f"[{self.__class__.__name__}] Dynamic duration calculation:")
        logger.info(f"  - Content width: {self.total_content_width}px")
        logger.info(f"  - Display width: {display_width}px")
        logger.info(f"  - Scroll distance: {scroll_distance}px")
        logger.info(f"  - Scroll speed: {self.scroll_speed_pixels_per_second} px/s")
        logger.info(f"  - Base time: {base_time:.2f}s")
        logger.info(f"  - Buffer: {buffer_time:.2f}s ({self.duration_buffer*100}%)")
        logger.info(f"  - Final duration: {final_duration:.2f}s")
        
        return final_duration
    
    def get_duration(self) -> float:
        """
        Get the current display duration.
        
        Returns:
            Duration in seconds
        """
        return self.calculated_duration
    
    # ===== Performance Monitoring =====
    
    def _update_fps_tracking(self, current_time: float) -> None:
        """
        Update FPS tracking and optionally log statistics.
        
        Args:
            current_time: Current timestamp
        """
        if self.last_frame_time > 0:
            frame_time = current_time - self.last_frame_time
            self.frame_times.append(frame_time)
            
            # Keep only recent samples
            if len(self.frame_times) > self.max_frame_samples:
                self.frame_times.pop(0)
        
        self.last_frame_time = current_time
        
        # Log FPS at configured interval
        if self.enable_fps_logging and current_time - self.last_fps_log_time >= self.fps_log_interval:
            self._log_fps_stats()
            self.last_fps_log_time = current_time
    
    def _log_fps_stats(self) -> None:
        """Log FPS statistics with frame time spike detection."""
        if not self.frame_times:
            return
        
        avg_frame_time = sum(self.frame_times) / len(self.frame_times)
        avg_fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0
        
        # Calculate min/max FPS
        min_frame_time = min(self.frame_times)
        max_frame_time = max(self.frame_times)
        max_fps = 1.0 / min_frame_time if min_frame_time > 0 else 0
        min_fps = 1.0 / max_frame_time if max_frame_time > 0 else 0
        
        # Detect frame time spikes (frames that took much longer than average)
        spike_threshold = avg_frame_time * 2.0  # Spikes are 2x average
        spikes = [ft for ft in self.frame_times if ft > spike_threshold]
        spike_count = len(spikes)
        spike_percentage = (spike_count / len(self.frame_times)) * 100 if self.frame_times else 0
        
        logger.info(f"[{self.__class__.__name__}] Performance stats:")
        logger.info(f"  - Average FPS: {avg_fps:.1f} (target: {self.target_fps})")
        logger.info(f"  - FPS range: {min_fps:.1f} - {max_fps:.1f}")
        logger.info(f"  - Avg frame time: {avg_frame_time*1000:.2f}ms")
        logger.info(f"  - Max frame time: {max_frame_time*1000:.2f}ms")
        logger.info(f"  - Frame time spikes: {spike_count}/{len(self.frame_times)} ({spike_percentage:.1f}%)")
        logger.info(f"  - Scroll position: {self.scroll_position:.1f}px / {self.total_content_width}px")
        
        # Warn if spike rate is high
        if spike_percentage > 5.0:
            logger.warning(f"  ⚠ High spike rate ({spike_percentage:.1f}%) - may cause visible shuddering")
            logger.warning(f"  Consider: reducing scroll_speed, simplifying content, or increasing display_update_interval")
    
    def get_current_fps(self) -> float:
        """
        Get the current average FPS.
        
        Returns:
            Average FPS over recent frames
        """
        if not self.frame_times:
            return 0.0
        avg_frame_time = sum(self.frame_times) / len(self.frame_times)
        return 1.0 / avg_frame_time if avg_frame_time > 0 else 0.0
    
    # ===== Image Cache Management =====
    
    def invalidate_image_cache(self) -> None:
        """Mark the composite image cache as invalid, forcing regeneration."""
        self._image_cache_valid = False
        self.composite_image = None
        logger.debug(f"[{self.__class__.__name__}] Image cache invalidated")
    
    def _ensure_composite_image(self) -> bool:
        """
        Ensure the composite image is created and up-to-date.
        
        Returns:
            True if image is ready, False on error
        """
        if self._image_cache_valid and self.composite_image is not None:
            return True
        
        try:
            logger.debug(f"[{self.__class__.__name__}] Creating composite image...")
            self.composite_image = self._create_composite_image()
            
            if self.composite_image is None:
                logger.error(f"[{self.__class__.__name__}] Failed to create composite image")
                return False
            
            self._image_cache_valid = True
            
            # Calculate dynamic duration after creating image
            self.calculate_dynamic_duration()
            
            logger.info(f"[{self.__class__.__name__}] Composite image created: {self.total_content_width}x{self.composite_image.height}px")
            return True
            
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] Error creating composite image: {e}", exc_info=True)
            return False
    
    # ===== Main Display Method =====
    
    def display(self, force_clear: bool = False) -> bool:
        """
        Main display method - call this every frame.
        
        This method:
        1. Checks if content is available
        2. Creates/updates the composite image if needed
        3. Updates scroll position based on elapsed time
        4. Extracts and displays the visible portion
        5. Monitors performance
        
        Args:
            force_clear: If True, reset scroll position and restart animation
            
        Returns:
            True if scroll cycle completed, False otherwise
        """
        current_time = time.time()
        
        # Soft FPS limiting (without sleep to avoid jitter)
        # Skip frame if we're running too fast
        if self.max_fps > 0 and self.last_frame_start_time > 0:
            time_since_last_frame = current_time - self.last_frame_start_time
            if time_since_last_frame < self.min_frame_time:
                # Running too fast, skip this frame
                return False
        
        # Record frame start time for FPS limiting
        self.last_frame_start_time = current_time
        
        # Handle force clear / reset
        if force_clear or self._display_start_time == 0:
            self.reset_scroll_position()
        
        # Check if content is available
        content = self._get_content_data()
        if not content:
            self._display_fallback_message()
            return False
        
        # Ensure composite image is ready
        if not self._ensure_composite_image():
            self._display_fallback_message()
            return False
        
        # Calculate delta time for time-based scrolling
        if self.last_update_time == 0:
            self.last_update_time = current_time
            delta_time = 0.0
        else:
            delta_time = current_time - self.last_update_time
        
        # Optional FPS throttling (prevents excessive CPU usage)
        # Do this BEFORE updating last_update_time for more consistent timing
        if self.enable_throttling and self.min_frame_time > 0:
            # Calculate how long this frame took so far
            frame_time_so_far = current_time - self.last_update_time
            
            # If we're ahead of schedule, sleep to maintain target FPS
            if frame_time_so_far < self.min_frame_time:
                sleep_time = self.min_frame_time - frame_time_so_far
                time.sleep(sleep_time)
                
                # Recalculate current time and delta after sleep
                current_time = time.time()
                delta_time = current_time - self.last_update_time
        
        # Update last_update_time for next frame
        self.last_update_time = current_time
        
        # Start scrolling if not already
        if not self._is_scrolling:
            self._is_scrolling = True
            self.display_manager.set_scrolling_state(True)
        
        # Update scroll position based on elapsed time (always update for accuracy)
        cycle_completed = self.update_scroll_position(delta_time)
        
        # Increment frame counter
        self.frame_counter += 1
        
        # Only update display every Nth frame to reduce LED matrix overhead
        # This significantly reduces CPU/GPU load while maintaining smooth scrolling
        # (scroll position still updates every frame for accuracy)
        should_update_display = (self.frame_counter % self.display_update_interval == 0)
        
        if should_update_display:
            # Extract visible portion (optimized to reuse objects)
            visible_image = self.extract_visible_portion()
            if visible_image is None:
                logger.error(f"[{self.__class__.__name__}] Failed to extract visible portion")
                return False
            
            # Update display
            try:
                self.display_manager.image = visible_image
                # Only create draw if needed (display_manager will create if needed)
                # Avoid unnecessary object allocation every frame
                if hasattr(self.display_manager, 'draw') and self.display_manager.draw is not None:
                    # Reuse existing draw object
                    pass
                else:
                    # Create draw object only if doesn't exist
                    self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
                self.display_manager.update_display()
            except Exception as e:
                logger.error(f"[{self.__class__.__name__}] Error updating display: {e}", exc_info=True)
                return False
        
        # Update FPS tracking (track all frames, not just display updates)
        self._update_fps_tracking(current_time)
        
        return cycle_completed
    
    # ===== Utility Methods =====
    
    def set_scroll_speed(self, pixels_per_second: float) -> None:
        """
        Update the scroll speed.
        
        Args:
            pixels_per_second: New scroll speed in pixels per second
        """
        self.scroll_speed_pixels_per_second = max(0.1, pixels_per_second)
        logger.info(f"[{self.__class__.__name__}] Scroll speed set to {self.scroll_speed_pixels_per_second} px/s")
        
        # Recalculate duration if dynamic
        if self.dynamic_duration_enabled:
            self.calculate_dynamic_duration()
    
    def set_loop_mode(self, mode: str) -> None:
        """
        Change the loop mode.
        
        Args:
            mode: One of 'continuous', 'single', or 'modulo'
        """
        if mode in ['continuous', 'single', 'modulo']:
            self.loop_mode = mode
            logger.info(f"[{self.__class__.__name__}] Loop mode set to: {mode}")
        else:
            logger.warning(f"[{self.__class__.__name__}] Invalid loop mode: {mode}")
    
    def get_progress(self) -> Tuple[float, float]:
        """
        Get scroll progress information.
        
        Returns:
            Tuple of (current_position, total_width)
        """
        return (self.scroll_position, self.total_content_width)
    
    def get_elapsed_time(self) -> float:
        """
        Get elapsed time since display started.
        
        Returns:
            Elapsed time in seconds
        """
        if self._display_start_time == 0:
            return 0.0
        return time.time() - self._display_start_time
    
    def get_remaining_time(self) -> float:
        """
        Get remaining time in current display cycle.
        
        Returns:
            Remaining time in seconds (negative if over duration)
        """
        return self.calculated_duration - self.get_elapsed_time()

