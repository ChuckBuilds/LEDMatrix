"""
Tests for state reconciliation system.

Desired state is config.json plus the plugins on disk; observed state is
the runtime snapshot the display publishes (plugin_runtime). There is no
third, persisted record: data/plugin_state.json is retired.
"""

import unittest
import tempfile
import shutil
import json
import time
from pathlib import Path
from unittest.mock import Mock, patch

from src.plugin_system.plugin_runtime import (
    PluginRuntimeView, SNAPSHOT_SCHEMA, view_from_snapshot,
)
from src.plugin_system.state_reconciliation import (
    StateReconciliation,
    InconsistencyType,
    FixAction,
    ReconciliationResult
)


def live_view(plugins):
    """A live runtime view, as read from a fresh snapshot of a running display."""
    now = time.time()
    return view_from_snapshot({
        "schema": SNAPSHOT_SCHEMA, "running": True, "published_at": now,
        "stale_after": 180, "plugins": plugins,
    }, now=now)


def stale_view(plugins):
    """The same snapshot, read long after the display stopped refreshing it."""
    now = time.time()
    return view_from_snapshot({
        "schema": SNAPSHOT_SCHEMA, "running": True, "published_at": now - 3600,
        "stale_after": 180, "plugins": plugins,
    }, now=now)


class TestStateReconciliation(unittest.TestCase):
    """Test state reconciliation system."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.plugins_dir = self.temp_dir / "plugins"
        self.plugins_dir.mkdir()

        self.config_manager = Mock()
        # What the display reports; tests replace it.
        self.observed = PluginRuntimeView(status="unknown")

        # Initialize reconciliation system
        self.reconciler = StateReconciliation(
            config_manager=self.config_manager,
            plugins_dir=self.plugins_dir,
            runtime_source=lambda: self.observed,
        )

    def tearDown(self):
        """Clean up test fixtures."""
        shutil.rmtree(self.temp_dir)

    def _install(self, plugin_id, version="1.0.0"):
        plugin_dir = self.plugins_dir / plugin_id
        plugin_dir.mkdir()
        with open(plugin_dir / "manifest.json", 'w') as f:
            json.dump({"id": plugin_id, "version": version, "name": plugin_id}, f)

    def test_reconcile_no_inconsistencies(self):
        """Config, disk and the display agree."""
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }
        self._install("plugin1")
        self.observed = live_view({"plugin1": {
            "loaded": True, "state": "enabled", "error": None,
            "version": "1.0.0", "loaded_at": time.time()}})

        result = self.reconciler.reconcile_state()

        self.assertIsInstance(result, ReconciliationResult)
        self.assertEqual(len(result.inconsistencies_found), 0)
        self.assertTrue(result.reconciliation_successful)

    def test_plugin_missing_in_config(self):
        """Test detection of plugin missing in config."""
        self.config_manager.load_config.return_value = {}
        self._install("plugin1")

        result = self.reconciler.reconcile_state()

        self.assertEqual(len(result.inconsistencies_found), 1)
        inconsistency = result.inconsistencies_found[0]
        self.assertEqual(inconsistency.plugin_id, "plugin1")
        self.assertEqual(inconsistency.inconsistency_type, InconsistencyType.PLUGIN_MISSING_IN_CONFIG)
        self.assertEqual(inconsistency.fix_action, FixAction.AUTO_FIX)

    def test_plugin_missing_on_disk(self):
        """Test detection of plugin missing on disk."""
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }

        result = self.reconciler.reconcile_state()

        self.assertEqual(len(result.inconsistencies_found), 1)
        inconsistency = result.inconsistencies_found[0]
        self.assertEqual(inconsistency.plugin_id, "plugin1")
        self.assertEqual(inconsistency.inconsistency_type, InconsistencyType.PLUGIN_MISSING_ON_DISK)
        self.assertEqual(inconsistency.fix_action, FixAction.MANUAL_FIX_REQUIRED)

    def test_enabled_but_not_loaded_is_reported_not_fixed(self):
        """Enabled in config, installed, but the display reports a failed load:
        a finding that names the error, left alone (the display loads by
        config on its own) and not counted as needing manual attention."""
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }
        self._install("plugin1")
        self.observed = live_view({"plugin1": {
            "loaded": False, "state": "error",
            "error": {"type": "ImportError", "message": "No module named 'x'",
                      "at": time.time(), "recoverable": False},
            "version": None, "loaded_at": None}})
        self.config_manager.save_config = Mock()

        result = self.reconciler.reconcile_state()

        self.assertEqual(len(result.inconsistencies_found), 1)
        inconsistency = result.inconsistencies_found[0]
        self.assertEqual(inconsistency.inconsistency_type, InconsistencyType.PLUGIN_ENABLED_MISMATCH)
        self.assertEqual(inconsistency.fix_action, FixAction.NO_ACTION)
        self.assertIn("No module named", inconsistency.description)
        self.assertEqual(result.inconsistencies_fixed, [])
        self.assertEqual(result.inconsistencies_manual, [])
        self.assertTrue(result.reconciliation_successful)
        self.config_manager.save_config.assert_not_called()

    def test_enabled_and_absent_from_live_snapshot_is_not_loaded(self):
        """A plugin a live snapshot does not list is not loaded."""
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }
        self._install("plugin1")
        self.observed = live_view({})

        result = self.reconciler.reconcile_state()

        types = [i.inconsistency_type for i in result.inconsistencies_found]
        self.assertEqual(types, [InconsistencyType.PLUGIN_ENABLED_MISMATCH])

    def test_disabled_and_not_loaded_agrees(self):
        """Missing "enabled" is disabled (the display's rule), and a plugin
        the display has not loaded matches it."""
        self.config_manager.load_config.return_value = {"plugin1": {}}
        self._install("plugin1")
        self.observed = live_view({})

        result = self.reconciler.reconcile_state()

        self.assertEqual(result.inconsistencies_found, [])

    def test_loaded_at_an_older_version_is_reported(self):
        """The display runs 1.0.0 while 2.0.0 is on disk: restart needed."""
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }
        self._install("plugin1", version="2.0.0")
        self.observed = live_view({"plugin1": {
            "loaded": True, "state": "enabled", "error": None,
            "version": "1.0.0", "loaded_at": time.time()}})

        result = self.reconciler.reconcile_state()

        self.assertEqual(len(result.inconsistencies_found), 1)
        inconsistency = result.inconsistencies_found[0]
        self.assertEqual(inconsistency.inconsistency_type, InconsistencyType.PLUGIN_VERSION_MISMATCH)
        self.assertEqual(inconsistency.fix_action, FixAction.NO_ACTION)
        self.assertIn("restart", inconsistency.description)
        self.assertTrue(result.reconciliation_successful)

    def test_stale_or_missing_snapshot_is_not_compared(self):
        """Observed state that is not live says nothing: no findings from it."""
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }
        self._install("plugin1")
        dead = {"plugin1": {"loaded": False, "state": "error", "error": None,
                            "version": None, "loaded_at": None}}

        for observed in (stale_view(dead), PluginRuntimeView(status="stopped"),
                         PluginRuntimeView(status="unknown")):
            self.observed = observed
            result = self.reconciler.reconcile_state()
            self.assertEqual(result.inconsistencies_found, [], observed.status)

    def test_no_runtime_source_is_unknown(self):
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }
        self._install("plugin1")
        reconciler = StateReconciliation(config_manager=self.config_manager,
                                         plugins_dir=self.plugins_dir)
        self.assertEqual(reconciler.reconcile_state().inconsistencies_found, [])

    def test_runtime_source_that_raises_is_unknown(self):
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True}
        }
        self._install("plugin1")

        def boom():
            raise RuntimeError("cache unreadable")

        reconciler = StateReconciliation(config_manager=self.config_manager,
                                         plugins_dir=self.plugins_dir,
                                         runtime_source=boom)
        result = reconciler.reconcile_state()
        self.assertEqual(result.inconsistencies_found, [])
        self.assertTrue(result.reconciliation_successful)

    def test_old_constructor_arguments_are_refused(self):
        """state_manager / plugin_manager are gone; passing them is an error,
        not something silently ignored."""
        with self.assertRaises(TypeError):
            StateReconciliation(state_manager=Mock(), config_manager=self.config_manager,
                                plugin_manager=Mock(), plugins_dir=self.plugins_dir)

    def test_plugin_states_combines_desired_and_observed(self):
        """What /plugins/state serves in place of plugin_state.json."""
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True},
            "plugin2": {"enabled": False},
            "ghost": {"enabled": True},
        }
        self._install("plugin1", version="1.2.0")
        self._install("plugin2")
        self._install("plugin3")
        self.observed = live_view({"plugin1": {
            "loaded": True, "state": "enabled", "error": None,
            "version": "1.2.0", "loaded_at": 1234.0}})

        states = self.reconciler.plugin_states()

        self.assertEqual(set(states), {"plugin1", "plugin2", "plugin3", "ghost"})
        self.assertEqual(states["plugin1"], {
            "plugin_id": "plugin1", "installed": True, "version": "1.2.0",
            "in_config": True, "enabled": True, "loaded": True,
            "state": "enabled", "error_info": None,
            "loaded_version": "1.2.0", "loaded_at": 1234.0,
        })
        self.assertFalse(states["plugin2"]["enabled"])
        self.assertIs(states["plugin2"]["loaded"], False)
        self.assertEqual(states["plugin2"]["state"], "unloaded")
        self.assertFalse(states["plugin3"]["in_config"])
        self.assertFalse(states["ghost"]["installed"])

    def test_plugin_states_without_a_live_snapshot_reports_unknown(self):
        self.config_manager.load_config.return_value = {"plugin1": {"enabled": True}}
        self._install("plugin1")
        self.observed = stale_view({"plugin1": {"loaded": True, "state": "enabled"}})

        record = self.reconciler.plugin_states()["plugin1"]

        self.assertTrue(record["enabled"])
        self.assertIsNone(record["loaded"])
        self.assertIsNone(record["state"])
        self.assertIsNone(record["error_info"])

    def test_auto_fix_plugin_missing_in_config(self):
        """Test auto-fix of plugin missing in config."""
        self.config_manager.load_config.return_value = {}
        self._install("plugin1")

        saved_configs = []

        def save_config(config):
            saved_configs.append(config)

        self.config_manager.save_config = save_config

        result = self.reconciler.reconcile_state()

        self.assertEqual(len(result.inconsistencies_fixed), 1)
        self.assertEqual(len(saved_configs), 1)
        self.assertIn("plugin1", saved_configs[0])
        self.assertEqual(saved_configs[0]["plugin1"]["enabled"], False)

    def test_multiple_inconsistencies(self):
        """Test reconciliation with multiple inconsistencies."""
        self.config_manager.load_config.return_value = {
            "plugin1": {"enabled": True},  # Exists in config but not on disk
            # plugin2 exists on disk but not in config
        }
        self._install("plugin2")

        result = self.reconciler.reconcile_state()

        self.assertGreaterEqual(len(result.inconsistencies_found), 2)
        inconsistency_types = [inc.inconsistency_type for inc in result.inconsistencies_found]
        self.assertIn(InconsistencyType.PLUGIN_MISSING_ON_DISK, inconsistency_types)
        self.assertIn(InconsistencyType.PLUGIN_MISSING_IN_CONFIG, inconsistency_types)

    def test_reconciliation_with_exception(self):
        """Test reconciliation handles exceptions gracefully."""
        self.config_manager.load_config.side_effect = Exception("Config error")

        result = self.reconciler.reconcile_state()

        self.assertIsInstance(result, ReconciliationResult)

    def test_fix_failure_handling(self):
        """Test that fix failures are handled correctly."""
        self.config_manager.load_config.return_value = {}
        self._install("plugin1")
        self.config_manager.save_config.side_effect = Exception("Save failed")

        result = self.reconciler.reconcile_state()

        self.assertEqual(len(result.inconsistencies_found), 1)
        self.assertEqual(len(result.inconsistencies_fixed), 0)
        self.assertEqual(len(result.inconsistencies_manual), 1)

    def test_get_config_state_handles_exception(self):
        """Test that _get_config_state handles exceptions."""
        self.config_manager.load_config.side_effect = Exception("Config error")

        state = self.reconciler._get_config_state()

        self.assertEqual(state, {})

    def test_get_disk_state_handles_exception(self):
        """Test that _get_disk_state handles exceptions."""
        with patch.object(self.reconciler, 'plugins_dir', create=True) as mock_dir:
            mock_dir.exists.side_effect = Exception("Disk error")
            mock_dir.iterdir.side_effect = Exception("Disk error")

            state = self.reconciler._get_disk_state()

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

        self.config_manager = Mock()
        self.config_manager.load_config.return_value = {
            "ghost": {"enabled": True}
        }

        # Store manager with an empty registry — install_plugin always fails
        self.store_manager = Mock()
        self.store_manager.fetch_registry.return_value = {"plugins": []}
        self.store_manager.install_plugin.return_value = False
        # A bare Mock() returns a truthy Mock for is_plugin_uninstalled(),
        # which reads as "persistently uninstalled" and skips auto-repair
        # entirely — these tests need the repair path to run.
        self.store_manager.is_plugin_uninstalled.return_value = False

        self.reconciler = StateReconciliation(
            config_manager=self.config_manager,
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

    def test_persistently_uninstalled_skips_auto_repair(self):
        """A plugin the user uninstalled must not be resurrected by the reconciler."""
        self.store_manager.is_plugin_uninstalled.return_value = True
        self.store_manager.fetch_registry.return_value = {
            "plugins": [{"id": "ghost"}]
        }

        result = self.reconciler.reconcile_state()

        self.assertEqual(len(result.inconsistencies_found), 1)
        inc = result.inconsistencies_found[0]
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
            config_manager=self.config_manager,
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

