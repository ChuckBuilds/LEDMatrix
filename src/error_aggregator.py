"""
Error Aggregation Service

Provides centralized error tracking, pattern detection, and reporting
for the LEDMatrix system. Enables automatic bug detection by tracking
error frequency, patterns, and context.

This is a local-only implementation with no external dependencies.
Errors are stored in memory with optional JSON export.
"""

import math
import threading
import time
import traceback
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple
import logging

from src.exceptions import LEDMatrixError
from src.redaction import redact_credentials


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

        # Bumped on every change, so a publisher can tell "nothing new since
        # the last snapshot" without comparing snapshots.
        self._version = 0

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
            self._version += 1

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

    @property
    def version(self) -> int:
        """Changes whenever the recorded errors do (see ErrorSnapshotPublisher)."""
        return self._version

    def clear_before(self, cutoff: datetime) -> int:
        """Forget every error recorded at or before ``cutoff``.

        Unlike clear_old_records, this also resets what the summary reports:
        the per-type and per-plugin counts are rebuilt from the records that
        remain, and detected patterns that began before the cutoff are dropped
        (one that is still happening is detected again on its next
        occurrence). Errors recorded after the
        cutoff are kept, so a clear that is applied a few seconds after it was
        requested does not swallow what happened in between.

        Returns:
            Number of records removed
        """
        with self._lock:
            kept = [r for r in self._records if r.timestamp > cutoff]
            cleared = len(self._records) - len(kept)
            self._records = kept
            self._error_counts = defaultdict(int)
            self._plugin_error_counts = defaultdict(lambda: defaultdict(int))
            for r in kept:
                self._error_counts[r.error_type] += 1
                if r.plugin_id:
                    self._plugin_error_counts[r.plugin_id][r.error_type] += 1
            # A pattern that began after the cutoff is made only of kept
            # errors; any other would carry cleared ones in its count.
            self._patterns = {k: p for k, p in self._patterns.items() if p.first_seen > cutoff}
            self._version += 1
            return cleared

    def build_snapshot(self) -> Dict[str, Any]:
        """A bounded, JSON-safe copy of the summary for another process.

        The shape of get_error_summary() plus ``generated_at`` and
        ``plugin_health`` (get_plugin_health() for every plugin with errors).
        Messages, stack traces and context are clipped so that one plugin
        raising a huge exception cannot make the snapshot large.
        """
        with self._lock:
            summary = self.get_error_summary()
            summary["generated_at"] = datetime.now().isoformat()
            summary["recent_errors"] = [
                _compact_record(r) for r in summary["recent_errors"][-_SNAPSHOT_RECENT_ERRORS:]
            ]
            patterns = {}
            for key, pattern in list(summary["active_patterns"].items())[:_SNAPSHOT_MAX_PATTERNS]:
                pattern = dict(pattern)
                pattern["affected_plugins"] = [
                    _clip(p, _SNAPSHOT_ID_CHARS) for p in pattern.get("affected_plugins", [])
                ][:_SNAPSHOT_MAX_AFFECTED_PLUGINS]
                pattern["sample_messages"] = [
                    _redacted_clip(m, _SNAPSHOT_SAMPLE_CHARS) for m in pattern.get("sample_messages", [])
                ][:3]
                patterns[key] = pattern
            summary["active_patterns"] = patterns
            health = {}
            for plugin_id in summary["plugin_error_counts"]:
                entry = self.get_plugin_health(plugin_id)
                if entry["last_error"] is not None:
                    entry["last_error"] = _compact_record(entry["last_error"])
                health[plugin_id] = entry
            summary["plugin_health"] = health
        # Round-trip so a context value the cache's encoder cannot handle is
        # turned into a string here rather than failing the write.
        return json.loads(json.dumps(summary, default=str))

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


# ---------------------------------------------------------------------------
# Sharing the display service's errors with the web interface
# ---------------------------------------------------------------------------
#
# The two services are separate processes, so each has its own aggregator,
# and only the display service's ever records anything (plugin_executor runs
# the plugins there). The web interface therefore reads a snapshot the display
# service publishes to the shared cache directory -- the same channel, and the
# same file permissions, as display_current_state and plugin_metrics:*: files
# are 0660 and carry the cache directory's group, so root writes and the web
# user reads, and the other way round for the clear request.
#
#   ERROR_SNAPSHOT_KEY       written by the display service only
#   ERROR_CLEAR_REQUEST_KEY  written by the web interface only
#
# A clear is asynchronous: the web interface records a request, and the
# display service applies it (clear_before) on its next tick and republishes.
# Until it has, the web interface hides whatever the snapshot shows from
# before the cutoff, so a clear takes effect for readers immediately and a
# snapshot published just before the request cannot bring old errors back.
# The web interface never writes the snapshot itself: two writers would race,
# and a snapshot owned by the web user is one more file root's write has to
# replace.

ERROR_SNAPSHOT_KEY = "plugin_error_snapshot"
ERROR_CLEAR_REQUEST_KEY = "plugin_error_clear_request"

#: Shortest gap between two snapshot writes, in seconds. A plugin failing in
#: a tight loop changes the aggregator many times a second; the snapshot is
#: rewritten at most this often, and only when something changed.
SNAPSHOT_MIN_INTERVAL = 10.0

#: How often the display service checks for changes and clear requests. A
#: check is an in-memory comparison plus reading one small file.
SNAPSHOT_TICK_INTERVAL = 5.0

_SNAPSHOT_RECENT_ERRORS = 20
_SNAPSHOT_MAX_PATTERNS = 50
_SNAPSHOT_MAX_AFFECTED_PLUGINS = 50
_SNAPSHOT_MESSAGE_CHARS = 300
_SNAPSHOT_SAMPLE_CHARS = 200
_SNAPSHOT_TRACE_CHARS = 1200
_SNAPSHOT_ID_CHARS = 100
_SNAPSHOT_CONTEXT_KEYS = 20
_SNAPSHOT_CONTEXT_VALUE_CHARS = 200

#: Fields of get_error_summary(), which is what /errors/summary has always
#: returned. The snapshot's extra bookkeeping stays out of that response.
_SUMMARY_FIELDS = (
    "session_start", "total_errors", "error_rate_per_hour",
    "error_counts_by_type", "plugin_error_counts", "active_patterns",
    "recent_errors",
)

_snapshot_logger = logging.getLogger(__name__ + ".snapshot")


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _redacted_clip(value: Any, limit: int) -> str:
    """Redact, then clip. Clipping first could cut a ``token=`` marker off
    while keeping the secret after it, and the web side's redaction would then
    have nothing to match."""
    return _clip(redact_credentials(value if isinstance(value, str) else str(value)), limit)


def _compact_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """An ErrorRecord dict with every free-text field redacted and bounded."""
    compact = dict(record)
    compact["message"] = _redacted_clip(record.get("message") or "", _SNAPSHOT_MESSAGE_CHARS)
    if record.get("plugin_id") is not None:
        compact["plugin_id"] = _clip(record["plugin_id"], _SNAPSHOT_ID_CHARS)
    trace = record.get("stack_trace")
    if isinstance(trace, str):
        trace = redact_credentials(trace)
        if len(trace) > _SNAPSHOT_TRACE_CHARS:
            # The end of a traceback is the part that says what went wrong.
            trace = "..." + trace[-(_SNAPSHOT_TRACE_CHARS - 3):]
        compact["stack_trace"] = trace
    context = record.get("context")
    if isinstance(context, dict):
        compact["context"] = {
            _clip(k, _SNAPSHOT_ID_CHARS): (
                v if v is None or isinstance(v, (bool, int, float))
                else _redacted_clip(v, _SNAPSHOT_CONTEXT_VALUE_CHARS)
            )
            for k, v in list(context.items())[:_SNAPSHOT_CONTEXT_KEYS]
        }
    else:
        compact["context"] = {}
    return compact


class ErrorSnapshotPublisher:
    """Publishes the display service's aggregator to the shared cache.

    Runs in the display service only. tick() is the whole job; start() just
    calls it from a daemon thread every SNAPSHOT_TICK_INTERVAL seconds, which
    also means errors recorded while a write was being throttled still reach
    the cache once the interval has passed, and a clear request is applied
    even when no new error arrives to trigger a publish.

    Nothing here raises: a failure to read or write the cache is logged at
    debug and retried on a later tick.
    """

    def __init__(self, cache_manager: Any, aggregator: Optional[ErrorAggregator] = None,
                 min_interval: float = SNAPSHOT_MIN_INTERVAL,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.cache_manager = cache_manager
        self.aggregator = aggregator or get_error_aggregator()
        self.min_interval = min_interval
        self._clock = clock
        # None forces a first publish, which replaces a snapshot left behind
        # by a previous run of the service with this run's (empty) one.
        self._published_version: Optional[int] = None
        self._last_attempt: Optional[float] = None
        self._applied_clear_id: Optional[str] = None
        self._tick_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _apply_clear_request(self) -> bool:
        """Honour a clear request we have not applied yet. True if one was."""
        request = self.cache_manager.get(ERROR_CLEAR_REQUEST_KEY, max_age=None, memory_ttl=0)
        if not isinstance(request, dict):
            return False
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id or request_id == self._applied_clear_id:
            return False
        try:
            cutoff = float(request.get("cutoff"))
        except (TypeError, ValueError):
            cutoff = float("nan")
        if math.isfinite(cutoff):
            cleared = self.aggregator.clear_before(datetime.fromtimestamp(cutoff))
            _snapshot_logger.info("Cleared %d plugin error record(s) as requested (%s)",
                                  cleared, request_id)
        # A malformed request is acknowledged too, so it is not retried forever.
        self._applied_clear_id = request_id
        return True

    def tick(self) -> bool:
        """Apply a pending clear and publish if due. True if a snapshot was written."""
        with self._tick_lock:
            try:
                cleared = self._apply_clear_request()
                version = self.aggregator.version
                now = self._clock()
                if not cleared:
                    if version == self._published_version:
                        return False
                    if (self._last_attempt is not None
                            and now - self._last_attempt < self.min_interval):
                        return False
                # Stamp the attempt before writing: a cache that keeps failing
                # is retried at the throttled rate, not on every tick.
                self._last_attempt = now
                snapshot = self.aggregator.build_snapshot()
                snapshot["applied_clear_id"] = self._applied_clear_id
                self.cache_manager.set(ERROR_SNAPSHOT_KEY, snapshot)
                self._published_version = version
                return True
            except Exception as err:  # never let reporting break the display
                _snapshot_logger.debug("Could not publish the plugin error snapshot: %s",
                                       err, exc_info=True)
                return False

    def start(self, interval: float = SNAPSHOT_TICK_INTERVAL) -> None:
        """Tick from a daemon thread until stop(). A no-op while running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def run() -> None:
            self.tick()
            while not self._stop.wait(interval):
                self.tick()

        self._thread = threading.Thread(target=run, name="error-snapshot-publisher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


_snapshot_publisher: Optional[ErrorSnapshotPublisher] = None
_snapshot_publisher_lock = threading.Lock()


def start_error_snapshot_publisher(cache_manager: Any) -> Optional[ErrorSnapshotPublisher]:
    """Start publishing this process's errors for the web interface.

    Call from the display service only: whichever process calls it becomes
    the source of /api/v3/errors/*. Idempotent; never raises.
    """
    global _snapshot_publisher
    try:
        with _snapshot_publisher_lock:
            if _snapshot_publisher is None:
                _snapshot_publisher = ErrorSnapshotPublisher(cache_manager)
            else:
                _snapshot_publisher.cache_manager = cache_manager
            _snapshot_publisher.start()
            return _snapshot_publisher
    except Exception as err:
        _snapshot_logger.warning("Plugin error reporting to the web interface is unavailable: %s", err)
        return None


# --- Reading side (web interface) -------------------------------------------

def read_error_report(cache_manager: Any) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """The display service's latest snapshot and the latest clear request.

    memory_ttl=0: both keys are written by the other process, so only the
    file is current.
    """
    snapshot = cache_manager.get(ERROR_SNAPSHOT_KEY, max_age=None, memory_ttl=0)
    clear_request = cache_manager.get(ERROR_CLEAR_REQUEST_KEY, max_age=None, memory_ttl=0)
    return (snapshot if isinstance(snapshot, dict) else None,
            clear_request if isinstance(clear_request, dict) else None)


def _epoch(iso: Any) -> Optional[float]:
    """Seconds since the epoch for an aggregator timestamp (local, naive)."""
    if not isinstance(iso, str):
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _pending_cutoff(snapshot: Optional[Dict[str, Any]],
                    clear_request: Optional[Dict[str, Any]]) -> Optional[float]:
    """The cutoff of a clear the snapshot has not applied yet, if any."""
    if not clear_request:
        return None
    request_id = clear_request.get("request_id")
    if not request_id:
        return None
    if snapshot is not None and snapshot.get("applied_clear_id") == request_id:
        return None
    try:
        cutoff = float(clear_request.get("cutoff"))
    except (TypeError, ValueError):
        return None
    return cutoff if math.isfinite(cutoff) else None


def _is_after(item: Any, field_name: str, cutoff: float) -> bool:
    when = _epoch(item.get(field_name)) if isinstance(item, dict) else None
    return when is not None and when > cutoff


def _empty_summary(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "session_start": snapshot.get("session_start") if snapshot else None,
        "total_errors": 0,
        "error_rate_per_hour": 0.0,
        "error_counts_by_type": {},
        "plugin_error_counts": {},
        "active_patterns": {},
        "recent_errors": [],
    }


def error_summary_from_report(snapshot: Optional[Dict[str, Any]],
                              clear_request: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The /errors/summary payload: get_error_summary()'s shape plus
    ``generated_at``, ``snapshot_available`` and ``clear_pending``."""
    cutoff = _pending_cutoff(snapshot, clear_request)
    summary = _empty_summary(snapshot)
    if snapshot is not None:
        for name, default in summary.items():
            value = snapshot.get(name)
            if isinstance(value, type(default)) or (
                    isinstance(default, float) and isinstance(value, int)) or (
                    name == "session_start" and isinstance(value, str)):
                summary[name] = value
        if cutoff is not None:
            recent = summary["recent_errors"]
            newest = _epoch(recent[-1].get("timestamp")) if recent and isinstance(recent[-1], dict) else None
            if newest is None or newest <= cutoff:
                # Everything the display has reported predates the clear.
                summary = _empty_summary(snapshot)
            else:
                # Only part of it does. The lists can be filtered exactly; the
                # counts cannot, and stay as reported until the display
                # applies the clear (clear_pending says so).
                summary["recent_errors"] = [r for r in recent if _is_after(r, "timestamp", cutoff)]
                summary["active_patterns"] = {
                    k: p for k, p in summary["active_patterns"].items()
                    if _is_after(p, "last_seen", cutoff)
                }
    summary["generated_at"] = snapshot.get("generated_at") if snapshot else None
    summary["snapshot_available"] = snapshot is not None
    summary["clear_pending"] = cutoff is not None
    return summary


def plugin_health_from_report(snapshot: Optional[Dict[str, Any]],
                              clear_request: Optional[Dict[str, Any]],
                              plugin_id: str) -> Dict[str, Any]:
    """The /errors/plugin/<id> payload: get_plugin_health()'s shape plus
    ``generated_at``, ``snapshot_available`` and ``clear_pending``."""
    health: Dict[str, Any] = {
        "plugin_id": plugin_id,
        "status": "healthy",
        "total_errors": 0,
        "error_types": {},
        "recent_error_count": 0,
        "last_error": None,
    }
    cutoff = _pending_cutoff(snapshot, clear_request)
    table = snapshot.get("plugin_health") if snapshot else None
    entry = table.get(plugin_id) if isinstance(table, dict) else None
    if isinstance(entry, dict):
        # last_error is the plugin's newest error: if even that predates a
        # pending clear, so does everything else the display reported for it.
        if cutoff is None or _is_after(entry.get("last_error"), "timestamp", cutoff):
            for name in ("status", "total_errors", "error_types", "recent_error_count", "last_error"):
                if name in entry:
                    health[name] = entry[name]
    health["generated_at"] = snapshot.get("generated_at") if snapshot else None
    health["snapshot_available"] = snapshot is not None
    health["clear_pending"] = cutoff is not None
    return health


def _count_cleared(summary: Dict[str, Any], cutoff: float) -> Optional[int]:
    """How many of the reported errors a clear at ``cutoff`` hides, if known.

    Exact when every reported error predates the cutoff (always the case for
    a clear of everything) or when the report lists every error; otherwise
    only the display service knows, and None says so.
    """
    total = summary["total_errors"]
    times = [_epoch(r.get("timestamp")) if isinstance(r, dict) else None
             for r in summary["recent_errors"]]
    if total == 0 or (times and times[-1] is not None and times[-1] <= cutoff):
        return total
    if len(times) >= total and None not in times:
        return sum(1 for t in times if t <= cutoff)
    return None


def request_error_clear(cache_manager: Any, cutoff: float) -> Dict[str, Any]:
    """Ask the display service to forget errors recorded at or before ``cutoff``.

    Returns ``request_id``, ``cutoff`` (ISO, local time), ``cleared_count``
    (see _count_cleared) and ``clear_requested``. Raises OSError when the
    request did not reach the shared cache, since a cache without a usable
    directory accepts set() and keeps nothing.

    A request the display has not applied yet is only ever widened: a later,
    narrower one ("older than 24 hours" after "everything") overwriting it
    would otherwise bring back the errors the first one hid.
    """
    snapshot, clear_request = read_error_report(cache_manager)
    pending = _pending_cutoff(snapshot, clear_request)
    if pending is not None:
        cutoff = max(cutoff, pending)
    before = error_summary_from_report(snapshot, clear_request)
    request = {
        "request_id": uuid.uuid4().hex,
        "cutoff": cutoff,
        "requested_at": time.time(),
    }
    cache_manager.set(ERROR_CLEAR_REQUEST_KEY, request)
    stored = cache_manager.get(ERROR_CLEAR_REQUEST_KEY, max_age=None, memory_ttl=0)
    if not isinstance(stored, dict) or stored.get("request_id") != request["request_id"]:
        raise OSError("the clear request was not stored in the shared cache")
    return {
        "cleared_count": _count_cleared(before, cutoff),
        "clear_requested": True,
        "request_id": request["request_id"],
        "cutoff": datetime.fromtimestamp(cutoff).isoformat(),
    }
