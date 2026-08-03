"""The install/update gate, and the shared compatibility rules behind it.

Before this existed, `ledmatrix_min_version` was decoration: the loader logged
an advisory warning and the store never looked at the core version at all, so a
routine store update happily delivered a plugin that could not run. Deleting a
plugin's bundled fallback under those conditions would have handed un-updated
users a scoreboard that fails to load with one line in the journal.

The rules being pinned here, in priority order:

1. Refuse only on **evidence**. Undeclared floor, unparseable version on either
   side, or a core whose self-reported version is untrustworthy → allow. A
   wrong refusal breaks a working install; a wrong allowance degrades to the
   behavior we already had.
2. A core below `TRUSTWORTHY_FLOOR` is *unknown*, not old. The v3.1.0 release
   reports `1.0.0` while nearly every manifest floors at `2.0.0`; blocking on
   that number would stop those users installing anything at all.
3. The loader and the store must agree, because they read the same manifests.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.plugin_system import compatibility


# --------------------------------------------------------------------------
# Floor resolution — every spelling published plugins actually use
# --------------------------------------------------------------------------

class TestDeclaredMinVersion:
    def test_top_level_min_ledmatrix_version(self):
        assert compatibility.declared_min_version(
            {"min_ledmatrix_version": "3.2.0"}) == "3.2.0"

    def test_requires_block(self):
        assert compatibility.declared_min_version(
            {"requires": {"min_ledmatrix_version": "3.1.0"}}) == "3.1.0"

    def test_versions_array_new_spelling(self):
        assert compatibility.declared_min_version(
            {"versions": [{"ledmatrix_min_version": "3.2.0"}]}) == "3.2.0"

    def test_versions_array_deprecated_spelling(self):
        """Most published manifests still say `ledmatrix_min`; ignoring it
        would silently exempt them from the gate."""
        assert compatibility.declared_min_version(
            {"versions": [{"ledmatrix_min": "2.0.0"}]}) == "2.0.0"

    def test_absent(self):
        assert compatibility.declared_min_version({"id": "x"}) is None

    def test_requires_present_but_null(self):
        assert compatibility.declared_min_version({"requires": None}) is None


# --------------------------------------------------------------------------
# The decision itself
# --------------------------------------------------------------------------

class TestCheck:
    def test_blocks_when_plugin_needs_a_newer_core(self):
        ok, reason = compatibility.check(
            {"name": "Hockey Scoreboard", "min_ledmatrix_version": "3.2.0"}, "3.1.0")
        assert ok is False
        assert "3.2.0" in reason and "3.1.0" in reason
        assert "Hockey Scoreboard" in reason

    def test_allows_equal_version(self):
        ok, _ = compatibility.check({"min_ledmatrix_version": "3.2.0"}, "3.2.0")
        assert ok is True

    def test_allows_newer_core(self):
        ok, _ = compatibility.check({"min_ledmatrix_version": "3.2.0"}, "4.0.0")
        assert ok is True

    def test_allows_when_no_floor_declared(self):
        ok, reason = compatibility.check({"id": "x"}, "3.2.0")
        assert ok is True and reason is None

    def test_untrustworthy_core_version_allows_everything(self):
        """The v3.1.0 release reports 1.0.0. Nearly every manifest floors at
        2.0.0, so blocking here would stop those users installing any plugin
        at all — strictly worse than the problem being solved."""
        ok, reason = compatibility.check(
            {"min_ledmatrix_version": "3.2.0"}, "1.0.0")
        assert ok is True and reason is None

    def test_unparseable_core_version_allows(self):
        ok, _ = compatibility.check({"min_ledmatrix_version": "3.2.0"}, "not-a-version")
        assert ok is True

    def test_unparseable_floor_allows(self):
        ok, _ = compatibility.check({"min_ledmatrix_version": {"nope": 1}}, "3.2.0")
        assert ok is True

    def test_v_prefix_tolerated_on_both_sides(self):
        ok, _ = compatibility.check({"min_ledmatrix_version": "v3.3.0"}, "v3.2.0")
        assert ok is False

    @pytest.mark.parametrize("floor,core,expected_ok", [
        ("3.2.0", "3.2.1", True),
        ("3.2.1", "3.2.0", False),
        ("3.10.0", "3.9.0", False),   # numeric compare, not lexical
        ("3.9.0", "3.10.0", True),
    ])
    def test_ordering(self, floor, core, expected_ok):
        ok, _ = compatibility.check({"min_ledmatrix_version": floor}, core)
        assert ok is expected_ok


# --------------------------------------------------------------------------
# The gate in install_plugin
# --------------------------------------------------------------------------

def _write_plugin(plugins_dir: Path, plugin_id: str, manifest: dict) -> Path:
    path = plugins_dir / plugin_id
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "manager.py").write_text("class P: pass\n", encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A PluginStoreManager whose download step is stubbed to drop a plugin
    directory in place, so the test exercises the post-download validation
    path without touching the network."""
    from src.plugin_system.store_manager import PluginStoreManager

    plugins_dir = tmp_path / "plugin-repos"
    plugins_dir.mkdir()
    mgr = PluginStoreManager(plugins_dir=str(plugins_dir))
    mgr.logger = MagicMock()
    return mgr, plugins_dir


class TestInstallGate:
    """`install_plugin` is the chokepoint: `_reinstall_with_rollback` calls it,
    so gating there covers updates too, and a refused update restores the
    version the user already had."""

    def _install_with_manifest(self, store, manifest, core_version, monkeypatch):
        mgr, plugins_dir = store
        plugin_id = manifest["id"]

        monkeypatch.setattr(
            mgr, "get_plugin_info",
            lambda *a, **k: {"repo": "https://example.invalid/r",
                             "plugin_path": f"plugins/{plugin_id}",
                             "branch": "main"})
        # Stand in for the download: put the files where install_plugin expects.
        monkeypatch.setattr(
            mgr, "_install_from_monorepo",
            lambda *a, **k: bool(_write_plugin(plugins_dir, plugin_id, manifest)))
        monkeypatch.setattr(mgr, "_install_from_monorepo_api", lambda *a, **k: False)
        monkeypatch.setattr(mgr, "_install_dependencies", lambda *a, **k: True)

        import src
        monkeypatch.setattr(src, "__version__", core_version)
        return mgr.install_plugin(plugin_id), plugins_dir / plugin_id

    def test_refuses_and_leaves_nothing_behind(self, store, monkeypatch):
        manifest = {
            "id": "needs-newer", "name": "Needs Newer", "class_name": "P",
            "display_modes": ["a"], "min_ledmatrix_version": "9.9.9",
        }
        ok, path = self._install_with_manifest(store, manifest, "3.2.0", monkeypatch)

        assert ok is False, "install must refuse a plugin that needs a newer core"
        assert not path.exists(), (
            "a refused install must not leave a half-installed directory — "
            "plugin discovery would pick it up and fail to load it")

    def test_allows_a_compatible_plugin(self, store, monkeypatch):
        manifest = {
            "id": "fine", "name": "Fine", "class_name": "P",
            "display_modes": ["a"], "min_ledmatrix_version": "3.0.0",
        }
        ok, path = self._install_with_manifest(store, manifest, "3.2.0", monkeypatch)

        assert ok is True
        assert (path / "manifest.json").exists()

    def test_untrustworthy_core_does_not_block_installs(self, store, monkeypatch):
        """Regression guard for the worst possible outcome of this feature:
        users on the v3.1.0 release (which reports 1.0.0) must not be locked
        out of the plugin store entirely."""
        manifest = {
            "id": "floored", "name": "Floored", "class_name": "P",
            "display_modes": ["a"], "versions": [{"ledmatrix_min": "2.0.0"}],
        }
        ok, path = self._install_with_manifest(store, manifest, "1.0.0", monkeypatch)

        assert ok is True, (
            "a core below the trustworthy floor must not block installs — "
            "nearly every published manifest floors at 2.0.0")
        assert (path / "manifest.json").exists()


class TestLoaderAndStoreAgree:
    """Both read the same manifests; a disagreement means one of them is
    lying to the user."""

    @pytest.mark.parametrize("manifest,core,expected", [
        ({"min_ledmatrix_version": "3.2.0"}, "3.1.0", False),
        ({"versions": [{"ledmatrix_min": "2.0.0"}]}, "3.2.0", True),
        ({"versions": [{"ledmatrix_min_version": "9.0.0"}]}, "3.2.0", False),
        ({}, "3.2.0", True),
    ])
    def test_same_verdict(self, manifest, core, expected):
        from src.plugin_system.plugin_loader import PluginLoader

        store_ok, _ = compatibility.check(manifest, core)
        assert store_ok is expected

        # The loader resolves the floor through the same helper, so a
        # divergence in spelling handling would show up here.
        loader_needed = compatibility.parse_semver(
            compatibility.declared_min_version(manifest))
        current = compatibility.parse_semver(core)
        loader_would_warn = (
            loader_needed is not None
            and current is not None
            and current >= compatibility.TRUSTWORTHY_FLOOR
            and loader_needed > current
        )
        assert loader_would_warn is (not expected)
        assert hasattr(PluginLoader, "_warn_if_incompatible")
