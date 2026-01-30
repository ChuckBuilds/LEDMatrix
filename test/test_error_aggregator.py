"""
Tests for the error aggregation service.

Tests:
- Error recording
- Pattern detection
- Error summary generation
- Plugin health tracking
- Thread safety
"""

import pytest
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
import threading
import sys

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.error_aggregator import (
    ErrorAggregator,
    ErrorRecord,
    ErrorPattern,
    get_error_aggregator,
    record_error
)
from src.exceptions import PluginError, ConfigError


class TestErrorRecording:
    """Test basic error recording functionality."""

    def test_record_error_creates_record(self):
        """Recording an error should create an ErrorRecord."""
        aggregator = ErrorAggregator(max_records=100)

        error = ValueError("Test error message")
        record = aggregator.record_error(
            error=error,
            plugin_id="test-plugin",
            operation="update"
        )

        assert record.error_type == "ValueError"
        assert record.message == "Test error message"
        assert record.plugin_id == "test-plugin"
        assert record.operation == "update"
        assert record.stack_trace is not None

    def test_record_error_with_context(self):
        """Error context should be preserved."""
        aggregator = ErrorAggregator()

        error = ValueError("Test error")
        context = {"key": "value", "count": 42}

        record = aggregator.record_error(
            error=error,
            context=context,
            plugin_id="test-plugin"
        )

        assert record.context["key"] == "value"
        assert record.context["count"] == 42

    def test_ledmatrix_error_context_extracted(self):
        """Context from LEDMatrixError subclasses should be extracted."""
        aggregator = ErrorAggregator()

        error = PluginError(
            "Plugin failed",
            plugin_id="failing-plugin",
            context={"additional": "info"}
        )

        record = aggregator.record_error(error=error)

        assert "plugin_id" in record.context
        assert record.context["additional"] == "info"

    def test_max_records_limit(self):
        """Records should not exceed max_records limit."""
        aggregator = ErrorAggregator(max_records=5)

        for i in range(10):
            aggregator.record_error(error=ValueError(f"Error {i}"))

        assert len(aggregator._records) == 5
        # Oldest records should be removed
        assert "Error 5" in aggregator._records[0].message

    def test_error_counts_updated(self):
        """Error counts should be updated correctly."""
        aggregator = ErrorAggregator()

        for _ in range(3):
            aggregator.record_error(error=ValueError("Test"))

        for _ in range(2):
            aggregator.record_error(error=TypeError("Test"))

        assert aggregator._error_counts["ValueError"] == 3
        assert aggregator._error_counts["TypeError"] == 2

    def test_plugin_error_counts_updated(self):
        """Plugin-specific error counts should be updated."""
        aggregator = ErrorAggregator()

        aggregator.record_error(
            error=ValueError("Error 1"),
            plugin_id="plugin-a"
        )
        aggregator.record_error(
            error=ValueError("Error 2"),
            plugin_id="plugin-a"
        )
        aggregator.record_error(
            error=ValueError("Error 3"),
            plugin_id="plugin-b"
        )

        assert aggregator._plugin_error_counts["plugin-a"]["ValueError"] == 2
        assert aggregator._plugin_error_counts["plugin-b"]["ValueError"] == 1


class TestPatternDetection:
    """Test error pattern detection."""

    def test_pattern_detected_after_threshold(self):
        """Pattern should be detected after threshold occurrences."""
        aggregator = ErrorAggregator(
            pattern_threshold=3,
            pattern_window_minutes=60
        )

        # Record 3 errors of same type
        for _ in range(3):
            aggregator.record_error(error=ValueError("Recurring error"))

        assert "ValueError" in aggregator._patterns

    def test_pattern_not_detected_below_threshold(self):
        """Pattern should not be detected below threshold."""
        aggregator = ErrorAggregator(
            pattern_threshold=5,
            pattern_window_minutes=60
        )

        # Record only 2 errors
        for _ in range(2):
            aggregator.record_error(error=ValueError("Infrequent error"))

        assert "ValueError" not in aggregator._patterns

    def test_pattern_severity_increases_with_count(self):
        """Pattern severity should increase with more occurrences."""
        aggregator = ErrorAggregator(
            pattern_threshold=2,
            pattern_window_minutes=60
        )

        # Record enough to trigger critical severity
        for _ in range(10):
            aggregator.record_error(error=ValueError("Many errors"))

        pattern = aggregator._patterns.get("ValueError")
        assert pattern is not None
        assert pattern.severity in ["error", "critical"]

    def test_pattern_callback_called(self):
        """Pattern detection callback should be called."""
        aggregator = ErrorAggregator(pattern_threshold=2)

        callback_called = []

        def callback(pattern):
            callback_called.append(pattern)

        aggregator.on_pattern_detected(callback)

        # Trigger pattern
        for _ in range(3):
            aggregator.record_error(error=ValueError("Pattern trigger"))

        assert len(callback_called) == 1
        assert callback_called[0].error_type == "ValueError"


class TestErrorSummary:
    """Test error summary generation."""

    def test_summary_contains_required_fields(self):
        """Summary should contain all required fields."""
        aggregator = ErrorAggregator()

        aggregator.record_error(
            error=ValueError("Test"),
            plugin_id="test-plugin"
        )

        summary = aggregator.get_error_summary()

        assert "session_start" in summary
        assert "total_errors" in summary
        assert "error_rate_per_hour" in summary
        assert "error_counts_by_type" in summary
        assert "plugin_error_counts" in summary
        assert "active_patterns" in summary
        assert "recent_errors" in summary

    def test_summary_error_counts(self):
        """Summary should have correct error counts."""
        aggregator = ErrorAggregator()

        aggregator.record_error(error=ValueError("Error 1"))
        aggregator.record_error(error=ValueError("Error 2"))
        aggregator.record_error(error=TypeError("Error 3"))

        summary = aggregator.get_error_summary()

        assert summary["total_errors"] == 3
        assert summary["error_counts_by_type"]["ValueError"] == 2
        assert summary["error_counts_by_type"]["TypeError"] == 1


class TestPluginHealth:
    """Test plugin health tracking."""

    def test_healthy_plugin_status(self):
        """Plugin with no recent errors should be healthy."""
        aggregator = ErrorAggregator()

        health = aggregator.get_plugin_health("healthy-plugin")

        assert health["status"] == "healthy"
        assert health["total_errors"] == 0
        assert health["recent_error_count"] == 0

    def test_degraded_plugin_status(self):
        """Plugin with some errors should be degraded."""
        aggregator = ErrorAggregator()

        for _ in range(3):
            aggregator.record_error(
                error=ValueError("Error"),
                plugin_id="degraded-plugin"
            )

        health = aggregator.get_plugin_health("degraded-plugin")

        assert health["status"] == "degraded"
        assert health["recent_error_count"] == 3

    def test_unhealthy_plugin_status(self):
        """Plugin with many errors should be unhealthy."""
        aggregator = ErrorAggregator()

        for _ in range(10):
            aggregator.record_error(
                error=ValueError("Error"),
                plugin_id="unhealthy-plugin"
            )

        health = aggregator.get_plugin_health("unhealthy-plugin")

        assert health["status"] == "unhealthy"
        assert health["recent_error_count"] == 10


class TestRecordClearing:
    """Test clearing old records."""

    def test_clear_old_records(self):
        """Old records should be cleared."""
        aggregator = ErrorAggregator()

        # Add a record
        aggregator.record_error(error=ValueError("Old error"))

        # Manually age the record
        aggregator._records[0].timestamp = datetime.now() - timedelta(hours=48)

        # Clear records older than 24 hours
        cleared = aggregator.clear_old_records(max_age_hours=24)

        assert cleared == 1
        assert len(aggregator._records) == 0

    def test_recent_records_not_cleared(self):
        """Recent records should not be cleared."""
        aggregator = ErrorAggregator()

        aggregator.record_error(error=ValueError("Recent error"))

        cleared = aggregator.clear_old_records(max_age_hours=24)

        assert cleared == 0
        assert len(aggregator._records) == 1


class TestThreadSafety:
    """Test thread safety of error aggregator."""

    def test_concurrent_recording(self):
        """Multiple threads should be able to record errors concurrently."""
        aggregator = ErrorAggregator(max_records=1000)
        errors_per_thread = 100
        num_threads = 5

        def record_errors(thread_id):
            for i in range(errors_per_thread):
                aggregator.record_error(
                    error=ValueError(f"Thread {thread_id} error {i}"),
                    plugin_id=f"plugin-{thread_id}"
                )

        threads = [
            threading.Thread(target=record_errors, args=(i,))
            for i in range(num_threads)
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # All errors should be recorded
        assert len(aggregator._records) == errors_per_thread * num_threads


class TestGlobalAggregator:
    """Test global aggregator singleton."""

    def test_get_error_aggregator_returns_same_instance(self):
        """get_error_aggregator should return the same instance."""
        agg1 = get_error_aggregator()
        agg2 = get_error_aggregator()

        assert agg1 is agg2

    def test_record_error_convenience_function(self):
        """record_error convenience function should work."""
        record = record_error(
            error=ValueError("Convenience function test"),
            plugin_id="test"
        )

        assert record.error_type == "ValueError"
        assert record.plugin_id == "test"


class TestSerialization:
    """Test error record serialization."""

    def test_error_record_to_dict(self):
        """ErrorRecord should serialize to dict correctly."""
        record = ErrorRecord(
            error_type="ValueError",
            message="Test message",
            timestamp=datetime.now(),
            context={"key": "value"},
            plugin_id="test-plugin",
            operation="update",
            stack_trace="traceback..."
        )

        data = record.to_dict()

        assert data["error_type"] == "ValueError"
        assert data["message"] == "Test message"
        assert data["plugin_id"] == "test-plugin"
        assert data["operation"] == "update"
        assert "timestamp" in data

    def test_error_pattern_to_dict(self):
        """ErrorPattern should serialize to dict correctly."""
        pattern = ErrorPattern(
            error_type="ValueError",
            count=5,
            first_seen=datetime.now() - timedelta(hours=1),
            last_seen=datetime.now(),
            affected_plugins=["plugin-a", "plugin-b"],
            sample_messages=["Error 1", "Error 2"],
            severity="warning"
        )

        data = pattern.to_dict()

        assert data["error_type"] == "ValueError"
        assert data["count"] == 5
        assert data["severity"] == "warning"
        assert len(data["affected_plugins"]) == 2
