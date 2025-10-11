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


class BasePlugin(ABC):
    """
    Base class that all plugins must inherit from.
    Provides standard interface and helper methods.
    
    This is the core plugin interface that all plugins must implement.
    Provides common functionality for logging, configuration, and
    integration with the LEDMatrix core system.
    """
    
    API_VERSION = "1.0.0"
    
    def __init__(self, plugin_id: str, config: Dict[str, Any], 
                 display_manager, cache_manager, plugin_manager):
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
        self.enabled = config.get('enabled', True)
        
        self.logger.info(f"Initialized plugin: {plugin_id}")
        
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
        pass
    
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
        pass
    
    def get_display_duration(self) -> float:
        """
        Get the display duration for this plugin instance.
        
        Can be overridden by plugins to provide dynamic durations based
        on content (e.g., longer duration for more complex displays).
        
        Returns:
            Duration in seconds to display this plugin's content
        """
        return self.config.get('display_duration', 15.0)
    
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
                        self.logger.error(f"Missing required field: {field}")
                        return False
                return True
        """
        # Basic validation - check that enabled is a boolean if present
        if 'enabled' in self.config:
            if not isinstance(self.config['enabled'], bool):
                self.logger.error("'enabled' must be a boolean")
                return False
        
        # Check display_duration if present
        if 'display_duration' in self.config:
            duration = self.config['display_duration']
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
        self.logger.info(f"Cleaning up plugin: {self.plugin_id}")
    
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
            'id': self.plugin_id,
            'enabled': self.enabled,
            'config': self.config,
            'api_version': self.API_VERSION
        }
    
    def on_enable(self) -> None:
        """
        Called when plugin is enabled.
        
        Override this method to perform any actions needed when the
        plugin is enabled (e.g., start background tasks, open connections).
        """
        self.enabled = True
        self.logger.info(f"Plugin enabled: {self.plugin_id}")
    
    def on_disable(self) -> None:
        """
        Called when plugin is disabled.
        
        Override this method to perform any actions needed when the
        plugin is disabled (e.g., stop background tasks, close connections).
        """
        self.enabled = False
        self.logger.info(f"Plugin disabled: {self.plugin_id}")

