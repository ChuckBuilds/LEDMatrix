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
            "four_by_six": "assets/fonts/4x6-font.ttf",
            "cozette_bdf": "assets/fonts/cozette.bdf"
        }
        
        self.default_tokens = {
            "xs": 6, "sm": 8, "md": 10, "lg": 12, "xl": 14, "xxl": 16
        }
        
        self.default_settings = {
            "family": "press_start",
            "size_token": "sm"
        }
        
        self._initialize_fonts()
    
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
                # Check for element-specific overrides
                overrides = self.get_overrides()
                element_config = overrides.get(element_key, {})
                
                # Apply element overrides
                if 'family' in element_config:
                    resolved_family = element_config['family']
                if 'size_token' in element_config:
                    size_token = element_config['size_token']
                if 'size_px' in element_config:
                    resolved_size = element_config['size_px']
            
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
