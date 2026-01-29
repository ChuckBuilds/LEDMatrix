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
        logger.info(
            "[%s] Getting content (class=%s)",
            plugin_id, plugin.__class__.__name__
        )

        # Check cache first
        cached = self._get_cached(plugin_id)
        if cached is not None:
            total_width = sum(img.width for img in cached)
            logger.info(
                "[%s] Using cached content: %d images, %dpx total",
                plugin_id, len(cached), total_width
            )
            return cached

        # Try native Vegas content method first
        has_native = hasattr(plugin, 'get_vegas_content')
        logger.info("[%s] Has get_vegas_content: %s", plugin_id, has_native)
        if has_native:
            content = self._get_native_content(plugin, plugin_id)
            if content:
                total_width = sum(img.width for img in content)
                logger.info(
                    "[%s] Native content SUCCESS: %d images, %dpx total",
                    plugin_id, len(content), total_width
                )
                self._cache_content(plugin_id, content)
                return content
            logger.info("[%s] Native content returned None", plugin_id)

        # Try to get scroll_helper's cached image (for scrolling plugins like stocks/odds)
        has_scroll_helper = hasattr(plugin, 'scroll_helper')
        logger.info("[%s] Has scroll_helper: %s", plugin_id, has_scroll_helper)
        content = self._get_scroll_helper_content(plugin, plugin_id)
        if content:
            total_width = sum(img.width for img in content)
            logger.info(
                "[%s] ScrollHelper content SUCCESS: %d images, %dpx total",
                plugin_id, len(content), total_width
            )
            self._cache_content(plugin_id, content)
            return content
        if has_scroll_helper:
            logger.info("[%s] ScrollHelper content returned None", plugin_id)

        # Fall back to display capture
        logger.info("[%s] Trying fallback display capture...", plugin_id)
        content = self._capture_display_content(plugin, plugin_id)
        if content:
            total_width = sum(img.width for img in content)
            logger.info(
                "[%s] Fallback capture SUCCESS: %d images, %dpx total",
                plugin_id, len(content), total_width
            )
            self._cache_content(plugin_id, content)
            return content

        logger.warning(
            "[%s] NO CONTENT from any method (native=%s, scroll_helper=%s, fallback=tried)",
            plugin_id, has_native, has_scroll_helper
        )
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
            logger.info("[%s] Native: calling get_vegas_content()", plugin_id)
            result = plugin.get_vegas_content()

            if result is None:
                logger.info("[%s] Native: get_vegas_content() returned None", plugin_id)
                return None

            # Normalize to list
            if isinstance(result, Image.Image):
                images = [result]
                logger.info(
                    "[%s] Native: got single Image %dx%d",
                    plugin_id, result.width, result.height
                )
            elif isinstance(result, (list, tuple)):
                images = list(result)
                logger.info(
                    "[%s] Native: got %d items in list/tuple",
                    plugin_id, len(images)
                )
            else:
                logger.warning(
                    "[%s] Native: unexpected return type: %s",
                    plugin_id, type(result).__name__
                )
                return None

            # Validate images
            valid_images = []
            for i, img in enumerate(images):
                if not isinstance(img, Image.Image):
                    logger.warning(
                        "[%s] Native: item[%d] is not an Image: %s",
                        plugin_id, i, type(img).__name__
                    )
                    continue

                logger.info(
                    "[%s] Native: item[%d] is %dx%d, mode=%s",
                    plugin_id, i, img.width, img.height, img.mode
                )

                # Ensure correct height
                if img.height != self.display_height:
                    logger.info(
                        "[%s] Native: resizing item[%d]: %dx%d -> %dx%d",
                        plugin_id, i, img.width, img.height,
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
                total_width = sum(img.width for img in valid_images)
                logger.info(
                    "[%s] Native: SUCCESS - %d images, %dpx total width",
                    plugin_id, len(valid_images), total_width
                )
                return valid_images

            logger.info("[%s] Native: no valid images after validation", plugin_id)
            return None

        except (AttributeError, TypeError, ValueError, OSError) as e:
            logger.exception(
                "[%s] Native: ERROR calling get_vegas_content(): %s",
                plugin_id, e
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
                logger.debug("[%s] No scroll_helper attribute", plugin_id)
                return None

            logger.info(
                "[%s] Found scroll_helper: %s",
                plugin_id, type(scroll_helper).__name__
            )

            cached_image = getattr(scroll_helper, 'cached_image', None)
            if cached_image is None:
                logger.info(
                    "[%s] scroll_helper.cached_image is None, triggering content generation",
                    plugin_id
                )
                # Try to trigger scroll content generation
                cached_image = self._trigger_scroll_content_generation(
                    plugin, plugin_id, scroll_helper
                )
                if cached_image is None:
                    return None

            if not isinstance(cached_image, Image.Image):
                logger.info(
                    "[%s] scroll_helper.cached_image is not an Image: %s",
                    plugin_id, type(cached_image).__name__
                )
                return None

            logger.info(
                "[%s] scroll_helper.cached_image found: %dx%d, mode=%s",
                plugin_id, cached_image.width, cached_image.height, cached_image.mode
            )

            # Copy the image to prevent modification
            img = cached_image.copy()

            # Ensure correct height
            if img.height != self.display_height:
                logger.info(
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
                "[%s] ScrollHelper content ready: %dx%d",
                plugin_id, img.width, img.height
            )

            return [img]

        except (AttributeError, TypeError, ValueError, OSError):
            logger.exception("[%s] Error getting scroll_helper content", plugin_id)
            return None

    def _trigger_scroll_content_generation(
        self, plugin: 'BasePlugin', plugin_id: str, scroll_helper: Any
    ) -> Optional[Image.Image]:
        """
        Trigger scroll content generation for plugins that haven't built it yet.

        Tries multiple approaches:
        1. _create_scrolling_display() - stocks plugin pattern
        2. display(force_clear=True) - general pattern that populates scroll cache

        Args:
            plugin: Plugin instance
            plugin_id: Plugin identifier
            scroll_helper: Plugin's scroll_helper instance

        Returns:
            The generated cached_image or None
        """
        original_image = None
        try:
            # Save display state to restore after
            original_image = self.display_manager.image.copy()

            # Method 1: Try _create_scrolling_display (stocks pattern)
            if hasattr(plugin, '_create_scrolling_display'):
                logger.info(
                    "[%s] Triggering via _create_scrolling_display()",
                    plugin_id
                )
                try:
                    plugin._create_scrolling_display()
                    cached_image = getattr(scroll_helper, 'cached_image', None)
                    if cached_image is not None and isinstance(cached_image, Image.Image):
                        logger.info(
                            "[%s] _create_scrolling_display() SUCCESS: %dx%d",
                            plugin_id, cached_image.width, cached_image.height
                        )
                        return cached_image
                except (AttributeError, TypeError, ValueError, OSError):
                    logger.exception(
                        "[%s] _create_scrolling_display() failed", plugin_id
                    )

            # Method 2: Try display(force_clear=True) which typically builds scroll content
            if hasattr(plugin, 'display'):
                logger.info(
                    "[%s] Triggering via display(force_clear=True)",
                    plugin_id
                )
                try:
                    self.display_manager.clear()
                    plugin.display(force_clear=True)
                    cached_image = getattr(scroll_helper, 'cached_image', None)
                    if cached_image is not None and isinstance(cached_image, Image.Image):
                        logger.info(
                            "[%s] display(force_clear=True) SUCCESS: %dx%d",
                            plugin_id, cached_image.width, cached_image.height
                        )
                        return cached_image
                    logger.info(
                        "[%s] display(force_clear=True) did not populate cached_image",
                        plugin_id
                    )
                except (AttributeError, TypeError, ValueError, OSError):
                    logger.exception(
                        "[%s] display(force_clear=True) failed", plugin_id
                    )

            logger.info(
                "[%s] Could not trigger scroll content generation",
                plugin_id
            )
            return None

        except (AttributeError, TypeError, ValueError, OSError):
            logger.exception("[%s] Error triggering scroll content", plugin_id)
            return None

        finally:
            # Restore original display state
            if original_image is not None:
                self.display_manager.image = original_image

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
            logger.info("[%s] Fallback: saved original display state", plugin_id)

            # Ensure plugin has fresh data before capturing
            has_update_data = hasattr(plugin, 'update_data')
            logger.info("[%s] Fallback: has update_data=%s", plugin_id, has_update_data)
            if has_update_data:
                try:
                    plugin.update_data()
                    logger.info("[%s] Fallback: update_data() called", plugin_id)
                except (AttributeError, RuntimeError, OSError):
                    logger.exception("[%s] Fallback: update_data() failed", plugin_id)

            # Clear and call plugin display
            self.display_manager.clear()
            logger.info("[%s] Fallback: display cleared, calling display()", plugin_id)

            # First try without force_clear (some plugins behave better this way)
            try:
                plugin.display()
                logger.info("[%s] Fallback: display() called successfully", plugin_id)
            except TypeError:
                # Plugin may require force_clear argument
                logger.info("[%s] Fallback: display() failed, trying with force_clear=True", plugin_id)
                plugin.display(force_clear=True)

            # Capture the result
            captured = self.display_manager.image.copy()
            logger.info(
                "[%s] Fallback: captured frame %dx%d, mode=%s",
                plugin_id, captured.width, captured.height, captured.mode
            )

            # Check if captured image has content (not all black)
            is_blank, bright_ratio = self._is_blank_image(captured, return_ratio=True)
            logger.info(
                "[%s] Fallback: brightness check - %.3f%% bright pixels (threshold=0.5%%)",
                plugin_id, bright_ratio * 100
            )

            if is_blank:
                logger.info(
                    "[%s] Fallback: first capture blank, retrying with force_clear",
                    plugin_id
                )
                # Try once more with force_clear=True
                self.display_manager.clear()
                plugin.display(force_clear=True)
                captured = self.display_manager.image.copy()

                is_blank, bright_ratio = self._is_blank_image(captured, return_ratio=True)
                logger.info(
                    "[%s] Fallback: retry brightness - %.3f%% bright pixels",
                    plugin_id, bright_ratio * 100
                )

                if is_blank:
                    logger.warning(
                        "[%s] Fallback: BLANK IMAGE after retry (%.3f%% bright, size=%dx%d)",
                        plugin_id, bright_ratio * 100,
                        captured.width, captured.height
                    )
                    return None

            # Convert to RGB if needed
            if captured.mode != 'RGB':
                captured = captured.convert('RGB')

            logger.info(
                "[%s] Fallback: SUCCESS - captured %dx%d",
                plugin_id, captured.width, captured.height
            )

            return [captured]

        except (AttributeError, TypeError, ValueError, OSError, RuntimeError) as e:
            logger.exception(
                "[%s] Fallback: ERROR capturing display: %s",
                plugin_id, e
            )
            return None

        finally:
            # Always restore original image to prevent display corruption
            if original_image is not None:
                self.display_manager.image = original_image
                logger.debug("[%s] Fallback: restored original display state", plugin_id)

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
