"""Tests for atomic plugin updates (store_manager._reinstall_with_rollback).

Regression for a field data-loss incident: update_plugin's reinstall paths
(monorepo migration AND routine archive updates) deleted the installed
plugin BEFORE downloading its replacement — a mid-update network failure
permanently destroyed the plugin. Seen live: a Pi with broken DNS lost 12
plugins from one update pass.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.plugin_system.store_manager import PluginStoreManager  # noqa: E402

PLUGIN_ID = "rollback-test-plugin"


@pytest.fixture
def store(tmp_path):
    mgr = PluginStoreManager(plugins_dir=str(tmp_path))
    plugin_dir = tmp_path / PLUGIN_ID
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(json.dumps(
        {"id": PLUGIN_ID, "name": "Rollback Test", "version": "1.0.0"}))
    (plugin_dir / "manager.py").write_text("# old version marker\n")
    return mgr, plugin_dir


class TestReinstallWithRollback:
    def test_failed_install_restores_old_version(self, store):
        """The whole point: a failed download must leave the old install."""
        mgr, plugin_dir = store
        with patch.object(mgr, "install_plugin", return_value=False):
            ok = mgr._reinstall_with_rollback(PLUGIN_ID, plugin_dir)
        assert ok is False
        assert plugin_dir.exists()
        assert "old version marker" in (plugin_dir / "manager.py").read_text()
        # no aside debris left behind
        leftovers = [p for p in plugin_dir.parent.iterdir()
                     if "standalone-backup" in p.name]
        assert leftovers == []

    def test_install_exception_restores_old_version(self, store):
        mgr, plugin_dir = store
        with patch.object(mgr, "install_plugin",
                          side_effect=RuntimeError("network down")):
            ok = mgr._reinstall_with_rollback(PLUGIN_ID, plugin_dir)
        assert ok is False
        assert plugin_dir.exists()
        assert "old version marker" in (plugin_dir / "manager.py").read_text()

    def test_successful_install_removes_aside(self, store):
        mgr, plugin_dir = store

        def fake_install(plugin_id):
            new_dir = plugin_dir  # same path, new content
            new_dir.mkdir(exist_ok=True)
            (new_dir / "manager.py").write_text("# new version\n")
            (new_dir / "manifest.json").write_text(json.dumps(
                {"id": PLUGIN_ID, "name": "Rollback Test", "version": "2.0.0"}))
            return True

        with patch.object(mgr, "install_plugin", side_effect=fake_install):
            ok = mgr._reinstall_with_rollback(PLUGIN_ID, plugin_dir)
        assert ok is True
        assert "new version" in (plugin_dir / "manager.py").read_text()
        leftovers = [p for p in plugin_dir.parent.iterdir()
                     if "standalone-backup" in p.name]
        assert leftovers == []

    def test_partial_download_debris_is_replaced_by_old_version(self, store):
        """A failed install that left a partial directory must still roll back."""
        mgr, plugin_dir = store

        def fake_partial_install(plugin_id):
            plugin_dir.mkdir(exist_ok=True)
            (plugin_dir / "half-downloaded.tmp").write_text("junk")
            return False

        with patch.object(mgr, "install_plugin", side_effect=fake_partial_install):
            ok = mgr._reinstall_with_rollback(PLUGIN_ID, plugin_dir)
        assert ok is False
        assert "old version marker" in (plugin_dir / "manager.py").read_text()
        assert not (plugin_dir / "half-downloaded.tmp").exists()

    def test_stale_aside_from_previous_crash_is_cleared(self, store):
        mgr, plugin_dir = store
        stale = plugin_dir.parent / f"{PLUGIN_ID}.standalone-backup-migrating"
        stale.mkdir()
        (stale / "old.txt").write_text("stale")
        with patch.object(mgr, "install_plugin", return_value=False):
            ok = mgr._reinstall_with_rollback(PLUGIN_ID, plugin_dir)
        assert ok is False
        assert plugin_dir.exists()

    def test_aside_name_is_invisible_to_discovery(self, store, tmp_path):
        """The aside still contains a manifest.json — discovery must skip it
        (relies on the existing '.standalone-backup-' exclusion)."""
        mgr, plugin_dir = store
        from src.plugin_system.plugin_manager import PluginManager
        aside = plugin_dir.parent / f"{PLUGIN_ID}.standalone-backup-migrating"
        plugin_dir.rename(aside)
        pm = PluginManager(plugins_dir=str(tmp_path), config_manager=None,
                           display_manager=None, cache_manager=None)
        found = pm._scan_directory_for_plugins(Path(tmp_path))
        assert PLUGIN_ID not in found


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
