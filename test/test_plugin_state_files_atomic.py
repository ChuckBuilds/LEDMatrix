"""The operation history file is replaced atomically.

It was written with a plain ``open(path, 'w')`` + ``json.dump`` outside its
lock. The open truncates first, so a value json can't encode (or a crash, or
a second Flask thread saving at the same moment) left a partial file, and the
next load dropped every saved record. It now serialises first and goes
through a temp file + rename while holding the lock. (plugin_state.json had
the same fix; it is retired now -- nothing writes it.)
"""

import json

from src.plugin_system.operation_history import OperationHistory


def test_history_file_survives_a_failed_save(tmp_path):
    history_file = tmp_path / "operation_history.json"
    history = OperationHistory(history_file=str(history_file))
    history.record_operation("install", plugin_id="clock")
    before = json.loads(history_file.read_text())

    history.record_operation("update", plugin_id="clock", details={"bad": object()})

    assert json.loads(history_file.read_text()) == before
    assert [p.name for p in tmp_path.iterdir()] == ["operation_history.json"]
