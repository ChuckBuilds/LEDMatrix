"""Web routes find a plugin through the plugin manager's resolver.

Several routes built ``plugins_dir/<id>`` themselves. A plugin whose
directory is ``ledmatrix-<id>`` (the store's repo naming), or whose directory
name is not its manifest id, is not there, so those routes silently worked
on nothing: the installed list never refreshed its manifest or read its git
info, the recorded version was '', the update route compared manifests and
commits of a directory that does not exist, and the plugin pages 404'd or
rendered no schema. ``_plugin_directory()`` / ``get_plugin_directory()``
(src/plugin_system/plugin_dirs.py) is the one answer to "where is plugin X".
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.plugin_system.plugin_dirs import resolve_plugin_dir  # noqa: E402
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


def _resolver(plugins_dir):
    """What PluginManager.get_plugin_directory does for an id discovery has
    not mapped: <id>, then ledmatrix-<id>, in plugins_dir."""
    def get_plugin_directory(plugin_id):
        found = resolve_plugin_dir(plugin_id, [plugins_dir], prefix=True,
                                   by_manifest=False)
        return str(found) if found else None
    return get_plugin_directory


@pytest.fixture
def prefixed_plugin(tmp_path, api_v3_module):
    """A 'demo' plugin installed as plugins_dir/ledmatrix-demo."""
    plugins_dir = tmp_path / "plugin-repos"
    plugin_dir = plugins_dir / "ledmatrix-demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(json.dumps({
        "id": "demo", "name": "Demo", "version": "2.0.0",
        "description": "fresh from disk", "last_updated": "2026-09-01",
    }), encoding="utf-8")

    api = api_v3_module.api_v3
    api.plugin_catalog.plugins_dir = str(plugins_dir)
    api.plugin_catalog.get_plugin_directory = MagicMock(side_effect=_resolver(plugins_dir))
    api.plugin_store_manager.plugins_dir = str(plugins_dir)
    return plugin_dir


class TestInstalledList:
    def test_the_manifest_is_refreshed_from_the_prefixed_directory(
            self, api_v3_client, api_v3_module, prefixed_plugin):
        api = api_v3_module.api_v3
        info = {"id": "demo", "name": "Demo", "version": "1.0.0",
                "description": "stale cached copy", "loaded": False}
        api.plugin_catalog.get_all_plugin_info = MagicMock(return_value=[info])
        api.plugin_store_manager.get_cached_registry_info = MagicMock(return_value=None)
        api.plugin_store_manager._get_local_git_info = MagicMock(return_value=None)
        api.config_manager.load_config = MagicMock(return_value={})

        response = api_v3_client.get("/api/v3/plugins/installed")

        assert response.status_code == 200
        [entry] = [p for p in response.get_json()["data"]["plugins"] if p["id"] == "demo"]
        assert entry["description"] == "fresh from disk"
        # And git info is read from the same directory.
        api.plugin_store_manager._get_local_git_info.assert_called_once_with(prefixed_plugin)


class TestRecordedVersion:
    def test_the_version_is_read_from_the_prefixed_directory(
            self, api_v3_module, prefixed_plugin):
        assert api_v3_module._get_plugin_version("demo") == "2.0.0"

    def test_an_unsafe_id_is_still_refused(self, api_v3_module, prefixed_plugin):
        assert api_v3_module._get_plugin_version("../ledmatrix-demo") == ""


class TestUpdateRoute:
    def _update(self, client, api):
        api.plugin_store_manager.get_plugin_info = MagicMock(return_value=None)
        api.plugin_store_manager.update_plugin = MagicMock(return_value=True)
        api.plugin_store_manager._get_local_git_info = MagicMock(return_value=None)
        api.schema_manager = None
        api.operation_history = None
        return client.post("/api/v3/plugins/update", json={"plugin_id": "demo"})

    def test_it_works_on_the_prefixed_directory(
            self, api_v3_client, api_v3_module, prefixed_plugin):
        api = api_v3_module.api_v3
        response = self._update(api_v3_client, api)

        assert response.status_code == 200, response.get_json()
        # The manifest it reports from is the plugin's, not a missing one.
        assert response.get_json()["data"]["last_updated"] == "2026-09-01"
        called_with = {c.args[0] for c in api.plugin_store_manager._get_local_git_info.call_args_list}
        assert called_with == {prefixed_plugin}

    def test_git_info_is_read_once_before_and_once_after(
            self, api_v3_client, api_v3_module, prefixed_plugin):
        # A third read existed only to feed a debug log line. (A plain
        # plugins_dir/demo, so the old code's existence check let it run.)
        (prefixed_plugin.parent / "demo").mkdir()
        api = api_v3_module.api_v3
        self._update(api_v3_client, api)
        assert api.plugin_store_manager._get_local_git_info.call_count == 2

    def test_a_plugin_that_is_not_installed_touches_no_path(
            self, api_v3_client, api_v3_module, prefixed_plugin):
        # The directory is taken from a listing of plugins_dir, so an id
        # with nothing by that name installed never becomes a path at all.
        api = api_v3_module.api_v3
        api.plugin_store_manager.get_plugin_info = MagicMock(return_value=None)
        api.plugin_store_manager.update_plugin = MagicMock(return_value=False)
        api.plugin_store_manager._get_local_git_info = MagicMock(return_value=None)
        api.operation_history = None

        response = api_v3_client.post("/api/v3/plugins/update", json={"plugin_id": "ghost"})

        assert response.status_code >= 400
        assert "not found" in response.get_json()["message"]
        api.plugin_store_manager._get_local_git_info.assert_not_called()
        api.plugin_store_manager.update_plugin.assert_called_once_with("ghost")


@pytest.fixture
def pages(tmp_path, monkeypatch):
    from web_interface.blueprints import pages_v3 as module

    plugins_dir = tmp_path / "plugin-repos"
    plugins_dir.mkdir()
    plugin_manager = MagicMock()
    plugin_manager.plugins_dir = plugins_dir
    monkeypatch.setattr(module.pages_v3, "plugin_catalog", plugin_manager, raising=False)
    monkeypatch.setattr(module.pages_v3, "config_manager",
                        MagicMock(load_config=lambda: {}), raising=False)
    monkeypatch.setattr(module.pages_v3, "schema_manager", None, raising=False)
    monkeypatch.setattr(module.pages_v3, "plugin_store_manager", MagicMock(), raising=False)
    app = Flask(__name__, template_folder=str(
        Path(module.__file__).resolve().parents[1] / "templates"))
    app.config["TESTING"] = True
    app.register_blueprint(module.pages_v3)
    return module, plugins_dir, plugin_manager, app.test_client()


class TestPluginPages:
    def test_web_ui_is_served_from_the_directory_discovery_found(self, pages):
        # A directory name that is neither <id> nor ledmatrix-<id>: only the
        # plugin manager's discovery map knows it holds 'radar'.
        _, plugins_dir, plugin_manager, client = pages
        web_ui = plugins_dir / "Radar-Checkout" / "web_ui"
        web_ui.mkdir(parents=True)
        (web_ui / "panel.html").write_text("<p>radar panel</p>", encoding="utf-8")
        plugin_manager.get_plugin_directory.side_effect = (
            lambda pid: str(plugins_dir / "Radar-Checkout") if pid == "radar" else None)

        response = client.get("/plugin-ui/radar/web-ui/panel.html")

        assert response.status_code == 200
        assert "radar panel" in response.get_data(as_text=True)

    def test_the_config_form_reads_the_prefixed_directorys_schema(self, pages):
        _, plugins_dir, plugin_manager, client = pages
        plugin_dir = plugins_dir / "ledmatrix-weather"
        plugin_dir.mkdir()
        (plugin_dir / "config_schema.json").write_text(json.dumps({
            "type": "object",
            "properties": {"enabled": {"type": "boolean"},
                           "units": {"type": "string", "title": "Turn It On"}},
        }), encoding="utf-8")
        (plugin_dir / "manifest.json").write_text(
            json.dumps({"id": "weather", "name": "Weather"}), encoding="utf-8")
        plugin_manager.get_plugin_info.return_value = {"id": "weather", "name": "Weather"}
        plugin_manager.get_plugin_directory.side_effect = _resolver(plugins_dir)

        response = client.get("/partials/plugin-config/weather")

        # plugins_dir/weather has no schema, which rendered as a 500
        # "schema unavailable".
        assert response.status_code == 200, response.get_data(as_text=True)[:300]
        assert "Turn It On" in response.get_data(as_text=True)


class TestPartialsDoNoUnusedWork:
    def test_the_plugins_tab_reads_no_plugin_data(self, pages):
        # plugins.html takes no plugin list; plugins_manager.js fetches it.
        _, _, plugin_manager, client = pages
        response = client.get("/partials/plugins")
        assert response.status_code == 200
        plugin_manager.get_all_plugin_info.assert_not_called()
