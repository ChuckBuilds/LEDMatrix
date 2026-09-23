"""Registry entries that aren't plugins are hidden and refused.

The official registry no longer lists skins, but a custom registry (added
from the Plugin Store) can still carry ``"type": "skin"`` entries. With the
skin system removed, installing one as a plugin would unpack it into the
plugins directory, so the store hides such entries and refuses them at install.
"""

import json
from unittest.mock import MagicMock

import pytest

from src.plugin_system.store_manager import PluginStoreManager
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: F401

PLUGIN = {"id": "clock", "name": "Clock", "repo": "https://github.com/x/clock"}
UNTYPED = {"id": "news", "name": "News", "repo": "https://github.com/x/news"}
SKIN = {"id": "retro", "name": "Retro", "type": "skin", "repo": "https://github.com/x/retro"}


@pytest.mark.parametrize("entry,expected", [
    (PLUGIN, True),
    ({**PLUGIN, "type": "plugin"}, True),
    ({**PLUGIN, "type": None}, True),
    (SKIN, False),
    ({**PLUGIN, "type": "theme"}, False),
    (None, False),
    ("clock", False),
])
def test_is_plugin_entry(entry, expected):
    assert PluginStoreManager.is_plugin_entry(entry) is expected


def test_install_refuses_a_non_plugin_entry(tmp_path):
    store = PluginStoreManager(plugins_dir=str(tmp_path / "plugins"))
    store.get_plugin_info = MagicMock(return_value=dict(SKIN))
    store._install_from_monorepo_zip = MagicMock()
    store._install_via_git = MagicMock()
    assert store._install_plugin_impl("retro") is False
    assert not (tmp_path / "plugins" / "retro").exists()
    store._install_from_monorepo_zip.assert_not_called()
    store._install_via_git.assert_not_called()


@pytest.fixture
def store(api_v3_module):
    mock = MagicMock()
    mock.is_plugin_entry = PluginStoreManager.is_plugin_entry
    api_v3_module.api_v3.plugin_store_manager = mock
    return mock


def test_custom_registry_listing_hides_non_plugins(api_v3_client, store):
    store.fetch_registry_from_url.return_value = {"plugins": [PLUGIN, SKIN, UNTYPED]}
    resp = api_v3_client.post("/api/v3/plugins/registry-from-url",
                              data=json.dumps({"repo_url": "https://github.com/x/registry"}),
                              content_type="application/json")
    assert resp.status_code == 200
    assert [p["id"] for p in resp.get_json()["plugins"]] == ["clock", "news"]


def test_store_listing_hides_non_plugins(api_v3_client, store):
    store.search_plugins.return_value = [PLUGIN, SKIN, UNTYPED]
    resp = api_v3_client.get("/api/v3/plugins/store/list")
    assert resp.status_code == 200
    body = resp.get_json()
    listed = body.get("data", body).get("plugins", [])
    assert [p["id"] for p in listed] == ["clock", "news"]


def test_install_route_refuses_a_non_plugin_entry(api_v3_client, store):
    store.get_registry_info.return_value = dict(SKIN)
    resp = api_v3_client.post("/api/v3/plugins/install",
                              data=json.dumps({"plugin_id": "retro"}),
                              content_type="application/json")
    assert resp.status_code == 400
    assert "not a plugin" in resp.get_json()["message"]
    store.install_plugin.assert_not_called()
