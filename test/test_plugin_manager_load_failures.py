"""PluginManager failure paths that used to leave the manager inconsistent.

- load_plugin registered the instance in ``self.plugins`` before calling
  ``on_enable()``. When on_enable raised, the plugin stayed registered in
  ERROR state, so the next load_plugin returned True ("already loaded") for
  a plugin that never ran.
- get_plugin_info called ``plugin.get_info()`` unguarded, so one plugin
  raising broke /api/v3/plugins/installed for every plugin.
"""

import logging
from unittest.mock import MagicMock

import pytest

from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.plugin_state import PluginState


class _Plugin:
    def __init__(self, fail_enable=False, fail_info=False):
        self.fail_enable = fail_enable
        self.fail_info = fail_info
        self.enabled_calls = 0

    def on_enable(self):
        self.enabled_calls += 1
        if self.fail_enable:
            raise RuntimeError("on_enable blew up")

    def get_info(self):
        if self.fail_info:
            raise RuntimeError("get_info blew up")
        return {"ok": True}


@pytest.fixture
def pm(tmp_path):
    plugins_dir = tmp_path / "plugins"
    (plugins_dir / "demo").mkdir(parents=True)
    manager = PluginManager(plugins_dir=str(plugins_dir))
    manager.plugin_manifests["demo"] = {"id": "demo", "name": "Demo"}
    manager.schema_manager = MagicMock()
    manager.schema_manager.get_schema_path.return_value = None
    manager.plugin_loader = MagicMock()
    manager.plugin_loader.find_plugin_directory.return_value = plugins_dir / "demo"
    return manager


def test_on_enable_failure_unregisters_the_plugin(pm):
    plugin = _Plugin(fail_enable=True)
    pm.plugin_loader.load_plugin.return_value = (plugin, None)

    assert pm.load_plugin("demo") is False
    assert "demo" not in pm.plugins
    assert "demo" not in pm.plugin_last_update
    assert pm.state_manager.get_state("demo") == PluginState.ERROR


def test_load_after_on_enable_failure_retries_instead_of_already_loaded(pm):
    pm.plugin_loader.load_plugin.return_value = (_Plugin(fail_enable=True), None)
    assert pm.load_plugin("demo") is False

    fixed = _Plugin()
    pm.plugin_loader.load_plugin.return_value = (fixed, None)
    assert pm.load_plugin("demo") is True
    assert pm.plugins["demo"] is fixed
    assert fixed.enabled_calls == 1
    assert pm.state_manager.get_state("demo") == PluginState.ENABLED


def test_get_info_failure_does_not_break_the_listing(pm, caplog):
    pm.plugin_manifests["good"] = {"id": "good", "name": "Good"}
    pm.plugins["demo"] = _Plugin(fail_info=True)
    pm.plugins["good"] = _Plugin()

    with caplog.at_level(logging.WARNING):
        infos = {i["id"]: i for i in pm.get_all_plugin_info()}

    assert infos["demo"]["loaded"] is True
    assert infos["demo"]["runtime_info"] == {}
    assert infos["good"]["runtime_info"] == {"ok": True}
    assert any("demo" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)
