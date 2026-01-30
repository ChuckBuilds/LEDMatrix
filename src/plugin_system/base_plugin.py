"""
Base Plugin Interface

All LEDMatrix plugins must inherit from BasePlugin and implement
the required abstract methods: update() and display().

API Version: 1.0.0
Stability: Stable - maintains backward compatibility
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any, Optional, List
import logging
from src.logging_config import get_logger


class VegasDisplayMode(Enum):
    """
    Display mode for Vegas scroll integration.

    Determines how a plugin's content behaves within the continuous scroll:

    - SCROLL: Content scrolls continuously within the stream.
      Best for multi-item plugins like sports scores, odds tickers, news feeds.
      Plugin provides multiple frames via get_vegas_content().

    - FIXED_SEGMENT: Content is a fixed-width block that scrolls BY with
      the rest of the content. Best for static info like clock, weather.
      Plugin provides a single image sized to vegas_panel_count panels.

    - STATIC: Scroll pauses, plugin displays for its duration, then scroll
      resumes. Best for important alerts or detailed views that need attention.
      Plugin uses standard display() method during the pause.
    """
    SCROLL = "scroll"
    FIXED_SEGMENT = "fixed"
    STATIC = "static"


class BasePlugin(ABC):
    """
    Base class that all plugins must inherit from.
    Provides standard interface and helper methods.

    This is the core plugin interface that all plugins must implement.
    Provides common functionality for logging, configuration, and
    integration with the LEDMatrix core system.
    """

    API_VERSION = "1.0.0"

    def __init__(
        self,
        plugin_id: str,
        config: Dict[str, Any],
        display_manager: Any,
        cache_manager: Any,
        plugin_manager: Any,
    ) -> None:
        """
        Standard initialization for all plugins.

        Args:
            plugin_id: Unique identifier for this plugin instance
            config: Plugin-specific configuration dictionary
            display_manager: Shared display manager instance for rendering
            cache_manager: Shared cache manager instance for data persistence
            plugin_manager: Reference to plugin manager for inter-plugin communication
        """
        self.plugin_id: str = plugin_id
        self.config: Dict[str, Any] = config
        self.display_manager: Any = display_manager
        self.cache_manager: Any = cache_manager
        self.plugin_manager: Any = plugin_manager
        self.logger: logging.Logger = get_logger(f"plugin.{plugin_id}", plugin_id=plugin_id)
        self.enabled: bool = config.get("enabled", True)

        self.logger.info("Initialized plugin: %s", plugin_id)

    @abstractmethod
    def update(self) -> None:
        """
        Fetch/update data for this plugin.

        This method is called based on update_interval specified in the
        plugin's manifest. It should fetch any necessary data from APIs,
        databases, or other sources and prepare it for display.

        Use the cache_manager for caching API responses to avoid
        excessive requests.

        Example:
            def update(self):
                cache_key = f"{self.plugin_id}_data"
                cached = self.cache_manager.get(cache_key, max_age=3600)
                if cached:
                    self.data = cached
                    return

                self.data = self._fetch_from_api()
                self.cache_manager.set(cache_key, self.data)
        """
        raise NotImplementedError("Plugins must implement update()")

    @abstractmethod
    def display(self, force_clear: bool = False) -> None:
        """
        Render this plugin's display.

        This method is called during the display rotation or when the plugin
        is explicitly requested to render. It should use the display_manager
        to draw content on the LED matrix.

        Args:
            force_clear: If True, clear display before rendering

        Example:
            def display(self, force_clear=False):
                if force_clear:
                    self.display_manager.clear()

                self.display_manager.draw_text(
                    "Hello, World!",
                    x=5, y=15,
                    color=(255, 255, 255)
                )

                self.display_manager.update_display()
        """
        raise NotImplementedError("Plugins must implement display()")

    def get_display_duration(self) -> float:
        """
        Get the display duration for this plugin instance.

        Automatically detects duration from:
        1. self.display_duration instance variable (if exists)
        2. self.config.get("display_duration", 15.0) (fallback)

        Can be overridden by plugins to provide dynamic durations based
        on content (e.g., longer duration for more complex displays).

        Returns:
            Duration in seconds to display this plugin's content
        """
        # Check for instance variable first (common pattern in scoreboard plugins)
        if hasattr(self, 'display_duration'):
            try:
                duration = getattr(self, 'display_duration')
                # Handle None case
                if duration is None:
                    pass  # Fall through to config
                # Try to convert to float if it's a number or numeric string
                elif isinstance(duration, (int, float)):
                    if duration > 0:
                        return float(duration)
                    else:
                        self.logger.debug(
                            "display_duration instance variable is non-positive (%s), using config fallback",
                            duration
                        )
                # Try converting string representations of numbers
                elif isinstance(duration, str):
                    try:
                        duration_float = float(duration)
                        if duration_float > 0:
                            return duration_float
                        else:
                            self.logger.debug(
                                "display_duration string value is non-positive (%s), using config fallback",
                                duration
                            )
                    except (ValueError, TypeError):
                        self.logger.warning(
                            "display_duration instance variable has invalid string value '%s', using config fallback",
                            duration
                        )
                else:
                    self.logger.warning(
                        "display_duration instance variable has unexpected type %s (value: %s), using config fallback",
                        type(duration).__name__, duration
                    )
            except (TypeError, ValueError, AttributeError) as e:
                self.logger.warning(
                    "Error reading display_duration instance variable: %s, using config fallback",
                    e
                )

        # Fall back to config
        config_duration = self.config.get("display_duration", 15.0)
        try:
            # Ensure config value is also a valid float
            if isinstance(config_duration, (int, float)):
                if config_duration > 0:
                    return float(config_duration)
                else:
                    self.logger.debug(
                        "Config display_duration is non-positive (%s), using default 15.0",
                        config_duration
                    )
                    return 15.0
            elif isinstance(config_duration, str):
                try:
                    duration_float = float(config_duration)
                    if duration_float > 0:
                        return duration_float
                    else:
                        self.logger.debug(
                            "Config display_duration string is non-positive (%s), using default 15.0",
                            config_duration
                        )
                        return 15.0
                except ValueError:
                    self.logger.warning(
                        "Config display_duration has invalid string value '%s', using default 15.0",
                        config_duration
                    )
                    return 15.0
            else:
                self.logger.warning(
                    "Config display_duration has unexpected type %s (value: %s), using default 15.0",
                    type(config_duration).__name__, config_duration
                )
        except (ValueError, TypeError) as e:
            self.logger.warning(
                "Error processing config display_duration: %s, using default 15.0",
                e
            )

        return 15.0

    # ---------------------------------------------------------------------
    # Dynamic duration support hooks
    # ---------------------------------------------------------------------
    def _get_dynamic_duration_config(self) -> Dict[str, Any]:
        """
        Retrieve dynamic duration configuration block from plugin config.

        Returns:
            Dict with configuration values or empty dict if not configured.
        """
        value = self.config.get("dynamic_duration", {})
        if isinstance(value, dict):
            return value
        return {}

    def supports_dynamic_duration(self) -> bool:
        """
        Determine whether this plugin should use dynamic display durations.

        Plugins can override to implement custom logic. By default this reads the
        `dynamic_duration.enabled` flag from plugin configuration.
        """
        config = self._get_dynamic_duration_config()
        return bool(config.get("enabled", False))

    def get_dynamic_duration_cap(self) -> Optional[float]:
        """
        Return the maximum duration (in seconds) the controller should wait for
        this plugin to complete its display cycle when using dynamic duration.

        Returns:
            Positive float value for explicit cap, or None to indicate no
            additional cap beyond global defaults.
        """
        config = self._get_dynamic_duration_config()
        cap_value = config.get("max_duration_seconds")
        if cap_value is None:
            return None
        try:
            cap = float(cap_value)
            if cap <= 0:
                return None
            return cap
        except (TypeError, ValueError):
            self.logger.warning(
                "Invalid dynamic_duration.max_duration_seconds for %s: %s",
                self.plugin_id,
                cap_value,
            )
            return None

    def is_cycle_complete(self) -> bool:
        """
        Indicate whether the plugin has completed a full display cycle.

        The display controller calls this after each display iteration when
        dynamic duration is enabled. Plugins that render multi-step content
        should override this method and return True only after all content has
        been shown once.

        Returns:
            True if the plugin cycle is complete (default behaviour).
        """
        return True

    def reset_cycle_state(self) -> None:
        """
        Reset any internal counters/state related to cycle tracking.

        Called by the display controller before beginning a new dynamic-duration
        session. Override in plugins that maintain custom tracking data.
        """
        return

    def has_live_priority(self) -> bool:
        """
        Check if this plugin has live priority enabled.

        Live priority allows a plugin to take over the display when it has
        live/urgent content (e.g., live sports games, breaking news).

        Returns:
            True if live priority is enabled in config, False otherwise
        """
        return self.config.get("live_priority", False)

    def has_live_content(self) -> bool:
        """
        Check if this plugin currently has live content to display.

        Override this method in your plugin to implement live content detection.
        This is called by the display controller to determine if a live priority
        plugin should take over the display.

        Returns:
            True if plugin has live content, False otherwise

        Example (sports plugin):
            def has_live_content(self):
                # Check if there are any live games
                return hasattr(self, 'live_games') and len(self.live_games) > 0

        Example (news plugin):
            def has_live_content(self):
                # Check if there's breaking news
                return hasattr(self, 'breaking_news') and self.breaking_news
        """
        return False

    def get_live_modes(self) -> List[str]:
        """
        Get list of display modes that should be used during live priority takeover.

        Override this method to specify which modes should be shown when this
        plugin has live content. By default, returns all display modes from manifest.

        Returns:
            List of mode names to display during live priority

        Example:
            def get_live_modes(self):
                # Only show live game mode, not upcoming/recent
                return ['nhl_live', 'nba_live']
        """
        # Get display modes from manifest via plugin manager
        if self.plugin_manager and hasattr(self.plugin_manager, "plugin_manifests"):
            manifest = self.plugin_manager.plugin_manifests.get(self.plugin_id, {})
            return manifest.get("display_modes", [self.plugin_id])
        return [self.plugin_id]

    # -------------------------------------------------------------------------
    # Vegas scroll mode support
    # -------------------------------------------------------------------------
    def get_vegas_content(self) -> Optional[Any]:
        """
        Get content for Vegas-style continuous scroll mode.

        Override this method to provide optimized content for continuous scrolling.
        Plugins can return:
        - A single PIL Image: Displayed as a static block in the scroll
        - A list of PIL Images: Each image becomes a separate item in the scroll
        - None: Vegas mode will fall back to capturing display() output

        Multi-item plugins (sports scores, odds) should return individual game/item
        images so they scroll smoothly with other plugins.

        Returns:
            PIL Image, list of PIL Images, or None

        Example (sports plugin):
            def get_vegas_content(self):
                # Return individual game cards for smooth scrolling
                return [self._render_game(game) for game in self.games]

        Example (static plugin):
            def get_vegas_content(self):
                # Return current display as single block
                return self._render_current_view()
        """
        return None

    def get_vegas_content_type(self) -> str:
        """
        Indicate the type of content this plugin provides for Vegas scroll.

        Override this to specify how Vegas mode should treat this plugin's content.

        Returns:
            'multi' - Plugin has multiple scrollable items (sports, odds, news)
            'static' - Plugin is a static block (clock, weather, music)
            'none' - Plugin should not appear in Vegas scroll mode

        Example:
            def get_vegas_content_type(self):
                return 'multi'  # We have multiple games to scroll
        """
        return 'static'

    def get_vegas_display_mode(self) -> VegasDisplayMode:
        """
        Get the display mode for Vegas scroll integration.

        This method determines how the plugin's content behaves within Vegas mode:
        - SCROLL: Content scrolls continuously (multi-item plugins)
        - FIXED_SEGMENT: Fixed block that scrolls by (clock, weather)
        - STATIC: Pause scroll to display (alerts, detailed views)

        Override to change default behavior. By default, reads from config
        or maps legacy get_vegas_content_type() for backward compatibility.

        Returns:
            VegasDisplayMode enum value

        Example:
            def get_vegas_display_mode(self):
                return VegasDisplayMode.SCROLL
        """
        # Check for explicit config setting first
        config_mode = self.config.get("vegas_mode")
        if config_mode:
            try:
                return VegasDisplayMode(config_mode)
            except ValueError:
                self.logger.warning(
                    "Invalid vegas_mode '%s' for %s, using default",
                    config_mode, self.plugin_id
                )

        # Fall back to mapping legacy content_type
        content_type = self.get_vegas_content_type()
        if content_type == 'multi':
            return VegasDisplayMode.SCROLL
        elif content_type == 'static':
            return VegasDisplayMode.FIXED_SEGMENT
        elif content_type == 'none':
            # 'none' means excluded - return FIXED_SEGMENT as default
            # The exclusion is handled by checking get_vegas_content_type() separately
            return VegasDisplayMode.FIXED_SEGMENT

        return VegasDisplayMode.FIXED_SEGMENT

    def get_supported_vegas_modes(self) -> List[VegasDisplayMode]:
        """
        Return list of Vegas display modes this plugin supports.

        Used by the web UI to show available mode options for user configuration.
        Override to customize which modes are available for this plugin.

        By default:
        - 'multi' content type plugins support SCROLL and FIXED_SEGMENT
        - 'static' content type plugins support FIXED_SEGMENT and STATIC
        - 'none' content type plugins return empty list (excluded from Vegas)

        Returns:
            List of VegasDisplayMode values this plugin can use

        Example:
            def get_supported_vegas_modes(self):
                # This plugin only makes sense as a scrolling ticker
                return [VegasDisplayMode.SCROLL]
        """
        content_type = self.get_vegas_content_type()

        if content_type == 'none':
            return []
        elif content_type == 'multi':
            return [VegasDisplayMode.SCROLL, VegasDisplayMode.FIXED_SEGMENT]
        else:  # 'static'
            return [VegasDisplayMode.FIXED_SEGMENT, VegasDisplayMode.STATIC]

    def get_vegas_segment_width(self) -> Optional[int]:
        """
        Get the preferred width for this plugin in Vegas FIXED_SEGMENT mode.

        Returns the number of panels this plugin should occupy when displayed
        as a fixed segment. The actual pixel width is calculated as:
            width = panels * single_panel_width

        Where single_panel_width comes from display.hardware.cols in config.

        Override to provide dynamic sizing based on content.
        Returns None to use the default (1 panel).

        Returns:
            Number of panels, or None for default (1 panel)

        Example:
            def get_vegas_segment_width(self):
                # Clock needs 2 panels to show time clearly
                return 2
        """
        raw_value = self.config.get("vegas_panel_count", None)
        if raw_value is None:
            return None

        try:
            panel_count = int(raw_value)
            if panel_count > 0:
                return panel_count
            else:
                self.logger.warning(
                    "vegas_panel_count must be positive, got %s; using default",
                    raw_value
                )
                return None
        except (ValueError, TypeError):
            self.logger.warning(
                "Invalid vegas_panel_count value '%s'; using default",
                raw_value
            )
            return None

    def validate_config(self) -> bool:
        """
        Validate plugin configuration against schema.

        Called during plugin loading to ensure configuration is valid.
        Override this method to implement custom validation logic.

        Returns:
            True if config is valid, False otherwise

        Example:
            def validate_config(self):
                required_fields = ['api_key', 'city']
                for field in required_fields:
                    if field not in self.config:
                self.logger.error("Missing required field: %s", field)
                        return False
                return True
        """
        # Basic validation - check that enabled is a boolean if present
        if "enabled" in self.config:
            if not isinstance(self.config["enabled"], bool):
                self.logger.error("'enabled' must be a boolean")
                return False

        # Check display_duration if present
        if "display_duration" in self.config:
            duration = self.config["display_duration"]
            if not isinstance(duration, (int, float)) or duration <= 0:
                self.logger.error("'display_duration' must be a positive number")
                return False

        return True

    def cleanup(self) -> None:
        """
        Cleanup resources when plugin is unloaded.

        Override this method to clean up any resources (e.g., close
        file handles, terminate threads, close network connections).

        This method is called when the plugin is unloaded or when the
        system is shutting down.

        Example:
            def cleanup(self):
                if hasattr(self, 'api_client'):
                    self.api_client.close()
                if hasattr(self, 'worker_thread'):
                    self.worker_thread.stop()
        """
        self.logger.info("Cleaning up plugin: %s", self.plugin_id)

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        """
        Called after the plugin configuration has been updated via the web API.

        Plugins may override this to apply changes immediately without a restart.
        The default implementation updates the in-memory config.

        Args:
            new_config: The full, merged configuration for this plugin (including
                        any secret-derived values that are merged at runtime).
        """
        # Update config reference
        self.config = new_config or {}

        # Update simple flags
        self.enabled = self.config.get("enabled", self.enabled)

    def get_info(self) -> Dict[str, Any]:
        """
        Return plugin info for display in web UI.

        Override this method to provide additional information about
        the plugin's current state.

        Returns:
            Dict with plugin information including id, enabled status, and config

        Example:
            def get_info(self):
                info = super().get_info()
                info['games_count'] = len(self.games)
                info['last_update'] = self.last_update_time
                return info
        """
        return {
            "id": self.plugin_id,
            "enabled": self.enabled,
            "config": self.config,
            "api_version": self.API_VERSION,
        }

    def on_enable(self) -> None:
        """
        Called when plugin is enabled.

        Override this method to perform any actions needed when the
        plugin is enabled (e.g., start background tasks, open connections).
        """
        self.enabled = True
        self.logger.info("Plugin enabled: %s", self.plugin_id)

    def on_disable(self) -> None:
        """
        Called when plugin is disabled.

        Override this method to perform any actions needed when the
        plugin is disabled (e.g., stop background tasks, close connections).
        """
        self.enabled = False
        self.logger.info("Plugin disabled: %s", self.plugin_id)
