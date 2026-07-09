"""
Tests for src/plugin_system/plugin_health.py

Focus on the additive ``set_degraded`` mechanism used by the warn-only schema
validation path: it must surface a degraded reason without touching the circuit
breaker or causing the plugin to be skipped.
"""

from unittest.mock import MagicMock

from src.plugin_system.plugin_health import PluginHealthTracker, CircuitState


def _cache():
    cache = MagicMock()
    cache.get.return_value = None
    return cache


def test_set_degraded_marks_and_surfaces_reason():
    tracker = PluginHealthTracker(_cache())
    tracker.set_degraded("p", "bad config")
    summary = tracker.get_health_summary("p")
    assert summary["degraded"] is True
    assert summary["degraded_reason"] == "bad config"


def test_set_degraded_none_clears():
    tracker = PluginHealthTracker(_cache())
    tracker.set_degraded("p", "bad config")
    tracker.set_degraded("p", None)
    summary = tracker.get_health_summary("p")
    assert summary["degraded"] is False
    assert summary["degraded_reason"] is None


def test_set_degraded_does_not_affect_circuit_breaker():
    tracker = PluginHealthTracker(_cache())
    tracker.set_degraded("p", "bad config")
    summary = tracker.get_health_summary("p")
    # Degraded is a *separate* signal from circuit health: the plugin is not
    # counted as failing, the circuit stays closed, and it is not skipped.
    assert summary["circuit_state"] == CircuitState.CLOSED.value
    assert summary["consecutive_failures"] == 0
    assert summary["is_healthy"] is True
    assert tracker.should_skip_plugin("p") is False


def test_set_degraded_skips_redundant_cache_write():
    cache = _cache()
    tracker = PluginHealthTracker(cache)
    tracker.set_degraded("p", "x")
    writes_after_first = cache.set.call_count
    assert writes_after_first >= 1
    tracker.set_degraded("p", "x")  # unchanged → no extra write
    assert cache.set.call_count == writes_after_first


def test_default_summary_has_degraded_fields():
    tracker = PluginHealthTracker(_cache())
    summary = tracker.get_health_summary("never-seen")
    assert summary["degraded"] is False
    assert summary["degraded_reason"] is None


def test_force_reload_refreshes_stale_in_memory_snapshot():
    """A long-lived reader (e.g. the web process) must not be pinned to the
    first snapshot: force_reload re-reads persisted state and bypasses the
    cache manager's memory tier so cross-process updates are visible."""
    cache = _cache()
    tracker = PluginHealthTracker(cache)

    # First read snapshots an empty (healthy) state into the in-memory copy.
    assert tracker.get_health_summary("p")["consecutive_failures"] == 0

    # The display service later persists a failing/open state.
    cache.get.return_value = {
        "consecutive_failures": 5,
        "circuit_state": "open",
        "total_failures": 5,
        "total_successes": 0,
    }

    # A plain read is still pinned to the stale snapshot...
    assert tracker.get_health_summary("p")["consecutive_failures"] == 0

    # ...but force_reload observes the new persisted state.
    fresh = tracker.get_health_summary("p", force_reload=True)
    assert fresh["consecutive_failures"] == 5
    assert fresh["circuit_state"] == "open"

    # and it asked the cache to bypass the in-memory tier (memory_ttl=0).
    assert any(c.kwargs.get("memory_ttl") == 0 for c in cache.get.call_args_list)
