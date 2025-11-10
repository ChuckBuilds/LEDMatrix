"""
Base Plugin Interface

All LEDMatrix plugins must inherit from BasePlugin and implement
the required abstract methods: update() and display().

API Version: 1.0.0
Stability: Stable - maintains backward compatibility
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import logging
from PIL import Image

# Import transition systems
try:
    from ..display_transitions import DisplayTransitions
except ImportError:
    DisplayTransitions = None

try:
    from ..high_performance_transitions import HighPerformanceDisplayTransitions, create_high_performance_transition_manager
except ImportError:
    HighPerformanceDisplayTransitions = None
    create_high_performance_transition_manager = None


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
        display_manager,
        cache_manager,
        plugin_manager,
    ):
        """
        Standard initialization for all plugins.

        Args:
            plugin_id: Unique identifier for this plugin instance
            config: Plugin-specific configuration dictionary
            display_manager: Shared display manager instance for rendering
            cache_manager: Shared cache manager instance for data persistence
            plugin_manager: Reference to plugin manager for inter-plugin communication
        """
        self.plugin_id = plugin_id
        self.config = config
        self.display_manager = display_manager
        self.cache_manager = cache_manager
        self.plugin_manager = plugin_manager
        self.logger = logging.getLogger(f"plugin.{plugin_id}")
        self.enabled = config.get("enabled", True)

        # Initialize transition system
        self.transition_manager = None
        self.high_performance_mode = config.get("high_performance_transitions", False)
        
        if self.high_performance_mode and HighPerformanceDisplayTransitions:
            try:
                self.transition_manager = create_high_performance_transition_manager(display_manager)
                self.logger.info("High-performance transition system enabled (120 FPS)")
            except Exception as e:  # pylint: disable=broad-except
                self.logger.warning(
                    "Failed to initialize high-performance transitions: %s", e
                )
                self.transition_manager = None
        
        if not self.transition_manager and DisplayTransitions and display_manager:
            try:
                self.transition_manager = DisplayTransitions(display_manager)
                self.logger.info("Standard transition system enabled (30 FPS)")
            except Exception as e:  # pylint: disable=broad-except
                self.logger.warning("Failed to initialize transition system: %s", e)
                self.transition_manager = None

        # Load transition configuration
        self.transition_config = self._load_transition_config()

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

        Can be overridden by plugins to provide dynamic durations based
        on content (e.g., longer duration for more complex displays).

        Returns:
            Duration in seconds to display this plugin's content
        """
        return self.config.get("display_duration", 15.0)

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

    def get_live_modes(self) -> list:
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

        # Validate transition configuration if present
        if "transition" in self.config:
            if not self._validate_transition_config():
                return False

        return True

    def _validate_transition_config(self) -> bool:
        """
        Validate transition configuration.

        Returns:
            True if transition config is valid, False otherwise
        """
        if not self.transition_manager:
            # Skip validation if transition system not available
            return True

        transition_config = self.config.get("transition", {})
        is_valid, error_msg = self.transition_manager.validate_transition_config(
            transition_config
        )

        if not is_valid:
            self.logger.error("Invalid transition configuration: %s", error_msg)
            return False

        # Log warnings for potentially problematic configurations
        transition_type = transition_config.get("type", "redraw")
        if self.transition_manager:
            recommendations = self.transition_manager.get_recommended_transitions()
            avoid_list = recommendations.get("avoid", [])

            if transition_type in avoid_list:
                self.logger.warning(
                    "Transition type '%s' may not work well with %s display (%sx%s)",
                    transition_type,
                    recommendations["aspect_ratio"],
                    self.transition_manager.width,
                    self.transition_manager.height,
                )

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
        The default implementation updates the in-memory config and refreshes
        transition settings if relevant keys are present.

        Args:
            new_config: The full, merged configuration for this plugin (including
                        any secret-derived values that are merged at runtime).
        """
        # Update config reference
        self.config = new_config or {}

        # Update simple flags
        self.enabled = self.config.get("enabled", self.enabled)
        self.high_performance_mode = self.config.get("high_performance_transitions", self.high_performance_mode)

        # Rebuild transition configuration if provided
        try:
            self.transition_config = self._load_transition_config()
        except Exception as e:  # pylint: disable=broad-except
            self.logger.warning(
                "Failed to refresh transition configuration after config change: %s", e
            )

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

    def _load_transition_config(self) -> Dict[str, Any]:
        """
        Load transition configuration from plugin config.

        Returns:
            Transition configuration dictionary
        """
        transition_config = self.config.get("transition", {})

        # Set defaults
        default_config = {"type": "redraw", "speed": 2, "enabled": True}

        # Merge with defaults
        config = default_config.copy()
        config.update(transition_config)

        return config

    def apply_transition(
        self, new_image: Image.Image, transition_config: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Apply transition to display new image.

        Args:
            new_image: PIL Image to transition to
            transition_config: Optional transition config override
        """
        if not self.transition_manager:
            # Fallback to simple redraw if transition system not available
            self.display_manager.image = new_image.copy()
            self.display_manager.update_display()
            return

        # Use provided config or default
        config = transition_config or self.transition_config

        # Get current display image for transition
        current_image = None
        if hasattr(self.display_manager, "image") and self.display_manager.image:
            current_image = self.display_manager.image

        # Apply transition
        self.transition_manager.transition(current_image, new_image, config)

    def get_transition_recommendations(self) -> Dict[str, Any]:
        """
        Get transition recommendations based on display dimensions.

        Returns:
            Dict with aspect ratio info and recommendations
        """
        if not self.transition_manager:
            return {"available": False, "reason": "Transition system not initialized"}

        return {
            "available": True,
            "recommendations": self.transition_manager.get_recommended_transitions(),
            "current_config": self.transition_config,
        }
