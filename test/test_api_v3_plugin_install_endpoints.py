"""
Endpoint tests for POST /plugins/install and POST /plugins/install-from-url.

Both were only ever tested at the PluginStoreManager layer, so the route
logic — the queue-vs-direct branch, schema invalidation, plugin discovery,
state and history recording — was unexercised.

/plugins/install carries the same install logic twice: once inside the
operation-queue callback and once in the direct fallback. The paired
tests below assert both branches produce the same side effects, so the
duplication cannot quietly drift.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402

INSTALL = "/api/v3/plugins/install"
FROM_URL = "/api/v3/plugins/install-from-url"


@pytest.fixture
def queued(api_v3_module):
    """Enable the operation queue and run its callback synchronously."""
    queue = MagicMock()

    def enqueue(operation_type, plugin_id, operation_callback=None):
        queue.callback_result = operation_callback(MagicMock())
        return "op-123"

    queue.enqueue_operation.side_effect = enqueue
    api_v3_module.api_v3.operation_queue = queue
    return queue


def side_effects(module):
    """The manager calls a successful install is expected to make."""
    api = module.api_v3
    return {
        "schema_invalidated": api.schema_manager.invalidate_cache.call_args_list,
        "discovered": api.plugin_manager.discover_plugins.call_count,
        "loaded": api.plugin_manager.load_plugin.call_args_list,
        "state_set": api.plugin_state_manager.set_plugin_installed.call_args_list,
        "history": api.operation_history.record_operation.call_args_list,
    }


class TestInstallValidation:
    def test_uninitialized_store_manager_is_a_500(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager = None
        response = api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        assert response.status_code == 500
        assert "not initialized" in response.get_json()["message"]

    def test_missing_plugin_id_is_a_400(self, api_v3_client, api_v3_module):
        response = api_v3_client.post(INSTALL, json={})
        assert response.status_code == 400
        assert "plugin_id required" in response.get_json()["message"]
        api_v3_module.api_v3.plugin_store_manager.install_plugin.assert_not_called()

    def test_empty_body_is_a_400(self, api_v3_client, api_v3_module):
        assert api_v3_client.post(INSTALL, json=None).status_code == 400


class TestInstallDirectPath:
    """operation_queue is None — the fallback branch."""

    def test_success(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = True
        response = api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        assert response.status_code == 200
        assert response.get_json()["status"] == "success"

    def test_success_side_effects(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = True
        api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        effects = side_effects(api_v3_module)
        assert effects["schema_invalidated"] == [(("clock",), {})]
        assert effects["discovered"] == 1
        assert effects["loaded"] == [(("clock",), {})]
        assert effects["state_set"] == [(("clock",), {})]
        assert effects["history"][0].kwargs["status"] == "success"

    def test_branch_forwarded_to_the_manager(self, api_v3_client, api_v3_module):
        manager = api_v3_module.api_v3.plugin_store_manager
        manager.install_plugin.return_value = True
        api_v3_client.post(INSTALL, json={"plugin_id": "clock", "branch": "dev"})
        manager.install_plugin.assert_called_once_with("clock", branch="dev")

    def test_branch_named_in_the_message(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = True
        response = api_v3_client.post(INSTALL, json={"plugin_id": "clock", "branch": "dev"})
        assert "(branch: dev)" in response.get_json()["message"]

    def test_failure_is_a_500(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = False
        response = api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        assert response.status_code == 500
        assert "Failed to install" in response.get_json()["message"]

    def test_failure_mentions_missing_registry_entry(self, api_v3_client, api_v3_module):
        manager = api_v3_module.api_v3.plugin_store_manager
        manager.install_plugin.return_value = False
        manager.get_plugin_info.return_value = None
        response = api_v3_client.post(INSTALL, json={"plugin_id": "ghost"})
        assert "not found in registry" in response.get_json()["message"]

    def test_failure_omits_registry_note_when_plugin_is_known(
            self, api_v3_client, api_v3_module):
        manager = api_v3_module.api_v3.plugin_store_manager
        manager.install_plugin.return_value = False
        manager.get_plugin_info.return_value = {"id": "clock"}
        response = api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        assert "not found in registry" not in response.get_json()["message"]

    def test_failure_recorded_in_history(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = False
        api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        record = api_v3_module.api_v3.operation_history.record_operation.call_args
        assert record.kwargs["status"] == "failed"

    def test_no_side_effects_on_failure(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = False
        api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        effects = side_effects(api_v3_module)
        assert effects["schema_invalidated"] == []
        assert effects["loaded"] == []
        assert effects["state_set"] == []


class TestInstallQueuedPath:
    """operation_queue present — the callback branch."""

    def test_returns_an_operation_id(self, api_v3_client, api_v3_module, queued):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = True
        response = api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        assert response.status_code == 200
        assert response.get_json()["data"]["operation_id"] == "op-123"

    def test_message_says_queued(self, api_v3_client, api_v3_module, queued):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = True
        response = api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        assert "queued" in response.get_json()["message"]

    def test_callback_success_side_effects(self, api_v3_client, api_v3_module, queued):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = True
        api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        effects = side_effects(api_v3_module)
        assert effects["schema_invalidated"] == [(("clock",), {})]
        assert effects["discovered"] == 1
        assert effects["loaded"] == [(("clock",), {})]
        assert effects["state_set"] == [(("clock",), {})]
        assert effects["history"][0].kwargs["status"] == "success"

    def test_callback_reports_success(self, api_v3_client, api_v3_module, queued):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = True
        api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        assert queued.callback_result["success"] is True

    def test_callback_failure_raises_for_the_queue(self, api_v3_client, api_v3_module, queued):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = False
        # The callback signals failure by raising, so the queue can mark the
        # operation failed; the route's catch-all turns it into a 500.
        response = api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        assert response.status_code == 500

    def test_callback_failure_recorded_in_history(self, api_v3_client, api_v3_module, queued):
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = False
        api_v3_client.post(INSTALL, json={"plugin_id": "clock"})
        record = api_v3_module.api_v3.operation_history.record_operation.call_args
        assert record.kwargs["status"] == "failed"

    def test_branch_forwarded_from_the_callback(self, api_v3_client, api_v3_module, queued):
        manager = api_v3_module.api_v3.plugin_store_manager
        manager.install_plugin.return_value = True
        api_v3_client.post(INSTALL, json={"plugin_id": "clock", "branch": "dev"})
        manager.install_plugin.assert_called_once_with("clock", branch="dev")


class TestInstallPathsAgree:
    """The queue callback and the direct fallback duplicate the same logic."""

    def _run(self, client, module, install_ok, queue):
        module.api_v3.plugin_store_manager.install_plugin.return_value = install_ok
        client.post(INSTALL, json={"plugin_id": "clock", "branch": "dev"})
        return side_effects(module)

    def test_success_side_effects_match(self, api_v3_client, api_v3_module):
        direct = self._run(api_v3_client, api_v3_module, True, None)

        # Reset and re-run through the queue.
        for mock in (api_v3_module.api_v3.schema_manager,
                     api_v3_module.api_v3.plugin_manager,
                     api_v3_module.api_v3.plugin_state_manager,
                     api_v3_module.api_v3.operation_history):
            mock.reset_mock()
        queue = MagicMock()
        queue.enqueue_operation.side_effect = (
            lambda t, p, operation_callback=None: operation_callback(MagicMock()) and "op")
        api_v3_module.api_v3.operation_queue = queue
        queued = self._run(api_v3_client, api_v3_module, True, queue)

        assert direct["schema_invalidated"] == queued["schema_invalidated"]
        assert direct["discovered"] == queued["discovered"]
        assert direct["loaded"] == queued["loaded"]
        assert direct["state_set"] == queued["state_set"]
        assert (direct["history"][0].kwargs["status"]
                == queued["history"][0].kwargs["status"])
        assert (direct["history"][0].kwargs["details"]
                == queued["history"][0].kwargs["details"])

    def test_only_the_message_wording_differs(self, api_v3_client, api_v3_module):
        # Characterized: the direct path says "Plugin installed
        # successfully" while the queue callback says "Plugin clock
        # installed successfully". Cosmetic, and the queue's text is
        # internal to the operation record rather than the HTTP response.
        api_v3_module.api_v3.plugin_store_manager.install_plugin.return_value = True
        direct = api_v3_client.post(INSTALL, json={"plugin_id": "clock"}).get_json()
        assert direct["message"] == "Plugin installed successfully"


class TestInstallFromUrl:
    def test_uninitialized_store_manager_is_a_500(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager = None
        assert api_v3_client.post(FROM_URL, json={"repo_url": "http://x"}).status_code == 500

    def test_missing_repo_url_is_a_400(self, api_v3_client, api_v3_module):
        response = api_v3_client.post(FROM_URL, json={})
        assert response.status_code == 400
        assert "repo_url required" in response.get_json()["message"]

    def test_success(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_from_url.return_value = {
            "success": True, "plugin_id": "clock", "name": "Clock"}
        response = api_v3_client.post(FROM_URL, json={"repo_url": "https://github.com/o/r"})
        assert response.status_code == 200
        body = response.get_json()
        assert body["plugin_id"] == "clock"
        assert body["name"] == "Clock"

    def test_all_optional_arguments_forwarded(self, api_v3_client, api_v3_module):
        manager = api_v3_module.api_v3.plugin_store_manager
        manager.install_from_url.return_value = {"success": True, "plugin_id": "clock"}
        api_v3_client.post(FROM_URL, json={
            "repo_url": "  https://github.com/o/r  ",
            "plugin_id": "clock",
            "plugin_path": "plugins/clock",
            "branch": "dev",
        })
        manager.install_from_url.assert_called_once_with(
            repo_url="https://github.com/o/r",
            plugin_id="clock",
            plugin_path="plugins/clock",
            branch="dev",
        )

    def test_success_invalidates_schema_and_loads_plugin(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_from_url.return_value = {
            "success": True, "plugin_id": "clock"}
        api_v3_client.post(FROM_URL, json={"repo_url": "http://x"})
        api_v3_module.api_v3.schema_manager.invalidate_cache.assert_called_once_with("clock")
        api_v3_module.api_v3.plugin_manager.load_plugin.assert_called_once_with("clock")

    def test_success_without_plugin_id_skips_discovery(self, api_v3_client, api_v3_module):
        # install_from_url can succeed without naming the plugin; there is
        # then nothing to invalidate or load.
        api_v3_module.api_v3.plugin_store_manager.install_from_url.return_value = {
            "success": True, "plugin_id": None}
        api_v3_client.post(FROM_URL, json={"repo_url": "http://x"})
        api_v3_module.api_v3.schema_manager.invalidate_cache.assert_not_called()
        api_v3_module.api_v3.plugin_manager.load_plugin.assert_not_called()

    def test_branch_from_result_included(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_from_url.return_value = {
            "success": True, "plugin_id": "clock", "branch": "dev"}
        body = api_v3_client.post(FROM_URL, json={"repo_url": "http://x"}).get_json()
        assert body["branch"] == "dev"
        assert "(branch: dev)" in body["message"]

    def test_failure_reports_the_managers_error(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_from_url.return_value = {
            "success": False, "error": "repo not found"}
        response = api_v3_client.post(FROM_URL, json={"repo_url": "http://x"})
        assert response.status_code == 500
        assert response.get_json()["message"] == "repo not found"

    def test_failure_without_error_uses_fallback_text(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_from_url.return_value = {
            "success": False}
        response = api_v3_client.post(FROM_URL, json={"repo_url": "http://x"})
        assert "Failed to install plugin from URL" in response.get_json()["message"]

    def test_manager_exception_is_a_500(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.install_from_url.side_effect = (
            RuntimeError("boom"))
        assert api_v3_client.post(FROM_URL, json={"repo_url": "http://x"}).status_code == 500
