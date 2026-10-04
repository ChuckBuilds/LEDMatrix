"""A second install or uninstall while one is in progress is a 409, not a 500.

PluginOperationQueue refuses a second operation for a plugin that already
has one waiting or running (test_operation_queue_pending_and_trim.py), and
says so by raising ValueError. /plugins/install let that escape to the
blueprint's catch-all, so a double-clicked Install answered 500 "An error
occurred; see logs for details" while the first install carried on.
/plugins/uninstall caught it in its own catch-all: a 500 "Failed to
uninstall plugin", and an "uninstall failed" entry in the operation
history for an uninstall that never started.
"""

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

from src.plugin_system.operation_queue import PluginOperationQueue  # noqa: E402

INSTALL = "/api/v3/plugins/install"
UNINSTALL = "/api/v3/plugins/uninstall"


@pytest.fixture
def installing(api_v3_module, tmp_path):
    """A real queue with an install of "clock" running and held there."""
    queue = PluginOperationQueue(max_history=10)
    api_v3_module.api_v3.operation_queue = queue
    started, release = threading.Event(), threading.Event()

    def slow_install(plugin_id, branch=None):
        started.set()
        release.wait(10)
        return True

    store = api_v3_module.api_v3.plugin_store_manager
    store.install_plugin.side_effect = slow_install
    store.get_registry_info.return_value = None
    store.plugins_dir = str(tmp_path)
    api_v3_module.api_v3.plugin_catalog.get_plugin_directory.return_value = None
    yield {"queue": queue, "started": started, "store": store}
    release.set()
    queue.shutdown()


def _start_first_install(client, installing):
    response = client.post(INSTALL, json={"plugin_id": "clock"})
    assert response.status_code == 200, response.get_json()
    assert installing["started"].wait(5)


def _failed_history(api_v3_module):
    return [c for c in api_v3_module.api_v3.operation_history.record_operation.call_args_list
            if c.kwargs.get("status") == "failed"]


def test_a_second_install_click_is_a_conflict(api_v3_client, api_v3_module, installing):
    _start_first_install(api_v3_client, installing)
    response = api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert body["status"] == "error"
    assert body["error_code"] == "PLUGIN_OPERATION_CONFLICT"
    assert "clock" in body["message"]
    assert installing["store"].install_plugin.call_count == 1
    assert _failed_history(api_v3_module) == []


def test_an_uninstall_during_the_install_is_a_conflict(api_v3_client, api_v3_module,
                                                        installing):
    _start_first_install(api_v3_client, installing)
    response = api_v3_client.post(UNINSTALL, json={"plugin_id": "clock"})
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["error_code"] == "PLUGIN_OPERATION_CONFLICT"
    assert _failed_history(api_v3_module) == [], (
        "an uninstall that never started was recorded as failed")
    api_v3_module.api_v3.plugin_store_manager.uninstall_plugin.assert_not_called()


def test_another_plugin_is_still_queued(api_v3_client, installing):
    _start_first_install(api_v3_client, installing)
    response = api_v3_client.post(INSTALL, json={"plugin_id": "weather"})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["operation_id"]
