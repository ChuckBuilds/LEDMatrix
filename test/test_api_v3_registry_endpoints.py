"""
Endpoint tests for the plugin-registry routes in api_v3:
POST /plugins/store/refresh and POST /plugins/registry-from-url.

Both reach out to the network through PluginStoreManager (mocked here) and
had no endpoint-level coverage; registry-from-url in particular takes a
user-supplied URL and hands it straight to the manager.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401,E402


class TestRefreshPluginStore:
    URL = "/api/v3/plugins/store/refresh"

    def test_uninitialized_manager_is_a_500(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager = None
        response = api_v3_client.post(self.URL, json={})
        assert response.status_code == 500
        assert "not initialized" in response.get_json()["message"]

    def test_success_reports_plugin_count(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.fetch_registry.return_value = {
            "plugins": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
        response = api_v3_client.post(self.URL, json={})
        assert response.status_code == 200
        assert response.get_json()["plugin_count"] == 3

    def test_forces_a_refresh_rather_than_using_cache(self, api_v3_client, api_v3_module):
        manager = api_v3_module.api_v3.plugin_store_manager
        manager.fetch_registry.return_value = {"plugins": []}
        api_v3_client.post(self.URL, json={})
        manager.fetch_registry.assert_called_once_with(force_refresh=True)

    def test_empty_registry_reports_zero(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.fetch_registry.return_value = {}
        response = api_v3_client.post(self.URL, json={})
        assert response.get_json()["plugin_count"] == 0

    def test_no_body_is_accepted(self, api_v3_client, api_v3_module):
        # Regression: `request.get_json() or {}` says a missing body is
        # fine, but get_json() raises UnsupportedMediaType before `or {}`
        # is reached, so a bodyless POST — the natural way to call a
        # refresh endpoint — came back 500.
        api_v3_module.api_v3.plugin_store_manager.fetch_registry.return_value = {"plugins": []}
        assert api_v3_client.post(self.URL).status_code == 200

    def test_body_without_json_content_type_is_accepted(
            self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.fetch_registry.return_value = {"plugins": []}
        response = api_v3_client.post(self.URL, data="", content_type="text/plain")
        assert response.status_code == 200

    def test_malformed_json_body_falls_back_to_defaults(
            self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.fetch_registry.return_value = {"plugins": []}
        response = api_v3_client.post(
            self.URL, data="{not json", content_type="application/json")
        assert response.status_code == 200

    @pytest.mark.parametrize("key", ["fetch_commit_info", "fetch_latest_versions"])
    def test_either_commit_info_key_extends_the_message(
            self, api_v3_client, api_v3_module, key):
        # fetch_latest_versions is the older spelling; both must work.
        api_v3_module.api_v3.plugin_store_manager.fetch_registry.return_value = {"plugins": []}
        response = api_v3_client.post(self.URL, json={key: True})
        assert "commit metadata" in response.get_json()["message"]

    def test_message_stays_plain_without_the_flag(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.fetch_registry.return_value = {"plugins": []}
        response = api_v3_client.post(self.URL, json={})
        assert response.get_json()["message"] == "Plugin store refreshed"

    def test_network_failure_is_a_500(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.fetch_registry.side_effect = (
            ConnectionError("github unreachable"))
        response = api_v3_client.post(self.URL, json={})
        assert response.status_code == 500
        assert response.get_json()["message"] == "An error occurred; see logs for details"

    def test_failure_body_carries_no_traceback_or_paths(
            self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.fetch_registry.side_effect = (
            RuntimeError("failed at /home/user/LEDMatrix/src/secret.py line 42"))
        body = api_v3_client.post(self.URL, json={}).get_json()
        assert "Traceback" not in str(body)
        # `details` is describe_exception output: one line, type-named,
        # credential-redacted. It may quote the message, but never a stack.
        assert body["details"].startswith("RuntimeError:")
        assert "\n" not in body["details"]


class TestRegistryFromUrl:
    URL = "/api/v3/plugins/registry-from-url"

    def test_uninitialized_manager_is_a_500(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager = None
        response = api_v3_client.post(self.URL, json={"repo_url": "http://x"})
        assert response.status_code == 500

    def test_missing_repo_url_is_a_400(self, api_v3_client, api_v3_module):
        response = api_v3_client.post(self.URL, json={})
        assert response.status_code == 400
        assert "repo_url required" in response.get_json()["message"]
        api_v3_module.api_v3.plugin_store_manager.fetch_registry_from_url.assert_not_called()

    def test_success_returns_the_plugin_list(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.fetch_registry_from_url.return_value = {
            "plugins": [{"id": "clock"}]}
        response = api_v3_client.post(
            self.URL, json={"repo_url": "https://github.com/o/r"})
        assert response.status_code == 200
        body = response.get_json()
        assert body["plugins"] == [{"id": "clock"}]
        assert body["registry_url"] == "https://github.com/o/r"

    def test_url_is_trimmed_before_use(self, api_v3_client, api_v3_module):
        manager = api_v3_module.api_v3.plugin_store_manager
        manager.fetch_registry_from_url.return_value = {"plugins": []}
        api_v3_client.post(self.URL, json={"repo_url": "  https://github.com/o/r  "})
        manager.fetch_registry_from_url.assert_called_once_with("https://github.com/o/r")

    def test_registry_without_plugins_key_returns_empty_list(
            self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.fetch_registry_from_url.return_value = {
            "other": 1}
        response = api_v3_client.post(self.URL, json={"repo_url": "http://x"})
        assert response.get_json()["plugins"] == []

    def test_no_registry_found_is_a_400(self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.fetch_registry_from_url.return_value = None
        response = api_v3_client.post(self.URL, json={"repo_url": "http://x/not-a-registry"})
        assert response.status_code == 400
        assert "Failed to fetch registry" in response.get_json()["message"]

    @pytest.mark.parametrize("url", [
        "not a url",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://localhost:8080/admin",
    ])
    def test_unusable_urls_fail_cleanly(self, api_v3_client, api_v3_module, url):
        # Characterization: the handler performs no URL validation of its
        # own — whatever the manager makes of the URL decides the outcome.
        # What is pinned here is that a rejected URL produces a clean 400
        # rather than a traceback or a 500.
        api_v3_module.api_v3.plugin_store_manager.fetch_registry_from_url.return_value = None
        response = api_v3_client.post(self.URL, json={"repo_url": url})
        assert response.status_code == 400
        assert "Traceback" not in str(response.get_json())

    def test_fetch_exception_is_a_500_without_internals(
            self, api_v3_client, api_v3_module):
        api_v3_module.api_v3.plugin_store_manager.fetch_registry_from_url.side_effect = (
            ValueError("parse failed in /srv/app/internal.py"))
        response = api_v3_client.post(self.URL, json={"repo_url": "http://x"})
        assert response.status_code == 500
        body = response.get_json()
        assert body["message"] == "An error occurred; see logs for details"
        assert "Traceback" not in str(body)

    def test_non_string_repo_url_is_a_500_not_a_crash(
            self, api_v3_client, api_v3_module):
        # .strip() on a non-string raises; the handler's catch-all turns
        # that into a 500 rather than propagating.
        response = api_v3_client.post(self.URL, json={"repo_url": 12345})
        assert response.status_code == 500
