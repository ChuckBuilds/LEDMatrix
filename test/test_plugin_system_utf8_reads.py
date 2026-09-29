"""Plugin-system JSON files are read as UTF-8, whatever the locale says.

store_manager (install_from_url's manifest, the secrets file) opened text
files without an encoding, so the platform default applied. (So did the
retired plugin_state.json reader.) A manifest written in UTF-8 with a non-ASCII
name then read as mojibake -- or raised UnicodeDecodeError -- on a host
whose locale encoding is not UTF-8 (Windows' cp1252; a Pi with LANG=C).
On a UTF-8 host these pass either way; they fail on old code where the
default encoding differs.
"""

import json

import pytest

from src.plugin_system.store_manager import PluginStoreManager

NAME = "Météo Á"  # "Á" is C3 81 in UTF-8; 0x81 is undefined in cp1252


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
        manifest = {"id": "meteo", "name": NAME, "class_name": "P",
                    "display_modes": ["meteo"], "version": "1.0.0"}
        (target / "manifest.json").write_bytes(
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
        (target / "manager.py").write_text("class P: pass\n")
        return "main"

    monkeypatch.setattr(mgr, "_install_via_git", fake_clone)
    return mgr


def test_install_from_url_reads_a_utf8_manifest(store):
    result = store.install_from_url("https://github.com/x/y")
    assert result["success"] is True
    assert result["name"] == NAME
    written = json.loads(
        (store.plugins_dir / "meteo" / "manifest.json").read_bytes().decode("utf-8"))
    assert written["name"] == NAME
