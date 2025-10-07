#!/usr/bin/env python3
"""
Windows Development Viewer for Flight Tracker
Displays geographic tiles with ADS-B data overlaid for development and testing.
"""

import json
import math
import time
import requests
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageDraw, ImageTk, ImageFont
from pathlib import Path
import threading
import logging
from typing import Dict, List, Optional, Tuple, Any
import tempfile
import os

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class FlightTrackerDevViewer:
    """Windows development viewer for flight tracker with map tiles and ADS-B data."""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Flight Tracker Development Viewer")
        self.root.geometry("1200x800")
        
        # Configuration
        self.config = self._load_config()
        
        # Map configuration
        self.center_lat = self.config.get('center_latitude', 27.9506)
        self.center_lon = self.config.get('center_longitude', -82.4572)
        self.map_radius_miles = self.config.get('map_radius_miles', 10)
        self.zoom_factor = self.config.get('zoom_factor', 1.0)
        
        # Map background configuration
        self.map_bg_config = self.config.get('map_background', {})
        self.tile_provider = self.map_bg_config.get('tile_provider', 'carto')  # Use most reliable provider
        self.tile_size = self.map_bg_config.get('tile_size', 256)
        self.cache_ttl_hours = self.map_bg_config.get('cache_ttl_hours', 24)
        self.fade_intensity = self.map_bg_config.get('fade_intensity', 0.7)
        
        # Custom tile server URL (for self-hosted OSM servers)
        self.custom_tile_server = self.map_bg_config.get('custom_tile_server', None)
        
        # Display configuration
        self.display_width = 800
        self.display_height = 600
        
        # ADS-B data
        self.skyaware_url = self.config.get('skyaware_url', 'http://192.168.86.30/skyaware/data/aircraft.json')
        self.aircraft_data = {}
        self.last_update = 0
        self.update_interval = 30  # Update every 30 seconds instead of 5
        
        # Altitude colors
        self.altitude_colors = self.config.get('altitude_colors', {
            '0': [255, 165, 0],      # Orange
            '4000': [255, 255, 0],   # Yellow
            '8000': [0, 255, 0],     # Green
            '20000': [135, 206, 250], # Light Blue
            '30000': [0, 0, 139],    # Dark Blue
            '40000': [128, 0, 128]   # Purple
        })
        
        # Tile cache
        self.tile_cache_dir = Path(tempfile.gettempdir()) / 'flight_tracker_tiles'
        self.tile_cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Rate limiting for tile requests
        self.tile_request_times = []
        self.max_tile_requests_per_minute = 20  # Conservative limit to avoid blocking
        self.tile_request_delay = 1.0  # Longer delay to be more respectful
        
        # Cached map background
        self.cached_map_bg = None
        self.last_map_center = None
        self.last_map_zoom = None
        
        # GUI elements
        self.setup_gui()
        
        # Start update thread
        self.running = True
        self.update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self.update_thread.start()
        
        logger.info("Flight Tracker Development Viewer initialized")
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from config files."""
        try:
            # Try to load from config.json
            config_path = Path("config/config.json")
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    flight_config = config.get('flight_tracker', {})
                    logger.info("Loaded configuration from config.json")
                    return flight_config
            
            # Fallback to template config
            template_path = Path("config/config.template.json")
            if template_path.exists():
                with open(template_path, 'r') as f:
                    config = json.load(f)
                    flight_config = config.get('flight_tracker', {})
                    logger.info("Loaded configuration from config.template.json")
                    return flight_config
            
            # Default configuration
            logger.warning("No config file found, using defaults")
            return {
                'center_latitude': 27.9506,
                'center_longitude': -82.4572,
                'map_radius_miles': 10,
                'zoom_factor': 1.0,
                'skyaware_url': 'http://192.168.86.30/skyaware/data/aircraft.json',
                'map_background': {
                    'tile_provider': 'osm',
                    'tile_size': 256,
                    'cache_ttl_hours': 24,
                    'fade_intensity': 0.7
                },
                'altitude_colors': {
                    '0': [255, 165, 0],
                    '4000': [255, 255, 0],
                    '8000': [0, 255, 0],
                    '20000': [135, 206, 250],
                    '30000': [0, 0, 139],
                    '40000': [128, 0, 128]
                }
            }
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            return {}
    
    def setup_gui(self):
        """Setup the GUI elements."""
        # Main frame
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Control frame
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Location controls
        ttk.Label(control_frame, text="Center Lat:").grid(row=0, column=0, padx=(0, 5))
        self.lat_var = tk.StringVar(value=str(self.center_lat))
        ttk.Entry(control_frame, textvariable=self.lat_var, width=10).grid(row=0, column=1, padx=(0, 10))
        
        ttk.Label(control_frame, text="Center Lon:").grid(row=0, column=2, padx=(0, 5))
        self.lon_var = tk.StringVar(value=str(self.center_lon))
        ttk.Entry(control_frame, textvariable=self.lon_var, width=10).grid(row=0, column=3, padx=(0, 10))
        
        ttk.Label(control_frame, text="Radius (mi):").grid(row=0, column=4, padx=(0, 5))
        self.radius_var = tk.StringVar(value=str(self.map_radius_miles))
        ttk.Entry(control_frame, textvariable=self.radius_var, width=8).grid(row=0, column=5, padx=(0, 10))
        
        # Tile provider selection
        ttk.Label(control_frame, text="Provider:").grid(row=0, column=6, padx=(10, 5))
        self.provider_var = tk.StringVar(value=self.tile_provider)
        provider_combo = ttk.Combobox(control_frame, textvariable=self.provider_var, width=12, state="readonly")
        provider_combo['values'] = ('osm', 'carto', 'carto_dark', 'stamen', 'esri')
        provider_combo.grid(row=0, column=7, padx=(0, 10))
        
        # Update button
        ttk.Button(control_frame, text="Update", command=self.update_location).grid(row=0, column=8, padx=(10, 0))
        
        # Status frame
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.status_var = tk.StringVar(value="Initializing...")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side=tk.LEFT)
        
        # Aircraft count
        self.aircraft_count_var = tk.StringVar(value="Aircraft: 0")
        ttk.Label(status_frame, textvariable=self.aircraft_count_var).pack(side=tk.RIGHT)
        
        # Map display
        self.map_canvas = tk.Canvas(main_frame, width=self.display_width, height=self.display_height, bg='black')
        self.map_canvas.pack(fill=tk.BOTH, expand=True)
        
        # Bind resize event
        self.map_canvas.bind('<Configure>', self._on_canvas_resize)
        
        # Initial display
        self._update_display()
    
    def _on_canvas_resize(self, event):
        """Handle canvas resize."""
        self.display_width = event.width
        self.display_height = event.height
        self._update_display()
    
    def update_location(self):
        """Update the center location and refresh display."""
        try:
            self.center_lat = float(self.lat_var.get())
            self.center_lon = float(self.lon_var.get())
            self.map_radius_miles = float(self.radius_var.get())
            
            # Update tile provider if changed
            new_provider = self.provider_var.get()
            if new_provider != self.tile_provider:
                self.tile_provider = new_provider
                logger.info(f"Switched to tile provider: {self.tile_provider}")
            
            # Clear cached map
            self.cached_map_bg = None
            self.last_map_center = None
            
            self._update_display()
            self.status_var.set(f"Location updated: ({self.center_lat:.4f}, {self.center_lon:.4f}) | Provider: {self.tile_provider}")
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Please enter valid numbers: {e}")
    
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
            return [f"{base_url}/{zoom}/{x}/{y}.png"]
        
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
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
            ]
        elif self.tile_provider == 'carto_dark':
            return [
                f"https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{zoom}/{x}/{y}.png",
                f"https://cartodb-basemaps-b.global.ssl.fastly.net/dark_all/{zoom}/{x}/{y}.png",
                f"https://cartodb-basemaps-c.global.ssl.fastly.net/dark_all/{zoom}/{x}/{y}.png",
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
            ]
        elif self.tile_provider == 'stamen':
            return [
                f"https://stamen-tiles.a.ssl.fastly.net/terrain/{zoom}/{x}/{y}.png",
                f"https://stamen-tiles.b.ssl.fastly.net/terrain/{zoom}/{x}/{y}.png",
                f"https://stamen-tiles-c.a.ssl.fastly.net/terrain/{zoom}/{x}/{y}.png",
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
            ]
        elif self.tile_provider == 'esri':
            return [
                f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{zoom}/{y}/{x}",
                f"https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{zoom}/{y}/{x}",
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
            ]
        elif self.tile_provider == 'google':
            # Google Maps requires API key, so fallback to OSM
            return [
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
            ]
        else:
            # Default to OSM with multiple mirrors
            return [
                f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png",
                f"https://a.tile.openstreetmap.org/{zoom}/{x}/{y}.png",
                f"https://b.tile.openstreetmap.org/{zoom}/{x}/{y}.png",
                f"https://c.tile.openstreetmap.org/{zoom}/{x}/{y}.png"
            ]
    
    def _get_tile_cache_path(self, x: int, y: int, zoom: int) -> Path:
        """Get the cache file path for a tile."""
        return self.tile_cache_dir / f"{self.tile_provider}_{zoom}_{x}_{y}.png"
    
    def _is_tile_cached(self, x: int, y: int, zoom: int) -> bool:
        """Check if a tile is cached and not expired."""
        cache_path = self._get_tile_cache_path(x, y, zoom)
        if not cache_path.exists():
            return False
        
        # Use longer cache time for development - tiles don't change often
        tile_age = time.time() - cache_path.stat().st_mtime
        cache_ttl_seconds = self.cache_ttl_hours * 3600 * 7  # 7 days for development
        return tile_age < cache_ttl_seconds
    
    def _check_tile_rate_limit(self) -> bool:
        """Check if we're within tile request rate limits."""
        current_time = time.time()
        
        # Remove requests older than 1 minute
        self.tile_request_times = [t for t in self.tile_request_times if current_time - t < 60]
        
        # Check if we're under the limit
        if len(self.tile_request_times) >= self.max_tile_requests_per_minute:
            logger.warning(f"Tile rate limit reached: {len(self.tile_request_times)}/{self.max_tile_requests_per_minute} requests in the last minute")
            return False
        
        return True
    
    def _record_tile_request(self):
        """Record a tile request for rate limiting."""
        current_time = time.time()
        self.tile_request_times.append(current_time)
        
        # Add delay between requests to be respectful, but skip delay for first few tiles
        if len(self.tile_request_times) > 3:  # Skip delay for first 3 tiles for faster startup
            time.sleep(self.tile_request_delay)
    
    def _fetch_tile(self, x: int, y: int, zoom: int) -> Optional[Image.Image]:
        """Fetch a map tile, using cache if available."""
        cache_path = self._get_tile_cache_path(x, y, zoom)
        
        # Try to load from cache first
        if self._is_tile_cached(x, y, zoom):
            try:
                return Image.open(cache_path)
            except Exception as e:
                logger.warning(f"Failed to load cached tile {x},{y},{zoom}: {e}")
        
        # Check rate limit before making requests
        if not self._check_tile_rate_limit():
            logger.warning(f"Skipping tile {x},{y},{zoom} due to rate limit")
            return None
        
        # Fetch from server
        urls = self._get_tile_urls(x, y, zoom)
        
        for i, url in enumerate(urls):
            try:
                logger.debug(f"Trying tile URL {i+1}/{len(urls)}: {url}")
                
                # Record the request for rate limiting
                self._record_tile_request()
                
                response = requests.get(url, timeout=15, headers={
                    'User-Agent': 'FlightTrackerDevViewer/1.0 (Development Tool)',
                    'Accept': 'image/png,image/*,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive'
                })
                
                # Check for blocking/error responses
                if response.status_code == 403:
                    logger.warning(f"Access blocked for {url} - trying next provider")
                    continue
                elif response.status_code == 429:
                    logger.warning(f"Rate limited for {url} - trying next provider")
                    continue
                elif response.status_code != 200:
                    logger.warning(f"HTTP {response.status_code} for {url} - trying next provider")
                    continue
                
                # Check if we got an error page
                content_type = response.headers.get('content-type', '').lower()
                if 'text/html' in content_type or 'text/plain' in content_type:
                    logger.debug(f"Got HTML/text response from {url}")
                    continue
                
                # Check if response is too small (likely an error page)
                if len(response.content) < 2000:
                    logger.debug(f"Response too small ({len(response.content)} bytes) from {url}")
                    continue
                
                # Additional validation: check for blocking messages in content
                try:
                    content_text = response.content.decode('utf-8', errors='ignore').lower()
                    if any(blocked_word in content_text for blocked_word in ['blocked', 'overusing', 'rate limit', 'access denied']):
                        logger.warning(f"Blocked content detected from {url}")
                        continue
                except:
                    pass  # If we can't decode, assume it's binary image data
                
                # Save to cache
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(cache_path, 'wb') as f:
                        f.write(response.content)
                    logger.debug(f"Cached tile {x},{y},{zoom}")
                    return Image.open(cache_path)
                except Exception as e:
                    logger.warning(f"Could not save tile to cache: {e}")
                    # Return from memory
                    import io
                    return Image.open(io.BytesIO(response.content))
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request failed for {url}: {e}")
                if i == len(urls) - 1:
                    return None
                continue
            except Exception as e:
                logger.warning(f"Unexpected error fetching tile from {url}: {e}")
                if i == len(urls) - 1:
                    return None
                continue
        
        logger.warning(f"All tile URLs failed for {x},{y},{zoom}")
        return None
    
    def _get_map_background(self, center_lat: float, center_lon: float) -> Optional[Image.Image]:
        """Get the map background for the current view."""
        # Calculate appropriate zoom level
        if self.map_radius_miles <= 2:
            zoom = 13
        elif self.map_radius_miles <= 5:
            zoom = 12
        elif self.map_radius_miles <= 10:
            zoom = 11
        elif self.map_radius_miles <= 25:
            zoom = 10
        elif self.map_radius_miles <= 50:
            zoom = 9
        else:
            zoom = 8
        
        # Check if we need to update the background
        current_center = (round(center_lat, 4), round(center_lon, 4))
        if (self.cached_map_bg is not None and 
            self.last_map_center == current_center and 
            self.last_map_zoom == zoom):
            logger.debug("Using cached map background - no tile fetching needed")
            return self.cached_map_bg
        
        # Calculate tile coordinates for center
        center_x, center_y = self._latlon_to_tile_coords(center_lat, center_lon, zoom)
        
        # Calculate how many tiles we need
        lat_degrees = (self.map_radius_miles * 2) / 69.0
        lon_degrees = lat_degrees / math.cos(math.radians(center_lat))
        
        tiles_per_degree = 2 ** zoom
        base_tiles_x = max(1, int(lon_degrees * tiles_per_degree / 360.0 * 2))
        base_tiles_y = max(1, int(lat_degrees * tiles_per_degree / 360.0 * 2))
        
        # Limit maximum tiles for faster loading - use fewer tiles
        max_tiles = 4  # 2x2 maximum for much faster loading
        tiles_x = min(max_tiles, max(2, base_tiles_x))
        tiles_y = min(max_tiles, max(2, base_tiles_y))
        
        logger.info(f"Fetching {tiles_x}x{tiles_y} tiles at zoom {zoom}")
        
        # Calculate tile bounds
        start_x = center_x - tiles_x // 2
        start_y = center_y - tiles_y // 2
        
        # Create composite image
        composite_width = tiles_x * self.tile_size
        composite_height = tiles_y * self.tile_size
        composite = Image.new('RGB', (composite_width, composite_height), (0, 0, 0))
        
        # Fetch and composite tiles
        tiles_fetched = 0
        tiles_skipped = 0
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                tile_x = start_x + tx
                tile_y = start_y + ty
                
                # Check if tile is already cached first
                if self._is_tile_cached(tile_x, tile_y, zoom):
                    try:
                        cache_path = self._get_tile_cache_path(tile_x, tile_y, zoom)
                        tile_img = Image.open(cache_path)
                        tiles_skipped += 1
                        logger.debug(f"Using cached tile {tile_x},{tile_y},{zoom}")
                    except Exception as e:
                        logger.warning(f"Failed to load cached tile {tile_x},{tile_y},{zoom}: {e}")
                        tile_img = self._fetch_tile(tile_x, tile_y, zoom)
                else:
                    tile_img = self._fetch_tile(tile_x, tile_y, zoom)
                
                if tile_img:
                    if tile_img.mode != 'RGB':
                        tile_img = tile_img.convert('RGB')
                    
                    paste_x = tx * self.tile_size
                    paste_y = ty * self.tile_size
                    composite.paste(tile_img, (paste_x, paste_y))
                    tiles_fetched += 1
        
        logger.info(f"Tiles: {tiles_fetched} fetched, {tiles_skipped} from cache")
        
        if tiles_fetched == 0:
            logger.warning("No map tiles could be fetched")
            return None
        
        # Calculate crop area to match display bounds
        center_tile_x = center_x - start_x
        center_tile_y = center_y - start_y
        
        # Calculate position within the center tile
        center_lon_in_tile = (center_lon - self._tile_to_lon(start_x + center_tile_x, zoom)) / (self._tile_to_lon(start_x + center_tile_x + 1, zoom) - self._tile_to_lon(start_x + center_tile_x, zoom))
        center_lat_in_tile = (self._tile_to_lat(start_y + center_tile_y, zoom) - center_lat) / (self._tile_to_lat(start_y + center_tile_y, zoom) - self._tile_to_lat(start_y + center_tile_y + 1, zoom))
        
        # Calculate pixel position in composite
        center_pixel_x = int((center_tile_x + center_lon_in_tile) * self.tile_size)
        center_pixel_y = int((center_tile_y + center_lat_in_tile) * self.tile_size)
        
        # Calculate crop bounds
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
            fade_overlay = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            cropped = Image.blend(cropped, fade_overlay, 1.0 - self.fade_intensity)
        
        # Cache the result
        self.cached_map_bg = cropped
        self.last_map_center = current_center
        self.last_map_zoom = zoom
        
        logger.info(f"Generated map background with {tiles_fetched} tiles")
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
    
    def _fetch_aircraft_data(self) -> Optional[Dict]:
        """Fetch aircraft data from SkyAware API."""
        try:
            response = requests.get(self.skyaware_url, timeout=5)
            response.raise_for_status()
            data = response.json()
            logger.debug(f"Fetched data: {len(data.get('aircraft', []))} aircraft")
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch aircraft data: {e}")
            return None
    
    def _calculate_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two lat/lon points in miles."""
        R = 3959  # Earth's radius in miles
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        
        a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return R * c
    
    def _altitude_to_color(self, altitude: float) -> Tuple[int, int, int]:
        """Convert altitude to color using gradient interpolation."""
        breakpoints = sorted([(int(k), v) for k, v in self.altitude_colors.items()])
        
        if altitude <= breakpoints[0][0]:
            return tuple(breakpoints[0][1])
        if altitude >= breakpoints[-1][0]:
            return tuple(breakpoints[-1][1])
        
        for i in range(len(breakpoints) - 1):
            alt1, color1 = breakpoints[i]
            alt2, color2 = breakpoints[i + 1]
            
            if alt1 <= altitude <= alt2:
                ratio = (altitude - alt1) / (alt2 - alt1)
                r = int(color1[0] + (color2[0] - color1[0]) * ratio)
                g = int(color1[1] + (color2[1] - color1[1]) * ratio)
                b = int(color1[2] + (color2[2] - color1[2]) * ratio)
                return (r, g, b)
        
        return (255, 255, 255)
    
    def _latlon_to_pixel(self, lat: float, lon: float) -> Optional[Tuple[int, int]]:
        """Convert lat/lon to pixel coordinates on the display."""
        # Calculate degrees per pixel based on radius and display size
        lat_degrees = (self.map_radius_miles * 2) / 69.0
        lon_degrees = lat_degrees / math.cos(math.radians(self.center_lat))
        
        # Apply zoom factor
        effective_lat_degrees = lat_degrees / self.zoom_factor
        effective_lon_degrees = lon_degrees / self.zoom_factor
        
        # Calculate pixel scale
        lat_scale = self.display_height / effective_lat_degrees
        lon_scale = self.display_width / effective_lon_degrees
        
        # Convert to pixel coordinates
        x = int((lon - self.center_lon) * lon_scale + self.display_width / 2)
        y = int((self.center_lat - lat) * lat_scale + self.display_height / 2)
        
        # Check if within display bounds
        if 0 <= x < self.display_width and 0 <= y < self.display_height:
            return (x, y)
        
        return None
    
    def _process_aircraft_data(self, data: Dict) -> None:
        """Process and update aircraft data."""
        if not data or 'aircraft' not in data:
            return
        
        current_time = time.time()
        active_icao = set()
        
        for aircraft in data['aircraft']:
            icao = aircraft.get('hex', '').upper()
            if not icao:
                continue
            
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
            speed = aircraft.get('gs', 0)
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
            
            self.aircraft_data[icao] = aircraft_info
        
        # Clean up old aircraft
        stale_icao = [icao for icao, info in self.aircraft_data.items() 
                      if current_time - info['last_seen'] > 60]
        for icao in stale_icao:
            del self.aircraft_data[icao]
        
        logger.debug(f"Processed {len(active_icao)} aircraft, removed {len(stale_icao)} stale")
    
    def _update_loop(self):
        """Main update loop running in separate thread."""
        while self.running:
            try:
                current_time = time.time()
                
                # Fetch aircraft data
                if current_time - self.last_update >= self.update_interval:
                    data = self._fetch_aircraft_data()
                    if data:
                        self._process_aircraft_data(data)
                        self.last_update = current_time
                
                # Update display
                self.root.after(0, self._update_display)
                
                time.sleep(1)  # Update every second
                
            except Exception as e:
                logger.error(f"Error in update loop: {e}")
                time.sleep(5)
    
    def _update_display(self):
        """Update the display with current data."""
        try:
            # Get map background
            map_bg = self._get_map_background(self.center_lat, self.center_lon)
            
            # Create image with background
            if map_bg:
                img = map_bg.copy()
            else:
                img = Image.new('RGB', (self.display_width, self.display_height), (0, 0, 0))
            
            draw = ImageDraw.Draw(img)
            
            # Draw center position marker
            center_pixel = self._latlon_to_pixel(self.center_lat, self.center_lon)
            if center_pixel:
                x, y = center_pixel
                # Draw center marker
                for dx in [-2, -1, 0, 1, 2]:
                    for dy in [-2, -1, 0, 1, 2]:
                        if abs(dx) + abs(dy) <= 2:
                            draw.point((x + dx, y + dy), fill=(0, 0, 0))
                
                draw.point((x, y), fill=(255, 255, 255))
                draw.point((x-1, y), fill=(255, 255, 255))
                draw.point((x+1, y), fill=(255, 255, 255))
                draw.point((x, y-1), fill=(255, 255, 255))
                draw.point((x, y+1), fill=(255, 255, 255))
            
            # Draw aircraft
            for aircraft in self.aircraft_data.values():
                pixel = self._latlon_to_pixel(aircraft['lat'], aircraft['lon'])
                if not pixel:
                    continue
                
                x, y = pixel
                color = aircraft['color']
                
                # Draw aircraft with heading arrow
                heading = aircraft['heading']
                if heading:
                    # Calculate arrow points
                    angle_rad = math.radians(heading)
                    dx = int(3 * math.sin(angle_rad))
                    dy = int(-3 * math.cos(angle_rad))
                    
                    # Draw arrow with outline
                    arrow_points = [(x + dx, y + dy), (x, y)]
                    
                    # Draw black outline for arrow
                    for px, py in arrow_points:
                        for ox in [-1, 0, 1]:
                            for oy in [-1, 0, 1]:
                                if ox != 0 or oy != 0:
                                    draw.point((px + ox, py + oy), fill=(0, 0, 0))
                    
                    # Draw colored arrow
                    draw.point((x + dx, y + dy), fill=color)
                    draw.point((x, y), fill=color)
                else:
                    # No heading data, draw circle
                    for dx in [-1, 0, 1]:
                        for dy in [-1, 0, 1]:
                            if dx != 0 or dy != 0:
                                draw.point((x + dx, y + dy), fill=(0, 0, 0))
                    draw.point((x, y), fill=color)
            
            # Convert to PhotoImage and display
            photo = ImageTk.PhotoImage(img)
            self.map_canvas.delete("all")
            self.map_canvas.create_image(0, 0, anchor=tk.NW, image=photo)
            self.map_canvas.image = photo  # Keep a reference
            
            # Update status
            self.aircraft_count_var.set(f"Aircraft: {len(self.aircraft_data)}")
            self.status_var.set(f"Center: ({self.center_lat:.4f}, {self.center_lon:.4f}) | Radius: {self.map_radius_miles}mi")
            
        except Exception as e:
            logger.error(f"Error updating display: {e}")
            self.status_var.set(f"Error: {e}")
    
    def run(self):
        """Run the application."""
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            logger.info("Application interrupted by user")
        finally:
            self.running = False
            logger.info("Application closed")

def main():
    """Main entry point."""
    try:
        app = FlightTrackerDevViewer()
        app.run()
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        messagebox.showerror("Error", f"Failed to start application: {e}")

if __name__ == "__main__":
    main()
