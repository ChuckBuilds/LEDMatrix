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
    """Centralized font management supporting TTF and BDF fonts with caching, measurement, and plugin support."""
    
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
    
    def reload_config(self, new_config: Dict[str, Any]):
        """Reload configuration and refresh font catalog."""
        self.config = new_config
        self.fonts_config = new_config.get("fonts", {})
        self.font_cache.clear()  # Clear cache to force reload
        self.metrics_cache.clear()  # Clear metrics cache
        self._initialize_fonts()
        logger.info("FontManager configuration reloaded successfully")

    # Plugin Font Management Methods

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
            elif source.startswith("file://"):
                # Local file path (relative to plugin directory)
                font_path = self._resolve_plugin_font_path(plugin_id, source[7:])
            else:
                # Assume it's a relative path within plugin
                font_path = self._resolve_plugin_font_path(plugin_id, source)

            if not font_path or not os.path.exists(font_path):
                logger.error(f"Font file not found for plugin {plugin_id}: {source}")
                return False

            # Check if font family already exists globally
            if family in self.font_catalog:
                logger.warning(f"Font family '{family}' already exists globally, plugin {plugin_id} will use plugin namespace")

            # Add to plugin-specific catalog with namespaced key
            plugin_family_key = f"{plugin_id}::{family}"
            self.plugin_font_catalogs[plugin_id][plugin_family_key] = font_path

            # Store metadata
            self.font_metadata[plugin_family_key] = {
                "original_family": family,
                "plugin_id": plugin_id,
                "source": source,
                "path": font_path,
                **font_def
            }

            # Handle dependencies
            dependencies = font_def.get("depends_on", [])
            self.font_dependencies[plugin_family_key] = dependencies

            return True

        except Exception as e:
            logger.error(f"Error registering font {font_def.get('family', 'unknown')} for plugin {plugin_id}: {e}")
            return False

    def _download_font(self, url: str, font_def: Dict[str, Any]) -> Optional[str]:
        """Download a font from a URL and return the local path."""
        try:
            # Create unique filename based on URL hash
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            filename = f"{font_def['family']}_{url_hash}.{self._get_font_extension(url)}"
            local_path = self.temp_font_dir / filename

            # Download if not already cached
            if not local_path.exists():
                logger.info(f"Downloading font from {url}")
                urllib.request.urlretrieve(url, local_path)
                logger.info(f"Downloaded font to {local_path}")

            return str(local_path)

        except Exception as e:
            logger.error(f"Error downloading font from {url}: {e}")
            return None

    def _get_font_extension(self, url: str) -> str:
        """Extract font file extension from URL."""
        if url.lower().endswith('.ttf'):
            return 'ttf'
        elif url.lower().endswith('.bdf'):
            return 'bdf'
        else:
            return 'ttf'  # Default assumption

    def _resolve_plugin_font_path(self, plugin_id: str, relative_path: str) -> Optional[str]:
        """Resolve a plugin-relative font path to absolute path."""
        # This would need to be implemented based on how plugins are structured
        # For now, assume plugins are in a plugins/ directory
        plugin_dir = Path(f"plugins/{plugin_id}")
        font_path = plugin_dir / relative_path

        if font_path.exists():
            return str(font_path)

        # Try common font subdirectories
        common_dirs = ["fonts", "assets/fonts", "resources/fonts"]
        for dir_name in common_dirs:
            test_path = plugin_dir / dir_name / relative_path
            if test_path.exists():
                return str(test_path)

        return None

    def unregister_plugin_fonts(self, plugin_id: str) -> bool:
        """Unregister all fonts for a specific plugin."""
        try:
            if plugin_id not in self.plugin_fonts:
                logger.warning(f"No fonts registered for plugin {plugin_id}")
                return True

            # Remove from plugin catalogs
            if plugin_id in self.plugin_font_catalogs:
                del self.plugin_font_catalogs[plugin_id]

            # Remove metadata for plugin fonts
            plugin_font_keys = [key for key in self.font_metadata.keys() if key.startswith(f"{plugin_id}::")]
            for key in plugin_font_keys:
                del self.font_metadata[key]
                if key in self.font_dependencies:
                    del self.font_dependencies[key]

            # Remove from global catalog if they were added there
            global_keys = [key for key in self.font_catalog.keys() if key.startswith(f"{plugin_id}::")]
            for key in global_keys:
                del self.font_catalog[key]

            # Clear plugin fonts record
            del self.plugin_fonts[plugin_id]

            # Clear caches that might reference plugin fonts
            self._clear_plugin_font_cache(plugin_id)

            logger.info(f"Unregistered all fonts for plugin {plugin_id}")
            return True

        except Exception as e:
            logger.error(f"Error unregistering fonts for plugin {plugin_id}: {e}")
            return False

    def _clear_plugin_font_cache(self, plugin_id: str):
        """Clear font cache entries for a specific plugin."""
        plugin_prefix = f"{plugin_id}::"
        keys_to_remove = []

        # Find cache keys that belong to this plugin
        for cache_key in self.font_cache.keys():
            if cache_key.startswith(plugin_prefix):
                keys_to_remove.append(cache_key)

        for key in keys_to_remove:
            del self.font_cache[key]

        # Clear metrics cache for plugin fonts
        metrics_keys_to_remove = []
        for cache_key in self.metrics_cache.keys():
            if plugin_prefix in cache_key:
                metrics_keys_to_remove.append(cache_key)

        for key in metrics_keys_to_remove:
            del self.metrics_cache[key]

    def get_plugin_fonts(self, plugin_id: str) -> List[str]:
        """Get list of font families registered by a plugin."""
        if plugin_id not in self.plugin_font_catalogs:
            return []

        return list(self.plugin_font_catalogs[plugin_id].keys())

    def resolve_font_with_plugin_support(self, *, element_key: Optional[str] = None,
                                       family: Optional[str] = None, size_px: Optional[int] = None,
                                       size_token: Optional[str] = None, plugin_id: Optional[str] = None) -> Union[ImageFont.FreeTypeFont, freetype.Face]:
        """
        Enhanced font resolution with plugin support.

        Args:
            element_key: Element key for smart defaults
            family: Font family name (can include plugin namespace like "plugin_id::family")
            size_px: Font size in pixels
            size_token: Size token (xs, sm, md, lg, xl, xxl)
            plugin_id: Plugin ID for context (used for font resolution)

        Returns:
            Resolved font object
        """
        try:
            # Handle namespaced font family (plugin_id::family)
            resolved_family = family
            if family and "::" in family:
                plugin_prefix, actual_family = family.split("::", 1)
                if plugin_prefix in self.plugin_font_catalogs and actual_family in self.plugin_font_catalogs[plugin_prefix]:
                    resolved_family = family  # Keep namespaced version for loading

            # Use enhanced resolution logic
            return self._resolve_font_enhanced(element_key, resolved_family, size_px, size_token, plugin_id)

        except Exception as e:
            logger.error(f"Error resolving font: {e}", exc_info=True)
            return self._get_fallback_font()

    def _resolve_font_enhanced(self, element_key: Optional[str], family: Optional[str],
                              size_px: Optional[int], size_token: Optional[str],
                              plugin_id: Optional[str]) -> Union[ImageFont.FreeTypeFont, freetype.Face]:
        """Enhanced font resolution with plugin support and intelligent fallback chains."""
        try:
            # Try original resolution first for backward compatibility
            try:
                return self.resolve(element_key=element_key, family=family, size_px=size_px, size_token=size_token)
            except Exception:
                # If original resolution fails, try enhanced fallback logic
                pass

            # Enhanced fallback resolution
            fallback_font = self._find_best_fallback_font(family, size_px, size_token, plugin_id)
            if fallback_font:
                logger.info(f"Using fallback font for {family}: {fallback_font}")
                return fallback_font

            # Last resort: system default
            return self._get_fallback_font()

        except Exception as e:
            logger.error(f"Error in enhanced font resolution: {e}")
            return self._get_fallback_font()

    def _find_best_fallback_font(self, target_family: Optional[str], size_px: Optional[int],
                                size_token: Optional[str], plugin_id: Optional[str]) -> Optional[Union[ImageFont.FreeTypeFont, freetype.Face]]:
        """Find the best fallback font using similarity metrics."""

        # Determine target size
        if not size_px and size_token:
            tokens = self.get_tokens()
            size_px = tokens.get(size_token, 12)
        elif not size_px:
            size_px = 12

        # Get all available fonts organized by source
        all_fonts = self._get_all_available_fonts()

        # Score each available font for similarity to target
        scored_fonts = []
        for source, fonts in all_fonts.items():
            for font_family in fonts:
                similarity_score = self._calculate_font_similarity(target_family, font_family, size_px)
                scored_fonts.append((font_family, similarity_score, source))

        # Sort by similarity score (highest first)
        scored_fonts.sort(key=lambda x: x[1], reverse=True)

        # Try to load fonts in order of similarity
        for font_family, score, source in scored_fonts[:5]:  # Try top 5 matches
            try:
                if "::" in font_family:
                    # Plugin font
                    return self._load_plugin_font(font_family, size_px)
                else:
                    # Global font
                    return self._load_font(font_family, size_px)
            except Exception as e:
                logger.debug(f"Fallback font {font_family} failed to load: {e}")
                continue

        return None

    def _get_all_available_fonts(self) -> Dict[str, List[str]]:
        """Get all available fonts organized by source."""
        fonts = {
            "global": list(self.font_catalog.keys()),
            "plugins": {}
        }

        for plugin_id, catalog in self.plugin_font_catalogs.items():
            fonts["plugins"][plugin_id] = list(catalog.keys())

        return fonts

    def _calculate_font_similarity(self, target_family: Optional[str], candidate_family: str, target_size: int) -> float:
        """Calculate similarity score between target font and candidate font."""

        if not target_family:
            return 0.5  # Neutral score for no target

        score = 0.0

        # Exact match gets highest score
        if target_family == candidate_family:
            return 1.0

        # Check if it's a namespaced version of the target
        if "::" in candidate_family:
            plugin_family = candidate_family.split("::", 1)[1]
            if plugin_family == target_family:
                score += 0.8

        # Category-based similarity (from metadata if available)
        target_metadata = self.font_metadata.get(target_family, {})
        candidate_metadata = self.font_metadata.get(candidate_family, {})

        target_category = target_metadata.get("category")
        candidate_category = candidate_metadata.get("category")

        if target_category and candidate_category:
            if target_category == candidate_category:
                score += 0.3
            elif self._categories_similar(target_category, candidate_category):
                score += 0.15

        # Style similarity
        target_style = target_metadata.get("style")
        candidate_style = candidate_metadata.get("style")

        if target_style and candidate_style:
            if target_style == candidate_style:
                score += 0.2

        # Size compatibility check
        candidate_compat = candidate_metadata.get("compatibility", {})
        min_size = candidate_compat.get("min_size", 6)
        max_size = candidate_compat.get("max_size", 24)

        if min_size <= target_size <= max_size:
            score += 0.1

        # Preferred sizes bonus
        preferred_sizes = candidate_compat.get("preferred_sizes", [])
        if target_size in preferred_sizes:
            score += 0.05

        return min(score, 1.0)  # Cap at 1.0

    def _record_performance_metric(self, operation: str, font_key: str, duration: float):
        """Record performance metric for monitoring."""
        if operation not in self.performance_stats:
            self.performance_stats[operation] = {}

        if font_key not in self.performance_stats[operation]:
            self.performance_stats[operation][font_key] = []

        self.performance_stats[operation][font_key].append(duration)

        # Keep only last 100 measurements per operation per font
        if len(self.performance_stats[operation][font_key]) > 100:
            self.performance_stats[operation][font_key] = self.performance_stats[operation][font_key][-100:]

    def _categories_similar(self, cat1: str, cat2: str) -> bool:
        """Check if two font categories are similar."""
        similar_groups = [
            {"pixel", "monospace"},
            {"serif", "display"},
            {"sans-serif"}
        ]

        for group in similar_groups:
            if cat1 in group and cat2 in group:
                return True

        return False
    
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
        """Discover available fonts in assets/fonts directory and auto-register them."""
        font_dirs = [
            "assets/fonts",
            "plugins/*/fonts",  # Plugin font directories
            "plugins/*/assets/fonts",
            "plugins/*/resources/fonts"
        ]

        discovered_count = 0

        for font_dir_pattern in font_dirs:
            if "*" in font_dir_pattern:
                # Handle glob patterns for plugin directories
                import glob
                matching_dirs = glob.glob(font_dir_pattern)
                for font_dir in matching_dirs:
                    discovered_count += self._scan_font_directory(font_dir)
            else:
                # Handle regular directories
                discovered_count += self._scan_font_directory(font_dir_pattern)

        logger.info(f"Auto-discovered {discovered_count} fonts")

    def _scan_font_directory(self, font_dir: str) -> int:
        """Scan a single directory for fonts and auto-register them."""
        if not os.path.exists(font_dir):
            return 0

        discovered_count = 0

        try:
            for root, dirs, files in os.walk(font_dir):
                for filename in files:
                    if self._is_font_file(filename):
                        font_path = os.path.join(root, filename)
                        family_name = self._extract_font_family(filename, font_path)

                        if family_name:
                            # Check if already registered
                            if family_name not in self.font_catalog:
                                self.font_catalog[family_name] = font_path

                                # Extract and store metadata
                                metadata = self._extract_font_metadata(filename, font_path)
                                if metadata:
                                    self.font_metadata[family_name] = metadata

                                logger.debug(f"Auto-registered font: {family_name} -> {font_path}")
                                discovered_count += 1
                            else:
                                logger.debug(f"Font {family_name} already registered, skipping")
        except Exception as e:
            logger.error(f"Error scanning font directory {font_dir}: {e}", exc_info=True)

        return discovered_count

    def _is_font_file(self, filename: str) -> bool:
        """Check if a file is a supported font format."""
        supported_extensions = {'.ttf', '.otf', '.bdf', '.pcf', '.woff', '.woff2'}
        return any(filename.lower().endswith(ext) for ext in supported_extensions)

    def _extract_font_family(self, filename: str, font_path: str) -> Optional[str]:
        """Extract font family name from filename and file content."""
        # Try to extract from filename first
        family_name = os.path.splitext(filename)[0].lower()
        family_name = family_name.replace('-', '_').replace(' ', '_')

        # Try to read font metadata if possible
        try:
            if filename.lower().endswith(('.ttf', '.otf')):
                # For TTF/OTF, we could use fonttools or similar to extract name
                # For now, use filename-based extraction
                pass
            elif filename.lower().endswith('.bdf'):
                # For BDF, try to read font properties
                family_name = self._extract_bdf_family(font_path) or family_name
        except Exception:
            # Fall back to filename-based extraction
            pass

        return family_name if family_name else None

    def _extract_bdf_family(self, font_path: str) -> Optional[str]:
        """Extract font family from BDF file properties."""
        try:
            with open(font_path, 'r', encoding='latin-1') as f:
                for line in f:
                    if line.startswith('FAMILY_NAME'):
                        family = line.split('"')[1] if '"' in line else line.split()[-1]
                        return family.lower().replace(' ', '_').replace('-', '_')
                    elif line.startswith('FONT '):
                        # Alternative: extract from FONT line
                        parts = line.split()
                        if len(parts) >= 2:
                            font_name = parts[1].replace('-', '_')
                            return font_name.lower()
        except Exception:
            pass
        return None

    def _extract_font_metadata(self, filename: str, font_path: str) -> Dict[str, Any]:
        """Extract comprehensive metadata from font file with intelligent analysis."""
        metadata = {
            "source": "auto_discovered",
            "file_path": font_path,
            "file_size": os.path.getsize(font_path),
            "extension": os.path.splitext(filename)[1].lower(),
            "filename": filename
        }

        # Analyze file content for detailed metadata
        file_analysis = self._analyze_font_file(font_path, filename)
        metadata.update(file_analysis)

        # Extract category based on filename patterns and file analysis
        lower_filename = filename.lower()
        if any(word in lower_filename for word in ['pixel', 'bitmap', '8bit', 'game']):
            metadata["category"] = "pixel"
        elif any(word in lower_filename for word in ['display', 'title', 'header']):
            metadata["category"] = "display"
        elif any(word in lower_filename for word in ['mono', 'fixed', 'typewriter']):
            metadata["category"] = "monospace"
        elif any(word in lower_filename for word in ['serif']):
            metadata["category"] = "serif"
        elif any(word in lower_filename for word in ['sans']):
            metadata["category"] = "sans-serif"
        else:
            # Use analysis-based category if available
            metadata["category"] = file_analysis.get("detected_category", "unknown")

        # Override with file analysis if more accurate
        if "detected_weight" in file_analysis:
            metadata["weight"] = file_analysis["detected_weight"]
        elif any(word in lower_filename for word in ['bold', 'heavy', 'black']):
            metadata["weight"] = "bold"
        elif any(word in lower_filename for word in ['light', 'thin']):
            metadata["weight"] = "light"
        else:
            metadata["weight"] = "regular"

        if "detected_style" in file_analysis:
            metadata["style"] = file_analysis["detected_style"]
        elif any(word in lower_filename for word in ['italic', 'oblique']):
            metadata["style"] = "italic"
        else:
            metadata["style"] = "regular"

        return metadata

    def _analyze_font_file(self, font_path: str, filename: str) -> Dict[str, Any]:
        """Analyze font file to extract detailed metadata."""
        analysis = {}

        try:
            if filename.lower().endswith(('.ttf', '.otf')):
                analysis = self._analyze_ttf_font(font_path)
            elif filename.lower().endswith('.bdf'):
                analysis = self._analyze_bdf_font(font_path)
            elif filename.lower().endswith('.pcf'):
                analysis = self._analyze_pcf_font(font_path)
            else:
                # Generic analysis based on file size and patterns
                analysis = self._generic_font_analysis(font_path, filename)
        except Exception as e:
            logger.debug(f"Error analyzing font file {font_path}: {e}")
            analysis = {"analysis_error": str(e)}

        return analysis

    def _analyze_ttf_font(self, font_path: str) -> Dict[str, Any]:
        """Analyze TTF/OTF font file."""
        analysis = {"format": "ttf"}

        try:
            # Basic file header analysis
            with open(font_path, 'rb') as f:
                header = f.read(64)

                # Check for TTF/OTF magic numbers
                if header.startswith(b'\x00\x01\x00\x00'):
                    analysis["format"] = "ttf"
                elif header.startswith(b'OTTO'):
                    analysis["format"] = "otf"
                elif b'true' in header.lower() or b'type' in header.lower():
                    analysis["format"] = "ttf"

                # Analyze file size for complexity hints
                file_size = os.path.getsize(font_path)
                if file_size < 10000:
                    analysis["complexity"] = "simple"
                elif file_size < 100000:
                    analysis["complexity"] = "moderate"
                else:
                    analysis["complexity"] = "complex"

                # Try to detect character set by reading cmap table offset
                f.seek(0)
                data = f.read()
                cmap_offset = data.find(b'cmap')
                if cmap_offset > 0:
                    analysis["has_cmap"] = True

        except Exception as e:
            logger.debug(f"TTF analysis error: {e}")

        return analysis

    def _analyze_bdf_font(self, font_path: str) -> Dict[str, Any]:
        """Analyze BDF font file."""
        analysis = {"format": "bdf"}

        try:
            with open(font_path, 'r', encoding='latin-1') as f:
                lines = f.readlines()

                # Extract BDF properties
                for line in lines[:50]:  # Check first 50 lines for properties
                    line = line.strip()
                    if line.startswith('FONT '):
                        font_name = line.split()[1]
                        analysis["bdf_font_name"] = font_name
                    elif line.startswith('SIZE '):
                        parts = line.split()
                        if len(parts) >= 3:
                            analysis["bdf_point_size"] = int(parts[1])
                            analysis["bdf_resolution_x"] = int(parts[2])
                            analysis["bdf_resolution_y"] = int(parts[3])
                    elif line.startswith('FONTBOUNDINGBOX '):
                        parts = line.split()
                        if len(parts) >= 5:
                            analysis["bdf_width"] = int(parts[1])
                            analysis["bdf_height"] = int(parts[2])
                            analysis["bdf_x_offset"] = int(parts[3])
                            analysis["bdf_y_offset"] = int(parts[4])
                    elif line.startswith('CHARS '):
                        analysis["bdf_char_count"] = int(line.split()[1])

                # Analyze character set
                char_lines = [line for line in lines if line.startswith('STARTCHAR ')]
                if char_lines:
                    char_names = [line.split()[1] for line in char_lines[:20]]  # Sample first 20
                    analysis["sample_chars"] = char_names

                    # Detect if it's ASCII-only or extended
                    ascii_only = all(name in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789' or len(name) == 1 for name in char_names)
                    analysis["ascii_only"] = ascii_only

        except Exception as e:
            logger.debug(f"BDF analysis error: {e}")

        return analysis

    def _analyze_pcf_font(self, font_path: str) -> Dict[str, Any]:
        """Analyze PCF font file."""
        analysis = {"format": "pcf"}

        try:
            # PCF files are binary, basic analysis
            file_size = os.path.getsize(font_path)

            if file_size < 5000:
                analysis["complexity"] = "simple"
            elif file_size < 50000:
                analysis["complexity"] = "moderate"
            else:
                analysis["complexity"] = "complex"

        except Exception as e:
            logger.debug(f"PCF analysis error: {e}")

        return analysis

    def _generic_font_analysis(self, font_path: str, filename: str) -> Dict[str, Any]:
        """Generic font file analysis based on patterns."""
        analysis = {"format": "unknown"}

        # Size-based complexity analysis
        file_size = os.path.getsize(font_path)
        if file_size < 10000:
            analysis["complexity"] = "simple"
        elif file_size < 100000:
            analysis["complexity"] = "moderate"
        else:
            analysis["complexity"] = "complex"

        # Try to detect format from file header
        try:
            with open(font_path, 'rb') as f:
                header = f.read(16)

                if header.startswith(b'\x00\x01\x00\x00'):
                    analysis["format"] = "ttf"
                elif header.startswith(b'OTTO'):
                    analysis["format"] = "otf"
                elif b'STARTFONT' in header:
                    analysis["format"] = "bdf"
                elif header[0:4] == b'\x01\x66\x63\x70':  # PCF magic
                    analysis["format"] = "pcf"

        except Exception:
            pass

        return analysis

    def detect_font_format(self, font_path: str) -> str:
        """Dynamically detect font format from file content."""
        try:
            analysis = self._analyze_font_file(font_path, os.path.basename(font_path))
            return analysis.get("format", "unknown")
        except Exception:
            return "unknown"

    def get_font_capabilities(self, font_path: str) -> Dict[str, Any]:
        """Get comprehensive font capabilities and metadata."""
        try:
            filename = os.path.basename(font_path)
            analysis = self._analyze_font_file(font_path, filename)
            metadata = self._extract_font_metadata(filename, font_path)

            return {
                "metadata": metadata,
                "analysis": analysis,
                "capabilities": self._extract_capabilities(analysis, metadata)
            }
        except Exception as e:
            logger.error(f"Error getting font capabilities: {e}")
            return {"error": str(e)}

    def _extract_capabilities(self, analysis: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Extract font capabilities from analysis and metadata."""
        capabilities = {}

        # Size capabilities
        if "bdf_width" in analysis and "bdf_height" in analysis:
            capabilities["fixed_size"] = True
            capabilities["width"] = analysis["bdf_width"]
            capabilities["height"] = analysis["bdf_height"]
        else:
            capabilities["fixed_size"] = False

        # Character set capabilities
        if "ascii_only" in analysis:
            capabilities["ascii_only"] = analysis["ascii_only"]
        elif "sample_chars" in analysis:
            sample_chars = analysis["sample_chars"]
            capabilities["has_basic_ascii"] = any(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789' for c in sample_chars)

        # Complexity capabilities
        complexity = analysis.get("complexity", "unknown")
        capabilities["rendering_complexity"] = complexity

        # Performance hints
        if complexity == "simple":
            capabilities["performance_hint"] = "fast"
        elif complexity == "complex":
            capabilities["performance_hint"] = "slow"
        else:
            capabilities["performance_hint"] = "moderate"

        return capabilities

    def enable_continuous_discovery(self, scan_interval: int = 300):
        """Enable continuous font discovery in the background."""
        import threading

        def discovery_loop():
            while True:
                try:
                    self._continuous_font_discovery()
                    time.sleep(scan_interval)
                except Exception as e:
                    logger.error(f"Error in continuous font discovery: {e}")
                    time.sleep(scan_interval)

        self.discovery_thread = threading.Thread(target=discovery_loop, daemon=True)
        self.discovery_thread.start()
        logger.info(f"Continuous font discovery enabled (interval: {scan_interval}s)")

    def _continuous_font_discovery(self):
        """Perform continuous font discovery scan."""
        before_count = len(self.font_catalog)

        # Re-scan all directories
        self._discover_fonts()

        after_count = len(self.font_catalog)
        new_fonts = after_count - before_count

        if new_fonts > 0:
            logger.info(f"Continuous discovery found {new_fonts} new fonts")
            # Clear caches to pick up new fonts
            self.clear_cache()

    def watch_plugin_directories(self):
        """Watch plugin directories for new font files."""
        try:
            import watchdog
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class FontFileHandler(FileSystemEventHandler):
                def __init__(self, font_manager):
                    self.font_manager = font_manager

                def on_created(self, event):
                    if not event.is_directory and self.font_manager._is_font_file(os.path.basename(event.src_path)):
                        logger.info(f"New font file detected: {event.src_path}")
                        self.font_manager._scan_font_directory(os.path.dirname(event.src_path))

                def on_deleted(self, event):
                    if not event.is_directory:
                        # Remove font if file was deleted
                        filename = os.path.basename(event.src_path)
                        family_name = self.font_manager._extract_font_family(filename, event.src_path)
                        if family_name and family_name in self.font_manager.font_catalog:
                            del self.font_manager.font_catalog[family_name]
                            logger.info(f"Removed font due to file deletion: {family_name}")

            observer = Observer()
            self.font_observer = observer

            # Watch main font directories
            font_dirs = ["assets/fonts"]
            for font_dir in font_dirs:
                if os.path.exists(font_dir):
                    observer.schedule(FontFileHandler(self), font_dir, recursive=True)

            # Watch plugin directories
            plugins_dir = "plugins"
            if os.path.exists(plugins_dir):
                observer.schedule(FontFileHandler(self), plugins_dir, recursive=True)

            observer.start()
            logger.info("Font directory watching enabled")

        except ImportError:
            logger.warning("watchdog not available, font directory watching disabled")
        except Exception as e:
            logger.error(f"Error setting up font directory watching: {e}")

    def stop_watching(self):
        """Stop watching font directories."""
        if hasattr(self, 'font_observer'):
            self.font_observer.stop()
            self.font_observer.join()
            logger.info("Font directory watching stopped")
    
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
    
    def _get_smart_defaults(self, element_key: str) -> Optional[Dict[str, str]]:
        """
        Get smart default family and size token based on dynamically generated baseline mapping.

        This system dynamically generates font defaults based on:
        1. Available managers and plugins
        2. Pattern-based element naming
        3. Intelligent fallback strategies

        Returns:
            Dict with 'family' and 'size_token' keys, or None if no defaults found
        """
        # Generate dynamic baseline defaults instead of hardcoded ones
        baseline_defaults = self._generate_dynamic_baseline_defaults()
        return baseline_defaults.get(element_key)

    def _generate_dynamic_baseline_defaults(self) -> Dict[str, Dict[str, str]]:
        """
        Generate baseline font defaults dynamically based on available components.

        This replaces the hardcoded baseline_defaults with a dynamic system that:
        1. Scans for available managers and plugins
        2. Generates defaults based on naming patterns
        3. Allows runtime updates when plugins are loaded/unloaded
        """
        baseline_defaults = {}

        # Get available sports managers dynamically
        available_sports = self._discover_available_sports_managers()

        # Generate defaults for each sport and display mode
        for sport in available_sports:
            self._add_sport_defaults(baseline_defaults, sport)

        # Add general-purpose defaults
        self._add_general_defaults(baseline_defaults)

        # Allow plugins to register their own defaults
        self._add_plugin_defaults(baseline_defaults)

        return baseline_defaults

    def _discover_available_sports_managers(self) -> List[str]:
        """Dynamically discover available sports managers."""
        available_sports = []

        # Check which sports managers are actually available in the system
        sports_patterns = [
            'nfl', 'nhl', 'nba', 'mlb', 'soccer', 'ncaa_fb', 'ncaa_baseball',
            'ncaam_basketball', 'ncaaw_basketball', 'ncaam_hockey', 'ncaaw_hockey',
            'wnba', 'milb'
        ]

        # Check if manager classes exist (this is a simplified check)
        # In a real implementation, you'd check the actual manager instances
        for sport in sports_patterns:
            # Check if the sport appears in the display controller or config
            # This is a simplified approach - in practice you'd check actual manager instances
            if self._sport_manager_exists(sport):
                available_sports.append(sport)

        return available_sports

    def _sport_manager_exists(self, sport: str) -> bool:
        """Check if a sport manager exists in the system."""
        # This is a simplified check - in practice you'd check actual manager instances
        # For now, we'll assume common sports are available
        common_sports = ['nfl', 'nhl', 'nba', 'mlb', 'soccer']
        return sport in common_sports

    def _add_sport_defaults(self, baseline_defaults: Dict[str, Dict[str, str]], sport: str):
        """Add font defaults for a specific sport."""
        # Map sport names to appropriate font families
        sport_font_map = {
            'nfl': 'press_start',
            'nhl': 'press_start',
            'nba': 'press_start',
            'mlb': 'press_start',
            'soccer': 'press_start',
            'ncaa_fb': 'press_start',
            'ncaa_baseball': 'press_start',
            'ncaam_basketball': 'press_start',
            'ncaaw_basketball': 'press_start',
            'ncaam_hockey': 'press_start',
            'ncaaw_hockey': 'press_start',
            'wnba': 'press_start',
            'milb': 'press_start'
        }

        default_family = sport_font_map.get(sport, 'press_start')

        # Generate defaults for different display modes and elements
        display_modes = ['live', 'recent', 'upcoming']
        elements = ['score', 'time', 'team', 'status', 'record', 'odds', 'detail']

        for mode in display_modes:
            for element in elements:
                element_key = f"{sport}.{mode}.{element}"

                # Special cases for soccer (smaller team fonts)
                if sport == 'soccer' and element == 'team':
                    baseline_defaults[element_key] = {'family': default_family, 'size_token': 'xs'}
                # Special cases for certain elements
                elif element in ['status', 'record', 'odds', 'detail']:
                    baseline_defaults[element_key] = {'family': 'four_by_six', 'size_token': 'xs'}
                # Default cases
                elif element == 'score':
                    size_token = 'lg' if mode in ['recent', 'upcoming'] else 'md'
                    baseline_defaults[element_key] = {'family': default_family, 'size_token': size_token}
                elif element == 'time':
                    baseline_defaults[element_key] = {'family': default_family, 'size_token': 'sm'}
                else:  # team
                    baseline_defaults[element_key] = {'family': default_family, 'size_token': 'sm'}

    def _add_general_defaults(self, baseline_defaults: Dict[str, Dict[str, str]]):
        """Add general-purpose font defaults."""
        general_defaults = {
            # Clock elements
            'clock.time': {'family': 'press_start', 'size_token': 'sm'},
            'clock.ampm': {'family': 'press_start', 'size_token': 'sm'},
            'clock.weekday': {'family': 'press_start', 'size_token': 'sm'},
            'clock.date': {'family': 'press_start', 'size_token': 'sm'},

            # Calendar elements
            'calendar.datetime': {'family': 'press_start', 'size_token': 'sm'},
            'calendar.title': {'family': 'press_start', 'size_token': 'sm'},

            # Leaderboard elements
            'leaderboard.title': {'family': 'press_start', 'size_token': 'lg'},
            'leaderboard.rank': {'family': 'four_by_six', 'size_token': 'xs'},
            'leaderboard.team': {'family': 'press_start', 'size_token': 'sm'},
            'leaderboard.record': {'family': 'press_start', 'size_token': 'sm'},
            'leaderboard.medium': {'family': 'press_start', 'size_token': 'md'},
            'leaderboard.large': {'family': 'press_start', 'size_token': 'lg'},
            'leaderboard.xlarge': {'family': 'press_start', 'size_token': 'xl'},

            # Weather elements
            'weather.condition': {'family': 'press_start', 'size_token': 'sm'},
            'weather.temperature': {'family': 'press_start', 'size_token': 'sm'},
            'weather.high_low': {'family': 'four_by_six', 'size_token': 'xs'},
            'weather.uv': {'family': 'four_by_six', 'size_token': 'xs'},
            'weather.humidity': {'family': 'four_by_six', 'size_token': 'xs'},
            'weather.wind': {'family': 'four_by_six', 'size_token': 'xs'},
            'weather.hourly.time': {'family': 'four_by_six', 'size_token': 'xs'},
            'weather.hourly.temp': {'family': 'four_by_six', 'size_token': 'xs'},
            'weather.daily.day': {'family': 'four_by_six', 'size_token': 'xs'},
            'weather.daily.temp': {'family': 'four_by_six', 'size_token': 'xs'},

            # Stock elements
            'stock.symbol': {'family': 'press_start', 'size_token': 'sm'},
            'stock.price': {'family': 'press_start', 'size_token': 'sm'},
            'stock.change': {'family': 'press_start', 'size_token': 'sm'},
            'stock.news.title': {'family': 'press_start', 'size_token': 'sm'},
            'stock.news.summary': {'family': 'four_by_six', 'size_token': 'xs'},

            # Music elements
            'music.artist': {'family': 'press_start', 'size_token': 'sm'},
            'music.title': {'family': 'press_start', 'size_token': 'lg'},
            'music.album': {'family': 'press_start', 'size_token': 'sm'},
        }

        baseline_defaults.update(general_defaults)

    def _add_plugin_defaults(self, baseline_defaults: Dict[str, Dict[str, str]]):
        """Allow plugins to register their own font defaults."""
        # Add plugin defaults if they exist
        if hasattr(self, '_plugin_defaults'):
            for plugin_defaults in self._plugin_defaults.values():
                baseline_defaults.update(plugin_defaults)

    def register_plugin_font_defaults(self, plugin_id: str, defaults: Dict[str, Dict[str, str]]):
        """Allow plugins to register their font defaults."""
        # Store plugin defaults for dynamic generation
        if not hasattr(self, '_plugin_defaults'):
            self._plugin_defaults = {}

        self._plugin_defaults[plugin_id] = defaults

        # Regenerate baseline defaults to include plugin defaults
        # Note: In a full implementation, you'd want to cache this or regenerate on demand
        logger.info(f"Registered font defaults for plugin {plugin_id}")

    def unregister_plugin_font_defaults(self, plugin_id: str):
        """Remove font defaults for a plugin."""
        if hasattr(self, '_plugin_defaults') and plugin_id in self._plugin_defaults:
            del self._plugin_defaults[plugin_id]
            logger.info(f"Unregistered font defaults for plugin {plugin_id}")

    def refresh_dynamic_defaults(self):
        """Refresh the dynamic baseline defaults (call when plugins are loaded/unloaded)."""
        # Clear any cached dynamic defaults
        if hasattr(self, '_cached_dynamic_defaults'):
            delattr(self, '_cached_dynamic_defaults')

        # Force regeneration of defaults
        _ = self._generate_dynamic_baseline_defaults()

    def _get_smart_defaults(self, element_key: str) -> Optional[Dict[str, str]]:
        """
        Get smart default family and size token based on dynamically generated baseline mapping.

        This system dynamically generates font defaults based on:
        1. Available managers and plugins
        2. Pattern-based element naming
        3. Intelligent fallback strategies

        Returns:
            Dict with 'family' and 'size_token' keys, or None if no defaults found
        """
        # Generate dynamic baseline defaults instead of hardcoded ones
        baseline_defaults = self._generate_dynamic_baseline_defaults()
        
    
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
                if not element_config:  # No overrides at all, try smart defaults
                    smart_defaults = self._get_smart_defaults(element_key)
                    if smart_defaults:
                        if 'family' in smart_defaults and not resolved_family:
                            resolved_family = smart_defaults['family']
                        if 'size_token' in smart_defaults and not resolved_size:
                            size_token = smart_defaults['size_token']
            
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
                self.performance_stats["cache_hits"] += 1
                return self.font_cache[cache_key]
            else:
                self.performance_stats["cache_misses"] += 1
            
            # Load font with performance monitoring
            start_time = time.time()
            font = self._load_font(resolved_family, resolved_size)
            load_time = time.time() - start_time

            self.font_cache[cache_key] = font

            # Record performance
            self._record_performance_metric("font_load", cache_key, load_time)

            return font
            
        except Exception as e:
            logger.error(f"Error resolving font for element_key={element_key}, family={family}, size={size_px}, size_token={size_token}: {e}", exc_info=True)
            return self._get_fallback_font()
    
    def _load_font(self, family: str, size: int) -> Union[ImageFont.FreeTypeFont, freetype.Face]:
        """Load a specific font by family and size, with plugin support."""
        # Handle plugin namespaced fonts (plugin_id::family)
        if "::" in family:
            return self._load_plugin_font(family, size)

        # Handle regular fonts
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

    def _load_plugin_font(self, namespaced_family: str, size: int) -> Union[ImageFont.FreeTypeFont, freetype.Face]:
        """Load a font from a plugin namespace."""
        try:
            plugin_id, family = namespaced_family.split("::", 1)

            # Find the font in plugin catalogs
            if plugin_id not in self.plugin_font_catalogs or namespaced_family not in self.plugin_font_catalogs[plugin_id]:
                logger.warning(f"Plugin font '{namespaced_family}' not found in plugin catalogs")
                return self._get_fallback_font()

            font_path = self.plugin_font_catalogs[plugin_id][namespaced_family]

            # Check dependencies before loading
            if not self._check_font_dependencies(namespaced_family):
                logger.error(f"Font dependencies not satisfied for {namespaced_family}")
                return self._get_fallback_font()

            try:
                # Load font with performance monitoring
                start_time = time.time()
                if font_path.lower().endswith('.ttf'):
                    # Load TTF with PIL
                    font = ImageFont.truetype(font_path, size)
                    load_time = time.time() - start_time
                    logger.debug(f"Loaded plugin TTF font: {namespaced_family} ({size}px) from {font_path}")
                    self._record_performance_metric("font_load", f"{namespaced_family}_{size}", load_time)
                    return font
                elif font_path.lower().endswith('.bdf'):
                    # Load BDF with freetype
                    if freetype is None:
                        logger.error("freetype not available for BDF fonts")
                        return self._get_fallback_font()

                    face = freetype.Face(font_path)
                    face.set_pixel_sizes(0, size)
                    load_time = time.time() - start_time
                    logger.debug(f"Loaded plugin BDF font: {namespaced_family} ({size}px) from {font_path}")
                    self._record_performance_metric("font_load", f"{namespaced_family}_{size}", load_time)
                    return face
                else:
                    logger.warning(f"Unsupported plugin font type: {font_path}")
                    return self._get_fallback_font()

            except Exception as e:
                logger.error(f"Failed to load plugin font {namespaced_family} ({size}px) from {font_path}: {e}", exc_info=True)
                return self._get_fallback_font()

        except Exception as e:
            logger.error(f"Error loading plugin font {namespaced_family}: {e}")
            return self._get_fallback_font()

    def _check_font_dependencies(self, font_key: str) -> bool:
        """Check if all font dependencies are satisfied."""
        if font_key not in self.font_dependencies:
            return True  # No dependencies

        dependencies = self.font_dependencies[font_key]
        for dep in dependencies:
            # Check if dependency exists in any catalog
            found = False
            if dep in self.font_catalog:
                found = True
            else:
                # Check plugin catalogs
                for plugin_catalog in self.plugin_font_catalogs.values():
                    if dep in plugin_catalog:
                        found = True
                        break

            if not found:
                logger.warning(f"Font dependency '{dep}' not found for font '{font_key}'")
                return False

        return True
    
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

    def clear_plugin_cache(self, plugin_id: Optional[str] = None):
        """Clear font cache for specific plugin or all plugins."""
        if plugin_id:
            self._clear_plugin_font_cache(plugin_id)
            logger.info(f"Plugin font cache cleared for {plugin_id}")
        else:
            # Clear all plugin font caches
            for pid in list(self.plugin_font_catalogs.keys()):
                self._clear_plugin_font_cache(pid)
            logger.info("All plugin font caches cleared")
    
    def reload_config(self, config: Dict[str, Any]):
        """Reload font configuration."""
        self.config = config
        self.fonts_config = config.get("fonts", {})

        # Clear all caches including plugin caches
        self.clear_cache()
        self.clear_plugin_cache()

        # Reinitialize fonts
        self._initialize_fonts()
        logger.info("Font configuration reloaded")

    def hot_reload_font_config(self, font_updates: Dict[str, Any]) -> bool:
        """
        Hot-reload font configuration without full restart.

        Args:
            font_updates: Partial font configuration updates

        Returns:
            True if reload successful, False otherwise
        """
        try:
            # Merge updates with existing config
            updated_fonts_config = self.fonts_config.copy()
            self._deep_merge(updated_fonts_config, font_updates)

            # Update internal config
            self.fonts_config = updated_fonts_config

            # Clear relevant caches
            self._clear_affected_caches(font_updates)

            # Reinitialize affected parts
            self._hot_reinitialize_fonts(font_updates)

            logger.info("Font configuration hot-reloaded successfully")
            return True

        except Exception as e:
            logger.error(f"Error hot-reloading font config: {e}")
            return False

    def _deep_merge(self, base: Dict[str, Any], updates: Dict[str, Any]):
        """Deep merge dictionaries for configuration updates."""
        for key, value in updates.items():
            if isinstance(value, dict) and key in base and isinstance(base[key], dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def _clear_affected_caches(self, updates: Dict[str, Any]):
        """Clear caches that are affected by configuration changes."""
        # Clear main caches if core settings changed
        if any(key in updates for key in ["families", "tokens", "defaults"]):
            self.clear_cache()

        # Clear plugin caches if plugin settings changed
        if "plugins" in updates:
            self.clear_plugin_cache()

    def _hot_reinitialize_fonts(self, updates: Dict[str, Any]):
        """Reinitialize fonts based on specific updates."""
        # If families changed, rediscover fonts
        if "families" in updates:
            self._discover_fonts()

        # If tokens changed, update size mappings
        if "tokens" in updates:
            # No specific reinitialization needed for tokens, they're used dynamically
            pass

        # If defaults changed, update default settings
        if "defaults" in updates:
            # No specific reinitialization needed for defaults, they're used dynamically
            pass

        # If overrides changed, clear metrics cache for affected elements
        if "overrides" in updates:
            # Clear metrics cache for elements that might have new overrides
            affected_elements = set()
            for element_key in updates["overrides"].keys():
                # Remove cached metrics for this element
                keys_to_remove = [k for k in self.metrics_cache.keys() if element_key in k]
                for key in keys_to_remove:
                    del self.metrics_cache[key]

    def add_font_at_runtime(self, family: str, font_path: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Add a font to the catalog at runtime.

        Args:
            family: Font family name
            font_path: Path to font file
            metadata: Optional font metadata

        Returns:
            True if font added successfully, False otherwise
        """
        try:
            if not os.path.exists(font_path):
                logger.error(f"Font file not found: {font_path}")
                return False

            # Check if font already exists
            if family in self.font_catalog:
                logger.warning(f"Font family '{family}' already exists, overwriting")

            # Add to catalog
            self.font_catalog[family] = font_path

            # Add metadata if provided
            if metadata:
                self.font_metadata[family] = metadata

            # Clear cache for this font
            cache_keys_to_remove = [k for k in self.font_cache.keys() if k.startswith(f"{family}_")]
            for key in cache_keys_to_remove:
                del self.font_cache[key]

            logger.info(f"Added font '{family}' at runtime: {font_path}")
            return True

        except Exception as e:
            logger.error(f"Error adding font at runtime: {e}")
            return False

    def remove_font_at_runtime(self, family: str) -> bool:
        """
        Remove a font from the catalog at runtime.

        Args:
            family: Font family name to remove

        Returns:
            True if font removed successfully, False otherwise
        """
        try:
            if family not in self.font_catalog:
                logger.warning(f"Font family '{family}' not found in catalog")
                return False

            # Remove from catalog
            del self.font_catalog[family]

            # Remove metadata
            if family in self.font_metadata:
                del self.font_metadata[family]

            # Clear cache for this font
            cache_keys_to_remove = [k for k in self.font_cache.keys() if k.startswith(f"{family}_")]
            for key in cache_keys_to_remove:
                del self.font_cache[key]

            # Clear metrics cache for this font
            metrics_keys_to_remove = [k for k in self.metrics_cache.keys() if family in k]
            for key in metrics_keys_to_remove:
                del self.metrics_cache[key]

            logger.info(f"Removed font '{family}' at runtime")
            return True

        except Exception as e:
            logger.error(f"Error removing font at runtime: {e}")
            return False

    def update_font_metadata(self, family: str, metadata: Dict[str, Any]) -> bool:
        """
        Update metadata for a font at runtime.

        Args:
            family: Font family name
            metadata: New metadata to merge

        Returns:
            True if metadata updated successfully, False otherwise
        """
        try:
            if family not in self.font_metadata:
                self.font_metadata[family] = {}

            # Merge new metadata
            self.font_metadata[family].update(metadata)

            logger.info(f"Updated metadata for font '{family}'")
            return True

        except Exception as e:
            logger.error(f"Error updating font metadata: {e}")
            return False

    def get_font_statistics(self) -> Dict[str, Any]:
        """Get statistics about the current font system."""
        return {
            "total_fonts": len(self.font_catalog),
            "plugin_fonts": sum(len(catalog) for catalog in self.plugin_font_catalogs.values()),
            "cached_fonts": len(self.font_cache),
            "cached_metrics": len(self.metrics_cache),
            "loaded_plugins": len(self.plugin_fonts),
            "font_metadata_count": len(self.font_metadata)
        }

    # Adaptive Font Sizing System

    def calculate_optimal_font_size(self, text: str, font_family: str, max_width: int,
                                   max_height: int, min_size: int = 6, max_size: int = 24) -> int:
        """
        Calculate optimal font size for given text and constraints.

        Args:
            text: Text to measure
            font_family: Font family to use
            max_width: Maximum width in pixels
            max_height: Maximum height in pixels
            min_size: Minimum font size to consider
            max_size: Maximum font size to consider

        Returns:
            Optimal font size in pixels
        """
        if not text:
            return min_size

        best_size = min_size
        best_efficiency = 0

        # Try sizes from largest to smallest for better readability
        for size in range(max_size, min_size - 1, -1):
            try:
                # Get font for this size
                font = self.resolve(family=font_family, size_px=size)

                # Measure text
                width, height, baseline = self.measure_text(text, font)

                # Check if it fits
                if width <= max_width and height <= max_height:
                    # Calculate efficiency (how much space is used)
                    width_efficiency = width / max_width
                    height_efficiency = height / max_height
                    efficiency = min(width_efficiency, height_efficiency)

                    # Prefer sizes that use more space (better readability)
                    if efficiency > best_efficiency:
                        best_efficiency = efficiency
                        best_size = size

            except Exception:
                continue

        return best_size if best_size >= min_size else min_size

    def adaptive_font_resolution(self, *, text: str, element_key: Optional[str] = None,
                               family: Optional[str] = None, max_width: int, max_height: int,
                               min_size: int = 6, max_size: int = 24, plugin_id: Optional[str] = None):
        """
        Resolve font with adaptive sizing based on content and constraints.

        Args:
            text: Text content to optimize for
            element_key: Element key for smart defaults
            family: Font family name
            max_width: Maximum available width
            max_height: Maximum available height
            min_size: Minimum acceptable size
            max_size: Maximum acceptable size
            plugin_id: Plugin context

        Returns:
            Font object with optimal size
        """
        # First try to resolve with element-specific logic
        if element_key:
            try:
                return self.resolve_font_with_plugin_support(
                    element_key=element_key, family=family, plugin_id=plugin_id
                )
            except Exception:
                pass

        # If no element key or resolution failed, use adaptive sizing
        optimal_size = self.calculate_optimal_font_size(
            text, family or "press_start", max_width, max_height, min_size, max_size
        )

        return self.resolve_font_with_plugin_support(
            family=family, size_px=optimal_size, plugin_id=plugin_id
        )

    def get_text_fitting_info(self, text: str, font_family: str, max_width: int,
                             max_height: int, target_size: int = 12) -> Dict[str, Any]:
        """
        Get detailed information about how text fits at different sizes.

        Returns:
            Dictionary with size recommendations and fitting analysis
        """
        info = {
            "text": text,
            "font_family": font_family,
            "max_width": max_width,
            "max_height": max_height,
            "target_size": target_size,
            "recommendations": [],
            "best_fit": None
        }

        for size in range(6, 25):
            try:
                font = self.resolve(family=font_family, size_px=size)
                width, height, baseline = self.measure_text(text, font)

                fits_width = width <= max_width
                fits_height = height <= max_height
                fits = fits_width and fits_height

                recommendation = {
                    "size": size,
                    "width": width,
                    "height": height,
                    "fits": fits,
                    "fits_width": fits_width,
                    "fits_height": fits_height,
                    "efficiency": min(width/max_width, height/max_height) if fits else 0
                }

                info["recommendations"].append(recommendation)

                if fits and (not info["best_fit"] or recommendation["efficiency"] > info["best_fit"]["efficiency"]):
                    info["best_fit"] = recommendation

            except Exception as e:
                info["recommendations"].append({
                    "size": size,
                    "error": str(e)
                })

        return info

    def auto_size_text_for_display(self, text: str, display_width: int, display_height: int,
                                  font_family: str = "press_start", padding: int = 4) -> Dict[str, Any]:
        """
        Automatically determine optimal text sizing for display constraints.

        Args:
            text: Text to display
            display_width: Available display width
            display_height: Available display height
            font_family: Font family to use
            padding: Padding around text

        Returns:
            Dictionary with sizing recommendations and layout info
        """
        # Account for padding
        available_width = display_width - (padding * 2)
        available_height = display_height - (padding * 2)

        if available_width <= 0 or available_height <= 0:
            return {"error": "Insufficient space for text"}

        # Get fitting analysis
        fitting_info = self.get_text_fitting_info(
            text, font_family, available_width, available_height
        )

        if not fitting_info.get("best_fit"):
            return {"error": "No suitable font size found"}

        best_fit = fitting_info["best_fit"]

        # Calculate layout position for centering
        center_x = display_width // 2
        center_y = display_height // 2

        text_x = center_x - (best_fit["width"] // 2)
        text_y = center_y - (best_fit["height"] // 2)

        return {
            "optimal_size": best_fit["size"],
            "text_width": best_fit["width"],
            "text_height": best_fit["height"],
            "position": (text_x, text_y),
            "padding": padding,
            "fitting_info": fitting_info,
            "layout": {
                "centered": True,
                "horizontal_alignment": "center",
                "vertical_alignment": "center"
            }
        }

    def multi_line_text_sizing(self, lines: List[str], font_family: str, max_width: int,
                              max_height: int, line_spacing: int = 2) -> Dict[str, Any]:
        """
        Calculate optimal sizing for multi-line text.

        Args:
            lines: List of text lines
            font_family: Font family to use
            max_width: Maximum width per line
            max_height: Maximum total height
            line_spacing: Pixels between lines

        Returns:
            Dictionary with sizing recommendations
        """
        if not lines:
            return {"error": "No text lines provided"}

        # Find optimal size that works for all lines
        optimal_size = 6
        for size in range(6, 25):
            try:
                font = self.resolve(family=font_family, size_px=size)

                # Check if all lines fit
                total_height = 0
                max_line_width = 0

                for line in lines:
                    width, height, baseline = self.measure_text(line, font)
                    max_line_width = max(max_line_width, width)
                    total_height += height

                # Add line spacing
                total_height += line_spacing * (len(lines) - 1)

                if max_line_width <= max_width and total_height <= max_height:
                    optimal_size = size
                else:
                    break  # Stop when it no longer fits

            except Exception:
                continue

        if optimal_size == 6:
            return {"error": "Text too large for available space"}

        # Calculate final dimensions
        font = self.resolve(family=font_family, size_px=optimal_size)
        total_height = sum(self.measure_text(line, font)[1] for line in lines)
        total_height += line_spacing * (len(lines) - 1)
        max_line_width = max(self.measure_text(line, font)[0] for line in lines)

        return {
            "optimal_size": optimal_size,
            "total_width": max_line_width,
            "total_height": total_height,
            "line_count": len(lines),
            "line_spacing": line_spacing,
            "fits": max_line_width <= max_width and total_height <= max_height
        }

    # Performance Monitoring and Analytics

    def get_performance_statistics(self) -> Dict[str, Any]:
        """Get comprehensive performance statistics."""
        uptime = time.time() - self.performance_stats["start_time"]

        # Calculate cache hit rate
        total_cache_operations = self.performance_stats["cache_hits"] + self.performance_stats["cache_misses"]
        cache_hit_rate = (self.performance_stats["cache_hits"] / total_cache_operations * 100) if total_cache_operations > 0 else 0

        # Calculate average load times
        avg_load_times = {}
        for font_key, times in self.performance_stats["font_load_times"].items():
            if times:
                avg_load_times[font_key] = sum(times) / len(times)

        # Calculate failure rate
        failure_rate = (self.performance_stats["failed_loads"] / max(1, self.performance_stats["total_renders"])) * 100

        return {
            "uptime_seconds": uptime,
            "cache_statistics": {
                "hits": self.performance_stats["cache_hits"],
                "misses": self.performance_stats["cache_misses"],
                "hit_rate_percent": cache_hit_rate,
                "total_operations": total_cache_operations
            },
            "font_loading": {
                "average_load_times": avg_load_times,
                "total_loads": len(self.font_cache),
                "failed_loads": self.performance_stats["failed_loads"],
                "failure_rate_percent": failure_rate
            },
            "rendering": {
                "total_renders": self.performance_stats["total_renders"],
                "average_render_times": self._calculate_avg_render_times()
            },
            "system": {
                "total_fonts": len(self.font_catalog),
                "plugin_fonts": sum(len(catalog) for catalog in self.plugin_font_catalogs.values()),
                "cached_fonts": len(self.font_cache),
                "cached_metrics": len(self.metrics_cache)
            }
        }

    def _calculate_avg_render_times(self) -> Dict[str, float]:
        """Calculate average render times per font."""
        avg_times = {}
        for font_key, times in self.performance_stats["render_times"].items():
            if times:
                avg_times[font_key] = sum(times) / len(times)
        return avg_times

    def monitor_font_loading(self, font_key: str) -> Dict[str, Any]:
        """Get detailed performance info for a specific font."""
        load_times = self.performance_stats["font_load_times"].get(font_key, [])
        render_times = self.performance_stats["render_times"].get(font_key, [])

        return {
            "font_key": font_key,
            "load_performance": {
                "count": len(load_times),
                "average_time": sum(load_times) / len(load_times) if load_times else 0,
                "min_time": min(load_times) if load_times else 0,
                "max_time": max(load_times) if load_times else 0
            },
            "render_performance": {
                "count": len(render_times),
                "average_time": sum(render_times) / len(render_times) if render_times else 0,
                "min_time": min(render_times) if render_times else 0,
                "max_time": max(render_times) if render_times else 0
            },
            "cache_status": font_key in self.font_cache
        }

    def optimize_performance(self) -> Dict[str, Any]:
        """Analyze performance and suggest optimizations."""
        stats = self.get_performance_statistics()
        recommendations = []

        # Cache optimization recommendations
        cache_hit_rate = stats["cache_statistics"]["hit_rate_percent"]
        if cache_hit_rate < 80:
            recommendations.append({
                "type": "cache",
                "priority": "high",
                "message": f"Low cache hit rate ({cache_hit_rate:.1f}%). Consider increasing cache size or optimizing font usage patterns."
            })

        # Font loading optimization
        avg_load_time = sum(stats["font_loading"]["average_load_times"].values()) / len(stats["font_loading"]["average_load_times"]) if stats["font_loading"]["average_load_times"] else 0
        if avg_load_time > 0.1:  # 100ms threshold
            recommendations.append({
                "type": "loading",
                "priority": "medium",
                "message": f"Slow font loading (avg {avg_load_time*1000:.1f}ms). Consider font preloading or optimization."
            })

        # Memory optimization
        total_fonts = stats["system"]["total_fonts"]
        cached_fonts = stats["system"]["cached_fonts"]
        if cached_fonts > total_fonts * 0.8:
            recommendations.append({
                "type": "memory",
                "priority": "low",
                "message": f"High cache utilization ({cached_fonts}/{total_fonts}). Consider cache size limits."
            })

        return {
            "current_performance": stats,
            "recommendations": sorted(recommendations, key=lambda x: x["priority"]),
            "optimization_score": self._calculate_optimization_score(stats)
        }

    def _calculate_optimization_score(self, stats: Dict[str, Any]) -> int:
        """Calculate an optimization score (0-100, higher is better)."""
        score = 100

        # Penalize low cache hit rate
        cache_hit_rate = stats["cache_statistics"]["hit_rate_percent"]
        score -= max(0, (80 - cache_hit_rate) * 0.5)

        # Penalize slow loading
        avg_load_time = sum(stats["font_loading"]["average_load_times"].values()) / len(stats["font_loading"]["average_load_times"]) if stats["font_loading"]["average_load_times"] else 0
        if avg_load_time > 0.1:
            score -= min(20, avg_load_time * 100)

        # Penalize high failure rate
        failure_rate = stats["font_loading"]["failure_rate_percent"]
        score -= failure_rate * 2

        return max(0, min(100, int(score)))

    def reset_performance_stats(self):
        """Reset all performance statistics."""
        self.performance_stats = {
            "font_load_times": {},
            "cache_hits": 0,
            "cache_misses": 0,
            "render_times": {},
            "total_renders": 0,
            "failed_loads": 0,
            "start_time": time.time()
        }
        logger.info("Performance statistics reset")

    def export_performance_data(self, format: str = "json") -> str:
        """Export performance data for analysis."""
        stats = self.get_performance_statistics()

        if format.lower() == "json":
            return json.dumps(stats, indent=2, default=str)
        elif format.lower() == "csv":
            # Simple CSV export for key metrics
            lines = ["Metric,Value"]
            for category, data in stats.items():
                if isinstance(data, dict):
                    for key, value in data.items():
                        lines.append(f"{category}.{key},{value}")
                else:
                    lines.append(f"{category},{data}")
            return "\n".join(lines)
        else:
            raise ValueError(f"Unsupported export format: {format}")
