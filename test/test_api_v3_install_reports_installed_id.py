"""POST /plugins/install says which id the plugin was installed as.

A registry entry can install under another id: `weather` (aliases
`ledmatrix-weather`) installs a directory whose manifest declares
`ledmatrix-weather`, and that is the id the plugin list, the plugin's config
section and /plugins/toggle know it by. The store's Install button enabled
the new plugin by the registry id, which /plugins/toggle answered with 404
"Plugin not found", so Weather, Music, Stocks and Leaderboard installed
disabled behind an "enabling it failed" warning.

The answer -- the queued operation's result, or the direct response --
carries `plugin_id`: the id the installed manifest declares, found the way
the store's update and uninstall find an install.
"""

import json
from unittest.mock import MagicMock

import pytest

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401

INSTALL = "/api/v3/plugins/install"


@pytest.fixture
def store(api_v3_module, tmp_path):
    manager = api_v3_module.api_v3.plugin_store_manager
    manager.install_plugin.return_value = True
    manager.get_registry_info.return_value = None
    manager._find_plugin_path.return_value = None

    def installed_as(directory, manifest):
        path = tmp_path / directory
        path.mkdir()
        (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        manager._find_plugin_path.side_effect = (
            lambda pid: path if pid == "weather" else None)
        return path

    manager.installed_as = installed_as
    return manager


@pytest.fixture
def queued(api_v3_module):
    queue = MagicMock()

    def enqueue(operation_type, plugin_id, operation_callback=None):
        queue.callback_result = operation_callback(MagicMock())
        return "op-1"

    queue.enqueue_operation.side_effect = enqueue
    api_v3_module.api_v3.operation_queue = queue
    return queue


class TestDirectInstall:
    def test_an_aliased_entry_reports_the_manifest_id(self, api_v3_client, store):
        store.installed_as("ledmatrix-weather", {"id": "ledmatrix-weather"})
        body = api_v3_client.post(INSTALL, json={"plugin_id": "weather"}).get_json()
        assert body["status"] == "success"
        assert body["plugin_id"] == "ledmatrix-weather"
        store._find_plugin_path.assert_called_with("weather")

    def test_an_entry_installed_under_its_own_id_reports_that(self, api_v3_client, store):
        store.installed_as("weather", {"id": "weather"})
        body = api_v3_client.post(INSTALL, json={"plugin_id": "weather"}).get_json()
        assert body["plugin_id"] == "weather"

    def test_an_install_that_cannot_be_found_reports_the_requested_id(self, api_v3_client, store):
        body = api_v3_client.post(INSTALL, json={"plugin_id": "weather"}).get_json()
        assert body["status"] == "success"
        assert body["plugin_id"] == "weather"

    def test_a_manifest_id_that_is_not_a_plain_name_is_not_passed_on(self, api_v3_client, store):
        store.installed_as("ledmatrix-weather", {"id": "../elsewhere"})
        body = api_v3_client.post(INSTALL, json={"plugin_id": "weather"}).get_json()
        assert body["plugin_id"] == "weather"

    def test_an_unreadable_manifest_reports_the_requested_id(self, api_v3_client, store):
        path = store.installed_as("ledmatrix-weather", {})
        (path / "manifest.json").write_text("[not json", encoding="utf-8")
        body = api_v3_client.post(INSTALL, json={"plugin_id": "weather"}).get_json()
        assert body["plugin_id"] == "weather"

    def test_the_restart_fields_are_still_sent(self, api_v3_client, store):
        store.installed_as("ledmatrix-weather", {"id": "ledmatrix-weather"})
        body = api_v3_client.post(INSTALL, json={"plugin_id": "weather"}).get_json()
        assert "restart_required" in body


class TestQueuedInstall:
    def test_the_operation_result_names_the_manifest_id(self, api_v3_client, store, queued):
        store.installed_as("ledmatrix-weather", {"id": "ledmatrix-weather"})
        body = api_v3_client.post(INSTALL, json={"plugin_id": "weather"}).get_json()
        assert body["data"]["operation_id"] == "op-1"
        assert queued.callback_result["success"] is True
        assert queued.callback_result["plugin_id"] == "ledmatrix-weather"

    def test_an_install_that_cannot_be_found_names_the_requested_id(
            self, api_v3_client, store, queued):
        api_v3_client.post(INSTALL, json={"plugin_id": "weather"})
        assert queued.callback_result["plugin_id"] == "weather"
