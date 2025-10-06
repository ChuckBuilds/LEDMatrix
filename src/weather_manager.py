import requests
import time
from datetime import datetime
from typing import Dict, Any, List
from PIL import Image, ImageDraw
import freetype
from .weather_icons import WeatherIcons
from .cache_manager import CacheManager

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass

class WeatherManager:

    def __init__(self, config: Dict[str, Any], display_manager):
        self.config = config
        self.display_manager = display_manager
        self.weather_config = config.get('weather', {})
        self.location = config.get('location', {})
        self.last_update = 0
        self.weather_data = None
        self.forecast_data = None
        self.hourly_forecast = None
        self.daily_forecast = None
        self.last_draw_time = 0
        self.cache_manager = CacheManager()
        
        # Error handling and throttling
        self.consecutive_errors = 0
        self.last_error_time = 0
        self.error_backoff_time = 60  # Start with 1 minute backoff
        self.max_consecutive_errors = 5  # Stop trying after 5 consecutive errors
        self.error_log_throttle = 300  # Only log errors every 5 minutes
        self.last_error_log_time = 0
        
        # Layout constants
        self.PADDING = 1
        self.ICON_SIZE = {
            'extra_large': 40, # Changed from 30
            'large': 30,
            'medium': 24,
            'small': 14
        }
        self.COLORS = {
            'text': (255, 255, 255),
            'highlight': (255, 200, 0),
            'separator': (64, 64, 64),
            'temp_high': (255, 100, 100),
            'temp_low': (100, 100, 255),
            'dim': (180, 180, 180),
            'extra_dim': (120, 120, 120),  # Even dimmer for smallest text
            'uv_low': (0, 150, 0),          # Green
            'uv_moderate': (255, 200, 0),   # Yellow
            'uv_high': (255, 120, 0),       # Orange
            'uv_very_high': (200, 0, 0),    # Red
            'uv_extreme': (150, 0, 200)     # Purple
        }
        # Add caching for last drawn states
        self.last_weather_state = None
        self.last_hourly_state = None
        self.last_daily_state = None

    def _fetch_weather(self) -> None:
        """Fetch current weather and forecast data from OpenWeatherMap API."""
        current_time = time.time()
        
        # Check if we're in error backoff period
        if self.consecutive_errors >= self.max_consecutive_errors:
            if current_time - self.last_error_time < self.error_backoff_time:
                # Still in backoff period, don't attempt fetch
                if current_time - self.last_error_log_time > self.error_log_throttle:
                    print(f"Weather API disabled due to {self.consecutive_errors} consecutive errors. Retrying in {self.error_backoff_time - (current_time - self.last_error_time):.0f} seconds")
                    self.last_error_log_time = current_time
                return
            else:
                # Backoff period expired, reset error count and try again
                self.consecutive_errors = 0
                self.error_backoff_time = 60  # Reset to initial backoff
        
        api_key = self.weather_config.get('api_key')
        if not api_key or api_key == "YOUR_OPENWEATHERMAP_API_KEY":
            if current_time - self.last_error_log_time > self.error_log_throttle:
                print("No valid API key configured for weather")
                self.last_error_log_time = current_time
            return

        # Try to get cached data first
        cached_data = self.cache_manager.get('weather')
        if cached_data:
            self.weather_data = cached_data.get('current')
            self.forecast_data = cached_data.get('forecast')
            if self.weather_data and self.forecast_data:
                self._process_forecast_data(self.forecast_data)
                self.last_update = time.time()
                # Reset error count on successful cache usage
                self.consecutive_errors = 0
                print("Using cached weather data")
                return

        city = self.location['city']
        state = self.location['state']
        country = self.location['country']
        units = self.weather_config.get('units', 'imperial')

        # First get coordinates using geocoding API
        geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={city},{state},{country}&limit=1&appid={api_key}"
        
        try:
            # Get coordinates
            response = requests.get(geo_url)
            response.raise_for_status()
            geo_data = response.json()
            
            # Increment API counter for geocoding call
            increment_api_counter('weather', 1)
            
            if not geo_data:
                print(f"Could not find coordinates for {city}, {state}")
                return
                
            lat = geo_data[0]['lat']
            lon = geo_data[0]['lon']
            
            # Get current weather and daily forecast using One Call API
            one_call_url = f"https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={lon}&exclude=minutely,alerts&appid={api_key}&units={units}"
            
            # Fetch current weather and daily forecast
            response = requests.get(one_call_url)
            response.raise_for_status()
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
                    'speed': one_call_data['current'].get('wind_speed', 0),
                    'deg': one_call_data['current'].get('wind_deg', 0)
                }
            }

            # Store forecast data (for hourly and daily forecasts)
            self.forecast_data = one_call_data
            
            # Process forecast data
            self._process_forecast_data(self.forecast_data)
            
            # Cache the new data
            cache_data = {
                'current': self.weather_data,
                'forecast': self.forecast_data
            }
            self.cache_manager.update_cache('weather', cache_data)
            
            self.last_update = time.time()
            # Reset error count on successful fetch
            self.consecutive_errors = 0
            print("Weather data updated successfully")
            
        except Exception as e:
            self.consecutive_errors += 1
            self.last_error_time = current_time
            
            # Exponential backoff: double the backoff time (max 1 hour)
            self.error_backoff_time = min(self.error_backoff_time * 2, 3600)
            
            # Only log errors periodically to avoid spam
            if current_time - self.last_error_log_time > self.error_log_throttle:
                print(f"Error fetching weather data (attempt {self.consecutive_errors}/{self.max_consecutive_errors}): {e}")
                if self.consecutive_errors >= self.max_consecutive_errors:
                    print(f"Weather API disabled for {self.error_backoff_time} seconds due to repeated failures")
                self.last_error_log_time = current_time
            
            # If we have cached data, use it as fallback
            if cached_data:
                self.weather_data = cached_data.get('current')
                self.forecast_data = cached_data.get('forecast')
                if self.weather_data and self.forecast_data:
                    self._process_forecast_data(self.forecast_data)
                    print("Using cached weather data as fallback")
            else:
                self.weather_data = None
                self.forecast_data = None

    def _process_forecast_data(self, forecast_data: Dict[str, Any]) -> None:
        """Process forecast data into hourly and daily forecasts."""
        if not forecast_data:
            return

        # Process hourly forecast (next 5 hours)
        hourly_list = forecast_data.get('hourly', [])[:5]  # Get next 5 hours
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

    def get_weather(self) -> Dict[str, Any]:
        """Get current weather data, fetching new data if needed."""
        current_time = time.time()
        update_interval = self.weather_config.get('update_interval', 300)
        # Add a throttle for log spam
        log_throttle_interval = 600  # 10 minutes
        if not hasattr(self, '_last_weather_log_time'):
            self._last_weather_log_time = 0
        # Check if we need to update based on time or if we have no data
        if (not self.weather_data or 
            current_time - self.last_update > update_interval):
            # Check if data has changed before fetching
            current_state = self._get_weather_state()
            if current_state and not self.cache_manager.has_data_changed('weather', current_state):
                if current_time - self._last_weather_log_time > log_throttle_interval:
                    print("Weather data hasn't changed, using existing data")
                    self._last_weather_log_time = current_time
                return self.weather_data
            self._fetch_weather()
        return self.weather_data

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
            for f in self.daily_forecast[:4]  # Changed to 4 days
        ]

    def _get_text_width(self, text, font):
        """Get text width handling both PIL and freetype fonts."""
        try:
            # Check if it's a PIL font (has getlength method)
            if hasattr(font, 'getlength'):
                return int(font.getlength(text))
            else:
                # Use FontManager's measure_text for freetype fonts
                width, _, _ = self.display_manager.font_manager.measure_text(text, font)
                return int(width)
        except Exception as e:
            logger.warning(f"Error measuring text width: {e}")
            # Fallback to character count estimate
            return len(text) * 8

    def display_weather(self, force_clear: bool = False) -> None:
        """Display current weather information using a modern layout."""
        try:
            weather_data = self.get_weather()
            if not weather_data:
                print("No weather data available")
                return

            # Check if state has changed
            current_state = self._get_weather_state()
            if not force_clear and current_state == self.last_weather_state:
                return  # No need to redraw if nothing changed

            # Clear the display once at the start
            self.display_manager.clear()
            
            # Create a new image for drawing
            image = Image.new('RGB', (self.display_manager.matrix.width, self.display_manager.matrix.height))
            draw = ImageDraw.Draw(image)
            
            # --- Top Left: Icon ---
            condition = weather_data['weather'][0]['main']
            icon_code = weather_data['weather'][0]['icon']
            icon_size = self.ICON_SIZE['extra_large'] # Use extra_large size
            icon_x = 1 # Small padding from left edge
            # Center the icon vertically in the top two-thirds of the display
            available_height = (self.display_manager.matrix.height * 2) // 3  # Use top 2/3 of screen
            icon_y = (available_height - icon_size) // 2
            WeatherIcons.draw_weather_icon(image, icon_code, icon_x, icon_y, size=icon_size)
            
            # --- Top Right: Condition Text ---
            condition_text = condition
            condition_font = self.display_manager.font_manager.resolve(element_key="weather.condition")
            # Handle both PIL and freetype fonts for text measurement
            condition_text_width = self._get_text_width(condition_text, condition_font)
            condition_x = self.display_manager.matrix.width - condition_text_width - 1 # Align right
            condition_y = 1 # Align top
            draw.text((condition_x, condition_y), 
                     condition_text, 
                     font=condition_font, 
                     fill=self.COLORS['text'])

            # --- Right Side (Below Condition): Current Temp ---
            temp = round(weather_data['main']['temp'])
            temp_text = f"{temp}°"
            # Use unified font system
            temp_font = self.display_manager.font_manager.resolve(element_key="weather.temperature")
            # Handle both PIL and freetype fonts for text measurement
            temp_text_width = self._get_text_width(temp_text, temp_font)
            temp_x = self.display_manager.matrix.width - temp_text_width - 1 # Align right
            temp_y = condition_y + 8 # Position below condition text (adjust 8 based on font size)
            draw.text((temp_x, temp_y),
                     temp_text,
                     font=temp_font,
                     fill=self.COLORS['highlight'])
            
            # --- Right Side (Below Current Temp): High/Low Temp ---
            temp_max = round(weather_data['main']['temp_max'])
            temp_min = round(weather_data['main']['temp_min'])
            high_low_text = f"{temp_min}°/{temp_max}°"
            high_low_font = self.display_manager.font_manager.resolve(element_key="weather.high_low")
            # Handle both PIL and freetype fonts for text measurement
            high_low_width = self._get_text_width(high_low_text, high_low_font)
            high_low_x = self.display_manager.matrix.width - high_low_width - 1 # Align right
            high_low_y = temp_y + 8 # Position below current temp text (adjust 8 based on font size)
            draw.text((high_low_x, high_low_y),
                     high_low_text,
                     font=high_low_font,
                     fill=self.COLORS['dim'])
            
            # --- Bottom: Additional Metrics (Unchanged) ---
            display_width = self.display_manager.matrix.width
            section_width = display_width // 3
            y_pos = self.display_manager.matrix.height - 7 # Position near bottom for 6px font

            # --- UV Index (Section 1) ---
            uv_index = weather_data['main'].get('uvi', 0)
            uv_prefix = "UV:"
            uv_value_text = f"{uv_index:.0f}"
            
            uv_font = self.display_manager.font_manager.resolve(element_key="weather.uv")
            # Handle both PIL and freetype fonts for text measurement
            prefix_width = self._get_text_width(uv_prefix, uv_font)
            value_width = self._get_text_width(uv_value_text, uv_font)
            total_width = prefix_width + value_width
            
            start_x = (section_width - total_width) // 2
            
            # Draw "UV:" prefix
            draw.text((start_x, y_pos),
                        uv_prefix,
                        font=uv_font,
                        fill=self.COLORS['dim'])

            # Draw UV value with color
            uv_color = self._get_uv_color(uv_index)
            draw.text((start_x + prefix_width, y_pos),
                        uv_value_text,
                        font=uv_font,
                        fill=uv_color)
            
            # --- Humidity (Section 2) ---
            humidity = weather_data['main']['humidity']
            humidity_text = f"H:{humidity}%"
            humidity_font = self.display_manager.font_manager.resolve(element_key="weather.humidity")
            # Handle both PIL and freetype fonts for text measurement
            humidity_width = self._get_text_width(humidity_text, humidity_font)
            humidity_x = section_width + (section_width - humidity_width) // 2 # Center in second third
            draw.text((humidity_x, y_pos),
                     humidity_text,
                     font=humidity_font,
                     fill=self.COLORS['dim'])

            # --- Wind (Section 3) ---
            wind_speed = weather_data['wind']['speed']
            wind_deg = weather_data['wind']['deg']
            wind_dir = self._get_wind_direction(wind_deg)
            wind_text = f"W:{wind_speed:.0f}{wind_dir}"
            wind_font = self.display_manager.font_manager.resolve(element_key="weather.wind")
            # Handle both PIL and freetype fonts for text measurement
            wind_width = self._get_text_width(wind_text, wind_font)
            wind_x = (2 * section_width) + (section_width - wind_width) // 2 # Center in third third
            draw.text((wind_x, y_pos),
                     wind_text,
                     font=wind_font,
                     fill=self.COLORS['dim'])
            
            # Update the display
            self.display_manager.image = image
            self.display_manager.update_display()
            self.last_weather_state = current_state

        except Exception as e:
            print(f"Error displaying weather: {e}")

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

    def display_hourly_forecast(self, force_clear: bool = False):
        """Display the next few hours of weather forecast."""
        try:
            if not self.hourly_forecast:
                print("No hourly forecast data available")
                return
            
            # Check if state has changed
            current_state = self._get_hourly_state()
            if not force_clear and current_state == self.last_hourly_state:
                return
            
            # Clear the display
            self.display_manager.clear()
            
            # Create a new image for drawing
            image = Image.new('RGB', (self.display_manager.matrix.width, self.display_manager.matrix.height))
            draw = ImageDraw.Draw(image)
            
            # Calculate layout based on matrix dimensions
            hours_to_show = min(4, len(self.hourly_forecast))
            total_width = self.display_manager.matrix.width
            section_width = total_width // hours_to_show
            padding = max(2, section_width // 6)  # Increased padding for more space
            
            for i in range(hours_to_show):
                forecast = self.hourly_forecast[i]
                x = i * section_width + padding
                center_x = x + (section_width - 2 * padding) // 2
                
                # Draw hour at top
                hour_text = forecast['hour']
                hour_text = hour_text.replace(":00 ", "").replace("PM", "p").replace("AM", "a")
                hour_font = self.display_manager.font_manager.resolve(element_key="weather.hourly.time")
                # Handle both PIL and freetype fonts for text measurement
                hour_width = self._get_text_width(hour_text, hour_font)
                draw.text((center_x - hour_width // 2, 1),
                         hour_text,
                         font=hour_font,
                         fill=self.COLORS['text'])
                
                # Draw weather icon centered vertically between top/bottom text
                icon_size = self.ICON_SIZE['large'] # Changed from medium to large (28)
                top_text_height = 8 # Approx height reservation for top text
                bottom_text_y = self.display_manager.matrix.height - 8 # Starting Y for bottom text
                available_height_for_icon = bottom_text_y - top_text_height
                # Ensure calculated y is not negative if space is very tight
                calculated_y = top_text_height + (available_height_for_icon - icon_size) // 2
                icon_y = (self.display_manager.matrix.height // 2) - 16
                icon_x = center_x - icon_size // 2
                WeatherIcons.draw_weather_icon(image, forecast['icon'], icon_x, icon_y, icon_size)
                
                # Draw temperature at bottom
                temp_text = f"{forecast['temp']}°"
                temp_font = self.display_manager.font_manager.resolve(element_key="weather.hourly.temp")
                # Handle both PIL and freetype fonts for text measurement
                temp_width = self._get_text_width(temp_text, temp_font)
                temp_y = self.display_manager.matrix.height - 8  # Position at bottom with small margin
                draw.text((center_x - temp_width // 2, temp_y),
                         temp_text,
                         font=temp_font,
                         fill=self.COLORS['text'])
            
            # Update the display
            self.display_manager.image = image
            self.display_manager.update_display()
            self.last_hourly_state = current_state

        except Exception as e:
            print(f"Error displaying hourly forecast: {e}")

    def display_daily_forecast(self, force_clear: bool = False):
        """Display the daily weather forecast."""
        try:
            if not self.daily_forecast:
                print("No daily forecast data available")
                return
            
            # Check if state has changed
            current_state = self._get_daily_state()
            if not force_clear and current_state == self.last_daily_state:
                return
            
            # Clear the display
            self.display_manager.clear()
            
            # Create a new image for drawing
            image = Image.new('RGB', (self.display_manager.matrix.width, self.display_manager.matrix.height))
            draw = ImageDraw.Draw(image)
            
            # Calculate layout based on matrix dimensions for 3 days
            days_to_show = min(3, len(self.daily_forecast)) # Changed from 4 to 3
            if days_to_show == 0:
                # Handle case where there's no forecast data after filtering
                no_forecast_font = self.display_manager.font_manager.resolve(element_key="weather.daily.day")
                draw.text((2, 2), "No daily forecast", font=no_forecast_font, fill=self.COLORS['dim'])
            else:
                total_width = self.display_manager.matrix.width
                section_width = total_width // days_to_show # Divide by 3 (or fewer if less data)
                padding = max(2, section_width // 6)
                
                for i in range(days_to_show):
                    forecast = self.daily_forecast[i]
                    x = i * section_width # No need for padding here, centering handles spacing
                    center_x = x + section_width // 2 # Center within the section
                    
                    # Draw day name at top
                    day_text = forecast['date']
                    day_font = self.display_manager.font_manager.resolve(element_key="weather.daily.day")
                    # Handle both PIL and freetype fonts for text measurement
                    day_width = self._get_text_width(day_text, day_font)
                    draw.text((center_x - day_width // 2, 1),
                             day_text,
                             font=day_font,
                             fill=self.COLORS['text'])
                    
                    # Draw weather icon centered vertically between top/bottom text
                    icon_size = self.ICON_SIZE['large'] # Changed from medium to large (28)
                    top_text_height = 8 # Approx height reservation for top text
                    bottom_text_y = self.display_manager.matrix.height - 8 # Starting Y for bottom text
                    available_height_for_icon = bottom_text_y - top_text_height
                    # Ensure calculated y is not negative if space is very tight
                    calculated_y = top_text_height + (available_height_for_icon - icon_size) // 2
                    icon_y = (self.display_manager.matrix.height // 2) - 16
                    icon_x = center_x - icon_size // 2
                    WeatherIcons.draw_weather_icon(image, forecast['icon'], icon_x, icon_y, icon_size)
                    
                    # Draw high/low temperatures at bottom (without degree symbol)
                    temp_text = f"{forecast['temp_low']} / {forecast['temp_high']}" # Removed degree symbols
                    temp_font = self.display_manager.font_manager.resolve(element_key="weather.daily.temp")
                    # Handle both PIL and freetype fonts for text measurement
                    temp_width = self._get_text_width(temp_text, temp_font)
                    temp_y = self.display_manager.matrix.height - 8  # Position at bottom with small margin
                    draw.text((center_x - temp_width // 2, temp_y),
                             temp_text,
                             font=temp_font,
                             fill=self.COLORS['text'])
            
            # Update the display
            self.display_manager.image = image
            self.display_manager.update_display()
            self.last_daily_state = current_state

        except Exception as e:
            print(f"Error displaying daily forecast: {e}") 