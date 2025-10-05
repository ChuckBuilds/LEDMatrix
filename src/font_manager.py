import os
import logging
import freetype
from PIL import ImageFont
from typing import Dict, Tuple, Optional, Union, Any
from functools import lru_cache

logger = logging.getLogger(__name__)

class FontManager:
    """Centralized font management supporting TTF and BDF fonts with caching and measurement."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.fonts_config = config.get("fonts", {})
        
        # Font discovery and catalog
        self.font_catalog: Dict[str, str] = {}  # family_name -> file_path
        self.font_cache: Dict[str, Union[ImageFont.FreeTypeFont, freetype.Face]] = {}  # (family, size) -> font
        self.metrics_cache: Dict[str, Tuple[int, int, int]] = {}  # (text, font_id) -> (width, height, baseline)
        
        # Default configuration
        self.default_families = {
            "press_start": "assets/fonts/PressStart2P-Regular.ttf",
            "four_by_six": "assets/fonts/4x6-font.ttf"
        }
        
        self.default_tokens = {
            "xs": 6, "sm": 8, "md": 10, "lg": 12, "xl": 14, "xxl": 16
        }
        
        self.default_settings = {
            "family": "press_start",
            "size_token": "sm"
        }
        
        self._initialize_fonts()
    
    def reload_config(self, new_config: Dict[str, Any]):
        """Reload configuration and refresh font catalog."""
        self.config = new_config
        self.fonts_config = new_config.get("fonts", {})
        self.font_cache.clear()  # Clear cache to force reload
        self.metrics_cache.clear()  # Clear metrics cache
        self._initialize_fonts()
        logger.info("FontManager configuration reloaded successfully")
    
    def _initialize_fonts(self):
        """Initialize font catalog and validate configuration."""
        try:
            # Discover fonts from assets/fonts directory
            self._discover_fonts()
            
            # Load configured families or use defaults
            configured_families = self.fonts_config.get("families", {})
            if not configured_families:
                # Auto-register default families if they exist
                for family_name, font_path in self.default_families.items():
                    if os.path.exists(font_path):
                        self.font_catalog[family_name] = font_path
                        logger.info(f"Auto-registered default font: {family_name} -> {font_path}")
            else:
                # Use configured families and validate paths
                for family_name, font_path in configured_families.items():
                    if os.path.exists(font_path):
                        self.font_catalog[family_name] = font_path
                        logger.info(f"Registered configured font: {family_name} -> {font_path}")
                    else:
                        logger.warning(f"Configured font not found: {family_name} -> {font_path}")
            
            # Validate that we have at least one font
            if not self.font_catalog:
                logger.error("No valid fonts found! Using PIL default font as fallback.")
                # We'll handle this in resolve() method
                
        except Exception as e:
            logger.error(f"Error initializing fonts: {e}", exc_info=True)
    
    def _discover_fonts(self):
        """Discover available fonts in assets/fonts directory."""
        font_dir = "assets/fonts"
        if not os.path.exists(font_dir):
            logger.warning(f"Font directory not found: {font_dir}")
            return
        
        try:
            for filename in os.listdir(font_dir):
                if filename.lower().endswith(('.ttf', '.bdf')):
                    # Extract family name from filename
                    family_name = os.path.splitext(filename)[0].lower().replace('-', '_').replace(' ', '_')
                    font_path = os.path.join(font_dir, filename)
                    self.font_catalog[family_name] = font_path
                    logger.debug(f"Discovered font: {family_name} -> {font_path}")
        except Exception as e:
            logger.error(f"Error discovering fonts: {e}", exc_info=True)
    
    def get_font_catalog(self) -> Dict[str, str]:
        """Get the current font catalog."""
        return self.font_catalog.copy()
    
    def get_tokens(self) -> Dict[str, int]:
        """Get available size tokens."""
        return self.fonts_config.get("tokens", self.default_tokens).copy()
    
    def get_defaults(self) -> Dict[str, str]:
        """Get default font settings."""
        return self.fonts_config.get("defaults", self.default_settings).copy()
    
    def get_overrides(self) -> Dict[str, Dict[str, str]]:
        """Get per-element font overrides."""
        return self.fonts_config.get("overrides", {}).copy()
    
    def _get_smart_default_token(self, element_key: str) -> Optional[str]:
        """
        Get smart default size token based on complete baseline mapping.
        
        This implements the complete baseline font sizes that were previously in overrides.
        Now all font sizes are baked into the font manager as smart defaults.
        """
        # Complete baseline mapping - all font sizes from the original hardcoded values
        baseline_defaults = {
            # NFL elements
            'nfl.live.score': 'md',      # 10px
            'nfl.live.time': 'sm',       # 8px
            'nfl.live.team': 'sm',       # 8px
            'nfl.live.status': 'xs',     # 6px
            'nfl.live.record': 'xs',     # 6px
            'nfl.live.odds': 'xs',       # 6px
            'nfl.recent.score': 'lg',    # 12px
            'nfl.recent.time': 'sm',     # 8px
            'nfl.recent.team': 'sm',     # 8px
            'nfl.recent.status': 'xs',   # 6px
            'nfl.recent.record': 'xs',   # 6px
            'nfl.recent.odds': 'xs',     # 6px
            'nfl.upcoming.score': 'lg',  # 12px
            'nfl.upcoming.time': 'sm',   # 8px
            'nfl.upcoming.team': 'sm',   # 8px
            'nfl.upcoming.status': 'xs', # 6px
            'nfl.upcoming.record': 'xs', # 6px
            'nfl.upcoming.odds': 'xs',   # 6px
            
            # NHL elements
            'nhl.live.score': 'md',      # 10px
            'nhl.live.time': 'sm',       # 8px
            'nhl.live.team': 'sm',       # 8px
            'nhl.live.status': 'xs',     # 6px
            'nhl.live.record': 'xs',     # 6px
            'nhl.live.odds': 'xs',       # 6px
            'nhl.recent.score': 'md',    # 10px
            'nhl.recent.time': 'sm',     # 8px
            'nhl.recent.team': 'sm',     # 8px
            'nhl.recent.status': 'xs',   # 6px
            'nhl.recent.record': 'xs',   # 6px
            'nhl.recent.shots': 'xs',    # 6px
            'nhl.recent.odds': 'xs',     # 6px
            'nhl.upcoming.score': 'md',  # 10px
            'nhl.upcoming.time': 'sm',   # 8px
            'nhl.upcoming.team': 'sm',   # 8px
            'nhl.upcoming.status': 'xs', # 6px
            'nhl.upcoming.record': 'xs', # 6px
            'nhl.upcoming.odds': 'xs',   # 6px
            
            # NBA elements
            'nba.live.score': 'md',      # 10px
            'nba.live.time': 'sm',       # 8px
            'nba.live.team': 'sm',       # 8px
            'nba.live.status': 'xs',     # 6px
            'nba.live.record': 'xs',     # 6px
            'nba.live.odds': 'xs',       # 6px
            'nba.recent.score': 'lg',    # 12px
            'nba.recent.time': 'sm',     # 8px
            'nba.recent.team': 'sm',     # 8px
            'nba.recent.status': 'xs',   # 6px
            'nba.recent.record': 'xs',   # 6px
            'nba.recent.odds': 'xs',     # 6px
            'nba.upcoming.score': 'lg',  # 12px
            'nba.upcoming.time': 'sm',   # 8px
            'nba.upcoming.team': 'sm',   # 8px
            'nba.upcoming.status': 'xs', # 6px
            'nba.upcoming.record': 'xs', # 6px
            'nba.upcoming.odds': 'xs',   # 6px
            
            # NCAA Basketball elements
            'ncaam.live.score': 'md',    # 10px
            'ncaam.live.time': 'sm',     # 8px
            'ncaam.live.team': 'sm',     # 8px
            'ncaam.live.status': 'xs',   # 6px
            'ncaam.live.record': 'xs',   # 6px
            'ncaam.live.odds': 'xs',     # 6px
            'ncaam.recent.score': 'lg',  # 12px
            'ncaam.recent.time': 'sm',   # 8px
            'ncaam.recent.team': 'sm',   # 8px
            'ncaam.recent.status': 'xs', # 6px
            'ncaam.recent.record': 'xs', # 6px
            'ncaam.recent.odds': 'xs',   # 6px
            'ncaam.upcoming.score': 'lg', # 12px
            'ncaam.upcoming.time': 'sm', # 8px
            'ncaam.upcoming.team': 'sm', # 8px
            'ncaam.upcoming.status': 'xs', # 6px
            'ncaam.upcoming.record': 'xs', # 6px
            'ncaam.upcoming.odds': 'xs', # 6px
            
            # Soccer elements (special case - smaller team fonts)
            'soccer.live.score': 'md',   # 10px
            'soccer.live.time': 'sm',    # 8px
            'soccer.live.team': 'xs',    # 6px (special case)
            'soccer.live.status': 'xs',  # 6px
            'soccer.live.record': 'xs',  # 6px
            'soccer.live.odds': 'xs',    # 6px
            'soccer.recent.score': 'lg', # 12px
            'soccer.recent.time': 'sm',  # 8px
            'soccer.recent.team': 'xs',  # 6px (special case)
            'soccer.recent.status': 'xs', # 6px
            'soccer.recent.record': 'xs', # 6px
            'soccer.recent.odds': 'xs',  # 6px
            'soccer.upcoming.score': 'lg', # 12px
            'soccer.upcoming.time': 'sm', # 8px
            'soccer.upcoming.team': 'xs', # 6px (special case)
            'soccer.upcoming.status': 'xs', # 6px
            'soccer.upcoming.record': 'xs', # 6px
            'soccer.upcoming.odds': 'xs', # 6px
            
            # MiLB elements
            'milb.live.score': 'md',     # 10px
            'milb.live.time': 'sm',      # 8px
            'milb.live.team': 'sm',      # 8px
            'milb.live.status': 'xs',    # 6px
            'milb.live.record': 'xs',    # 6px
            'milb.live.odds': 'xs',      # 6px
            'milb.recent.score': 'lg',   # 12px
            'milb.recent.time': 'sm',    # 8px
            'milb.recent.team': 'sm',    # 8px
            'milb.recent.status': 'xs',  # 6px
            'milb.recent.record': 'xs',  # 6px
            'milb.recent.odds': 'xs',    # 6px
            'milb.upcoming.score': 'lg', # 12px
            'milb.upcoming.time': 'sm',  # 8px
            'milb.upcoming.team': 'sm',  # 8px
            'milb.upcoming.status': 'xs', # 6px
            'milb.upcoming.record': 'xs', # 6px
            'milb.upcoming.odds': 'xs',  # 6px
            
            # Clock elements
            'clock.time': 'sm',          # 8px
            'clock.ampm': 'sm',          # 8px
            'clock.weekday': 'sm',       # 8px
            'clock.date': 'sm',          # 8px
            
            # Calendar elements
            'calendar.datetime': 'sm',   # 8px
            'calendar.title': 'sm',      # 8px
            
            # Leaderboard elements
            'leaderboard.title': 'lg',   # 12px
            'leaderboard.rank': 'xs',    # 6px
            'leaderboard.team': 'sm',    # 8px
            'leaderboard.record': 'sm',  # 8px
            'leaderboard.medium': 'md',  # 10px
            'leaderboard.large': 'lg',   # 12px
            'leaderboard.xlarge': 'xl',  # 14px
            
            # Weather elements
            'weather.condition': 'sm',   # 8px
            'weather.temperature': 'sm', # 8px
            'weather.high_low': 'xs',    # 6px
            'weather.uv': 'xs',          # 6px
            'weather.humidity': 'xs',    # 6px
            'weather.wind': 'xs',        # 6px
            'weather.hourly.time': 'xs', # 6px
            'weather.hourly.temp': 'xs', # 6px
            'weather.daily.day': 'xs',   # 6px
            'weather.daily.temp': 'xs',  # 6px
            
            # Stock elements
            'stock.symbol': 'sm',        # 8px
            'stock.price': 'sm',         # 8px
            'stock.change': 'sm',        # 8px
            'stock.news.title': 'sm',    # 8px
            'stock.news.summary': 'xs',  # 6px
            
            # Music elements
            'music.artist': 'sm',        # 8px
            'music.title': 'lg',         # 12px (special case)
            'music.album': 'sm',         # 8px
        }
        
        # Return the exact baseline token for this element
        return baseline_defaults.get(element_key)
    
    def resolve(self, *, element_key: Optional[str] = None, family: Optional[str] = None, 
                size_px: Optional[int] = None, size_token: Optional[str] = None) -> Union[ImageFont.FreeTypeFont, freetype.Face]:
        """
        Resolve font based on element key or explicit parameters.
        
        Args:
            element_key: Element key (e.g., 'nfl.live.score') to look up overrides
            family: Font family name
            size_px: Font size in pixels
            size_token: Size token (e.g., 'sm', 'lg')
            
        Returns:
            Resolved font (PIL Font for TTF, freetype.Face for BDF)
        """
        try:
            # Determine family and size
            resolved_family = family
            resolved_size = size_px
            
            if element_key:
                # Check for element-specific overrides first
                overrides = self.get_overrides()
                element_config = overrides.get(element_key, {})
                
                # Apply element overrides
                if 'family' in element_config:
                    resolved_family = element_config['family']
                if 'size_token' in element_config:
                    size_token = element_config['size_token']
                if 'size_px' in element_config:
                    resolved_size = element_config['size_px']
                
                # If no override found, try smart defaults based on element type
                if 'size_token' not in element_config and not resolved_size:
                    smart_token = self._get_smart_default_token(element_key)
                    if smart_token:
                        size_token = smart_token
            
            # Apply defaults if not specified
            if not resolved_family:
                defaults = self.get_defaults()
                resolved_family = defaults.get('family', 'press_start')
            
            if not resolved_size and size_token:
                tokens = self.get_tokens()
                resolved_size = tokens.get(size_token, 8)
            elif not resolved_size:
                defaults = self.get_defaults()
                default_token = defaults.get('size_token', 'sm')
                tokens = self.get_tokens()
                resolved_size = tokens.get(default_token, 8)
            
            # Load and cache font
            cache_key = f"{resolved_family}_{resolved_size}"
            if cache_key in self.font_cache:
                return self.font_cache[cache_key]
            
            # Load font
            font = self._load_font(resolved_family, resolved_size)
            self.font_cache[cache_key] = font
            return font
            
        except Exception as e:
            logger.error(f"Error resolving font for element_key={element_key}, family={family}, size={size_px}, size_token={size_token}: {e}", exc_info=True)
            return self._get_fallback_font()
    
    def _load_font(self, family: str, size: int) -> Union[ImageFont.FreeTypeFont, freetype.Face]:
        """Load a specific font by family and size."""
        if family not in self.font_catalog:
            logger.warning(f"Font family '{family}' not found in catalog. Available: {list(self.font_catalog.keys())}")
            return self._get_fallback_font()
        
        font_path = self.font_catalog[family]
        
        try:
            if font_path.lower().endswith('.ttf'):
                # Load TTF with PIL
                font = ImageFont.truetype(font_path, size)
                logger.debug(f"Loaded TTF font: {family} ({size}px) from {font_path}")
                return font
            elif font_path.lower().endswith('.bdf'):
                # Load BDF with freetype
                if freetype is None:
                    logger.error("freetype not available for BDF fonts")
                    return self._get_fallback_font()
                
                face = freetype.Face(font_path)
                face.set_pixel_sizes(0, size)
                logger.debug(f"Loaded BDF font: {family} ({size}px) from {font_path}")
                return face
            else:
                logger.warning(f"Unsupported font type: {font_path}")
                return self._get_fallback_font()
                
        except Exception as e:
            logger.error(f"Failed to load font {family} ({size}px) from {font_path}: {e}", exc_info=True)
            return self._get_fallback_font()
    
    def _get_fallback_font(self) -> ImageFont.FreeTypeFont:
        """Get a fallback font when loading fails."""
        try:
            # Try to use the default font if available
            if 'press_start' in self.font_catalog:
                return ImageFont.truetype(self.font_catalog['press_start'], 8)
            else:
                # Use PIL default font
                return ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()
    
    def measure_text(self, text: str, font: Union[ImageFont.FreeTypeFont, freetype.Face]) -> Tuple[int, int, int]:
        """
        Measure text dimensions and baseline.
        
        Args:
            text: Text to measure
            font: Font to use for measurement
            
        Returns:
            Tuple of (width, height, baseline_offset)
        """
        cache_key = f"{text}_{id(font)}"
        if cache_key in self.metrics_cache:
            return self.metrics_cache[cache_key]
        
        try:
            if isinstance(font, freetype.Face):
                # BDF font measurement
                width = 0
                max_height = 0
                max_ascender = 0
                
                for char in text:
                    font.load_char(char)
                    width += font.glyph.advance.x >> 6  # Convert from 26.6 fixed point
                    glyph_height = font.glyph.bitmap.rows
                    max_height = max(max_height, glyph_height)
                    
                    # Get ascender for baseline calculation
                    ascender = font.size.ascender >> 6
                    max_ascender = max(max_ascender, ascender)
                
                # Baseline is typically the ascender
                baseline = max_ascender
                height = max_height
                
            else:
                # TTF font measurement with PIL
                bbox = font.getbbox(text)
                width = bbox[2] - bbox[0]
                height = bbox[3] - bbox[1]
                baseline = -bbox[1]  # Distance from top to baseline
                
        except Exception as e:
            logger.error(f"Error measuring text '{text}': {e}", exc_info=True)
            # Fallback measurements
            width = len(text) * 8  # Rough estimate
            height = 12
            baseline = 10
        
        result = (width, height, baseline)
        self.metrics_cache[cache_key] = result
        return result
    
    def get_font_height(self, font: Union[ImageFont.FreeTypeFont, freetype.Face]) -> int:
        """Get the height of a font."""
        try:
            if isinstance(font, freetype.Face):
                return font.size.height >> 6
            else:
                # Use a common character to measure height
                bbox = font.getbbox("Ay")
                return bbox[3] - bbox[1]
        except Exception as e:
            logger.error(f"Error getting font height: {e}", exc_info=True)
            return 12  # Default height
    
    def clear_cache(self):
        """Clear font and metrics cache."""
        self.font_cache.clear()
        self.metrics_cache.clear()
        logger.info("Font cache cleared")
    
    def reload_config(self, config: Dict[str, Any]):
        """Reload font configuration."""
        self.config = config
        self.fonts_config = config.get("fonts", {})
        self.clear_cache()
        self._initialize_fonts()
        logger.info("Font configuration reloaded")
