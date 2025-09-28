import time
import logging
from datetime import datetime
import pytz
from typing import Dict, Any
from src.config_manager import ConfigManager
from src.display_manager import DisplayManager
from src.config.config_models import RootConfig

# Get logger without configuring
logger = logging.getLogger(__name__)

class Clock:
    def __init__(self, display_manager: DisplayManager, config: RootConfig):
        # Use the provided display_manager or create a new one if none provided
        self.config = config
        self.display_manager = display_manager
        logger.info("Clock initialized with display_manager: %s", id(self.display_manager))
        self.location = self.config.location
        self.clock_config = self.config.clock
        # Use configured timezone if available, otherwise try to determine it
        self.timezone = self._get_timezone()
        self.last_time = None
        self.last_date = None
        # Colors for different elements - using super bright colors
        self.COLORS = {
            'time': (255, 255, 255),    # Pure white for time
            'ampm': (255, 255, 128),    # Bright warm yellow for AM/PM
            'date': (255, 128, 64)      # Bright orange for date
        }

    def _get_timezone(self) -> pytz.timezone:
        """Get timezone from the config file."""
        config_timezone = self.config.timezone
        try:
            return pytz.timezone(config_timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            logger.warning(
                f"Invalid timezone '{config_timezone}' in config. "
                "Falling back to UTC. Please check your config.json file. "
                "A list of valid timezones can be found at "
                "https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
            )
            return pytz.utc

    def _get_ordinal_suffix(self, day: int) -> str:
        """Get the ordinal suffix for a day number (1st, 2nd, 3rd, etc.)."""
        if 10 <= day % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        return suffix

    def get_current_time(self) -> tuple:
        """Get the current time and date in the configured timezone."""
        current = datetime.now(self.timezone)
        
        # Format time in 12-hour format with AM/PM
        time_str = current.strftime('%I:%M')  # Remove leading zero from hour
        if time_str.startswith('0'):
            time_str = time_str[1:]
        
        # Get AM/PM
        ampm = current.strftime('%p')
        
        # Format date with ordinal suffix - split into two lines
        day_suffix = self._get_ordinal_suffix(current.day)
        # Full weekday on first line, full month and day on second line
        weekday = current.strftime('%A')
        date_str = current.strftime(f'%B %-d{day_suffix}')
        
        return time_str, ampm, weekday, date_str

    def display_time(self, force_clear: bool = False) -> None:
        """Display the current time and date."""
        time_str, ampm, weekday, date_str = self.get_current_time()
        
        # Only update if something has changed
        if time_str != self.last_time or date_str != self.last_date or force_clear:
            # Clear the display
            self.display_manager.clear()
            
            # Calculate positions
            display_width = self.display_manager.matrix.width
            display_height = self.display_manager.matrix.height
            
            # Draw time (large, centered, near top)
            self.display_manager.draw_text(
                time_str,
                y=4,  # Move up slightly to make room for two lines of date
                color=self.COLORS['time'],
                small_font=True
            )
            
            # Draw AM/PM (small, next to time)
            time_width = self.display_manager.font.getlength(time_str)
            ampm_x = (display_width + time_width) // 2 + 4
            self.display_manager.draw_text(
                ampm,
                x=ampm_x,
                y=4,  # Align with time
                color=self.COLORS['ampm'],
                small_font=True
            )
            
            # Draw weekday on first line (small font)
            self.display_manager.draw_text(
                weekday,
                y=display_height - 18,  # First line of date
                color=self.COLORS['date'],
                small_font=True
            )
            
            # Draw month and day on second line (small font)
            self.display_manager.draw_text(
                date_str,
                y=display_height - 9,  # Second line of date
                color=self.COLORS['date'],
                small_font=True
            )
            
            # Update the display after drawing everything
            self.display_manager.update_display()
            
            # Update cache
            self.last_time = time_str
            self.last_date = date_str

if __name__ == "__main__":
    clock = Clock()
    try:
        while True:
            clock.display_time()
            time.sleep(clock.clock_config.update_interval)
    except KeyboardInterrupt:
        print("\nClock stopped by user")
    finally:
        clock.display_manager.cleanup() 