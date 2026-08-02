"""A failed (re)install must not destroy the working plugin it replaced.

`_install_plugin_impl` deletes the existing plugin directory *before* it
downloads anything, so any failure after that point used to leave the user with
nothing. The update path was protected — `_reinstall_with_rollback` renames the
old copy aside first — but a direct `install_plugin` was not, and the
compatibility gate added a new way to fail late: a plugin whose declared floor
exceeds the running core is now refused *after* the old copy is already gone.

Concretely, without the wrapper: a user on core 3.1.0 with a working
hockey-scoreboard clicks Install; the new manifest floors at 3.2.0; the gate
refuses; the plugin they had is deleted. Floors are hand-written and can be
over-declared, so this could remove a plugin that was working fine.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.plugin_system.store_manager import PluginStoreManager


@pytest.fixture
def store(tmp_path):
    plugins_dir = tmp_path / "plugin-repos"
    plugins_dir.mkdir()
    mgr = PluginStoreManager(plugins_dir=str(plugins_dir))
    mgr.logger = MagicMock()
    return mgr, plugins_dir


def _existing_install(plugins_dir: Path, plugin_id: str, marker: str) -> Path:
    path = plugins_dir / plugin_id
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(
        json.dumps({"id": plugin_id, "name": plugin_id, "class_name": "P",
                    "display_modes": ["a"], "version": "1.0.0"}),
        encoding="utf-8")
    (path / "marker.txt").write_text(marker, encoding="utf-8")
    return path


class TestFailedInstallPreservesPrevious:
    def test_failed_install_restores_the_old_copy(self, store, monkeypatch):
        mgr, plugins_dir = store
        path = _existing_install(plugins_dir, "hockey-scoreboard", "the-original")

        monkeypatch.setattr(mgr, "_install_plugin_impl", lambda *a, **k: False)

        assert mgr.install_plugin("hockey-scoreboard") is False
        assert path.exists(), "the previous install must be restored"
        assert (path / "marker.txt").read_text() == "the-original"

    def test_raising_install_restores_and_reraises(self, store, monkeypatch):
        mgr, plugins_dir = store
        path = _existing_install(plugins_dir, "hockey-scoreboard", "the-original")

        def boom(*a, **k):
            raise RuntimeError("network died mid-install")

        monkeypatch.setattr(mgr, "_install_plugin_impl", boom)

        with pytest.raises(RuntimeError):
            mgr.install_plugin("hockey-scoreboard")
        assert path.exists()
        assert (path / "marker.txt").read_text() == "the-original"

    def test_successful_install_clears_the_backup(self, store, monkeypatch):
        mgr, plugins_dir = store
        _existing_install(plugins_dir, "hockey-scoreboard", "the-original")

        def succeed(plugin_id, branch=None):
            _existing_install(plugins_dir, plugin_id, "the-new-one")
            return True

        monkeypatch.setattr(mgr, "_install_plugin_impl", succeed)

        assert mgr.install_plugin("hockey-scoreboard") is True
        assert (plugins_dir / "hockey-scoreboard" / "marker.txt").read_text() == "the-new-one"
        leftovers = [p.name for p in plugins_dir.iterdir() if "backup" in p.name]
        assert not leftovers, f"backup left behind: {leftovers}"

    def test_backup_name_is_invisible_to_plugin_discovery(self, store, monkeypatch):
        """A backup that discovery can see becomes a duplicate plugin entry;
        the marker '.standalone-backup-' is what makes it skip."""
        mgr, plugins_dir = store
        _existing_install(plugins_dir, "hockey-scoreboard", "the-original")

        seen = {}

        def capture(plugin_id, branch=None):
            seen["dirs"] = sorted(p.name for p in plugins_dir.iterdir())
            return False

        monkeypatch.setattr(mgr, "_install_plugin_impl", capture)
        mgr.install_plugin("hockey-scoreboard")

        backups = [d for d in seen["dirs"] if d != "hockey-scoreboard"]
        assert backups, "expected the old copy to be set aside during install"
        for name in backups:
            assert ".standalone-backup-" in name, (
                f"{name} would be picked up by "
                "plugin_manager._scan_directory_for_plugins as a real plugin")

    def test_fresh_install_is_a_pass_through(self, store, monkeypatch):
        """Nothing installed means nothing to protect; don't create stray dirs."""
        mgr, plugins_dir = store
        calls = []
        monkeypatch.setattr(
            mgr, "_install_plugin_impl",
            lambda *a, **k: calls.append(a) or True)

        assert mgr.install_plugin("brand-new") is True
        assert calls, "the implementation must still be called"
        assert list(plugins_dir.iterdir()) == []

    def test_stale_backup_from_a_crash_does_not_block(self, store, monkeypatch):
        mgr, plugins_dir = store
        _existing_install(plugins_dir, "hockey-scoreboard", "the-original")
        stale = plugins_dir / "hockey-scoreboard.standalone-backup-preinstall"
        stale.mkdir()
        (stale / "junk.txt").write_text("from a previous crash", encoding="utf-8")

        monkeypatch.setattr(mgr, "_install_plugin_impl", lambda *a, **k: False)

        assert mgr.install_plugin("hockey-scoreboard") is False
        assert (plugins_dir / "hockey-scoreboard" / "marker.txt").read_text() == "the-original"


class TestUpdatePathStillWorks:
    def test_reinstall_with_rollback_is_not_double_wrapped(self, store, monkeypatch):
        """_reinstall_with_rollback moves the plugin aside itself, so by the
        time install_plugin runs there is nothing at the original path and the
        wrapper must be a pass-through rather than staging a second backup."""
        mgr, plugins_dir = store
        path = _existing_install(plugins_dir, "hockey-scoreboard", "the-original")

        observed = {}

        def impl(plugin_id, branch=None):
            observed["dirs"] = sorted(p.name for p in plugins_dir.iterdir())
            return False

        monkeypatch.setattr(mgr, "_install_plugin_impl", impl)

        assert mgr._reinstall_with_rollback("hockey-scoreboard", path) is False
        # Exactly one aside directory existed during the attempt — rollback's.
        assert observed["dirs"] == ["hockey-scoreboard.standalone-backup-migrating"]
        # And the user still has their plugin.
        assert (plugins_dir / "hockey-scoreboard" / "marker.txt").read_text() == "the-original"
