"""
Every "which directory holds plugin X?" answer, from one tree, per caller.

src/plugin_system/plugin_dirs.py holds the rules; the callers differ only in
explicit arguments (search dirs, ``ledmatrix-`` prefix, case folding, whether
the manifest pass runs). This file builds one project tree that exercises
every rule and pins each caller's answer for each id, so a change to either
the shared rules or a caller's arguments shows up as a table row.

Callers:
  discovery  PluginManager._scan_directory_for_plugins -> plugin_directories
  pm_get     PluginManager.get_plugin_directory before discovery has run
  loader     PluginLoader.find_plugin_directory (no discovery mapping)
  store      PluginStoreManager._find_plugin_path
"""

import json
import logging
import os
import sys
import threading
from pathlib import Path

import pytest

from src.plugin_system import plugin_dirs
from src.plugin_system.plugin_dirs import (
    ManifestStatus, PluginDirectoryIndex, resolve_plugin_dir,
)
from src.plugin_system.plugin_loader import PluginLoader
from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.state_reconciliation import (
    StateReconciliation, disk_plugin_ids,
)
from src.plugin_system.store_manager import PluginStoreManager

CONFIGURED = "plugin-repos"
SIBLING = "plugins"


def _write(base: Path, dir_name: str, manifest) -> Path:
    d = base / dir_name
    d.mkdir(parents=True)
    if manifest is not None:
        text = manifest if isinstance(manifest, str) else json.dumps(manifest)
        (d / "manifest.json").write_text(text, encoding="utf-8")
    return d


def _plugin(base: Path, dir_name: str, plugin_id: str, **extra) -> Path:
    return _write(base, dir_name, dict({"id": plugin_id, "name": plugin_id,
                                        "version": "1.0.0"}, **extra))


def _link_dir(link: Path, target: Path) -> None:
    """A symlink where the OS allows one; on Windows without the privilege,
    a directory junction, which the code under test sees the same way
    (is_dir() follows it, iterdir() lists it under the link's name)."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi
        _winapi.CreateJunction(str(target), str(link))


@pytest.fixture
def tree(tmp_path):
    repos = tmp_path / CONFIGURED
    legacy = tmp_path / SIBLING
    repos.mkdir()
    legacy.mkdir()

    # configured dir (plugin-repos/)
    _plugin(repos, "exact", "exact")                       # id == dir name
    _plugin(repos, "ledmatrix-stocks", "stocks")           # manifest id != dir name
    _plugin(repos, "ledmatrix-legacy", "ledmatrix-legacy")  # prefix only by name
    _plugin(repos, "MixedCase", "MixedCase")               # case differences
    _plugin(repos, "renamed-dir", "other-id")              # dir name belongs to no id
    _plugin(repos, "shadow", "not-shadow")                 # name says one id ...
    _plugin(repos, "real-shadow", "shadow")                # ... manifest says it's here
    _plugin(repos, "ghost.standalone-backup-preinstall", "ghost")   # set aside
    _plugin(repos, "exact.standalone-backup-migrating", "exact")    # set aside, dup id
    _plugin(repos, ".hidden", "hidden")                    # hidden / staging
    _plugin(repos, "zz-dupe", "dupe")                      # duplicate ids:
    _plugin(repos, "ledmatrix-dupe", "dupe")               #   prefix beats other,
    _plugin(repos, "dupe", "dupe")                         #   exact beats prefix
    _write(repos, "broken", "{ not json")                  # unreadable manifest
    _write(repos, "noid", {"name": "No id"})               # parses, no id
    _write(repos, "listy", [1, 2])                         # parses, not an object
    _write(repos, "nomanifest", None)                      # not a plugin
    _plugin(repos, "both", "both")                         # also in plugins/
    _plugin(repos, "ledmatrix-weather", "weather")         # manifest hit here vs
    (repos / "README.md").write_text("not a dir")          # a file, never a match

    dev_target = _plugin(tmp_path / "dev-checkouts", "devplug-src", "devplug")
    _link_dir(repos / "dev-link", dev_target)              # symlinked dev plugin

    # sibling dir (plugins/): store fallback only
    _plugin(legacy, "both", "both")
    _plugin(legacy, "legacy-only", "legacy-only")
    _plugin(legacy, "ledmatrix-sibling", "sibling")
    _plugin(legacy, "weather", "weather")                  # ... a name hit here
    return tmp_path


def _discovery(root: Path) -> PluginManager:
    pm = object.__new__(PluginManager)
    pm.logger = logging.getLogger("test_plugin_dirs")
    pm._discovery_lock = threading.RLock()
    pm._skip_reported = set()
    pm.plugin_manifests = {}
    pm.plugin_directories = {}
    pm.plugins_dir = root / CONFIGURED
    return pm


def _answers(root: Path, plugin_id: str) -> dict:
    repos = root / CONFIGURED
    discovered = _discovery(root)
    discovered._scan_directory_for_plugins(repos)
    fresh = _discovery(root)  # discovery not run: exercises the disk rules
    store = PluginStoreManager(plugins_dir=str(repos),
                               uninstalled_registry_path=str(root / "u.json"))

    def rel(p):
        return None if p is None else Path(p).relative_to(root).as_posix()

    return {
        "discovery": rel(discovered.plugin_directories.get(plugin_id)),
        "pm_get": rel(fresh.get_plugin_directory(plugin_id)),
        "loader": rel(PluginLoader().find_plugin_directory(plugin_id, repos)),
        "store": rel(store._find_plugin_path(plugin_id)),
    }


R = CONFIGURED + "/"
S = SIBLING + "/"

#          id                     discovery          pm_get             loader             store
TABLE = [
    ("exact",                     R + "exact",       R + "exact",       R + "exact",       R + "exact"),
    # manifest id != dir name: the manifest finds it; pm_get by prefix
    ("stocks",                    R + "ledmatrix-stocks", R + "ledmatrix-stocks",
                                  R + "ledmatrix-stocks", R + "ledmatrix-stocks"),
    # ledmatrix- prefix: name-only callers with prefix=True; the store has none
    ("legacy",                    None,              R + "ledmatrix-legacy", R + "ledmatrix-legacy", None),
    ("ledmatrix-legacy",          R + "ledmatrix-legacy", R + "ledmatrix-legacy",
                                  R + "ledmatrix-legacy", R + "ledmatrix-legacy"),
    # case: only the loader folds case
    ("mixedcase",                 None,              None,              R + "MixedCase",   None),
    ("MixedCase",                 R + "MixedCase",   R + "MixedCase",   R + "MixedCase",   R + "MixedCase"),
    # a directory name no manifest claims still resolves by name
    ("renamed-dir",               None,              R + "renamed-dir", R + "renamed-dir", R + "renamed-dir"),
    ("other-id",                  R + "renamed-dir", None,              R + "renamed-dir", R + "renamed-dir"),
    # manifest id wins over directory name (pm_get has no manifest pass)
    ("shadow",                    R + "real-shadow", R + "shadow",      R + "real-shadow", R + "real-shadow"),
    # backups and hidden dirs are never plugins, by id or by name
    ("ghost",                     None,              None,              None,              None),
    ("ghost.standalone-backup-preinstall", None,     None,              None,              None),
    ("hidden",                    None,              None,              None,              None),
    (".hidden",                   None,              None,              None,              None),
    # duplicate ids: exact name, then ledmatrix-<id>, then by name
    ("dupe",                      R + "dupe",        R + "dupe",        R + "dupe",        R + "dupe"),
    # unreadable manifest: found by name so it can be repaired/removed
    ("broken",                    None,              R + "broken",      R + "broken",      R + "broken"),
    ("noid",                      None,              R + "noid",        R + "noid",        R + "noid"),
    ("nomanifest",                None,              R + "nomanifest",  R + "nomanifest",  R + "nomanifest"),
    ("README.md",                 None,              None,              None,              None),
    # symlinked dev plugin: found through the link, path kept inside the dir
    ("devplug",                   R + "dev-link",    None,              R + "dev-link",    R + "dev-link"),
    ("dev-link",                  None,              R + "dev-link",    R + "dev-link",    R + "dev-link"),
    # search order: only the store looks in plugins/, and configured first
    ("both",                      R + "both",        R + "both",        R + "both",        R + "both"),
    ("legacy-only",               None,              None,              None,              S + "legacy-only"),
    ("sibling",                   None,              None,              None,              S + "ledmatrix-sibling"),
    # configured dir searched completely (manifest hit) before plugins/ (name hit)
    ("weather",                   R + "ledmatrix-weather", R + "ledmatrix-weather",
                                  R + "ledmatrix-weather", R + "ledmatrix-weather"),
    # not one plain path segment: nothing, never a join or a truncation
    ("../plugins/both",           None,              None,              None,              None),
    ("plugin-repos/exact",        None,              None,              None,              None),
    ("",                          None,              None,              None,              None),
]


@pytest.mark.parametrize("plugin_id,discovery,pm_get,loader,store", TABLE,
                         ids=[row[0] or "<empty>" for row in TABLE])
def test_each_caller_resolves_each_id(tree, plugin_id, discovery, pm_get, loader, store):
    assert _answers(tree, plugin_id) == {
        "discovery": discovery, "pm_get": pm_get, "loader": loader, "store": store,
    }


class TestListings:
    def test_discovery_registers_manifest_ids_only(self, tree):
        pm = _discovery(tree)
        found = pm._scan_directory_for_plugins(tree / CONFIGURED)
        assert sorted(found) == sorted([
            "exact", "stocks", "ledmatrix-legacy", "MixedCase", "other-id",
            "not-shadow", "shadow", "dupe", "both", "weather", "devplug",
        ])
        assert len(found) == len(set(found)), "a duplicate id was listed twice"
        assert set(pm.plugin_manifests) == set(found)

    def test_store_lists_every_dir_with_a_manifest(self, tree):
        store = PluginStoreManager(plugins_dir=str(tree / CONFIGURED),
                                   uninstalled_registry_path=str(tree / "u.json"))
        assert store.list_installed_plugins() == sorted([
            "exact", "stocks", "ledmatrix-legacy", "MixedCase", "other-id",
            "not-shadow", "shadow", "dupe", "both", "weather", "devplug",
            # manifest present but no usable id: listed by directory name
            "broken", "noid", "listy",
        ])

    def test_reconciliation_counts_parseable_manifests(self, tree):
        assert disk_plugin_ids(tree / CONFIGURED) == {
            "exact", "stocks", "ledmatrix-legacy", "MixedCase", "other-id",
            "not-shadow", "shadow", "dupe", "both", "weather", "devplug",
            "noid", "listy",
        }

    def test_reconciliation_disk_state_is_keyed_like_config(self, tree):
        recon = object.__new__(StateReconciliation)
        recon.plugins_dir = tree / CONFIGURED
        recon.logger = logging.getLogger("test_plugin_dirs")
        state = recon._get_disk_state()
        assert state["stocks"] == {"exists_on_disk": True, "version": "1.0.0",
                                   "name": "stocks"}
        assert "ledmatrix-stocks" not in state
        # A manifest that is valid JSON but not an object used to abort the
        # whole disk state with AttributeError.
        assert state["listy"] == {"exists_on_disk": True, "version": None, "name": None}

    def test_all_listings_skip_backups_and_hidden(self, tree):
        store = PluginStoreManager(plugins_dir=str(tree / CONFIGURED),
                                   uninstalled_registry_path=str(tree / "u.json"))
        pm = _discovery(tree)
        listings = {
            "discovery": set(pm._scan_directory_for_plugins(tree / CONFIGURED)),
            "store": set(store.list_installed_plugins()),
            "reconciliation": disk_plugin_ids(tree / CONFIGURED),
        }
        for name, ids in listings.items():
            assert not {"ghost", "hidden", ".hidden"} & ids, name
            assert not any(plugin_dirs.BACKUP_MARKER in i for i in ids), name
        # the backup of `exact` did not replace the live one
        assert pm.plugin_directories["exact"] == tree / CONFIGURED / "exact"


class TestIndex:
    def test_each_manifest_is_read_once_per_scan(self, tree, monkeypatch):
        reads = []
        real = plugin_dirs._read_entry
        monkeypatch.setattr(plugin_dirs, "_read_entry",
                            lambda p: reads.append(p.name) or real(p))
        index = PluginDirectoryIndex.scan(tree / CONFIGURED)
        for plugin_id in ("exact", "stocks", "dupe", "shadow", "nope"):
            index.find(plugin_id, prefix=True, case_insensitive=True)
        index.plugins()
        index.installed_ids(require_parseable_manifest=True)
        assert len(reads) == len(set(reads)) == len(index.entries)

    def test_manifest_statuses(self, tree):
        index = PluginDirectoryIndex.scan(tree / CONFIGURED)
        status = {e.name: e.status for e in index.entries}
        assert status["exact"] == ManifestStatus.OK
        assert status["broken"] == ManifestStatus.UNREADABLE
        assert status["noid"] == ManifestStatus.NO_ID
        assert status["listy"] == ManifestStatus.NOT_OBJECT
        assert status["nomanifest"] == ManifestStatus.MISSING
        assert "README.md" not in status

    def test_duplicates_are_reported_with_every_claimant(self, tree):
        dupes = PluginDirectoryIndex.scan(tree / CONFIGURED).duplicates()
        assert sorted(e.name for e in dupes["dupe"]) == \
            ["dupe", "ledmatrix-dupe", "zz-dupe"]
        # the backup of `exact` is not a claimant
        assert "exact" not in dupes

    def test_duplicate_preference_without_an_exact_name(self, tmp_path):
        _plugin(tmp_path, "zz-dupe", "dupe")
        _plugin(tmp_path, "aa-dupe", "dupe")
        _plugin(tmp_path, "ledmatrix-dupe", "dupe")
        assert resolve_plugin_dir("dupe", [tmp_path], prefix=False) == \
            tmp_path / "ledmatrix-dupe"
        (tmp_path / "ledmatrix-dupe" / "manifest.json").unlink()
        assert resolve_plugin_dir("dupe", [tmp_path], prefix=False) == \
            tmp_path / "aa-dupe"

    def test_exact_name_beats_prefix_even_when_it_sorts_later(self, tmp_path):
        _plugin(tmp_path, "ledmatrix-zeta", "zeta")
        _plugin(tmp_path, "zeta", "zeta")
        index = PluginDirectoryIndex.scan(tmp_path)
        assert index.plugins()["zeta"].name == "zeta"
        assert resolve_plugin_dir("zeta", [tmp_path], prefix=True) == tmp_path / "zeta"

    @pytest.mark.parametrize("bad", [None, 5, b"exact", ["exact"]])
    def test_non_string_ids_resolve_to_nothing(self, tree, bad):
        for kwargs in ({"prefix": True}, {"prefix": True, "by_manifest": False},
                       {"prefix": False, "case_insensitive": True}):
            assert resolve_plugin_dir(bad, [tree / CONFIGURED], **kwargs) is None

    def test_missing_search_dir_is_empty_not_an_error(self, tmp_path):
        index = PluginDirectoryIndex.scan(tmp_path / "absent")
        assert index.entries == [] and index.error is None
        assert resolve_plugin_dir("x", [tmp_path / "absent"], prefix=True) is None

    def test_discovery_warns_once_about_a_duplicate(self, tree, caplog):
        pm = _discovery(tree)
        with caplog.at_level(logging.WARNING, logger="test_plugin_dirs"):
            for _ in range(3):
                pm._scan_directory_for_plugins(tree / CONFIGURED)
        hits = [r for r in caplog.records if "'dupe'" in r.getMessage()]
        assert len(hits) == 1
        assert "zz-dupe" in hits[0].getMessage()


class TestAutoUpdateUsesManifestIds:
    def test_update_targets_manifest_id_and_its_directory(self, tree, monkeypatch):
        from web_interface import auto_update

        store = PluginStoreManager(plugins_dir=str(tree / CONFIGURED),
                                   uninstalled_registry_path=str(tree / "u.json"))
        calls = []

        def fake_update(plugin_id):
            calls.append(plugin_id)
            if plugin_id == "stocks":
                path = tree / CONFIGURED / "ledmatrix-stocks" / "manifest.json"
                m = json.loads(path.read_text(encoding="utf-8"))
                m["version"] = "2.0.0"
                path.write_text(json.dumps(m), encoding="utf-8")
            return True

        monkeypatch.setattr(store, "update_plugin", fake_update)
        monkeypatch.setattr(store, "_get_local_git_info", lambda p: None)
        updated, failed = auto_update.update_plugins(store)
        assert "stocks" in calls and "ledmatrix-stocks" not in calls
        assert not any(plugin_dirs.BACKUP_MARKER in c for c in calls)
        # the version change was seen in ledmatrix-stocks/, not a missing stocks/
        assert updated == ["stocks"] and failed == []
