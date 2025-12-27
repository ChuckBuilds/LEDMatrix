"""
Plugin Resource Monitor

Tracks resource usage (memory, CPU, execution time) for plugins.
Provides resource limits and performance monitoring.
"""

import time
import logging
import threading
from typing import Dict, Optional, Any, Callable
from dataclasses import dataclass, field

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


class ResourceLimitExceeded(Exception):
    """Raised when a plugin exceeds its resource limits."""
    pass


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
        
        # Thread-local storage for execution tracking
        self._local = threading.local()
        
        # Lock for thread-safe access
        self._lock = threading.Lock()
        
        if not PSUTIL_AVAILABLE and enable_monitoring:
            self.logger.warning(
                "psutil not available - resource monitoring will be limited to execution time only"
            )
    
    def _get_metrics_key(self, plugin_id: str) -> str:
        """Get cache key for plugin metrics."""
        return f"plugin_metrics:{plugin_id}"
    
    def _get_limits_key(self, plugin_id: str) -> str:
        """Get cache key for plugin limits."""
        return f"plugin_limits:{plugin_id}"
    
    def get_metrics(self, plugin_id: str) -> ResourceMetrics:
        """Get current metrics for a plugin."""
        with self._lock:
            if plugin_id not in self._metrics:
                # Try to load from cache
                cache_key = self._get_metrics_key(plugin_id)
                cached = self.cache_manager.get(cache_key, max_age=None)
                if cached:
                    metrics = ResourceMetrics(**cached)
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
        if not self.enable_monitoring:
            return 0.0
        try:
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024
        except Exception:
            return 0.0
    
    def _get_process_cpu_percent(self, interval: float = 0.1) -> float:
        """Get current process CPU usage percentage."""
        if not self.enable_monitoring:
            return 0.0
        try:
            process = psutil.Process()
            return process.cpu_percent(interval=interval)
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
                
                # Persist metrics
                cache_key = self._get_metrics_key(plugin_id)
                self.cache_manager.set(cache_key, {
                    'memory_mb': metrics.memory_mb,
                    'cpu_percent': metrics.cpu_percent,
                    'execution_time': metrics.execution_time,
                    'call_count': metrics.call_count,
                    'total_execution_time': metrics.total_execution_time,
                    'max_execution_time': metrics.max_execution_time,
                    'min_execution_time': metrics.min_execution_time if metrics.min_execution_time != float('inf') else 0.0,
                    'last_update_time': metrics.last_update_time
                })
            
            # Check limits
            if limits:
                self._check_limits(plugin_id, metrics, limits, execution_time)
            
            return result
            
        except ResourceLimitExceeded:
            raise
        except Exception as e:
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
    
    def get_metrics_summary(self, plugin_id: str) -> Dict[str, Any]:
        """Get metrics summary for a plugin."""
        metrics = self.get_metrics(plugin_id)
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
    
    def reset_metrics(self, plugin_id: str) -> None:
        """Reset metrics for a plugin."""
        with self._lock:
            if plugin_id in self._metrics:
                self._metrics[plugin_id] = ResourceMetrics()
                cache_key = self._get_metrics_key(plugin_id)
                self.cache_manager.delete(cache_key)

