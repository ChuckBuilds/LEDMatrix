"""
Logo Helper

Handles logo loading, caching, resizing, and management for LED matrix displays.
Extracted from LEDMatrix core to provide reusable functionality for plugins.
"""

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

import requests
from PIL import Image
from src.common.permission_utils import (
    ensure_directory_permissions,
    get_assets_dir_mode,
)

# How long a missing logo stays remembered as missing.
#
# This was 600s, and measured on a live rig that turned out to suppress nothing:
# the display rotation is ~618s, so every recheck landed just as the plugin came
# round again and the warning rate was unchanged at ~6/hour. A TTL has to be long
# relative to the loop that does the asking, not merely "a while".
#
# An hour is safe because the TTL is not the main way an entry clears. A download
# through load_logo_with_download() drops it immediately, and clear_cache() drops
# all of them; the TTL only covers a file that appeared some other way -- someone
# copying one in by hand. Waiting up to an hour for that, or restarting, is a fair
# trade for not re-warning about a file nobody is going to add.
MISSING_LOGO_RECHECK_SECONDS = 3600.0

#: Bounds on a user-supplied logo scale. Wide enough to be useful, closed
#: enough that a typo cannot ask for a 4000px image on a 64px panel.
MIN_LOGO_SCALE = 0.05
MAX_LOGO_SCALE = 8.0


def _usable_scale(scale) -> float:
    """A scale that can be applied, or 1.0.

    Anything unusable -- None, a string, zero, a negative, NaN, infinity --
    means "as shipped", because the alternative is a blank panel from a
    mistyped number.
    """
    try:
        value = float(scale)
    except (TypeError, ValueError):
        return 1.0
    if value != value or value in (float('inf'), float('-inf')):
        return 1.0
    if value < MIN_LOGO_SCALE or value > MAX_LOGO_SCALE:
        return 1.0
    return value



# Well above any real team logo; bounds what a remote URL can write to disk.
# The cap for every logo download: src.logo_downloader.fetch_logo uses it too.
MAX_LOGO_BYTES = 10 * 1024 * 1024


class LogoHelper:
    """
    Helper class for logo loading, caching, and resizing.
    
    Provides functionality for:
    - Loading logos from files
    - Caching loaded logos in memory
    - Resizing logos to fit display dimensions
    - Handling logo variations and fallbacks
    - Downloading missing logos from URLs
    """
    
    def __init__(self, display_width: int, display_height: int, 
                 cache_size: int = 100, logger: Optional[logging.Logger] = None):
        """
        Initialize the LogoHelper.
        
        Args:
            display_width: Width of the LED matrix display
            display_height: Height of the LED matrix display
            cache_size: Maximum number of logos to cache in memory
            logger: Optional logger instance
        """
        self.display_width = display_width
        self.display_height = display_height
        self.cache_size = cache_size
        self.logger = logger or logging.getLogger(__name__)
        
        # In-memory logo cache
        self._logo_cache: Dict[str, Image.Image] = {}
        self._cache_order: List[str] = []  # For LRU cache management

        # Misses, so an absent file is stat'd and warned about once rather than
        # on every call. Without this a permanently missing logo produced a
        # warning per rotation forever -- measured at 114 lines in 24 hours for
        # a single missing ticker icon, for a file nobody was going to add.
        # Time-bounded rather than permanent so a logo that appears later (the
        # downloader writes them at runtime) is still picked up.
        self._missing_logos: Dict[str, float] = {}
        
        # Session for HTTP requests
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'LEDMatrix-Common/1.0',
            'Accept': 'image/*',
        })
    
    def load_logo(self, team_abbr: str, logo_path: Union[str, Path],
                  max_width: Optional[int] = None,
                  max_height: Optional[int] = None,
                  scale: float = 1.0) -> Optional[Image.Image]:
        """
        Load and resize a team logo.

        Args:
            team_abbr: Team abbreviation for caching
            logo_path: Path to the logo file
            max_width: Maximum width (defaults to display_width * 1.5)
            max_height: Maximum height (defaults to display_height * 1.5)
            scale: User's size multiplier for this image, from
                ``customization.layout.<element>.scale``. 1.0 is untouched and
                takes exactly the path it always did. Callers hold the config,
                so they resolve the element name; this only applies the number.

        Returns:
            PIL Image object or None if loading fails

        Note: for new adaptive-layout code prefer ``BasePlugin.draw_image``
        / ``LayoutContext.fit_image`` (src/adaptive_images.py) for the
        fitting step — LogoHelper remains useful for its download and
        placeholder logic.
        """
        # Resolve the effective target size BEFORE the cache lookup so the
        # key is size-qualified — a panel-size change must not return a
        # logo resized for the old dimensions.
        if max_width is None:
            max_width = int(self.display_width * 1.5)
        if max_height is None:
            max_height = int(self.display_height * 1.5)
        scale = _usable_scale(scale)
        if scale != 1.0:
            max_width = max(1, int(round(max_width * scale)))
            max_height = max(1, int(round(max_height * scale)))
        # The key carries the scaled box, so two elements scaled differently
        # cannot be served each other's image.
        cache_key = f"{team_abbr}_{logo_path}_{max_width}x{max_height}"
        if cache_key in self._logo_cache:
            self.logger.debug(f"Using cached logo for {team_abbr}")
            # Update LRU order (move to end)
            if cache_key in self._cache_order:
                self._cache_order.remove(cache_key)
            self._cache_order.append(cache_key)
            return self._logo_cache[cache_key]
        
        # A known-missing file: skip the stat and stay quiet until the entry
        # ages out. Checked after the positive cache so a logo that has since
        # been loaded always wins.
        missed_at = self._missing_logos.get(cache_key)
        if missed_at is not None:
            if time.time() - missed_at < MISSING_LOGO_RECHECK_SECONDS:
                return None
            del self._missing_logos[cache_key]

        try:
            logo_path = Path(logo_path)
            if not logo_path.exists():
                self._missing_logos[cache_key] = time.time()
                self.logger.warning(f"Logo not found for {team_abbr} at {logo_path}")
                return None
            
            # Load image
            logo = Image.open(logo_path)
            if logo.mode != 'RGBA':
                logo = logo.convert('RGBA')
            
            # Resize if needed
            logo = self._resize_logo(logo, max_width, max_height,
                                     allow_upscale=scale > 1.0)
            
            # Cache the logo
            self._cache_logo(cache_key, logo)
            
            self.logger.debug(f"Loaded logo for {team_abbr} from {logo_path}")
            return logo
            
        except Exception as e:
            self.logger.error(f"Error loading logo for {team_abbr}: {e}")
            return None
    
    def load_logo_with_download(self, team_abbr: str, logo_path: Union[str, Path],
                               logo_url: Optional[str] = None,
                               max_width: Optional[int] = None,
                               max_height: Optional[int] = None,
                               scale: float = 1.0) -> Optional[Image.Image]:
        """
        Load logo with automatic download if missing.
        
        Args:
            team_abbr: Team abbreviation
            logo_path: Local path to store/load logo
            logo_url: URL to download logo from if local file missing
            max_width: Maximum width for resizing
            max_height: Maximum height for resizing
            
        Returns:
            PIL Image object or None if loading fails
        """
        logo_path = Path(logo_path)
        
        # Try to load existing logo first. A placeholder written by a previous
        # failed download does not count: it wears the real logo's filename, so
        # trusting the file's existence is what left teams as grey boxes.
        if logo_path.exists() and not self._is_stale_placeholder(logo_path):
            return self.load_logo(team_abbr, logo_path, max_width, max_height,
                                  scale)
        
        # Download if URL provided and file doesn't exist
        if logo_url:
            try:
                self.logger.info(f"Downloading logo for {team_abbr} from {logo_url}")
                self._download_logo(logo_url, logo_path)
                # The file on disk just changed. Any cached image for it is the
                # placeholder we came here to replace, and load_logo() answers
                # from the cache before touching the disk -- so without this the
                # real logo would not appear until the process restarted.
                self._invalidate_cached_logo(team_abbr, logo_path)
                return self.load_logo(team_abbr, logo_path, max_width, max_height,
                                  scale)
            except Exception as e:
                self.logger.error(f"Failed to download logo for {team_abbr}: {e}")
                # The retry failed, so restart the back-off. The stale
                # placeholder is still on disk with its old timestamp, and
                # leaving it there means the next call retries immediately --
                # a download attempt per call, which is what the back-off
                # exists to prevent.
                self._refresh_stale_placeholder(logo_path)
        
        # Create placeholder if all else fails
        return self._create_placeholder_logo(team_abbr, max_width, max_height)
    
    def _invalidate_cached_logo(self, team_abbr: str, logo_path: Path) -> None:
        """Drop every cached size of one logo after its file changed on disk."""
        prefix = f"{team_abbr}_{logo_path}_"
        for key in [k for k in self._logo_cache if k.startswith(prefix)]:
            self._logo_cache.pop(key, None)
            if key in self._cache_order:
                self._cache_order.remove(key)
        # The file exists now, so any record of it being missing is wrong --
        # and load_logo() consults that record before it stats the disk, so
        # leaving it would hide a logo we just downloaded.
        for key in [k for k in self._missing_logos if k.startswith(prefix)]:
            del self._missing_logos[key]

    @staticmethod
    def _refresh_stale_placeholder(logo_path: Path) -> None:
        """Restart the retry back-off after a failed download attempt."""
        try:
            from src.logo_downloader import refresh_placeholder_timestamp
        except ImportError:
            return
        refresh_placeholder_timestamp(logo_path)

    @staticmethod
    def _is_stale_placeholder(logo_path: Path) -> bool:
        """True if the file is a placeholder old enough to be worth retrying.

        Imported lazily so this module keeps working against a core build whose
        logo_downloader predates placeholder marking.
        """
        try:
            from src.logo_downloader import (
                PLACEHOLDER_RETRY_SECONDS,
                is_placeholder_logo,
                placeholder_age_seconds,
            )
        except ImportError:
            return False
        if not is_placeholder_logo(logo_path):
            return False
        age = placeholder_age_seconds(logo_path)
        return age is None or age >= PLACEHOLDER_RETRY_SECONDS

    def get_logo_variations(self, team_abbr: str) -> List[str]:
        """
        Get possible filename variations for a team abbreviation.
        
        Args:
            team_abbr: Team abbreviation
            
        Returns:
            List of possible filename variations
        """
        variations = [team_abbr]
        
        # Common variations
        if '&' in team_abbr:
            variations.append(team_abbr.replace('&', 'AND'))
        if 'AND' in team_abbr:
            variations.append(team_abbr.replace('AND', '&'))
        
        # Handle special cases
        special_cases = {
            'TA&M': ['TAMU', 'TEXASAM'],
            'UCLA': ['UCLA'],
            'USC': ['USC'],
            'LSU': ['LSU'],
        }
        
        if team_abbr in special_cases:
            variations.extend(special_cases[team_abbr])
        
        return variations
    
    def normalize_abbreviation(self, team_abbr: str) -> str:
        """
        Normalize team abbreviation for consistent filename usage.

        NOTE: this deliberately differs from
        LogoDownloader.normalize_abbreviation (src/logo_downloader.py),
        which replaces filesystem-unsafe characters (/ \\ : * ? " < > |)
        but does not strip spaces. Plugins call the LogoDownloader
        version; changing either implementation changes which logo
        filenames resolve on existing installs.

        Args:
            team_abbr: Raw team abbreviation

        Returns:
            Normalized abbreviation
        """
        # Remove spaces and convert to uppercase
        normalized = team_abbr.strip().upper()
        
        # Handle special characters
        normalized = normalized.replace('&', 'AND')
        normalized = normalized.replace(' ', '')
        
        return normalized
    
    def clear_cache(self) -> None:
        """Clear the logo cache."""
        self._logo_cache.clear()
        self._cache_order.clear()
        self._missing_logos.clear()
        self.logger.debug("Logo cache cleared")
    
    def get_cache_stats(self) -> Dict[str, int]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        return {
            'cached_logos': len(self._logo_cache),
            'cache_size_limit': self.cache_size,
            'cache_usage_percent': (
                (len(self._logo_cache) / self.cache_size) * 100
                if self.cache_size else 0
            ),
        }
    
    def _resize_logo(self, logo: Image.Image, max_width: Optional[int] = None,
                    max_height: Optional[int] = None,
                    allow_upscale: bool = False) -> Image.Image:
        """Resize logo to fit display dimensions.

        ``allow_upscale`` is only set when the user asked for a scale above 1:
        the fit rule is "never larger than the box", and growing an image
        nobody asked to grow would change every existing render.
        """
        if max_width is None:
            max_width = int(self.display_width * 1.5)
        if max_height is None:
            max_height = int(self.display_height * 1.5)

        # Only resize if necessary
        if logo.width <= max_width and logo.height <= max_height:
            if not allow_upscale or not logo.width or not logo.height:
                return logo
            ratio = min(max_width / logo.width, max_height / logo.height)
            if ratio <= 1:
                return logo
            return logo.resize((max(1, int(logo.width * ratio)),
                                max(1, int(logo.height * ratio))),
                               Image.Resampling.LANCZOS)

        # Maintain aspect ratio
        logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        return logo
    
    def _cache_logo(self, cache_key: str, logo: Image.Image) -> None:
        """Cache a logo with LRU eviction."""
        # Remove oldest if cache is full
        if len(self._logo_cache) >= self.cache_size:
            if self._cache_order:
                oldest_key = self._cache_order.pop(0)
                del self._logo_cache[oldest_key]
        
        # Add to cache
        self._logo_cache[cache_key] = logo
        self._cache_order.append(cache_key)
    
    def _download_logo(self, url: str, file_path: Path) -> None:
        """Download a logo from ``url`` to ``file_path``; raises on failure.

        Delegates to ``src.logo_downloader.fetch_logo``, the same hardened
        download the scoreboard plugins use: streamed and capped at
        ``MAX_LOGO_BYTES``, ``image/*`` only, decoded by Pillow, stored as an
        RGBA PNG, and moved into place atomically -- a failure leaves neither
        a partial file nor a temp file. Uses this helper's own session.

        Imported lazily: src.logo_downloader imports src.common, so a
        module-level import here would be circular.
        """
        from src.logo_downloader import fetch_logo

        # Ensure directory exists with proper permissions
        ensure_directory_permissions(file_path.parent, get_assets_dir_mode())
        fetch_logo(self.session, url, file_path, timeout=30,
                   max_bytes=MAX_LOGO_BYTES)
        self.logger.debug(f"Downloaded logo to {file_path}")
    
    def _create_placeholder_logo(self, team_abbr: str, 
                               max_width: Optional[int] = None,
                               max_height: Optional[int] = None) -> Optional[Image.Image]:
        """
        Create a placeholder logo with team abbreviation.
        
        Args:
            team_abbr: Team abbreviation to display
            max_width: Maximum width
            max_height: Maximum height
            
        Returns:
            PIL Image with placeholder logo
        """
        try:
            if max_width is None:
                max_width = int(self.display_width * 1.5)
            if max_height is None:
                max_height = int(self.display_height * 1.5)
            
            # Create placeholder image
            placeholder = Image.new('RGBA', (max_width, max_height), (0, 0, 0, 0))
            
            # This would require a font, so we'll create a simple colored rectangle
            # In a real implementation, you'd want to add text rendering here
            from PIL import ImageDraw
            draw = ImageDraw.Draw(placeholder)
            
            # Draw a simple rectangle with team abbreviation
            draw.rectangle([0, 0, max_width-1, max_height-1], 
                          fill=(100, 100, 100, 200), outline=(200, 200, 200, 255))
            
            self.logger.debug(f"Created placeholder logo for {team_abbr}")
            return placeholder
            
        except Exception as e:
            self.logger.error(f"Error creating placeholder for {team_abbr}: {e}")
            return None
