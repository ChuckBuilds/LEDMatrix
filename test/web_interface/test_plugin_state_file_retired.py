"""data/plugin_state.json is retired; /plugins/state and reconciliation read
config + disk (desired) and the display's runtime snapshot (observed).

The file held, per plugin, an enabled flag (a copy of config.json's), a
version (a copy of the manifest's, when anything set it at all), a status
derived from those two, and install/update timestamps that only
GET /api/v3/plugins/state ever returned -- the operation history records the
same events. Nothing in it was needed that cannot be derived, so it is not
migrated: nothing reads or writes it any more, and an existing file is left
in place, unread. These tests hold that line and check the replacements.
"""
import ast
import json
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.plugin_system.operation_history import OperationRecord
from src.plugin_system.plugin_runtime import SNAPSHOT_SCHEMA, view_from_snapshot
from src.plugin_system.state_reconciliation import StateReconciliation
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401

REPO = Path(__file__).resolve().parents[2]


def _code_strings(path):
    """String constants in a module, docstrings excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(node.value) for node in ast.walk(tree)
                  if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def test_no_code_reads_or_writes_the_state_file():
    offenders = []
    for root in ("src", "web_interface", "scripts"):
        for path in (REPO / root).rglob("*.py"):
            if any("plugin_state.json" in s for s in _code_strings(path)):
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_the_web_side_state_manager_is_gone():
    import importlib.util
    assert importlib.util.find_spec("src.plugin_system.state_manager") is None
    from web_interface.blueprints.api_v3 import api_v3
    import web_interface.app as app_module
    assert not hasattr(app_module, "plugin_state_manager")
    assert getattr(api_v3, "plugin_state_manager", None) is None


def _install(plugins_dir, plugin_id, version):
    d = plugins_dir / plugin_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"id": plugin_id, "version": version}),
                                     encoding="utf-8")


def _live(plugins):
    now = time.time()
    return view_from_snapshot({"schema": SNAPSHOT_SCHEMA, "running": True,
                               "published_at": now, "plugins": plugins}, now=now)


def test_an_existing_state_file_changes_nothing(tmp_path):
    """A device upgraded with a plugin_state.json that disagrees with config
    and disk: reconciliation answers exactly as on a device without one."""
    plugins_dir = tmp_path / "plugin-repos"
    _install(plugins_dir, "clock", "1.0.0")
    config = MagicMock()
    config.load_config.return_value = {"clock": {"enabled": True}}
    config.get_secrets_path.return_value = str(tmp_path / "none.json")

    def run():
        r = StateReconciliation(config_manager=config, plugins_dir=plugins_dir)
        result = r.reconcile_state()
        return ([i.inconsistency_type for i in result.inconsistencies_found],
                r.plugin_states())

    without = run()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "plugin_state.json").write_text(json.dumps({
        "version": 1, "states": {
            "clock": {"plugin_id": "clock", "status": "disabled", "enabled": False,
                      "version": "0.0.1"},
            "gone": {"plugin_id": "gone", "status": "installed", "enabled": True}}}),
        encoding="utf-8")

    assert run() == without
    assert without[1]["clock"]["enabled"] is True
    assert "gone" not in without[1]


@pytest.fixture
def state_api(api_v3_module, api_v3_client, tmp_path, monkeypatch):  # noqa: F811
    api = api_v3_module.api_v3
    plugins_dir = tmp_path / "plugin-repos"
    _install(plugins_dir, "clock", "1.1.0")
    _install(plugins_dir, "weather", "2.0.0")
    api.plugin_catalog.plugins_dir = str(plugins_dir)
    api.config_manager.load_config = MagicMock(return_value={
        "clock": {"enabled": True}, "weather": {"enabled": False},
        "ghost": {"enabled": True}})
    api.config_manager.get_secrets_path = MagicMock(return_value=str(tmp_path / "s.json"))
    api.operation_history.get_history = MagicMock(return_value=[
        OperationRecord("3", "update", "clock", datetime(2026, 9, 3), "success"),
        OperationRecord("2", "update", "clock", datetime(2026, 9, 2), "failed"),
        OperationRecord("1", "install", "clock", datetime(2026, 9, 1), "success"),
    ])
    observed = {"view": _live({"clock": {"loaded": True, "state": "enabled", "error": None,
                                         "version": "1.0.0", "loaded_at": 50.0}})}
    import web_interface.blueprints.api_v3 as pkg
    monkeypatch.setattr(pkg, "_plugin_runtime_view", lambda: observed["view"])
    return api_v3_client, observed


class TestPluginStateRoute:
    def test_every_plugin_desired_and_observed(self, state_api):
        client, _ = state_api
        body = client.get("/api/v3/plugins/state").get_json()

        assert body["status"] == "success"
        assert body["runtime"]["status"] == "live"
        data = body["data"]
        assert set(data) == {"clock", "weather", "ghost"}
        clock = data["clock"]
        assert clock["status"] == "enabled"
        assert (clock["installed"], clock["enabled"], clock["version"]) == (True, True, "1.1.0")
        assert (clock["loaded"], clock["loaded_version"]) == (True, "1.0.0")
        assert clock["installed_at"] == "2026-09-01T00:00:00"
        assert clock["last_updated"] == "2026-09-03T00:00:00"
        assert data["weather"]["status"] == "disabled"
        assert data["weather"]["loaded"] is False
        assert data["weather"]["installed_at"] is None
        assert data["ghost"]["status"] == "unknown"  # configured, not installed

    def test_one_plugin(self, state_api):
        client, _ = state_api
        body = client.get("/api/v3/plugins/state?plugin_id=clock").get_json()
        assert body["data"]["plugin_id"] == "clock"
        response = client.get("/api/v3/plugins/state?plugin_id=nope")
        assert response.status_code == 404

    def test_display_not_running_leaves_observed_fields_null(self, state_api):
        client, observed = state_api
        observed["view"] = view_from_snapshot({"schema": SNAPSHOT_SCHEMA, "running": False,
                                               "published_at": time.time()})
        body = client.get("/api/v3/plugins/state").get_json()
        assert body["runtime"]["status"] == "stopped"
        assert body["data"]["clock"]["loaded"] is None
        assert body["data"]["clock"]["enabled"] is True

    def test_reconcile_reports_what_the_display_runs(self, state_api):
        client, _ = state_api
        body = client.post("/api/v3/plugins/state/reconcile", json={}).get_json()
        found = {(i["plugin_id"], i["type"], i["fix_action"])
                 for i in body["data"]["inconsistencies"]}
        assert ("clock", "plugin_version_mismatch", "no_action") in found
        assert ("ghost", "plugin_missing_on_disk", "manual_fix_required") in found
        # Reported only; nothing the user must fix beyond the missing plugin.
        assert [i["plugin_id"] for i in body["data"]["manual_fix_required"]] == ["ghost"]
