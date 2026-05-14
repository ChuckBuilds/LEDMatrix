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


class TestStateReconciliationUnrecoverable(unittest.TestCase):
    """Tests for the unrecoverable-plugin cache and force reconcile.

    Regression coverage for the infinite reinstall loop where a config
    entry referenced a plugin not present in the registry (e.g. legacy
    'github' / 'youtube' entries). The reconciler used to retry the
    install on every HTTP request; it now caches the failure for the
    process lifetime and only retries on an explicit ``force=True``
    reconcile call.
    """

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.plugins_dir = self.temp_dir / "plugins"
        self.plugins_dir.mkdir()

        self.state_manager = Mock(spec=PluginStateManager)
        self.state_manager.get_all_states.return_value = {}
        self.config_manager = Mock()
        self.config_manager.load_config.return_value = {
            "ghost": {"enabled": True}
        }
        self.plugin_manager = Mock()
        self.plugin_manager.plugin_manifests = {}
        self.plugin_manager.plugins = {}

        # Store manager with an empty registry — install_plugin always fails
        self.store_manager = Mock()
        self.store_manager.fetch_registry.return_value = {"plugins": []}
        self.store_manager.install_plugin.return_value = False
        self.store_manager.was_recently_uninstalled.return_value = False

        self.reconciler = StateReconciliation(
            state_manager=self.state_manager,
            config_manager=self.config_manager,
            plugin_manager=self.plugin_manager,
            plugins_dir=self.plugins_dir,
            store_manager=self.store_manager,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_not_in_registry_marks_unrecoverable_without_install(self):
        """If the plugin isn't in the registry at all, skip install_plugin."""
        result = self.reconciler.reconcile_state()

        # One inconsistency, unfixable, no install attempt made.
        self.assertEqual(len(result.inconsistencies_found), 1)
        self.assertEqual(len(result.inconsistencies_fixed), 0)
        self.store_manager.install_plugin.assert_not_called()
        self.assertIn("ghost", self.reconciler._unrecoverable_missing_on_disk)

    def test_subsequent_reconcile_does_not_retry(self):
        """Second reconcile pass must not touch install_plugin or fetch_registry again."""
        self.reconciler.reconcile_state()
        self.store_manager.fetch_registry.reset_mock()
        self.store_manager.install_plugin.reset_mock()

        result = self.reconciler.reconcile_state()

        # Still one inconsistency, still no install attempt, no new registry fetch
        self.assertEqual(len(result.inconsistencies_found), 1)
        inc = result.inconsistencies_found[0]
        self.assertFalse(inc.can_auto_fix)
        self.assertEqual(inc.fix_action, FixAction.MANUAL_FIX_REQUIRED)
        self.store_manager.install_plugin.assert_not_called()
        self.store_manager.fetch_registry.assert_not_called()

    def test_force_reconcile_clears_unrecoverable_cache(self):
        """force=True must re-attempt previously-failed plugins."""
        self.reconciler.reconcile_state()
        self.assertIn("ghost", self.reconciler._unrecoverable_missing_on_disk)

        # Now pretend the registry gained the plugin so the pre-check passes
        # and install_plugin is actually invoked.
        self.store_manager.fetch_registry.return_value = {
            "plugins": [{"id": "ghost"}]
        }
        self.store_manager.install_plugin.return_value = True
        self.store_manager.install_plugin.reset_mock()

        # Config still references ghost; disk still missing it — the
        # reconciler should re-attempt install now that force=True cleared
        # the cache. Use assert_called_once_with so a future regression
        # that accidentally triggers a second install attempt on force=True
        # is caught.
        result = self.reconciler.reconcile_state(force=True)

        self.store_manager.install_plugin.assert_called_once_with("ghost")

    def test_registry_unreachable_does_not_mark_unrecoverable(self):
        """Transient registry failures should not poison the cache."""
        self.store_manager.fetch_registry.side_effect = Exception("network down")

        result = self.reconciler.reconcile_state()

        self.assertEqual(len(result.inconsistencies_found), 1)
        self.assertNotIn("ghost", self.reconciler._unrecoverable_missing_on_disk)
        self.store_manager.install_plugin.assert_not_called()

    def test_recently_uninstalled_skips_auto_repair(self):
        """A freshly-uninstalled plugin must not be resurrected by the reconciler."""
        self.store_manager.was_recently_uninstalled.return_value = True
        self.store_manager.fetch_registry.return_value = {
            "plugins": [{"id": "ghost"}]
        }

        result = self.reconciler.reconcile_state()

        self.assertEqual(len(result.inconsistencies_found), 1)
        inc = result.inconsistencies_found[0]
        self.assertFalse(inc.can_auto_fix)
        self.assertEqual(inc.fix_action, FixAction.MANUAL_FIX_REQUIRED)
        self.store_manager.install_plugin.assert_not_called()

    def test_real_store_manager_empty_registry_on_network_failure(self):
        """Regression: using the REAL PluginStoreManager (not a Mock), verify
        the reconciler does NOT poison the unrecoverable cache when
        ``fetch_registry`` fails with no stale cache available.

        Previously, the default stale-cache fallback in ``fetch_registry``
        silently returned ``{"plugins": []}`` on network failure with no
        cache. The reconciler's ``_auto_repair_missing_plugin`` saw "no
        candidates in registry" and marked everything unrecoverable — a
        regression that would bite every user doing a fresh boot on flaky
        WiFi. The fix is ``fetch_registry(raise_on_failure=True)`` in
        ``_auto_repair_missing_plugin`` so the reconciler can tell a real
        registry miss from a network error.
        """
        from src.plugin_system.store_manager import PluginStoreManager
        import requests as real_requests

        real_store = PluginStoreManager(plugins_dir=str(self.plugins_dir))
        real_store.registry_cache = None  # fresh boot, no cache
        real_store.registry_cache_time = None

        # Stub the underlying HTTP so no real network call is made but the
        # real fetch_registry code path runs.
        real_store._http_get_with_retries = Mock(
            side_effect=real_requests.ConnectionError("wifi down")
        )

        reconciler = StateReconciliation(
            state_manager=self.state_manager,
            config_manager=self.config_manager,
            plugin_manager=self.plugin_manager,
            plugins_dir=self.plugins_dir,
            store_manager=real_store,
        )

        result = reconciler.reconcile_state()

        # One inconsistency (ghost is in config, not on disk), but
        # because the registry lookup failed transiently, we must NOT
        # have marked it unrecoverable — a later reconcile (after the
        # network comes back) can still auto-repair.
        self.assertEqual(len(result.inconsistencies_found), 1)
        self.assertNotIn("ghost", reconciler._unrecoverable_missing_on_disk)


if __name__ == '__main__':
    unittest.main()

