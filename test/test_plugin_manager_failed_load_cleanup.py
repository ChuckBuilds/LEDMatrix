"""A load that fails after the plugin module was imported must not leave
that module behind.

PluginLoader.load_module() reuses ``plugin_<id>`` from sys.modules. When
instantiation or validate_config() failed, the half-loaded module stayed
there (and in the loader's ``_loaded_modules``), so after the user fixed the
plugin, the next load kept running the old, broken code until a restart.
Font registrations the failed instance made stayed listed too.
"""

import json
import sys
from unittest.mock import MagicMock

import pytest

from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.plugin_state import PluginState

PLUGIN_ID = "failed-load-demo"
MODULE_NAME = "plugin_failed_load_demo"

_BROKEN_INIT = '''
class Demo:
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        raise RuntimeError("broken constructor")
'''

_BAD_CONFIG = '''
class Demo:
    VERSION = "bad-config"
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        pass
    def validate_config(self):
        return False
'''

_FIXED = '''
class Demo:
    VERSION = "fixed"
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        self.enabled = True
'''


@pytest.fixture
def plugin_env(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / PLUGIN_ID
    plugin_dir.mkdir(parents=True)
    manifest = {"id": PLUGIN_ID, "name": "Demo", "class_name": "Demo",
                "entry_point": "manager.py"}
    (plugin_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    manager = PluginManager(plugins_dir=str(plugins_dir))
    manager.font_manager = MagicMock()
    manager.plugin_manifests[PLUGIN_ID] = manifest
    yield manager, plugin_dir
    sys.modules.pop(MODULE_NAME, None)


@pytest.mark.parametrize("first_source", [_BROKEN_INIT, _BAD_CONFIG],
                         ids=["instantiate-fails", "validate-config-fails"])
def test_fixed_plugin_loads_new_code_after_failed_load(plugin_env, first_source):
    pm, plugin_dir = plugin_env
    (plugin_dir / "manager.py").write_text(first_source, encoding="utf-8")

    assert pm.load_plugin(PLUGIN_ID) is False
    assert pm.state_manager.get_state(PLUGIN_ID) == PluginState.ERROR
    assert MODULE_NAME not in sys.modules
    assert PLUGIN_ID not in pm.plugin_loader._loaded_modules
    pm.font_manager.forget_manager_fonts.assert_called_with(PLUGIN_ID)
    pm.font_manager.forget_plugin_fonts.assert_called_with(PLUGIN_ID)

    (plugin_dir / "manager.py").write_text(_FIXED, encoding="utf-8")
    assert pm.load_plugin(PLUGIN_ID) is True
    assert pm.plugins[PLUGIN_ID].VERSION == "fixed"
