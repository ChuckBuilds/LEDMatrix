"""
Plugin Adapter for Vegas Mode

Converts plugin content to scrollable images. Supports both plugins that
implement get_vegas_content() and fallback capture of display() output.
"""

import logging
import threading
import time
from typing import Optional, List, Any, Tuple, Union, TYPE_CHECKING
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
        # Handle both property and method access patterns
        self.display_width = (
            display_manager.width() if callable(display_manager.width)
            else display_manager.width
        )
        self.display_height = (
            display_manager.height() if callable(display_manager.height)
            else display_manager.height
        )

        # Cache for recently fetched content (prevents redundant fetch)
        self._content_cache: dict = {}
        self._cache_lock = threading.Lock()
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

        # Try to get scroll_helper's cached image (for scrolling plugins like stocks/odds)
        content = self._get_scroll_helper_content(plugin, plugin_id)
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

        except (AttributeError, TypeError, ValueError, OSError):
            logger.exception(
                "Error calling get_vegas_content() on %s",
                plugin_id
            )
            return None

    def _get_scroll_helper_content(
        self, plugin: 'BasePlugin', plugin_id: str
    ) -> Optional[List[Image.Image]]:
        """
        Get content from plugin's scroll_helper if available.

        Many scrolling plugins (stocks, odds) use a ScrollHelper that caches
        their full scrolling image. This method extracts that image for Vegas
        mode instead of falling back to single-frame capture.

        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier

        Returns:
            List with the cached scroll image, or None if not available
        """
        try:
            # Check for scroll_helper with cached_image
            scroll_helper = getattr(plugin, 'scroll_helper', None)
            if scroll_helper is None:
                return None

            cached_image = getattr(scroll_helper, 'cached_image', None)
            if cached_image is None or not isinstance(cached_image, Image.Image):
                return None

            # Copy the image to prevent modification
            img = cached_image.copy()

            # Ensure correct height
            if img.height != self.display_height:
                logger.debug(
                    "[%s] Resizing scroll_helper content: %dx%d -> %dx%d",
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

            logger.info(
                "[%s] Using scroll_helper cached image: %dx%d",
                plugin_id, img.width, img.height
            )

            return [img]

        except (AttributeError, TypeError, ValueError, OSError):
            logger.debug(
                "[%s] No scroll_helper content available",
                plugin_id
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

            # Ensure plugin has fresh data before capturing
            if hasattr(plugin, 'update_data'):
                try:
                    plugin.update_data()
                except Exception as e:
                    logger.debug("[%s] update_data() failed: %s", plugin_id, e)

            # Clear and call plugin display
            self.display_manager.clear()

            # First try without force_clear (some plugins behave better this way)
            try:
                plugin.display()
            except TypeError:
                # Plugin may require force_clear argument
                plugin.display(force_clear=True)

            # Capture the result
            captured = self.display_manager.image.copy()

            # Check if captured image has content (not all black)
            is_blank, bright_ratio = self._is_blank_image(captured, return_ratio=True)
            if is_blank:
                logger.debug(
                    "[%s] First capture blank (%.3f%% bright), retrying with force_clear",
                    plugin_id, bright_ratio * 100
                )
                # Try once more with force_clear=True
                self.display_manager.clear()
                plugin.display(force_clear=True)
                captured = self.display_manager.image.copy()

                is_blank, bright_ratio = self._is_blank_image(captured, return_ratio=True)
                if is_blank:
                    logger.info(
                        "[%s] Produced blank image after retry (%.3f%% bright pixels, "
                        "threshold=0.5%%), size=%dx%d",
                        plugin_id, bright_ratio * 100,
                        captured.width, captured.height
                    )
                    return None

            # Convert to RGB if needed
            if captured.mode != 'RGB':
                captured = captured.convert('RGB')

            logger.debug(
                "Captured display content from %s: %dx%d",
                plugin_id, captured.width, captured.height
            )

            return [captured]

        except (AttributeError, TypeError, ValueError, OSError, RuntimeError):
            logger.exception(
                "Error capturing display from %s",
                plugin_id
            )
            return None

        finally:
            # Always restore original image to prevent display corruption
            if original_image is not None:
                self.display_manager.image = original_image

    def _is_blank_image(
        self, img: Image.Image, return_ratio: bool = False
    ) -> Union[bool, Tuple[bool, float]]:
        """
        Check if an image is essentially blank (all black or nearly so).

        Uses histogram-based detection which is more reliable than
        point sampling for content that may be positioned anywhere.

        Args:
            img: Image to check
            return_ratio: If True, return tuple of (is_blank, bright_ratio)

        Returns:
            True if image is blank, or tuple (is_blank, bright_ratio) if return_ratio=True
        """
        # Convert to RGB for consistent checking
        if img.mode != 'RGB':
            img = img.convert('RGB')

        # Use histogram to check for any non-black content
        # This is more reliable than point sampling
        histogram = img.histogram()

        # RGB histogram: 256 values per channel
        # Check if there's any significant brightness in any channel
        total_bright_pixels = 0
        threshold = 15  # Minimum brightness to count as "content"

        for channel_offset in [0, 256, 512]:  # R, G, B
            for brightness in range(threshold, 256):
                total_bright_pixels += histogram[channel_offset + brightness]

        # If less than 0.5% of pixels have any brightness, consider blank
        total_pixels = img.width * img.height
        bright_ratio = total_bright_pixels / (total_pixels * 3)  # Normalize across channels

        is_blank = bright_ratio < 0.005  # Less than 0.5% bright pixels

        if return_ratio:
            return is_blank, bright_ratio
        return is_blank

    def _get_cached(self, plugin_id: str) -> Optional[List[Image.Image]]:
        """Get cached content if still valid."""
        with self._cache_lock:
            if plugin_id not in self._content_cache:
                return None

            cached_time, content = self._content_cache[plugin_id]
            if time.time() - cached_time > self._cache_ttl:
                del self._content_cache[plugin_id]
                return None

            return content

    def _cache_content(self, plugin_id: str, content: List[Image.Image]) -> None:
        """Cache content for a plugin."""
        # Make copies to prevent mutation (done outside lock to minimize hold time)
        cached_content = [img.copy() for img in content]

        with self._cache_lock:
            # Periodic cleanup of expired entries to prevent memory leak
            self._cleanup_expired_cache_locked()
            self._content_cache[plugin_id] = (time.time(), cached_content)

    def _cleanup_expired_cache_locked(self) -> None:
        """Remove expired entries from cache. Must be called with _cache_lock held."""
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
        with self._cache_lock:
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
            except (AttributeError, TypeError, ValueError):
                logger.exception(
                    "Error calling get_vegas_content_type() on %s",
                    plugin_id
                )

        # Default to static for plugins without explicit type
        return 'static'

    def cleanup(self) -> None:
        """Clean up resources."""
        with self._cache_lock:
            self._content_cache.clear()
        logger.debug("PluginAdapter cleanup complete")
