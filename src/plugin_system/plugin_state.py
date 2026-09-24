"""
Plugin State Management

Manages plugin state machine (loaded → enabled → running → error)
with state transitions and queries.
"""

import threading
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
        self._lock = threading.RLock()
        self._states: Dict[str, PluginState] = {}
        # Lifetime transition totals, reported by get_state_info().
        self._state_transition_counts: Dict[str, int] = {}
        self._error_info: Dict[str, Dict[str, Any]] = {}
        self._last_update: Dict[str, datetime] = {}
    
    def _record_transition(self, plugin_id: str) -> None:
        """Count a state transition. Callers must already hold ``_lock``."""
        self._state_transition_counts[plugin_id] = (
            self._state_transition_counts.get(plugin_id, 0) + 1
        )

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
        with self._lock:
            old_state = self._states.get(plugin_id, PluginState.UNLOADED)
            self._states[plugin_id] = state
            self._record_transition(plugin_id)

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
    
    def set_state_with_error(
        self,
        plugin_id: str,
        state: PluginState,
        error_info: Dict[str, Any],
    ) -> None:
        """Set plugin state and persist error context atomically.

        Holds ``_lock`` for both writes so no reader can observe the new
        state without the accompanying error context.

        Intentionally does not clear ``_error_info`` the way set_state() does
        for non-ERROR transitions — this is the recoverable-failure path where
        the error dict is the entire point.

        Args:
            plugin_id: Plugin identifier
            state: New state
            error_info: Structured error dict to persist alongside the state
        """
        with self._lock:
            old_state = self._states.get(plugin_id, PluginState.UNLOADED)
            self._states[plugin_id] = state
            self._record_transition(plugin_id)
            self._error_info[plugin_id] = dict(error_info)

            self.logger.debug(
                "Plugin %s state transition: %s → %s (recoverable error stored)",
                plugin_id,
                old_state.value,
                state.value,
            )

    def get_error_info(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """
        Get error information for a plugin.

        Returns the stored error dict whether the plugin is in ERROR state or
        still ENABLED after a recoverable failure. Returns a shallow copy so
        callers cannot mutate the stored snapshot.

        Args:
            plugin_id: Plugin identifier

        Returns:
            Copy of the error information dict, or None
        """
        with self._lock:
            info = self._error_info.get(plugin_id)
            return dict(info) if info is not None else None
    
    def record_update(self, plugin_id: str) -> None:
        """Record that plugin update() was called."""
        self._last_update[plugin_id] = datetime.now()
    
    def get_last_update(self, plugin_id: str) -> Optional[datetime]:
        """Get timestamp of last update() call."""
        return self._last_update.get(plugin_id)

    def get_state_info(self, plugin_id: str) -> Dict[str, Any]:
        """
        Get comprehensive state information for a plugin.
        
        Args:
            plugin_id: Plugin identifier
            
        Returns:
            Dictionary with state information
        """
        # One snapshot, one critical section. Each field was read under its own
        # lock, so an unload running concurrently could be observed half-done:
        # 'state' read before clear_state() removed it and
        # 'state_history_count' read after, giving a caller a plugin that is
        # ENABLED with zero transitions. _lock is an RLock, so the helpers
        # below can still take it.
        with self._lock:
            state = self.get_state(plugin_id)
            info = {
                'state': state.value,
                'is_loaded': self.is_loaded(plugin_id),
                'is_enabled': self.is_enabled(plugin_id),
                'is_running': self.is_running(plugin_id),
                'is_error': self.is_error(plugin_id),
                'can_execute': self.can_execute(plugin_id),
                'last_update': self.get_last_update(plugin_id),
                'error_info': self.get_error_info(plugin_id),
                'state_history_count': self._state_transition_counts.get(plugin_id, 0)
            }
        return info
    
    def clear_state(self, plugin_id: str) -> None:
        """Clear all state information for a plugin.

        Held under ``_lock`` so the dicts are dropped as one unit: every
        other mutator takes the lock, and without it a concurrent set_state()
        could interleave and leave a plugin with a transition count but no
        state.
        """
        with self._lock:
            self._states.pop(plugin_id, None)
            self._state_transition_counts.pop(plugin_id, None)
            self._error_info.pop(plugin_id, None)
            self._last_update.pop(plugin_id, None)

