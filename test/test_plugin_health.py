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


# --- persisted state that does not match the current schema -------------------
#
# A record on disk can be missing fields the callers index directly: a partial
# write, a restored backup, or a state written by an older schema. Returning it
# verbatim raises KeyError inside record_success / record_failure, which takes
# the display down in a restart loop that survives reboots, because the bad
# entry is on disk and gets read again on the way back up. Observed in the wild
# as `plugin clock-simple operation failed: 'circuit_state'`, repeating ~50x a
# minute with the panel frozen.

_INDEXED_FIELDS = (
    "consecutive_failures", "total_failures", "total_successes",
    "last_success_time", "last_failure_time", "circuit_state",
    "circuit_opened_time", "half_open_start_time", "last_error",
)


def _tracker_reading(persisted):
    cache = _cache()
    cache.get.return_value = persisted
    return PluginHealthTracker(cache)


def test_partial_state_is_completed_not_returned_raw():
    """The shape seen in the wild: one field, everything else absent."""
    state = _tracker_reading({"circuit_state": "closed"}).get_health_state("p")
    for field in _INDEXED_FIELDS:
        assert field in state, f"{field} missing; callers index it directly"


def test_repair_keeps_real_failure_history():
    """A record with genuine counts must not be reset to healthy just because
    an optional field is absent -- that would clear a tripped breaker."""
    state = _tracker_reading({
        "consecutive_failures": 5,
        "total_failures": 5,
        "circuit_state": "open",
    }).get_health_state("p")
    assert state["consecutive_failures"] == 5
    assert state["total_failures"] == 5
    assert state["circuit_state"] == "open"


def test_wrong_types_fall_back_per_field():
    """A counter persisted as a string would pass a membership check and then
    fail on the first += 1; an unknown circuit_state would take a branch the
    breaker has no handling for."""
    state = _tracker_reading({
        "consecutive_failures": "3",
        "circuit_state": "melted",
        "total_failures": 7,
    }).get_health_state("p")
    assert state["consecutive_failures"] == 0
    assert state["circuit_state"] == CircuitState.CLOSED.value
    assert state["total_failures"] == 7, "valid neighbours must survive"


def test_newer_fields_are_carried_through():
    """degraded/degraded_reason are read with .get() and are not part of the
    indexed set; repairing must not drop them."""
    state = _tracker_reading({
        "circuit_state": "closed", "degraded": True, "degraded_reason": "x",
    }).get_health_state("p")
    assert state["degraded"] is True
    assert state["degraded_reason"] == "x"


def test_recording_against_a_repaired_state_does_not_raise():
    """The actual failure: record_failure indexing a field that was not there."""
    tracker = _tracker_reading({"circuit_state": "closed"})
    tracker.record_failure("p", Exception("boom"))
    tracker.record_success("p")
