"""
Error Aggregation Service

Provides centralized error tracking, pattern detection, and reporting
for the LEDMatrix system. Enables automatic bug detection by tracking
error frequency, patterns, and context.

This is a local-only implementation with no external dependencies.
Errors are stored in memory with optional JSON export.
"""

import threading
import traceback
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
import logging

from src.exceptions import LEDMatrixError


@dataclass
class ErrorRecord:
    """Record of a single error occurrence."""
    error_type: str
    message: str
    timestamp: datetime
    context: Dict[str, Any] = field(default_factory=dict)
    plugin_id: Optional[str] = None
    operation: Optional[str] = None
    stack_trace: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "error_type": self.error_type,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "context": self.context,
            "plugin_id": self.plugin_id,
            "operation": self.operation,
            "stack_trace": self.stack_trace
        }


@dataclass
class ErrorPattern:
    """Detected error pattern for automatic detection."""
    error_type: str
    count: int
    first_seen: datetime
    last_seen: datetime
    affected_plugins: List[str] = field(default_factory=list)
    sample_messages: List[str] = field(default_factory=list)
    severity: str = "warning"  # warning, error, critical

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "error_type": self.error_type,
            "count": self.count,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "affected_plugins": list(set(self.affected_plugins)),
            "sample_messages": self.sample_messages[:3],  # Keep only 3 samples
            "severity": self.severity
        }


class ErrorAggregator:
    """
    Aggregates and analyzes errors across the system.

    Features:
    - Error counting by type, plugin, and time window
    - Pattern detection (recurring errors)
    - Error rate alerting via callbacks
    - Export for analytics/reporting

    Thread-safe for concurrent access.
    """

    def __init__(
        self,
        max_records: int = 1000,
        pattern_threshold: int = 5,
        pattern_window_minutes: int = 60,
        export_path: Optional[Path] = None
    ):
        """
        Initialize the error aggregator.

        Args:
            max_records: Maximum number of error records to keep in memory
            pattern_threshold: Number of occurrences to detect a pattern
            pattern_window_minutes: Time window for pattern detection
            export_path: Optional path for JSON export (auto-export on pattern detection)
        """
        self.logger = logging.getLogger(__name__)
        self.max_records = max_records
        self.pattern_threshold = pattern_threshold
        self.pattern_window = timedelta(minutes=pattern_window_minutes)
        self.export_path = export_path

        self._records: List[ErrorRecord] = []
        self._error_counts: Dict[str, int] = defaultdict(int)
        self._plugin_error_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._patterns: Dict[str, ErrorPattern] = {}
        self._pattern_callbacks: List[Callable[[ErrorPattern], None]] = []
        self._lock = threading.RLock()  # RLock allows nested acquisition for export_to_file

        # Track session start for relative timing
        self._session_start = datetime.now()

    def record_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        plugin_id: Optional[str] = None,
        operation: Optional[str] = None
    ) -> ErrorRecord:
        """
        Record an error occurrence.

        Args:
            error: The exception that occurred
            context: Optional context dictionary with additional details
            plugin_id: Optional plugin ID that caused the error
            operation: Optional operation name (e.g., "update", "display")

        Returns:
            The created ErrorRecord
        """
        with self._lock:
            error_type = type(error).__name__

            # Extract additional context from LEDMatrixError subclasses
            error_context = context or {}
            if isinstance(error, LEDMatrixError) and error.context:
                error_context.update(error.context)

            record = ErrorRecord(
                error_type=error_type,
                message=str(error),
                timestamp=datetime.now(),
                context=error_context,
                plugin_id=plugin_id,
                operation=operation,
                stack_trace=traceback.format_exc()
            )

            # Add record (with size limit)
            self._records.append(record)
            if len(self._records) > self.max_records:
                self._records.pop(0)

            # Update counts
            self._error_counts[error_type] += 1
            if plugin_id:
                self._plugin_error_counts[plugin_id][error_type] += 1

            # Check for patterns
            self._detect_pattern(record)

            # Log the error
            self.logger.debug(
                f"Error recorded: {error_type} - {str(error)[:100]}",
                extra={"plugin_id": plugin_id, "operation": operation}
            )

            return record

    def _detect_pattern(self, record: ErrorRecord) -> None:
        """Detect recurring error patterns."""
        cutoff = datetime.now() - self.pattern_window
        recent_same_type = [
            r for r in self._records
            if r.error_type == record.error_type and r.timestamp > cutoff
        ]

        if len(recent_same_type) >= self.pattern_threshold:
            pattern_key = record.error_type
            is_new_pattern = pattern_key not in self._patterns

            # Determine severity based on count
            count = len(recent_same_type)
            if count > self.pattern_threshold * 3:
                severity = "critical"
            elif count > self.pattern_threshold * 2:
                severity = "error"
            else:
                severity = "warning"

            # Collect affected plugins
            affected_plugins = [r.plugin_id for r in recent_same_type if r.plugin_id]

            # Collect sample messages
            sample_messages = list(set(r.message for r in recent_same_type[:5]))

            if is_new_pattern:
                pattern = ErrorPattern(
                    error_type=record.error_type,
                    count=count,
                    first_seen=recent_same_type[0].timestamp,
                    last_seen=record.timestamp,
                    affected_plugins=affected_plugins,
                    sample_messages=sample_messages,
                    severity=severity
                )
                self._patterns[pattern_key] = pattern

                self.logger.warning(
                    f"Error pattern detected: {record.error_type} occurred "
                    f"{count} times in last {self.pattern_window}. "
                    f"Affected plugins: {set(affected_plugins) or 'unknown'}"
                )

                # Notify callbacks
                for callback in self._pattern_callbacks:
                    try:
                        callback(pattern)
                    except Exception as e:
                        self.logger.error(f"Pattern callback failed: {e}")

                # Auto-export if path configured
                if self.export_path:
                    self._auto_export()
            else:
                # Update existing pattern
                self._patterns[pattern_key].count = count
                self._patterns[pattern_key].last_seen = record.timestamp
                self._patterns[pattern_key].severity = severity
                self._patterns[pattern_key].affected_plugins.extend(affected_plugins)

    def on_pattern_detected(self, callback: Callable[[ErrorPattern], None]) -> None:
        """
        Register a callback to be called when a new error pattern is detected.

        Args:
            callback: Function that takes an ErrorPattern as argument
        """
        self._pattern_callbacks.append(callback)

    def get_error_summary(self) -> Dict[str, Any]:
        """
        Get summary of all errors for reporting.

        Returns:
            Dictionary with error statistics and recent errors
        """
        with self._lock:
            # Calculate error rate (errors per hour)
            session_duration = (datetime.now() - self._session_start).total_seconds() / 3600
            error_rate = len(self._records) / max(session_duration, 0.01)

            return {
                "session_start": self._session_start.isoformat(),
                "total_errors": len(self._records),
                "error_rate_per_hour": round(error_rate, 2),
                "error_counts_by_type": dict(self._error_counts),
                "plugin_error_counts": {
                    k: dict(v) for k, v in self._plugin_error_counts.items()
                },
                "active_patterns": {
                    k: v.to_dict() for k, v in self._patterns.items()
                },
                "recent_errors": [
                    r.to_dict() for r in self._records[-20:]
                ]
            }

    def get_plugin_health(self, plugin_id: str) -> Dict[str, Any]:
        """
        Get health status for a specific plugin.

        Args:
            plugin_id: Plugin ID to check

        Returns:
            Dictionary with plugin error statistics
        """
        with self._lock:
            plugin_errors = self._plugin_error_counts.get(plugin_id, {})
            recent_plugin_errors = [
                r for r in self._records[-100:]
                if r.plugin_id == plugin_id
            ]

            # Determine health status
            recent_count = len(recent_plugin_errors)
            if recent_count == 0:
                status = "healthy"
            elif recent_count < 5:
                status = "degraded"
            else:
                status = "unhealthy"

            return {
                "plugin_id": plugin_id,
                "status": status,
                "total_errors": sum(plugin_errors.values()),
                "error_types": dict(plugin_errors),
                "recent_error_count": recent_count,
                "last_error": recent_plugin_errors[-1].to_dict() if recent_plugin_errors else None
            }

    def clear_old_records(self, max_age_hours: int = 24) -> int:
        """
        Clear records older than specified age.

        Args:
            max_age_hours: Maximum age in hours

        Returns:
            Number of records cleared
        """
        with self._lock:
            cutoff = datetime.now() - timedelta(hours=max_age_hours)
            original_count = len(self._records)
            self._records = [r for r in self._records if r.timestamp > cutoff]
            cleared = original_count - len(self._records)

            if cleared > 0:
                self.logger.info(f"Cleared {cleared} old error records")

            return cleared

    def export_to_file(self, filepath: Path) -> None:
        """
        Export error data to JSON file.

        Args:
            filepath: Path to export file
        """
        with self._lock:
            data = {
                "exported_at": datetime.now().isoformat(),
                "summary": self.get_error_summary(),
                "all_records": [r.to_dict() for r in self._records]
            }
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(json.dumps(data, indent=2))
            self.logger.info(f"Exported error data to {filepath}")

    def _auto_export(self) -> None:
        """Auto-export on pattern detection (if export_path configured)."""
        if self.export_path:
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filepath = self.export_path / f"errors_{timestamp}.json"
                self.export_to_file(filepath)
            except Exception as e:
                self.logger.error(f"Auto-export failed: {e}")


# Global singleton instance
_error_aggregator: Optional[ErrorAggregator] = None
_aggregator_lock = threading.Lock()


def get_error_aggregator(
    max_records: int = 1000,
    pattern_threshold: int = 5,
    pattern_window_minutes: int = 60,
    export_path: Optional[Path] = None
) -> ErrorAggregator:
    """
    Get or create the global error aggregator instance.

    Args:
        max_records: Maximum records to keep (only used on first call)
        pattern_threshold: Pattern detection threshold (only used on first call)
        pattern_window_minutes: Pattern detection window (only used on first call)
        export_path: Export path for auto-export (only used on first call)

    Returns:
        The global ErrorAggregator instance
    """
    global _error_aggregator

    with _aggregator_lock:
        if _error_aggregator is None:
            _error_aggregator = ErrorAggregator(
                max_records=max_records,
                pattern_threshold=pattern_threshold,
                pattern_window_minutes=pattern_window_minutes,
                export_path=export_path
            )
        return _error_aggregator


def record_error(
    error: Exception,
    context: Optional[Dict[str, Any]] = None,
    plugin_id: Optional[str] = None,
    operation: Optional[str] = None
) -> ErrorRecord:
    """
    Convenience function to record an error to the global aggregator.

    Args:
        error: The exception that occurred
        context: Optional context dictionary
        plugin_id: Optional plugin ID
        operation: Optional operation name

    Returns:
        The created ErrorRecord
    """
    return get_error_aggregator().record_error(
        error=error,
        context=context,
        plugin_id=plugin_id,
        operation=operation
    )
