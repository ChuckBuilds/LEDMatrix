"""
Base scrolling class for standardized scroll logic across all display managers.

This class provides:
- High frame rate (100fps+) support
- Low resource usage optimized for Raspberry Pi
- Speed control separate from FPS
- Comprehensive debugging and diagnostics
- Smooth, flicker-free scrolling
- Display-size independent speed settings
- Multiple scrolling modes (continuous loop, one-shot, bounce)
"""

import time
import logging
from typing import Dict, Any, Optional, Callable, List
from PIL import Image
from enum import Enum
from collections import deque

logger = logging.getLogger(__name__)


class ScrollMode(Enum):
    """Scrolling behavior modes."""
    CONTINUOUS_LOOP = "continuous_loop"  # Loop back to start when reaching end
    ONE_SHOT = "one_shot"               # Stop at end, don't loop
    BOUNCE = "bounce"                   # Bounce back and forth
    STATIC = "static"                   # No scrolling


class ScrollDirection(Enum):
    """Scrolling direction."""
    LEFT = "left"      # Text moves left (standard ticker)
    RIGHT = "right"    # Text moves right
    UP = "up"          # Text moves up
    DOWN = "down"      # Text moves down


class ScrollMetrics:
    """Performance and diagnostic metrics for scrolling."""
    
    def __init__(self, buffer_size: int = 100):
        self.buffer_size = buffer_size
        self.frame_times = deque(maxlen=buffer_size)
        self.scroll_distances = deque(maxlen=buffer_size)
        self.last_metrics_log = 0
        self.metrics_log_interval = 30.0  # Log every 30 seconds
        
        # Counters
        self.total_frames = 0
        self.total_pixels_scrolled = 0
        self.start_time = time.time()
        
    def record_frame(self, frame_time: float, scroll_distance: float):
        """Record frame timing and scroll distance."""
        self.frame_times.append(frame_time)
        self.scroll_distances.append(scroll_distance)
        self.total_frames += 1
        self.total_pixels_scrolled += abs(scroll_distance)
        
    def get_current_fps(self) -> float:
        """Get current average FPS over the buffer window."""
        if len(self.frame_times) < 2:
            return 0.0
        return len(self.frame_times) / sum(self.frame_times)
    
    def get_average_scroll_speed(self) -> float:
        """Get average scroll speed in pixels per second."""
        if len(self.scroll_distances) == 0 or len(self.frame_times) == 0:
            return 0.0
        total_distance = sum(abs(d) for d in self.scroll_distances)
        total_time = sum(self.frame_times)
        return total_distance / total_time if total_time > 0 else 0.0
    
    def get_overall_fps(self) -> float:
        """Get overall FPS since start."""
        elapsed = time.time() - self.start_time
        return self.total_frames / elapsed if elapsed > 0 else 0.0
    
    def should_log_metrics(self) -> bool:
        """Check if it's time to log metrics."""
        current_time = time.time()
        if current_time - self.last_metrics_log >= self.metrics_log_interval:
            self.last_metrics_log = current_time
            return True
        return False


class BaseScrollController:
    """
    Base scrolling controller with standardized logic for all display types.
    
    Features:
    - Frame-rate independent scrolling using pixels per second
    - Multiple scroll modes (loop, one-shot, bounce)
    - Comprehensive performance metrics and debugging
    - Optimized for high frame rates (100fps+) and low resource usage
    - Display-size independent speed configuration
    """
    
    def __init__(self, 
                 config: Dict[str, Any], 
                 display_width: int, 
                 display_height: int,
                 content_width: int = 0,
                 content_height: int = 0,
                 debug_name: str = "BaseScroll"):
        """
        Initialize the scroll controller.
        
        Args:
            config: Configuration dictionary with scroll settings
            display_width: Width of the display area in pixels
            display_height: Height of the display area in pixels
            content_width: Width of the content to scroll (0 = auto-detect)
            content_height: Height of the content to scroll (0 = auto-detect)
            debug_name: Name for debugging/logging purposes
        """
        self.config = config
        self.display_width = display_width
        self.display_height = display_height
        self.content_width = content_width
        self.content_height = content_height
        self.debug_name = debug_name
        
        # Core scrolling state
        self.scroll_position = 0.0  # Current scroll position (float for precision)
        self.scroll_direction_multiplier = -1  # -1 for left, 1 for right, etc.
        self.last_update_time = 0.0
        self.is_scrolling_active = False
        
        # Configuration with smart defaults
        self._load_config()
        
        # Performance metrics
        self.metrics = ScrollMetrics()
        self.enable_metrics = self.config.get('enable_scroll_metrics', False)
        
        # State tracking
        self.mode = ScrollMode(self.config.get('scroll_mode', 'continuous_loop'))
        self.direction = ScrollDirection(self.config.get('scroll_direction', 'left'))
        self._update_direction_multiplier()
        
        # Bounce mode state
        self.bounce_direction = 1  # 1 for forward, -1 for backward
        
        logger.debug(f"{self.debug_name}: Initialized scroll controller - "
                    f"Mode: {self.mode.value}, Speed: {self.pixels_per_second}px/s, "
                    f"Direction: {self.direction.value}, Display: {display_width}x{display_height}")
    
    def _load_config(self):
        """Load and validate configuration with smart defaults."""
        # Primary speed control - pixels per second (display-size independent)
        self.pixels_per_second = self.config.get('scroll_pixels_per_second', 
                                                self.config.get('pixels_per_second', 20.0))
        
        # Fallback to legacy settings if new ones not present
        if 'scroll_pixels_per_second' not in self.config and 'pixels_per_second' not in self.config:
            # Convert legacy scroll_speed to pixels_per_second
            legacy_speed = self.config.get('scroll_speed', 1)
            legacy_delay = self.config.get('scroll_delay', 0.05)
            # Estimate: speed / delay gives approximate pixels per second
            self.pixels_per_second = legacy_speed / max(legacy_delay, 0.001)
            logger.info(f"{self.debug_name}: Converted legacy scroll settings - "
                       f"speed={legacy_speed}, delay={legacy_delay} -> {self.pixels_per_second:.1f}px/s")
        
        # Frame rate control
        self.target_fps = self.config.get('scroll_target_fps', 100.0)
        self.max_fps = self.config.get('scroll_max_fps', 120.0)
        
        # Smoothing and performance
        self.enable_subpixel_positioning = self.config.get('scroll_subpixel_positioning', True)
        self.frame_skip_threshold = self.config.get('scroll_frame_skip_threshold', 0.001)  # Skip frames < 1ms
        
        # Gap settings for looping
        self.loop_gap_pixels = self.config.get('scroll_loop_gap_pixels', self.display_width // 2)
        
        # Validation
        self.pixels_per_second = max(0.1, min(1000.0, self.pixels_per_second))  # Reasonable bounds
        self.target_fps = max(10.0, min(200.0, self.target_fps))
        
    def _update_direction_multiplier(self):
        """Update the direction multiplier based on scroll direction."""
        if self.direction == ScrollDirection.LEFT:
            self.scroll_direction_multiplier = 1  # Positive scroll moves content left
        elif self.direction == ScrollDirection.RIGHT:
            self.scroll_direction_multiplier = -1  # Negative scroll moves content right
        elif self.direction == ScrollDirection.UP:
            self.scroll_direction_multiplier = 1  # Positive scroll moves content up
        elif self.direction == ScrollDirection.DOWN:
            self.scroll_direction_multiplier = -1  # Negative scroll moves content down
    
    def set_content_dimensions(self, width: int, height: int):
        """Update content dimensions (call when content changes)."""
        self.content_width = width
        self.content_height = height
        
        # Reset scroll position if content changed significantly
        if self.mode == ScrollMode.CONTINUOUS_LOOP:
            # Allow position to continue for smooth transitions
            pass
        else:
            self.scroll_position = 0.0
    
    def set_scroll_speed(self, pixels_per_second: float):
        """Update scroll speed in pixels per second."""
        self.pixels_per_second = max(0.1, min(1000.0, pixels_per_second))
        logger.debug(f"{self.debug_name}: Updated scroll speed to {self.pixels_per_second:.1f}px/s")
    
    def set_scroll_mode(self, mode: ScrollMode):
        """Change scroll mode."""
        if mode != self.mode:
            self.mode = mode
            self.scroll_position = 0.0  # Reset position on mode change
            self.bounce_direction = 1
            logger.debug(f"{self.debug_name}: Changed scroll mode to {mode.value}")
    
    def set_scroll_direction(self, direction: ScrollDirection):
        """Change scroll direction."""
        if direction != self.direction:
            self.direction = direction
            self._update_direction_multiplier()
            logger.debug(f"{self.debug_name}: Changed scroll direction to {direction.value}")
    
    def should_scroll(self) -> bool:
        """Determine if scrolling should be active based on content size and mode."""
        if self.mode == ScrollMode.STATIC:
            return False
        
        # For horizontal scrolling
        if self.direction in [ScrollDirection.LEFT, ScrollDirection.RIGHT]:
            return self.content_width > self.display_width
        
        # For vertical scrolling
        if self.direction in [ScrollDirection.UP, ScrollDirection.DOWN]:
            return self.content_height > self.display_height
        
        return False
    
    def update(self, current_time: Optional[float] = None) -> Dict[str, Any]:
        """
        Update scroll position and return scroll state information.
        
        Args:
            current_time: Current time (if None, uses time.time())
        
        Returns:
            Dictionary with scroll state information:
            - scroll_position: Current scroll position
            - scroll_delta: Change in position this frame
            - is_scrolling: Whether scrolling is active
            - fps: Current FPS (if metrics enabled)
            - needs_content_update: Whether content should be regenerated
        """
        if current_time is None:
            current_time = time.time()
        
        # Initialize timing on first call
        if self.last_update_time == 0:
            self.last_update_time = current_time
            self.is_scrolling_active = self.should_scroll()
            return {
                'scroll_position': self.scroll_position,
                'scroll_delta': 0.0,
                'is_scrolling': self.is_scrolling_active,
                'fps': 0.0,
                'needs_content_update': False
            }
        
        # Calculate frame time
        frame_time = current_time - self.last_update_time
        self.last_update_time = current_time
        
        # Skip very fast frames to prevent excessive CPU usage
        if frame_time < self.frame_skip_threshold:
            return {
                'scroll_position': self.scroll_position,
                'scroll_delta': 0.0,
                'is_scrolling': self.is_scrolling_active,
                'fps': self.metrics.get_current_fps() if self.enable_metrics else 0.0,
                'needs_content_update': False
            }
        
        # Check if we should be scrolling
        self.is_scrolling_active = self.should_scroll()
        scroll_delta = 0.0
        needs_content_update = False
        
        if self.is_scrolling_active and self.mode != ScrollMode.STATIC:
            # Calculate scroll distance for this frame
            base_scroll_distance = self.pixels_per_second * frame_time
            
            # Apply direction and bounce mode
            if self.mode == ScrollMode.BOUNCE:
                scroll_delta = base_scroll_distance * self.bounce_direction * self.scroll_direction_multiplier
            else:
                scroll_delta = base_scroll_distance * self.scroll_direction_multiplier
            
            # Update position
            old_position = self.scroll_position
            self.scroll_position += scroll_delta
            
            # Handle different scroll modes
            if self.mode == ScrollMode.CONTINUOUS_LOOP:
                total_scroll_width = self.content_width + self.loop_gap_pixels
                if total_scroll_width > 0:
                    if self.scroll_position >= total_scroll_width:
                        self.scroll_position = self.scroll_position % total_scroll_width
                        needs_content_update = True
                    elif self.scroll_position < 0:
                        self.scroll_position = total_scroll_width + (self.scroll_position % total_scroll_width)
                        needs_content_update = True
            
            elif self.mode == ScrollMode.ONE_SHOT:
                max_position = max(0, self.content_width - self.display_width)
                if self.scroll_position >= max_position:
                    self.scroll_position = max_position
                    self.is_scrolling_active = False
                elif self.scroll_position < 0:
                    self.scroll_position = 0
                    self.is_scrolling_active = False
            
            elif self.mode == ScrollMode.BOUNCE:
                max_position = max(0, self.content_width - self.display_width)
                if self.scroll_position >= max_position and self.bounce_direction > 0:
                    self.scroll_position = max_position
                    self.bounce_direction = -1
                elif self.scroll_position <= 0 and self.bounce_direction < 0:
                    self.scroll_position = 0
                    self.bounce_direction = 1
        
        # Record metrics
        if self.enable_metrics:
            self.metrics.record_frame(frame_time, abs(scroll_delta))
            
            # Log metrics periodically
            if self.metrics.should_log_metrics():
                current_fps = self.metrics.get_current_fps()
                avg_speed = self.metrics.get_average_scroll_speed()
                overall_fps = self.metrics.get_overall_fps()
                
                logger.info(f"{self.debug_name} Metrics - "
                           f"FPS: {current_fps:.1f} (avg: {overall_fps:.1f}), "
                           f"Speed: {avg_speed:.1f}px/s (target: {self.pixels_per_second:.1f}), "
                           f"Frames: {self.metrics.total_frames}, "
                           f"Position: {self.scroll_position:.1f}/{self.content_width}")
        
        return {
            'scroll_position': self.scroll_position,
            'scroll_delta': scroll_delta,
            'is_scrolling': self.is_scrolling_active,
            'fps': self.metrics.get_current_fps() if self.enable_metrics else 0.0,
            'needs_content_update': needs_content_update
        }
    
    def get_crop_region(self, wrap_around: bool = True) -> Dict[str, Any]:
        """
        Get the crop region for the current scroll position.
        
        Args:
            wrap_around: Whether to handle wrap-around for continuous scrolling
        
        Returns:
            Dictionary with crop information:
            - source_x, source_y: Source coordinates
            - width, height: Crop dimensions
            - needs_wrap: Whether wrap-around is needed
            - wrap_segments: List of segments for wrap-around rendering
        """
        if self.direction in [ScrollDirection.LEFT, ScrollDirection.RIGHT]:
            return self._get_horizontal_crop_region(wrap_around)
        else:
            return self._get_vertical_crop_region(wrap_around)
    
    def _get_horizontal_crop_region(self, wrap_around: bool) -> Dict[str, Any]:
        """Get crop region for horizontal scrolling."""
        source_x = int(self.scroll_position) if not self.enable_subpixel_positioning else self.scroll_position
        source_y = 0
        width = self.display_width
        height = self.display_height
        
        # Check if we need wrap-around
        if wrap_around and self.mode == ScrollMode.CONTINUOUS_LOOP:
            total_width = self.content_width + self.loop_gap_pixels
            
            if source_x + width > total_width:
                # Need wrap-around
                segment1_width = total_width - source_x
                segment2_width = width - segment1_width
                
                return {
                    'source_x': source_x,
                    'source_y': source_y,
                    'width': width,
                    'height': height,
                    'needs_wrap': True,
                    'wrap_segments': [
                        {'source_x': source_x, 'source_y': 0, 'width': segment1_width, 'dest_x': 0},
                        {'source_x': 0, 'source_y': 0, 'width': segment2_width, 'dest_x': segment1_width}
                    ]
                }
        
        return {
            'source_x': source_x,
            'source_y': source_y,
            'width': width,
            'height': height,
            'needs_wrap': False,
            'wrap_segments': []
        }
    
    def _get_vertical_crop_region(self, wrap_around: bool) -> Dict[str, Any]:
        """Get crop region for vertical scrolling."""
        source_x = 0
        source_y = int(self.scroll_position) if not self.enable_subpixel_positioning else self.scroll_position
        width = self.display_width
        height = self.display_height
        
        # Similar logic for vertical wrap-around
        if wrap_around and self.mode == ScrollMode.CONTINUOUS_LOOP:
            total_height = self.content_height + self.loop_gap_pixels
            
            if source_y + height > total_height:
                segment1_height = total_height - source_y
                segment2_height = height - segment1_height
                
                return {
                    'source_x': source_x,
                    'source_y': source_y,
                    'width': width,
                    'height': height,
                    'needs_wrap': True,
                    'wrap_segments': [
                        {'source_x': 0, 'source_y': source_y, 'height': segment1_height, 'dest_y': 0},
                        {'source_x': 0, 'source_y': 0, 'height': segment2_height, 'dest_y': segment1_height}
                    ]
                }
        
        return {
            'source_x': source_x,
            'source_y': source_y,
            'width': width,
            'height': height,
            'needs_wrap': False,
            'wrap_segments': []
        }
    
    def reset(self):
        """Reset scroll position to start."""
        self.scroll_position = 0.0
        self.bounce_direction = 1
        self.last_update_time = 0.0
        logger.debug(f"{self.debug_name}: Reset scroll position")
    
    def reset_scroll(self):
        """Reset scroll position to start (alias for reset)."""
        self.reset()
    
    def is_complete(self) -> bool:
        """
        Check if scroll animation is complete.
        
        Returns:
            True if scrolling is complete, False otherwise
        """
        if not self.is_scrolling_active:
            return True
        
        if self.mode == ScrollMode.STATIC:
            return True
        
        if self.mode == ScrollMode.CONTINUOUS_LOOP:
            # Continuous loop never completes
            return False
        
        if self.mode == ScrollMode.ONE_SHOT:
            # Complete when we've scrolled to the end
            max_position = max(0, self.content_width - self.display_width)
            return self.scroll_position >= max_position
        
        if self.mode == ScrollMode.BOUNCE:
            # Bounce mode completes when it reaches the end in forward direction
            max_position = max(0, self.content_width - self.display_width)
            return (self.scroll_position >= max_position and 
                    self.bounce_direction > 0)
        
        return False
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current scroll performance metrics."""
        if not self.enable_metrics:
            return {}
        
        return {
            'current_fps': self.metrics.get_current_fps(),
            'overall_fps': self.metrics.get_overall_fps(),
            'average_scroll_speed': self.metrics.get_average_scroll_speed(),
            'total_frames': self.metrics.total_frames,
            'total_pixels_scrolled': self.metrics.total_pixels_scrolled,
            'target_fps': self.target_fps,
            'pixels_per_second': self.pixels_per_second,
            'scroll_position': self.scroll_position,
            'is_scrolling_active': self.is_scrolling_active
        }
    
    def get_debug_info(self) -> Dict[str, Any]:
        """Get comprehensive debug information."""
        return {
            'debug_name': self.debug_name,
            'scroll_position': self.scroll_position,
            'content_size': f"{self.content_width}x{self.content_height}",
            'display_size': f"{self.display_width}x{self.display_height}",
            'pixels_per_second': self.pixels_per_second,
            'mode': self.mode.value,
            'direction': self.direction.value,
            'is_scrolling_active': self.is_scrolling_active,
            'should_scroll': self.should_scroll(),
            'current_fps': self.metrics.get_current_fps() if self.enable_metrics else 0.0,
            'target_fps': self.target_fps,
            'total_frames': self.metrics.total_frames,
            'total_pixels_scrolled': self.metrics.total_pixels_scrolled
        }
