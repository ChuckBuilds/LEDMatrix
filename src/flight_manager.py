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
        
        # Location configuration
        self.center_lat = self.flight_config.get('center_latitude', 27.9506)
        self.center_lon = self.flight_config.get('center_longitude', -82.4572)
        self.map_radius_miles = self.flight_config.get('map_radius_miles', 10)
        
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
        self.area_outline = self.flight_config.get('area_outline', 'auto')  # 'auto', 'custom', or 'none'
        self.custom_coastline = self.flight_config.get('custom_coastline', [])
        
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
    
    def _load_fonts(self) -> Dict[str, Any]:
        """Load fonts for text rendering with better readability."""
        fonts = {}
        try:
            # Use PressStart2P font for better readability, similar to other managers
            if self.display_height >= 64:
                fonts['small'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 6)
                fonts['medium'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 8)
                fonts['large'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 10)
            else:
                fonts['small'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 5)
                fonts['medium'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 6)
                fonts['large'] = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 7)
            logger.info("[Flight Tracker] Successfully loaded PressStart2P fonts for better readability")
        except Exception as e:
            logger.warning(f"[Flight Tracker] Failed to load PressStart2P fonts: {e}, trying 4x6 fallback")
            try:
                # Fallback to 4x6 font if PressStart2P fails
                if self.display_height >= 64:
                    fonts['small'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 6)
                    fonts['medium'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 8)
                    fonts['large'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 10)
                else:
                    fonts['small'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 5)
                    fonts['medium'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 6)
                    fonts['large'] = ImageFont.truetype('assets/fonts/4x6-font.ttf', 7)
                logger.info("[Flight Tracker] Using 4x6 font fallback")
            except Exception as e2:
                logger.warning(f"[Flight Tracker] All custom fonts failed: {e2}, using default")
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
        # Use smaller dimension for aspect ratio considerations
        min_dimension = min(self.display_width, self.display_height)
        
        # Degrees of latitude/longitude to cover
        # 1 degree of latitude ≈ 69 miles, 1 degree of longitude varies by latitude
        lat_degrees = (self.map_radius_miles * 2) / 69.0
        
        # Adjust longitude for latitude (longitude lines converge at poles)
        lon_degrees = lat_degrees / math.cos(math.radians(self.center_lat))
        
        # Calculate pixel scale
        lat_scale = self.display_height / lat_degrees
        lon_scale = self.display_width / lon_degrees
        
        # Convert to pixel coordinates (center of display is center_lat, center_lon)
        x = int((lon - self.center_lon) * lon_scale + self.display_width / 2)
        y = int((self.center_lat - lat) * lat_scale + self.display_height / 2)  # Flip Y axis
        
        # Check if within display bounds
        if 0 <= x < self.display_width and 0 <= y < self.display_height:
            return (x, y)
        
        return None
    
    def _generate_area_outline(self) -> List[Tuple[float, float]]:
        """Generate lightweight area outline based on center point and radius."""
        if self.area_outline == 'custom' and self.custom_coastline:
            return self.custom_coastline
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
    
    def display(self, force_clear: bool = False) -> None:
        """Display the flight map with aircraft and optional coastline."""
        if force_clear:
            self.display_manager.clear()
        
        # Create image
        img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Draw area outline
        outline_pixels = self._get_area_outline_pixels()
        logger.debug(f"[Flight Tracker] Drawing area outline with {len(outline_pixels)} pixels")
        if len(outline_pixels) >= 2:
            # Draw lines connecting outline points
            for i in range(len(outline_pixels)):
                p1 = outline_pixels[i]
                p2 = outline_pixels[(i + 1) % len(outline_pixels)]
                logger.debug(f"[Flight Tracker] Drawing outline line from {p1} to {p2}")
                draw.line([p1, p2], fill=(255, 255, 255), width=2)  # Make it bright white and visible
        
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
        
        # Draw info text with outline for better readability
        if len(self.aircraft_data) > 0:
            info_text = f"{len(self.aircraft_data)} aircraft"
            self._draw_text_with_outline(draw, info_text, (2, 2), self.fonts['small'], 
                                       fill=(200, 200, 200), outline_color=(0, 0, 0))
        
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
            # Small display layout (128x32)
            y_offset = 2
            line_height = 6
            
            # Line 1: Callsign
            self._draw_text_with_outline(draw, f"{closest['callsign']}", (2, y_offset), 
                                       self.fonts['medium'], fill=(255, 255, 255), outline_color=(0, 0, 0))
            y_offset += line_height
            
            # Line 2: Altitude and Speed
            self._draw_text_with_outline(draw, f"ALT:{int(closest['altitude'])}ft", (2, y_offset), 
                                       self.fonts['small'], fill=closest['color'], outline_color=(0, 0, 0))
            self._draw_text_with_outline(draw, f"SPD:{int(closest['speed'])}kt", (self.display_width // 2, y_offset), 
                                       self.fonts['small'], fill=(200, 200, 200), outline_color=(0, 0, 0))
            y_offset += line_height
            
            # Line 3: Distance and Heading
            self._draw_text_with_outline(draw, f"DIST:{closest['distance_miles']:.2f}mi", (2, y_offset), 
                                       self.fonts['small'], fill=(200, 200, 200), outline_color=(0, 0, 0))
            if closest['heading']:
                self._draw_text_with_outline(draw, f"HDG:{int(closest['heading'])}°", (self.display_width // 2, y_offset), 
                                           self.fonts['small'], fill=(200, 200, 200), outline_color=(0, 0, 0))
            y_offset += line_height
            
            # Line 4: Type
            self._draw_text_with_outline(draw, f"TYPE:{closest['aircraft_type']}", (2, y_offset), 
                                       self.fonts['small'], fill=(150, 150, 150), outline_color=(0, 0, 0))
        else:
            # Large display layout (192x96 or bigger)
            y_offset = 4
            line_height = 10
            
            # Title
            self._draw_text_with_outline(draw, "OVERHEAD AIRCRAFT", (self.display_width // 2 - 40, y_offset), 
                                       self.fonts['large'], fill=(255, 200, 0), outline_color=(0, 0, 0))
            y_offset += line_height + 4
            
            # Callsign (large)
            self._draw_text_with_outline(draw, f"Callsign: {closest['callsign']}", (4, y_offset), 
                                       self.fonts['large'], fill=(255, 255, 255), outline_color=(0, 0, 0))
            y_offset += line_height
            
            # Altitude (with color)
            self._draw_text_with_outline(draw, f"Altitude: {int(closest['altitude'])} ft", (4, y_offset), 
                                       self.fonts['medium'], fill=closest['color'], outline_color=(0, 0, 0))
            y_offset += line_height
            
            # Speed
            self._draw_text_with_outline(draw, f"Speed: {int(closest['speed'])} knots", (4, y_offset), 
                                       self.fonts['medium'], fill=(200, 200, 200), outline_color=(0, 0, 0))
            y_offset += line_height
            
            # Distance
            self._draw_text_with_outline(draw, f"Distance: {closest['distance_miles']:.2f} miles", (4, y_offset), 
                                       self.fonts['medium'], fill=(255, 150, 0), outline_color=(0, 0, 0))
            y_offset += line_height
            
            # Heading
            if closest['heading']:
                self._draw_text_with_outline(draw, f"Heading: {int(closest['heading'])}°", (4, y_offset), 
                                           self.fonts['medium'], fill=(200, 200, 200), outline_color=(0, 0, 0))
                y_offset += line_height
            
            # Aircraft type
            self._draw_text_with_outline(draw, f"Type: {closest['aircraft_type']}", (4, y_offset), 
                                       self.fonts['medium'], fill=(150, 150, 150), outline_color=(0, 0, 0))
        
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
            # Small display layout
            y_offset = 1
            line_height = 6
            
            # Title
            self._draw_text_with_outline(draw, title, (2, y_offset), 
                                       self.fonts['medium'], fill=title_color, outline_color=(0, 0, 0))
            y_offset += line_height
            
            # Callsign
            self._draw_text_with_outline(draw, aircraft['callsign'], (2, y_offset), 
                                       self.fonts['small'], fill=(255, 255, 255), outline_color=(0, 0, 0))
            y_offset += line_height
            
            # Key stat
            if self.current_stat == 0:
                stat_text = f"{aircraft['distance_miles']:.2f}mi"
            elif self.current_stat == 1:
                stat_text = f"{int(aircraft['speed'])}kt"
            else:
                stat_text = f"{int(aircraft['altitude'])}ft"
            
            self._draw_text_with_outline(draw, stat_text, (2, y_offset), 
                                       self.fonts['medium'], fill=aircraft['color'], outline_color=(0, 0, 0))
            y_offset += line_height + 1
            
            # Additional info
            self._draw_text_with_outline(draw, f"ALT:{int(aircraft['altitude'])} SPD:{int(aircraft['speed'])}", (2, y_offset), 
                                       self.fonts['small'], fill=(150, 150, 150), outline_color=(0, 0, 0))
        else:
            # Large display layout
            y_offset = 4
            line_height = 10
            
            # Title
            self._draw_text_with_outline(draw, title, (self.display_width // 2 - 30, y_offset), 
                                       self.fonts['large'], fill=title_color, outline_color=(0, 0, 0))
            y_offset += line_height + 4
            
            # Callsign
            self._draw_text_with_outline(draw, f"Callsign: {aircraft['callsign']}", (4, y_offset), 
                                       self.fonts['large'], fill=(255, 255, 255), outline_color=(0, 0, 0))
            y_offset += line_height
            
            # Key statistic (large)
            if self.current_stat == 0:
                self._draw_text_with_outline(draw, f"Distance: {aircraft['distance_miles']:.2f} miles", (4, y_offset), 
                                           self.fonts['large'], fill=title_color, outline_color=(0, 0, 0))
            elif self.current_stat == 1:
                self._draw_text_with_outline(draw, f"Speed: {int(aircraft['speed'])} knots", (4, y_offset), 
                                           self.fonts['large'], fill=title_color, outline_color=(0, 0, 0))
            else:
                self._draw_text_with_outline(draw, f"Altitude: {int(aircraft['altitude'])} ft", (4, y_offset), 
                                           self.fonts['large'], fill=title_color, outline_color=(0, 0, 0))
            y_offset += line_height + 2
            
            # Other stats
            self._draw_text_with_outline(draw, f"Altitude: {int(aircraft['altitude'])} ft", (4, y_offset), 
                                       self.fonts['medium'], fill=aircraft['color'], outline_color=(0, 0, 0))
            y_offset += line_height - 2
            
            self._draw_text_with_outline(draw, f"Speed: {int(aircraft['speed'])} knots", (4, y_offset), 
                                       self.fonts['medium'], fill=(200, 200, 200), outline_color=(0, 0, 0))
            y_offset += line_height - 2
            
            self._draw_text_with_outline(draw, f"Distance: {aircraft['distance_miles']:.2f} miles", (4, y_offset), 
                                       self.fonts['medium'], fill=(200, 200, 200), outline_color=(0, 0, 0))
            y_offset += line_height - 2
            
            if aircraft['heading']:
                self._draw_text_with_outline(draw, f"Heading: {int(aircraft['heading'])}°", (4, y_offset), 
                                           self.fonts['medium'], fill=(150, 150, 150), outline_color=(0, 0, 0))
        
        # Display the image
        self.display_manager.image = img.copy()
        self.display_manager.update_display()

