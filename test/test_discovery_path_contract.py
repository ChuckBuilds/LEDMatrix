"""
Drift guard: three components independently answer "where is plugin X?" and
their answers must stay coherent — plus the `.standalone-backup-` naming
contract that install/rollback shares with discovery.

The three resolvers:
1. PluginManager._scan_directory_for_plugins — scans ONLY the configured dir.
2. PluginStoreManager._find_plugin_path — configured dir, then a sibling
   `plugins/` fallback derived from the configured dir's parent.
3. SchemaManager.get_schema_path — configured dir, then project-root
   `plugins/`, then `plugin-repos/`, then case-insensitive scans.

The divergence is characterized (a plugin visible to the store/schema
fallbacks but invisible to discovery is a real support-issue shape) so any
change to the fallback chains is a deliberate one.

The `.standalone-backup-` contract: store_manager renames a plugin dir aside
with that substring during install/rollback; discovery MUST skip such dirs
or a half-finished install would surface a ghost plugin. The substring is
duplicated as a literal in both files — this test breaks if either side
changes it unilaterally.
"""

import json
import logging
import threading
from pathlib import Path

import pytest

from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.schema_manager import SchemaManager
from src.plugin_system.store_manager import PluginStoreManager


def _write_plugin(base: Path, plugin_id: str, dir_name: str = None):
    plugin_dir = base / (dir_name or plugin_id)
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(json.dumps({
        "id": plugin_id, "name": plugin_id, "version": "1.0.0",
    }))
    (plugin_dir / "config_schema.json").write_text(json.dumps({
        "type": "object", "properties": {"enabled": {"type": "boolean"}},
    }))
    return plugin_dir


def _scanner():
    """A PluginManager stripped to just its discovery machinery — the full
    constructor wires config/schema/health managers this test doesn't need."""
    pm = object.__new__(PluginManager)
    pm.logger = logging.getLogger("test_discovery_path_contract")
    pm._discovery_lock = threading.Lock()
    pm.plugin_manifests = {}
    pm.plugin_directories = {}
    return pm


class TestResolversAgreeOnConfiguredDir:
    def test_all_three_find_a_plugin_in_the_configured_dir(self, tmp_path):
        plugins_dir = tmp_path / "plugin-repos"
        plugin_dir = _write_plugin(plugins_dir, "demo-plugin")

        found = _scanner()._scan_directory_for_plugins(plugins_dir)
        assert found == ["demo-plugin"]

        store = PluginStoreManager(
            plugins_dir=str(plugins_dir),
            uninstalled_registry_path=str(tmp_path / "uninstalled.json"))
        assert store._find_plugin_path("demo-plugin") == plugin_dir

        schema = SchemaManager(plugins_dir=plugins_dir, project_root=tmp_path)
        assert schema.get_schema_path("demo-plugin") == \
            plugin_dir / "config_schema.json"


class TestFallbackDivergence:
    def test_plugin_only_in_plugins_dir_fallback(self, tmp_path):
        """Characterized divergence: configured dir is plugin-repos/, but the
        plugin sits in a sibling plugins/. The store and schema fallbacks
        find it; discovery does NOT — so the plugin is installable/
        configurable but never loads. Pinned so a change to any fallback
        chain shows up here."""
        configured = tmp_path / "plugin-repos"
        configured.mkdir()
        legacy_dir = _write_plugin(tmp_path / "plugins", "legacy-plugin")

        # Discovery: invisible.
        assert _scanner()._scan_directory_for_plugins(configured) == []

        # Store fallback: visible (parent-of-configured / 'plugins').
        store = PluginStoreManager(
            plugins_dir=str(configured),
            uninstalled_registry_path=str(tmp_path / "uninstalled.json"))
        assert store._find_plugin_path("legacy-plugin") == legacy_dir

        # Schema fallback: visible (project_root / 'plugins').
        schema = SchemaManager(plugins_dir=configured, project_root=tmp_path)
        assert schema.get_schema_path("legacy-plugin") == \
            legacy_dir / "config_schema.json"

    def test_schema_manager_probes_plugins_before_plugin_repos(self, tmp_path):
        # Documented order (also in CLAUDE.md): plugins/ wins over
        # plugin-repos/ when the same id exists in both.
        in_plugins = _write_plugin(tmp_path / "plugins", "dupe")
        _write_plugin(tmp_path / "plugin-repos", "dupe")
        schema = SchemaManager(plugins_dir=None, project_root=tmp_path)
        assert schema.get_schema_path("dupe") == \
            in_plugins / "config_schema.json"

    def test_schema_manager_case_insensitive_fallback(self, tmp_path):
        plugin_dir = _write_plugin(tmp_path / "plugins", "MyPlugin",
                                   dir_name="MyPlugin")
        schema = SchemaManager(plugins_dir=None, project_root=tmp_path)
        assert schema.get_schema_path("myplugin") == \
            plugin_dir / "config_schema.json"


class TestStandaloneBackupContract:
    def test_discovery_skips_backup_dirs(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        _write_plugin(plugins_dir, "real-plugin")
        # A rollback-in-progress dir with a valid manifest must NOT surface.
        _write_plugin(plugins_dir, "real-plugin",
                      dir_name="real-plugin.standalone-backup-migrating")

        found = _scanner()._scan_directory_for_plugins(plugins_dir)
        assert found == ["real-plugin"]

    def test_backup_substring_literal_matches_across_files(self):
        """The substring is duplicated in plugin_manager (skip check) and
        store_manager (rename-aside names). If either side changes it, the
        other silently stops honoring the contract — this test is the
        tripwire."""
        root = Path(__file__).resolve().parents[1]
        pm_text = (root / "src/plugin_system/plugin_manager.py").read_text()
        sm_text = (root / "src/plugin_system/store_manager.py").read_text()
        assert "'.standalone-backup-'" in pm_text.replace('"', "'")
        assert ".standalone-backup-" in sm_text


class TestSkinTargetResolution:
    def _store(self, tmp_path):
        return PluginStoreManager(
            plugins_dir=str(tmp_path / "plugins"),
            uninstalled_registry_path=str(tmp_path / "uninstalled.json"))

    def test_valid_skin_id_resolves_inside_skins_dir(self, tmp_path):
        from src.skin_system import skin_runtime
        store = self._store(tmp_path)
        target = store._resolve_skin_target("my-skin")
        assert target is not None
        assert target.parent == skin_runtime.get_skins_directory().resolve()

    @pytest.mark.parametrize("bad_id", [
        "../evil",
        "..",
        "a/../../etc",
        "/etc/passwd",
        "skin/../../outside",
        "",
        None,
        123,
    ])
    def test_traversal_and_malformed_ids_rejected(self, tmp_path, bad_id):
        store = self._store(tmp_path)
        assert store._resolve_skin_target(bad_id) is None
