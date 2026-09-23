"""Plugin state transitions are counted, not stored.

`PluginStateManager` used to keep every transition in a per-plugin history on
the hot scheduling path (RUNNING on reserve, ENABLED on finish), but nothing
ever read the entries -- `get_state_info()` only reported how many there were.
It now keeps just that lifetime count, which `state_history_count` surfaces
through the web API.
"""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.plugin_system.plugin_state import (  # noqa: E402
    PluginState,
    PluginStateManager,
)


def _cycle_updates(manager, plugin_id, cycles):
    """Drive the real scheduling path: RUNNING on reserve, ENABLED on finish."""
    for _ in range(cycles):
        manager.set_state(plugin_id, PluginState.RUNNING)
        manager.set_state(plugin_id, PluginState.ENABLED)


def test_state_history_count_reports_lifetime_total():
    """`get_state_info()['state_history_count']` counts every transition."""
    manager = PluginStateManager()
    manager.set_state("clock", PluginState.ENABLED)

    cycles = 4000
    _cycle_updates(manager, "clock", cycles)

    info = manager.get_state_info("clock")
    assert info["state_history_count"] == 1 + cycles * 2


def test_error_transitions_are_counted():
    """set_state_with_error() is a transition too."""
    manager = PluginStateManager()
    manager.set_state("clock", PluginState.ENABLED)
    manager.set_state_with_error(
        "clock", PluginState.ENABLED, {"reason": "update timeout"}
    )

    info = manager.get_state_info("clock")
    assert info["state_history_count"] == 2
    assert info["error_info"] == {"reason": "update timeout"}


def test_count_is_isolated_per_plugin():
    manager = PluginStateManager()
    manager.set_state("clock", PluginState.ENABLED)
    _cycle_updates(manager, "clock", 50)
    manager.set_state("weather", PluginState.ENABLED)

    assert manager.get_state_info("clock")["state_history_count"] == 101
    assert manager.get_state_info("weather")["state_history_count"] == 1


def test_clear_state_drops_the_count():
    """Unloading a plugin still releases everything it accumulated."""
    manager = PluginStateManager()
    manager.set_state("clock", PluginState.ENABLED)
    _cycle_updates(manager, "clock", 10)

    manager.clear_state("clock")

    info = manager.get_state_info("clock")
    assert info["state"] == PluginState.UNLOADED.value
    assert info["state_history_count"] == 0


def test_get_state_info_is_a_consistent_snapshot():
    """An unload running concurrently must not be observed half-done.

    Each field used to be read under its own lock, so clear_state() could
    interleave: 'state' read before the removal, 'state_history_count' after,
    handing a caller a plugin that is ENABLED with zero transitions. The whole
    payload is now built in one critical section.
    """
    m = PluginStateManager()
    _cycle_updates(m, "clock", 50)

    inconsistent = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            info = m.get_state_info("clock")
            # Either fully present or fully cleared -- never a live state with
            # a wiped count.
            if info["state"] != PluginState.UNLOADED.value and \
                    info["state_history_count"] == 0:
                inconsistent.append(info)
                return

    def clearer():
        for _ in range(200):
            for _ in range(20):
                m.set_state("clock", PluginState.ENABLED)
            m.clear_state("clock")

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    clearer()
    stop.set()
    t.join(timeout=5)

    assert not inconsistent, f"observed a torn snapshot: {inconsistent[:1]}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
