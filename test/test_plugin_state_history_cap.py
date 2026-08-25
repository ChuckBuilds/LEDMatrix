"""Plugin state history must not grow without bound.

`PluginStateManager` recorded every state transition in a per-plugin list and
never trimmed it. The only code that removed entries was `clear_state()`, called
solely from `PluginManager.unload_plugin()`, so a plugin that stays loaded --
i.e. normal operation -- never released a single entry.

The list is written on the hot scheduling path. Every update cycle appends
twice: `_reserve_for_update()` sets RUNNING and `_finish()` sets ENABLED back
again. At the default 60-second update interval that is 2,880 entries per
plugin per day, and nothing ever reads the entries -- `get_state_info()` only
takes their `len()`. It is pure dead weight.

Measured against the unpatched class, ten plugins on a 60s interval retain
864,010 transitions after thirty simulated days, for 231 MB of heap. On a 1 GB
Pi that is fatal on its own, and the failure is not a clean OOM: once
MemAvailable falls far enough, fork() starts returning ENOMEM, so sshd accepts
connections and closes them before its banner while the kernel still answers
pings. The board looks like a hardware fault and needs a power cycle.

These tests pin the cap, the retention order, and the one piece of behaviour the
cap must not change: `state_history_count` is surfaced through the web API, so
it has to keep reporting the lifetime total rather than plateauing at the cap.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.plugin_system.plugin_state import (  # noqa: E402
    MAX_STATE_HISTORY_PER_PLUGIN,
    PluginState,
    PluginStateManager,
)


def _cycle_updates(manager, plugin_id, cycles):
    """Drive the real scheduling path: RUNNING on reserve, ENABLED on finish."""
    for _ in range(cycles):
        manager.set_state(plugin_id, PluginState.RUNNING)
        manager.set_state(plugin_id, PluginState.ENABLED)


def test_state_history_is_capped():
    """A day of updates must not retain a day of transitions."""
    manager = PluginStateManager()
    manager.set_state("clock", PluginState.ENABLED)

    # One simulated day at the default 60s update interval.
    _cycle_updates(manager, "clock", 1440)

    history = manager.get_state_history("clock")
    assert len(history) <= MAX_STATE_HISTORY_PER_PLUGIN, (
        f"history grew to {len(history)} entries; it is never trimmed"
    )


def test_state_history_keeps_the_most_recent_transitions():
    """Trimming drops the oldest entries, not the newest."""
    manager = PluginStateManager()
    manager.set_state("clock", PluginState.ENABLED)
    _cycle_updates(manager, "clock", MAX_STATE_HISTORY_PER_PLUGIN)

    history = manager.get_state_history("clock")

    # The scheduling cycle ends on ENABLED, so the newest entry is the
    # RUNNING -> ENABLED half of the last cycle.
    assert history[-1]["from"] == PluginState.RUNNING.value
    assert history[-1]["to"] == PluginState.ENABLED.value

    # And the very first ENABLED transition has aged out.
    assert history[0]["from"] != PluginState.UNLOADED.value


def test_state_history_count_reports_lifetime_total():
    """The count exposed through the API must not plateau at the cap.

    `get_state_info()['state_history_count']` is surfaced by the web UI. Capping
    the retained list must not turn it into "entries we happen to still hold".
    """
    manager = PluginStateManager()
    manager.set_state("clock", PluginState.ENABLED)
    total = 1

    cycles = MAX_STATE_HISTORY_PER_PLUGIN * 2
    _cycle_updates(manager, "clock", cycles)
    total += cycles * 2

    info = manager.get_state_info("clock")
    assert info["state_history_count"] == total
    assert len(manager.get_state_history("clock")) <= MAX_STATE_HISTORY_PER_PLUGIN


def test_error_transitions_are_capped_too():
    """set_state_with_error() appends to the same list and needs the same cap."""
    manager = PluginStateManager()
    manager.set_state("clock", PluginState.ENABLED)

    for _ in range(MAX_STATE_HISTORY_PER_PLUGIN * 2):
        manager.set_state_with_error(
            "clock",
            PluginState.ENABLED,
            {"reason": "update timeout"},
            error=RuntimeError("boom"),
        )

    assert len(manager.get_state_history("clock")) <= MAX_STATE_HISTORY_PER_PLUGIN


def test_history_is_isolated_per_plugin():
    """The cap is per plugin, not shared across the manager."""
    manager = PluginStateManager()
    for plugin_id in ("clock", "weather"):
        manager.set_state(plugin_id, PluginState.ENABLED)
        _cycle_updates(manager, plugin_id, 50)

    assert len(manager.get_state_history("clock")) == 101
    assert len(manager.get_state_history("weather")) == 101


def test_get_state_history_returns_a_copy():
    """Callers must not be able to mutate the manager's internal history."""
    manager = PluginStateManager()
    manager.set_state("clock", PluginState.ENABLED)

    history = manager.get_state_history("clock")
    history.clear()

    assert len(manager.get_state_history("clock")) == 1


def test_clear_state_drops_history():
    """Unloading a plugin still releases everything it accumulated."""
    manager = PluginStateManager()
    manager.set_state("clock", PluginState.ENABLED)
    _cycle_updates(manager, "clock", 10)

    manager.clear_state("clock")

    assert manager.get_state_history("clock") == []
    assert manager.get_state_info("clock")["state_history_count"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
