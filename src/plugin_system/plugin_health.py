"""
Plugin Health Tracker

Tracks plugin health metrics including success/failure rates, consecutive failures,
and circuit breaker state. Provides automatic recovery mechanisms.
"""

import time
from typing import Dict, Optional, Any, Tuple
from enum import Enum

from src.logging_config import get_logger


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
        self.logger = get_logger(__name__)
        
        # In-memory health state (also persisted to cache)
        self._health_state: Dict[str, Dict[str, Any]] = {}
    
    def _get_health_key(self, plugin_id: str) -> str:
        """Get cache key for plugin health data."""
        return f"plugin_health:{plugin_id}"
    
    def _load_health_state(self, plugin_id: str, force_reload: bool = False) -> Dict[str, Any]:
        """Load health state from cache or return defaults.

        ``force_reload=True`` bypasses the cache manager's in-memory tier so a
        read-only consumer (e.g. the web process) observes the writer process's
        latest persisted state instead of a stale first snapshot.
        """
        cache_key = self._get_health_key(plugin_id)
        cached = self.cache_manager.get(
            cache_key, max_age=None, memory_ttl=0 if force_reload else None
        )

        if isinstance(cached, dict) and cached:
            # Complete it rather than trusting it: a persisted record can be
            # missing fields the callers index directly (a partial write, a
            # restored backup, an older schema), and returning it verbatim makes
            # record_success / record_failure raise KeyError, which takes the
            # display down in a restart loop that survives reboots because the
            # bad entry is on disk.
            state, repaired = self._repair_health_state(cached)
            if repaired:
                self.logger.warning(
                    f"Repaired health state for {plugin_id}: "
                    f"{sorted(repaired)} missing or invalid, using defaults for those."
                )
            return state

        # Not a dict at all: written by something other than
        # _save_health_state (a key collision, a corrupted entry). Nothing to
        # salvage.
        if cached is not None and not isinstance(cached, dict):
            self.logger.warning(
                f"Discarding malformed health state for {plugin_id}: expected "
                f"dict, got {type(cached).__name__}. Falling back to defaults."
            )

        return self._default_health_state()
    
    def _save_health_state(self, plugin_id: str, state: Dict[str, Any]) -> None:
        """Save health state to cache."""
        cache_key = self._get_health_key(plugin_id)
        self.cache_manager.set(cache_key, state)  # Persist indefinitely
        self._health_state[plugin_id] = state
    
    # The fields callers index directly (state['circuit_state'] and friends).
    # A cached dict missing any of them raises KeyError deep in record_success /
    # record_failure, so the value is completed before it is handed out.
    _COUNTER_FIELDS = ('consecutive_failures', 'total_failures', 'total_successes')
    _TIMESTAMP_FIELDS = ('last_success_time', 'last_failure_time',
                         'circuit_opened_time', 'half_open_start_time')

    @staticmethod
    def _default_health_state() -> Dict[str, Any]:
        """A fresh state with every field the callers expect."""
        return {
            'consecutive_failures': 0,
            'total_failures': 0,
            'total_successes': 0,
            'last_success_time': None,
            'last_failure_time': None,
            'circuit_state': CircuitState.CLOSED.value,
            'circuit_opened_time': None,
            'half_open_start_time': None,
            'last_error': None,
        }

    @classmethod
    def _repair_health_state(cls, cached: Dict[str, Any]) -> Tuple[Dict[str, Any], list]:
        """Return `cached` completed against the defaults, plus what was repaired.

        Per-field rather than all-or-nothing: a record that has real failure
        counts but is missing `last_error` should keep the counts, not be reset
        to healthy. Only values that are absent or the wrong type fall back to
        the default, so a partial or older-schema record survives with whatever
        it does carry, while every field the callers index is guaranteed present
        and of a usable type.
        """
        state = cls._default_health_state()
        repaired = []
        for field, default in state.items():
            if field not in cached:
                repaired.append(field)
                continue
            value = cached[field]
            if field in cls._COUNTER_FIELDS:
                ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
            elif field in cls._TIMESTAMP_FIELDS:
                # bool is a subclass of int, so True would pass as a timestamp
                # and then compare as 1.0 -- expiring a cooldown the instant it
                # opens, or (False) making the elapsed check never fire.
                ok = value is None or (
                    isinstance(value, (int, float)) and not isinstance(value, bool)
                )
            elif field == 'circuit_state':
                # Membership first requires the value to be hashable: a list or
                # dict here would raise TypeError out of the repair itself,
                # which is the crash this whole path exists to prevent.
                ok = isinstance(value, str) and value in {
                    member.value for member in CircuitState
                }
            else:  # last_error
                ok = value is None or isinstance(value, str)
            if ok:
                state[field] = value
            else:
                repaired.append(field)
        # Anything the schema has since grown (degraded, degraded_reason) is
        # read with .get() by its callers, so carry it through untouched.
        for field, value in cached.items():
            if field not in state:
                state[field] = value
        return state, repaired

    def get_health_state(self, plugin_id: str, force_reload: bool = False) -> Dict[str, Any]:
        """Get current health state for a plugin.

        ``force_reload=True`` re-reads the persisted state from the cache,
        bypassing the in-memory copy — needed by cross-process readers that
        would otherwise be pinned to the first snapshot they loaded.
        """
        if force_reload or plugin_id not in self._health_state:
            self._health_state[plugin_id] = self._load_health_state(
                plugin_id, force_reload=force_reload
            )
        return self._health_state[plugin_id]
    
    # Fields the circuit breaker is rebuilt from after a restart. Everything
    # else in a health record is reporting, read only for display.
    _DURABLE_FIELDS = ('consecutive_failures', 'circuit_state',
                       'circuit_opened_time', 'half_open_start_time')

    def _durable(self, state: Dict[str, Any]) -> tuple:
        """The part of a health record whose loss would change behaviour."""
        return tuple(state.get(field) for field in self._DURABLE_FIELDS)

    def record_success(self, plugin_id: str) -> None:
        """Record a successful plugin execution."""
        state = self.get_health_state(plugin_id)
        current_time = time.time()
        durable_before = self._durable(state)

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

        # A healthy plugin reports success every cycle, and in that steady state
        # the only fields changed above are a counter and a timestamp that
        # nothing reads back after a restart. Persisting them anyway rewrites a
        # small file per plugin per cycle: on a rig running 24 plugins, a
        # five-minute sample measured 22 rewrites, about 4.4 a minute or 6,300 a
        # day. Those land on an SD card, where the cost is an erase-block cycle
        # rather than the 400 bytes involved, and where wear is what eventually
        # kills the card.
        # In-memory state is still updated every time, so the health API and web
        # UI show exactly what they did before; only the write is skipped.
        if self._durable(state) != durable_before:
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
    
    def record_hang(self, plugin_id: str, operation: str, seconds: float,
                    error: Optional[Exception] = None) -> None:
        """Record a display() or update() call that ran past its limit.

        Counts as a failure, so the ordinary circuit breaker handles a plugin
        that keeps hanging: after ``failure_threshold`` in a row it is skipped
        by both the update scheduler and the display rotation until the
        cooldown ends. The hang itself is kept alongside (``hang_count``,
        ``last_hang``) so the health API can tell "hung" from "raised".

        Not for an update skipped because the plugin's lock stayed held: the
        holder may be a healthy but long render (Vegas prefetch). That is
        :meth:`record_busy_skip`, which never touches the breaker.

        Args:
            plugin_id: Plugin identifier
            operation: What hung: ``"display"`` or ``"update"``.
            seconds: How long it had been running when this was recorded.
            error: The error to store as ``last_error``; one is built from
                the other arguments when omitted.
        """
        state = self.get_health_state(plugin_id)
        count = state.get('hang_count')
        state['hang_count'] = (count if isinstance(count, int) and not isinstance(count, bool)
                               else 0) + 1
        state['last_hang'] = {
            'operation': operation,
            'seconds': round(float(seconds), 3),
            'time': time.time(),
        }
        if error is None:
            error = TimeoutError(f"{operation} still running after {seconds:.1f}s")
        # record_failure saves the record, hang fields included.
        self.record_failure(plugin_id, error)

    #: Minimum seconds between persisting a plugin's slow-call or busy-skip
    #: counters. The in-memory record is updated every time; a plugin that is
    #: slow on every frame must not become an SD-card write per frame.
    SLOW_CALL_PERSIST_INTERVAL = 60.0

    def record_slow_call(self, plugin_id: str, operation: str, seconds: float) -> None:
        """Note a call that finished, but slowly. Reporting only.

        Unlike :meth:`record_hang` this never touches the circuit breaker: a
        slow display() still drew its frame.
        """
        state = self.get_health_state(plugin_id)
        count = state.get('slow_call_count')
        state['slow_call_count'] = (count if isinstance(count, int) and not isinstance(count, bool)
                                    else 0) + 1
        now = time.time()
        state['last_slow_call'] = {
            'operation': operation,
            'seconds': round(float(seconds), 3),
            'time': now,
        }
        self._save_reporting_throttled('slow', plugin_id, state, now)

    def record_busy_skip(self, plugin_id: str, operation: str, seconds: float) -> None:
        """Note a call skipped because the plugin's lock stayed held. Reporting only.

        The update worker gives up on a plugin's lock after
        ``PluginManager.PLUGIN_LOCK_TIMEOUT``. Whatever held it may be healthy
        -- Vegas prefetch holds the lock for a plugin's whole content render,
        which on a slow Pi can take longer than that -- so like
        :meth:`record_slow_call` this never touches the circuit breaker, the
        failure streak or ``last_error``. A real hang is recorded by
        :meth:`record_hang` where it is measured.

        Args:
            plugin_id: Plugin identifier
            operation: What was skipped, e.g. ``"update lock wait"``.
            seconds: How long the lock was waited on.
        """
        state = self.get_health_state(plugin_id)
        count = state.get('busy_skip_count')
        state['busy_skip_count'] = (count if isinstance(count, int) and not isinstance(count, bool)
                                    else 0) + 1
        now = time.time()
        state['last_busy_skip'] = {
            'operation': operation,
            'seconds': round(float(seconds), 3),
            'time': now,
        }
        self._save_reporting_throttled('busy', plugin_id, state, now)

    def _save_reporting_throttled(self, kind: str, plugin_id: str,
                                  state: Dict[str, Any], now: float) -> None:
        """Persist a reporting-only change at most once per
        SLOW_CALL_PERSIST_INTERVAL per plugin and ``kind``. The first one is
        saved at once, so the web process (which reads the persisted record)
        sees it; repeats in between stay in memory until the next save."""
        saved_at = self.__dict__.setdefault('_reporting_saved_at', {})
        last = saved_at.get((kind, plugin_id))
        if last is None or now - last >= self.SLOW_CALL_PERSIST_INTERVAL:
            saved_at[(kind, plugin_id)] = now
            self._save_health_state(plugin_id, state)

    def set_degraded(self, plugin_id: str, reason: Optional[str]) -> None:
        """Flag (or clear) a plugin as degraded without touching the circuit breaker.

        Used for non-fatal issues — e.g. a config that no longer satisfies the
        plugin's schema — that should be surfaced to the user but must NOT cause
        the plugin to be skipped or counted as a runtime failure. Passing
        ``reason=None`` clears the flag. The write is skipped when nothing
        actually changes, so calling this on every load is cheap.

        Args:
            plugin_id: Plugin identifier
            reason: Human-readable reason string, or None to clear the flag
        """
        state = self.get_health_state(plugin_id)
        new_degraded = bool(reason)
        new_reason = reason if reason else None
        if state.get('degraded', False) == new_degraded and state.get('degraded_reason') == new_reason:
            return  # No change — avoid a redundant cache write
        state['degraded'] = new_degraded
        state['degraded_reason'] = new_reason
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
    
    def get_health_summary(self, plugin_id: str, force_reload: bool = False) -> Dict[str, Any]:
        """Get health summary for a plugin.

        ``force_reload=True`` refreshes from the persisted cache first so
        cross-process readers reflect the writer's latest state.
        """
        state = self.get_health_state(plugin_id, force_reload=force_reload)
        
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
            'degraded': state.get('degraded', False),
            'degraded_reason': state.get('degraded_reason'),
            'circuit_opened_time': state.get('circuit_opened_time'),
            'half_open_start_time': state.get('half_open_start_time'),
            'hang_count': state.get('hang_count', 0),
            'last_hang': state.get('last_hang'),
            'slow_call_count': state.get('slow_call_count', 0),
            'last_slow_call': state.get('last_slow_call'),
            'busy_skip_count': state.get('busy_skip_count', 0),
            'last_busy_skip': state.get('last_busy_skip'),
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

