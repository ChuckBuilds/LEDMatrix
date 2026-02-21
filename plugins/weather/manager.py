"""
Weather Plugin for LEDMatrix

Comprehensive weather display with current conditions, hourly forecast, and daily forecast.
Uses OpenWeatherMap API to provide accurate weather information with beautiful icons.

Features:
- Current weather conditions with temperature, humidity, wind speed
- Hourly forecast (next 24-48 hours)
- Daily forecast (next 7 days)
- Weather icons matching conditions
- UV index display
- Automatic error handling and retry logic

API Version: 1.0.0
"""

import logging
import requests
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from PIL import Image, ImageDraw
from pathlib import Path

from src.plugin_system.base_plugin import BasePlugin

# Import weather icons from local module
try:
    # Try relative import first (if module is loaded as package)
    from .weather_icons import WeatherIcons
except ImportError:
    try:
        # Fallback to direct import (plugin dir is in sys.path)
        import weather_icons
        WeatherIcons = weather_icons.WeatherIcons
    except ImportError:
        # Fallback if weather icons not available
        class WeatherIcons:
            @staticmethod
            def draw_weather_icon(image, icon_code, x, y, size):
                # Simple fallback - just draw a circle
                draw = ImageDraw.Draw(image)
                draw.ellipse([x, y, x + size, y + size], outline=(255, 255, 255), width=2)

# Import API counter function
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    def increment_api_counter(kind: str, count: int = 1):
        pass

logger = logging.getLogger(__name__)


class WeatherPlugin(BasePlugin):
    """
    Weather plugin that displays current conditions and forecasts.
    
    Supports three display modes:
    - weather: Current conditions
    - hourly_forecast: Hourly forecast for next 48 hours
    - daily_forecast: Daily forecast for next 7 days
    
    Configuration options:
        api_key (str): OpenWeatherMap API key
        location (dict): City, state, country for weather data
        units (str): 'imperial' (F) or 'metric' (C)
        update_interval (int): Seconds between API updates
        display_modes (dict): Enable/disable specific display modes
    """
    
    def __init__(self, plugin_id: str, config: Dict[str, Any],
                 display_manager, cache_manager, plugin_manager):
        """Initialize the weather plugin."""
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        
        # Weather configuration
        self.api_key = config.get('api_key', 'YOUR_OPENWEATHERMAP_API_KEY')
        
        # Location - read from flat format (location_city, location_state, location_country)
        # These are the fields defined in config_schema.json for the web interface
        self.location = {
            'city': config.get('location_city', 'Dallas'),
            'state': config.get('location_state', 'Texas'),
            'country': config.get('location_country', 'US')
        }
        
        self.units = config.get('units', 'imperial')
        
        # Handle update_interval - ensure it's an int
        update_interval = config.get('update_interval', 1800)
        try:
            self.update_interval = int(update_interval)
        except (ValueError, TypeError):
            self.update_interval = 1800
        
        # Display modes - read from flat boolean fields
        # These are the fields defined in config_schema.json for the web interface
        self.show_current = config.get('show_current_weather', True)
        self.show_hourly = config.get('show_hourly_forecast', True)
        self.show_daily = config.get('show_daily_forecast', True)
        
        # Data storage
        self.weather_data = None
        self.forecast_data = None
        self.hourly_forecast = None
        self.daily_forecast = None
        self.last_update = 0
        
        # Error handling and throttling
        self.consecutive_errors = 0
        self.last_error_time = 0
        self.error_backoff_time = 60
        self.max_consecutive_errors = 5
        self.error_log_throttle = 300  # Only log errors every 5 minutes
        self.last_error_log_time = 0
        self._last_error_hint = None  # Human-readable hint for diagnostic display
        
        # State caching for display optimization
        self.last_weather_state = None
        self.last_hourly_state = None
        self.last_daily_state = None
        self.current_display_mode = None  # Track current mode to detect switches
        
        # Internal mode cycling (similar to hockey plugin)
        # Build list of enabled modes in order
        self.modes = []
        if self.show_current:
            self.modes.append('weather')
        if self.show_hourly:
            self.modes.append('hourly_forecast')
        if self.show_daily:
            self.modes.append('daily_forecast')
        
        # Default to first mode if none enabled
        if not self.modes:
            self.modes = ['weather']
        
        self.current_mode_index = 0
        self.last_mode_switch = 0
        self.display_duration = config.get('display_duration', 30)
        
        # Layout constants
        self.PADDING = 1
        self.COLORS = {
            'text': (255, 255, 255),
            'highlight': (255, 200, 0),
            'separator': (64, 64, 64),
            'temp_high': (255, 100, 100),
            'temp_low': (100, 100, 255),
            'dim': (180, 180, 180),
            'extra_dim': (120, 120, 120),
            'uv_low': (0, 150, 0),
            'uv_moderate': (255, 200, 0),
            'uv_high': (255, 120, 0),
            'uv_very_high': (200, 0, 0),
            'uv_extreme': (150, 0, 200)
        }
        
        # Resolve project root path (plugin_dir -> plugins -> project_root)
        self.project_root = Path(__file__).resolve().parent.parent.parent
        
        # Weather icons path (Note: WeatherIcons class resolves paths itself, this is just for reference)
        self.icons_dir = self.project_root / 'assets' / 'weather'
        
        # Register fonts
        self._register_fonts()
        
        self.logger.info(f"Weather plugin initialized for {self.location.get('city', 'Unknown')}")
        self.logger.info(f"Units: {self.units}, Update interval: {self.update_interval}s")
    
    def _register_fonts(self):
        """Register fonts with the font manager."""
        try:
            if not hasattr(self.plugin_manager, 'font_manager') or self.plugin_manager.font_manager is None:
                self.logger.warning("Font manager not available")
                return
            
            font_manager = self.plugin_manager.font_manager
            
            # Register fonts for different elements
            font_manager.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.temperature",
                family="press_start",
                size_px=16,
                color=self.COLORS['text']
            )
            
            font_manager.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.condition",
                family="four_by_six",
                size_px=8,
                color=self.COLORS['highlight']
            )
            
            font_manager.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.forecast_label",
                family="four_by_six",
                size_px=6,
                color=self.COLORS['dim']
            )
            
            self.logger.info("Weather plugin fonts registered successfully")
        except Exception as e:
            self.logger.warning(f"Error registering fonts: {e}")

    def _get_layout(self) -> dict:
        """Return cached layout parameters (computed once on first call).

        Icon sizes scale proportionally with display height.
        Text spacing stays fixed because fonts are fixed-size bitmaps.
        Reference baseline: 128x32 display.
        """
        if hasattr(self, '_layout_cache'):
            return self._layout_cache

        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        h_scale = height / 32.0

        # Fixed font metrics (do not change with display size)
        small_font_h = 8
        extra_small_font_h = 7

        margin = max(1, round(1 * h_scale))

        # --- Current weather mode ---
        current_icon_size = max(14, round(40 * h_scale))
        current_icon_x = margin
        current_available_h = (height * 2) // 3
        current_icon_y = (current_available_h - current_icon_size) // 2

        # Text rows on right side (fixed spacing since fonts are fixed)
        condition_y = margin
        temp_y = condition_y + small_font_h
        high_low_y = temp_y + small_font_h
        bottom_bar_y = height - extra_small_font_h

        # --- Forecast modes (hourly + daily) ---
        # Scale with height but cap by narrowest column width to prevent overflow
        min_column_width = width // 4
        forecast_icon_size = max(14, min(round(30 * h_scale), min_column_width))
        forecast_top_y = margin
        forecast_icon_y = max(0, (height - forecast_icon_size) // 2)
        forecast_bottom_y = height - small_font_h

        self._layout_cache = {
            'current_icon_size': current_icon_size,
            'current_icon_x': current_icon_x,
            'current_icon_y': current_icon_y,
            'condition_y': condition_y,
            'temp_y': temp_y,
            'high_low_y': high_low_y,
            'bottom_bar_y': bottom_bar_y,
            'right_margin': margin,
            'forecast_icon_size': forecast_icon_size,
            'forecast_top_y': forecast_top_y,
            'forecast_icon_y': forecast_icon_y,
            'forecast_bottom_y': forecast_bottom_y,
            'margin': margin,
        }
        return self._layout_cache

    def update(self) -> None:
        """
        Update weather data from OpenWeatherMap API.
        
        Fetches current conditions and forecast data, respecting
        update intervals and error backoff periods.
        """
        current_time = time.time()
        
        # Check if we need to update
        if current_time - self.last_update < self.update_interval:
            return
        
        # Check if we're in error backoff period
        if self.consecutive_errors >= self.max_consecutive_errors:
            if current_time - self.last_error_time < self.error_backoff_time:
                self.logger.debug(f"In error backoff period, retrying in {self.error_backoff_time - (current_time - self.last_error_time):.0f}s")
                return
            else:
                # Reset error count after backoff
                self.consecutive_errors = 0
                self.error_backoff_time = 60
        
        # Validate API key
        if not self.api_key or self.api_key == "YOUR_OPENWEATHERMAP_API_KEY":
            self.logger.warning("No valid OpenWeatherMap API key configured")
            return
        
        # Try to fetch weather data
        try:
            self._fetch_weather()
            self.last_update = current_time
            self.consecutive_errors = 0
            self._last_error_hint = None
        except Exception as e:
            self.consecutive_errors += 1
            self.last_error_time = current_time
            if not self._last_error_hint:
                self._last_error_hint = str(e)[:40]

            # Exponential backoff: double the backoff time (max 1 hour)
            self.error_backoff_time = min(self.error_backoff_time * 2, 3600)

            # Only log errors periodically to avoid spam
            if current_time - self.last_error_log_time > self.error_log_throttle:
                self.logger.error(f"Error updating weather (attempt {self.consecutive_errors}/{self.max_consecutive_errors}): {e}")
                if self.consecutive_errors >= self.max_consecutive_errors:
                    self.logger.error(f"Weather API disabled for {self.error_backoff_time} seconds due to repeated failures")
                self.last_error_log_time = current_time
    
    def _fetch_weather(self) -> None:
        """Fetch weather data from OpenWeatherMap API."""
        # Check cache first - use update_interval as max_age to respect configured refresh rate
        cache_key = 'weather'
        cached_data = self.cache_manager.get(cache_key, max_age=self.update_interval)
        if cached_data:
            self.weather_data = cached_data.get('current')
            self.forecast_data = cached_data.get('forecast')
            if self.weather_data and self.forecast_data:
                self._process_forecast_data(self.forecast_data)
                self.logger.info("Using cached weather data")
                return
        
        # Fetch fresh data
        city = self.location.get('city', 'Dallas')
        state = self.location.get('state', 'Texas')
        country = self.location.get('country', 'US')
        
        # Get coordinates using geocoding API
        geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={city},{state},{country}&limit=1&appid={self.api_key}"

        try:
            response = requests.get(geo_url, timeout=10)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 401:
                self._last_error_hint = "Invalid API key"
                self.logger.error(
                    "Geocoding API returned 401 Unauthorized. "
                    "Verify your API key is correct at https://openweathermap.org/api"
                )
            elif status == 429:
                self._last_error_hint = "Rate limit exceeded"
                self.logger.error("Geocoding API rate limit exceeded (429). Increase update_interval.")
            else:
                self._last_error_hint = f"Geo API error {status}"
                self.logger.error(f"Geocoding API HTTP error {status}: {e}")
            raise
        geo_data = response.json()
        
        # Increment API counter for geocoding call
        increment_api_counter('weather', 1)
        
        if not geo_data:
            self._last_error_hint = f"Unknown: {city}, {state}"
            self.logger.error(f"Could not find coordinates for {city}, {state}, {country}")
            self.last_update = time.time()  # Prevent immediate retry
            return
        
        lat = geo_data[0]['lat']
        lon = geo_data[0]['lon']
        
        # Get weather data using One Call API
        one_call_url = f"https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={lon}&exclude=minutely,alerts&appid={self.api_key}&units={self.units}"

        try:
            response = requests.get(one_call_url, timeout=10)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 401:
                self._last_error_hint = "Subscribe to One Call 3.0"
                self.logger.error(
                    "One Call API 3.0 returned 401 Unauthorized. "
                    "Your API key is NOT subscribed to One Call API 3.0. "
                    "Subscribe (free tier available) at https://openweathermap.org/api "
                    "-> One Call API 3.0 -> Subscribe."
                )
            elif status == 429:
                self._last_error_hint = "Rate limit exceeded"
                self.logger.error("One Call API rate limit exceeded (429). Increase update_interval.")
            else:
                self._last_error_hint = f"Weather API error {status}"
                self.logger.error(f"One Call API HTTP error {status}: {e}")
            raise
        one_call_data = response.json()
        
        # Increment API counter for weather data call
        increment_api_counter('weather', 1)
        
        # Store current weather data
        self.weather_data = {
            'main': {
                'temp': one_call_data['current']['temp'],
                'temp_max': one_call_data['daily'][0]['temp']['max'],
                'temp_min': one_call_data['daily'][0]['temp']['min'],
                'humidity': one_call_data['current']['humidity'],
                'pressure': one_call_data['current']['pressure'],
                'uvi': one_call_data['current'].get('uvi', 0)
            },
            'weather': one_call_data['current']['weather'],
            'wind': {
                'speed': one_call_data['current']['wind_speed'],
                'deg': one_call_data['current'].get('wind_deg', 0)
            }
        }
        
        # Store forecast data
        self.forecast_data = one_call_data
        
        # Process forecast data
        self._process_forecast_data(self.forecast_data)
        
        # Cache the data
        self.cache_manager.set(cache_key, {
            'current': self.weather_data,
            'forecast': self.forecast_data
        })
        
        self.logger.info(f"Weather data updated for {city}: {self.weather_data['main']['temp']}°")
    
    def _process_forecast_data(self, forecast_data: Dict) -> None:
        """Process forecast data into hourly and daily lists."""
        if not forecast_data:
            return

        # Process hourly forecast (next 5 hours, excluding current hour)
        hourly_list = forecast_data.get('hourly', [])
        
        # Filter out the current hour - get current timestamp rounded down to the hour
        current_time = time.time()
        current_hour_timestamp = int(current_time // 3600) * 3600  # Round down to nearest hour
        
        # Filter out entries that are in the current hour or past
        future_hourly = [
            hour_data for hour_data in hourly_list
            if hour_data.get('dt', 0) > current_hour_timestamp
        ]
        
        # Get next 5 hours
        hourly_list = future_hourly[:5]
        self.hourly_forecast = []
        
        for hour_data in hourly_list:
            dt = datetime.fromtimestamp(hour_data['dt'])
            temp = round(hour_data['temp'])
            condition = hour_data['weather'][0]['main']
            icon_code = hour_data['weather'][0]['icon']
            self.hourly_forecast.append({
                'hour': dt.strftime('%I:00 %p').lstrip('0'),  # Format as "2:00 PM"
                'temp': temp,
                'condition': condition,
                'icon': icon_code
            })

        # Process daily forecast
        daily_list = forecast_data.get('daily', [])[1:4]  # Skip today (index 0) and get next 3 days
        self.daily_forecast = []
        
        for day_data in daily_list:
            dt = datetime.fromtimestamp(day_data['dt'])
            temp_high = round(day_data['temp']['max'])
            temp_low = round(day_data['temp']['min'])
            condition = day_data['weather'][0]['main']
            icon_code = day_data['weather'][0]['icon']
            
            self.daily_forecast.append({
                'date': dt.strftime('%a'),  # Day name (Mon, Tue, etc.)
                'date_str': dt.strftime('%m/%d'),  # Date (4/8, 4/9, etc.)
                'temp_high': temp_high,
                'temp_low': temp_low,
                'condition': condition,
                'icon': icon_code
            })
    
    def display(self, display_mode: str = None, force_clear: bool = False) -> None:
        """
        Display weather information with internal mode cycling.
        
        The display controller registers each mode separately (weather, hourly_forecast, daily_forecast)
        but calls display() without passing the mode name. This plugin handles mode cycling internally
        similar to the hockey plugin, advancing through enabled modes based on time.
        
        Args:
            display_mode: Optional mode name (not currently used, kept for compatibility)
            force_clear: If True, clear the display before rendering (ignored, kept for compatibility)
        """
        if not self.weather_data:
            self._display_no_data()
            return
        
        # Note: force_clear is handled by display_manager, not needed here
        # This parameter is kept for compatibility with BasePlugin interface
        
        current_mode = None

        # If a specific mode is requested (compatibility methods), honor it
        if display_mode and display_mode in self.modes:
            try:
                requested_index = self.modes.index(display_mode)
            except ValueError:
                requested_index = None

            if requested_index is not None:
                current_mode = self.modes[requested_index]
                if current_mode != self.current_display_mode:
                    self.current_mode_index = requested_index
                    self._on_mode_changed(current_mode)
        else:
            # Default rotation synchronized with display controller
            if self.current_display_mode is None:
                current_mode = self.modes[self.current_mode_index]
                self._on_mode_changed(current_mode)
            elif force_clear:
                self.current_mode_index = (self.current_mode_index + 1) % len(self.modes)
                current_mode = self.modes[self.current_mode_index]
                self._on_mode_changed(current_mode)
            else:
                current_mode = self.modes[self.current_mode_index]
        
        # Ensure we have a mode even if none of the above paths triggered a change
        if current_mode is None:
            current_mode = self.current_display_mode or self.modes[self.current_mode_index]
        
        # Display the current mode
        if current_mode == 'hourly_forecast' and self.show_hourly:
            self._display_hourly_forecast()
        elif current_mode == 'daily_forecast' and self.show_daily:
            self._display_daily_forecast()
        elif current_mode == 'weather' and self.show_current:
            self._display_current_weather()
        else:
            # Fallback: show current weather if mode doesn't match
            self.logger.warning(f"Mode {current_mode} not available, showing current weather")
            self._display_current_weather()
    
    def _on_mode_changed(self, new_mode: str) -> None:
        """Handle logic needed when switching display modes."""
        if new_mode == self.current_display_mode:
            return

        self.logger.info(f"Display mode changed from {self.current_display_mode} to {new_mode}")
        if new_mode == 'hourly_forecast':
            self.last_hourly_state = None
            self.logger.debug("Reset hourly state cache for mode switch")
        elif new_mode == 'daily_forecast':
            self.last_daily_state = None
            self.logger.debug("Reset daily state cache for mode switch")
        else:
            self.last_weather_state = None
            self.logger.debug("Reset weather state cache for mode switch")

        self.current_display_mode = new_mode
        self.last_mode_switch = time.time()
    
    def _display_no_data(self) -> None:
        """Display a diagnostic message when no weather data is available."""
        img = Image.new('RGB', (self.display_manager.matrix.width, self.display_manager.matrix.height), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        from PIL import ImageFont
        try:
            font_path = self.project_root / 'assets' / 'fonts' / '4x6-font.ttf'
            font = ImageFont.truetype(str(font_path), 8)
        except Exception:
            font = ImageFont.load_default()

        if not self.api_key or self.api_key == "YOUR_OPENWEATHERMAP_API_KEY":
            draw.text((2, 8), "Weather:", font=font, fill=(200, 200, 200))
            draw.text((2, 18), "No API Key", font=font, fill=(255, 100, 100))
        elif self._last_error_hint:
            draw.text((2, 4), "Weather Err", font=font, fill=(200, 200, 200))
            hint = self._last_error_hint[:22]
            draw.text((2, 14), hint, font=font, fill=(255, 100, 100))
        else:
            draw.text((5, 8), "No Weather", font=font, fill=(200, 200, 200))
            draw.text((5, 18), "Data", font=font, fill=(200, 200, 200))

        self.display_manager.image = img
        self.display_manager.update_display()
    
    def _render_current_weather_image(self) -> Optional[Image.Image]:
        """Render current weather conditions to an Image without display side effects."""
        try:
            width = self.display_manager.matrix.width
            height = self.display_manager.matrix.height
            img = Image.new('RGB', (width, height), (0, 0, 0))
            draw = ImageDraw.Draw(img)

            # Get weather info
            temp = int(self.weather_data['main']['temp'])
            condition = self.weather_data['weather'][0]['main']
            icon_code = self.weather_data['weather'][0]['icon']
            humidity = self.weather_data['main']['humidity']
            wind_speed = self.weather_data['wind'].get('speed', 0)
            wind_deg = self.weather_data['wind'].get('deg', 0)
            uv_index = self.weather_data['main'].get('uvi', 0)
            temp_high = int(self.weather_data['main']['temp_max'])
            temp_low = int(self.weather_data['main']['temp_min'])

            layout = self._get_layout()

            # --- Top Left: Weather Icon ---
            icon_size = layout['current_icon_size']
            icon_x = layout['current_icon_x']
            icon_y = layout['current_icon_y']
            WeatherIcons.draw_weather_icon(img, icon_code, icon_x, icon_y, size=icon_size)

            # --- Top Right: Condition Text ---
            condition_font = self.display_manager.small_font
            condition_text_width = draw.textlength(condition, font=condition_font)
            condition_x = width - condition_text_width - layout['right_margin']
            condition_y = layout['condition_y']
            draw.text((condition_x, condition_y), condition, font=condition_font, fill=self.COLORS['text'])

            # --- Right Side: Current Temperature ---
            temp_text = f"{temp}°"
            temp_font = self.display_manager.small_font
            temp_text_width = draw.textlength(temp_text, font=temp_font)
            temp_x = width - temp_text_width - layout['right_margin']
            temp_y = layout['temp_y']
            draw.text((temp_x, temp_y), temp_text, font=temp_font, fill=self.COLORS['highlight'])

            # --- Right Side: High/Low Temperature ---
            high_low_text = f"{temp_low}°/{temp_high}°"
            high_low_font = self.display_manager.small_font
            high_low_width = draw.textlength(high_low_text, font=high_low_font)
            high_low_x = width - high_low_width - layout['right_margin']
            high_low_y = layout['high_low_y']
            draw.text((high_low_x, high_low_y), high_low_text, font=high_low_font, fill=self.COLORS['dim'])

            # --- Bottom: Additional Metrics ---
            section_width = width // 3
            y_pos = layout['bottom_bar_y']
            font = self.display_manager.extra_small_font

            # UV Index (Section 1)
            uv_prefix = "UV:"
            uv_value_text = f"{uv_index:.0f}"
            prefix_width = draw.textlength(uv_prefix, font=font)
            value_width = draw.textlength(uv_value_text, font=font)
            total_width = prefix_width + value_width
            start_x = (section_width - total_width) // 2
            draw.text((start_x, y_pos), uv_prefix, font=font, fill=self.COLORS['dim'])
            uv_color = self._get_uv_color(uv_index)
            draw.text((start_x + prefix_width, y_pos), uv_value_text, font=font, fill=uv_color)

            # Humidity (Section 2)
            humidity_text = f"H:{humidity}%"
            humidity_width = draw.textlength(humidity_text, font=font)
            humidity_x = section_width + (section_width - humidity_width) // 2
            draw.text((humidity_x, y_pos), humidity_text, font=font, fill=self.COLORS['dim'])

            # Wind (Section 3)
            wind_dir = self._get_wind_direction(wind_deg)
            wind_text = f"W:{wind_speed:.0f}{wind_dir}"
            wind_width = draw.textlength(wind_text, font=font)
            wind_x = (2 * section_width) + (section_width - wind_width) // 2
            draw.text((wind_x, y_pos), wind_text, font=font, fill=self.COLORS['dim'])

            return img
        except Exception as e:
            self.logger.exception("Error rendering current weather")
            return None

    def _display_current_weather(self) -> None:
        """Display current weather conditions using comprehensive layout with icons."""
        try:
            current_state = self._get_weather_state()
            if current_state == self.last_weather_state:
                self.display_manager.update_display()
                return

            self.display_manager.clear()
            img = self._render_current_weather_image()
            if img:
                self.display_manager.image = img
                self.display_manager.update_display()
                self.last_weather_state = current_state
        except Exception as e:
            self.logger.error(f"Error displaying current weather: {e}")
    
    def _get_wind_direction(self, degrees: float) -> str:
        """Convert wind degrees to cardinal direction."""
        directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
        index = round(degrees / 45) % 8
        return directions[index]

    def _get_uv_color(self, uv_index: float) -> tuple:
        """Get color based on UV index value."""
        if uv_index <= 2:
            return self.COLORS['uv_low']
        elif uv_index <= 5:
            return self.COLORS['uv_moderate']
        elif uv_index <= 7:
            return self.COLORS['uv_high']
        elif uv_index <= 10:
            return self.COLORS['uv_very_high']
        else:
            return self.COLORS['uv_extreme']
    
    def _get_weather_state(self) -> Dict[str, Any]:
        """Get current weather state for comparison."""
        if not self.weather_data:
            return None
        return {
            'temp': round(self.weather_data['main']['temp']),
            'condition': self.weather_data['weather'][0]['main'],
            'humidity': self.weather_data['main']['humidity'],
            'uvi': self.weather_data['main'].get('uvi', 0)
        }

    def _get_hourly_state(self) -> List[Dict[str, Any]]:
        """Get current hourly forecast state for comparison."""
        if not self.hourly_forecast:
            return None
        return [
            {'hour': f['hour'], 'temp': round(f['temp']), 'condition': f['condition']}
            for f in self.hourly_forecast[:3]
        ]

    def _get_daily_state(self) -> List[Dict[str, Any]]:
        """Get current daily forecast state for comparison."""
        if not self.daily_forecast:
            return None
        return [
            {
                'date': f['date'],
                'temp_high': round(f['temp_high']),
                'temp_low': round(f['temp_low']),
                'condition': f['condition']
            }
            for f in self.daily_forecast[:4]
        ]
    
    def _render_hourly_forecast_image(self) -> Optional[Image.Image]:
        """Render hourly forecast to an Image without display side effects."""
        try:
            if not self.hourly_forecast:
                return None

            width = self.display_manager.matrix.width
            height = self.display_manager.matrix.height
            img = Image.new('RGB', (width, height), (0, 0, 0))
            draw = ImageDraw.Draw(img)

            layout = self._get_layout()
            hours_to_show = min(4, len(self.hourly_forecast))
            section_width = width // hours_to_show
            padding = max(2, section_width // 6)

            for i in range(hours_to_show):
                forecast = self.hourly_forecast[i]
                x = i * section_width + padding
                center_x = x + (section_width - 2 * padding) // 2

                # Hour at top
                hour_text = forecast['hour']
                hour_text = hour_text.replace(":00 ", "").replace("PM", "p").replace("AM", "a")
                hour_width = draw.textlength(hour_text, font=self.display_manager.small_font)
                draw.text((center_x - hour_width // 2, layout['forecast_top_y']),
                         hour_text,
                         font=self.display_manager.small_font,
                         fill=self.COLORS['text'])

                # Weather icon
                icon_size = layout['forecast_icon_size']
                icon_y = layout['forecast_icon_y']
                icon_x = center_x - icon_size // 2
                WeatherIcons.draw_weather_icon(img, forecast['icon'], icon_x, icon_y, icon_size)

                # Temperature at bottom
                temp_text = f"{forecast['temp']}°"
                temp_width = draw.textlength(temp_text, font=self.display_manager.small_font)
                temp_y = layout['forecast_bottom_y']
                draw.text((center_x - temp_width // 2, temp_y),
                         temp_text,
                         font=self.display_manager.small_font,
                         fill=self.COLORS['text'])

            return img
        except Exception as e:
            self.logger.exception("Error rendering hourly forecast")
            return None

    def _display_hourly_forecast(self) -> None:
        """Display hourly forecast with weather icons."""
        try:
            if not self.hourly_forecast:
                self.logger.warning("No hourly forecast data available, showing no data message")
                self._display_no_data()
                return

            current_state = self._get_hourly_state()
            if current_state == self.last_hourly_state:
                self.display_manager.update_display()
                return

            self.display_manager.clear()
            img = self._render_hourly_forecast_image()
            if img:
                self.display_manager.image = img
                self.display_manager.update_display()
                self.last_hourly_state = current_state
        except Exception as e:
            self.logger.error(f"Error displaying hourly forecast: {e}")
    
    def _render_daily_forecast_image(self) -> Optional[Image.Image]:
        """Render daily forecast to an Image without display side effects."""
        try:
            if not self.daily_forecast:
                return None

            width = self.display_manager.matrix.width
            height = self.display_manager.matrix.height
            img = Image.new('RGB', (width, height), (0, 0, 0))
            draw = ImageDraw.Draw(img)

            layout = self._get_layout()
            days_to_show = min(3, len(self.daily_forecast))
            if days_to_show == 0:
                draw.text((2, 2), "No daily forecast", font=self.display_manager.small_font, fill=self.COLORS['dim'])
            else:
                section_width = width // days_to_show

                for i in range(days_to_show):
                    forecast = self.daily_forecast[i]
                    center_x = i * section_width + section_width // 2

                    # Day name at top
                    day_text = forecast['date']
                    day_width = draw.textlength(day_text, font=self.display_manager.small_font)
                    draw.text((center_x - day_width // 2, layout['forecast_top_y']),
                             day_text,
                             font=self.display_manager.small_font,
                             fill=self.COLORS['text'])

                    # Weather icon
                    icon_size = layout['forecast_icon_size']
                    icon_y = layout['forecast_icon_y']
                    icon_x = center_x - icon_size // 2
                    WeatherIcons.draw_weather_icon(img, forecast['icon'], icon_x, icon_y, icon_size)

                    # High/low temperatures at bottom
                    temp_text = f"{forecast['temp_low']} / {forecast['temp_high']}"
                    temp_width = draw.textlength(temp_text, font=self.display_manager.extra_small_font)
                    temp_y = layout['forecast_bottom_y']
                    draw.text((center_x - temp_width // 2, temp_y),
                             temp_text,
                             font=self.display_manager.extra_small_font,
                             fill=self.COLORS['text'])

            return img
        except Exception as e:
            self.logger.exception("Error rendering daily forecast")
            return None

    def _display_daily_forecast(self) -> None:
        """Display daily forecast with weather icons."""
        try:
            if not self.daily_forecast:
                self._display_no_data()
                return

            current_state = self._get_daily_state()
            if current_state == self.last_daily_state:
                self.display_manager.update_display()
                return

            self.display_manager.clear()
            img = self._render_daily_forecast_image()
            if img:
                self.display_manager.image = img
                self.display_manager.update_display()
                self.last_daily_state = current_state
        except Exception as e:
            self.logger.error(f"Error displaying daily forecast: {e}")
    
    def get_vegas_content(self):
        """Return images for all enabled weather display modes."""
        if not self.weather_data:
            return None

        images = []

        if self.show_current:
            img = self._render_current_weather_image()
            if img:
                images.append(img)

        if self.show_hourly and self.hourly_forecast:
            img = self._render_hourly_forecast_image()
            if img:
                images.append(img)

        if self.show_daily and self.daily_forecast:
            img = self._render_daily_forecast_image()
            if img:
                images.append(img)

        if images:
            total_width = sum(img.width for img in images)
            self.logger.info(
                "[Weather Vegas] Returning %d image(s), %dpx total",
                len(images), total_width
            )
            return images

        return None

    def display_weather(self, force_clear: bool = False) -> None:
        """Display current weather (compatibility method for display controller)."""
        self.display('weather', force_clear)
    
    def display_hourly_forecast(self, force_clear: bool = False) -> None:
        """Display hourly forecast (compatibility method for display controller)."""
        self.display('hourly_forecast', force_clear)
    
    def display_daily_forecast(self, force_clear: bool = False) -> None:
        """Display daily forecast (compatibility method for display controller)."""
        self.display('daily_forecast', force_clear)

    def get_info(self) -> Dict[str, Any]:
        """Return plugin info for web UI."""
        info = super().get_info()
        info.update({
            'location': self.location,
            'units': self.units,
            'api_key_configured': bool(self.api_key),
            'last_update': self.last_update,
            'current_temp': self.weather_data.get('main', {}).get('temp') if self.weather_data else None,
            'current_humidity': self.weather_data.get('main', {}).get('humidity') if self.weather_data else None,
            'current_description': self.weather_data.get('weather', [{}])[0].get('description', '') if self.weather_data else '',
            'forecast_available': bool(self.forecast_data),
            'daily_forecast_count': len(self.daily_forecast) if hasattr(self, 'daily_forecast') and self.daily_forecast is not None else 0,
            'hourly_forecast_count': len(self.hourly_forecast) if hasattr(self, 'hourly_forecast') and self.hourly_forecast is not None else 0
        })
        return info

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        """Reload all config-derived attributes when settings change via web UI."""
        super().on_config_change(new_config)

        self.api_key = new_config.get('api_key', 'YOUR_OPENWEATHERMAP_API_KEY')
        self.location = {
            'city': new_config.get('location_city', 'Dallas'),
            'state': new_config.get('location_state', 'Texas'),
            'country': new_config.get('location_country', 'US')
        }
        self.units = new_config.get('units', 'imperial')
        update_interval = new_config.get('update_interval', 1800)
        try:
            self.update_interval = int(update_interval)
        except (ValueError, TypeError):
            self.update_interval = 1800

        self.show_current = new_config.get('show_current_weather', True)
        self.show_hourly = new_config.get('show_hourly_forecast', True)
        self.show_daily = new_config.get('show_daily_forecast', True)
        self.display_duration = new_config.get('display_duration', 30)

        # Rebuild the enabled modes list
        self.modes = []
        if self.show_current:
            self.modes.append('weather')
        if self.show_hourly:
            self.modes.append('hourly_forecast')
        if self.show_daily:
            self.modes.append('daily_forecast')
        if not self.modes:
            self.modes = ['weather']

        # Reset update timer and error state so new settings take effect immediately
        self.last_update = 0
        self.consecutive_errors = 0
        self.error_backoff_time = 60

        # Clear layout cache since units/display settings may have changed
        if hasattr(self, '_layout_cache'):
            del self._layout_cache

        self.logger.info(
            "Weather plugin config reloaded: city=%s, units=%s, api_key_set=%s",
            self.location.get('city'),
            self.units,
            bool(self.api_key and self.api_key != 'YOUR_OPENWEATHERMAP_API_KEY')
        )

    def cleanup(self) -> None:
        """Cleanup resources."""
        self.weather_data = None
        self.forecast_data = None
        self.logger.info("Weather plugin cleaned up")

