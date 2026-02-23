"""
Stock & Crypto Ticker Plugin for LEDMatrix (Refactored)

Displays scrolling stock tickers with prices, changes, and optional charts
for stocks and cryptocurrencies. This refactored version splits functionality
into focused modules for better maintainability.
"""

import time
from typing import Dict, Any, Optional

from src.plugin_system.base_plugin import BasePlugin

# Import our modular components
from data_fetcher import StockDataFetcher
from display_renderer import StockDisplayRenderer
from chart_renderer import StockChartRenderer
from config_manager import StockConfigManager


class StockTickerPlugin(BasePlugin):
    """
    Stock and cryptocurrency ticker plugin with scrolling display.
    
    This refactored version uses modular components:
    - StockDataFetcher: Handles API calls and data fetching
    - StockDisplayRenderer: Handles display creation and layout
    - StockChartRenderer: Handles chart drawing functionality
    - StockConfigManager: Handles configuration management
    """
    
    def __init__(self, plugin_id: str, config: Dict[str, Any], 
                 display_manager, cache_manager, plugin_manager):
        """Initialize the stock ticker plugin."""
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        
        # Get display dimensions
        self.display_width = display_manager.width
        self.display_height = display_manager.height
        
        # Initialize modular components
        self.config_manager = StockConfigManager(config, self.logger)
        self.data_fetcher = StockDataFetcher(self.config_manager, self.cache_manager, self.logger)
        self.display_renderer = StockDisplayRenderer(
            self.config_manager.plugin_config, 
            self.display_width, 
            self.display_height, 
            self.logger
        )
        self.chart_renderer = StockChartRenderer(
            self.config_manager.plugin_config,
            self.display_width,
            self.display_height,
            self.logger
        )
        
        # Plugin state
        self.stock_data = {}
        self.current_stock_index = 0
        self.scroll_complete = False
        self._has_scrolled = False
        
        # Expose enable_scrolling for display controller FPS detection
        self.enable_scrolling = self.config_manager.enable_scrolling
        self.last_update_time = 0
        
        # Initialize scroll helper
        self.scroll_helper = self.display_renderer.get_scroll_helper()
        # Convert pixels per frame to pixels per second for ScrollHelper
        # scroll_speed is pixels per frame, scroll_delay is seconds per frame
        # pixels per second = pixels per frame / seconds per frame
        pixels_per_second = self.config_manager.scroll_speed / self.config_manager.scroll_delay if self.config_manager.scroll_delay > 0 else self.config_manager.scroll_speed * 100
        self.scroll_helper.set_scroll_speed(pixels_per_second)
        self.scroll_helper.set_scroll_delay(self.config_manager.scroll_delay)
        
        # Configure dynamic duration settings
        self.scroll_helper.set_dynamic_duration_settings(
            enabled=self.config_manager.dynamic_duration,
            min_duration=int(self.config_manager.min_duration),
            max_duration=int(self.config_manager.max_duration),
            buffer=self.config_manager.duration_buffer
        )
        
        self.logger.info("Stock ticker plugin initialized - %dx%d", 
                        self.display_width, self.display_height)
    
    def update(self) -> None:
        """Update stock and crypto data."""
        current_time = time.time()
        
        # Check if it's time to update
        if current_time - self.last_update_time >= self.config_manager.update_interval:
            try:
                self.logger.debug("Updating stock and crypto data")
                fetched_data = self.data_fetcher.fetch_all_data()
                self.stock_data = fetched_data
                self.last_update_time = current_time
                
                # Clear scroll cache when data updates
                if hasattr(self.scroll_helper, 'cached_image'):
                    self.scroll_helper.cached_image = None
                
                
            except Exception as e:
                import traceback
                self.logger.error("Error updating stock/crypto data: %s", e)
                self.logger.debug("Traceback: %s", traceback.format_exc())
    
    def display(self, force_clear: bool = False) -> None:
        """Display stocks with scrolling or static mode."""
        if not self.stock_data:
            self.logger.warning("No stock data available, showing error state")
            self._show_error_state()
            return
        
        if self.config_manager.enable_scrolling:
            self._display_scrolling(force_clear)
        else:
            self._display_static(force_clear)
    
    def _display_scrolling(self, force_clear: bool = False) -> None:
        """Display stocks with smooth scrolling animation."""
        # Create scrolling image if needed
        if not self.scroll_helper.cached_image or force_clear:
            self._create_scrolling_display()
        
        if force_clear:
            self.scroll_helper.reset_scroll()
            self._has_scrolled = False
            self.scroll_complete = False
        
        # Signal scrolling state
        self.display_manager.set_scrolling_state(True)
        self.display_manager.process_deferred_updates()
        
        # Update scroll position using the scroll helper
        self.scroll_helper.update_scroll_position()
        
        # Get visible portion
        visible_portion = self.scroll_helper.get_visible_portion()
        if visible_portion:
            # Update display - paste overwrites previous content (no need to clear)
            self.display_manager.image.paste(visible_portion, (0, 0))
            self.display_manager.update_display()
        
        # Log frame rate (less frequently to avoid spam)
        self.scroll_helper.log_frame_rate()
        
        # Check if scroll is complete using ScrollHelper's method
        if hasattr(self.scroll_helper, 'is_scroll_complete'):
            self.scroll_complete = self.scroll_helper.is_scroll_complete()
        elif self.scroll_helper.scroll_position == 0 and self._has_scrolled:
            # Fallback: check if we've wrapped around (position is 0 after scrolling)
            self.scroll_complete = True
    
    def _display_static(self, force_clear: bool = False) -> None:
        """Display stocks in static mode - one at a time without scrolling."""
        # Signal not scrolling
        self.display_manager.set_scrolling_state(False)
        
        # Get current stock
        symbols = list(self.stock_data.keys())
        if not symbols:
            self._show_error_state()
            return
        
        current_symbol = symbols[self.current_stock_index % len(symbols)]
        current_data = self.stock_data[current_symbol]
        
        # Create static display
        static_image = self.display_renderer.create_static_display(current_symbol, current_data)
        
        # Update display - paste overwrites previous content (no need to clear)
        self.display_manager.image.paste(static_image, (0, 0))
        self.display_manager.update_display()
        
        # Move to next stock after a delay
        time.sleep(2)  # Show each stock for 2 seconds
        self.current_stock_index += 1
    
    def _create_scrolling_display(self):
        """Create the wide scrolling image with all stocks."""
        try:
            # Create scrolling image using display renderer
            scrolling_image = self.display_renderer.create_scrolling_display(self.stock_data)
            
            if scrolling_image:
                # Set up scroll helper with the image (properly initializes cached_array and state)
                self.scroll_helper.set_scrolling_image(scrolling_image)
                
                self.logger.debug("Created scrolling image: %dx%d", 
                                scrolling_image.width, scrolling_image.height)
            else:
                self.logger.error("Failed to create scrolling image")
                self.scroll_helper.clear_cache()
            
        except Exception as e:
            import traceback
            self.logger.error("Error creating scrolling display: %s", e)
            self.logger.error("Traceback: %s", traceback.format_exc())
            self.scroll_helper.clear_cache()
    
    def _show_error_state(self):
        """Show error state when no data is available."""
        try:
            error_image = self.display_renderer._create_error_display()
            self.display_manager.image.paste(error_image, (0, 0))
            self.display_manager.update_display()
        except Exception as e:
            self.logger.error("Error showing error state: %s", e)
    
    def get_cycle_duration(self, display_mode: str = None) -> Optional[float]:
        """
        Calculate the expected cycle duration based on content width and scroll speed.
        
        This implements dynamic duration scaling where:
        - Duration is calculated from total scroll distance and scroll speed
        - Includes buffer time for smooth cycling
        - Respects min/max duration limits
        
        Args:
            display_mode: The display mode (unused for stock ticker as it has a single mode)
        
        Returns:
            Calculated duration in seconds, or None if dynamic duration is disabled or not available
        """
        # display_mode is unused but kept for API consistency with other plugins
        _ = display_mode
        if not self.config_manager.dynamic_duration:
            return None
        
        # Check if we have a cached image with calculated duration
        if self.scroll_helper and self.scroll_helper.cached_image:
            try:
                dynamic_duration = self.scroll_helper.get_dynamic_duration()
                if dynamic_duration and dynamic_duration > 0:
                    self.logger.debug(
                        "get_cycle_duration() returning calculated duration: %.1fs",
                        dynamic_duration
                    )
                    return float(dynamic_duration)
            except Exception as e:
                self.logger.warning(
                    "Error getting dynamic duration from scroll helper: %s",
                    e
                )
        
        # If no cached image yet, return None (will be calculated when image is created)
        self.logger.debug("get_cycle_duration() returning None (no cached image yet)")
        return None
    
    def get_display_duration(self) -> float:
        """
        Get the display duration in seconds.
        
        If dynamic duration is enabled and scroll helper has calculated a duration,
        use that. Otherwise use the static display_duration.
        """
        # If dynamic duration is enabled and scroll helper has calculated a duration, use it
        if (self.config_manager.dynamic_duration and 
            hasattr(self.scroll_helper, 'calculated_duration') and 
            self.scroll_helper.calculated_duration > 0):
            return float(self.scroll_helper.calculated_duration)
        
        # Otherwise use static duration
        return self.config_manager.get_display_duration()
    
    def get_dynamic_duration(self) -> int:
        """Get the dynamic duration setting."""
        return self.config_manager.get_dynamic_duration()
    
    def supports_dynamic_duration(self) -> bool:
        """
        Determine whether this plugin should use dynamic display durations.
        
        Returns True if dynamic_duration is enabled in the display config.
        """
        return bool(self.config_manager.dynamic_duration)
    
    def get_dynamic_duration_cap(self) -> Optional[float]:
        """
        Return the maximum duration (in seconds) the controller should wait for
        this plugin to complete its display cycle when using dynamic duration.
        
        Returns the max_duration from config, or None if not set.
        """
        if not self.config_manager.dynamic_duration:
            return None
        
        max_duration = self.config_manager.max_duration
        if max_duration and max_duration > 0:
            return float(max_duration)
        return None
    
    def is_cycle_complete(self) -> bool:
        """
        Report whether the plugin has shown a full cycle of content.
        
        For scrolling content, this checks if the scroll has completed one full cycle.
        """
        if not self.config_manager.dynamic_duration:
            # If dynamic duration is disabled, always report complete
            return True
        
        if not self.config_manager.enable_scrolling:
            # For static mode, cycle is complete after showing all stocks once
            if not self.stock_data:
                return True
            symbols = list(self.stock_data.keys())
            return self.current_stock_index >= len(symbols)
        
        # For scrolling mode, check if scroll has completed
        if hasattr(self.scroll_helper, 'is_scroll_complete'):
            return self.scroll_helper.is_scroll_complete()
        
        # Fallback: check if scroll position has wrapped around
        return self.scroll_complete
    
    def reset_cycle_state(self) -> None:
        """
        Reset any internal counters/state related to cycle tracking.
        
        Called by the display controller before beginning a new dynamic-duration
        session. Resets scroll position and stock index.
        """
        super().reset_cycle_state()
        self.scroll_complete = False
        self.current_stock_index = 0
        self._has_scrolled = False
        if hasattr(self.scroll_helper, 'reset_scroll'):
            self.scroll_helper.reset_scroll()
    
    def get_info(self) -> Dict[str, Any]:
        """Get plugin information."""
        return self.config_manager.get_plugin_info()
    
    # Configuration methods
    def set_toggle_chart(self, enabled: bool) -> None:
        """Set whether to show mini charts."""
        self.config_manager.set_toggle_chart(enabled)
        self.display_renderer.set_toggle_chart(enabled)
    
    def set_scroll_speed(self, speed: float) -> None:
        """Set the scroll speed (pixels per frame)."""
        self.config_manager.set_scroll_speed(speed)
        # Convert pixels per frame to pixels per second for ScrollHelper
        pixels_per_second = speed / self.config_manager.scroll_delay if self.config_manager.scroll_delay > 0 else speed * 100
        self.scroll_helper.set_scroll_speed(pixels_per_second)
    
    def set_scroll_delay(self, delay: float) -> None:
        """Set the scroll delay."""
        self.config_manager.set_scroll_delay(delay)
        # Update scroll helper with new delay and recalculate pixels per second
        self.scroll_helper.set_scroll_delay(delay)
        # Recalculate pixels per second with new delay
        pixels_per_second = self.config_manager.scroll_speed / delay if delay > 0 else self.config_manager.scroll_speed * 100
        self.scroll_helper.set_scroll_speed(pixels_per_second)
    
    def set_enable_scrolling(self, enabled: bool) -> None:
        """Set whether scrolling is enabled."""
        self.config_manager.set_enable_scrolling(enabled)
        self.enable_scrolling = enabled  # Keep in sync
    
    def validate_config(self) -> bool:
        """Validate plugin configuration."""
        # Call parent validation first
        if not super().validate_config():
            return False
            
        # Use config manager's validation
        if not self.config_manager.validate_config():
            return False
            
        return True

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        """Reload all config-derived attributes when settings change via web UI."""
        super().on_config_change(new_config)

        # Feed new config into config_manager and re-parse all cached attributes
        self.config_manager.plugin_config = new_config
        self.config_manager._load_config()

        # Sync cached scalar attributes in display_renderer that are read at
        # __init__ time and never automatically updated from the config dict.
        self.display_renderer.config = new_config
        self.display_renderer.toggle_chart = self.config_manager.toggle_chart

        # Sync remaining components
        self.chart_renderer.config = new_config
        self.data_fetcher.config = self.config_manager.plugin_config

        # Clear scroll cache so the next display() call re-renders with the
        # new settings (e.g. chart on/off changes the image dimensions).
        self.scroll_helper.clear_cache()

        self.logger.info(
            "Stock ticker config reloaded: chart=%s, scroll_speed=%.1f",
            self.config_manager.toggle_chart,
            self.config_manager.scroll_speed,
        )

    def reload_config(self) -> None:
        """Reload configuration."""
        self.config_manager.reload_config()
        # Update components with new config
        self.data_fetcher.config = self.config_manager.plugin_config
        self.display_renderer.config = self.config_manager.plugin_config
        # Sync the cached toggle_chart attribute — setting .config alone is not enough
        self.display_renderer.toggle_chart = self.config_manager.toggle_chart
        self.chart_renderer.config = self.config_manager.plugin_config
    
    def cleanup(self) -> None:
        """Clean up resources."""
        try:
            if hasattr(self.data_fetcher, 'cleanup'):
                self.data_fetcher.cleanup()
            self.logger.info("Stock ticker plugin cleanup completed")
        except Exception as e:
            self.logger.error("Error during cleanup: %s", e)
