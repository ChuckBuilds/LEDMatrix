import logging
import math
import time
import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

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
        
        # Get API key from flight_tracker config (secrets are merged by ConfigManager)
        self.flightaware_api_key = self.flight_config.get('flightaware_api_key', '')
        
        # Rate limiting and cost control for FlightAware API
        self.api_call_timestamps = []  # Track API call timestamps for rate limiting
        self.max_api_calls_per_hour = self.flight_config.get('max_api_calls_per_hour', 20)  # Reduced from 50 to 20 for cost control
        self.cache_ttl_seconds = self.flight_config.get('flight_plan_cache_ttl_hours', 12) * 3600  # 12 hours for fresher data
        self.min_callsign_length = self.flight_config.get('min_callsign_length', 4)  # Increased from 3 to 4 to filter more
        self.daily_api_budget = self.flight_config.get('daily_api_budget', 60)  # Max 60 calls per day (1800/month)
        self.api_calls_today = 0
        self.last_reset_date = None
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
        # Cache tiles for 1 year by default - map tiles don't change frequently
        self.cache_ttl_hours = self.map_bg_config.get('cache_ttl_hours', 8760)
        self.fade_intensity = self.map_bg_config.get('fade_intensity', 0.3)
        self.map_brightness = self.map_bg_config.get('brightness', 1.0)
        self.map_contrast = self.map_bg_config.get('contrast', 1.0)
        self.map_saturation = self.map_bg_config.get('saturation', 1.0)
        self.disable_on_cache_error = self.map_bg_config.get('disable_on_cache_error', False)
        
        # Custom tile server URL (for self-hosted OSM servers)
        self.custom_tile_server = self.map_bg_config.get('custom_tile_server', None)
        
        # Log tile server configuration
        if self.custom_tile_server:
            logger.info(f"[Flight Tracker] Configured to use custom tile server: {self.custom_tile_server}")
        else:
            logger.info(f"[Flight Tracker] Configured to use tile provider: {self.tile_provider}")
        
        # Log map appearance settings
        logger.info(f"[Flight Tracker] Map appearance - Brightness: {self.map_brightness}, Contrast: {self.map_contrast}, Saturation: {self.map_saturation}, Fade: {self.fade_intensity}")
        
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
        self.cached_pixels_per_mile = None  # Actual scale of the cached map
        
        # Display configuration
        self.display_width = display_manager.matrix.width
        self.display_height = display_manager.matrix.height
        self.show_trails = self.flight_config.get('show_trails', False)
        self.trail_length = self.flight_config.get('trail_length', 10)
        
        # Logging rate limiting for bounds warnings
        self.bounds_warning_cache = {}
        self.bounds_warning_interval = 30  # Only log each unique coordinate once every 30 seconds
        
        # Altitude color configuration - matches the gradient from the image
        # This uses the standard aviation altitude color scale
        self.altitude_colors = {
            '0': [255, 100, 0],       # Deep orange-red (ground level)
            '500': [255, 120, 0],     # Slightly lighter orange-red
            '1000': [255, 140, 0],    # Distinct orange
            '2000': [255, 200, 0],    # Bright orange-yellow
            '4000': [255, 255, 0],    # Clear yellow
            '6000': [200, 255, 0],    # Yellowish-green
            '8000': [0, 255, 0],      # Vibrant green
            '10000': [0, 200, 150],   # Bright teal (bluish-green)
            '20000': [0, 150, 255],   # Clear bright blue
            '30000': [0, 0, 200],     # Deep royal blue
            '40000': [150, 0, 200],   # Vibrant purple
            '45000': [200, 0, 150]    # Distinct magenta/purple
        }
        
        
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
        
        # Cost monitoring
        self.monthly_api_calls = 0
        self.cost_per_call = 0.005  # $0.005 per call based on your data
        self.monthly_budget = 10.0  # $10 monthly budget
        self.budget_warning_threshold = 0.8  # Warn at 80% of budget
        
        # Background service for flight plan data
        self.background_service_enabled = self.flight_config.get('background_service', {}).get('enabled', True)
        self.background_fetch_interval = self.flight_config.get('background_service', {}).get('fetch_interval_hours', 4) * 3600  # More frequent fetching
        self.last_background_fetch = 0
        self.pending_flight_plans = set()  # Callsigns to fetch in background
        self.max_background_calls_per_run = self.flight_config.get('background_service', {}).get('max_calls_per_run', 10)  # More calls per background run
        
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
        
        callsign_upper = callsign.upper()
        
        # Priority 1: Major US airlines (most likely to have interesting flight plans)
        major_us_airlines = ['AAL', 'UAL', 'DAL', 'SWA', 'JBU', 'B6', 'WN', 'AA', 'UA', 'DL', 'ASQ', 'ENY', 'FFT', 'NKS', 'F9', 'G4']
        for prefix in major_us_airlines:
            if callsign_upper.startswith(prefix):
                return True
        
        # Priority 2: International airlines (expanded for better coverage)
        international_airlines = ['BAW', 'AFR', 'LUF', 'KLM', 'SAS', 'IBE', 'EZY', 'RYR', 'WZZ', 'EIN', 'DLH', 'AUA', 'SWR', 'AZA', 'IBB', 'VLG', 'TAP', 'KLM', 'AFR', 'LUF']
        for prefix in international_airlines:
            if callsign_upper.startswith(prefix):
                return True
        
        # Priority 3: Cargo airlines (often have interesting routes)
        cargo_airlines = ['UPS', 'FDX', 'GTI', 'ABX', 'CPZ', 'DHL', 'TNT', 'QFA', 'SIA', 'CAL']
        for prefix in cargo_airlines:
            if callsign_upper.startswith(prefix):
                return True
        
        # Priority 4: Regional airlines (for more comprehensive coverage)
        regional_airlines = ['ENY', 'ASQ', 'FFT', 'NKS', 'JBU', 'G4', 'B6', 'F9', 'NK', 'G4']
        for prefix in regional_airlines:
            if callsign_upper.startswith(prefix):
                return True
        
        # Priority 5: International aircraft with country prefixes (interesting routes)
        if callsign_upper.startswith(('G-', 'F-', 'D-', 'I-', 'HB-', 'OE-', 'PH-', 'SE-', 'LN-', 'OY-', 'VH-', 'C-G', 'C-F', 'JA-', 'B-', 'HL-', '9V-', 'A6-', 'VT-', 'PK-', 'HS-', 'RP-', 'ZS-', '4X-', 'SU-', 'RA-', 'UR-', 'EW-', 'S7-', 'U6-', 'FV-', 'DP-')):
            return True
        
        # Skip military and private aircraft to focus on commercial traffic
        if callsign_upper.startswith(('N', 'C-', 'CF-', 'AF-', 'NATO-', 'USAF-', 'USN-', 'USMC-', 'USCG-')):
            return False
        
        return False
    
    def _categorize_aircraft(self, callsign: str) -> str:
        """Categorize aircraft based on callsign patterns."""
        if not callsign:
            return "Unknown"
        
        callsign_upper = callsign.upper()
        
        # Check for military patterns
        if callsign_upper.startswith(('C-', 'CF-', 'AF-', 'NATO-', 'USAF-', 'USN-', 'USMC-', 'USCG-', 'RAZOR', 'VADER', 'SPIRIT')):
            return "Military"
        
        # Check for cargo airlines (often have interesting routes)
        cargo_prefixes = ['UPS', 'FDX', 'GTI', 'ABX', 'CPZ', 'DHL', 'TNT', 'QFA', 'SIA', 'CAL', 'CARGO']
        for prefix in cargo_prefixes:
            if callsign_upper.startswith(prefix):
                return "Cargo"
        
        # Check for known major airline callsigns
        major_airlines = ['AAL', 'UAL', 'DAL', 'SWA', 'JBU', 'B6', 'WN', 'AA', 'UA', 'DL']
        for prefix in major_airlines:
            if callsign_upper.startswith(prefix):
                return "Airline"
        
        # Check for other airline callsigns
        for prefix in self.airline_callsign_prefixes:
            if callsign_upper.startswith(prefix):
                return "Airline"
        
        # Check for international aircraft with country prefixes
        if callsign_upper.startswith(('G-', 'F-', 'D-', 'I-', 'HB-', 'OE-', 'PH-', 'SE-', 'LN-', 'OY-', 'VH-', 'C-G', 'C-F', 'JA-', 'B-', 'HL-', '9V-', 'A6-', 'VT-', 'PK-', 'HS-', 'RP-', 'ZS-', '4X-', 'SU-', 'RA-', 'UR-', 'EW-', 'S7-', 'U6-', 'FV-', 'DP-', 'P4-', 'P5-', 'P6-', 'P7-', 'P8-', 'P9-', 'P0-', 'P1-', 'P2-', 'P3-')):
            return "International"
        
        # Check for private aircraft (N-prefix with numbers/letters)
        if callsign_upper.startswith('N') and len(callsign) >= 4:
            # Check if it looks like a commercial aircraft (longer callsigns)
            if len(callsign) >= 6:
                return "Commercial"
            else:
                return "Private"
        
        # Check for general aviation patterns
        if callsign_upper.startswith(('N', 'C-', 'CF-')) and len(callsign) >= 4:
            return "General Aviation"
        
        # Additional pattern matching for common aircraft types
        if len(callsign) >= 4:
            # Check for common commercial flight patterns
            if any(pattern in callsign_upper for pattern in ['1', '2', '3', '4', '5', '6', '7', '8', '9']):
                if len(callsign) >= 6:
                    return "Commercial"
                else:
                    return "General Aviation"
            
            # Check for common airline patterns
            if callsign_upper.startswith(('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z')):
                if len(callsign) >= 5:
                    return "Commercial"
                else:
                    return "General Aviation"
        
        # Default categorization
        if len(callsign) <= 3:
            return "Unknown"
        else:
            return "General Aviation"
    
    def _check_rate_limit(self) -> bool:
        """Check if we're within API rate limits and daily budget."""
        current_time = time.time()
        current_date = datetime.now().date()
        
        # Reset daily counter if new day
        if self.last_reset_date != current_date:
            self.api_calls_today = 0
            self.last_reset_date = current_date
            logger.info(f"[Flight Tracker] Daily API budget reset: {self.daily_api_budget} calls available")
        
        # Check daily budget first (more restrictive)
        if self.api_calls_today >= self.daily_api_budget:
            logger.warning(f"[Flight Tracker] Daily API budget reached: {self.api_calls_today}/{self.daily_api_budget} calls today")
            return False
        
        # Check hourly rate limit
        hour_ago = current_time - 3600  # 1 hour ago
        self.api_call_timestamps = [ts for ts in self.api_call_timestamps if ts > hour_ago]
        
        if len(self.api_call_timestamps) >= self.max_api_calls_per_hour:
            logger.warning(f"[Flight Tracker] Hourly rate limit reached: {len(self.api_call_timestamps)}/{self.max_api_calls_per_hour} calls in the last hour")
            return False
        
        return True
    
    def _record_api_call(self):
        """Record an API call for rate limiting and cost monitoring."""
        current_time = time.time()
        self.api_call_timestamps.append(current_time)
        self.api_calls_today += 1
        self.monthly_api_calls += 1
        
        # Calculate current costs
        current_cost = self.monthly_api_calls * self.cost_per_call
        budget_usage = current_cost / self.monthly_budget
        
        # Log cost information
        logger.info(f"[Flight Tracker] API call recorded. Today: {self.api_calls_today}/{self.daily_api_budget}, "
                   f"Monthly: {self.monthly_api_calls} calls (${current_cost:.2f}), "
                   f"Budget usage: {budget_usage:.1%}")
        
        # Budget warning
        if budget_usage >= self.budget_warning_threshold:
            logger.warning(f"[Flight Tracker] BUDGET WARNING: {budget_usage:.1%} of monthly budget used "
                          f"(${current_cost:.2f}/${self.monthly_budget:.2f})")
        
        # Smart budget management - reduce daily budget as month progresses
        days_in_month = 30
        current_day = datetime.now().day
        if current_day > 15:  # After mid-month, be more conservative
            self.daily_api_budget = min(self.daily_api_budget, 40)  # Reduce to 40 calls/day
            logger.info(f"[Flight Tracker] Mid-month budget adjustment: {self.daily_api_budget} calls/day")
        
        # Emergency stop at 95% budget
        if budget_usage >= 0.95:
            logger.error(f"[Flight Tracker] EMERGENCY STOP: 95% of budget reached. Disabling API calls.")
            self.daily_api_budget = 0  # Effectively disable further calls
    
    
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
        # Always provide aircraft type categorization as fallback
        aircraft_category = self._categorize_aircraft(callsign)
        
        if not self.flight_plan_enabled:
            logger.info(f"[Flight Tracker] Flight plan disabled for {callsign} (flight_plan_enabled=False)")
            return {'origin': 'Unknown', 'destination': 'Unknown', 'aircraft_type': aircraft_category}
        
        if not self.flightaware_api_key:
            logger.info(f"[Flight Tracker] No API key configured for {callsign}")
            return {'origin': 'Unknown', 'destination': 'Unknown', 'aircraft_type': aircraft_category}
        
        # Check if callsign is worth fetching (cost control)
        if not self._is_callsign_worth_fetching(callsign):
            logger.info(f"[Flight Tracker] Skipping flight plan fetch for {callsign} (not worth fetching - category: {aircraft_category})")
            return {'origin': 'Unknown', 'destination': 'Unknown', 'aircraft_type': aircraft_category}
        
        # Check rate limiting
        if not self._check_rate_limit():
            logger.warning(f"[Flight Tracker] Rate limit reached, skipping API call for {callsign}")
            return {'origin': 'Unknown', 'destination': 'Unknown', 'aircraft_type': aircraft_category}
        
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
                    
                    # Try multiple field names for aircraft type
                    aircraft_type = (
                        flight.get('aircraft_type') or 
                        flight.get('aircraft', {}).get('type') or
                        flight.get('type') or
                        'Unknown'
                    )
                    
                    flight_plan = {
                        'origin': flight.get('origin', {}).get('code', 'Unknown'),
                        'destination': flight.get('destination', {}).get('code', 'Unknown'),
                        'aircraft_type': aircraft_type
                    }
                    
                    # Log the full flight data for debugging (first time only)
                    logger.debug(f"[Flight Tracker] API response keys for {callsign}: {list(flight.keys())}")
                else:
                    # Fallback for single flight response format
                    aircraft_type = (
                        data.get('aircraft_type') or 
                        data.get('aircraft', {}).get('type') or
                        data.get('type') or
                        'Unknown'
                    )
                    
                    flight_plan = {
                        'origin': data.get('origin', {}).get('code', 'Unknown'),
                        'destination': data.get('destination', {}).get('code', 'Unknown'),
                        'aircraft_type': aircraft_type
                    }
                
                # Cache using the cache manager
                self.cache_manager.set(cache_key, flight_plan)
                self._record_api_call()
                logger.info(f"[Flight Tracker] Successfully fetched and cached flight plan for {callsign}: {flight_plan['origin']} -> {flight_plan['destination']} ({flight_plan['aircraft_type']})")
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
            logger.warning("[Flight Tracker] No aircraft data in response")
            return
        
        total_aircraft = len(data['aircraft'])
        logger.info(f"[Flight Tracker] Processing {total_aircraft} aircraft from SkyAware")
        
        current_time = time.time()
        active_icao = set()
        aircraft_with_position = 0
        aircraft_in_range = 0
        
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
            
            aircraft_with_position += 1
            
            # Calculate distance from center
            distance_miles = self._calculate_distance(lat, lon, self.center_lat, self.center_lon)
            
            # Filter by radius
            if distance_miles > self.map_radius_miles:
                continue
            
            aircraft_in_range += 1
            
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
        
        logger.info(f"[Flight Tracker] Summary - Total: {total_aircraft}, With position: {aircraft_with_position}, In range ({self.map_radius_miles}mi): {aircraft_in_range}, Tracking: {len(self.aircraft_data)}, Removed stale: {len(stale_icao)}")
    
    def _altitude_to_color(self, altitude: float) -> Tuple[int, int, int]:
        """Convert altitude to color using smooth gradient interpolation matching the altitude scale."""
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
                # Smooth linear interpolation between colors
                ratio = (altitude - alt1) / (alt2 - alt1)
                
                # Interpolate each RGB component
                r = int(color1[0] + (color2[0] - color1[0]) * ratio)
                g = int(color1[1] + (color2[1] - color1[1]) * ratio)
                b = int(color1[2] + (color2[2] - color1[2]) * ratio)
                
                # Ensure values are within valid RGB range
                r = max(0, min(255, r))
                g = max(0, min(255, g))
                b = max(0, min(255, b))
                
                return (r, g, b)
        
        # Fallback (shouldn't reach here)
        logger.warning(f"[Flight Tracker] Could not find color for altitude {altitude}")
        return (255, 255, 255)
    
    def _calculate_zoom_level(self) -> int:
        """Calculate the appropriate zoom level for tile detail.
        
        This determines tile detail level, NOT the geographic area shown.
        The area is controlled by map_radius_miles independently.
        """
        effective_radius = self.map_radius_miles / self.zoom_factor
        
        # Choose zoom level based on desired detail, not scale
        # We'll scale the tiles to fit the desired radius afterward
        if effective_radius <= 5:
            return 12  # High detail for very local areas
        elif effective_radius <= 25:
            return 11  # Good detail for city/regional areas
        elif effective_radius <= 100:
            return 10  # Regional detail
        elif effective_radius <= 300:
            return 9   # State-level detail
        elif effective_radius <= 600:
            return 8   # Multi-state detail
        elif effective_radius <= 1200:
            return 7   # Country-level detail
        else:
            return 6   # Continental detail
    
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
        # Calculate pixels per mile based on the DESIRED display radius
        # This ensures we show exactly map_radius_miles * 2 across the display
        
        effective_radius = self.map_radius_miles / self.zoom_factor
        
        # The display shows (effective_radius * 2) miles across
        # Calculate pixels per mile to fit this area
        pixels_per_mile = self.display_width / (effective_radius * 2)
        
        # Calculate distance in miles from center to aircraft
        distance_miles = self._calculate_distance(self.center_lat, self.center_lon, lat, lon)
        
        # Calculate bearing from center to aircraft (in radians)
        lat1_rad = math.radians(self.center_lat)
        lat2_rad = math.radians(lat)
        delta_lon_rad = math.radians(lon - self.center_lon)
        
        x = math.sin(delta_lon_rad) * math.cos(lat2_rad)
        y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon_rad)
        bearing_rad = math.atan2(x, y)
        
        # Convert distance and bearing to pixel offset from center
        pixel_distance = distance_miles * pixels_per_mile
        offset_x = pixel_distance * math.sin(bearing_rad)
        offset_y = -pixel_distance * math.cos(bearing_rad)  # Negative because screen Y increases downward
        
        # Map to display coordinates (center is at display_width/2, display_height/2)
        x_pixel = int(self.display_width / 2 + offset_x)
        y_pixel = int(self.display_height / 2 + offset_y)
        
        # Debug logging
        logger.debug(f"[Flight Tracker] Converting ({lat:.6f}, {lon:.6f}) to pixel ({x_pixel}, {y_pixel})")
        logger.debug(f"[Flight Tracker] Distance: {distance_miles:.2f}mi, Bearing: {math.degrees(bearing_rad):.1f}°, Pixels/mile: {pixels_per_mile:.2f}, Radius: {effective_radius:.1f}mi")
        
        # Check if within display bounds
        if 0 <= x_pixel < self.display_width and 0 <= y_pixel < self.display_height:
            return (x_pixel, y_pixel)
        
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
    
    def _get_tile_urls(self, x: int, y: int, zoom: int) -> List[str]:
        """Get multiple tile URLs to try in order of preference."""
        # If custom tile server is configured, use it for all requests
        if self.custom_tile_server:
            # Remove trailing slash if present
            base_url = self.custom_tile_server.rstrip('/')
            return [f"{base_url}/tile/{zoom}/{x}/{y}.png"]
        
        if self.tile_provider == 'osm':
            # Use multiple OSM mirrors to avoid blocking
            return [
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png",
                f"https://a.tile.openstreetmap.org/{zoom}/{x}/{y}.png",
                f"https://b.tile.openstreetmap.org/{zoom}/{x}/{y}.png",
                f"https://c.tile.openstreetmap.org/{zoom}/{x}/{y}.png"
            ]
        elif self.tile_provider == 'carto':
            return [
                f"https://cartodb-basemaps-a.global.ssl.fastly.net/light_all/{zoom}/{x}/{y}.png",
                f"https://cartodb-basemaps-b.global.ssl.fastly.net/light_all/{zoom}/{x}/{y}.png",
                f"https://cartodb-basemaps-c.global.ssl.fastly.net/light_all/{zoom}/{x}/{y}.png",
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"  # Fallback to OSM
            ]
        elif self.tile_provider == 'carto_dark':
            return [
                f"https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{zoom}/{x}/{y}.png",
                f"https://cartodb-basemaps-b.global.ssl.fastly.net/dark_all/{zoom}/{x}/{y}.png",
                f"https://cartodb-basemaps-c.global.ssl.fastly.net/dark_all/{zoom}/{x}/{y}.png",
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"  # Fallback to OSM
            ]
        elif self.tile_provider == 'stamen':
            return [
                f"https://stamen-tiles.a.ssl.fastly.net/terrain/{zoom}/{x}/{y}.png",
                f"https://stamen-tiles.b.ssl.fastly.net/terrain/{zoom}/{x}/{y}.png",
                f"https://stamen-tiles-c.a.ssl.fastly.net/terrain/{zoom}/{x}/{y}.png",
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"  # Fallback to OSM
            ]
        elif self.tile_provider == 'esri':
            return [
                f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{zoom}/{y}/{x}",
                f"https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{zoom}/{y}/{x}",
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"  # Fallback to OSM
            ]
        else:
            # Default to OSM with multiple mirrors
            return [
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png",
                f"https://a.tile.openstreetmap.org/{zoom}/{x}/{y}.png",
                f"https://b.tile.openstreetmap.org/{zoom}/{x}/{y}.png",
                f"https://c.tile.openstreetmap.org/{zoom}/{x}/{y}.png"
            ]
    
    def _get_tile_url(self, x: int, y: int, zoom: int) -> str:
        """Get the URL for a map tile based on provider (backward compatibility)."""
        urls = self._get_tile_urls(x, y, zoom)
        return urls[0]  # Return first URL for backward compatibility
    
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
        from PIL import Image as PILImage
        
        cache_path = self._get_tile_cache_path(x, y, zoom)
        
        # Try to load from cache first
        if self._is_tile_cached(x, y, zoom):
            try:
                return PILImage.open(cache_path)
            except Exception as e:
                logger.warning(f"[Flight Tracker] Failed to load cached tile {x},{y},{zoom}: {e}")
        
        # Fetch from server - try multiple URLs
        urls = self._get_tile_urls(x, y, zoom)
        
        for i, url in enumerate(urls):
            try:
                logger.info(f"[Flight Tracker] Fetching tile {x},{y} at zoom {zoom} from: {url}")
                
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                
                # Check if we got an error page instead of a tile
                content_type = response.headers.get('content-type', '').lower()
                if 'text/html' in content_type or 'text/plain' in content_type:
                    logger.debug(f"[Flight Tracker] Got HTML/text response from {url}")
                    continue  # Try next URL
                
                # Check if response is too small (likely an error page)
                if len(response.content) < 2000:  # Tiles are usually much larger
                    logger.debug(f"[Flight Tracker] Tile response too small ({len(response.content)} bytes) from {url}")
                    # Try to read the error message
                    try:
                        error_text = response.content.decode('utf-8', errors='ignore')[:200]
                        logger.debug(f"[Flight Tracker] Error content: {error_text}")
                    except:
                        pass
                    continue  # Try next URL
                
                # Additional validation: try to load as image and check for text artifacts
                try:
                    import io
                    test_img = PILImage.open(io.BytesIO(response.content))
                    
                    # Check if image is too small (likely an error page rendered as image)
                    if test_img.size[0] < 100 or test_img.size[1] < 100:
                        logger.debug(f"[Flight Tracker] Tile image too small: {test_img.size}")
                        continue
                    
                    # Check for suspiciously uniform colors (error pages often have solid colors)
                    if test_img.mode == 'RGB':
                        # Convert to grayscale for analysis
                        gray_img = test_img.convert('L')
                        # Get pixel data
                        pixels = list(gray_img.getdata())
                        if len(pixels) > 0:
                            # Check if image is mostly one color (suspicious for error pages)
                            color_counts = {}
                            for pixel in pixels[::100]:  # Sample every 100th pixel for performance
                                color_counts[pixel] = color_counts.get(pixel, 0) + 1
                            
                            # If more than 80% of pixels are the same color, it's likely an error page
                            max_count = max(color_counts.values())
                            if max_count > len(pixels[::100]) * 0.8:
                                logger.debug(f"[Flight Tracker] Tile appears to be solid color (error page)")
                                continue
                    
                except Exception as e:
                    logger.debug(f"[Flight Tracker] Could not validate tile image: {e}")
                    # Continue anyway if we can't validate
                
                # If we get here, we have a valid tile
                logger.debug(f"[Flight Tracker] ✓ Successfully fetched tile from URL {i+1}: {url}")
                logger.debug(f"[Flight Tracker]   Tile size: {len(response.content)} bytes, Content-Type: {response.headers.get('content-type', 'unknown')}")
                
                # Save to cache
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(cache_path, 'wb') as f:
                        f.write(response.content)
                    logger.debug(f"[Flight Tracker] Cached tile {x},{y},{zoom}")
                    # Reset cache error count on successful cache
                    if self.cache_error_count > 0:
                        self.cache_error_count = 0
                    return PILImage.open(cache_path)
                except (PermissionError, OSError) as e:
                    logger.warning(f"[Flight Tracker] Could not save tile to cache {cache_path}: {e}")
                    # Track cache error
                    self.cache_error_count += 1
                    # Continue without caching - create a temporary file
                    import tempfile
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                    temp_file.write(response.content)
                    temp_file.close()
                    return PILImage.open(temp_file.name)
                
            except Exception as e:
                logger.warning(f"[Flight Tracker] Failed to fetch tile from {url}: {e}")
                if i == len(urls) - 1:  # Last URL failed
                    return None
                continue  # Try next URL
        
        # If we get here, all URLs failed
        return None
    
    def _get_map_background(self, center_lat: float, center_lon: float) -> Optional[Image.Image]:
        """Get the map background for the current view."""
        if not self.map_bg_enabled:
            return None
        
        # Check if we should disable due to too many cache errors
        if self.disable_on_cache_error and self.cache_error_count >= self.max_cache_errors:
            logger.warning(f"[Flight Tracker] Disabling map background due to {self.cache_error_count} cache errors")
            return None
        
        # Calculate appropriate zoom level based on map radius and zoom factor
        zoom = self._calculate_zoom_level()
        effective_radius = self.map_radius_miles / self.zoom_factor
        
        logger.debug(f"[Flight Tracker] Map zoom calculation: radius={self.map_radius_miles}mi, zoom_factor={self.zoom_factor}, effective_radius={effective_radius:.2f}mi, zoom={zoom}")
        
        # Check if we can reuse the cached composite map
        current_center = (round(center_lat, 4), round(center_lon, 4))
        if (self.cached_map_bg is not None and 
            self.last_map_center == current_center and 
            self.last_map_zoom == zoom):
            # Location and zoom haven't changed, reuse cached composite
            return self.cached_map_bg
        
        # Calculate tile coordinates for center
        center_x, center_y = self._latlon_to_tile_coords(center_lat, center_lon, zoom)
        
        # Calculate how many tiles we need to cover the display
        # Each tile covers a certain lat/lon area, adjusted by zoom_factor
        lat_degrees = (effective_radius * 2) / 69.0
        lon_degrees = lat_degrees / math.cos(math.radians(center_lat))
        
        # Calculate tile coverage - optimize for reasonable number of tiles
        tiles_per_degree = 2 ** zoom
        
        # Calculate base tile coverage
        base_tiles_x = max(1, int(lon_degrees * tiles_per_degree / 360.0 * 2))
        base_tiles_y = max(1, int(lat_degrees * tiles_per_degree / 360.0 * 2))
        
        # Limit maximum tiles to prevent excessive fetching
        max_tiles = 16  # 4x4 maximum
        tiles_x = min(max_tiles, max(2, base_tiles_x + 2))  # Add 2 for buffer
        tiles_y = min(max_tiles, max(2, base_tiles_y + 2))  # Add 2 for buffer
        
        logger.info(f"[Flight Tracker] Tile calculation: base=({base_tiles_x}x{base_tiles_y}), final=({tiles_x}x{tiles_y}), total={tiles_x * tiles_y}")
        
        # Log tile server being used
        if self.custom_tile_server:
            logger.info(f"[Flight Tracker] Using custom tile server: {self.custom_tile_server}")
        else:
            logger.info(f"[Flight Tracker] Using tile provider: {self.tile_provider}")
        
        # Calculate tile bounds
        start_x = center_x - tiles_x // 2
        start_y = center_y - tiles_y // 2
        
        # Create composite image
        composite_width = tiles_x * self.tile_size
        composite_height = tiles_y * self.tile_size
        composite = Image.new('RGB', (composite_width, composite_height), (0, 0, 0))
        
        # Fetch and composite tiles
        tiles_fetched = 0
        failed_tiles = []
        
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                tile_x = start_x + tx
                tile_y = start_y + ty
                
                # Fetch tile (reduced logging for performance)
                tile_img = self._fetch_tile(tile_x, tile_y, zoom)
                if tile_img:
                    # Ensure tile is in RGB mode for proper compositing
                    if tile_img.mode != 'RGB':
                        tile_img = tile_img.convert('RGB')
                    
                    # Paste tile into composite
                    paste_x = tx * self.tile_size
                    paste_y = ty * self.tile_size
                    composite.paste(tile_img, (paste_x, paste_y))
                    tiles_fetched += 1
                    logger.debug(f"[Flight Tracker] ✓ Placed tile {tile_x},{tile_y} at ({paste_x},{paste_y})")
                else:
                    failed_tiles.append((tile_x, tile_y))
                    logger.warning(f"[Flight Tracker] ✗ Failed to fetch tile {tile_x},{tile_y}")
        
        if tiles_fetched == 0:
            logger.warning("[Flight Tracker] No map tiles could be fetched")
            return None
        
        # Log summary of failed tiles
        if failed_tiles:
            logger.warning(f"[Flight Tracker] Failed to fetch {len(failed_tiles)} tiles: {failed_tiles}")
            # If more than 50% of tiles failed, disable map background
            failure_rate = len(failed_tiles) / (tiles_x * tiles_y)
            if failure_rate > 0.5:
                logger.warning(f"[Flight Tracker] High tile failure rate ({failure_rate:.1%}), disabling map background")
                self.map_bg_enabled = False
                return None
        else:
            logger.info(f"[Flight Tracker] All tiles fetched successfully")
        
        # Calculate what geographic area the tiles natively show at this zoom level
        world_pixels_at_zoom = self.tile_size * (2 ** zoom)
        pixels_per_degree_lon_native = world_pixels_at_zoom / 360.0
        # Adjust for latitude (longitude degrees get smaller as you move away from equator)
        meters_per_degree_lat = 111000  # approximately 111km or 69 miles per degree
        miles_per_degree_lat = 69.0
        miles_per_degree_lon = miles_per_degree_lat * math.cos(math.radians(center_lat))
        
        pixels_per_mile_at_zoom = pixels_per_degree_lon_native / miles_per_degree_lon
        
        # Calculate what we WANT to show (effective_radius * 2 miles wide)
        desired_miles_wide = effective_radius * 2
        desired_pixels_per_mile = self.display_width / desired_miles_wide
        
        # Calculate how many pixels we need to crop from the composite to get the desired geographic area
        # maintaining the display aspect ratio to avoid stretching
        crop_width_needed = int(desired_miles_wide * pixels_per_mile_at_zoom)
        # Calculate height based on display aspect ratio to avoid stretching when we resize
        crop_height_needed = int(crop_width_needed * (self.display_height / self.display_width))
        
        # Find the center tile and position within it
        center_tile_x = center_x - start_x
        center_tile_y = center_y - start_y
        
        # Calculate position within the center tile
        center_lon_in_tile = (center_lon - self._tile_to_lon(start_x + center_tile_x, zoom)) / (self._tile_to_lon(start_x + center_tile_x + 1, zoom) - self._tile_to_lon(start_x + center_tile_x, zoom))
        center_lat_in_tile = (self._tile_to_lat(start_y + center_tile_y, zoom) - center_lat) / (self._tile_to_lat(start_y + center_tile_y, zoom) - self._tile_to_lat(start_y + center_tile_y + 1, zoom))
        
        # Calculate pixel position in composite
        center_pixel_x = int((center_tile_x + center_lon_in_tile) * self.tile_size)
        center_pixel_y = int((center_tile_y + center_lat_in_tile) * self.tile_size)
        
        # Calculate crop bounds centered on the center point, using the geographic-aware crop size
        crop_left = max(0, center_pixel_x - crop_width_needed // 2)
        crop_top = max(0, center_pixel_y - crop_height_needed // 2)
        crop_right = min(composite_width, crop_left + crop_width_needed)
        crop_bottom = min(composite_height, crop_top + crop_height_needed)
        
        # Adjust if we hit the edges
        if crop_right - crop_left < crop_width_needed:
            crop_width_needed = crop_right - crop_left
        if crop_bottom - crop_top < crop_height_needed:
            crop_height_needed = crop_bottom - crop_top
        
        # Crop to get the desired geographic area
        cropped = composite.crop((crop_left, crop_top, crop_right, crop_bottom))
        logger.debug(f"[Flight Tracker] Cropped size: {cropped.size} (wanted {crop_width_needed}x{crop_height_needed} pixels for {desired_miles_wide:.1f} miles)")
        
        # Now resize to display dimensions - this scales the geographic area to fit the display
        logger.debug(f"[Flight Tracker] Resizing from {cropped.size} to ({self.display_width}, {self.display_height})")
        cropped = cropped.resize((self.display_width, self.display_height), Image.Resampling.LANCZOS)
        
        # Apply fade effect
        if self.fade_intensity < 1.0:
            # Create a fade overlay
            fade_overlay = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            cropped = Image.blend(cropped, fade_overlay, 1.0 - self.fade_intensity)
        
        # Apply brightness adjustment
        if self.map_brightness != 1.0:
            enhancer = ImageEnhance.Brightness(cropped)
            cropped = enhancer.enhance(self.map_brightness)
            logger.debug(f"[Flight Tracker] Applied brightness: {self.map_brightness}")
        
        # Apply contrast adjustment
        if self.map_contrast != 1.0:
            enhancer = ImageEnhance.Contrast(cropped)
            cropped = enhancer.enhance(self.map_contrast)
            logger.debug(f"[Flight Tracker] Applied contrast: {self.map_contrast}")
        
        # Apply saturation adjustment
        if self.map_saturation != 1.0:
            enhancer = ImageEnhance.Color(cropped)
            cropped = enhancer.enhance(self.map_saturation)
            logger.debug(f"[Flight Tracker] Applied saturation: {self.map_saturation}")
        
        # Cache the result
        self.cached_map_bg = cropped
        self.last_map_center = current_center
        self.last_map_zoom = zoom
        
        # Calculate the geographic height coverage
        desired_miles_high = crop_height_needed / pixels_per_mile_at_zoom
        
        # Log the final map configuration
        logger.info(f"[Flight Tracker] Generated map background with {tiles_fetched} tiles at zoom {zoom}")
        logger.info(f"[Flight Tracker] Center: ({center_lat:.4f}, {center_lon:.4f}), Radius: {self.map_radius_miles}mi, Effective: {effective_radius:.2f}mi (zoom_factor: {self.zoom_factor})")
        logger.info(f"[Flight Tracker] Tile coverage: {tiles_x}x{tiles_y}, Crop: ({crop_left},{crop_top})-({crop_right},{crop_bottom})")
        logger.info(f"[Flight Tracker] Map displays {desired_miles_wide:.1f} miles wide x {desired_miles_high:.1f} miles high (no stretching)")
        logger.info(f"[Flight Tracker] Native tile scale: {pixels_per_mile_at_zoom:.3f} pixels/mile, cropped {crop_width_needed}x{crop_height_needed} pixels, scaled to {self.display_width}x{self.display_height}")
        
        # Debug: Save composite image to see what's happening
        try:
            debug_composite = Path("debug_composite.png")
            composite.save(debug_composite)
            logger.debug(f"[Flight Tracker] Saved composite to: {debug_composite}")
            
            debug_cropped = Path("debug_cropped.png")
            cropped.save(debug_cropped)
            logger.debug(f"[Flight Tracker] Saved cropped to: {debug_cropped}")
        except Exception as e:
            logger.debug(f"[Flight Tracker] Could not save debug images: {e}")
        
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
            logger.info(f"[Flight Tracker] Fetching aircraft data from {self.skyaware_url}")
            
            data = self._fetch_aircraft_data()
            if data:
                logger.info(f"[Flight Tracker] Received data, processing aircraft...")
                self._process_aircraft_data(data)
                logger.info(f"[Flight Tracker] Currently tracking {len(self.aircraft_data)} aircraft")
                # Queue interesting callsigns for background fetching
                self._queue_interesting_callsigns()
            else:
                logger.warning("[Flight Tracker] No data received from SkyAware")
            
            self.last_update = current_time
        
        # Background service for flight plan data
        if self.background_service_enabled and current_time - self.last_background_fetch >= self.background_fetch_interval:
            logger.info("[Flight Tracker] Running background service for flight plans")
            self._background_fetch_flight_plans()
            self.last_background_fetch = current_time
    
    def _queue_interesting_callsigns(self):
        """Queue callsigns that are worth fetching flight plan data for, with priority."""
        # Sort aircraft by distance (closer = higher priority)
        sorted_aircraft = sorted(self.aircraft_data.values(), key=lambda a: a['distance_miles'])
        
        for aircraft in sorted_aircraft:
            callsign = aircraft['callsign']
            if self._is_callsign_worth_fetching(callsign):
                # Check if we already have cached data
                cache_key = f"flight_plan_{callsign}"
                if not self.cache_manager.get(cache_key, max_age=self.cache_ttl_seconds):
                    # Add priority based on distance and aircraft type
                    priority = 1 if aircraft['distance_miles'] < 5 else 2  # Closer aircraft get priority
                    self.pending_flight_plans.add((priority, callsign))
    
    def _background_fetch_flight_plans(self):
        """Fetch flight plan data in background to avoid blocking display."""
        if not self.pending_flight_plans:
            return
        
        logger.info(f"[Flight Tracker] Background fetching {len(self.pending_flight_plans)} flight plans")
        
        # Sort by priority (lower number = higher priority)
        sorted_plans = sorted(self.pending_flight_plans, key=lambda x: x[0])
        
        # Process a limited number of callsigns per background run
        max_per_run = min(self.max_background_calls_per_run, len(sorted_plans))
        plans_to_process = sorted_plans[:max_per_run]
        
        for priority, callsign in plans_to_process:
            if self._check_rate_limit():
                # Fetch flight plan data
                flight_plan = self._get_flight_plan_data(callsign)
                if flight_plan and flight_plan.get('origin') != 'Unknown':
                    logger.info(f"[Flight Tracker] Background fetched (priority {priority}): {callsign} -> {flight_plan['origin']}-{flight_plan['destination']}")
                
                self.pending_flight_plans.remove((priority, callsign))
            else:
                logger.warning(f"[Flight Tracker] Rate limit reached, deferring {len(self.pending_flight_plans)} callsigns")
                break
    
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
    
    def _draw_airplane_icon(self, draw: ImageDraw.Draw, x: int, y: int, color: Tuple[int, int, int] = (200, 200, 200)) -> None:
        """Draw a simple airplane icon at the specified position with black outline.
        
        Args:
            draw: ImageDraw object to draw on
            x: X coordinate for the icon's top-left corner
            y: Y coordinate for the icon's top-left corner
            color: RGB color tuple for the icon
        """
        # Simple 5x5 pixel airplane icon
        # Format: (relative_x, relative_y)
        airplane_pixels = [
            (2, 0),  # Nose
            (2, 1),  # Body
            (0, 2), (1, 2), (2, 2), (3, 2), (4, 2),  # Wings
            (2, 3),  # Body
            (1, 4), (2, 4), (3, 4),  # Tail
        ]
        
        airplane_set = set(airplane_pixels)
        
        # Draw black outline (all pixels adjacent to airplane pixels)
        outline_pixels = set()
        for px, py in airplane_pixels:
            # Check all 8 surrounding pixels
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx == 0 and dy == 0:
                        continue  # Skip the center pixel
                    neighbor = (px + dx, py + dy)
                    if neighbor not in airplane_set:
                        outline_pixels.add(neighbor)
        
        # Draw outline first
        for px, py in outline_pixels:
            draw.point((x + px, y + py), fill=(0, 0, 0))
        
        # Draw airplane on top
        for px, py in airplane_pixels:
            draw.point((x + px, y + py), fill=color)
    
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
            # Draw white center dot
            draw.point((x, y), fill=(255, 255, 255))
        
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
        for aircraft in self.aircraft_data.values():
            pixel = self._latlon_to_pixel(aircraft['lat'], aircraft['lon'])
            if not pixel:
                continue
            
            x, y = pixel
            # Brighten the plane colors by boosting RGB values
            base_color = aircraft['color']
            color = tuple(min(255, int(c * 1.3)) for c in base_color)
            
            # Draw single pixel for each aircraft
            draw.point((x, y), fill=color)
        
        # Draw info text with pixel-perfect rendering for better readability
        if len(self.aircraft_data) > 0:
            # Draw aircraft count
            info_text = f"{len(self.aircraft_data)}"
            self._draw_text_smart(draw, info_text, (2, 2), self.fonts['small'], 
                                fill=(200, 200, 200), use_outline=False)
            
            # Get text width to position the airplane icon
            bbox = draw.textbbox((0, 0), info_text, font=self.fonts['small'])
            text_width = bbox[2] - bbox[0]
            
            # Draw airplane icon after the count (with 2px spacing)
            self._draw_airplane_icon(draw, 2 + text_width + 2, 2, color=(200, 200, 200))
        
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
        
        # Get flight plan data once and cache it for this display cycle
        flight_plan = self._get_flight_plan_data(aircraft['callsign'])
        origin = flight_plan.get('origin', 'Unknown')
        destination = flight_plan.get('destination', 'Unknown')
        aircraft_type = flight_plan.get('aircraft_type', 'Unknown')
        
        # Improve aircraft type display with better categorization
        if aircraft_type == 'Unknown':
            aircraft_type = self._categorize_aircraft(aircraft['callsign'])
            # Log uncategorized aircraft for debugging
            if aircraft_type == 'Unknown':
                logger.debug(f"[Flight Tracker] Unclassified aircraft: {aircraft['callsign']}")
        
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
                # Show appropriate information based on aircraft type
                if origin == 'Unknown':
                    if aircraft_type in ['Military', 'Private', 'General Aviation', 'Other']:
                        self._draw_text_smart(draw, f"TYPE: {aircraft_type}", (right_x, right_y), 
                                            self.fonts['data_small'], fill=(255, 200, 0), use_outline=False)
                    elif aircraft_type in ['Airline', 'Cargo', 'Commercial', 'International']:
                        self._draw_text_smart(draw, f"TYPE: {aircraft_type}", (right_x, right_y), 
                                            self.fonts['data_small'], fill=(255, 200, 0), use_outline=False)
                    else:
                        self._draw_text_smart(draw, f"TYPE: {aircraft_type}", (right_x, right_y), 
                                            self.fonts['data_small'], fill=(255, 200, 0), use_outline=False)
                else:
                    self._draw_text_smart(draw, f"FROM: {origin}", (right_x, right_y), 
                                        self.fonts['data_small'], fill=(150, 150, 150), use_outline=False)
                right_y += self._calculate_line_spacing(self.fonts['data_small'])
            
            # Destination (from flight plan data)
            if right_y + self._calculate_line_spacing(self.fonts['data_small']) <= self.display_height:
                # Show appropriate information based on aircraft type
                if destination == 'Unknown':
                    if aircraft_type in ['Military', 'Private', 'General Aviation', 'Other']:
                        # Skip destination for non-airline aircraft
                        pass
                    else:
                        # Show aircraft type for commercial aircraft without destination
                        self._draw_text_smart(draw, f"TYPE: {aircraft_type}", (right_x, right_y), 
                                            self.fonts['data_small'], fill=(255, 200, 0), use_outline=False)
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
                # Show appropriate information based on aircraft type
                if origin == 'Unknown':
                    if aircraft_type in ['Military', 'Private', 'General Aviation', 'Other']:
                        self._draw_text_smart(draw, f"Category: {aircraft_type}", (right_x, right_y), 
                                            self.fonts['data_medium'], fill=(255, 200, 0), use_outline=False)
                    elif aircraft_type in ['Airline', 'Cargo', 'Commercial', 'International']:
                        self._draw_text_smart(draw, f"Category: {aircraft_type}", (right_x, right_y), 
                                            self.fonts['data_medium'], fill=(255, 200, 0), use_outline=False)
                    else:
                        self._draw_text_smart(draw, f"Category: {aircraft_type}", (right_x, right_y), 
                                            self.fonts['data_medium'], fill=(255, 200, 0), use_outline=False)
                else:
                    self._draw_text_smart(draw, f"From: {origin}", (right_x, right_y), 
                                        self.fonts['data_medium'], fill=(150, 150, 150), use_outline=False)
                right_y += self._calculate_line_spacing(self.fonts['data_medium'])
            
            # Destination (from flight plan data)
            if right_y + self._calculate_line_spacing(self.fonts['data_medium']) <= self.display_height:
                # Show appropriate information based on aircraft type
                if destination == 'Unknown':
                    if aircraft_type in ['Military', 'Private', 'General Aviation', 'Other']:
                        # Skip destination for non-airline aircraft
                        pass
                    else:
                        # Show aircraft type for commercial aircraft without destination
                        self._draw_text_smart(draw, f"Category: {aircraft_type}", (right_x, right_y), 
                                            self.fonts['data_medium'], fill=(255, 200, 0), use_outline=False)
                else:
                    self._draw_text_smart(draw, f"To: {destination}", (right_x, right_y), 
                                        self.fonts['data_medium'], fill=(150, 150, 150), use_outline=False)
        
        # Display the image
        self.display_manager.image = img.copy()
        self.display_manager.update_display()

