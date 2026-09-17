"""Every "is this top-level key a plugin?" decision uses src/core_config_keys.py.

#589 moved plugin-state reconciliation onto the shared list, but three private
copies stayed behind and did not know about auto_update, dim_schedule, sync,
location, target_fps, ...:

- StartupValidator warned "Plugin 'auto_update' is enabled but not found in
  plugins directory" on every display start with auto-update or a dim
  schedule switched on;
- SchemaManager.detect_config_key_collisions let a plugin id collide with
  those sections silently;
- ConfigManager.cleanup_orphaned_plugin_configs deleted display, schedule,
  auto_update, ... as orphans, and validate_all_plugin_configs validated core
  sections as plugins.
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config_manager import ConfigManager
from src.core_config_keys import CORE_CONFIG_KEYS, CORE_SECRETS_KEYS
from src.plugin_system.schema_manager import SchemaManager
from src.startup_validator import StartupValidator

REPO = Path(__file__).resolve().parent.parent


class TestStartupValidator:
    def test_enabled_core_sections_are_not_missing_plugins(self):
        config = {
            'display': {'hardware': {}},
            'auto_update': {'enabled': True},
            'dim_schedule': {'enabled': True},
            'schedule': {'enabled': True},
            'gone-plugin': {'enabled': True},
        }
        cm = MagicMock()
        cm.get_config.return_value = config
        pm = MagicMock()
        pm.discover_plugins.return_value = []
        validator = StartupValidator(cm, plugin_manager=pm, cache_manager=MagicMock())
        validator.warnings = []
        validator.errors = []
        validator._validate_plugins()
        missing = [w for w in validator.warnings if 'not found in plugins directory' in w]
        assert missing == ["Plugin 'gone-plugin' is enabled but not found in plugins directory"]


class TestReservedPluginIds:
    @pytest.mark.parametrize('key', sorted(CORE_CONFIG_KEYS))
    def test_every_core_key_is_reserved(self, key):
        collisions = SchemaManager().detect_config_key_collisions([key])
        assert [c['type'] for c in collisions] == ['reserved_key_collision']

    def test_an_ordinary_plugin_id_is_not(self):
        assert SchemaManager().detect_config_key_collisions(['clock-simple']) == []


class TestConfigManagerHelpers:
    @pytest.fixture
    def manager(self, tmp_path):
        config = {key: {'enabled': True} for key in ('display', 'schedule', 'auto_update',
                                                      'dim_schedule', 'sync', 'location')}
        config.update({'timezone': 'UTC', 'installed': {'enabled': True},
                       'orphan': {'enabled': True}})
        secrets = {'github': {'token': 't'}, 'youtube': {'api_key': 'k'},
                   'installed': {'api_key': 'a'}, 'orphan': {'api_key': 'o'}}
        (tmp_path / 'config.json').write_text(json.dumps(config))
        (tmp_path / 'secrets.json').write_text(json.dumps(secrets))
        manager = ConfigManager(config_path=str(tmp_path / 'config.json'),
                                secrets_path=str(tmp_path / 'secrets.json'))
        manager.template_path = str(tmp_path / 'no-template.json')
        return manager

    def test_cleanup_removes_only_real_orphans(self, manager, tmp_path):
        removed = manager.cleanup_orphaned_plugin_configs(['installed'])
        assert removed == ['orphan']
        config = json.loads((tmp_path / 'config.json').read_text())
        assert {'display', 'schedule', 'auto_update', 'dim_schedule', 'sync',
                'location', 'timezone', 'installed'} == set(config)
        secrets = json.loads((tmp_path / 'secrets.json').read_text())
        assert set(secrets) == {'github', 'youtube', 'installed'}

    def test_validate_all_skips_core_sections(self, manager):
        schema_manager = MagicMock()
        schema_manager.load_schema.return_value = None
        results = manager.validate_all_plugin_configs(schema_manager)
        assert set(results) == {'installed', 'orphan'}


def test_core_secrets_keys_are_not_core_config_keys():
    assert not (CORE_SECRETS_KEYS & CORE_CONFIG_KEYS)


@pytest.mark.parametrize('relpath', [
    'src/startup_validator.py',
    'src/config_manager.py',
    'src/plugin_system/schema_manager.py',
])
def test_no_private_copy_of_the_list_remains(relpath):
    """The old copies all started with this literal; a new copy likely would too."""
    source = (REPO / relpath).read_text(encoding='utf-8')
    assert not re.search(r"""\[\s*['"]display['"]\s*,\s*['"]schedule['"]""", source), \
        f'{relpath} has a private core-key list again; use src/core_config_keys.py'
