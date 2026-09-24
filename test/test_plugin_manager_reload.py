"""reload_plugin re-reads the manifest from the plugin's actual directory.

It read ``plugins_dir / plugin_id / manifest.json``, but a plugin directory's
name need not be the id its manifest declares -- discovery maps ids to
directories for exactly that reason. For such a plugin the path did not exist,
the re-read was skipped silently, and the reload kept the stale manifest.
"""

import json

import pytest

from src.plugin_system.plugin_manager import PluginManager


@pytest.fixture
def pm_with_renamed_dir(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "stock-ticker-v2"
    plugin_dir.mkdir(parents=True)
    manifest_path = plugin_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"id": "stocks", "version": "1.0.0"}))
    pm = PluginManager(plugins_dir=str(plugins_dir))
    assert pm.discover_plugins() == ["stocks"]
    return pm, manifest_path


def test_reload_picks_up_an_edited_manifest(pm_with_renamed_dir, monkeypatch):
    pm, manifest_path = pm_with_renamed_dir
    manifest_path.write_text(json.dumps({"id": "stocks", "version": "2.0.0"}))
    loaded = []
    monkeypatch.setattr(pm, "load_plugin", lambda pid: loaded.append(pid) or True)

    assert pm.reload_plugin("stocks") is True
    assert pm.plugin_manifests["stocks"]["version"] == "2.0.0"
    assert loaded == ["stocks"]
