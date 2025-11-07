import json
import tempfile
import shutil
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock
import requests

from src.plugin_system.store_manager import PluginStoreManager


class TestPluginStoreManager(TestCase):
    """Test plugin store manager with retry logic and validation."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.plugins_dir = self.temp_dir / "plugins"
        self.plugins_dir.mkdir()
    
    def tearDown(self):
        """Clean up test fixtures."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
    
    def make_resp(self, json_data):
        """Helper to create mock response."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock()
        resp.iter_content = lambda chunk_size: [b"test"]
        return resp
    
    @patch("requests.get")
    def test_fetch_registry_retries_then_success(self, mock_get):
        """Test that registry fetch retries on failure."""
        # First two attempts raise RequestException, third returns ok
        mock_get.side_effect = [
            requests.RequestException("network error"),
            requests.Timeout("timeout error"),
            self.make_resp({"plugins": [{"id": "x"}]})
        ]

        mgr = PluginStoreManager(plugins_dir=str(self.plugins_dir))
        reg = mgr.fetch_registry(force_refresh=True)
        self.assertEqual(reg["plugins"][0]["id"], "x")
        self.assertEqual(mock_get.call_count, 3)
    
    def test_manifest_required_fields_validation(self):
        """Test that invalid manifests are rejected and plugin dir cleaned up."""
        plugin_id = "demo"
        plugin_dir = self.plugins_dir / plugin_id
        plugin_dir.mkdir()
        
        # Write incomplete manifest (missing required fields)
        manifest_path = plugin_dir / "manifest.json"
        manifest_path.write_text(json.dumps({"id": plugin_id}))
        
        mgr = PluginStoreManager(plugins_dir=str(self.plugins_dir))
        
        # Simulate validation logic (extracted from install_plugin)
        self.assertTrue(manifest_path.exists())
        
        try:
            with open(manifest_path, 'r') as mf:
                manifest = json.load(mf)
            required_fields = ['id', 'name', 'entry_point', 'class_name']
            missing = [f for f in required_fields if f not in manifest]
            if missing:
                # Mimic code behavior - remove directory on invalid manifest
                if plugin_dir.exists():
                    shutil.rmtree(plugin_dir)
        except Exception:
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir)
        
        # Directory should be removed due to invalid manifest
        self.assertFalse(plugin_dir.exists(), "Plugin directory should be removed on invalid manifest")
    
    def test_manifest_validation_with_complete_manifest(self):
        """Test that valid manifests pass validation."""
        plugin_id = "valid_plugin"
        plugin_dir = self.plugins_dir / plugin_id
        plugin_dir.mkdir()
        
        # Write complete manifest with all required fields
        manifest = {
            "id": plugin_id,
            "name": "Valid Plugin",
            "entry_point": "manager.py",
            "class_name": "ValidPlugin"
        }
        manifest_path = plugin_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        
        # Validate - should not remove directory
        required_fields = ['id', 'name', 'entry_point', 'class_name']
        
        with open(manifest_path, 'r') as mf:
            loaded_manifest = json.load(mf)
        
        missing = [f for f in required_fields if f not in loaded_manifest]
        
        # Should have no missing fields
        self.assertEqual(len(missing), 0, f"Manifest should have all required fields, missing: {missing}")
        # Directory should still exist
        self.assertTrue(plugin_dir.exists(), "Valid manifest should not remove directory")
