"""
Tests for state reconciliation system.
"""

import unittest
import tempfile
import shutil
import json
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

from src.plugin_system.state_reconciliation import (
    StateReconciliation,
    InconsistencyType,
    FixAction,
    ReconciliationResult
)
from src.plugin_system.state_manager import PluginStateManager, PluginState, PluginStateStatus


class TestStateReconciliation(unittest.TestCase):
    """Test state reconciliation system."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.plugins_dir = self.temp_dir / "plugins"
        self.plugins_dir.mkdir()
        
        # Create mock managers
        self.state_manager = Mock(spec=PluginStateManager)
        self.config_manager = Mock()
        self.plugin_manager = Mock()
        
        # Initialize reconciliation system
        self.reconciler = StateReconciliation(
            state_manager=self.state_manager,
            config_manager=self.config_manager,
            plugin_manager=self.plugin_manager,
            plugins_dir=self.plugins_dir
        )
    
    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.temp_dir)
    
    def test_reconcile_no_inconsistencies(self):
        """Test reconciliation with no inconsistencies."""
        # Setup: All states are consistent
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }
        
        self.state_manager.get_all_states.return_value = {
            "plugin1": Mock(
                enabled=True,
                status=PluginStateStatus.ENABLED,
                version="1.0.0"
            )
        }
        
        self.plugin_manager.plugin_manifests = {"plugin1": {}}
        self.plugin_manager.plugins = {"plugin1": Mock()}
        
        # Create plugin directory
        plugin_dir = self.plugins_dir / "plugin1"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump({"version": "1.0.0", "name": "Plugin 1"}, f)
        
        # Run reconciliation
        result = self.reconciler.reconcile_state()
        
        # Verify
        self.assertIsInstance(result, ReconciliationResult)
        self.assertEqual(len(result.inconsistencies_found), 0)
        self.assertTrue(result.reconciliation_successful)
    
    def test_plugin_missing_in_config(self):
        """Test detection of plugin missing in config."""
        # Setup: Plugin exists on disk but not in config
        self.config_manager.load_config.return_value = {}
        
        self.state_manager.get_all_states.return_value = {}
        
        self.plugin_manager.plugin_manifests = {}
        self.plugin_manager.plugins = {}
        
        # Create plugin directory
        plugin_dir = self.plugins_dir / "plugin1"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump({"version": "1.0.0", "name": "Plugin 1"}, f)
        
        # Run reconciliation
        result = self.reconciler.reconcile_state()
        
        # Verify inconsistency detected
        self.assertEqual(len(result.inconsistencies_found), 1)
        inconsistency = result.inconsistencies_found[0]
        self.assertEqual(inconsistency.plugin_id, "plugin1")
        self.assertEqual(inconsistency.inconsistency_type, InconsistencyType.PLUGIN_MISSING_IN_CONFIG)
        self.assertTrue(inconsistency.can_auto_fix)
        self.assertEqual(inconsistency.fix_action, FixAction.AUTO_FIX)
    
    def test_plugin_missing_on_disk(self):
        """Test detection of plugin missing on disk."""
        # Setup: Plugin in config but not on disk
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }
        
        self.state_manager.get_all_states.return_value = {}
        
        self.plugin_manager.plugin_manifests = {}
        self.plugin_manager.plugins = {}
        
        # Don't create plugin directory
        
        # Run reconciliation
        result = self.reconciler.reconcile_state()
        
        # Verify inconsistency detected
        self.assertEqual(len(result.inconsistencies_found), 1)
        inconsistency = result.inconsistencies_found[0]
        self.assertEqual(inconsistency.plugin_id, "plugin1")
        self.assertEqual(inconsistency.inconsistency_type, InconsistencyType.PLUGIN_MISSING_ON_DISK)
        self.assertFalse(inconsistency.can_auto_fix)
        self.assertEqual(inconsistency.fix_action, FixAction.MANUAL_FIX_REQUIRED)
    
    def test_enabled_state_mismatch(self):
        """Test detection of enabled state mismatch."""
        # Setup: Config says enabled=True, state manager says enabled=False
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }
        
        self.state_manager.get_all_states.return_value = {
            "plugin1": Mock(
                enabled=False,
                status=PluginStateStatus.DISABLED,
                version="1.0.0"
            )
        }
        
        self.plugin_manager.plugin_manifests = {"plugin1": {}}
        self.plugin_manager.plugins = {}
        
        # Create plugin directory
        plugin_dir = self.plugins_dir / "plugin1"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump({"version": "1.0.0", "name": "Plugin 1"}, f)
        
        # Run reconciliation
        result = self.reconciler.reconcile_state()
        
        # Verify inconsistency detected
        self.assertEqual(len(result.inconsistencies_found), 1)
        inconsistency = result.inconsistencies_found[0]
        self.assertEqual(inconsistency.plugin_id, "plugin1")
        self.assertEqual(inconsistency.inconsistency_type, InconsistencyType.PLUGIN_ENABLED_MISMATCH)
        self.assertTrue(inconsistency.can_auto_fix)
        self.assertEqual(inconsistency.fix_action, FixAction.AUTO_FIX)
    
    def test_auto_fix_plugin_missing_in_config(self):
        """Test auto-fix of plugin missing in config."""
        # Setup
        self.config_manager.load_config.return_value = {}
        
        self.state_manager.get_all_states.return_value = {}
        
        self.plugin_manager.plugin_manifests = {}
        self.plugin_manager.plugins = {}
        
        # Create plugin directory
        plugin_dir = self.plugins_dir / "plugin1"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump({"version": "1.0.0", "name": "Plugin 1"}, f)
        
        # Mock save_config to track calls
        saved_configs = []
        def save_config(config):
            saved_configs.append(config)
        
        self.config_manager.save_config = save_config
        
        # Run reconciliation
        result = self.reconciler.reconcile_state()
        
        # Verify fix was attempted
        self.assertEqual(len(result.inconsistencies_fixed), 1)
        self.assertEqual(len(saved_configs), 1)
        self.assertIn("plugin1", saved_configs[0])
        self.assertEqual(saved_configs[0]["plugin1"]["enabled"], False)
    
    def test_auto_fix_enabled_state_mismatch(self):
        """Test auto-fix of enabled state mismatch."""
        # Setup: Config says enabled=True, state manager says enabled=False
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }
        
        self.state_manager.get_all_states.return_value = {
            "plugin1": Mock(
                enabled=False,
                status=PluginStateStatus.DISABLED,
                version="1.0.0"
            )
        }
        
        self.plugin_manager.plugin_manifests = {"plugin1": {}}
        self.plugin_manager.plugins = {}
        
        # Create plugin directory
        plugin_dir = self.plugins_dir / "plugin1"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump({"version": "1.0.0", "name": "Plugin 1"}, f)
        
        # Mock save_config to track calls
        saved_configs = []
        def save_config(config):
            saved_configs.append(config)
        
        self.config_manager.save_config = save_config
        
        # Run reconciliation
        result = self.reconciler.reconcile_state()
        
        # Verify fix was attempted
        self.assertEqual(len(result.inconsistencies_fixed), 1)
        self.assertEqual(len(saved_configs), 1)
        self.assertEqual(saved_configs[0]["plugin1"]["enabled"], False)
    
    def test_multiple_inconsistencies(self):
        """Test reconciliation with multiple inconsistencies."""
        # Setup: Multiple plugins with different issues
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True},  # Exists in config but not on disk
            # plugin2 exists on disk but not in config
        }
        
        self.state_manager.get_all_states.return_value = {
            "plugin1": Mock(
                enabled=True,
                status=PluginStateStatus.ENABLED,
                version="1.0.0"
            )
        }
        
        self.plugin_manager.plugin_manifests = {}
        self.plugin_manager.plugins = {}
        
        # Create plugin2 directory (exists on disk but not in config)
        plugin2_dir = self.plugins_dir / "plugin2"
        plugin2_dir.mkdir()
        manifest_path = plugin2_dir / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump({"version": "1.0.0", "name": "Plugin 2"}, f)
        
        # Run reconciliation
        result = self.reconciler.reconcile_state()
        
        # Verify multiple inconsistencies found
        self.assertGreaterEqual(len(result.inconsistencies_found), 2)
        
        # Check types
        inconsistency_types = [inc.inconsistency_type for inc in result.inconsistencies_found]
        self.assertIn(InconsistencyType.PLUGIN_MISSING_ON_DISK, inconsistency_types)
        self.assertIn(InconsistencyType.PLUGIN_MISSING_IN_CONFIG, inconsistency_types)
    
    def test_reconciliation_with_exception(self):
        """Test reconciliation handles exceptions gracefully."""
        # Setup: State manager raises exception when getting states
        self.config_manager.load_config.return_value = {}
        self.state_manager.get_all_states.side_effect = Exception("State manager error")
        
        # Run reconciliation
        result = self.reconciler.reconcile_state()
        
        # Verify error is handled - reconciliation may still succeed if other sources work
        self.assertIsInstance(result, ReconciliationResult)
        # Note: Reconciliation may still succeed if other sources provide valid state
    
    def test_fix_failure_handling(self):
        """Test that fix failures are handled correctly."""
        # Setup: Plugin missing in config, but save fails
        self.config_manager.load_config.return_value = {}
        
        self.state_manager.get_all_states.return_value = {}
        
        self.plugin_manager.plugin_manifests = {}
        self.plugin_manager.plugins = {}
        
        # Create plugin directory
        plugin_dir = self.plugins_dir / "plugin1"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump({"version": "1.0.0", "name": "Plugin 1"}, f)
        
        # Mock save_config to raise exception
        self.config_manager.save_config.side_effect = Exception("Save failed")
        
        # Run reconciliation
        result = self.reconciler.reconcile_state()
        
        # Verify inconsistency detected but not fixed
        self.assertEqual(len(result.inconsistencies_found), 1)
        self.assertEqual(len(result.inconsistencies_fixed), 0)
        self.assertEqual(len(result.inconsistencies_manual), 1)
    
    def test_get_config_state_handles_exception(self):
        """Test that _get_config_state handles exceptions."""
        # Setup: Config manager raises exception
        self.config_manager.load_config.side_effect = Exception("Config error")
        
        # Call method directly
        state = self.reconciler._get_config_state()
        
        # Verify empty state returned
        self.assertEqual(state, {})
    
    def test_get_disk_state_handles_exception(self):
        """Test that _get_disk_state handles exceptions."""
        # Setup: Make plugins_dir inaccessible
        with patch.object(self.reconciler, 'plugins_dir', create=True) as mock_dir:
            mock_dir.exists.side_effect = Exception("Disk error")
            mock_dir.iterdir.side_effect = Exception("Disk error")
            
            # Call method directly
            state = self.reconciler._get_disk_state()
            
            # Verify empty state returned
            self.assertEqual(state, {})


if __name__ == '__main__':
    unittest.main()

