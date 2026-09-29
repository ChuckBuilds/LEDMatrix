"""Vegas participation: one declared 'scroll' | 'pause' | 'exclude' per plugin.

Before it existed Vegas read two hooks and branched on only part of each:
get_vegas_content_type() == 'none' excluded a plugin (unless it was STATIC),
get_vegas_display_mode() == STATIC paused the scroll for it, and nothing
distinguished SCROLL from FIXED_SEGMENT. These tests pin the derivation from
those hooks to exactly what the old StreamManager did, so every existing
plugin keeps its behaviour, and cover the new ways to declare it.
"""

import logging
import os
import warnings
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

os.environ.setdefault("EMULATOR", "true")

from src import deprecation
from src.plugin_system import base_plugin
from src.plugin_system.base_plugin import (
    VEGAS_PARTICIPATION_VALUES, BasePlugin, VegasDisplayMode,
    legacy_vegas_participation, resolve_vegas_participation,
)
from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.stream_manager import StreamManager
from test._api_v3_test_helpers import (  # noqa: F401 - fixtures
    api_v3_client, api_v3_module,
)

_MISSING = object()


class _Raises:
    """A hook that raises ``exc`` when called."""

    def __init__(self, exc):
        self.exc = exc


def _duck(display_mode=_MISSING, content_type=_MISSING, **attrs):
    """A plugin that is not a BasePlugin: only the hooks it is given."""
    ns = SimpleNamespace(enabled=True, **attrs)
    for name, value in (('get_vegas_display_mode', display_mode),
                        ('get_vegas_content_type', content_type)):
        if value is _MISSING:
            continue
        if isinstance(value, _Raises):
            def hook(exc=value.exc):
                raise exc
        else:
            def hook(value=value):
                return value
        setattr(ns, name, hook)
    return ns


class _Plugin(BasePlugin):
    """A real BasePlugin; class attributes override the legacy hooks."""

    content_type = None
    display_mode = None

    def update(self):
        pass

    def display(self, force_clear=False):
        pass

    def get_vegas_content_type(self):
        if self.content_type is None:
            return super().get_vegas_content_type()
        return self.content_type

    def get_vegas_display_mode(self):
        if self.display_mode is None:
            return super().get_vegas_display_mode()
        return self.display_mode


def _plugin(config=None, manifest=None, cls=_Plugin, plugin_id='demo', **overrides):
    manifests = {plugin_id: manifest} if manifest is not None else {}
    plugin_manager = SimpleNamespace(plugin_manifests=manifests, plugins={})
    if overrides:
        cls = type('Custom', (cls,), overrides)
    return cls(plugin_id, dict(config or {}), MagicMock(), MagicMock(), plugin_manager)


@pytest.fixture(autouse=True)
def _quiet_once_sets(monkeypatch):
    """Each test sees first-time warnings afresh."""
    monkeypatch.setattr(deprecation, '_warned', set())
    monkeypatch.setattr(base_plugin, '_vegas_warned', set())


# ---------------------------------------------------------------------------
# What the StreamManager on origin/main (6047eb5e) decided, copied verbatim in
# substance, so the derivation is checked against the old code rather than
# against a restatement of the new one.
# ---------------------------------------------------------------------------

def _old_included(plugin):
    content_type = 'static'                      # PluginAdapter.get_content_type
    if hasattr(plugin, 'get_vegas_content_type'):
        try:
            content_type = plugin.get_vegas_content_type()
        except (AttributeError, TypeError, ValueError):
            pass
    display_mode = VegasDisplayMode.FIXED_SEGMENT  # _refresh_plugin_list
    try:
        display_mode = plugin.get_vegas_display_mode()
    except Exception:
        pass
    return content_type != 'none' or display_mode == VegasDisplayMode.STATIC


def _old_paused(plugin):                          # is_static_plugin
    try:
        return plugin.get_vegas_display_mode() == VegasDisplayMode.STATIC
    except Exception:
        return False


DISPLAY_MODES = [
    _MISSING, VegasDisplayMode.SCROLL, VegasDisplayMode.FIXED_SEGMENT,
    VegasDisplayMode.STATIC, None, 'static', 'scroll', _Raises(AttributeError('x')),
]
CONTENT_TYPES = [
    _MISSING, 'multi', 'static', 'single', 'none', None, _Raises(ValueError('x')),
]


def _label(value):
    if value is _MISSING:
        return 'missing'
    if isinstance(value, _Raises):
        return f'raises-{type(value.exc).__name__}'
    return repr(value)


class TestLegacyDerivation:
    @pytest.mark.parametrize('display_mode', DISPLAY_MODES, ids=_label)
    @pytest.mark.parametrize('content_type', CONTENT_TYPES, ids=_label)
    def test_matches_what_the_old_stream_manager_did(self, display_mode, content_type):
        plugin = _duck(display_mode, content_type)
        participation = legacy_vegas_participation(plugin)
        assert (participation != 'exclude') == _old_included(plugin)
        assert (participation == 'pause') == _old_paused(plugin)

    @pytest.mark.parametrize('display_mode, content_type, expected', [
        (VegasDisplayMode.STATIC, 'multi', 'pause'),
        (VegasDisplayMode.STATIC, 'none', 'pause'),   # STATIC beats 'none'
        (VegasDisplayMode.SCROLL, 'none', 'exclude'),
        (VegasDisplayMode.FIXED_SEGMENT, 'none', 'exclude'),
        (VegasDisplayMode.SCROLL, 'multi', 'scroll'),
        (VegasDisplayMode.FIXED_SEGMENT, 'static', 'scroll'),  # never told apart
        (VegasDisplayMode.FIXED_SEGMENT, 'single', 'scroll'),
        ('static', 'multi', 'scroll'),     # only the enum member ever paused
    ])
    def test_the_table(self, display_mode, content_type, expected):
        assert legacy_vegas_participation(_duck(display_mode, content_type)) == expected

    @pytest.mark.parametrize('vegas_mode, content_type, expected', [
        (None, None, 'scroll'),            # BasePlugin defaults: 'static' / FIXED
        (None, 'multi', 'scroll'),
        (None, 'none', 'exclude'),
        ('scroll', None, 'scroll'),
        ('fixed', 'multi', 'scroll'),
        ('static', None, 'pause'),
        ('static', 'none', 'pause'),
        ('bogus', 'none', 'exclude'),      # an invalid vegas_mode is ignored
    ])
    def test_base_plugin_defaults(self, vegas_mode, content_type, expected):
        config = {} if vegas_mode is None else {'vegas_mode': vegas_mode}
        plugin = _plugin(config, content_type=content_type)
        assert plugin.get_vegas_participation() == expected
        assert resolve_vegas_participation(plugin) == expected

    def test_a_magicmock_plugin_scrolls(self):
        # Test doubles all over the suite are MagicMocks; they scrolled before.
        assert resolve_vegas_participation(MagicMock(), 'mock') == 'scroll'


class TestDeclaredParticipation:
    @pytest.mark.parametrize('value', VEGAS_PARTICIPATION_VALUES)
    def test_user_config_wins_over_manifest_and_hooks(self, value):
        plugin = _plugin({'vegas_participation': value, 'vegas_mode': 'static'},
                         manifest={'vegas_participation': 'exclude' if value != 'exclude'
                                   else 'scroll'},
                         content_type='none')
        assert plugin.get_vegas_participation() == value
        assert resolve_vegas_participation(plugin) == value

    @pytest.mark.parametrize('value', VEGAS_PARTICIPATION_VALUES)
    def test_manifest_wins_over_hooks(self, value):
        hooks_say = 'pause' if value != 'pause' else 'scroll'
        plugin = _plugin({'vegas_mode': 'static'} if hooks_say == 'pause' else {},
                         manifest={'vegas_participation': value})
        assert legacy_vegas_participation(plugin) == hooks_say
        assert plugin.get_vegas_participation() == value

    def test_case_and_whitespace_are_ignored(self):
        assert _plugin({'vegas_participation': ' Pause '}).get_vegas_participation() == 'pause'

    @pytest.mark.parametrize('unset', [None, '', '   '])
    def test_empty_config_value_is_no_setting(self, unset):
        plugin = _plugin({'vegas_participation': unset, 'vegas_mode': 'static'})
        assert plugin.get_vegas_participation() == 'pause'

    def test_invalid_config_value_is_ignored_and_logged_once(self, caplog):
        plugin = _plugin({'vegas_participation': 'fixed', 'vegas_mode': 'static'})
        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                assert resolve_vegas_participation(plugin) == 'pause'
        warned = [r for r in caplog.records if 'Invalid vegas_participation' in r.getMessage()]
        assert len(warned) == 1

    def test_invalid_manifest_value_is_ignored(self, caplog):
        plugin = _plugin({}, manifest={'vegas_participation': 'sometimes'}, content_type='none')
        with caplog.at_level(logging.WARNING):
            assert plugin.get_vegas_participation() == 'exclude'
            assert plugin.get_vegas_participation() == 'exclude'
        assert sum('manifest vegas_participation' in r.getMessage()
                   for r in caplog.records) == 1

    def test_a_plugin_manager_without_manifests_is_fine(self):
        plugin = _Plugin('demo', {}, MagicMock(), MagicMock(), None)
        assert plugin.get_vegas_participation() == 'scroll'

    def test_an_override_is_used(self):
        plugin = _plugin(get_vegas_participation=lambda self: 'pause')
        assert resolve_vegas_participation(plugin) == 'pause'

    def test_the_user_setting_beats_an_override(self):
        plugin = _plugin({'vegas_participation': 'exclude'},
                         get_vegas_participation=lambda self: 'pause')
        assert resolve_vegas_participation(plugin) == 'exclude'

    def test_an_override_returning_junk_falls_back_to_the_hooks(self, caplog):
        plugin = _plugin({'vegas_mode': 'static'},
                         get_vegas_participation=lambda self: 'fixed')
        with caplog.at_level(logging.WARNING):
            assert resolve_vegas_participation(plugin) == 'pause'

    def test_an_override_that_raises_falls_back_to_the_hooks(self):
        def boom(self):
            raise RuntimeError('broken')
        plugin = _plugin(content_type='none', get_vegas_participation=boom)
        assert resolve_vegas_participation(plugin) == 'exclude'


# ---------------------------------------------------------------------------
# StreamManager decides through participation
# ---------------------------------------------------------------------------

def _stream(plugins):
    adapter = MagicMock()
    adapter.get_content.return_value = [Image.new('RGB', (20, 8))]
    return StreamManager(VegasModeConfig(), SimpleNamespace(plugins=plugins), adapter), adapter


class TestStreamManager:
    def _plugins(self):
        return {
            'scroller': _plugin(plugin_id='scroller', content_type='multi'),
            'pauser': _plugin({'vegas_mode': 'static'}, plugin_id='pauser'),
            'hidden': _plugin(plugin_id='hidden', content_type='none'),
            'declared': _plugin({'vegas_participation': 'pause'}, plugin_id='declared'),
            'opted_out': _plugin({'vegas_participation': 'exclude'}, plugin_id='opted_out',
                                 content_type='multi'),
        }

    def test_refresh_keeps_scroll_and_pause_and_drops_exclude(self):
        stream, _ = _stream(self._plugins())
        stream._refresh_plugin_list()
        assert sorted(stream._ordered_plugins) == ['declared', 'pauser', 'scroller']

    def test_only_pause_plugins_are_static(self):
        stream, _ = _stream(self._plugins())
        assert {pid: stream.is_static_plugin(pid) for pid in self._plugins()} == {
            'scroller': False, 'pauser': True, 'hidden': False,
            'declared': True, 'opted_out': False}
        assert stream.is_static_plugin('not-loaded') is False

    def test_swap_mode_fetch_makes_a_placeholder_for_pause(self):
        stream, adapter = _stream(self._plugins())
        pause = stream._fetch_plugin_content('declared')
        assert pause.display_mode == VegasDisplayMode.STATIC and pause.images == []
        adapter.get_content.assert_not_called()
        scroll = stream._fetch_plugin_content('scroller')
        assert scroll.display_mode == VegasDisplayMode.SCROLL and len(scroll.images) == 1

    def test_continuous_mode_group_leaves_pause_plugins_unfetched(self):
        plugins = self._plugins()
        stream, adapter = _stream(plugins)
        group = dict(stream.take_next_group(count=10))
        assert group['declared'] == [] and group['pauser'] == []
        assert len(group['scroller']) == 1
        assert 'hidden' not in group and 'opted_out' not in group
        fetched = {call.args[1] for call in adapter.get_content.call_args_list}
        assert fetched == {'scroller'}

    def test_a_user_can_make_a_static_plugin_scroll(self):
        plugin = _plugin({'vegas_mode': 'static', 'vegas_participation': 'scroll'},
                         plugin_id='p')
        stream, _ = _stream({'p': plugin})
        assert stream.is_static_plugin('p') is False
        assert stream._fetch_plugin_content('p').display_mode == VegasDisplayMode.SCROLL

    @pytest.mark.parametrize('display_mode', DISPLAY_MODES, ids=_label)
    @pytest.mark.parametrize('content_type', CONTENT_TYPES, ids=_label)
    def test_decisions_match_the_old_stream_manager(self, display_mode, content_type):
        plugin = _duck(display_mode, content_type)
        stream, _ = _stream({'p': plugin})
        stream._refresh_plugin_list()
        assert ('p' in stream._ordered_plugins) == _old_included(plugin)
        assert stream.is_static_plugin('p') == _old_paused(plugin)
        segment = stream._fetch_plugin_content('p')
        assert (segment.display_mode == VegasDisplayMode.STATIC) == _old_paused(plugin)

    def test_a_display_mode_hook_that_raises_no_longer_drops_the_swap_segment(self):
        # The one intended difference: swap mode's fetch let anything but
        # AttributeError/TypeError from get_vegas_display_mode() abort the
        # fetch, while every other decision point treated it as "not STATIC".
        # All three now agree: the plugin scrolls.
        stream, _ = _stream({'p': _duck(_Raises(RuntimeError('x')), 'multi')})
        assert stream._fetch_plugin_content('p').display_mode == VegasDisplayMode.SCROLL


# ---------------------------------------------------------------------------
# Deprecations (removal in 3.9.0)
# ---------------------------------------------------------------------------

class TestDeprecations:
    @pytest.mark.parametrize('name', ['get_supported_vegas_modes', 'get_vegas_segment_width'])
    def test_marked_for_3_9_0(self, name):
        marker = getattr(getattr(BasePlugin, name), '__deprecated__', '')
        assert 'LEDMatrix 3.9.0' in marker

    def test_the_kept_hooks_are_not_deprecated(self):
        for name in ('get_vegas_content', 'get_vegas_render_width',
                     'get_vegas_priority_weight', 'get_vegas_participation',
                     'get_vegas_content_type', 'get_vegas_display_mode'):
            assert not hasattr(getattr(BasePlugin, name), '__deprecated__'), name

    def test_calling_them_still_works_and_warns_once(self, caplog):
        plugin = _plugin({'vegas_panel_count': 2}, content_type='multi')
        with warnings.catch_warnings(record=True) as caught, \
                caplog.at_level(logging.WARNING):
            warnings.simplefilter('always')
            assert plugin.get_supported_vegas_modes() == [
                VegasDisplayMode.SCROLL, VegasDisplayMode.FIXED_SEGMENT]
            assert plugin.get_supported_vegas_modes()
            assert plugin.get_vegas_segment_width() == 2
        messages = [str(w.message) for w in caught]
        assert len(messages) == 2
        assert all('3.9.0' in m for m in messages)

    def test_vegas_panel_count_warns_once_per_plugin(self, caplog):
        first = _plugin({'vegas_panel_count': 2}, plugin_id='a')
        second = _plugin({'vegas_panel_count': 3}, plugin_id='b')
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            for _ in range(3):
                resolve_vegas_participation(first, 'a')
                resolve_vegas_participation(second, 'b')
        messages = [str(w.message) for w in caught]
        assert len(messages) == 2
        assert "plugin 'a'" in messages[0] and "plugin 'b'" in messages[1]
        assert all('removed in LEDMatrix 3.9.0' in m for m in messages)

    def test_no_warning_without_the_key(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            resolve_vegas_participation(_plugin(), 'demo')
        assert caught == []

    def test_warn_deprecated_is_once_per_key(self, caplog):
        with warnings.catch_warnings(record=True) as caught, \
                caplog.at_level(logging.WARNING):
            warnings.simplefilter('always')
            assert deprecation.warn_deprecated('Thing', '9.9.9', 'use other') is True
            assert deprecation.warn_deprecated('Thing', '9.9.9') is False
            assert deprecation.warn_deprecated('Thing', '9.9.9', once_key='k2') is True
        assert [str(w.message) for w in caught] == [
            'Thing is deprecated and will be removed in LEDMatrix 9.9.9; use other',
            'Thing is deprecated and will be removed in LEDMatrix 9.9.9',
        ]
        assert caught[0].category is DeprecationWarning


# ---------------------------------------------------------------------------
# Config schema and web API
# ---------------------------------------------------------------------------

class TestSchema:
    def test_core_property_is_an_enum_without_a_default(self):
        from src.plugin_system.schema_manager import (
            CORE_PLUGIN_PROPERTIES, CORE_VEGAS_TUNING_KEYS, plugin_config_defaults)
        prop = CORE_PLUGIN_PROPERTIES['vegas_participation']
        assert prop['enum'] == list(VEGAS_PARTICIPATION_VALUES)
        # A default would be written into every plugin's config and override
        # what the plugin declares.
        assert 'default' not in prop
        assert 'vegas_participation' in CORE_VEGAS_TUNING_KEYS
        defaults = plugin_config_defaults({'type': 'object', 'properties': {}})
        assert 'vegas_participation' not in defaults

    def test_validation_accepts_the_values_and_rejects_others(self):
        from src.plugin_system.schema_manager import SchemaManager
        manager = SchemaManager(plugins_dir='.')
        schema = {'type': 'object', 'additionalProperties': False,
                  'properties': {'city': {'type': 'string'}}}
        for value in VEGAS_PARTICIPATION_VALUES:
            ok, errors = manager.validate_config_against_schema(
                {'vegas_participation': value}, schema, 'demo')
            assert ok, errors
        ok, _ = manager.validate_config_against_schema(
            {'vegas_participation': 'fixed'}, schema, 'demo')
        assert not ok

    def test_manifest_schema_declares_it(self):
        import json
        from pathlib import Path
        schema = json.loads((Path(__file__).resolve().parent.parent / 'schema'
                             / 'manifest_schema.json').read_text(encoding='utf-8'))
        assert schema['properties']['vegas_participation']['enum'] == list(
            VEGAS_PARTICIPATION_VALUES)


class TestInstalledPluginsApi:
    @pytest.fixture
    def installed(self, api_v3_module, api_v3_client, tmp_path):
        def _get(instance, config):
            api = api_v3_module.api_v3
            info = {'id': 'demo', 'name': 'Demo', 'version': '1.0.0', 'loaded': True}
            api.plugin_manager.plugins_dir = str(tmp_path)
            api.plugin_manager.get_all_plugin_info = MagicMock(return_value=[info])
            api.plugin_manager.get_plugin = MagicMock(return_value=instance)
            api.plugin_store_manager.get_registry_info = MagicMock(return_value=None)
            api.config_manager.load_config = MagicMock(return_value={'demo': config})
            response = api_v3_client.get('/api/v3/plugins/installed')
            assert response.status_code == 200
            return [p for p in response.get_json()['data']['plugins']
                    if p['id'] == 'demo'][0]
        return _get

    def test_a_loaded_plugin_reports_its_participation(self, installed):
        plugin = _plugin({'vegas_mode': 'static'})
        assert installed(plugin, plugin.config)['vegas_participation'] == 'pause'

    def test_an_unloaded_plugin_reports_only_the_user_setting(self, installed):
        assert installed(None, {'vegas_participation': 'exclude'})[
            'vegas_participation'] == 'exclude'
        assert installed(None, {})['vegas_participation'] is None

