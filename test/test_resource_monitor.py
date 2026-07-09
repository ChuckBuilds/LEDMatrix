"""
Tests for src/plugin_system/resource_monitor.py

Focus areas:
- Execution-time metrics are captured regardless of psutil availability.
- CPU sampling is non-blocking (regression guard for the previous
  ``cpu_percent(interval=0.1)`` call that blocked 100 ms per monitored call).
- Resource limits are enforced.
"""

import time

import pytest
from unittest.mock import MagicMock

from src.plugin_system.resource_monitor import (
    PluginResourceMonitor,
    ResourceLimits,
    ResourceLimitExceeded,
    PSUTIL_AVAILABLE,
)


def _cache():
    cache = MagicMock()
    cache.get.return_value = None
    return cache


class TestExecutionTimeMetrics:
    def test_monitor_call_returns_value_and_records_call(self):
        mon = PluginResourceMonitor(_cache(), enable_monitoring=False)
        result = mon.monitor_call("p", lambda: 42)
        assert result == 42
        metrics = mon.get_metrics("p")
        assert metrics.call_count == 1
        assert metrics.total_execution_time >= 0.0

    def test_avg_and_max_execution_time(self):
        mon = PluginResourceMonitor(_cache(), enable_monitoring=False)
        mon.monitor_call("p", lambda: time.sleep(0.01))
        mon.monitor_call("p", lambda: None)
        summary = mon.get_metrics_summary("p")
        assert summary["call_count"] == 2
        assert summary["max_execution_time"] >= summary["avg_execution_time"] >= 0.0

    def test_exception_propagates_but_is_still_timed(self):
        mon = PluginResourceMonitor(_cache(), enable_monitoring=False)

        def boom():
            raise ValueError("nope")

        with pytest.raises(ValueError):
            mon.monitor_call("p", boom)
        # Execution time is still recorded even when the call raised.
        assert mon.get_metrics("p").execution_time >= 0.0


class TestNonBlockingCpu:
    def test_cpu_sampling_is_fast_when_disabled(self):
        mon = PluginResourceMonitor(_cache(), enable_monitoring=False)
        start = time.time()
        for _ in range(50):
            mon._get_process_cpu_percent()
        # The old implementation blocked ~0.1s/call (~5s for 50). Non-blocking
        # must complete near-instantly.
        assert time.time() - start < 0.5
        assert mon._get_process_cpu_percent() == 0.0

    @pytest.mark.skipif(not PSUTIL_AVAILABLE, reason="psutil not installed")
    def test_cpu_sampling_is_fast_with_psutil(self):
        mon = PluginResourceMonitor(_cache(), enable_monitoring=True)
        assert mon._process is not None
        start = time.time()
        for _ in range(30):
            mon._get_process_cpu_percent()
        # 30 blocking 0.1s samples would be ~3s; non-blocking must be well under.
        assert time.time() - start < 0.5

    def test_monitor_call_does_not_block_on_cpu_sampling(self):
        mon = PluginResourceMonitor(_cache())  # enable depends on psutil
        start = time.time()
        for _ in range(25):
            mon.monitor_call("p", lambda: None)
        # 25 * 0.1s = 2.5s under the old blocking bug; must be far faster now.
        assert time.time() - start < 1.0


class TestResourceLimits:
    def test_execution_time_limit_raises(self):
        mon = PluginResourceMonitor(_cache(), enable_monitoring=False)
        mon.set_limits("p", ResourceLimits(max_execution_time=0.001))
        with pytest.raises(ResourceLimitExceeded):
            mon.monitor_call("p", lambda: time.sleep(0.02))

    def test_reset_metrics_clears_counts(self):
        cache = _cache()
        mon = PluginResourceMonitor(cache, enable_monitoring=False)
        mon.monitor_call("p", lambda: None)
        assert mon.get_metrics("p").call_count == 1
        mon.reset_metrics("p")
        assert mon.get_metrics("p").call_count == 0


class TestForceReload:
    def test_force_reload_refreshes_stale_snapshot(self):
        """A read-only consumer must see the writer process's latest persisted
        metrics rather than a pinned first snapshot."""
        cache = MagicMock()
        persisted = {"value": None}  # only the metrics key returns data

        def cache_get(key, max_age=None, memory_ttl=None):
            return persisted["value"] if key.startswith("plugin_metrics:") else None

        cache.get.side_effect = cache_get
        mon = PluginResourceMonitor(cache, enable_monitoring=False)

        # First read snapshots empty metrics.
        assert mon.get_metrics_summary("p")["call_count"] == 0

        # The display service later persists real metrics.
        persisted["value"] = {"call_count": 7, "total_execution_time": 1.4}

        # Plain read stays stale...
        assert mon.get_metrics_summary("p")["call_count"] == 0
        # ...force_reload picks up the persisted values and bypasses memory.
        fresh = mon.get_metrics_summary("p", force_reload=True)
        assert fresh["call_count"] == 7
        assert any(c.kwargs.get("memory_ttl") == 0 for c in cache.get.call_args_list)
