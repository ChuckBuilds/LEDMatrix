"""
Plugin State Management

Manages plugin state machine (loaded → enabled → running → error)
with state transitions and queries.
"""

from enum import Enum
from typing import Optional, Dict, Any
from datetime import datetime
import logging

from src.logging_config import get_logger


class PluginState(Enum):
    """Plugin state enumeration."""
    UNLOADED = "unloaded"  # Plugin not loaded
    LOADED = "loaded"  # Plugin module loaded but not instantiated
    ENABLED = "enabled"  # Plugin instantiated and enabled
    RUNNING = "running"  # Plugin is currently executing
    ERROR = "error"  # Plugin encountered an error
    DISABLED = "disabled"  # Plugin is disabled in config


class PluginStateManager:
    """Manages plugin state transitions and queries."""
    
    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize the plugin state manager.
        
        Args:
            logger: Optional logger instance
        """
        self.logger = logger or get_logger(__name__)
        self._states: Dict[str, PluginState] = {}
        self._state_history: Dict[str, list] = {}
        self._error_info: Dict[str, Dict[str, Any]] = {}
        self._last_update: Dict[str, datetime] = {}
        self._last_display: Dict[str, datetime] = {}
    
    def set_state(
        self,
        plugin_id: str,
        state: PluginState,
        error: Optional[Exception] = None
    ) -> None:
        """
        Set plugin state and record transition.
        
        Args:
            plugin_id: Plugin identifier
            state: New state
            error: Optional error if transitioning to ERROR state
        """
        old_state = self._states.get(plugin_id, PluginState.UNLOADED)
        self._states[plugin_id] = state
        
        # Record state transition
        if plugin_id not in self._state_history:
            self._state_history[plugin_id] = []
        
        transition = {
            'timestamp': datetime.now(),
            'from': old_state.value,
            'to': state.value,
            'error': str(error) if error else None
        }
        self._state_history[plugin_id].append(transition)
        
        # Store error info if transitioning to ERROR state
        if state == PluginState.ERROR and error:
            self._error_info[plugin_id] = {
                'error': str(error),
                'error_type': type(error).__name__,
                'timestamp': datetime.now()
            }
        elif state != PluginState.ERROR:
            # Clear error info when leaving ERROR state
            self._error_info.pop(plugin_id, None)
        
        self.logger.debug(
            "Plugin %s state transition: %s → %s",
            plugin_id,
            old_state.value,
            state.value
        )
    
    def get_state(self, plugin_id: str) -> PluginState:
        """
        Get current state of a plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Current plugin state
        """
        return self._states.get(plugin_id, PluginState.UNLOADED)
    
    def is_loaded(self, plugin_id: str) -> bool:
        """Check if plugin is loaded."""
        state = self.get_state(plugin_id)
        return state in [PluginState.LOADED, PluginState.ENABLED, PluginState.RUNNING]
    
    def is_enabled(self, plugin_id: str) -> bool:
        """Check if plugin is enabled."""
        state = self.get_state(plugin_id)
        return state == PluginState.ENABLED
    
    def is_running(self, plugin_id: str) -> bool:
        """Check if plugin is currently running."""
        state = self.get_state(plugin_id)
        return state == PluginState.RUNNING
    
    def is_error(self, plugin_id: str) -> bool:
        """Check if plugin is in error state."""
        state = self.get_state(plugin_id)
        return state == PluginState.ERROR
    
    def can_execute(self, plugin_id: str) -> bool:
        """Check if plugin can execute (update/display)."""
        state = self.get_state(plugin_id)
        return state == PluginState.ENABLED
    
    def get_state_history(self, plugin_id: str) -> list:
        """
        Get state transition history for a plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            List of state transitions
        """
        return self._state_history.get(plugin_id, [])
    
    def get_error_info(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """
        Get error information for a plugin in ERROR state.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Error information dict or None
        """
        return self._error_info.get(plugin_id)
    
    def record_update(self, plugin_id: str) -> None:
        """Record that plugin update() was called."""
        self._last_update[plugin_id] = datetime.now()
    
    def record_display(self, plugin_id: str) -> None:
        """Record that plugin display() was called."""
        self._last_display[plugin_id] = datetime.now()
    
    def get_last_update(self, plugin_id: str) -> Optional[datetime]:
        """Get timestamp of last update() call."""
        return self._last_update.get(plugin_id)
    
    def get_last_display(self, plugin_id: str) -> Optional[datetime]:
        """Get timestamp of last display() call."""
        return self._last_display.get(plugin_id)
    
    def get_state_info(self, plugin_id: str) -> Dict[str, Any]:
        """
        Get comprehensive state information for a plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Dictionary with state information
        """
        state = self.get_state(plugin_id)
        info = {
            'state': state.value,
            'is_loaded': self.is_loaded(plugin_id),
            'is_enabled': self.is_enabled(plugin_id),
            'is_running': self.is_running(plugin_id),
            'is_error': self.is_error(plugin_id),
            'can_execute': self.can_execute(plugin_id),
            'last_update': self.get_last_update(plugin_id),
            'last_display': self.get_last_display(plugin_id),
            'error_info': self.get_error_info(plugin_id),
            'state_history_count': len(self.get_state_history(plugin_id))
        }
        return info
    
    def clear_state(self, plugin_id: str) -> None:
        """Clear all state information for a plugin."""
        self._states.pop(plugin_id, None)
        self._state_history.pop(plugin_id, None)
        self._error_info.pop(plugin_id, None)
        self._last_update.pop(plugin_id, None)
        self._last_display.pop(plugin_id, None)

