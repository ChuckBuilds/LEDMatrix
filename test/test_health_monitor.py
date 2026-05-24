"""
Tests for src/plugin_system/health_monitor.py

Covers PluginHealthMonitor: get_plugin_health_status, get_plugin_health_metrics,
get_all_plugin_health, _get_recovery_suggestions, start/stop_monitoring,
register_health_check.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from src.plugin_system.health_monitor import (
    PluginHealthMonitor,
    HealthStatus,
    HealthMetrics,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_health_tracker(
    summary: dict | None = None,
    all_summaries: dict | None = None,
):
    """Return a mock PluginHealthTracker."""
    tracker = MagicMock()
    tracker.get_health_summary.return_value = summary
    tracker.get_all_health_summaries.return_value = all_summaries or {}
    return tracker


def _healthy_summary() -> dict:
    return {
        "success_rate": 100.0,
        "circuit_state": "closed",
        "consecutive_failures": 0,
        "total_failures": 0,
        "total_successes": 50,
        "last_success_time": datetime.now().isoformat(),
        "last_error": None,
    }


def _degraded_summary() -> dict:
    return {
        "success_rate": 40.0,  # 60% error rate
        "circuit_state": "closed",
        "consecutive_failures": 3,
        "total_failures": 6,
        "total_successes": 4,
        "last_success_time": None,
        "last_error": "timeout occurred",
    }


def _unhealthy_summary() -> dict:
    return {
        "success_rate": 10.0,  # 90% error rate
        "circuit_state": "open",
        "consecutive_failures": 10,
        "total_failures": 9,
        "total_successes": 1,
        "last_success_time": None,
        "last_error": "ImportError: missing module",
    }


@pytest.fixture
def monitor():
    tracker = _make_health_tracker(_healthy_summary())
    return PluginHealthMonitor(health_tracker=tracker)


# ---------------------------------------------------------------------------
# get_plugin_health_status
# ---------------------------------------------------------------------------

class TestGetPluginHealthStatus:
    def test_healthy_status(self):
        tracker = _make_health_tracker(_healthy_summary())
        monitor = PluginHealthMonitor(tracker)
        status = monitor.get_plugin_health_status("plugin_a")
        assert status == HealthStatus.HEALTHY

    def test_degraded_status(self):
        tracker = _make_health_tracker(_degraded_summary())
        monitor = PluginHealthMonitor(tracker, degraded_threshold=0.5, unhealthy_threshold=0.8)
        status = monitor.get_plugin_health_status("plugin_b")
        assert status == HealthStatus.DEGRADED

    def test_unhealthy_status(self):
        tracker = _make_health_tracker(_unhealthy_summary())
        monitor = PluginHealthMonitor(tracker, unhealthy_threshold=0.8)
        status = monitor.get_plugin_health_status("plugin_c")
        assert status == HealthStatus.UNHEALTHY

    def test_open_circuit_breaker_is_unhealthy(self):
        summary = _healthy_summary()
        summary["circuit_state"] = "open"
        tracker = _make_health_tracker(summary)
        monitor = PluginHealthMonitor(tracker)
        status = monitor.get_plugin_health_status("plugin_d")
        assert status == HealthStatus.UNHEALTHY

    def test_unknown_when_no_tracker(self):
        monitor = PluginHealthMonitor(health_tracker=None)
        status = monitor.get_plugin_health_status("plugin_e")
        assert status == HealthStatus.UNKNOWN

    def test_unknown_when_no_summary(self):
        tracker = _make_health_tracker(None)
        monitor = PluginHealthMonitor(tracker)
        status = monitor.get_plugin_health_status("plugin_f")
        assert status == HealthStatus.UNKNOWN


# ---------------------------------------------------------------------------
# get_plugin_health_metrics
# ---------------------------------------------------------------------------

class TestGetPluginHealthMetrics:
    def test_healthy_metrics(self):
        tracker = _make_health_tracker(_healthy_summary())
        monitor = PluginHealthMonitor(tracker)
        metrics = monitor.get_plugin_health_metrics("plugin_a")
        assert isinstance(metrics, HealthMetrics)
        assert metrics.status == HealthStatus.HEALTHY
        assert metrics.success_rate == pytest.approx(1.0)
        assert metrics.error_rate == pytest.approx(0.0)

    def test_degraded_metrics(self):
        tracker = _make_health_tracker(_degraded_summary())
        monitor = PluginHealthMonitor(tracker, degraded_threshold=0.5, unhealthy_threshold=0.8)
        metrics = monitor.get_plugin_health_metrics("plugin_b")
        assert metrics.status == HealthStatus.DEGRADED
        assert metrics.consecutive_failures == 3

    def test_unhealthy_metrics(self):
        tracker = _make_health_tracker(_unhealthy_summary())
        monitor = PluginHealthMonitor(tracker, unhealthy_threshold=0.8)
        metrics = monitor.get_plugin_health_metrics("plugin_c")
        assert metrics.status == HealthStatus.UNHEALTHY
        assert metrics.circuit_breaker_state == "open"
        assert metrics.last_error is not None

    def test_metrics_without_tracker(self):
        monitor = PluginHealthMonitor(health_tracker=None)
        metrics = monitor.get_plugin_health_metrics("plugin_d")
        assert metrics.status == HealthStatus.UNKNOWN
        assert metrics.plugin_id == "plugin_d"

    def test_metrics_without_summary(self):
        tracker = _make_health_tracker(None)
        monitor = PluginHealthMonitor(tracker)
        metrics = monitor.get_plugin_health_metrics("plugin_e")
        assert metrics.status == HealthStatus.UNKNOWN

    def test_last_successful_update_parsed(self):
        summary = _healthy_summary()
        summary["last_success_time"] = "2024-06-01T12:00:00"
        tracker = _make_health_tracker(summary)
        monitor = PluginHealthMonitor(tracker)
        metrics = monitor.get_plugin_health_metrics("plugin_a")
        assert metrics.last_successful_update is not None
        assert isinstance(metrics.last_successful_update, datetime)

    def test_invalid_last_success_time_handled(self):
        summary = _healthy_summary()
        summary["last_success_time"] = "not-a-date"
        tracker = _make_health_tracker(summary)
        monitor = PluginHealthMonitor(tracker)
        # Should not raise
        metrics = monitor.get_plugin_health_metrics("plugin_a")
        assert metrics.last_successful_update is None

    def test_total_successes_failures(self):
        tracker = _make_health_tracker(_degraded_summary())
        monitor = PluginHealthMonitor(tracker, degraded_threshold=0.5, unhealthy_threshold=0.8)
        metrics = monitor.get_plugin_health_metrics("plugin_b")
        assert metrics.total_failures == 6
        assert metrics.total_successes == 4


# ---------------------------------------------------------------------------
# get_all_plugin_health
# ---------------------------------------------------------------------------

class TestGetAllPluginHealth:
    def test_returns_empty_without_tracker(self):
        monitor = PluginHealthMonitor(health_tracker=None)
        result = monitor.get_all_plugin_health()
        assert result == {}

    def test_returns_metrics_for_each_plugin(self):
        all_summaries = {
            "plugin_a": _healthy_summary(),
            "plugin_b": _degraded_summary(),
        }
        tracker = MagicMock()
        tracker.get_all_health_summaries.return_value = all_summaries
        tracker.get_health_summary.side_effect = lambda pid: all_summaries.get(pid)
        monitor = PluginHealthMonitor(tracker, degraded_threshold=0.5, unhealthy_threshold=0.8)
        result = monitor.get_all_plugin_health()
        assert "plugin_a" in result
        assert "plugin_b" in result
        assert isinstance(result["plugin_a"], HealthMetrics)

    def test_returns_empty_when_no_summaries(self):
        tracker = _make_health_tracker(all_summaries={})
        monitor = PluginHealthMonitor(tracker)
        result = monitor.get_all_plugin_health()
        assert result == {}


# ---------------------------------------------------------------------------
# _get_recovery_suggestions
# ---------------------------------------------------------------------------

class TestGetRecoverySuggestions:
    def test_healthy_plugin_suggestion(self):
        tracker = _make_health_tracker(_healthy_summary())
        monitor = PluginHealthMonitor(tracker)
        suggestions = monitor._get_recovery_suggestions("p", _healthy_summary(), HealthStatus.HEALTHY)
        assert any("healthy" in s.lower() for s in suggestions)

    def test_unhealthy_suggestions(self):
        tracker = _make_health_tracker(_unhealthy_summary())
        monitor = PluginHealthMonitor(tracker, unhealthy_threshold=0.8)
        suggestions = monitor._get_recovery_suggestions("p", _unhealthy_summary(), HealthStatus.UNHEALTHY)
        assert len(suggestions) > 0
        assert any("unhealthy" in s.lower() for s in suggestions)

    def test_open_circuit_breaker_suggestion(self):
        summary = _unhealthy_summary()
        summary["circuit_state"] = "open"
        tracker = _make_health_tracker(summary)
        monitor = PluginHealthMonitor(tracker, unhealthy_threshold=0.8)
        suggestions = monitor._get_recovery_suggestions("p", summary, HealthStatus.UNHEALTHY)
        assert any("circuit" in s.lower() for s in suggestions)

    def test_timeout_error_suggestion(self):
        summary = _degraded_summary()
        summary["last_error"] = "connection timeout occurred"
        tracker = _make_health_tracker(summary)
        monitor = PluginHealthMonitor(tracker, degraded_threshold=0.5, unhealthy_threshold=0.8)
        suggestions = monitor._get_recovery_suggestions("p", summary, HealthStatus.DEGRADED)
        assert any("timeout" in s.lower() for s in suggestions)

    def test_import_error_suggestion(self):
        summary = _unhealthy_summary()
        summary["last_error"] = "ImportError: missing module"
        tracker = _make_health_tracker(summary)
        monitor = PluginHealthMonitor(tracker, unhealthy_threshold=0.8)
        suggestions = monitor._get_recovery_suggestions("p", summary, HealthStatus.UNHEALTHY)
        assert any("dependencies" in s.lower() or "import" in s.lower() or "missing" in s.lower()
                   for s in suggestions)

    def test_permission_error_suggestion(self):
        summary = _unhealthy_summary()
        summary["last_error"] = "permission denied to access resource"
        tracker = _make_health_tracker(summary)
        monitor = PluginHealthMonitor(tracker, unhealthy_threshold=0.8)
        suggestions = monitor._get_recovery_suggestions("p", summary, HealthStatus.UNHEALTHY)
        assert any("permission" in s.lower() for s in suggestions)

    def test_degraded_suggestions_include_error_rate(self):
        tracker = _make_health_tracker(_degraded_summary())
        monitor = PluginHealthMonitor(tracker, degraded_threshold=0.5, unhealthy_threshold=0.8)
        suggestions = monitor._get_recovery_suggestions("p", _degraded_summary(), HealthStatus.DEGRADED)
        assert any("%" in s for s in suggestions)


# ---------------------------------------------------------------------------
# start / stop monitoring
# ---------------------------------------------------------------------------

class TestMonitorLifecycle:
    def test_start_monitoring(self, monitor):
        monitor.start_monitoring()
        assert monitor._monitor_thread is not None
        assert monitor._monitor_thread.is_alive()
        monitor.stop_monitoring()

    def test_stop_monitoring(self, monitor):
        monitor.start_monitoring()
        monitor.stop_monitoring()
        # Thread should no longer be alive
        assert not monitor._monitor_thread.is_alive()

    def test_double_start_no_duplicate_threads(self, monitor):
        monitor.start_monitoring()
        thread1 = monitor._monitor_thread
        monitor.start_monitoring()  # should be idempotent
        assert monitor._monitor_thread is thread1
        monitor.stop_monitoring()

    def test_register_health_check(self, monitor):
        callback = MagicMock()
        monitor.register_health_check(callback)
        assert callback in monitor._health_check_callbacks
