"""
Plugin Adapter for Vegas Mode

Converts plugin content to scrollable images. Supports both plugins that
implement get_vegas_content() and fallback capture of display() output.
"""

import logging
import time
import copy
from typing import Optional, List, Any, TYPE_CHECKING
from PIL import Image

if TYPE_CHECKING:
    from src.plugin_system.base_plugin import BasePlugin

logger = logging.getLogger(__name__)


class PluginAdapter:
    """
    Adapter for extracting scrollable content from plugins.

    Supports two modes:
    1. Native: Plugin implements get_vegas_content() returning PIL Image(s)
    2. Fallback: Capture display_manager.image after calling plugin.display()
    """

    def __init__(self, display_manager: Any):
        """
        Initialize the plugin adapter.

        Args:
            display_manager: DisplayManager instance for fallback capture
        """
        self.display_manager = display_manager
        self.display_width = display_manager.width
        self.display_height = display_manager.height

        # Cache for recently fetched content (prevents redundant fetch)
        self._content_cache: dict = {}
        self._cache_ttl = 5.0  # Cache for 5 seconds

        logger.info(
            "PluginAdapter initialized: display=%dx%d",
            self.display_width, self.display_height
        )

    def get_content(self, plugin: 'BasePlugin', plugin_id: str) -> Optional[List[Image.Image]]:
        """
        Get scrollable content from a plugin.

        Tries get_vegas_content() first, falls back to display capture.

        Args:
            plugin: Plugin instance to get content from
            plugin_id: Plugin identifier for logging

        Returns:
            List of PIL Images representing plugin content, or None if no content
        """
        try:
            # Check cache first
            cached = self._get_cached(plugin_id)
            if cached is not None:
                logger.debug("Using cached content for %s", plugin_id)
                return cached

            # Try native Vegas content method first
            if hasattr(plugin, 'get_vegas_content'):
                content = self._get_native_content(plugin, plugin_id)
                if content:
                    self._cache_content(plugin_id, content)
                    return content

            # Fall back to display capture
            content = self._capture_display_content(plugin, plugin_id)
            if content:
                self._cache_content(plugin_id, content)
                return content

            logger.warning("No content available from plugin %s", plugin_id)
            return None

        except Exception as e:
            logger.error("Error getting content from plugin %s: %s", plugin_id, e)
            return None

    def _get_native_content(
        self, plugin: 'BasePlugin', plugin_id: str
    ) -> Optional[List[Image.Image]]:
        """
        Get content via plugin's native get_vegas_content() method.

        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier

        Returns:
            List of images or None
        """
        try:
            result = plugin.get_vegas_content()

            if result is None:
                logger.debug("Plugin %s get_vegas_content() returned None", plugin_id)
                return None

            # Normalize to list
            if isinstance(result, Image.Image):
                images = [result]
            elif isinstance(result, (list, tuple)):
                images = list(result)
            else:
                logger.warning(
                    "Plugin %s get_vegas_content() returned unexpected type: %s",
                    plugin_id, type(result)
                )
                return None

            # Validate images
            valid_images = []
            for img in images:
                if not isinstance(img, Image.Image):
                    logger.warning(
                        "Plugin %s returned non-Image in list: %s",
                        plugin_id, type(img)
                    )
                    continue

                # Ensure correct height
                if img.height != self.display_height:
                    logger.debug(
                        "Resizing content from %s: %dx%d -> %dx%d",
                        plugin_id, img.width, img.height,
                        img.width, self.display_height
                    )
                    img = img.resize(
                        (img.width, self.display_height),
                        Image.Resampling.LANCZOS
                    )

                # Convert to RGB if needed
                if img.mode != 'RGB':
                    img = img.convert('RGB')

                valid_images.append(img)

            if valid_images:
                logger.debug(
                    "Got %d native images from %s (total width: %dpx)",
                    len(valid_images), plugin_id,
                    sum(img.width for img in valid_images)
                )
                return valid_images

            return None

        except Exception as e:
            logger.error(
                "Error calling get_vegas_content() on %s: %s",
                plugin_id, e
            )
            return None

    def _capture_display_content(
        self, plugin: 'BasePlugin', plugin_id: str
    ) -> Optional[List[Image.Image]]:
        """
        Capture content by calling plugin.display() and grabbing the frame.

        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier

        Returns:
            List with single captured image, or None
        """
        original_image = None
        try:
            # Save current display state
            original_image = self.display_manager.image.copy()

            # Clear and call plugin display
            self.display_manager.clear()
            plugin.display(force_clear=True)

            # Capture the result
            captured = self.display_manager.image.copy()

            # Check if captured image has content (not all black)
            if self._is_blank_image(captured):
                logger.debug("Plugin %s produced blank image", plugin_id)
                return None

            # Convert to RGB if needed
            if captured.mode != 'RGB':
                captured = captured.convert('RGB')

            logger.debug(
                "Captured display content from %s: %dx%d",
                plugin_id, captured.width, captured.height
            )

            return [captured]

        except Exception as e:
            logger.error(
                "Error capturing display from %s: %s",
                plugin_id, e
            )
            return None

        finally:
            # Always restore original image to prevent display corruption
            if original_image is not None:
                self.display_manager.image = original_image

    def _is_blank_image(self, img: Image.Image) -> bool:
        """
        Check if an image is essentially blank (all black or nearly so).

        Args:
            img: Image to check

        Returns:
            True if image is blank
        """
        # Convert to RGB for consistent checking
        if img.mode != 'RGB':
            img = img.convert('RGB')

        # Sample some pixels rather than checking all
        width, height = img.size
        sample_points = [
            (width // 4, height // 4),
            (width // 2, height // 2),
            (3 * width // 4, 3 * height // 4),
            (width // 4, 3 * height // 4),
            (3 * width // 4, height // 4),
        ]

        for x, y in sample_points:
            pixel = img.getpixel((x, y))
            if isinstance(pixel, tuple):
                if sum(pixel[:3]) > 30:  # Not very dark
                    return False
            elif pixel > 10:
                return False

        return True

    def _get_cached(self, plugin_id: str) -> Optional[List[Image.Image]]:
        """Get cached content if still valid."""
        if plugin_id not in self._content_cache:
            return None

        cached_time, content = self._content_cache[plugin_id]
        if time.time() - cached_time > self._cache_ttl:
            del self._content_cache[plugin_id]
            return None

        return content

    def _cache_content(self, plugin_id: str, content: List[Image.Image]) -> None:
        """Cache content for a plugin."""
        # Periodic cleanup of expired entries to prevent memory leak
        self._cleanup_expired_cache()

        # Make copies to prevent mutation
        cached_content = [img.copy() for img in content]
        self._content_cache[plugin_id] = (time.time(), cached_content)

    def _cleanup_expired_cache(self) -> None:
        """Remove expired entries from cache to prevent memory leaks."""
        current_time = time.time()
        expired_keys = [
            key for key, (cached_time, _) in self._content_cache.items()
            if current_time - cached_time > self._cache_ttl
        ]
        for key in expired_keys:
            del self._content_cache[key]

    def invalidate_cache(self, plugin_id: Optional[str] = None) -> None:
        """
        Invalidate cached content.

        Args:
            plugin_id: Specific plugin to invalidate, or None for all
        """
        if plugin_id:
            self._content_cache.pop(plugin_id, None)
        else:
            self._content_cache.clear()

    def get_content_type(self, plugin: 'BasePlugin', plugin_id: str) -> str:
        """
        Get the type of content a plugin provides.

        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier

        Returns:
            'multi' for multiple items, 'static' for single frame, 'none' for excluded
        """
        if hasattr(plugin, 'get_vegas_content_type'):
            try:
                return plugin.get_vegas_content_type()
            except Exception as e:
                logger.warning(
                    "Error calling get_vegas_content_type() on %s: %s",
                    plugin_id, e
                )

        # Default to static for plugins without explicit type
        return 'static'

    def cleanup(self) -> None:
        """Clean up resources."""
        self._content_cache.clear()
        logger.debug("PluginAdapter cleanup complete")
