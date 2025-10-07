import logging
import math
import time
import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont

from src.cache_manager import CacheManager
from src.display_manager import DisplayManager

logger = logging.getLogger(__name__)


class BaseFlightManager:
    """Base class for flight tracking with common functionality."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        self.config = config
        self.display_manager = display_manager
        self.cache_manager = cache_manager
        
        # Flight tracker configuration
        self.flight_config = config.get('flight_tracker', {})
        self.enabled = self.flight_config.get('enabled', False)
        self.update_interval = self.flight_config.get('update_interval', 5)
        self.skyaware_url = self.flight_config.get('skyaware_url', 'http://192.168.86.30/skyaware/data/aircraft.json')
        
        # Flight plan data configuration
        self.flight_plan_enabled = self.flight_config.get('flight_plan_enabled', False)
        
        # Get API key from secrets config
        secrets_config = config.get('secrets', {})
        flight_secrets = secrets_config.get('flight_tracker', {})
        self.flightaware_api_key = flight_secrets.get('flightaware_api_key', '')
        
        # Rate limiting and cost control for FlightAware API
        self.api_call_timestamps = []  # Track API call timestamps for rate limiting
        self.max_api_calls_per_hour = self.flight_config.get('max_api_calls_per_hour', 50)  # Conservative default
        self.cache_ttl_seconds = self.flight_config.get('flight_plan_cache_ttl_hours', 4) * 3600  # Convert hours to seconds
        self.min_callsign_length = self.flight_config.get('min_callsign_length', 3)  # Filter out short callsigns
        self.airline_callsign_prefixes = self.flight_config.get('airline_callsign_prefixes', [
            'AAL', 'UAL', 'DAL', 'SWA', 'JBU', 'ASQ', 'ENY', 'FFT', 'NKS', 'F9', 'G4', 'B6', 'WN', 'AA', 'UA', 'DL'
        ])  # Only fetch for known airline callsigns
        
        # Location configuration
        self.center_lat = self.flight_config.get('center_latitude', 27.9506)
        self.center_lon = self.flight_config.get('center_longitude', -82.4572)
        self.map_radius_miles = self.flight_config.get('map_radius_miles', 10)  # Reduced from 50 to 10 miles for better visibility
        self.zoom_factor = self.flight_config.get('zoom_factor', 1.0)  # Zoom factor to use more of the display
        
        # Map background configuration
        self.map_bg_config = self.flight_config.get('map_background', {})
        self.map_bg_enabled = self.map_bg_config.get('enabled', True)
        self.tile_provider = self.map_bg_config.get('tile_provider', 'osm')
        self.tile_size = self.map_bg_config.get('tile_size', 256)
        self.cache_ttl_hours = self.map_bg_config.get('cache_ttl_hours', 24)
        self.fade_intensity = self.map_bg_config.get('fade_intensity', 0.3)
        self.update_on_location_change = self.map_bg_config.get('update_on_location_change', True)
        self.disable_on_cache_error = self.map_bg_config.get('disable_on_cache_error', False)
        
        # Track cache errors
        self.cache_error_count = 0
        self.max_cache_errors = 5  # Disable after 5 consecutive cache errors
        
        # Map tile cache directory - use the same cache system as the rest of the project
        cache_dir = cache_manager.cache_dir
        if cache_dir:
            self.tile_cache_dir = Path(cache_dir) / 'map_tiles'
            try:
                self.tile_cache_dir.mkdir(parents=True, exist_ok=True)
                # Test write access
                test_file = self.tile_cache_dir / '.writetest'
                test_file.write_text('test')
                test_file.unlink()
                logger.info(f"[Flight Tracker] Using map tile cache directory: {self.tile_cache_dir}")
            except (PermissionError, OSError) as e:
                logger.warning(f"[Flight Tracker] Could not use map tile cache directory {self.tile_cache_dir}: {e}")
                # Fallback to a temporary directory
                import tempfile
                self.tile_cache_dir = Path(tempfile.gettempdir()) / 'ledmatrix_map_tiles'
                self.tile_cache_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"[Flight Tracker] Using temporary map tile cache: {self.tile_cache_dir}")
        else:
            # No cache directory available, use temporary
            import tempfile
            self.tile_cache_dir = Path(tempfile.gettempdir()) / 'ledmatrix_map_tiles'
            self.tile_cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"[Flight Tracker] Using temporary map tile cache: {self.tile_cache_dir}")
        
        # Cached map background
        self.cached_map_bg = None
        self.last_map_center = None
        self.last_map_zoom = None
        
        # Display configuration
        self.display_width = display_manager.matrix.width
        self.display_height = display_manager.matrix.height
        self.show_trails = self.flight_config.get('show_trails', False)
        self.trail_length = self.flight_config.get('trail_length', 10)
        
        # Logging rate limiting for bounds warnings
        self.bounds_warning_cache = {}
        self.bounds_warning_interval = 30  # Only log each unique coordinate once every 30 seconds
        
        # Altitude color configuration
        self.altitude_colors = self.flight_config.get('altitude_colors', {
            '0': [255, 165, 0],      # Orange
            '4000': [255, 255, 0],   # Yellow
            '8000': [0, 255, 0],     # Green
            '20000': [135, 206, 250], # Light Blue
            '30000': [0, 0, 139],    # Dark Blue
            '40000': [128, 0, 128]   # Purple
        })
        
        
        # Proximity alert configuration
        self.proximity_config = self.flight_config.get('proximity_alert', {})
        self.proximity_enabled = self.proximity_config.get('enabled', True)
        self.proximity_distance_miles = self.proximity_config.get('distance_miles', 0.1)
        self.proximity_duration = self.proximity_config.get('duration_seconds', 30)
        
        # Runtime data
        self.aircraft_data = {}  # ICAO -> aircraft dict
        self.aircraft_trails = {}  # ICAO -> list of (lat, lon, timestamp) tuples
        self.last_update = 0
        self.last_fetch = 0
        
        # Fonts
        self.fonts = self._load_fonts()
        
        logger.info(f"[Flight Tracker] Initialized with center: ({self.center_lat}, {self.center_lon}), radius: {self.map_radius_miles}mi")
        logger.info(f"[Flight Tracker] Display: {self.display_width}x{self.display_height}, SkyAware: {self.skyaware_url}")
    
    def _load_fonts(self) -> Dict[str, Any]:
        """Load fonts for text rendering with mixed approach: PressStart2P for titles, 4x6 for data."""
        fonts = {}
        try:
            # Load PressStart2P for titles (larger, more readable for headers)
            if self.display_height >= 64:
                fonts['title_small'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 8)
                fonts['title_medium'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 10)
                fonts['title_large'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 12)
            else:
                fonts['title_small'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 6)
                fonts['title_medium'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 8)
                fonts['title_large'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 10)
            
            # Load 4x6 for data (smaller, more compact for detailed info)
            if self.display_height >= 64:
                fonts['data_small'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 8)  # Larger for readability
                fonts['data_medium'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 10)
                fonts['data_large'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 12)
            else:
                fonts['data_small'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 6)
                fonts['data_medium'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 8)
                fonts['data_large'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 10)
            
            # Legacy aliases for backward compatibility
            fonts['small'] = fonts['data_small']
            fonts['medium'] = fonts['data_medium'] 
            fonts['large'] = fonts['data_large']
            
            logger.info("[Flight Tracker] Successfully loaded mixed fonts: PressStart2P for titles, 4x6 for data")
        except Exception as e:
            logger.warning(f"[Flight Tracker] Failed to load mixed fonts: {e}, using PressStart2P fallback")
            try:
                # Fallback to PressStart2P for everything
                if self.display_height >= 64:
                    fonts['title_small'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 8)
                    fonts['title_medium'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 10)
                    fonts['title_large'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 12)
                    fonts['data_small'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 6)
                    fonts['data_medium'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 8)
                    fonts['data_large'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 10)
                else:
                    fonts['title_small'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 6)
                    fonts['title_medium'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 8)
                    fonts['title_large'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 10)
                    fonts['data_small'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 5)
                    fonts['data_medium'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 6)
                    fonts['data_large'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 7)
                
                # Legacy aliases
                fonts['small'] = fonts['data_small']
                fonts['medium'] = fonts['data_medium']
                fonts['large'] = fonts['data_large']
                
                logger.info("[Flight Tracker] Using PressStart2P fallback for all fonts")
            except Exception as e2:
                logger.warning(f"[Flight Tracker] All custom fonts failed: {e2}, using default")
                fonts['title_small'] = ImageFont.load_default()
                fonts['title_medium'] = ImageFont.load_default()
                fonts['title_large'] = ImageFont.load_default()
                fonts['data_small'] = ImageFont.load_default()
                fonts['data_medium'] = ImageFont.load_default()
                fonts['data_large'] = ImageFont.load_default()
                fonts['small'] = ImageFont.load_default()
                fonts['medium'] = ImageFont.load_default()
                fonts['large'] = ImageFont.load_default()
        return fonts
    
    def _draw_text_with_outline(self, draw, text, position, font, fill=(255, 255, 255), outline_color=(0, 0, 0)):
        """Draw text with a black outline for better readability."""
        x, y = position
        # Draw outline
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        # Draw text
        draw.text((x, y), text, font=font, fill=fill)
    
    def _draw_text_pixel_perfect(self, draw, text, position, font, fill=(255, 255, 255)):
        """Draw text without outline for pixel-perfect rendering, especially for 4x6 font."""
        x, y = position
        draw.text((x, y), text, font=font, fill=fill)
    
    def _draw_text_smart(self, draw, text, position, font, fill=(255, 255, 255), outline_color=(0, 0, 0), use_outline=True):
        """Smart text drawing - uses outline for titles, pixel-perfect for data fonts."""
        # Check if this is a 4x6 font (data font) - use pixel-perfect rendering
        font_name = str(font).lower() if hasattr(font, '__str__') else ''
        is_4x6_font = '4x6' in font_name or 'data_' in str(font)
        
        if is_4x6_font and not use_outline:
            # Use pixel-perfect rendering for 4x6 data fonts
            self._draw_text_pixel_perfect(draw, text, position, font, fill)
        else:
            # Use outlined rendering for titles and when explicitly requested
            self._draw_text_with_outline(draw, text, position, font, fill, outline_color)
    
    def _get_font_height(self, font) -> int:
        """Get the height of a font for proper spacing calculations."""
        try:
            if hasattr(font, 'size'):
                # For PIL ImageFont
                return font.size
            else:
                # For BDF fonts or other types, estimate based on common sizes
                return 8  # Default fallback
        except Exception:
            return 8  # Safe fallback
    
    def _calculate_line_spacing(self, font, padding_factor: float = 1.2) -> int:
        """Calculate proper line spacing based on font height with padding."""
        font_height = self._get_font_height(font)
        return int(font_height * padding_factor)
    
    def _is_callsign_worth_fetching(self, callsign: str) -> bool:
        """Determine if a callsign is worth fetching flight plan data for."""
        if not callsign or len(callsign) < self.min_callsign_length:
            return False
        
        # Check if it's a known airline callsign
        callsign_upper = callsign.upper()
        for prefix in self.airline_callsign_prefixes:
            if callsign_upper.startswith(prefix):
                return True
        
        # Include international aircraft (they often have interesting flight plans)
        if callsign_upper.startswith(('G-', 'F-', 'D-', 'I-', 'HB-', 'OE-', 'PH-', 'SE-', 'LN-', 'OY-', 'VH-', 'C-G', 'C-F', 'JA-', 'B-', 'HL-', '9V-', 'A6-', 'VT-', 'PK-', 'HS-', 'RP-', 'ZS-', '4X-', 'SU-', 'RA-', 'UR-', 'EW-', 'S7-', 'U6-', 'FV-', 'DP-', 'P4-', 'P5-', 'P6-', 'P7-', 'P8-', 'P9-', 'P0-', 'P1-', 'P2-', 'P3-')):
            return True
        
        # Skip military/private aircraft for cost reasons
        if callsign_upper.startswith(('N', 'C-', 'CF-')):  # Military or private aircraft
            return False
        
        return False  # Default to not fetching unknown patterns
    
    def _categorize_aircraft(self, callsign: str) -> str:
        """Categorize aircraft based on callsign patterns."""
        if not callsign:
            return "Unknown"
        
        callsign_upper = callsign.upper()
        
        # Check for military patterns
        if callsign_upper.startswith(('C-', 'CF-', 'AF-', 'NATO-', 'USAF-', 'USN-', 'USMC-', 'USCG-')):
            return "Military"
        
        # Check for private aircraft (N-prefix with numbers/letters)
        if callsign_upper.startswith('N') and len(callsign) >= 4:
            return "Private"
        
        # Check for known airline callsigns
        for prefix in self.airline_callsign_prefixes:
            if callsign_upper.startswith(prefix):
                return "Airline"
        
        # Check for other patterns
        if callsign_upper.startswith(('G-', 'F-', 'D-', 'I-', 'HB-', 'OE-', 'PH-', 'SE-', 'LN-', 'OY-', 'OY-', 'VH-', 'C-G', 'C-F', 'JA-', 'B-', 'HL-', '9V-', 'A6-', 'VT-', 'PK-', 'HS-', 'RP-', 'ZS-', '4X-', 'SU-', 'RA-', 'UR-', 'EW-', 'S7-', 'U6-', 'FV-', 'DP-', 'P4-', 'P5-', 'P6-', 'P7-', 'P8-', 'P9-', 'P0-', 'P1-', 'P2-', 'P3-')):
            return "International"
        
        # Default categorization
        if len(callsign) <= 3:
            return "Unknown"
        elif callsign_upper.startswith('N'):
            return "Private"
        else:
            return "Other"
    
    def _check_rate_limit(self) -> bool:
        """Check if we're within API rate limits."""
        current_time = time.time()
        hour_ago = current_time - 3600  # 1 hour ago
        
        # Remove timestamps older than 1 hour
        self.api_call_timestamps = [ts for ts in self.api_call_timestamps if ts > hour_ago]
        
        # Check if we're under the limit
        if len(self.api_call_timestamps) >= self.max_api_calls_per_hour:
            logger.warning(f"[Flight Tracker] Rate limit reached: {len(self.api_call_timestamps)}/{self.max_api_calls_per_hour} calls in the last hour")
            return False
        
        return True
    
    def _record_api_call(self):
        """Record an API call for rate limiting."""
        self.api_call_timestamps.append(time.time())
        logger.debug(f"[Flight Tracker] API call recorded. Total calls in last hour: {len(self.api_call_timestamps)}")
    
    
    def _fetch_aircraft_data(self) -> Optional[Dict]:
        """Fetch aircraft data from SkyAware API."""
        try:
            response = requests.get(self.skyaware_url, timeout=5)
            response.raise_for_status()
            data = response.json()
            
            # Cache the data
            self.cache_manager.set('flight_tracker_data', data)
            
            logger.debug(f"[Flight Tracker] Fetched data: {len(data.get('aircraft', []))} aircraft")
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"[Flight Tracker] Failed to fetch aircraft data: {e}")
            
            # Try to use cached data
            cached_data = self.cache_manager.get('flight_tracker_data')
            if cached_data:
                logger.info("[Flight Tracker] Using cached aircraft data")
                return cached_data
            
            return None
    
    def _get_flight_plan_data(self, callsign: str) -> Dict[str, str]:
        """Get flight plan data for a callsign (origin/destination)."""
        if not self.flight_plan_enabled or not self.flightaware_api_key:
            logger.debug(f"[Flight Tracker] Flight plan disabled or no API key for {callsign}")
            return {'origin': 'Unknown', 'destination': 'Unknown', 'aircraft_type': 'Unknown'}
        
        # Check if callsign is worth fetching (cost control)
        if not self._is_callsign_worth_fetching(callsign):
            logger.debug(f"[Flight Tracker] Skipping flight plan fetch for {callsign} (not worth fetching)")
            category = self._categorize_aircraft(callsign)
            return {'origin': 'Unknown', 'destination': 'Unknown', 'aircraft_type': category}
        
        # Check rate limiting
        if not self._check_rate_limit():
            logger.warning(f"[Flight Tracker] Rate limit reached, skipping API call for {callsign}")
            return {'origin': 'Unknown', 'destination': 'Unknown', 'aircraft_type': 'Unknown'}
        
        # Use cache manager for flight plan data
        cache_key = f"flight_plan_{callsign}"
        cached_data = self.cache_manager.get(cache_key, max_age=self.cache_ttl_seconds)
        
        if cached_data:
            logger.debug(f"[Flight Tracker] Using cached flight plan for {callsign}")
            return cached_data
        
        logger.info(f"[Flight Tracker] Fetching flight plan data for {callsign}")
        
        try:
            # FlightAware AeroAPI integration
            url = f"https://aeroapi.flightaware.com/aeroapi/flights/{callsign}"
            headers = {"x-apikey": self.flightaware_api_key}
            
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                
                # Handle the API response format - it returns an array of flights
                if 'flights' in data and data['flights']:
                    # Get the first (most recent) flight
                    flight = data['flights'][0]
                    flight_plan = {
                        'origin': flight.get('origin', {}).get('code', 'Unknown'),
                        'destination': flight.get('destination', {}).get('code', 'Unknown'),
                        'aircraft_type': flight.get('aircraft_type', 'Unknown')
                    }
                else:
                    # Fallback for single flight response format
                    flight_plan = {
                        'origin': data.get('origin', {}).get('code', 'Unknown'),
                        'destination': data.get('destination', {}).get('code', 'Unknown'),
                        'aircraft_type': data.get('aircraft_type', 'Unknown')
                    }
                
                # Cache using the cache manager
                self.cache_manager.set(cache_key, flight_plan)
                self._record_api_call()
                logger.info(f"[Flight Tracker] Successfully fetched and cached flight plan for {callsign}: {flight_plan['origin']} -> {flight_plan['destination']}")
                return flight_plan
            else:
                logger.warning(f"[Flight Tracker] API returned status {response.status_code} for {callsign}: {response.text[:100]}")
                return {'origin': 'Unknown', 'destination': 'Unknown', 'aircraft_type': 'Unknown'}
                
        except Exception as e:
            logger.warning(f"[Flight Tracker] Failed to fetch flight plan for {callsign}: {e}")
            return {'origin': 'Unknown', 'destination': 'Unknown', 'aircraft_type': 'Unknown'}
    
    def _process_aircraft_data(self, data: Dict) -> None:
        """Process and update aircraft data."""
        if not data or 'aircraft' not in data:
            return
        
        current_time = time.time()
        active_icao = set()
        
        for aircraft in data['aircraft']:
            # Extract required fields
            icao = aircraft.get('hex', '').upper()
            if not icao:
                continue
            
            # Check if aircraft has valid position
            lat = aircraft.get('lat')
            lon = aircraft.get('lon')
            if lat is None or lon is None:
                continue
            
            # Calculate distance from center
            distance_miles = self._calculate_distance(lat, lon, self.center_lat, self.center_lon)
            
            # Filter by radius
            if distance_miles > self.map_radius_miles:
                continue
            
            active_icao.add(icao)
            
            # Extract other fields
            altitude = aircraft.get('alt_baro', aircraft.get('alt_geom', 0))
            if altitude == 'ground':
                altitude = 0
            
            callsign = aircraft.get('flight', '').strip() or icao
            speed = aircraft.get('gs', 0)  # Ground speed in knots
            heading = aircraft.get('track', aircraft.get('heading', 0))
            aircraft_type = aircraft.get('t', 'Unknown')
            
            # Calculate color based on altitude
            color = self._altitude_to_color(altitude)
            
            # Build aircraft dict
            aircraft_info = {
                'icao': icao,
                'callsign': callsign,
                'lat': lat,
                'lon': lon,
                'altitude': altitude,
                'speed': speed,
                'heading': heading,
                'aircraft_type': aircraft_type,
                'distance_miles': distance_miles,
                'color': color,
                'last_seen': current_time
            }
            
            # Update aircraft data
            self.aircraft_data[icao] = aircraft_info
            
            # Update trail if enabled
            if self.show_trails:
                if icao not in self.aircraft_trails:
                    self.aircraft_trails[icao] = []
                
                self.aircraft_trails[icao].append((lat, lon, current_time))
                
                # Limit trail length
                if len(self.aircraft_trails[icao]) > self.trail_length:
                    self.aircraft_trails[icao] = self.aircraft_trails[icao][-self.trail_length:]
        
        # Clean up old aircraft (not seen in last 60 seconds)
        stale_icao = [icao for icao, info in self.aircraft_data.items() 
                      if current_time - info['last_seen'] > 60]
        for icao in stale_icao:
            del self.aircraft_data[icao]
            if icao in self.aircraft_trails:
                del self.aircraft_trails[icao]
        
        logger.debug(f"[Flight Tracker] Processed {len(active_icao)} aircraft, removed {len(stale_icao)} stale")
    
    def _altitude_to_color(self, altitude: float) -> Tuple[int, int, int]:
        """Convert altitude to color using gradient interpolation."""
        # Sort altitude breakpoints
        breakpoints = sorted([(int(k), v) for k, v in self.altitude_colors.items()])
        
        # Handle edge cases
        if altitude <= breakpoints[0][0]:
            return tuple(breakpoints[0][1])
        if altitude >= breakpoints[-1][0]:
            return tuple(breakpoints[-1][1])
        
        # Find the two breakpoints to interpolate between
        for i in range(len(breakpoints) - 1):
            alt1, color1 = breakpoints[i]
            alt2, color2 = breakpoints[i + 1]
            
            if alt1 <= altitude <= alt2:
                # Linear interpolation
                ratio = (altitude - alt1) / (alt2 - alt1)
                r = int(color1[0] + (color2[0] - color1[0]) * ratio)
                g = int(color1[1] + (color2[1] - color1[1]) * ratio)
                b = int(color1[2] + (color2[2] - color1[2]) * ratio)
                return (r, g, b)
        
        # Fallback (shouldn't reach here)
        return (255, 255, 255)
    
    def _calculate_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two lat/lon points in miles using Haversine formula."""
        R = 3959  # Earth's radius in miles
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        
        a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return R * c
    
    def _latlon_to_pixel(self, lat: float, lon: float) -> Optional[Tuple[int, int]]:
        """Convert lat/lon to pixel coordinates on the display."""
        # Calculate degrees per pixel based on radius and display size
        # Apply zoom factor to use more of the display area
        
        # Degrees of latitude/longitude to cover
        # 1 degree of latitude ≈ 69 miles, 1 degree of longitude varies by latitude
        lat_degrees = (self.map_radius_miles * 2) / 69.0
        
        # Adjust longitude for latitude (longitude lines converge at poles)
        lon_degrees = lat_degrees / math.cos(math.radians(self.center_lat))
        
        # Apply zoom factor to reduce the effective area and use more of the display
        effective_lat_degrees = lat_degrees / self.zoom_factor
        effective_lon_degrees = lon_degrees / self.zoom_factor
        
        # Calculate pixel scale - use full display dimensions with zoom
        lat_scale = self.display_height / effective_lat_degrees
        lon_scale = self.display_width / effective_lon_degrees
        
        # Convert to pixel coordinates (center of display is center_lat, center_lon)
        x = int((lon - self.center_lon) * lon_scale + self.display_width / 2)
        y = int((self.center_lat - lat) * lat_scale + self.display_height / 2)  # Flip Y axis
        
        # Debug logging
        logger.debug(f"[Flight Tracker] Converting ({lat:.6f}, {lon:.6f}) to pixel ({x}, {y})")
        logger.debug(f"[Flight Tracker] Scale: lat={lat_scale:.2f}, lon={lon_scale:.2f}, effective_lat_degrees={effective_lat_degrees:.4f}, effective_lon_degrees={effective_lon_degrees:.4f}, zoom_factor={self.zoom_factor}")
        
        # Check if within display bounds
        if 0 <= x < self.display_width and 0 <= y < self.display_height:
            return (x, y)
        
        # Rate limit bounds warnings to prevent spam
        coord_key = f"{lat:.6f},{lon:.6f}"
        current_time = time.time()
        
        if coord_key not in self.bounds_warning_cache or \
           current_time - self.bounds_warning_cache[coord_key] > self.bounds_warning_interval:
            logger.debug(f"[Flight Tracker] Coordinate ({lat}, {lon}) -> pixel ({x}, {y}) is outside display bounds {self.display_width}x{self.display_height}")
            self.bounds_warning_cache[coord_key] = current_time
        
        return None
    
    def _latlon_to_tile_coords(self, lat: float, lon: float, zoom: int) -> Tuple[int, int]:
        """Convert lat/lon to tile coordinates for a given zoom level."""
        n = 2.0 ** zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
        return (x, y)
    
    def _get_tile_url(self, x: int, y: int, zoom: int) -> str:
        """Get the URL for a map tile based on provider."""
        if self.tile_provider == 'osm':
            # OpenStreetMap tile server
            return f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
        elif self.tile_provider == 'carto':
            # CartoDB Positron (light theme)
            return f"https://cartodb-basemaps-a.global.ssl.fastly.net/light_all/{zoom}/{x}/{y}.png"
        elif self.tile_provider == 'carto_dark':
            # CartoDB Dark Matter
            return f"https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{zoom}/{x}/{y}.png"
        else:
            # Default to OSM
            return f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
    
    def _get_tile_cache_path(self, x: int, y: int, zoom: int) -> Path:
        """Get the cache file path for a tile."""
        return self.tile_cache_dir / f"{self.tile_provider}_{zoom}_{x}_{y}.png"
    
    def _is_tile_cached(self, x: int, y: int, zoom: int) -> bool:
        """Check if a tile is cached and not expired."""
        cache_path = self._get_tile_cache_path(x, y, zoom)
        if not cache_path.exists():
            return False
        
        # Check if tile is not expired
        tile_age = time.time() - cache_path.stat().st_mtime
        return tile_age < (self.cache_ttl_hours * 3600)
    
    def _fetch_tile(self, x: int, y: int, zoom: int) -> Optional[Image.Image]:
        """Fetch a map tile, using cache if available."""
        cache_path = self._get_tile_cache_path(x, y, zoom)
        
        # Try to load from cache first
        if self._is_tile_cached(x, y, zoom):
            try:
                return Image.open(cache_path)
            except Exception as e:
                logger.warning(f"[Flight Tracker] Failed to load cached tile {x},{y},{zoom}: {e}")
        
        # Fetch from server
        try:
            url = self._get_tile_url(x, y, zoom)
            logger.debug(f"[Flight Tracker] Fetching tile from {url}")
            
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            # Save to cache
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, 'wb') as f:
                    f.write(response.content)
                logger.debug(f"[Flight Tracker] Cached tile {x},{y},{zoom}")
                # Reset cache error count on successful cache
                if self.cache_error_count > 0:
                    self.cache_error_count = 0
            except (PermissionError, OSError) as e:
                logger.warning(f"[Flight Tracker] Could not save tile to cache {cache_path}: {e}")
                # Track cache error
                self.cache_error_count += 1
                # Continue without caching - create a temporary file
                import tempfile
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                temp_file.write(response.content)
                temp_file.close()
                return Image.open(temp_file.name)
            
            return Image.open(cache_path)
            
        except Exception as e:
            logger.warning(f"[Flight Tracker] Failed to fetch tile {x},{y},{zoom}: {e}")
            return None
    
    def _get_map_background(self, center_lat: float, center_lon: float) -> Optional[Image.Image]:
        """Get the map background for the current view."""
        if not self.map_bg_enabled:
            return None
        
        # Check if we should disable due to cache errors
        if self.disable_on_cache_error and self.cache_error_count >= self.max_cache_errors:
            logger.warning(f"[Flight Tracker] Map background disabled due to {self.cache_error_count} consecutive cache errors")
            return None
        
        # Calculate appropriate zoom level based on map radius
        # Higher zoom for smaller radius to show more detail
        if self.map_radius_miles <= 2:
            zoom = 13  # Very detailed for small areas
        elif self.map_radius_miles <= 5:
            zoom = 12  # Detailed for local areas
        elif self.map_radius_miles <= 10:
            zoom = 11  # Good for city/metro areas
        elif self.map_radius_miles <= 25:
            zoom = 10  # Regional view
        elif self.map_radius_miles <= 50:
            zoom = 9   # State-level view
        else:
            zoom = 8   # Multi-state view
        
        # Check if we need to update the background
        current_center = (round(center_lat, 4), round(center_lon, 4))
        if (self.cached_map_bg is not None and 
            self.last_map_center == current_center and 
            self.last_map_zoom == zoom and
            not self.update_on_location_change):
            return self.cached_map_bg
        
        # Calculate tile coordinates for center
        center_x, center_y = self._latlon_to_tile_coords(center_lat, center_lon, zoom)
        
        # Calculate how many tiles we need to cover the display
        # Each tile covers a certain lat/lon area
        lat_degrees = (self.map_radius_miles * 2) / 69.0
        lon_degrees = lat_degrees / math.cos(math.radians(center_lat))
        
        # Calculate tile coverage - get more tiles for better coverage
        tiles_per_degree = 2 ** zoom
        # Add extra tiles to ensure we have good coverage
        tiles_x = max(2, int(lon_degrees * tiles_per_degree / 360.0 * 2) + 4)
        tiles_y = max(2, int(lat_degrees * tiles_per_degree / 360.0 * 2) + 4)
        
        # Calculate tile bounds
        start_x = center_x - tiles_x // 2
        start_y = center_y - tiles_y // 2
        
        # Create composite image
        composite_width = tiles_x * self.tile_size
        composite_height = tiles_y * self.tile_size
        composite = Image.new('RGB', (composite_width, composite_height), (0, 0, 0))
        
        # Fetch and composite tiles
        tiles_fetched = 0
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                tile_x = start_x + tx
                tile_y = start_y + ty
                
                tile_img = self._fetch_tile(tile_x, tile_y, zoom)
                if tile_img:
                    # Paste tile into composite
                    paste_x = tx * self.tile_size
                    paste_y = ty * self.tile_size
                    composite.paste(tile_img, (paste_x, paste_y))
                    tiles_fetched += 1
        
        if tiles_fetched == 0:
            logger.warning("[Flight Tracker] No map tiles could be fetched")
            return None
        
        # Calculate the crop area to match our display bounds
        # Find the center tile and position within it
        center_tile_x = center_x - start_x
        center_tile_y = center_y - start_y
        
        # Calculate position within the center tile
        center_lon_in_tile = (center_lon - self._tile_to_lon(start_x + center_tile_x, zoom)) / (self._tile_to_lon(start_x + center_tile_x + 1, zoom) - self._tile_to_lon(start_x + center_tile_x, zoom))
        center_lat_in_tile = (self._tile_to_lat(start_y + center_tile_y, zoom) - center_lat) / (self._tile_to_lat(start_y + center_tile_y, zoom) - self._tile_to_lat(start_y + center_tile_y + 1, zoom))
        
        # Calculate pixel position in composite
        center_pixel_x = int((center_tile_x + center_lon_in_tile) * self.tile_size)
        center_pixel_y = int((center_tile_y + center_lat_in_tile) * self.tile_size)
        
        # Calculate crop bounds centered on the center point
        crop_left = max(0, center_pixel_x - self.display_width // 2)
        crop_top = max(0, center_pixel_y - self.display_height // 2)
        crop_right = min(composite_width, crop_left + self.display_width)
        crop_bottom = min(composite_height, crop_top + self.display_height)
        
        # Crop to display size
        cropped = composite.crop((crop_left, crop_top, crop_right, crop_bottom))
        
        # Resize to exact display dimensions
        if cropped.size != (self.display_width, self.display_height):
            cropped = cropped.resize((self.display_width, self.display_height), Image.Resampling.LANCZOS)
        
        # Apply fade effect
        if self.fade_intensity < 1.0:
            # Create a fade overlay
            fade_overlay = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            cropped = Image.blend(cropped, fade_overlay, 1.0 - self.fade_intensity)
        
        # Cache the result
        self.cached_map_bg = cropped
        self.last_map_center = current_center
        self.last_map_zoom = zoom
        
        logger.info(f"[Flight Tracker] Generated map background with {tiles_fetched} tiles at zoom {zoom}")
        logger.info(f"[Flight Tracker] Center: ({center_lat:.4f}, {center_lon:.4f}), Radius: {self.map_radius_miles}mi")
        logger.info(f"[Flight Tracker] Tile coverage: {tiles_x}x{tiles_y}, Crop: ({crop_left},{crop_top})-({crop_right},{crop_bottom})")
        return cropped
    
    def _tile_to_lat(self, y: int, zoom: int) -> float:
        """Convert tile Y coordinate to latitude."""
        n = 2.0 ** zoom
        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
        return math.degrees(lat_rad)
    
    def _tile_to_lon(self, x: int, zoom: int) -> float:
        """Convert tile X coordinate to longitude."""
        n = 2.0 ** zoom
        return x / n * 360.0 - 180.0
    
    def update(self) -> None:
        """Update aircraft data from SkyAware."""
        current_time = time.time()
        
        # Check if it's time to fetch new data
        if current_time - self.last_fetch >= self.update_interval:
            self.last_fetch = current_time
            
            data = self._fetch_aircraft_data()
            if data:
                self._process_aircraft_data(data)
            
            self.last_update = current_time
    
    def get_closest_aircraft(self) -> Optional[Dict]:
        """Get the closest aircraft to the center point."""
        if not self.aircraft_data:
            return None
        
        closest = min(self.aircraft_data.values(), key=lambda a: a['distance_miles'])
        return closest
    
    def display(self, force_clear: bool = False) -> None:
        """Display method to be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement display()")


class FlightMapManager(BaseFlightManager):
    """Manager for map view display of aircraft."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        logger.info("[Flight Tracker] Initialized Map Manager")
    
    def display(self, force_clear: bool = False) -> None:
        """Display the flight map with aircraft and geographical background."""
        if force_clear:
            self.display_manager.clear()
        
        # Get map background if enabled
        map_bg = self._get_map_background(self.center_lat, self.center_lon)
        
        # Create image with background
        if map_bg:
            img = map_bg.copy()
        else:
            img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        
        draw = ImageDraw.Draw(img)
        
        # Draw center position marker (white dot at our lat/lon)
        center_pixel = self._latlon_to_pixel(self.center_lat, self.center_lon)
        if center_pixel:
            x, y = center_pixel
            # Draw a more visible center marker with outline
            # Draw black outline first
            for dx in [-2, -1, 0, 1, 2]:
                for dy in [-2, -1, 0, 1, 2]:
                    if abs(dx) + abs(dy) <= 2:
                        draw.point((x + dx, y + dy), fill=(0, 0, 0))
            
            # Draw white center
            draw.point((x, y), fill=(255, 255, 255))
            draw.point((x-1, y), fill=(255, 255, 255))
            draw.point((x+1, y), fill=(255, 255, 255))
            draw.point((x, y-1), fill=(255, 255, 255))
            draw.point((x, y+1), fill=(255, 255, 255))
        
        # Draw aircraft trails if enabled
        if self.show_trails:
            for icao, trail in self.aircraft_trails.items():
                if icao not in self.aircraft_data:
                    continue
                
                aircraft = self.aircraft_data[icao]
                trail_pixels = []
                
                for lat, lon, timestamp in trail:
                    pixel = self._latlon_to_pixel(lat, lon)
                    if pixel:
                        trail_pixels.append(pixel)
                
                # Draw trail with fading effect
                if len(trail_pixels) >= 2:
                    for i in range(len(trail_pixels) - 1):
                        # Fade from dim to bright
                        alpha = int(255 * (i + 1) / len(trail_pixels))
                        color = tuple(int(c * alpha / 255) for c in aircraft['color'])
                        draw.line([trail_pixels[i], trail_pixels[i + 1]], fill=color, width=1)
        
        # Draw aircraft
        is_small_display = self.display_width <= 128 and self.display_height <= 32
        
        for aircraft in self.aircraft_data.values():
            pixel = self._latlon_to_pixel(aircraft['lat'], aircraft['lon'])
            if not pixel:
                continue
            
            x, y = pixel
            color = aircraft['color']
            
            if is_small_display:
                # Small display: single pixel with outline for visibility
                # Draw black outline
                for dx in [-1, 0, 1]:
                    for dy in [-1, 0, 1]:
                        if dx != 0 or dy != 0:
                            draw.point((x + dx, y + dy), fill=(0, 0, 0))
                # Draw colored center
                draw.point((x, y), fill=color)
            else:
                # Large display: small arrow showing heading with outline
                heading = aircraft['heading']
                if heading:
                    # Calculate arrow points
                    angle_rad = math.radians(heading)
                    dx = int(2 * math.sin(angle_rad))
                    dy = int(-2 * math.cos(angle_rad))
                    
                    # Draw arrow with black outline
                    arrow_points = [(x + dx, y + dy), (x, y)]
                    
                    # Draw black outline for arrow head
                    for px, py in arrow_points:
                        for ox in [-1, 0, 1]:
                            for oy in [-1, 0, 1]:
                                if ox != 0 or oy != 0:
                                    draw.point((px + ox, py + oy), fill=(0, 0, 0))
                    
                    # Draw colored arrow head
                    draw.point((x + dx, y + dy), fill=color)
                    draw.point((x, y), fill=color)
                    
                    # Draw small wings with outline
                    wing_angle = math.radians(heading + 135)
                    wx1 = int(math.sin(wing_angle))
                    wy1 = int(-math.cos(wing_angle))
                    # Outline
                    for ox in [-1, 0, 1]:
                        for oy in [-1, 0, 1]:
                            if ox != 0 or oy != 0:
                                draw.point((x + wx1 + ox, y + wy1 + oy), fill=(0, 0, 0))
                    draw.point((x + wx1, y + wy1), fill=color)
                    
                    wing_angle = math.radians(heading - 135)
                    wx2 = int(math.sin(wing_angle))
                    wy2 = int(-math.cos(wing_angle))
                    # Outline
                    for ox in [-1, 0, 1]:
                        for oy in [-1, 0, 1]:
                            if ox != 0 or oy != 0:
                                draw.point((x + wx2 + ox, y + wy2 + oy), fill=(0, 0, 0))
                    draw.point((x + wx2, y + wy2), fill=color)
                else:
                    # No heading data, draw single pixel with outline
                    # Draw black outline
                    for dx in [-1, 0, 1]:
                        for dy in [-1, 0, 1]:
                            if dx != 0 or dy != 0:
                                draw.point((x + dx, y + dy), fill=(0, 0, 0))
                    # Draw colored center
                    draw.point((x, y), fill=color)
        
        # Draw info text with pixel-perfect rendering for better readability
        if len(self.aircraft_data) > 0:
            info_text = f"{len(self.aircraft_data)} aircraft"
            self._draw_text_smart(draw, info_text, (2, 2), self.fonts['small'], 
                                fill=(200, 200, 200), use_outline=False)
        
        # Display the image
        self.display_manager.image = img.copy()
        self.display_manager.update_display()


class FlightOverheadManager(BaseFlightManager):
    """Manager for detailed overhead view of closest aircraft."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.proximity_triggered_time = None
        logger.info("[Flight Tracker] Initialized Overhead Manager")
    
    def should_trigger_proximity_alert(self) -> bool:
        """Check if proximity alert should be triggered."""
        if not self.proximity_enabled:
            return False
        
        closest = self.get_closest_aircraft()
        if not closest:
            return False
        
        return closest['distance_miles'] <= self.proximity_distance_miles
    
    def display(self, force_clear: bool = False) -> None:
        """Display detailed overhead view of closest aircraft."""
        if force_clear:
            self.display_manager.clear()
        
        closest = self.get_closest_aircraft()
        if not closest:
            # No aircraft to display
            img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            self._draw_text_with_outline(draw, "No Aircraft", 
                                       (self.display_width // 2 - 30, self.display_height // 2 - 4), 
                                       self.fonts['medium'], fill=(200, 200, 200), outline_color=(0, 0, 0))
            self.display_manager.image = img.copy()
            self.display_manager.update_display()
            return
        
        # Create image
        img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Determine layout based on display size
        is_small_display = self.display_width <= 128 and self.display_height <= 32
        
        if is_small_display:
            # Small display layout (128x32) with dynamic spacing
            y_offset = 2
            
            # Line 1: Callsign (using 4x6 for compact data) - pixel perfect
            self._draw_text_smart(draw, f"{closest['callsign']}", (2, y_offset), 
                                self.fonts['data_medium'], fill=(255, 255, 255), use_outline=False)
            y_offset += self._calculate_line_spacing(self.fonts['data_medium'])
            
            # Line 2: Altitude and Speed (using 4x6 for compact data) - pixel perfect
            self._draw_text_smart(draw, f"ALT:{int(closest['altitude'])}ft", (2, y_offset), 
                                self.fonts['data_small'], fill=closest['color'], use_outline=False)
            self._draw_text_smart(draw, f"SPD:{int(closest['speed'])}kt", (self.display_width // 2, y_offset), 
                                self.fonts['data_small'], fill=(200, 200, 200), use_outline=False)
            y_offset += self._calculate_line_spacing(self.fonts['data_small'])
            
            # Line 3: Distance and Heading (using 4x6 for compact data) - pixel perfect
            self._draw_text_smart(draw, f"DIST:{closest['distance_miles']:.2f}mi", (2, y_offset), 
                                self.fonts['data_small'], fill=(200, 200, 200), use_outline=False)
            if closest['heading']:
                self._draw_text_smart(draw, f"HDG:{int(closest['heading'])}°", (self.display_width // 2, y_offset), 
                                    self.fonts['data_small'], fill=(200, 200, 200), use_outline=False)
            y_offset += self._calculate_line_spacing(self.fonts['data_small'])
            
            # Line 4: Type (using 4x6 for compact data) - pixel perfect, only if there's space
            if y_offset + self._calculate_line_spacing(self.fonts['data_small']) <= self.display_height:
                self._draw_text_smart(draw, f"TYPE:{closest['aircraft_type']}", (2, y_offset), 
                                    self.fonts['data_small'], fill=(150, 150, 150), use_outline=False)
        else:
            # Large display layout (192x96 or bigger) with dynamic spacing
            y_offset = 4
            
            # Title (using PressStart2P for better readability)
            self._draw_text_with_outline(draw, "OVERHEAD AIRCRAFT", (self.display_width // 2 - 40, y_offset), 
                                       self.fonts['title_large'], fill=(255, 200, 0), outline_color=(0, 0, 0))
            y_offset += self._calculate_line_spacing(self.fonts['title_large']) + 4
            
            # Callsign (using 4x6 for compact data) - pixel perfect
            self._draw_text_smart(draw, f"Callsign: {closest['callsign']}", (4, y_offset), 
                                self.fonts['data_large'], fill=(255, 255, 255), use_outline=False)
            y_offset += self._calculate_line_spacing(self.fonts['data_large'])
            
            # Altitude (using 4x6 for compact data) - pixel perfect
            self._draw_text_smart(draw, f"Altitude: {int(closest['altitude'])} ft", (4, y_offset), 
                                self.fonts['data_medium'], fill=closest['color'], use_outline=False)
            y_offset += self._calculate_line_spacing(self.fonts['data_medium'])
            
            # Speed (using 4x6 for compact data) - pixel perfect
            self._draw_text_smart(draw, f"Speed: {int(closest['speed'])} knots", (4, y_offset), 
                                self.fonts['data_medium'], fill=(200, 200, 200), use_outline=False)
            y_offset += self._calculate_line_spacing(self.fonts['data_medium'])
            
            # Distance (using 4x6 for compact data) - pixel perfect
            self._draw_text_smart(draw, f"Distance: {closest['distance_miles']:.2f} miles", (4, y_offset), 
                                self.fonts['data_medium'], fill=(255, 150, 0), use_outline=False)
            y_offset += self._calculate_line_spacing(self.fonts['data_medium'])
            
            # Heading (using 4x6 for compact data) - pixel perfect
            if closest['heading']:
                self._draw_text_smart(draw, f"Heading: {int(closest['heading'])}°", (4, y_offset), 
                                    self.fonts['data_medium'], fill=(200, 200, 200), use_outline=False)
                y_offset += self._calculate_line_spacing(self.fonts['data_medium'])
            
            # Aircraft type (using 4x6 for compact data) - pixel perfect, only if there's space
            if y_offset + self._calculate_line_spacing(self.fonts['data_medium']) <= self.display_height:
                self._draw_text_smart(draw, f"Type: {closest['aircraft_type']}", (4, y_offset), 
                                    self.fonts['data_medium'], fill=(150, 150, 150), use_outline=False)
        
        # Display the image
        self.display_manager.image = img.copy()
        self.display_manager.update_display()


class FlightStatsManager(BaseFlightManager):
    """Manager for flight statistics display."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager, cache_manager: CacheManager):
        super().__init__(config, display_manager, cache_manager)
        self.current_stat = 0
        self.last_stat_change = 0
        self.stat_duration = 10  # Show each stat for 10 seconds
        logger.info("[Flight Tracker] Initialized Stats Manager")
    
    def display(self, force_clear: bool = False) -> None:
        """Display flight statistics."""
        if force_clear:
            self.display_manager.clear()
        
        if not self.aircraft_data:
            # No aircraft to display
            img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            self._draw_text_with_outline(draw, "No Aircraft", 
                                       (self.display_width // 2 - 30, self.display_height // 2 - 4), 
                                       self.fonts['medium'], fill=(200, 200, 200), outline_color=(0, 0, 0))
            self.display_manager.image = img.copy()
            self.display_manager.update_display()
            return
        
        # Rotate stats every 10 seconds
        current_time = time.time()
        if current_time - self.last_stat_change >= self.stat_duration:
            self.current_stat = (self.current_stat + 1) % 3
            self.last_stat_change = current_time
        
        # Create image
        img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Determine layout based on display size
        is_small_display = self.display_width <= 128 and self.display_height <= 32
        
        # Get statistics
        if self.current_stat == 0:
            # Closest plane
            aircraft = min(self.aircraft_data.values(), key=lambda a: a['distance_miles'])
            title = "CLOSEST"
            title_color = (255, 100, 0)
        elif self.current_stat == 1:
            # Fastest plane
            aircraft = max(self.aircraft_data.values(), key=lambda a: a['speed'])
            title = "FASTEST"
            title_color = (0, 255, 100)
        else:
            # Highest plane
            aircraft = max(self.aircraft_data.values(), key=lambda a: a['altitude'])
            title = "HIGHEST"
            title_color = (100, 150, 255)
        
        if is_small_display:
            # Small display layout with dynamic spacing
            y_offset = 1
            
            # Title (using PressStart2P for better readability)
            self._draw_text_with_outline(draw, title, (2, y_offset), 
                                       self.fonts['title_medium'], fill=title_color, outline_color=(0, 0, 0))
            y_offset += self._calculate_line_spacing(self.fonts['title_medium'])
            
            # Callsign (using 4x6 for compact data) - pixel perfect
            self._draw_text_smart(draw, aircraft['callsign'], (2, y_offset), 
                                self.fonts['data_small'], fill=(255, 255, 255), use_outline=False)
            y_offset += self._calculate_line_spacing(self.fonts['data_small'])
            
            # Key stat (using 4x6 for compact data) - pixel perfect
            if self.current_stat == 0:
                stat_text = f"{aircraft['distance_miles']:.2f}mi"
            elif self.current_stat == 1:
                stat_text = f"{int(aircraft['speed'])}kt"
            else:
                stat_text = f"{int(aircraft['altitude'])}ft"
            
            self._draw_text_smart(draw, stat_text, (2, y_offset), 
                                self.fonts['data_medium'], fill=aircraft['color'], use_outline=False)
            y_offset += self._calculate_line_spacing(self.fonts['data_medium']) + 1
            
            # Additional info (using 4x6 for compact data) - pixel perfect, only if there's space
            if y_offset + self._calculate_line_spacing(self.fonts['data_small']) <= self.display_height:
                self._draw_text_smart(draw, f"ALT:{int(aircraft['altitude'])} SPD:{int(aircraft['speed'])}", (2, y_offset), 
                                    self.fonts['data_small'], fill=(150, 150, 150), use_outline=False)
            
            # Right side info - stacked vertically
            right_x = self.display_width - 60  # Start 60 pixels from right edge
            right_y = 1
            
            # Aircraft type
            if right_y + self._calculate_line_spacing(self.fonts['data_small']) <= self.display_height:
                self._draw_text_smart(draw, f"TYPE: {aircraft['aircraft_type']}", (right_x, right_y), 
                                    self.fonts['data_small'], fill=(200, 200, 200), use_outline=False)
                right_y += self._calculate_line_spacing(self.fonts['data_small'])
            
            # Origin (from flight plan data)
            if right_y + self._calculate_line_spacing(self.fonts['data_small']) <= self.display_height:
                flight_plan = self._get_flight_plan_data(aircraft['callsign'])
                origin = flight_plan.get('origin', 'Unknown')
                aircraft_type = flight_plan.get('aircraft_type', 'Unknown')
                
                # Show appropriate information based on aircraft type
                if origin == 'Unknown' and aircraft_type in ['Military', 'Private', 'Other']:
                    self._draw_text_smart(draw, f"TYPE: {aircraft_type}", (right_x, right_y), 
                                        self.fonts['data_small'], fill=(255, 200, 0), use_outline=False)
                else:
                    self._draw_text_smart(draw, f"FROM: {origin}", (right_x, right_y), 
                                        self.fonts['data_small'], fill=(150, 150, 150), use_outline=False)
                right_y += self._calculate_line_spacing(self.fonts['data_small'])
            
            # Destination (from flight plan data)
            if right_y + self._calculate_line_spacing(self.fonts['data_small']) <= self.display_height:
                flight_plan = self._get_flight_plan_data(aircraft['callsign'])
                destination = flight_plan.get('destination', 'Unknown')
                aircraft_type = flight_plan.get('aircraft_type', 'Unknown')
                
                # Show appropriate information based on aircraft type
                if destination == 'Unknown' and aircraft_type in ['Military', 'Private', 'Other']:
                    # Skip destination for non-airline aircraft
                    pass
                else:
                    self._draw_text_smart(draw, f"TO: {destination}", (right_x, right_y), 
                                        self.fonts['data_small'], fill=(150, 150, 150), use_outline=False)
        else:
            # Large display layout with dynamic spacing
            y_offset = 4
            
            # Title (using PressStart2P for better readability)
            self._draw_text_with_outline(draw, title, (self.display_width // 2 - 30, y_offset), 
                                       self.fonts['title_large'], fill=title_color, outline_color=(0, 0, 0))
            y_offset += self._calculate_line_spacing(self.fonts['title_large']) + 4
            
            # Callsign (using 4x6 for compact data) - pixel perfect
            self._draw_text_smart(draw, f"Callsign: {aircraft['callsign']}", (4, y_offset), 
                                self.fonts['data_large'], fill=(255, 255, 255), use_outline=False)
            y_offset += self._calculate_line_spacing(self.fonts['data_large'])
            
            # Key statistic (using 4x6 for compact data) - pixel perfect
            if self.current_stat == 0:
                self._draw_text_smart(draw, f"Distance: {aircraft['distance_miles']:.2f} miles", (4, y_offset), 
                                    self.fonts['data_large'], fill=title_color, use_outline=False)
            elif self.current_stat == 1:
                self._draw_text_smart(draw, f"Speed: {int(aircraft['speed'])} knots", (4, y_offset), 
                                    self.fonts['data_large'], fill=title_color, use_outline=False)
            else:
                self._draw_text_smart(draw, f"Altitude: {int(aircraft['altitude'])} ft", (4, y_offset), 
                                    self.fonts['data_large'], fill=title_color, use_outline=False)
            y_offset += self._calculate_line_spacing(self.fonts['data_large']) + 2
            
            # Other stats (using 4x6 for compact data) - pixel perfect, only if there's space
            if y_offset + self._calculate_line_spacing(self.fonts['data_medium']) <= self.display_height:
                self._draw_text_smart(draw, f"Altitude: {int(aircraft['altitude'])} ft", (4, y_offset), 
                                    self.fonts['data_medium'], fill=aircraft['color'], use_outline=False)
                y_offset += self._calculate_line_spacing(self.fonts['data_medium'])
            
            if y_offset + self._calculate_line_spacing(self.fonts['data_medium']) <= self.display_height:
                self._draw_text_smart(draw, f"Speed: {int(aircraft['speed'])} knots", (4, y_offset), 
                                    self.fonts['data_medium'], fill=(200, 200, 200), use_outline=False)
                y_offset += self._calculate_line_spacing(self.fonts['data_medium'])
            
            if y_offset + self._calculate_line_spacing(self.fonts['data_medium']) <= self.display_height:
                self._draw_text_smart(draw, f"Distance: {aircraft['distance_miles']:.2f} miles", (4, y_offset), 
                                    self.fonts['data_medium'], fill=(200, 200, 200), use_outline=False)
                y_offset += self._calculate_line_spacing(self.fonts['data_medium'])
            
            if aircraft['heading'] and y_offset + self._calculate_line_spacing(self.fonts['data_medium']) <= self.display_height:
                self._draw_text_smart(draw, f"Heading: {int(aircraft['heading'])}°", (4, y_offset), 
                                    self.fonts['data_medium'], fill=(150, 150, 150), use_outline=False)
                y_offset += self._calculate_line_spacing(self.fonts['data_medium'])
            
            # Right side info - stacked vertically for large display
            right_x = self.display_width - 80  # Start 80 pixels from right edge
            right_y = 4
            
            # Aircraft type
            if right_y + self._calculate_line_spacing(self.fonts['data_medium']) <= self.display_height:
                self._draw_text_smart(draw, f"Type: {aircraft['aircraft_type']}", (right_x, right_y), 
                                    self.fonts['data_medium'], fill=(200, 200, 200), use_outline=False)
                right_y += self._calculate_line_spacing(self.fonts['data_medium'])
            
            # Origin (from flight plan data)
            if right_y + self._calculate_line_spacing(self.fonts['data_medium']) <= self.display_height:
                flight_plan = self._get_flight_plan_data(aircraft['callsign'])
                origin = flight_plan.get('origin', 'Unknown')
                aircraft_type = flight_plan.get('aircraft_type', 'Unknown')
                
                # Show appropriate information based on aircraft type
                if origin == 'Unknown' and aircraft_type in ['Military', 'Private', 'Other']:
                    self._draw_text_smart(draw, f"Category: {aircraft_type}", (right_x, right_y), 
                                        self.fonts['data_medium'], fill=(255, 200, 0), use_outline=False)
                else:
                    self._draw_text_smart(draw, f"From: {origin}", (right_x, right_y), 
                                        self.fonts['data_medium'], fill=(150, 150, 150), use_outline=False)
                right_y += self._calculate_line_spacing(self.fonts['data_medium'])
            
            # Destination (from flight plan data)
            if right_y + self._calculate_line_spacing(self.fonts['data_medium']) <= self.display_height:
                flight_plan = self._get_flight_plan_data(aircraft['callsign'])
                destination = flight_plan.get('destination', 'Unknown')
                aircraft_type = flight_plan.get('aircraft_type', 'Unknown')
                
                # Show appropriate information based on aircraft type
                if destination == 'Unknown' and aircraft_type in ['Military', 'Private', 'Other']:
                    # Skip destination for non-airline aircraft
                    pass
                else:
                    self._draw_text_smart(draw, f"To: {destination}", (right_x, right_y), 
                                        self.fonts['data_medium'], fill=(150, 150, 150), use_outline=False)
        
        # Display the image
        self.display_manager.image = img.copy()
        self.display_manager.update_display()

