"""plugin_state.json and the operation history file are replaced atomically.

Both were written with a plain ``open(path, 'w')`` + ``json.dump`` outside
their lock. The open truncates first, so a value json can't encode (or a
crash, or a second Flask thread saving at the same moment) left a partial
file, and the next load dropped every saved state. They now serialise first
and go through a temp file + rename while holding the lock.
"""

import json
import threading

from src.plugin_system.operation_history import OperationHistory
from src.plugin_system.state_manager import PluginStateManager


def test_state_file_survives_a_failed_save(tmp_path):
    state_file = tmp_path / "plugin_state.json"
    mgr = PluginStateManager(state_file=str(state_file))
    mgr.set_plugin_enabled("clock", True)
    before = json.loads(state_file.read_text())

    # Not JSON-serialisable: the save fails (and is logged, not raised).
    mgr.update_plugin_state("clock", {"metadata": {"bad": object()}})

    assert json.loads(state_file.read_text()) == before
    assert [p.name for p in tmp_path.iterdir()] == ["plugin_state.json"]


def test_state_file_is_valid_after_concurrent_saves(tmp_path):
    state_file = tmp_path / "plugin_state.json"
    mgr = PluginStateManager(state_file=str(state_file))

    def worker(n):
        for i in range(15):
            mgr.set_plugin_enabled(f"plugin-{n}", i % 2 == 0)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = json.loads(state_file.read_text())
    assert set(data["states"]) == {f"plugin-{n}" for n in range(6)}


def test_history_file_survives_a_failed_save(tmp_path):
    history_file = tmp_path / "operation_history.json"
    history = OperationHistory(history_file=str(history_file))
    history.record_operation("install", plugin_id="clock")
    before = json.loads(history_file.read_text())

    history.record_operation("update", plugin_id="clock", details={"bad": object()})

    assert json.loads(history_file.read_text()) == before
    assert [p.name for p in tmp_path.iterdir()] == ["operation_history.json"]
