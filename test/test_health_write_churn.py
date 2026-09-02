"""A healthy plugin must not rewrite its health record every cycle.

Every successful plugin update called record_success(), which persisted the
record unconditionally. In steady state the only fields that had changed were
total_successes and last_success_time -- a counter and a timestamp that
health_monitor reads for display and that nothing reads back after a restart.

Measured on a rig running 24 plugins: about 17 health-file rewrites a minute,
roughly 25,000 a day. Each is ~400 bytes, but they land on an SD card where
the unit of cost is an erase-block cycle, not the byte count, and where wear is
what eventually kills the card.

The circuit breaker still needs its own state to survive a restart, so the
write is kept for exactly the fields it is rebuilt from -- and a failure, a
circuit opening, or a recovery must still be written the moment it happens.
"""
import time

import copy

import pytest

from src.plugin_system.plugin_health import PluginHealthTracker, CircuitState


class _Cache:
    """Counts writes; serves back whatever was last written.

    Both directions deep-copy, so this behaves like a real cache that
    serialises through a file. Storing by reference let the tracker keep
    mutating the object already in the store, so a record could appear to
    have been persisted when no write ever happened -- which is precisely
    what test_durable_state_survives_a_restart is supposed to detect.
    """

    def __init__(self):
        self.store = {}
        self.writes = 0

    def set(self, key, data, ttl=None, **kwargs):
        self.writes += 1
        self.store[key] = copy.deepcopy(data)

    def get(self, key, max_age=None, memory_ttl=None, **kwargs):
        return copy.deepcopy(self.store.get(key))


@pytest.fixture
def tracker():
    cache = _Cache()
    t = PluginHealthTracker(cache_manager=cache)
    return t, cache


def test_steady_state_success_stops_writing(tracker):
    """The regression: 100 healthy cycles used to be 100 SD writes."""
    t, cache = tracker
    t.record_success("weather")
    first = cache.writes
    for _ in range(100):
        t.record_success("weather")
    assert cache.writes == first, (
        f"{cache.writes - first} redundant writes across 100 healthy cycles"
    )


def test_the_counters_are_still_accurate_in_memory(tracker):
    """Skipping the write must not skip the bookkeeping."""
    t, _ = tracker
    for _ in range(10):
        t.record_success("weather")
    state = t.get_health_state("weather")
    assert state["total_successes"] == 10
    assert state["last_success_time"] is not None
    assert state["last_success_time"] <= time.time()


def test_a_failure_is_written_immediately(tracker):
    t, cache = tracker
    t.record_success("weather")
    before = cache.writes
    t.record_failure("weather", RuntimeError("boom"))
    assert cache.writes > before, "a failure must reach disk"


def test_recovery_after_failure_is_written(tracker):
    """consecutive_failures returning to 0 is durable state changing."""
    t, cache = tracker
    t.record_failure("weather", RuntimeError("boom"))
    before = cache.writes
    t.record_success("weather")
    assert cache.writes > before, "recovery must reach disk"
    assert t.get_health_state("weather")["consecutive_failures"] == 0


def test_a_closing_circuit_is_written(tracker):
    """Success in half-open closes the circuit -- that must survive a restart."""
    t, cache = tracker
    state = t.get_health_state("weather")
    state["circuit_state"] = CircuitState.HALF_OPEN.value
    state["half_open_start_time"] = time.time()
    before = cache.writes
    t.record_success("weather")
    assert cache.writes > before, "a circuit transition must reach disk"
    assert t.get_health_state("weather")["circuit_state"] == CircuitState.CLOSED.value


def test_durable_state_survives_a_restart(tracker):
    """What is skipped must genuinely not matter to the breaker."""
    t, cache = tracker
    for _ in range(3):
        t.record_failure("weather", RuntimeError("boom"))
    for _ in range(50):
        t.record_success("weather")

    revived = PluginHealthTracker(cache_manager=cache)
    state = revived.get_health_state("weather")
    assert state["consecutive_failures"] == 0
    assert state["circuit_state"] == CircuitState.CLOSED.value
