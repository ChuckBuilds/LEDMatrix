import json
from unittest import TestCase
from unittest.mock import MagicMock

from flask import Flask

from web_interface.blueprints.api_v3 import api_v3


class TestAPIPlugins(TestCase):
    """Test plugin API endpoints."""
    
    def setUp(self):
        """Set up Flask test app."""
        self.app = Flask(__name__)
        self.app.register_blueprint(api_v3, url_prefix="/api/v3")
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.setup_api_mocks()
    
    def setup_api_mocks(self, store_ok=True):
        """Set up mock managers for API tests."""
        # Mock managers used by blueprint via attributes
        mock_config = MagicMock()
        # Keep a simple in-memory dict to simulate config persistence
        self.state = {"config": {}, "secrets": {}}

        def load_config():
            return self.state["config"]

        def save_config(cfg):
            self.state["config"] = cfg

        def get_raw_file_content(which):
            if which == 'secrets':
                return self.state["secrets"]
            return self.state["config"]

        def save_raw_file_content(which, data):
            if which == 'secrets':
                self.state["secrets"] = data
            else:
                self.state["config"] = data

        mock_config.load_config.side_effect = load_config
        mock_config.save_config.side_effect = save_config
        mock_config.get_raw_file_content.side_effect = get_raw_file_content
        mock_config.save_raw_file_content.side_effect = save_raw_file_content

        mock_store = MagicMock()
        mock_store.fetch_registry.return_value = {
            "plugins": [{
                "id": "clock-simple",
                "name": "Clock",
                "repo": "https://github.com/x/y"
            }]
        }
        mock_store.search_plugins.return_value = [{
            "id": "clock-simple",
            "name": "Clock",
            "category": "time",
            "description": "Simple clock plugin",
            "tags": [],
            "stars": 5,
            "verified": True,
            "repo": "https://github.com/x/y",
            "last_updated": "2025-11-01",
            "last_commit": "abc1234"
        }]
        mock_store.install_plugin.return_value = store_ok
        mock_store.uninstall_plugin.return_value = True
        mock_store.update_plugin.return_value = True
        mock_store.get_plugin_info.return_value = {"id": "clock-simple", "verified": True}

        mock_pm = MagicMock()
        mock_pm.plugin_manifests = {"clock-simple": {"id": "clock-simple", "name": "Clock"}}
        mock_pm.get_all_plugin_info.return_value = [{
            "id": "clock-simple",
            "name": "Clock",
            "last_updated": "2025-11-01",
            "loaded": False
        }]
        mock_pm.get_plugin.return_value = None
        mock_pm.discover_plugins.return_value = ["clock-simple"]
        mock_pm.load_plugin.return_value = True

        # Attach to blueprint
        api_v3.config_manager = mock_config
        api_v3.plugin_store_manager = mock_store
        api_v3.plugin_manager = mock_pm
        
        self.mock_pm = mock_pm
        self.mock_store = mock_store
    
    def test_list_store_plugins(self):
        """Test listing plugins from store."""
        resp = self.client.get("/api/v3/plugins/store/list")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["data"]["plugins"][0]["id"], "clock-simple")
    
    def test_install_plugin_and_loads(self):
        """Test plugin installation triggers discovery and loading."""
        resp = self.client.post(
            "/api/v3/plugins/install",
            json={"plugin_id": "clock-simple"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.mock_store.install_plugin.called)
        self.assertTrue(self.mock_pm.discover_plugins.called)
        self.assertTrue(self.mock_pm.load_plugin.called)
    
    def test_save_plugin_config_triggers_on_config_change(self):
        """Test that saving config triggers on_config_change hook."""
        class DummyPlugin:
            def __init__(self):
                self.received = None
            def on_config_change(self, cfg):
                self.received = cfg
        
        dummy = DummyPlugin()
        self.mock_pm.get_plugin.return_value = dummy

        # Pre-existing config
        self.state["config"] = {"clock-simple": {"enabled": False}}

        payload = {
            "plugin_id": "clock-simple",
            "config": {"enabled": True, "token": "abc", "nested": {"api_key": "xyz"}}
        }
        resp = self.client.post("/api/v3/plugins/config", json=payload)
        
        self.assertEqual(resp.status_code, 200)
        # on_config_change should receive the merged config
        self.assertIsInstance(dummy.received, dict)
        self.assertTrue(dummy.received.get("enabled"))
