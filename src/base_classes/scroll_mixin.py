"""
Scroll mixin for easy integration of BaseScrollController into existing display managers.

This mixin provides a simple interface to add standardized scrolling to any display manager
without requiring major refactoring of existing code.
"""

import time
import logging
from typing import Dict, Any, Optional, Tuple
from PIL import Image
from .scroll_base import BaseScrollController, ScrollMode, ScrollDirection

logger = logging.getLogger(__name__)


class ScrollMixin:
    """
    Mixin class to add standardized scrolling capabilities to display managers.
    
    Usage:
        class MyDisplayManager(ScrollMixin):
            def __init__(self, config, display_manager):
                self.config = config
                self.display_manager = display_manager
                self.init_scroll_controller('my_display')
                
            def display(self):
                # Your existing display logic
                scroll_state = self.update_scroll()
                if scroll_state['is_scrolling']:
                    # Use scroll_state['scroll_position'] for rendering
                    pass
    """
    
    def init_scroll_controller(self, 
                             debug_name: str,
                             config_section: Optional[str] = None,
                             content_width: int = 0,
                             content_height: int = 0):
        """
        Initialize the scroll controller.
        
        Args:
            debug_name: Name for debugging/logging
            config_section: Section in config to read scroll settings from (defaults to debug_name)
            content_width: Initial content width
            content_height: Initial content height
        """
        if not hasattr(self, 'display_manager'):
            raise AttributeError("ScrollMixin requires display_manager attribute to be set")
        
        if not hasattr(self, 'config'):
            raise AttributeError("ScrollMixin requires config attribute to be set")
        
        # Get scroll configuration
        if config_section is None:
            config_section = debug_name.lower().replace(' ', '_')
        
        scroll_config = self.config.get(config_section, {})
        
        # Initialize the scroll controller
        self.scroll_controller = BaseScrollController(
            config=scroll_config,
            display_width=self.display_manager.matrix.width,
            display_height=self.display_manager.matrix.height,
            content_width=content_width,
            content_height=content_height,
            debug_name=debug_name
        )
        
        # Track last scroll state for change detection
        self._last_scroll_state = None
        
        logger.debug(f"{debug_name}: Initialized scroll controller with config section '{config_section}'")
    
    def update_scroll(self, current_time: Optional[float] = None) -> Dict[str, Any]:
        """
        Update scroll position and return current state.
        
        Args:
            current_time: Current time (if None, uses time.time())
        
        Returns:
            Dictionary with scroll state information
        """
        if not hasattr(self, 'scroll_controller'):
            raise AttributeError("ScrollMixin not initialized. Call init_scroll_controller() first.")
        
        scroll_state = self.scroll_controller.update(current_time)
        
        # Notify display manager of scrolling state for deferred updates
        if hasattr(self.display_manager, 'set_scrolling_state'):
            self.display_manager.set_scrolling_state(scroll_state['is_scrolling'])
        
        # Process deferred updates when not scrolling
        if hasattr(self.display_manager, 'process_deferred_updates'):
            if not scroll_state['is_scrolling']:
                self.display_manager.process_deferred_updates()
        
        self._last_scroll_state = scroll_state
        return scroll_state
    
    def update_scroll_content_size(self, width: int, height: int):
        """Update the content dimensions for scrolling calculations."""
        if hasattr(self, 'scroll_controller'):
            self.scroll_controller.set_content_dimensions(width, height)
    
    def set_scroll_speed(self, pixels_per_second: float):
        """Update scroll speed in pixels per second."""
        if hasattr(self, 'scroll_controller'):
            self.scroll_controller.set_scroll_speed(pixels_per_second)
    
    def set_scroll_mode(self, mode: str):
        """Change scroll mode ('continuous_loop', 'one_shot', 'bounce', 'static')."""
        if hasattr(self, 'scroll_controller'):
            self.scroll_controller.set_scroll_mode(ScrollMode(mode))
    
    def set_scroll_direction(self, direction: str):
        """Change scroll direction ('left', 'right', 'up', 'down')."""
        if hasattr(self, 'scroll_controller'):
            self.scroll_controller.set_scroll_direction(ScrollDirection(direction))
    
    def reset_scroll_position(self):
        """Reset scroll position to the beginning."""
        if hasattr(self, 'scroll_controller'):
            self.scroll_controller.reset()
    
    def get_scroll_crop_region(self, wrap_around: bool = True) -> Dict[str, Any]:
        """Get the crop region for the current scroll position."""
        if hasattr(self, 'scroll_controller'):
            return self.scroll_controller.get_crop_region(wrap_around)
        return {
            'source_x': 0, 'source_y': 0,
            'width': self.display_manager.matrix.width,
            'height': self.display_manager.matrix.height,
            'needs_wrap': False, 'wrap_segments': []
        }
    
    def crop_scrolled_image(self, 
                           source_image: Image.Image, 
                           wrap_around: bool = True) -> Image.Image:
        """
        Crop the source image based on current scroll position.
        
        Args:
            source_image: The full content image to crop from
            wrap_around: Whether to handle wrap-around for continuous scrolling
        
        Returns:
            Cropped image ready for display
        """
        if not hasattr(self, 'scroll_controller'):
            # Fallback: return a crop of the source image
            return source_image.crop((0, 0, 
                                    self.display_manager.matrix.width, 
                                    self.display_manager.matrix.height))
        
        crop_info = self.get_scroll_crop_region(wrap_around)
        display_width = self.display_manager.matrix.width
        display_height = self.display_manager.matrix.height
        
        if not crop_info['needs_wrap']:
            # Simple crop
            source_x = max(0, min(int(crop_info['source_x']), source_image.width))
            source_y = max(0, min(int(crop_info['source_y']), source_image.height))
            end_x = min(source_x + display_width, source_image.width)
            end_y = min(source_y + display_height, source_image.height)
            
            cropped = source_image.crop((source_x, source_y, end_x, end_y))
            
            # If cropped image is smaller than display, pad with background
            if cropped.size != (display_width, display_height):
                padded = Image.new('RGB', (display_width, display_height), (0, 0, 0))
                padded.paste(cropped, (0, 0))
                return padded
            
            return cropped
        
        else:
            # Handle wrap-around
            result = Image.new('RGB', (display_width, display_height), (0, 0, 0))
            
            for segment in crop_info['wrap_segments']:
                # Extract segment information
                seg_src_x = max(0, min(int(segment['source_x']), source_image.width))
                seg_src_y = max(0, min(int(segment.get('source_y', 0)), source_image.height))
                seg_width = segment.get('width', segment.get('height', 0))
                seg_height = segment.get('height', display_height)
                
                # Destination position
                dest_x = segment.get('dest_x', 0)
                dest_y = segment.get('dest_y', 0)
                
                # Crop segment from source
                seg_end_x = min(seg_src_x + seg_width, source_image.width)
                seg_end_y = min(seg_src_y + seg_height, source_image.height)
                
                if seg_end_x > seg_src_x and seg_end_y > seg_src_y:
                    segment_img = source_image.crop((seg_src_x, seg_src_y, seg_end_x, seg_end_y))
                    result.paste(segment_img, (dest_x, dest_y))
            
            return result
    
    def get_scroll_debug_info(self) -> Dict[str, Any]:
        """Get debug information about the current scroll state."""
        if hasattr(self, 'scroll_controller'):
            return self.scroll_controller.get_debug_info()
        return {'error': 'ScrollMixin not initialized'}
    
    def should_scroll(self) -> bool:
        """Check if content should be scrolling based on current conditions."""
        if hasattr(self, 'scroll_controller'):
            return self.scroll_controller.should_scroll()
        return False
    
    def is_currently_scrolling(self) -> bool:
        """Check if scrolling is currently active."""
        if hasattr(self, 'scroll_controller'):
            return self.scroll_controller.is_scrolling_active
        return False
    
    def get_current_scroll_position(self) -> float:
        """Get the current scroll position."""
        if hasattr(self, 'scroll_controller'):
            return self.scroll_controller.scroll_position
        return 0.0
    
    def calculate_scroll_frame_delay(self, target_fps: Optional[float] = None) -> float:
        """
        Calculate the appropriate frame delay for smooth scrolling.
        
        Args:
            target_fps: Target frame rate (uses controller's target if None)
        
        Returns:
            Frame delay in seconds
        """
        if hasattr(self, 'scroll_controller'):
            fps = target_fps or self.scroll_controller.target_fps
            return 1.0 / max(10.0, min(200.0, fps))
        return 0.01  # Default 100 FPS
    
    def log_scroll_performance(self, force: bool = False):
        """
        Log scroll performance metrics.
        
        Args:
            force: Force logging even if not time for periodic log
        """
        if hasattr(self, 'scroll_controller') and self.scroll_controller.enable_metrics:
            if force or self.scroll_controller.metrics.should_log_metrics():
                debug_info = self.get_scroll_debug_info()
                logger.info(f"Scroll Performance - {debug_info['debug_name']}: "
                           f"FPS: {debug_info['current_fps']:.1f}, "
                           f"Speed: {debug_info['pixels_per_second']:.1f}px/s, "
                           f"Position: {debug_info['scroll_position']:.1f}, "
                           f"Frames: {debug_info['total_frames']}")


class LegacyScrollAdapter:
    """
    Adapter to help migrate existing scroll implementations to the new base class.
    
    This provides a compatibility layer for existing code that uses legacy scroll parameters.
    """
    
    @staticmethod
    def convert_legacy_config(config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert legacy scroll configuration to new format.
        
        Args:
            config: Dictionary with legacy scroll settings
        
        Returns:
            Dictionary with new scroll configuration
        """
        new_config = config.copy()
        
        # Convert scroll_speed and scroll_delay to pixels_per_second
        if 'scroll_speed' in config and 'scroll_delay' in config:
            scroll_speed = config['scroll_speed']
            scroll_delay = max(config['scroll_delay'], 0.001)  # Prevent division by zero
            
            # Legacy formula: pixels_per_second = scroll_speed / scroll_delay
            pixels_per_second = scroll_speed / scroll_delay
            new_config['scroll_pixels_per_second'] = pixels_per_second
            
            logger.debug(f"Converted legacy scroll settings: "
                        f"speed={scroll_speed}, delay={scroll_delay} -> {pixels_per_second:.1f}px/s")
        
        # Convert scroll_speed_scale if present (used in some managers)
        if 'scroll_speed_scale' in config:
            scale = config['scroll_speed_scale']
            base_speed = new_config.get('scroll_pixels_per_second', 20.0)
            new_config['scroll_pixels_per_second'] = base_speed * scale
        
        # Set reasonable defaults
        new_config.setdefault('scroll_target_fps', 100.0)
        new_config.setdefault('scroll_mode', 'continuous_loop')
        new_config.setdefault('scroll_direction', 'left')
        new_config.setdefault('enable_scroll_metrics', False)
        
        return new_config
    
    @staticmethod
    def create_scroll_controller_from_legacy(config: Dict[str, Any], 
                                           display_manager,
                                           debug_name: str) -> BaseScrollController:
        """
        Create a BaseScrollController from legacy configuration.
        
        Args:
            config: Legacy configuration dictionary
            display_manager: Display manager instance
            debug_name: Name for debugging
        
        Returns:
            Configured BaseScrollController
        """
        converted_config = LegacyScrollAdapter.convert_legacy_config(config)
        
        return BaseScrollController(
            config=converted_config,
            display_width=display_manager.matrix.width,
            display_height=display_manager.matrix.height,
            debug_name=debug_name
        )
