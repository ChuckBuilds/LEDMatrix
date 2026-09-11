"""The reconciliation-status endpoint must re-check its stored verdict.

#557 added _drop_stale_reconciliation_findings() so a resolved condition stops
being reported: the verdict is a snapshot written once per run, and a run that
fails to apply a fix also declines to retry, so a device whose plugins were all
present in config kept being told for hours that four of them were missing.

That wiring had no test. Only the pure still_unresolved() helper in
src/plugin_system/ was covered, which lives outside web_interface and therefore
survived the api_v3 blueprint split untouched -- so when #553 moved this
endpoint into api_v3/plugins.py, losing the filter would have been completely
silent. This test exists so that cannot happen again.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web_interface.blueprints.api_v3 import api_v3  # noqa: E402

IN_CONFIG = "plugin_missing_in_config"
ON_DISK = "plugin_missing_on_disk"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """App whose status file, config and plugins dir are all under our control."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    plugins_dir = tmp_path / "plugin-repos"
    plugins_dir.mkdir()

    def _install(plugin_id):
        d = plugins_dir / plugin_id
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({"id": plugin_id}), encoding="utf-8")

    def _setup(verdict, config=None, installed=()):
        (tmp_path / "ledmatrix_reconciliation.json").write_text(
            json.dumps(verdict), encoding="utf-8")
        for pid in installed:
            _install(pid)
        api_v3.config_manager = MagicMock()
        api_v3.config_manager.load_config.return_value = dict(config or {})
        api_v3.plugin_manager = MagicMock()
        api_v3.plugin_manager.plugins_dir = str(plugins_dir)

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(api_v3, url_prefix="/api/v3")
        return app.test_client()

    return _setup


def _get(c):
    return c.get("/api/v3/plugins/reconciliation-status").get_json()["data"]


class TestStaleFindingsAreDroppedByTheEndpoint:
    def test_a_plugin_now_in_config_is_no_longer_reported(self, client):
        c = client({"done": True, "unresolved": [
            {"plugin_id": "football-scoreboard", "type": IN_CONFIG}]},
            config={"football-scoreboard": {"enabled": True}})
        # The regression: this kept being reported for hours after it resolved.
        assert _get(c)["unresolved"] == []

    def test_a_plugin_now_installed_is_no_longer_reported(self, client):
        c = client({"done": True, "unresolved": [
            {"plugin_id": "odds-ticker", "type": ON_DISK}]},
            installed=["odds-ticker"])
        assert _get(c)["unresolved"] == []

    def test_a_finding_that_still_holds_is_kept(self, client):
        c = client({"done": True, "unresolved": [
            {"plugin_id": "ghost-plugin", "type": ON_DISK}]})
        assert [e["plugin_id"] for e in _get(c)["unresolved"]] == ["ghost-plugin"]

    def test_the_reported_device_verdict_clears(self, client):
        """The five findings the live device served, against its real state."""
        installed = ["football-scoreboard", "ledmatrix-weather",
                     "odds-ticker", "starlark-apps"]
        c = client({"done": True, "unresolved": [
            {"plugin_id": p, "type": IN_CONFIG} for p in installed]},
            config={p: {"enabled": True} for p in installed},
            installed=installed)
        assert _get(c)["unresolved"] == []


class TestTheEndpointStaysRobust:
    def test_an_empty_verdict_is_passed_through(self, client):
        c = client({"done": True, "unresolved": []})
        assert _get(c) == {"done": True, "unresolved": []}

    def test_a_broken_config_manager_leaves_findings_untouched(self, client):
        """Best-effort: a stale warning beats a failed endpoint."""
        c = client({"done": True, "unresolved": [
            {"plugin_id": "football-scoreboard", "type": IN_CONFIG}]})
        api_v3.config_manager.load_config.side_effect = OSError("config unreadable")
        body = _get(c)
        assert [e["plugin_id"] for e in body["unresolved"]] == ["football-scoreboard"]

    def test_a_run_still_in_progress_is_reported_as_such(self, client):
        c = client({"done": False, "unresolved": []})
        assert _get(c)["done"] is False
