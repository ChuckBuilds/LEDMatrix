"""
Logo Helper

Handles logo loading, caching, resizing, and management for LED matrix displays.
Extracted from LEDMatrix core to provide reusable functionality for plugins.
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse

import requests
from PIL import Image
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_assets_dir_mode,
    get_assets_file_mode
)


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
        
        # Session for HTTP requests
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'LEDMatrix-Common/1.0',
            'Accept': 'image/*',
        })
    
    def load_logo(self, team_abbr: str, logo_path: Union[str, Path], 
                  max_width: Optional[int] = None, 
                  max_height: Optional[int] = None) -> Optional[Image.Image]:
        """
        Load and resize a team logo.
        
        Args:
            team_abbr: Team abbreviation for caching
            logo_path: Path to the logo file
            max_width: Maximum width (defaults to display_width * 1.5)
            max_height: Maximum height (defaults to display_height * 1.5)
            
        Returns:
            PIL Image object or None if loading fails
        """
        # Check cache first
        cache_key = f"{team_abbr}_{logo_path}"
        if cache_key in self._logo_cache:
            self.logger.debug(f"Using cached logo for {team_abbr}")
            # Update LRU order (move to end)
            if cache_key in self._cache_order:
                self._cache_order.remove(cache_key)
            self._cache_order.append(cache_key)
            return self._logo_cache[cache_key]
        
        try:
            logo_path = Path(logo_path)
            if not logo_path.exists():
                self.logger.warning(f"Logo not found for {team_abbr} at {logo_path}")
                return None
            
            # Load image
            logo = Image.open(logo_path)
            if logo.mode != 'RGBA':
                logo = logo.convert('RGBA')
            
            # Resize if needed
            logo = self._resize_logo(logo, max_width, max_height)
            
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
                               max_height: Optional[int] = None) -> Optional[Image.Image]:
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
        
        # Try to load existing logo first
        if logo_path.exists():
            return self.load_logo(team_abbr, logo_path, max_width, max_height)
        
        # Download if URL provided and file doesn't exist
        if logo_url:
            try:
                self.logger.info(f"Downloading logo for {team_abbr} from {logo_url}")
                self._download_logo(logo_url, logo_path)
                return self.load_logo(team_abbr, logo_path, max_width, max_height)
            except Exception as e:
                self.logger.error(f"Failed to download logo for {team_abbr}: {e}")
        
        # Create placeholder if all else fails
        return self._create_placeholder_logo(team_abbr, max_width, max_height)
    
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
            'cache_usage_percent': (len(self._logo_cache) / self.cache_size) * 100
        }
    
    def _resize_logo(self, logo: Image.Image, max_width: Optional[int] = None, 
                    max_height: Optional[int] = None) -> Image.Image:
        """Resize logo to fit display dimensions."""
        if max_width is None:
            max_width = int(self.display_width * 1.5)
        if max_height is None:
            max_height = int(self.display_height * 1.5)
        
        # Only resize if necessary
        if logo.width <= max_width and logo.height <= max_height:
            return logo
        
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
        """Download logo from URL."""
        # Ensure directory exists with proper permissions
        ensure_directory_permissions(file_path.parent, get_assets_dir_mode())
        
        # Download with timeout
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        
        # Save to file
        with open(file_path, 'wb') as f:
            f.write(response.content)
        
        # Set proper file permissions after saving
        ensure_file_permissions(file_path, get_assets_file_mode())
        
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
