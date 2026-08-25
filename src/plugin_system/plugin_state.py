"""
Plugin State Management

Manages plugin state machine (loaded → enabled → running → error)
with state transitions and queries.
"""

import threading
import time
from collections import deque
from enum import Enum
from typing import Optional, Dict, Any, Deque, List, Tuple
from datetime import datetime
import logging

from src.logging_config import get_logger


# The history is diagnostic only -- nothing reads the entries themselves, just
# their count -- but it is appended to on the hot scheduling path: every update
# cycle records RUNNING on reserve and ENABLED on finish. Unbounded, that is
# 2,880 entries per plugin per day at the default 60s interval, which on a 1 GB
# Pi exhausts memory in weeks.
#
# Two limits, because a single entry count answers the wrong question. What a
# reader wants is "the last couple of hours", and how many transitions that is
# depends entirely on the plugin's update interval -- which on a real board
# spans 2s to 3600s. A flat 200 entries is 4.2 days for the slowest plugin and
# 3.3 minutes for the fastest, so the plugin churning hardest, the one worth
# looking at, keeps the least history.
#
# So: trim by AGE first, which makes the retained window comparable across
# plugins whatever their cadence...
STATE_HISTORY_MAX_AGE_SECONDS = 2 * 60 * 60

# ...and cap by COUNT second, purely as a memory ceiling for the fast pollers
# whose age window would otherwise run to thousands of entries. At ~230 bytes
# an entry this is ~0.5 MB per plugin worst case, and only plugins updating
# faster than roughly every 4s can reach it.
MAX_STATE_HISTORY_PER_PLUGIN = 2000


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
        # (monotonic timestamp, transition). The clock is monotonic so a DST
        # shift or an NTP step cannot make entries look old and flush the
        # history; the human-readable timestamp lives inside the transition.
        self._state_history: Dict[str, Deque[Tuple[float, Dict[str, Any]]]] = {}
        # Lifetime transition totals, kept separately so the count reported by
        # get_state_info() stays truthful once the history above starts rolling.
        self._state_transition_counts: Dict[str, int] = {}
        self._error_info: Dict[str, Dict[str, Any]] = {}
        self._last_update: Dict[str, datetime] = {}
        self._last_display: Dict[str, datetime] = {}
    
    def _record_transition(
        self,
        plugin_id: str,
        transition: Dict[str, Any]
    ) -> None:
        """Append a transition to the plugin's bounded history.

        Callers must already hold ``_lock``. The deque discards its oldest
        entry once it is full, so the history cannot grow without bound; the
        lifetime total is tracked separately for get_state_info().
        """
        history = self._state_history.get(plugin_id)
        if history is None:
            history = deque(maxlen=MAX_STATE_HISTORY_PER_PLUGIN)
            self._state_history[plugin_id] = history
        now = time.monotonic()
        history.append((now, transition))
        # Age out first; the deque's maxlen is the backstop for plugins that
        # produce more than the ceiling within the window.
        cutoff = now - STATE_HISTORY_MAX_AGE_SECONDS
        while history and history[0][0] < cutoff:
            history.popleft()
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

            transition = {
                'timestamp': datetime.now(),
                'from': old_state.value,
                'to': state.value,
                'error': str(error) if error else None
            }
            self._record_transition(plugin_id, transition)

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
    
    def get_state_history(self, plugin_id: str) -> List[Dict[str, Any]]:
        """
        Get state transition history for a plugin.

        Retention is by age first -- transitions older than
        STATE_HISTORY_MAX_AGE_SECONDS are dropped -- and by count second, at
        MAX_STATE_HISTORY_PER_PLUGIN, which only binds for plugins updating
        fast enough to exceed it inside that window.

        Args:
            plugin_id: Plugin identifier

        Returns:
            List of recent state transitions, oldest first. Both the list and
            the transition dicts are copies, so callers cannot mutate the
            manager's own history. The values inside a transition are all
            immutable, so a shallow copy per entry is enough.
        """
        with self._lock:
            return [
                dict(transition)
                for _stamp, transition in self._state_history.get(plugin_id, ())
            ]
    
    def set_error_info(self, plugin_id: str, error_info: Dict[str, Any]) -> None:
        """
        Persist structured error context without changing plugin state.

        Used for recoverable failures (e.g. update timeout) where the plugin
        stays ENABLED but the error details should remain queryable.

        Args:
            plugin_id: Plugin identifier
            error_info: Arbitrary dict describing the error
        """
        with self._lock:
            self._error_info[plugin_id] = dict(error_info)

    def set_state_with_error(
        self,
        plugin_id: str,
        state: PluginState,
        error_info: Dict[str, Any],
        error: Optional[Exception] = None,
    ) -> None:
        """Set plugin state and persist error context atomically.

        Unlike calling set_state() then set_error_info() separately, this
        method holds ``_lock`` for both writes so no reader can observe the
        new state without the accompanying error context.

        Intentionally does not clear ``_error_info`` the way set_state() does
        for non-ERROR transitions — this is the recoverable-failure path where
        the error dict is the entire point.

        Args:
            plugin_id: Plugin identifier
            state: New state
            error_info: Structured error dict to persist alongside the state
            error: Optional exception recorded in the transition history
        """
        with self._lock:
            old_state = self._states.get(plugin_id, PluginState.UNLOADED)
            self._states[plugin_id] = state

            self._record_transition(plugin_id, {
                'timestamp': datetime.now(),
                'from': old_state.value,
                'to': state.value,
                'error': str(error) if error else None,
            })

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
            'state_history_count': self._state_transition_counts.get(plugin_id, 0)
        }
        return info
    
    def clear_state(self, plugin_id: str) -> None:
        """Clear all state information for a plugin.

        Held under ``_lock`` so the five dicts are dropped as one unit: every
        other mutator takes the lock, and without it a concurrent set_state()
        could interleave and leave a plugin with history but no state.
        """
        with self._lock:
            self._states.pop(plugin_id, None)
            self._state_history.pop(plugin_id, None)
            self._state_transition_counts.pop(plugin_id, None)
            self._error_info.pop(plugin_id, None)
            self._last_update.pop(plugin_id, None)
            self._last_display.pop(plugin_id, None)

