"""
Plugin Resource Monitor

Tracks resource usage (memory, CPU, execution time) for plugins.
Provides resource limits and performance monitoring.
"""

import time
import logging
import threading
from typing import Dict, Optional, Any, Callable
from dataclasses import dataclass, field, fields

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


class ResourceLimitExceeded(Exception):
    """Raised when a plugin exceeds its resource limits."""


@dataclass
class ResourceLimits:
    """Resource limits for a plugin."""
    max_memory_mb: Optional[float] = None  # Maximum memory in MB
    max_cpu_percent: Optional[float] = None  # Maximum CPU percentage
    max_execution_time: Optional[float] = None  # Maximum execution time in seconds
    warning_threshold: float = 0.8  # Warning at 80% of limit


@dataclass
class ResourceMetrics:
    """Resource usage metrics for a plugin."""
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    execution_time: float = 0.0
    call_count: int = 0
    total_execution_time: float = 0.0
    max_execution_time: float = 0.0
    min_execution_time: float = float('inf')
    last_update_time: float = field(default_factory=time.time)
    
    def update_average_execution_time(self):
        """Update average execution time."""
        if self.call_count > 0:
            self.total_execution_time = self.total_execution_time / self.call_count


#: How often a plugin's metrics are written to the cache, in seconds.
#:
#: Persisting on every call meant a small file rewritten roughly nine times a
#: minute per plugin. On a rig with fourteen active plugins that was ~126
#: writes a minute for metrics alone, and since each ~350-byte file costs a
#: 4KB block plus an ext4 journal entry, it dominated the device's write
#: volume -- on an SD card, which wears out.
#:
#: The in-memory copy stays authoritative and exact; only the cross-process
#: snapshot the web UI reads is delayed, and telemetry up to half a minute old
#: is still a fair description of a long-running plugin.
_METRICS_PERSIST_INTERVAL = 30.0


class PluginResourceMonitor:
    """
    Monitors resource usage for plugins.
    
    Tracks:
    - Memory usage (if psutil available)
    - CPU usage (if psutil available)
    - Execution time for update() and display() calls
    - Call counts and statistics
    """
    
    def __init__(self, cache_manager, enable_monitoring: bool = True):
        """
        Initialize resource monitor.
        
        Args:
            cache_manager: Cache manager for persisting metrics
            enable_monitoring: Enable resource monitoring (requires psutil)
        """
        self.cache_manager = cache_manager
        self.enable_monitoring = enable_monitoring and PSUTIL_AVAILABLE
        self.logger = logging.getLogger(__name__)

        # Resource metrics per plugin
        self._metrics: Dict[str, ResourceMetrics] = {}
        self._limits: Dict[str, ResourceLimits] = {}
        # When each plugin's metrics last reached the cache. Metrics change on
        # every call, so they cannot be de-duplicated the way health state can;
        # they are rate-limited instead. See _METRICS_PERSIST_INTERVAL.
        self._metrics_persisted_at: Dict[str, float] = {}

        # Thread-local storage for execution tracking
        self._local = threading.local()

        # Lock for thread-safe access
        self._lock = threading.Lock()

        # Cache a single psutil.Process handle. Reusing the same handle is what
        # lets cpu_percent() be read non-blocking (interval=None): psutil returns
        # the utilisation since the *previous* call on that same object. Creating
        # a fresh Process() per call would force interval-based sampling that
        # blocks the caller — unacceptable on the display loop's update path.
        self._process = None
        if self.enable_monitoring:
            try:
                self._process = psutil.Process()
                # Prime cpu_percent so the first real measurement returns a
                # meaningful delta instead of 0.0.
                self._process.cpu_percent(interval=None)
            except Exception:  # pragma: no cover - psutil edge cases
                self._process = None

        if not PSUTIL_AVAILABLE and enable_monitoring:
            self.logger.warning(
                "psutil not available - resource monitoring will be limited to execution time only"
            )
    
    def _metrics_from_cache(self, plugin_id: str, cached: Any) -> "ResourceMetrics":
        """Build metrics from a cached record, ignoring anything unrecognised.

        ResourceMetrics(**cached) raises TypeError on a single unexpected key,
        and that exception escapes into plugin_manager, which reports it as
        "plugin <id> operation failed". Every plugin fails, and the plugin
        system never finishes initialising.

        Seen on a live rig: every plugin failing with

            ResourceMetrics.__init__() got an unexpected keyword argument
            'consecutive_failures'

        which is a plugin_health field, not a metrics one. How a health-shaped
        record came to sit under a plugin_metrics key on that machine is not
        established -- a restored backup that mixed two machines' caches is the
        likeliest explanation -- but the loader should not be brittle enough for
        it to matter. plugin_health already repairs its records field by field
        rather than trusting whatever is on disk; this does the same.

        Unknown keys are dropped and named once, so a genuine schema change is
        visible in the log instead of silently discarded.
        """
        if not isinstance(cached, dict):
            self.logger.warning(
                "Ignoring cached metrics for %s: expected a mapping, got %s",
                plugin_id, type(cached).__name__)
            return ResourceMetrics()

        known = {f.name for f in fields(ResourceMetrics)}
        unknown = sorted(set(cached) - known)
        if unknown:
            self.logger.warning(
                "Dropping unrecognised field(s) from cached metrics for %s: %s",
                plugin_id, ", ".join(unknown))
        # A dataclass does not enforce its annotations, so
        # ResourceMetrics(call_count="not a number") builds happily and only
        # blows up later, deep inside monitor_call ("can only concatenate str
        # (not \"int\") to str"). Coerce here, where there is still a cache
        # key to name in the warning.
        declared = {f.name: f.type for f in fields(ResourceMetrics)}
        usable = {}
        for key, value in cached.items():
            if key not in known:
                continue
            try:
                usable[key] = int(value) if declared[key] in ('int', int) else float(value)
            except (TypeError, ValueError):
                self.logger.warning(
                    "Cached metrics for %s have a bad %s (%r); starting fresh",
                    plugin_id, key, value)
                return ResourceMetrics()
        try:
            return ResourceMetrics(**usable)
        except (TypeError, ValueError) as e:
            self.logger.warning(
                "Cached metrics for %s unusable (%s); starting fresh",
                plugin_id, e)
            return ResourceMetrics()

    def _get_metrics_key(self, plugin_id: str) -> str:
        """Get cache key for plugin metrics."""
        return f"plugin_metrics:{plugin_id}"
    
    def _get_limits_key(self, plugin_id: str) -> str:
        """Get cache key for plugin limits."""
        return f"plugin_limits:{plugin_id}"
    
    def get_metrics(self, plugin_id: str, force_reload: bool = False) -> ResourceMetrics:
        """Get current metrics for a plugin.

        ``force_reload=True`` bypasses both the in-memory copy and the cache
        manager's memory tier so a read-only consumer (e.g. the web process)
        sees the writer process's latest persisted metrics rather than a stale
        first snapshot.
        """
        with self._lock:
            if force_reload or plugin_id not in self._metrics:
                # Try to load from cache
                cache_key = self._get_metrics_key(plugin_id)
                cached = self.cache_manager.get(
                    cache_key, max_age=None, memory_ttl=0 if force_reload else None
                )
                if cached:
                    metrics = self._metrics_from_cache(plugin_id, cached)
                else:
                    metrics = ResourceMetrics()
                self._metrics[plugin_id] = metrics
            return self._metrics[plugin_id]
    
    def set_limits(self, plugin_id: str, limits: ResourceLimits) -> None:
        """Set resource limits for a plugin."""
        with self._lock:
            self._limits[plugin_id] = limits
            # Persist to cache
            cache_key = self._get_limits_key(plugin_id)
            self.cache_manager.set(cache_key, {
                'max_memory_mb': limits.max_memory_mb,
                'max_cpu_percent': limits.max_cpu_percent,
                'max_execution_time': limits.max_execution_time,
                'warning_threshold': limits.warning_threshold
            })
    
    def get_limits(self, plugin_id: str) -> Optional[ResourceLimits]:
        """Get resource limits for a plugin."""
        with self._lock:
            if plugin_id not in self._limits:
                # Try to load from cache
                cache_key = self._get_limits_key(plugin_id)
                cached = self.cache_manager.get(cache_key, max_age=None)
                if cached:
                    self._limits[plugin_id] = ResourceLimits(**cached)
                else:
                    return None
            return self._limits[plugin_id]
    
    def _get_process_memory_mb(self) -> float:
        """Get current process memory usage in MB."""
        if not self.enable_monitoring or self._process is None:
            return 0.0
        try:
            return self._process.memory_info().rss / 1024 / 1024
        except Exception:
            return 0.0

    def _get_process_cpu_percent(self) -> float:
        """Get current process CPU usage percentage (non-blocking).

        Reads cpu_percent(interval=None) against the cached process handle, so
        it returns immediately with the utilisation observed since the previous
        call rather than blocking to sample a fresh interval.
        """
        if not self.enable_monitoring or self._process is None:
            return 0.0
        try:
            return self._process.cpu_percent(interval=None)
        except Exception:
            return 0.0
    
    def monitor_call(self, plugin_id: str, func: Callable, *args, **kwargs) -> Any:
        """
        Monitor a plugin method call.
        
        Tracks execution time and resource usage, enforces limits.
        
        Args:
            plugin_id: Plugin identifier
            func: Function to call
            *args: Function arguments
            **kwargs: Function keyword arguments
        
        Returns:
            Function return value
        
        Raises:
            ResourceLimitExceeded: If resource limits are exceeded
        """
        metrics = self.get_metrics(plugin_id)
        limits = self.get_limits(plugin_id)
        
        # Record start time and memory
        start_time = time.time()
        start_memory = self._get_process_memory_mb()
        
        try:
            # Execute the function
            result = func(*args, **kwargs)
            
            # Calculate execution time
            execution_time = time.time() - start_time
            
            # Update metrics
            with self._lock:
                metrics.execution_time = execution_time
                metrics.call_count += 1
                metrics.total_execution_time += execution_time
                metrics.max_execution_time = max(metrics.max_execution_time, execution_time)
                if metrics.min_execution_time == float('inf'):
                    metrics.min_execution_time = execution_time
                else:
                    metrics.min_execution_time = min(metrics.min_execution_time, execution_time)
                metrics.last_update_time = time.time()
                
                # Update memory and CPU if monitoring enabled
                if self.enable_monitoring:
                    end_memory = self._get_process_memory_mb()
                    metrics.memory_mb = max(metrics.memory_mb, end_memory - start_memory)
                    # CPU is harder to measure per-call, so we track it separately
                    metrics.cpu_percent = self._get_process_cpu_percent()
                
                # Persist metrics, at most once per interval per plugin.
                self._persist_metrics(plugin_id, metrics)
            
            # Check limits
            if limits:
                self._check_limits(plugin_id, metrics, limits, execution_time)
            
            return result
            
        except ResourceLimitExceeded:
            raise
        except Exception:
            # Still record execution time even on error
            execution_time = time.time() - start_time
            with self._lock:
                metrics.execution_time = execution_time
                metrics.last_update_time = time.time()
            raise
    
    def _check_limits(self, plugin_id: str, metrics: ResourceMetrics, 
                     limits: ResourceLimits, execution_time: float) -> None:
        """Check if plugin has exceeded resource limits."""
        warnings = []
        errors = []
        
        # Check execution time
        if limits.max_execution_time and execution_time > limits.max_execution_time:
            errors.append(
                f"Execution time {execution_time:.2f}s exceeds limit {limits.max_execution_time:.2f}s"
            )
        elif limits.max_execution_time and execution_time > limits.max_execution_time * limits.warning_threshold:
            warnings.append(
                f"Execution time {execution_time:.2f}s approaching limit {limits.max_execution_time:.2f}s"
            )
        
        # Check memory
        if limits.max_memory_mb and metrics.memory_mb > limits.max_memory_mb:
            errors.append(
                f"Memory usage {metrics.memory_mb:.2f}MB exceeds limit {limits.max_memory_mb:.2f}MB"
            )
        elif limits.max_memory_mb and metrics.memory_mb > limits.max_memory_mb * limits.warning_threshold:
            warnings.append(
                f"Memory usage {metrics.memory_mb:.2f}MB approaching limit {limits.max_memory_mb:.2f}MB"
            )
        
        # Check CPU
        if limits.max_cpu_percent and metrics.cpu_percent > limits.max_cpu_percent:
            errors.append(
                f"CPU usage {metrics.cpu_percent:.2f}% exceeds limit {limits.max_cpu_percent:.2f}%"
            )
        elif limits.max_cpu_percent and metrics.cpu_percent > limits.max_cpu_percent * limits.warning_threshold:
            warnings.append(
                f"CPU usage {metrics.cpu_percent:.2f}% approaching limit {limits.max_cpu_percent:.2f}%"
            )
        
        # Log warnings
        for warning in warnings:
            self.logger.warning(f"Plugin {plugin_id}: {warning}")
        
        # Raise exception for errors
        if errors:
            error_msg = f"Plugin {plugin_id} exceeded resource limits: {'; '.join(errors)}"
            self.logger.error(error_msg)
            raise ResourceLimitExceeded(error_msg)
    
    def get_metrics_summary(self, plugin_id: str, force_reload: bool = False) -> Dict[str, Any]:
        """Get metrics summary for a plugin.

        ``force_reload=True`` refreshes from the persisted cache first so
        cross-process readers reflect the writer's latest metrics.
        """
        metrics = self.get_metrics(plugin_id, force_reload=force_reload)
        limits = self.get_limits(plugin_id)
        
        avg_execution_time = 0.0
        if metrics.call_count > 0:
            avg_execution_time = metrics.total_execution_time / metrics.call_count
        
        summary = {
            'plugin_id': plugin_id,
            'memory_mb': round(metrics.memory_mb, 2),
            'cpu_percent': round(metrics.cpu_percent, 2),
            'execution_time': round(metrics.execution_time, 3),
            'avg_execution_time': round(avg_execution_time, 3),
            'min_execution_time': round(metrics.min_execution_time if metrics.min_execution_time != float('inf') else 0.0, 3),
            'max_execution_time': round(metrics.max_execution_time, 3),
            'call_count': metrics.call_count,
            'last_update_time': metrics.last_update_time
        }
        
        if limits:
            summary['limits'] = {
                'max_memory_mb': limits.max_memory_mb,
                'max_cpu_percent': limits.max_cpu_percent,
                'max_execution_time': limits.max_execution_time,
                'warning_threshold': limits.warning_threshold
            }
            
            # Calculate usage percentages
            if limits.max_memory_mb:
                summary['memory_usage_percent'] = round(
                    (metrics.memory_mb / limits.max_memory_mb) * 100, 2
                )
            if limits.max_cpu_percent:
                summary['cpu_usage_percent'] = round(
                    (metrics.cpu_percent / limits.max_cpu_percent) * 100, 2
                )
            if limits.max_execution_time:
                summary['execution_time_usage_percent'] = round(
                    (avg_execution_time / limits.max_execution_time) * 100, 2
                )
        
        return summary
    
    def get_all_metrics_summaries(self) -> Dict[str, Dict[str, Any]]:
        """Get metrics summaries for all tracked plugins."""
        summaries = {}
        for plugin_id in self._metrics.keys():
            summaries[plugin_id] = self.get_metrics_summary(plugin_id)
        return summaries
    
    def _persist_metrics(self, plugin_id: str, metrics: ResourceMetrics,
                         force: bool = False) -> None:
        """Write a plugin's metrics to the cache, at most once per interval.

        Caller must hold ``self._lock``.
        """
        # Monotonic, not wall clock: these devices have no RTC, so the clock
        # jumps by however far off boot-time was the moment NTP first syncs.
        # A forward jump would allow an early write, a backward one would
        # stall the snapshot well past the interval.
        #
        # The sentinel for "never written" is None, not 0.0. monotonic() is
        # time since boot on Linux, and systemd starts this service *at* boot,
        # so `now - 0.0 < 30` was true for the first half-minute of every
        # single run -- the throttle swallowed the very first snapshot, which
        # is the one that matters most after a restart.
        now = time.monotonic()
        last_written = self._metrics_persisted_at.get(plugin_id)
        if (not force and last_written is not None
                and now - last_written < _METRICS_PERSIST_INTERVAL):
            return
        cache_key = self._get_metrics_key(plugin_id)
        self.cache_manager.set(cache_key, {
            'memory_mb': metrics.memory_mb,
            'cpu_percent': metrics.cpu_percent,
            'execution_time': metrics.execution_time,
            'call_count': metrics.call_count,
            'total_execution_time': metrics.total_execution_time,
            'max_execution_time': metrics.max_execution_time,
            'min_execution_time': (metrics.min_execution_time
                                   if metrics.min_execution_time != float('inf')
                                   else 0.0),
            'last_update_time': metrics.last_update_time,
        })
        # Only after the write lands. Marking it first would mean a failed
        # set() bought the next interval's silence without leaving a snapshot.
        self._metrics_persisted_at[plugin_id] = now

    def reset_metrics(self, plugin_id: str) -> None:
        """Reset metrics for a plugin."""
        with self._lock:
            if plugin_id in self._metrics:
                self._metrics[plugin_id] = ResourceMetrics()
                cache_key = self._get_metrics_key(plugin_id)
                self.cache_manager.delete(cache_key)
                # Let the next call persist immediately rather than leaving the
                # deleted key absent for the rest of the interval.
                self._metrics_persisted_at.pop(plugin_id, None)

