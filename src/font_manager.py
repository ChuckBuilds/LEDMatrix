import os
import logging
import freetype
import json
import hashlib
import urllib.request
import zipfile
import tempfile
import shutil
import time
from pathlib import Path
from PIL import ImageFont
from typing import Dict, Tuple, Optional, Union, Any, List
from functools import lru_cache

logger = logging.getLogger(__name__)

class FontManager:
    """
    Comprehensive font management supporting TTF and BDF fonts with caching, 
    measurement, plugin support, and manager font registration.
    
    This FontManager serves dual purposes:
    1. Utility functions for font loading, caching, and measurement
    2. Dynamic detection and override of fonts used by managers/plugins
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.fonts_config = config.get("fonts", {})
        
        # Font discovery and catalog
        self.font_catalog: Dict[str, str] = {}  # family_name -> file_path
        self.font_cache: Dict[str, Union[ImageFont.FreeTypeFont, freetype.Face]] = {}  # (family, size) -> font
        self.metrics_cache: Dict[str, Tuple[int, int, int]] = {}  # (text, font_id) -> (width, height, baseline)

        # Plugin font management
        self.plugin_fonts: Dict[str, Dict[str, Any]] = {}  # plugin_id -> font_manifest
        self.plugin_font_catalogs: Dict[str, Dict[str, str]] = {}  # plugin_id -> {family_name -> file_path}
        self.font_metadata: Dict[str, Dict[str, Any]] = {}  # family_name -> metadata
        self.font_dependencies: Dict[str, List[str]] = {}  # family_name -> [required_families]

        # Manager font registration - NEW for manager-centric model
        self.manager_fonts: Dict[str, Dict[str, Any]] = {}  # manager_id -> {element_key: {family, size_px, color}}
        self.detected_fonts: Dict[str, Dict[str, Any]] = {}  # element_key -> {family, size_px, color, manager_id, usage_count}

        # Dynamic font loading
        self.temp_font_dir = Path(tempfile.gettempdir()) / "ledmatrix_fonts"
        self.temp_font_dir.mkdir(exist_ok=True)

        # Performance monitoring
        self.performance_stats = {
            "font_load_times": {},
            "cache_hits": 0,
            "cache_misses": 0,
            "render_times": {},
            "total_renders": 0,
            "failed_loads": 0,
            "start_time": time.time()
        }
        
        # Common font paths for convenience
        self.common_fonts = {
            "press_start": "assets/fonts/PressStart2P-Regular.ttf",
            "four_by_six": "assets/fonts/4x6-font.ttf",
            "five_by_seven": "assets/fonts/5x7.bdf",
            "cozette_bdf": "assets/fonts/cozette.bdf"
        }
        
        # Size tokens for convenience
        self.size_tokens = {
            "xs": 6, "sm": 8, "md": 10, "lg": 12, "xl": 14, "xxl": 16
        }
        
        # Font overrides storage (for manual overrides)
        self.font_overrides_file = "config/font_overrides.json"
        self.font_overrides: Dict[str, Dict[str, Any]] = {}
        
        self._initialize_fonts()
    
    def reload_config(self, new_config: Dict[str, Any]):
        """Reload configuration and refresh font catalog."""
        self.config = new_config
        self.fonts_config = new_config.get("fonts", {})
        self.font_cache.clear()  # Clear cache to force reload
        self.metrics_cache.clear()  # Clear metrics cache
        self._initialize_fonts()
        logger.info("FontManager configuration reloaded successfully")

    # ==================== Manager Font Registration ====================
    # NEW: Support for managers to register their font choices dynamically

    def register_manager_font(self, manager_id: str, element_key: str, 
                             family: str, size_px: int, color: Optional[Tuple[int, int, int]] = None):
        """
        Register a font choice made by a manager for a specific element.
        This allows us to detect and track which fonts managers are using.
        
        Args:
            manager_id: Identifier for the manager (e.g., 'nfl_live', 'nba_recent')
            element_key: Element key (e.g., 'nfl.live.score')
            family: Font family name
            size_px: Font size in pixels
            color: Optional RGB color tuple
        """
        if manager_id not in self.manager_fonts:
            self.manager_fonts[manager_id] = {}
        
        font_spec = {
            "family": family,
            "size_px": size_px,
            "manager_id": manager_id
        }
        if color:
            font_spec["color"] = color
        
        self.manager_fonts[manager_id][element_key] = font_spec
        
        # Track usage in detected_fonts
        if element_key not in self.detected_fonts:
            self.detected_fonts[element_key] = font_spec.copy()
            self.detected_fonts[element_key]["usage_count"] = 1
        else:
            self.detected_fonts[element_key]["usage_count"] += 1
        
        logger.debug(f"Registered font for {manager_id}.{element_key}: {family}@{size_px}px")

    def get_manager_fonts(self, manager_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get registered fonts for a specific manager or all managers.
        
        Args:
            manager_id: Optional manager ID, if None returns all
            
        Returns:
            Dictionary of registered fonts
        """
        if manager_id:
            return self.manager_fonts.get(manager_id, {})
        return self.manager_fonts.copy()

    def get_detected_fonts(self) -> Dict[str, Dict[str, Any]]:
        """Get all detected font usage across managers."""
        return self.detected_fonts.copy()

    # ==================== Plugin Font Management ====================

    def register_plugin_fonts(self, plugin_id: str, font_manifest: Dict[str, Any]) -> bool:
        """
        Register fonts for a specific plugin.

        Args:
            plugin_id: Unique identifier for the plugin
            font_manifest: Font manifest from plugin's manifest.json

        Returns:
            True if registration successful, False otherwise
        """
        try:
            # Validate font manifest structure
            if not self._validate_font_manifest(font_manifest):
                logger.error(f"Invalid font manifest for plugin {plugin_id}")
                return False

            # Store plugin font manifest
            self.plugin_fonts[plugin_id] = font_manifest

            # Create plugin-specific font catalog
            self.plugin_font_catalogs[plugin_id] = {}

            # Process font definitions
            fonts = font_manifest.get("fonts", [])
            for font_def in fonts:
                if self._register_plugin_font(plugin_id, font_def):
                    logger.info(f"Successfully registered font {font_def.get('family')} for plugin {plugin_id}")

            logger.info(f"Registered {len(fonts)} fonts for plugin {plugin_id}")
            return True

        except Exception as e:
            logger.error(f"Error registering fonts for plugin {plugin_id}: {e}", exc_info=True)
            return False

    def _validate_font_manifest(self, font_manifest: Dict[str, Any]) -> bool:
        """Validate the structure of a plugin's font manifest."""
        required_fields = ["fonts"]

        # Check required top-level fields
        for field in required_fields:
            if field not in font_manifest:
                logger.error(f"Missing required field '{field}' in font manifest")
                return False

        # Validate each font definition
        fonts = font_manifest.get("fonts", [])
        for font_def in fonts:
            if not isinstance(font_def, dict):
                logger.error("Font definition must be a dictionary")
                return False

            required_font_fields = ["family", "source"]
            for field in required_font_fields:
                if field not in font_def:
                    logger.error(f"Missing required field '{field}' in font definition")
                    return False

        return True

    def _register_plugin_font(self, plugin_id: str, font_def: Dict[str, Any]) -> bool:
        """Register a single font from a plugin."""
        try:
            family = font_def["family"]
            source = font_def["source"]

            # Handle different source types
            font_path = None
            if source.startswith(("http://", "https://")):
                # Download from URL
                font_path = self._download_font(source, font_def)
            elif source.startswith("plugin://"):
                # Relative to plugin directory
                relative_path = source.replace("plugin://", "")
                font_path = self._resolve_plugin_font_path(plugin_id, relative_path)
            else:
                # Absolute or relative path
                font_path = source

            if not font_path or not os.path.exists(font_path):
                logger.error(f"Font file not found: {font_path}")
                return False

            # Add to plugin catalog with namespaced family name
            namespaced_family = f"{plugin_id}::{family}"
            self.plugin_font_catalogs[plugin_id][family] = font_path
            self.font_catalog[namespaced_family] = font_path

            # Store metadata
            if "metadata" in font_def:
                self.font_metadata[namespaced_family] = font_def["metadata"]

            # Store dependencies
            if "dependencies" in font_def:
                self.font_dependencies[namespaced_family] = font_def["dependencies"]

            logger.info(f"Registered plugin font: {namespaced_family} -> {font_path}")
            return True

        except Exception as e:
            logger.error(f"Error registering plugin font: {e}", exc_info=True)
            return False

    def _download_font(self, url: str, font_def: Dict[str, Any]) -> Optional[str]:
        """Download a font from a URL."""
        try:
            family = font_def["family"]
            
            # Generate cache filename based on URL hash
            url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
            extension = self._get_font_extension(url)
            cache_filename = f"{family}_{url_hash}{extension}"
            cache_path = self.temp_font_dir / cache_filename

            # Check if already downloaded
            if cache_path.exists():
                logger.info(f"Using cached font: {cache_path}")
                return str(cache_path)

            # Download font
            logger.info(f"Downloading font from {url}")
            urllib.request.urlretrieve(url, cache_path)

            # Handle zip files
            if url.endswith('.zip'):
                extract_dir = self.temp_font_dir / f"{family}_{url_hash}"
                extract_dir.mkdir(exist_ok=True)
                
                with zipfile.ZipFile(cache_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                
                # Find the actual font file
                for file in extract_dir.iterdir():
                    if file.suffix.lower() in ['.ttf', '.otf', '.bdf']:
                        return str(file)

            return str(cache_path)

        except Exception as e:
            logger.error(f"Error downloading font from {url}: {e}")
            return None

    def _get_font_extension(self, url: str) -> str:
        """Extract font file extension from URL."""
        if '.ttf' in url.lower():
            return '.ttf'
        elif '.otf' in url.lower():
            return '.otf'
        elif '.bdf' in url.lower():
            return '.bdf'
        elif '.zip' in url.lower():
            return '.zip'
        return '.ttf'  # default

    def _resolve_plugin_font_path(self, plugin_id: str, relative_path: str) -> Optional[str]:
        """Resolve a plugin-relative font path."""
        # Assume plugins are in a 'plugins' directory
        plugin_dir = Path("plugins") / plugin_id
        font_path = plugin_dir / relative_path

        if font_path.exists():
            return str(font_path)

        logger.error(f"Plugin font not found: {font_path}")
        return None

    def unregister_plugin_fonts(self, plugin_id: str) -> bool:
        """Unregister all fonts for a plugin."""
        try:
            if plugin_id in self.plugin_fonts:
                # Remove from plugin catalogs
                if plugin_id in self.plugin_font_catalogs:
                    for family in self.plugin_font_catalogs[plugin_id]:
                        namespaced_family = f"{plugin_id}::{family}"
                        if namespaced_family in self.font_catalog:
                            del self.font_catalog[namespaced_family]
                        if namespaced_family in self.font_metadata:
                            del self.font_metadata[namespaced_family]
                    
                    del self.plugin_font_catalogs[plugin_id]

                # Remove plugin manifest
                del self.plugin_fonts[plugin_id]

                # Clear related cache entries
                self._clear_plugin_font_cache(plugin_id)

                logger.info(f"Unregistered fonts for plugin {plugin_id}")
                return True

            return False

        except Exception as e:
            logger.error(f"Error unregistering plugin fonts: {e}")
            return False

    def _clear_plugin_font_cache(self, plugin_id: str):
        """Clear font cache entries for a specific plugin."""
        keys_to_remove = [key for key in self.font_cache.keys() if key.startswith(f"{plugin_id}::")]
        for key in keys_to_remove:
            del self.font_cache[key]

    def get_plugin_fonts(self, plugin_id: str) -> List[str]:
        """Get list of font families registered by a plugin."""
        if plugin_id in self.plugin_font_catalogs:
            return list(self.plugin_font_catalogs[plugin_id].keys())
        return []

    # ==================== Font Resolution ====================

    def resolve_font(self, element_key: str, family: str, size_px: int, 
                    plugin_id: Optional[str] = None) -> Union[ImageFont.FreeTypeFont, freetype.Face]:
        """
        Resolve font for an element, checking for overrides.
        
        This is the main method managers should call to get fonts.
        It checks for manual overrides first, then uses the manager's choice.
        
        Args:
            element_key: Element key (e.g., 'nfl.live.score')
            family: Font family name (manager's choice)
            size_px: Font size in pixels (manager's choice)
            plugin_id: Optional plugin context for namespaced fonts
            
        Returns:
            Resolved font object
        """
        start_time = time.time()
        
        try:
            # Check for manual overrides first
            if element_key in self.font_overrides:
                override = self.font_overrides[element_key]
                if override.get("family"):
                    family = override["family"]
                if override.get("size_px"):
                    size_px = override["size_px"]
                logger.debug(f"Applied override for {element_key}: {family}@{size_px}px")

            # Handle namespaced plugin fonts
            if plugin_id and "::" not in family:
                # Check if plugin has this font
                if plugin_id in self.plugin_font_catalogs and family in self.plugin_font_catalogs[plugin_id]:
                    family = f"{plugin_id}::{family}"

            # Get the font
            font = self.get_font(family, size_px)
            
            # Record performance
            duration = time.time() - start_time
            self._record_performance_metric("resolve", f"{family}_{size_px}", duration)
            
            return font

        except Exception as e:
            logger.error(f"Error resolving font for {element_key}: {e}", exc_info=True)
            return self._get_fallback_font()

    def get_font(self, family: str, size_px: int) -> Union[ImageFont.FreeTypeFont, freetype.Face]:
        """
        Get a font object for the specified family and size.
        
        Args:
            family: Font family name (can include plugin namespace like "plugin_id::family")
            size_px: Font size in pixels
            
        Returns:
            Font object (PIL Font for TTF, freetype.Face for BDF)
        """
        # Check cache first
        cache_key = f"{family}_{size_px}"
        if cache_key in self.font_cache:
            self.performance_stats["cache_hits"] += 1
            return self.font_cache[cache_key]

        self.performance_stats["cache_misses"] += 1
        start_time = time.time()

        # Load font
        font_path = self.font_catalog.get(family)
        if not font_path:
            logger.warning(f"Font family '{family}' not found")
            self.performance_stats["failed_loads"] += 1
            font = ImageFont.load_default()
        else:
            try:
                if font_path.endswith('.bdf'):
                    font = self._load_bdf_font(font_path, size_px)
                else:
                    font = ImageFont.truetype(font_path, size_px)
            except Exception as e:
                logger.error(f"Error loading font {font_path}: {e}")
                self.performance_stats["failed_loads"] += 1
                font = ImageFont.load_default()

        # Cache and record performance
        self.font_cache[cache_key] = font
        duration = time.time() - start_time
        self.performance_stats["font_load_times"][cache_key] = duration
        
        return font

    def _load_bdf_font(self, font_path: str, size_px: int) -> freetype.Face:
        """Load a BDF font using FreeType."""
        try:
            face = freetype.Face(font_path)
            # Set character size (width, height) in 1/64th of points
            face.set_char_size(size_px * 64, size_px * 64, 72, 72)
            return face
        except Exception as e:
            logger.error(f"Error loading BDF font {font_path}: {e}")
            raise

    def _get_fallback_font(self) -> ImageFont.ImageFont:
        """Get a fallback font when loading fails."""
        return ImageFont.load_default()

    # ==================== Font Measurement ====================

    def measure_text(self, text: str, font: Union[ImageFont.FreeTypeFont, freetype.Face]) -> Tuple[int, int, int]:
        """
        Measure text dimensions and baseline.
        
        Args:
            text: Text to measure
            font: Font to use for measurement
            
        Returns:
            Tuple of (width, height, baseline_offset)
        """
        cache_key = f"{hash(text)}_{id(font)}"

        if cache_key in self.metrics_cache:
            return self.metrics_cache[cache_key]

        try:
            if isinstance(font, freetype.Face):
                # BDF font measurement using FreeType
                width = 0
                height = 0
                baseline = 0
                max_ascender = 0

                for char in text:
                    font.load_char(char)
                    width += font.glyph.advance.x >> 6  # Convert from 26.6 fixed point
                    glyph_height = font.glyph.bitmap.rows
                    height = max(height, glyph_height)

                    # Get ascender for baseline calculation
                    ascender = font.size.ascender >> 6
                    max_ascender = max(max_ascender, ascender)

                baseline = max_ascender

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

    # ==================== Override Management ====================

    def set_override(self, element_key: str, family: str = None, size_px: int = None):
        """Set font override for a specific element."""
        if element_key not in self.font_overrides:
            self.font_overrides[element_key] = {}

        if family is not None:
            self.font_overrides[element_key]["family"] = family
        if size_px is not None:
            self.font_overrides[element_key]["size_px"] = size_px

        # Remove empty overrides
        if not self.font_overrides[element_key]:
            del self.font_overrides[element_key]
        else:
            self._save_overrides()

        self.clear_cache()
        logger.info(f"Font override set for {element_key}: {self.font_overrides.get(element_key, {})}")

    def remove_override(self, element_key: str):
        """Remove font override for a specific element."""
        if element_key in self.font_overrides:
            del self.font_overrides[element_key]
            self._save_overrides()
            self.clear_cache()
            logger.info(f"Font override removed for {element_key}")

    def get_overrides(self) -> Dict[str, Dict[str, str]]:
        """Get current font overrides."""
        return self.font_overrides.copy()

    # ==================== Font Discovery ====================

    def _initialize_fonts(self):
        """Initialize font catalog and validate configuration."""
        self._scan_fonts_directory()
        self._register_common_fonts()
        self._load_overrides()

    def _scan_fonts_directory(self):
        """Scan assets/fonts directory for available fonts."""
        fonts_dir = "assets/fonts"
        if not os.path.exists(fonts_dir):
            logger.warning(f"Fonts directory not found: {fonts_dir}")
            return

        for filename in os.listdir(fonts_dir):
            if filename.endswith(('.ttf', '.bdf')):
                filepath = os.path.join(fonts_dir, filename)
                # Generate family name from filename (without extension)
                family_name = filename.rsplit('.', 1)[0].lower()
                self.font_catalog[family_name] = filepath
                logger.debug(f"Found font: {family_name} -> {filepath}")

    def _register_common_fonts(self):
        """Register common font aliases from common_fonts dictionary."""
        for family_name, font_path in self.common_fonts.items():
            # Check if font file exists
            if os.path.exists(font_path):
                # Register the common font name (overrides auto-generated name if exists)
                self.font_catalog[family_name] = font_path
                logger.debug(f"Registered common font: {family_name} -> {font_path}")
            else:
                logger.warning(f"Common font file not found: {font_path} (family: {family_name})")

    def _load_overrides(self):
        """Load font overrides from configuration."""
        try:
            if os.path.exists(self.font_overrides_file):
                with open(self.font_overrides_file, 'r') as f:
                    self.font_overrides = json.load(f)
                    logger.info(f"Loaded {len(self.font_overrides)} font overrides")
            else:
                self.font_overrides = {}
        except Exception as e:
            logger.warning(f"Could not load font overrides: {e}")
            self.font_overrides = {}

    def _save_overrides(self):
        """Save current font overrides to file."""
        try:
            os.makedirs(os.path.dirname(self.font_overrides_file), exist_ok=True)
            with open(self.font_overrides_file, 'w') as f:
                json.dump(self.font_overrides, f, indent=2)
            logger.info(f"Saved {len(self.font_overrides)} font overrides")
        except Exception as e:
            logger.error(f"Could not save font overrides: {e}")

    # ==================== Utility Methods ====================

    def clear_cache(self):
        """Clear font and metrics cache."""
        self.font_cache.clear()
        self.metrics_cache.clear()
        logger.info("Font cache cleared")

    def get_available_fonts(self) -> Dict[str, str]:
        """Get dictionary of available font families and their paths."""
        return self.font_catalog.copy()

    def get_size_tokens(self) -> Dict[str, int]:
        """Get available size tokens."""
        return self.size_tokens.copy()

    def _record_performance_metric(self, operation: str, font_key: str, duration: float):
        """Record a performance metric."""
        if operation not in self.performance_stats:
            self.performance_stats[operation] = {}
        self.performance_stats[operation][font_key] = duration

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        uptime = time.time() - self.performance_stats["start_time"]
        return {
            "uptime_seconds": uptime,
            "cache_hits": self.performance_stats["cache_hits"],
            "cache_misses": self.performance_stats["cache_misses"],
            "cache_hit_rate": (
                self.performance_stats["cache_hits"] / 
                (self.performance_stats["cache_hits"] + self.performance_stats["cache_misses"])
                if (self.performance_stats["cache_hits"] + self.performance_stats["cache_misses"]) > 0 else 0
            ),
            "total_fonts_cached": len(self.font_cache),
            "total_metrics_cached": len(self.metrics_cache),
            "failed_loads": self.performance_stats["failed_loads"],
            "total_fonts_available": len(self.font_catalog),
            "plugin_fonts": len(self.plugin_fonts),
            "manager_fonts": len(self.manager_fonts),
            "detected_fonts": len(self.detected_fonts)
        }

    def get_font_catalog(self) -> Dict[str, str]:
        """Get the current font catalog."""
        return self.font_catalog.copy()

    def add_font(self, font_file_path: str, family_name: str) -> bool:
        """Add a new font to the catalog."""
        try:
            # Validate font file
            if not os.path.exists(font_file_path):
                logger.error(f"Font file not found: {font_file_path}")
                return False

            # Check if family name already exists
            if family_name in self.font_catalog:
                logger.warning(f"Font family '{family_name}' already exists")
                return False

            # Copy font to assets/fonts directory
            fonts_dir = "assets/fonts"
            os.makedirs(fonts_dir, exist_ok=True)

            target_path = os.path.join(fonts_dir, f"{family_name}.{font_file_path.rsplit('.', 1)[-1]}")

            # Add to catalog
            self.font_catalog[family_name] = font_file_path
            self.clear_cache()
            logger.info(f"Added font {family_name}: {font_file_path}")
            return True

        except Exception as e:
            logger.error(f"Error adding font {family_name}: {e}")
            return False

    def remove_font(self, family_name: str) -> bool:
        """Remove a font from the catalog."""
        try:
            if family_name not in self.font_catalog:
                logger.warning(f"Font family '{family_name}' not found")
                return False

            # Check if font is currently in use
            in_use = False
            for override in self.font_overrides.values():
                if override.get("family") == family_name:
                    in_use = True
                    break

            if in_use:
                logger.error(f"Cannot remove font '{family_name}' - it is currently in use")
                return False

            del self.font_catalog[family_name]
            self.clear_cache()
            logger.info(f"Removed font {family_name}")
            return True

        except Exception as e:
            logger.error(f"Error removing font {family_name}: {e}")
            return False

    def validate_font(self, font_path: str) -> Dict[str, Any]:
        """Validate a font file."""
        try:
            if not os.path.exists(font_path):
                return {"valid": False, "error": "Font file not found"}

            if font_path.endswith('.bdf'):
                # Try to load BDF font
                face = freetype.Face(font_path)
                return {"valid": True, "type": "bdf", "family": "unknown"}
            elif font_path.endswith('.ttf'):
                # Try to load TTF font
                font = ImageFont.truetype(font_path, 12)
                return {"valid": True, "type": "ttf", "family": "unknown"}
            else:
                return {"valid": False, "error": "Unsupported font format"}

        except Exception as e:
            return {"valid": False, "error": str(e)}
