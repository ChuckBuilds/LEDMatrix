"""install_from_url keeps the installed copy until the new one is in place.

It deleted the existing plugin directory and then moved the download over it,
with no way back: a move that failed part-way left the user with no plugin at
all. And it did so outside the per-plugin reinstall lock install_plugin()
takes, so two overlapping installs of one id could interleave.
"""

import json
import threading

import pytest

from src.plugin_system import store_manager as store_module
from src.plugin_system.plugin_dirs import BACKUP_MARKER
from src.plugin_system.store_manager import PluginStoreManager

MANIFEST = {
    "id": "demo", "name": "Demo", "class_name": "P",
    "display_modes": ["demo"], "version": "2.0.0",
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    mgr = PluginStoreManager(
        plugins_dir=str(tmp_path / "plugins"),
        uninstalled_registry_path=str(tmp_path / "uninstalled.json"))
    mgr.plugins_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mgr, "_install_dependencies", lambda *a, **k: True)

    def fake_clone(repo_url, target, branches):
        target = type(mgr.plugins_dir)(target)
        target.mkdir(parents=True, exist_ok=True)
        (target / "manifest.json").write_text(json.dumps(MANIFEST))
        (target / "manager.py").write_text("NEW = True\n")
        return "main"

    monkeypatch.setattr(mgr, "_install_via_git", fake_clone)
    old = mgr.plugins_dir / "demo"
    old.mkdir()
    (old / "manager.py").write_text("OLD = True\n")
    return mgr


def _leftover_backups(store):
    return [p for p in store.plugins_dir.iterdir() if BACKUP_MARKER in p.name]


def test_failed_move_restores_the_existing_install(store, monkeypatch):
    def broken_move(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(store_module.shutil, "move", broken_move)

    result = store.install_from_url("https://github.com/x/y")

    assert result["success"] is False
    assert (store.plugins_dir / "demo" / "manager.py").read_text() == "OLD = True\n"
    assert _leftover_backups(store) == []


def test_successful_replace_leaves_no_backup(store):
    result = store.install_from_url("https://github.com/x/y")

    assert result["success"] is True
    assert (store.plugins_dir / "demo" / "manager.py").read_text() == "NEW = True\n"
    assert _leftover_backups(store) == []


def test_replace_happens_under_the_reinstall_lock(store, monkeypatch):
    real_move = store_module.shutil.move
    seen = {}

    def spying_move(src, dst):
        lock = store._get_reinstall_lock("demo")
        probe = threading.Thread(
            target=lambda: seen.setdefault("free", lock.acquire(blocking=False)))
        probe.start()
        probe.join()
        return real_move(src, dst)

    monkeypatch.setattr(store_module.shutil, "move", spying_move)

    assert store.install_from_url("https://github.com/x/y")["success"] is True
    assert seen == {"free": False}
