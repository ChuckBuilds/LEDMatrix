"""
Enhanced plugin health monitoring with background checks and auto-recovery.

Builds on existing PluginHealthTracker to provide:
- Background health checks
- Health status determination (healthy/degraded/unhealthy)
- Auto-recovery suggestions
- Health metrics aggregation
"""

import threading
import time
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass

from src.logging_config import get_logger


class HealthStatus(Enum):
    """Overall health status of a plugin."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthMetrics:
    """Health metrics for a plugin."""
    plugin_id: str
    status: HealthStatus
    last_successful_update: Optional[datetime]
    error_rate: float  # 0.0 to 1.0
    average_response_time: Optional[float]  # seconds
    consecutive_failures: int
    total_failures: int
    total_successes: int
    success_rate: float  # 0.0 to 1.0
    last_error: Optional[str]
    circuit_breaker_state: str
    recovery_suggestions: List[str]


class PluginHealthMonitor:
    """
    Enhanced health monitoring for plugins.
    
    Provides:
    - Background health checks
    - Health status determination
    - Auto-recovery suggestions
    - Health metrics aggregation
    """
    
    def __init__(
        self,
        health_tracker,
        check_interval: float = 60.0,
        degraded_threshold: float = 0.5,  # 50% error rate
        unhealthy_threshold: float = 0.8,  # 80% error rate
        max_response_time: float = 5.0  # seconds
    ):
        """
        Initialize health monitor.
        
        Args:
            health_tracker: PluginHealthTracker instance
            check_interval: Interval between background health checks (seconds)
            degraded_threshold: Error rate threshold for degraded status
            unhealthy_threshold: Error rate threshold for unhealthy status
            max_response_time: Maximum acceptable response time (seconds)
        """
        self.health_tracker = health_tracker
        self.check_interval = check_interval
        self.degraded_threshold = degraded_threshold
        self.unhealthy_threshold = unhealthy_threshold
        self.max_response_time = max_response_time
        self.logger = get_logger(__name__)
        
        # Background check thread
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Health check callbacks
        self._health_check_callbacks: List[Callable[[str], Dict[str, Any]]] = []
    
    def start_monitoring(self) -> None:
        """Start background health monitoring."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="PluginHealthMonitor"
        )
        self._monitor_thread.start()
        self.logger.info("Started plugin health monitoring")
    
    def stop_monitoring(self) -> None:
        """Stop background health monitoring."""
        self._stop_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)
        self.logger.info("Stopped plugin health monitoring")
    
    def register_health_check(self, callback: Callable[[str], Dict[str, Any]]) -> None:
        """
        Register a callback for health checks.
        
        Callback should accept plugin_id and return dict with health info.
        """
        self._health_check_callbacks.append(callback)
    
    def get_plugin_health_status(self, plugin_id: str) -> HealthStatus:
        """
        Determine overall health status for a plugin.
        
        Args:
            plugin_id: Plugin identifier
        
        Returns:
            HealthStatus enum value
        """
        if not self.health_tracker:
            return HealthStatus.UNKNOWN
        
        summary = self.health_tracker.get_health_summary(plugin_id)
        
        if not summary:
            return HealthStatus.UNKNOWN
        
        # Check circuit breaker state
        circuit_state = summary.get('circuit_state', 'closed')
        if circuit_state == 'open':
            return HealthStatus.UNHEALTHY
        
        # Check error rate
        success_rate = summary.get('success_rate', 100.0)
        error_rate = 1.0 - (success_rate / 100.0)
        
        if error_rate >= self.unhealthy_threshold:
            return HealthStatus.UNHEALTHY
        elif error_rate >= self.degraded_threshold:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY
    
    def get_plugin_health_metrics(self, plugin_id: str) -> HealthMetrics:
        """
        Get comprehensive health metrics for a plugin.
        
        Args:
            plugin_id: Plugin identifier
        
        Returns:
            HealthMetrics object
        """
        if not self.health_tracker:
            return HealthMetrics(
                plugin_id=plugin_id,
                status=HealthStatus.UNKNOWN,
                last_successful_update=None,
                error_rate=0.0,
                average_response_time=None,
                consecutive_failures=0,
                total_failures=0,
                total_successes=0,
                success_rate=0.0,
                last_error=None,
                circuit_breaker_state="unknown",
                recovery_suggestions=[]
            )
        
        summary = self.health_tracker.get_health_summary(plugin_id)
        
        if not summary:
            return HealthMetrics(
                plugin_id=plugin_id,
                status=HealthStatus.UNKNOWN,
                last_successful_update=None,
                error_rate=0.0,
                average_response_time=None,
                consecutive_failures=0,
                total_failures=0,
                total_successes=0,
                success_rate=0.0,
                last_error=None,
                circuit_breaker_state="unknown",
                recovery_suggestions=[]
            )
        
        # Calculate metrics
        success_rate = summary.get('success_rate', 100.0) / 100.0
        error_rate = 1.0 - success_rate
        
        # Parse last success time
        last_success_time = None
        if summary.get('last_success_time'):
            try:
                last_success_time = datetime.fromisoformat(summary['last_success_time'])
            except (ValueError, TypeError):
                pass
        
        # Determine status
        status = self.get_plugin_health_status(plugin_id)
        
        # Get recovery suggestions
        recovery_suggestions = self._get_recovery_suggestions(plugin_id, summary, status)
        
        return HealthMetrics(
            plugin_id=plugin_id,
            status=status,
            last_successful_update=last_success_time,
            error_rate=error_rate,
            average_response_time=None,  # Would need resource monitor for this
            consecutive_failures=summary.get('consecutive_failures', 0),
            total_failures=summary.get('total_failures', 0),
            total_successes=summary.get('total_successes', 0),
            success_rate=success_rate,
            last_error=summary.get('last_error'),
            circuit_breaker_state=summary.get('circuit_state', 'closed'),
            recovery_suggestions=recovery_suggestions
        )
    
    def get_all_plugin_health(self) -> Dict[str, HealthMetrics]:
        """
        Get health metrics for all tracked plugins.
        
        Returns:
            Dictionary mapping plugin_id to HealthMetrics
        """
        if not self.health_tracker:
            return {}
        
        summaries = self.health_tracker.get_all_health_summaries()
        health_metrics = {}
        
        for plugin_id in summaries.keys():
            health_metrics[plugin_id] = self.get_plugin_health_metrics(plugin_id)
        
        return health_metrics
    
    def _get_recovery_suggestions(
        self,
        plugin_id: str,
        summary: Dict[str, Any],
        status: HealthStatus
    ) -> List[str]:
        """
        Generate recovery suggestions based on health status.
        
        Args:
            plugin_id: Plugin identifier
            summary: Health summary from tracker
            status: Current health status
        
        Returns:
            List of suggested recovery actions
        """
        suggestions = []
        
        if status == HealthStatus.UNHEALTHY:
            suggestions.append("Plugin is unhealthy - check plugin logs for errors")
            suggestions.append("Verify plugin configuration is correct")
            suggestions.append("Check if plugin dependencies are installed")
            
            if summary.get('circuit_state') == 'open':
                suggestions.append("Circuit breaker is open - plugin is being skipped")
                suggestions.append("Wait for cooldown period or manually reset health")
            
            if summary.get('consecutive_failures', 0) > 0:
                suggestions.append(f"Plugin has {summary['consecutive_failures']} consecutive failures")
                suggestions.append("Consider disabling plugin temporarily")
        
        elif status == HealthStatus.DEGRADED:
            suggestions.append("Plugin is degraded - experiencing intermittent failures")
            suggestions.append("Monitor plugin performance")
            suggestions.append("Check for resource constraints (CPU, memory)")
            
            error_rate = (1.0 - (summary.get('success_rate', 100.0) / 100.0)) * 100
            suggestions.append(f"Current error rate: {error_rate:.1f}%")
        
        elif status == HealthStatus.HEALTHY:
            suggestions.append("Plugin is healthy - no action needed")
        
        # Add specific suggestions based on last error
        last_error = summary.get('last_error')
        if last_error:
            if "timeout" in last_error.lower():
                suggestions.append("Last error was a timeout - plugin may be slow or unresponsive")
            elif "import" in last_error.lower() or "module" in last_error.lower():
                suggestions.append("Last error suggests missing dependencies")
            elif "permission" in last_error.lower() or "access" in last_error.lower():
                suggestions.append("Last error suggests permission issues")
        
        return suggestions
    
    def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while not self._stop_event.is_set():
            try:
                # Run health checks for all plugins
                if self._health_check_callbacks:
                    # Get list of plugin IDs (would need plugin manager reference)
                    # For now, just wait
                    pass
                
                # Sleep until next check
                self._stop_event.wait(self.check_interval)
                
            except Exception as e:
                self.logger.error(f"Error in health monitor loop: {e}", exc_info=True)
                # Continue monitoring even if there's an error
                time.sleep(self.check_interval)

