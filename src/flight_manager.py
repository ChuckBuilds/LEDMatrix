import logging
import math
import time
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
        
        self.flight_plan_cache = {}  # Cache flight plan data
        
        # Location configuration
        self.center_lat = self.flight_config.get('center_latitude', 27.9506)
        self.center_lon = self.flight_config.get('center_longitude', -82.4572)
        self.map_radius_miles = self.flight_config.get('map_radius_miles', 10)  # Reduced from 50 to 10 miles for better visibility
        self.zoom_factor = self.flight_config.get('zoom_factor', 1.0)  # Zoom factor to use more of the display
        
        # Display configuration
        self.display_width = display_manager.matrix.width
        self.display_height = display_manager.matrix.height
        self.show_trails = self.flight_config.get('show_trails', False)
        self.trail_length = self.flight_config.get('trail_length', 10)
        
        # Altitude color configuration
        self.altitude_colors = self.flight_config.get('altitude_colors', {
            '0': [255, 165, 0],      # Orange
            '4000': [255, 255, 0],   # Yellow
            '8000': [0, 255, 0],     # Green
            '20000': [135, 206, 250], # Light Blue
            '30000': [0, 0, 139],    # Dark Blue
            '40000': [128, 0, 128]   # Purple
        })
        
        # Area outline configuration - can be custom coordinates or auto-generated
        self.area_outline = self.flight_config.get('area_outline', 'auto')  # 'auto', 'custom', 'tampa_bay', or 'none'
        self.custom_coastline = self.flight_config.get('custom_coastline', [])
        
        # Predefined coastline shapes for common areas
        self.predefined_coastlines = {
            'tampa_bay': [
                (28.0, -82.8),   # North of Tampa Bay
                (28.1, -82.7),   # Upper bay
                (28.0, -82.6),   # East side
                (27.9, -82.5),   # South Tampa
                (27.8, -82.4),   # Lower bay
                (27.7, -82.3),   # South side
                (27.8, -82.2),   # East side
                (27.9, -82.1),   # Upper east
                (28.0, -82.0),   # North east
                (28.1, -82.1),   # North
                (28.0, -82.2),   # Back to start
            ],
            'miami': [
                (25.9, -80.3),   # North Miami
                (25.8, -80.2),   # Upper bay
                (25.7, -80.1),   # East side
                (25.6, -80.0),   # South Miami
                (25.5, -79.9),   # Lower bay
                (25.4, -79.8),   # South side
                (25.5, -79.7),   # East side
                (25.6, -79.6),   # Upper east
                (25.7, -79.5),   # North east
                (25.8, -79.6),   # North
                (25.9, -79.7),   # Back to start
            ],
            'orlando': [
                (28.6, -81.4),   # North Orlando
                (28.5, -81.3),   # Upper area
                (28.4, -81.2),   # East side
                (28.3, -81.1),   # South Orlando
                (28.2, -81.0),   # Lower area
                (28.1, -80.9),   # South side
                (28.2, -80.8),   # East side
                (28.3, -80.7),   # Upper east
                (28.4, -80.6),   # North east
                (28.5, -80.7),   # North
                (28.6, -80.8),   # Back to start
            ],
            'jacksonville': [
                (30.4, -81.7),   # North Jacksonville
                (30.3, -81.6),   # Upper area
                (30.2, -81.5),   # East side
                (30.1, -81.4),   # South Jacksonville
                (30.0, -81.3),   # Lower area
                (29.9, -81.2),   # South side
                (30.0, -81.1),   # East side
                (30.1, -81.0),   # Upper east
                (30.2, -80.9),   # North east
                (30.3, -81.0),   # North
                (30.4, -81.1),   # Back to start
            ]
        }
        
        # Auto-generated area outline (lightweight rectangle)
        self.area_coords = self._generate_area_outline()
        
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
        self.coastline_pixels = None  # Cached pixel coordinates
        self.cached_display_size = (0, 0)  # Track when to recalculate coastline
        
        # Fonts
        self.fonts = self._load_fonts()
        
        logger.info(f"[Flight Tracker] Initialized with center: ({self.center_lat}, {self.center_lon}), radius: {self.map_radius_miles}mi")
        logger.info(f"[Flight Tracker] Display: {self.display_width}x{self.display_height}, SkyAware: {self.skyaware_url}")
        logger.info(f"[Flight Tracker] Area outline: {self.area_outline} mode with {len(self.area_coords)} points")
        logger.info(f"[Flight Tracker] Area coordinates: {self.area_coords}")
    
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
            return {'origin': 'Unknown', 'destination': 'Unknown'}
        
        # Check cache first
        if callsign in self.flight_plan_cache:
            return self.flight_plan_cache[callsign]
        
        try:
            # FlightAware AeroAPI integration
            url = f"https://aeroapi.flightaware.com/aeroapi/flights/{callsign}"
            headers = {"x-apikey": self.flightaware_api_key}
            
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                flight_plan = {
                    'origin': data.get('origin', {}).get('code', 'Unknown'),
                    'destination': data.get('destination', {}).get('code', 'Unknown')
                }
                # Cache for 1 hour
                self.flight_plan_cache[callsign] = flight_plan
                return flight_plan
            else:
                logger.debug(f"[Flight Tracker] No flight plan data for {callsign}")
                return {'origin': 'Unknown', 'destination': 'Unknown'}
                
        except Exception as e:
            logger.debug(f"[Flight Tracker] Failed to fetch flight plan for {callsign}: {e}")
            return {'origin': 'Unknown', 'destination': 'Unknown'}
    
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
        
        logger.warning(f"[Flight Tracker] Coordinate ({lat}, {lon}) -> pixel ({x}, {y}) is outside display bounds {self.display_width}x{self.display_height}")
        return None
    
    def _generate_area_outline(self) -> List[Tuple[float, float]]:
        """Generate lightweight area outline based on center point and radius."""
        if self.area_outline == 'custom' and self.custom_coastline:
            logger.info("[Flight Tracker] Using custom coastline coordinates")
            return self.custom_coastline
        elif self.area_outline in self.predefined_coastlines:
            logger.info(f"[Flight Tracker] Using predefined coastline: {self.area_outline}")
            return self.predefined_coastlines[self.area_outline]
        elif self.area_outline == 'none':
            return []
        else:
            # Auto-generate a simple rectangular outline around the center point
            # Calculate lat/lon bounds based on radius
            lat_range = self.map_radius_miles / 69.0  # Approximate miles per degree latitude
            lon_range = lat_range / math.cos(math.radians(self.center_lat))  # Adjust for longitude convergence
            
            # Create a simple rectangle outline (4 corners + close the loop)
            coords = [
                (self.center_lat + lat_range, self.center_lon - lon_range),  # Top-left
                (self.center_lat + lat_range, self.center_lon + lon_range),  # Top-right
                (self.center_lat - lat_range, self.center_lon + lon_range),  # Bottom-right
                (self.center_lat - lat_range, self.center_lon - lon_range),  # Bottom-left
                (self.center_lat + lat_range, self.center_lon - lon_range),  # Close the loop
            ]
            logger.debug(f"[Flight Tracker] Generated area outline coordinates: {coords}")
            return coords
    
    def _get_area_outline_pixels(self) -> List[Tuple[int, int]]:
        """Get area outline as pixel coordinates, with caching."""
        current_size = (self.display_width, self.display_height)
        
        # Check if we need to recalculate
        if self.coastline_pixels is None or self.cached_display_size != current_size:
            self.coastline_pixels = []
            for lat, lon in self.area_coords:
                pixel = self._latlon_to_pixel(lat, lon)
                if pixel:
                    self.coastline_pixels.append(pixel)
                    logger.debug(f"[Flight Tracker] Converted ({lat}, {lon}) to pixel {pixel}")
                else:
                    # If coordinate is outside bounds, clamp it to display edges
                    x = int((lon - self.center_lon) * (self.display_width / (self.map_radius_miles * 2 / 69.0 / math.cos(math.radians(self.center_lat)))) + self.display_width / 2)
                    y = int((self.center_lat - lat) * (self.display_height / (self.map_radius_miles * 2 / 69.0)) + self.display_height / 2)
                    
                    # Clamp to display bounds
                    x = max(0, min(self.display_width - 1, x))
                    y = max(0, min(self.display_height - 1, y))
                    self.coastline_pixels.append((x, y))
                    logger.debug(f"[Flight Tracker] Clamped ({lat}, {lon}) to pixel ({x}, {y})")
            
            self.cached_display_size = current_size
            logger.debug(f"[Flight Tracker] Cached {len(self.coastline_pixels)} area outline pixels: {self.coastline_pixels}")
        
        return self.coastline_pixels
    
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
        logger.info(f"[Flight Tracker] Area outline mode: {self.area_outline}")
        logger.info(f"[Flight Tracker] Generated {len(self.area_coords)} area coordinates")
    
    def display(self, force_clear: bool = False) -> None:
        """Display the flight map with aircraft and optional coastline."""
        if force_clear:
            self.display_manager.clear()
        
        # Create image
        img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Area outline drawing removed for cleaner display
        
        # Draw center position marker (white dot at our lat/lon)
        center_pixel = self._latlon_to_pixel(self.center_lat, self.center_lon)
        if center_pixel:
            x, y = center_pixel
            # Draw a small white dot at our center position
            draw.point((x, y), fill=(255, 255, 255))
            # Draw a small cross for better visibility
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
                # Small display: single pixel
                draw.point((x, y), fill=color)
            else:
                # Large display: small arrow showing heading
                heading = aircraft['heading']
                if heading:
                    # Draw 3-pixel arrow
                    # Calculate arrow points
                    angle_rad = math.radians(heading)
                    dx = int(2 * math.sin(angle_rad))
                    dy = int(-2 * math.cos(angle_rad))
                    
                    # Draw arrow head
                    draw.point((x + dx, y + dy), fill=color)
                    draw.point((x, y), fill=color)
                    
                    # Draw small wings
                    wing_angle = math.radians(heading + 135)
                    wx1 = int(math.sin(wing_angle))
                    wy1 = int(-math.cos(wing_angle))
                    draw.point((x + wx1, y + wy1), fill=color)
                    
                    wing_angle = math.radians(heading - 135)
                    wx2 = int(math.sin(wing_angle))
                    wy2 = int(-math.cos(wing_angle))
                    draw.point((x + wx2, y + wy2), fill=color)
                else:
                    # No heading data, draw single pixel
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
                self._draw_text_smart(draw, f"FROM: {origin}", (right_x, right_y), 
                                    self.fonts['data_small'], fill=(150, 150, 150), use_outline=False)
                right_y += self._calculate_line_spacing(self.fonts['data_small'])
            
            # Destination (from flight plan data)
            if right_y + self._calculate_line_spacing(self.fonts['data_small']) <= self.display_height:
                flight_plan = self._get_flight_plan_data(aircraft['callsign'])
                destination = flight_plan.get('destination', 'Unknown')
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
                self._draw_text_smart(draw, f"From: {origin}", (right_x, right_y), 
                                    self.fonts['data_medium'], fill=(150, 150, 150), use_outline=False)
                right_y += self._calculate_line_spacing(self.fonts['data_medium'])
            
            # Destination (from flight plan data)
            if right_y + self._calculate_line_spacing(self.fonts['data_medium']) <= self.display_height:
                flight_plan = self._get_flight_plan_data(aircraft['callsign'])
                destination = flight_plan.get('destination', 'Unknown')
                self._draw_text_smart(draw, f"To: {destination}", (right_x, right_y), 
                                    self.fonts['data_medium'], fill=(150, 150, 150), use_outline=False)
        
        # Display the image
        self.display_manager.image = img.copy()
        self.display_manager.update_display()

