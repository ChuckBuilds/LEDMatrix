"""update_plugin must not borrow the enclosing LEDMatrix checkout's remote.

Plugins live in ``plugin-repos/`` inside the LEDMatrix git checkout. For a
plugin installed from a ZIP (no ``.git`` of its own), ``git -C <plugin>``
walks up to the LEDMatrix repository, and ``git config --local --get
remote.origin.url`` answers with LEDMatrix's own URL. update_plugin then tried
to "reinstall" the plugin from the LEDMatrix repository.
"""

import json
import shutil
import subprocess

import pytest

from src.plugin_system.store_manager import PluginStoreManager

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

PLUGIN_ID = "zip-installed"
PARENT_REMOTE = "https://github.com/example/LEDMatrix"


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def store_inside_checkout(tmp_path):
    checkout = tmp_path / "LEDMatrix"
    checkout.mkdir()
    _git("init", "-q", cwd=checkout)
    _git("remote", "add", "origin", PARENT_REMOTE, cwd=checkout)

    plugins_dir = checkout / "plugin-repos"
    plugin_dir = plugins_dir / PLUGIN_ID
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(json.dumps(
        {"id": PLUGIN_ID, "name": "Zip", "version": "1.0.0"}))

    store = PluginStoreManager(
        plugins_dir=str(plugins_dir),
        uninstalled_registry_path=str(tmp_path / "uninstalled.json"))
    return store, plugin_dir


def test_a_plugin_without_its_own_git_has_no_remote(store_inside_checkout, monkeypatch):
    store, plugin_dir = store_inside_checkout
    # The premise: git itself does report the parent's remote here.
    parent_view = subprocess.run(
        ["git", "-C", str(plugin_dir), "config", "--local", "--get", "remote.origin.url"],
        capture_output=True, text=True)
    assert parent_view.stdout.strip() == PARENT_REMOTE

    monkeypatch.setattr(store, "fetch_registry", lambda *a, **k: {"plugins": []})
    monkeypatch.setattr(store, "get_plugin_info", lambda *a, **k: None)
    install_calls = []
    monkeypatch.setattr(store, "install_from_url",
                        lambda *a, **k: install_calls.append((a, k)) or {"success": True})

    assert store.update_plugin(PLUGIN_ID) is False
    assert install_calls == []
