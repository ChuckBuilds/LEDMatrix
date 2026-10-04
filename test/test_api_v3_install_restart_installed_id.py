"""POST /plugins/install asks for a restart by the id the plugin installed as.

A store install needs a display restart when config.json already enables the
plugin (a reinstall, or a config carried over): the display loads a plugin
when its ``enabled`` flag changes, and this flag did not. The route read the
flag under the registry id it was given. An aliased entry installs under
another id -- ``weather`` installs a directory whose manifest declares
``ledmatrix-weather``, and its config section is ``ledmatrix-weather`` -- so
reinstalling an enabled Weather never reported that a restart was needed,
and the display kept running the old copy.
"""

import json
from unittest.mock import MagicMock

import pytest

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401

INSTALL = "/api/v3/plugins/install"


@pytest.fixture
def store(api_v3_module, tmp_path):
    """The store installs registry entry ``weather`` as ``installed_id``."""
    manager = api_v3_module.api_v3.plugin_store_manager
    manager.install_plugin.return_value = True
    manager.get_registry_info.return_value = None
    manager._find_plugin_path.return_value = None

    def installs_as(installed_id):
        path = tmp_path / installed_id
        path.mkdir()
        (path / "manifest.json").write_text(json.dumps({"id": installed_id}),
                                            encoding="utf-8")
        manager._find_plugin_path.side_effect = (
            lambda pid: path if pid == "weather" else None)

    manager.installs_as = installs_as
    return manager


@pytest.fixture
def config(api_v3_module):
    """config.json with an ``enabled`` flag for each plugin id given."""
    def sections(enabled):
        api_v3_module.api_v3.config_manager.load_config.return_value = {
            plugin_id: {"enabled": flag} for plugin_id, flag in enabled.items()}
    return sections


@pytest.fixture
def queued(api_v3_module):
    queue = MagicMock()

    def enqueue(operation_type, plugin_id, operation_callback=None):
        queue.callback_result = operation_callback(MagicMock())
        return "op-1"

    queue.enqueue_operation.side_effect = enqueue
    api_v3_module.api_v3.operation_queue = queue
    return queue


def _direct(client):
    return client.post(INSTALL, json={"plugin_id": "weather"}).get_json()


def _queued(client, queue):
    client.post(INSTALL, json={"plugin_id": "weather"})
    return queue.callback_result


class TestDirectInstall:
    def test_an_aliased_install_enabled_under_its_installed_id_asks_for_a_restart(
            self, api_v3_client, store, config):
        store.installs_as("ledmatrix-weather")
        config({"ledmatrix-weather": True})
        body = _direct(api_v3_client)
        assert body["status"] == "success"
        assert body["restart_required"] is True
        assert body["restart_message"]

    def test_an_enabled_section_under_the_registry_id_alone_does_not(
            self, api_v3_client, store, config):
        """The display knows the plugin as ledmatrix-weather; nothing runs
        under a section called weather."""
        store.installs_as("ledmatrix-weather")
        config({"weather": True})
        assert _direct(api_v3_client)["restart_required"] is False

    def test_an_aliased_install_that_is_not_enabled_needs_no_restart(
            self, api_v3_client, store, config):
        store.installs_as("ledmatrix-weather")
        config({"ledmatrix-weather": False})
        assert _direct(api_v3_client)["restart_required"] is False

    def test_an_install_under_its_own_id_is_unchanged(self, api_v3_client, store, config):
        store.installs_as("weather")
        config({"weather": True})
        assert _direct(api_v3_client)["restart_required"] is True

    def test_an_install_that_cannot_be_found_uses_the_requested_id(
            self, api_v3_client, store, config):
        config({"weather": True})
        assert _direct(api_v3_client)["restart_required"] is True


class TestQueuedInstall:
    def test_an_aliased_install_enabled_under_its_installed_id_asks_for_a_restart(
            self, api_v3_client, store, config, queued):
        store.installs_as("ledmatrix-weather")
        config({"ledmatrix-weather": True})
        result = _queued(api_v3_client, queued)
        assert result["success"] is True
        assert result["restart_required"] is True
        assert result["restart_message"]

    def test_an_enabled_section_under_the_registry_id_alone_does_not(
            self, api_v3_client, store, config, queued):
        store.installs_as("ledmatrix-weather")
        config({"weather": True})
        assert _queued(api_v3_client, queued)["restart_required"] is False
