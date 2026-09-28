"""A plugin id from a request or a downloaded manifest cannot escape plugins_dir.

install_from_url joined ``plugins_dir / plugin_id`` -- plugin_id coming from
the request body or the downloaded manifest -- then removed whatever was
there and moved the download onto it, so ``"../x"`` deleted and replaced a
directory beside plugins_dir. _install_plugin_impl did the same when it
renamed the install to the manifest's id.
"""

import json

import pytest

from src.plugin_system.store_manager import PluginStoreManager

MANIFEST = {
    "id": "good-plugin", "name": "Good", "class_name": "P",
    "display_modes": ["good"], "version": "1.0.0",
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    mgr = PluginStoreManager(
        plugins_dir=str(tmp_path / "plugins"),
        uninstalled_registry_path=str(tmp_path / "uninstalled.json"))
    mgr.plugins_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mgr, "_install_dependencies", lambda *a, **k: True)
    mgr.manifest = dict(MANIFEST)

    def fake_clone(repo_url, target, branches):
        target = type(mgr.plugins_dir)(target)
        target.mkdir(parents=True, exist_ok=True)
        (target / "manifest.json").write_text(json.dumps(mgr.manifest))
        (target / "manager.py").write_text("class P: pass\n")
        return "main"

    monkeypatch.setattr(mgr, "_install_via_git", fake_clone)
    return mgr


@pytest.fixture
def victim(tmp_path):
    # A sibling of plugins_dir that a traversal id would target.
    d = tmp_path / "victim"
    d.mkdir()
    (d / "keep.txt").write_text("precious")
    return d


def test_install_from_url_rejects_a_traversal_id_from_the_request(store, victim):
    result = store.install_from_url("https://github.com/x/y", plugin_id="../victim")

    assert result["success"] is False
    assert "Invalid plugin ID" in result["error"]
    assert (victim / "keep.txt").read_text() == "precious"


def test_install_from_url_rejects_a_traversal_id_from_the_manifest(store, victim):
    store.manifest["id"] = "../victim"

    result = store.install_from_url("https://github.com/x/y")

    assert result["success"] is False
    assert (victim / "keep.txt").read_text() == "precious"


def test_install_from_url_still_installs_a_normal_id(store):
    result = store.install_from_url("https://github.com/x/y", plugin_id="ledmatrix-weather")

    assert result["success"] is True
    assert (store.plugins_dir / "ledmatrix-weather" / "manifest.json").exists()


def test_registry_install_rejects_a_traversal_manifest_id(store, victim, monkeypatch):
    store.manifest["id"] = "../victim"
    monkeypatch.setattr(store, "get_plugin_info", lambda *a, **k: {
        "id": "good-plugin", "repo": "https://github.com/x/y"})

    assert store._install_plugin_impl("good-plugin") is False
    assert (victim / "keep.txt").read_text() == "precious"
    assert not (store.plugins_dir / "good-plugin").exists()


def test_install_plugin_rejects_a_traversal_id_before_touching_disk(store, victim, monkeypatch):
    # install_plugin moves an existing plugins_dir / plugin_id aside before
    # installing; for "../victim" that is a directory outside plugins_dir.
    # A later rollback may move it back, so assert nothing was touched at all.
    touched = []
    monkeypatch.setattr(store, "_set_aside", lambda *a: touched.append("set_aside"))
    monkeypatch.setattr(store, "_install_plugin_impl", lambda *a, **k: touched.append("install"))
    assert store.install_plugin("../victim") is False
    assert touched == []
    assert (victim / "keep.txt").read_text() == "precious"
