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
