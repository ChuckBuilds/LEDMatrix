"""
Plugin Health Tracker

Tracks plugin health metrics including success/failure rates, consecutive failures,
and circuit breaker state. Provides automatic recovery mechanisms.
"""

import time
import logging
from typing import Dict, Optional, Any
from enum import Enum


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Circuit open, skipping calls
    HALF_OPEN = "half_open"  # Testing if plugin recovered


class PluginHealthTracker:
    """
    Tracks plugin health and manages circuit breaker state.
    
    Circuit breaker pattern:
    - CLOSED: Plugin is healthy, calls proceed normally
    - OPEN: Plugin has failed too many times, calls are skipped
    - HALF_OPEN: Testing if plugin has recovered (after cooldown)
    """
    
    def __init__(self, cache_manager, failure_threshold: int = 3, 
                 cooldown_period: float = 300.0, half_open_timeout: float = 60.0):
        """
        Initialize plugin health tracker.
        
        Args:
            cache_manager: Cache manager instance for persistence
            failure_threshold: Number of consecutive failures before opening circuit
            cooldown_period: Seconds to wait before attempting recovery (default: 5 minutes)
            half_open_timeout: Seconds to wait in half-open state before closing (default: 1 minute)
        """
        self.cache_manager = cache_manager
        self.failure_threshold = failure_threshold
        self.cooldown_period = cooldown_period
        self.half_open_timeout = half_open_timeout
        self.logger = logging.getLogger(__name__)
        
        # In-memory health state (also persisted to cache)
        self._health_state: Dict[str, Dict[str, Any]] = {}
    
    def _get_health_key(self, plugin_id: str) -> str:
        """Get cache key for plugin health data."""
        return f"plugin_health:{plugin_id}"
    
    def _load_health_state(self, plugin_id: str) -> Dict[str, Any]:
        """Load health state from cache or return defaults."""
        cache_key = self._get_health_key(plugin_id)
        cached = self.cache_manager.get(cache_key, max_age=None)
        
        if cached:
            return cached
        
        # Default state
        return {
            'consecutive_failures': 0,
            'total_failures': 0,
            'total_successes': 0,
            'last_success_time': None,
            'last_failure_time': None,
            'circuit_state': CircuitState.CLOSED.value,
            'circuit_opened_time': None,
            'half_open_start_time': None,
            'last_error': None
        }
    
    def _save_health_state(self, plugin_id: str, state: Dict[str, Any]) -> None:
        """Save health state to cache."""
        cache_key = self._get_health_key(plugin_id)
        self.cache_manager.set(cache_key, state)  # Persist indefinitely
        self._health_state[plugin_id] = state
    
    def get_health_state(self, plugin_id: str) -> Dict[str, Any]:
        """Get current health state for a plugin."""
        if plugin_id not in self._health_state:
            self._health_state[plugin_id] = self._load_health_state(plugin_id)
        return self._health_state[plugin_id]
    
    def record_success(self, plugin_id: str) -> None:
        """Record a successful plugin execution."""
        state = self.get_health_state(plugin_id)
        current_time = time.time()
        
        # Reset consecutive failures
        state['consecutive_failures'] = 0
        state['total_successes'] = state.get('total_successes', 0) + 1
        state['last_success_time'] = current_time
        
        # Update circuit state
        if state['circuit_state'] == CircuitState.HALF_OPEN.value:
            # Success in half-open state, close the circuit
            state['circuit_state'] = CircuitState.CLOSED.value
            state['half_open_start_time'] = None
            self.logger.info(f"Plugin {plugin_id} recovered, circuit closed")
        elif state['circuit_state'] == CircuitState.OPEN.value:
            # Shouldn't happen, but handle it
            state['circuit_state'] = CircuitState.CLOSED.value
            state['circuit_opened_time'] = None
        
        self._save_health_state(plugin_id, state)
    
    def record_failure(self, plugin_id: str, error: Optional[Exception] = None) -> None:
        """Record a failed plugin execution."""
        state = self.get_health_state(plugin_id)
        current_time = time.time()
        
        # Increment failure counters
        state['consecutive_failures'] = state.get('consecutive_failures', 0) + 1
        state['total_failures'] = state.get('total_failures', 0) + 1
        state['last_failure_time'] = current_time
        
        # Store error message
        if error:
            state['last_error'] = str(error)
        
        # Check if we should open the circuit
        if state['consecutive_failures'] >= self.failure_threshold:
            if state['circuit_state'] == CircuitState.CLOSED.value:
                state['circuit_state'] = CircuitState.OPEN.value
                state['circuit_opened_time'] = current_time
                self.logger.warning(
                    f"Plugin {plugin_id} circuit opened after {state['consecutive_failures']} consecutive failures"
                )
            elif state['circuit_state'] == CircuitState.HALF_OPEN.value:
                # Failed again in half-open, reopen circuit
                state['circuit_state'] = CircuitState.OPEN.value
                state['circuit_opened_time'] = current_time
                state['half_open_start_time'] = None
                self.logger.warning(f"Plugin {plugin_id} failed in half-open state, circuit reopened")
        
        self._save_health_state(plugin_id, state)
    
    def should_skip_plugin(self, plugin_id: str) -> bool:
        """
        Check if plugin should be skipped due to circuit breaker.
        
        Returns:
            True if plugin should be skipped, False if it should be called
        """
        state = self.get_health_state(plugin_id)
        current_time = time.time()
        circuit_state = state.get('circuit_state', CircuitState.CLOSED.value)
        
        if circuit_state == CircuitState.CLOSED.value:
            return False
        
        if circuit_state == CircuitState.OPEN.value:
            # Check if cooldown period has passed
            circuit_opened_time = state.get('circuit_opened_time')
            if circuit_opened_time and (current_time - circuit_opened_time) >= self.cooldown_period:
                # Move to half-open state
                state['circuit_state'] = CircuitState.HALF_OPEN.value
                state['half_open_start_time'] = current_time
                state['circuit_opened_time'] = None
                self._save_health_state(plugin_id, state)
                self.logger.info(f"Plugin {plugin_id} circuit moved to half-open state for testing")
                return False  # Allow one attempt
            return True  # Still in cooldown
        
        if circuit_state == CircuitState.HALF_OPEN.value:
            # In half-open state, allow calls but check timeout
            half_open_start = state.get('half_open_start_time')
            if half_open_start and (current_time - half_open_start) >= self.half_open_timeout:
                # Timeout in half-open, close circuit if no failures
                if state.get('consecutive_failures', 0) == 0:
                    state['circuit_state'] = CircuitState.CLOSED.value
                    state['half_open_start_time'] = None
                    self._save_health_state(plugin_id, state)
                    self.logger.info(f"Plugin {plugin_id} circuit closed after successful half-open period")
                return False
            return False  # Allow calls in half-open
        
        return False
    
    def get_health_summary(self, plugin_id: str) -> Dict[str, Any]:
        """Get health summary for a plugin."""
        state = self.get_health_state(plugin_id)
        
        total_calls = state.get('total_successes', 0) + state.get('total_failures', 0)
        success_rate = 0.0
        if total_calls > 0:
            success_rate = state.get('total_successes', 0) / total_calls * 100
        
        return {
            'plugin_id': plugin_id,
            'circuit_state': state.get('circuit_state', CircuitState.CLOSED.value),
            'consecutive_failures': state.get('consecutive_failures', 0),
            'total_failures': state.get('total_failures', 0),
            'total_successes': state.get('total_successes', 0),
            'success_rate': round(success_rate, 2),
            'last_success_time': state.get('last_success_time'),
            'last_failure_time': state.get('last_failure_time'),
            'last_error': state.get('last_error'),
            'is_healthy': state.get('circuit_state') == CircuitState.CLOSED.value,
            'circuit_opened_time': state.get('circuit_opened_time'),
            'half_open_start_time': state.get('half_open_start_time')
        }
    
    def get_all_health_summaries(self) -> Dict[str, Dict[str, Any]]:
        """Get health summaries for all tracked plugins."""
        summaries = {}
        for plugin_id in self._health_state.keys():
            summaries[plugin_id] = self.get_health_summary(plugin_id)
        return summaries
    
    def reset_health(self, plugin_id: str) -> None:
        """Reset health state for a plugin (manual recovery)."""
        state = self._load_health_state(plugin_id)
        state['consecutive_failures'] = 0
        state['circuit_state'] = CircuitState.CLOSED.value
        state['circuit_opened_time'] = None
        state['half_open_start_time'] = None
        self._save_health_state(plugin_id, state)
        self.logger.info(f"Health state reset for plugin {plugin_id}")

