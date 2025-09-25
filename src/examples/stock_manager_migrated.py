"""
Example migration of StockManager to use the new BaseScrollController.

This demonstrates how to migrate an existing display manager to use the
standardized scroll base class with minimal code changes.
"""

import time
import logging
import requests
import json
from typing import Dict, Any, List, Tuple
from PIL import Image, ImageDraw, ImageFont
from ..base_classes.scroll_mixin import ScrollMixin, LegacyScrollAdapter
from ..cache_manager import CacheManager

logger = logging.getLogger(__name__)


class MigratedStockManager(ScrollMixin):
    """
    Example of StockManager migrated to use BaseScrollController.
    
    Key changes from original:
    1. Inherits from ScrollMixin
    2. Uses standardized scroll configuration
    3. Simplified scroll logic - just call update_scroll() and crop_scrolled_image()
    4. Built-in performance metrics and debugging
    5. Frame-rate independent scrolling
    """
    
    def __init__(self, config: Dict[str, Any], display_manager):
        self.config = config
        self.display_manager = display_manager
        self.stocks_config = config.get('stocks', {})
        
        # Stock data management
        self.last_update = 0
        self.stock_data = {}
        self.current_stock_index = 0
        self.cached_text_image = None
        self.cached_text = None
        self.cache_manager = CacheManager()
        
        # Convert legacy scroll config and initialize scroll controller
        scroll_config = LegacyScrollAdapter.convert_legacy_config(self.stocks_config)
        self.stocks_config.update(scroll_config)
        
        # Initialize scroll controller (this replaces all the manual scroll variables)
        self.init_scroll_controller(
            debug_name="StockManager",
            config_section='stocks',
            content_width=0,  # Will be updated when content is created
            content_height=self.display_manager.matrix.height
        )
        
        # Other stock-specific settings
        self.toggle_chart = self.stocks_config.get('toggle_chart', False)
        self.dynamic_duration_enabled = self.stocks_config.get('dynamic_duration', True)
        self.min_duration = self.stocks_config.get('min_duration', 30)
        self.max_duration = self.stocks_config.get('max_duration', 300)
        
        logger.info("MigratedStockManager initialized with standardized scrolling")
    
    def should_update(self) -> bool:
        """Check if stock data should be updated."""
        update_interval = self.stocks_config.get('update_interval', 600)
        return (time.time() - self.last_update) > update_interval
    
    def fetch_stock_data(self):
        """Fetch stock data from API (simplified for example)."""
        # This would contain the actual stock fetching logic
        # For this example, we'll use dummy data
        self.stock_data = {
            'AAPL': {'price': 150.25, 'change': 2.5, 'change_percent': 1.69},
            'GOOGL': {'price': 2750.80, 'change': -15.20, 'change_percent': -0.55},
            'TSLA': {'price': 800.45, 'change': 12.30, 'change_percent': 1.56},
        }
        self.last_update = time.time()
        logger.debug("Stock data updated (example data)")
    
    def create_stock_display_image(self) -> Image.Image:
        """
        Create the full stock ticker image.
        
        This replaces the complex scrolling logic with simple image creation.
        The scroll controller handles all the positioning and animation.
        """
        if not self.stock_data:
            # Create a simple "loading" image
            width = self.display_manager.matrix.width * 2  # Make it wider for demo
            height = self.display_manager.matrix.height
            image = Image.new('RGB', (width, height), (0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.text((10, height // 2 - 5), "Loading stocks...", 
                     fill=(255, 255, 255), font=self.display_manager.regular_font)
            return image
        
        # Calculate total width needed
        font = self.display_manager.regular_font
        total_text_parts = []
        
        for symbol, data in self.stock_data.items():
            price = data['price']
            change = data['change']
            change_percent = data['change_percent']
            
            # Format the stock info
            if change >= 0:
                text = f"{symbol}: ${price:.2f} +{change:.2f} (+{change_percent:.1f}%)"
                color = (0, 255, 0)  # Green for positive
            else:
                text = f"{symbol}: ${price:.2f} {change:.2f} ({change_percent:.1f}%)"
                color = (255, 0, 0)  # Red for negative
            
            total_text_parts.append((text, color))
        
        # Join all text with separators
        separator = "  •  "
        full_text = separator.join([part[0] for part in total_text_parts])
        
        # Calculate dimensions
        text_width = self.display_manager.get_text_width(full_text, font)
        gap_width = self.stocks_config.get('scroll_loop_gap_pixels', 
                                          self.display_manager.matrix.width // 2)
        total_width = text_width + gap_width
        height = self.display_manager.matrix.height
        
        # Create image
        image = Image.new('RGB', (total_width, height), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Draw the text (simplified - in real implementation you'd handle colors per stock)
        y_pos = height // 2 - 5  # Center vertically
        draw.text((0, y_pos), full_text, fill=(255, 255, 255), font=font)
        
        # Update scroll controller with new content size
        self.update_scroll_content_size(total_width, height)
        
        logger.debug(f"Created stock display image: {total_width}x{height}px")
        return image
    
    def display_stocks(self, force_clear: bool = False):
        """
        Display stock information with scrolling.
        
        This is dramatically simplified compared to the original - the scroll
        controller handles all the complex timing and positioning logic.
        """
        # Update stock data if needed
        if self.should_update():
            self.fetch_stock_data()
        
        # Create or update the stock display image
        if self.cached_text_image is None or self.should_update():
            self.cached_text_image = self.create_stock_display_image()
        
        # Clear display if requested
        if force_clear:
            self.display_manager.clear()
            self.reset_scroll_position()
        
        # Update scroll position - this replaces all the manual scroll logic!
        scroll_state = self.update_scroll()
        
        # Get the visible portion of the image based on scroll position
        if scroll_state['is_scrolling']:
            # Use the scroll controller to crop the image
            visible_portion = self.crop_scrolled_image(self.cached_text_image, wrap_around=True)
        else:
            # Static display (shouldn't happen for stocks, but good to handle)
            visible_portion = self.cached_text_image.crop((
                0, 0, 
                self.display_manager.matrix.width, 
                self.display_manager.matrix.height
            ))
        
        # Display the visible portion
        self.display_manager.image = visible_portion
        self.display_manager.update_display()
        
        # Log performance metrics
        if scroll_state.get('fps', 0) > 0:
            self.log_scroll_performance()
        
        # Apply frame rate limiting for smooth scrolling
        frame_delay = self.calculate_scroll_frame_delay()
        if frame_delay > 0:
            time.sleep(frame_delay)
        
        # Check if we've completed a full scroll cycle
        return scroll_state.get('needs_content_update', False)
    
    def get_performance_info(self) -> Dict[str, Any]:
        """Get comprehensive performance and debug information."""
        debug_info = self.get_scroll_debug_info()
        debug_info.update({
            'stock_count': len(self.stock_data),
            'last_update': self.last_update,
            'cache_size': f"{self.cached_text_image.size[0]}x{self.cached_text_image.size[1]}" if self.cached_text_image else "None",
            'update_interval': self.stocks_config.get('update_interval', 600)
        })
        return debug_info


# Example of how to use the migrated manager
def example_usage():
    """
    Example of how to use the migrated stock manager.
    """
    # This would typically be called from display_controller.py
    config = {
        'stocks': {
            'enabled': True,
            'update_interval': 600,
            # Legacy settings (automatically converted)
            'scroll_speed': 1,
            'scroll_delay': 0.01,
            # New settings (take precedence)
            'scroll_pixels_per_second': 20.0,
            'scroll_target_fps': 100.0,
            'scroll_mode': 'continuous_loop',
            'enable_scroll_metrics': True,
            'symbols': ['AAPL', 'GOOGL', 'TSLA']
        }
    }
    
    # Initialize (assuming display_manager exists)
    # stock_manager = MigratedStockManager(config, display_manager)
    
    # Main display loop (much simpler now!)
    # while True:
    #     try:
    #         completed_cycle = stock_manager.display_stocks()
    #         if completed_cycle:
    #             logger.info("Stock ticker completed full cycle")
    #     except KeyboardInterrupt:
    #         break
    #     except Exception as e:
    #         logger.error(f"Error in stock display: {e}")
    #         time.sleep(1)
    
    # Get performance info
    # perf_info = stock_manager.get_performance_info()
    # logger.info(f"Stock display performance: {perf_info}")
    
    pass  # Placeholder for actual implementation


if __name__ == "__main__":
    example_usage()
