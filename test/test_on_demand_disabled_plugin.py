"""On-demand for a plugin that is installed but disabled in config.

The display process only loads enabled plugins, so a request for a disabled
one -- "Preview on display" offers it on every plugin's config page, with a
note that the plugin will be enabled for the preview -- failed with
"invalid-mode". Nothing loaded it short of a restart, and the on-demand
route no longer restarts the service.

The display now loads such a plugin live for the session (force_enabled, so
config.json keeps saying disabled) and the main loop unloads it once
on-demand moves off it: a stop, an expiry, or a request for another plugin.

Also here: a stop sent after a failed request clears the error instead of
leaving status 'error' published until the state ages out.
"""

import time
from unittest.mock import MagicMock

import pytest

from src.plugin_system.plugin_manager import PluginManager
from src.plugin_system.plugin_state import PluginState


def _make_plugin(modes):
    plugin = MagicMock()
    plugin.modes = list(modes)
    return plugin


@pytest.fixture
def controller(test_display_controller):
    """An idle controller running 'clock', with 'preview-me' installed but disabled."""
    c = test_display_controller
    clock = _make_plugin(['clock'])
    preview = _make_plugin(['preview_a', 'preview_b'])
    instances = {'clock': clock}
    catalogue = {'clock': clock, 'preview-me': preview}

    def load_plugin(plugin_id, force_enabled=False):
        instances[plugin_id] = catalogue[plugin_id]
        return True

    def unload_plugin(plugin_id):
        return instances.pop(plugin_id, None) is not None

    pm = c.plugin_manager
    pm.discovered_plugin_ids.return_value = set(catalogue)
    pm.discover_plugins.return_value = list(catalogue)
    pm.plugin_manifests = {}
    pm.load_plugin = MagicMock(side_effect=load_plugin)
    pm.unload_plugin = MagicMock(side_effect=unload_plugin)
    pm.get_plugin.side_effect = instances.get

    config = {'clock': {'enabled': True}, 'preview-me': {'enabled': False}}
    c.config_service.get_config = lambda: config
    c.config_manager.save_config = MagicMock()
    c.cache_manager.set = MagicMock()
    c.cache_manager.clear_cache = MagicMock()

    c._register_loaded_plugin('clock')
    c.current_mode_index = 0
    c.current_display_mode = 'clock'
    c.test_config = config
    c.test_instances = instances
    return c


def _start(c, plugin_id='preview-me', mode=None, **extra):
    request = {'request_id': 'r-' + plugin_id, 'action': 'start',
               'plugin_id': plugin_id, 'mode': mode or plugin_id}
    request.update(extra)
    c._activate_on_demand(request)


class TestLoadingForOnDemand:
    def test_a_disabled_plugin_is_loaded_and_shown(self, controller):
        _start(controller)

        controller.plugin_manager.load_plugin.assert_called_once_with(
            'preview-me', force_enabled=True)
        assert controller.on_demand_active is True
        assert controller.on_demand_status == 'active'
        assert controller.on_demand_plugin_id == 'preview-me'
        assert controller.current_display_mode == 'preview_a'
        assert controller.plugin_display_modes['preview-me'] == ['preview_a', 'preview_b']

    def test_config_json_is_not_written(self, controller):
        _start(controller)

        controller.config_manager.save_config.assert_not_called()
        assert controller.test_config['preview-me'] == {'enabled': False}

    def test_a_requested_mode_is_honoured(self, controller):
        _start(controller, mode='preview_b')
        assert controller.current_display_mode == 'preview_b'

    def test_an_enabled_plugin_is_not_reloaded(self, controller):
        _start(controller, plugin_id='clock')

        controller.plugin_manager.load_plugin.assert_not_called()
        assert controller.on_demand_active is True
        assert controller._on_demand_loaded_plugins == set()

    def test_a_plugin_that_is_not_installed_is_not_loaded(self, controller):
        _start(controller, plugin_id='uninstalled')

        controller.plugin_manager.load_plugin.assert_not_called()
        assert controller.on_demand_status == 'error'
        assert controller.on_demand_last_error == 'invalid-mode'

    def test_a_plugin_installed_after_startup_is_found_by_rescanning(self, controller):
        controller.plugin_manager.discovered_plugin_ids.return_value = {'clock'}

        _start(controller)

        controller.plugin_manager.discover_plugins.assert_called()
        assert controller.on_demand_active is True


class TestLoadFailures:
    def test_a_failed_load_reports_load_failed(self, controller):
        controller.plugin_manager.load_plugin = MagicMock(return_value=False)

        _start(controller)

        assert controller.on_demand_active is False
        assert controller.on_demand_status == 'error'
        assert controller.on_demand_last_error == 'load-failed'
        assert 'preview_a' not in controller.available_modes
        published = controller.cache_manager.set.call_args_list[-1]
        assert published.args[0] == 'display_on_demand_state'
        assert published.args[1]['status'] == 'error'
        assert published.args[1]['error'] == 'load-failed'

    def test_a_load_that_raises_reports_load_failed(self, controller):
        controller.plugin_manager.load_plugin = MagicMock(side_effect=ImportError('no module'))

        _start(controller)

        assert controller.on_demand_status == 'error'
        assert controller.on_demand_last_error == 'load-failed'

    def test_a_failed_load_leaves_the_rotation_alone(self, controller):
        controller.plugin_manager.load_plugin = MagicMock(return_value=False)

        _start(controller)
        controller._release_on_demand_plugins()

        assert controller.available_modes == ['clock']
        assert controller.current_display_mode == 'clock'
        assert controller._on_demand_loaded_plugins == set()
        controller.plugin_manager.unload_plugin.assert_not_called()

    def test_a_plugin_that_loads_but_has_no_modes_is_unloaded_again(self, controller):
        """Registered, then the activation fails: the release removes it."""
        controller._on_demand_modes_for_plugin = MagicMock(return_value=[])

        _start(controller)
        assert controller.on_demand_last_error == 'no-modes'
        controller._release_on_demand_plugins()

        controller.plugin_manager.unload_plugin.assert_called_once_with('preview-me')
        assert controller.available_modes == ['clock']


class TestReleasingThePlugin:
    def test_it_stays_loaded_while_on_demand_shows_it(self, controller):
        _start(controller)
        controller._release_on_demand_plugins()

        controller.plugin_manager.unload_plugin.assert_not_called()
        assert 'preview_a' in controller.plugin_modes

    def test_a_stop_unloads_it_and_resumes_the_rotation(self, controller):
        _start(controller)
        controller._clear_on_demand(reason='requested-stop')
        # Deferred to the main loop: the stop may be read mid-display().
        controller.plugin_manager.unload_plugin.assert_not_called()

        controller._release_on_demand_plugins()

        controller.plugin_manager.unload_plugin.assert_called_once_with('preview-me')
        assert controller.available_modes == ['clock']
        assert 'preview-me' not in controller.plugin_display_modes
        assert 'preview_a' not in controller.plugin_modes
        assert controller.current_display_mode == 'clock'
        assert controller._on_demand_loaded_plugins == set()
        assert controller.test_config['preview-me'] == {'enabled': False}

    def test_expiry_unloads_it(self, controller):
        _start(controller, duration=30)
        controller.on_demand_expires_at = time.time() - 1
        controller._check_on_demand_expiration()
        controller._release_on_demand_plugins()

        assert controller.on_demand_last_event == 'expired'
        controller.plugin_manager.unload_plugin.assert_called_once_with('preview-me')

    def test_a_request_for_another_plugin_unloads_it(self, controller):
        _start(controller)
        _start(controller, plugin_id='clock')
        controller._release_on_demand_plugins()

        controller.plugin_manager.unload_plugin.assert_called_once_with('preview-me')
        assert controller.on_demand_active is True
        assert controller.on_demand_plugin_id == 'clock'
        assert controller.current_display_mode == 'clock'

    def test_a_failed_request_that_ends_the_session_unloads_it(self, controller):
        _start(controller)
        _start(controller, plugin_id='uninstalled')
        controller._release_on_demand_plugins()

        controller.plugin_manager.unload_plugin.assert_called_once_with('preview-me')
        assert controller.current_display_mode == 'clock'
        assert controller.force_change is True

    def test_a_failed_request_that_ends_the_session_drops_its_saved_copy(self, controller):
        """Otherwise the next restart resumes the session that just ended."""
        _start(controller, plugin_id='clock')
        controller.cache_manager.clear_cache.reset_mock()

        _start(controller, plugin_id='uninstalled')

        controller.cache_manager.clear_cache.assert_called_once_with('display_on_demand_config')

    def test_a_plugin_enabled_during_the_session_stays_loaded(self, controller):
        _start(controller)
        controller.test_config['preview-me'] = {'enabled': True}
        controller._clear_on_demand(reason='requested-stop')
        controller._release_on_demand_plugins()

        controller.plugin_manager.unload_plugin.assert_not_called()
        assert 'preview_a' in controller.available_modes
        assert controller._on_demand_loaded_plugins == set()

    def test_the_main_loop_releases_right_after_its_own_poll(self, controller):
        """A stop read by the main loop unloads before the next screen, not
        one screen later. That poll runs with no display() on the stack."""
        import inspect
        source = inspect.getsource(type(controller).run)
        poll = source.index('self._check_on_demand_expiration()')
        release = source.index('self._release_on_demand_plugins()')
        render = source.index('self._tick_plugin_updates()')
        assert poll < release < render

    def test_a_reconcile_that_runs_first_unloads_it_the_same_way(self, controller):
        """A reconcile queued during the session runs at the top of the loop,
        before the release: it removes the plugin itself (not in the enabled
        set) and the release is then a no-op."""
        _start(controller)
        controller._clear_on_demand(reason='requested-stop')

        controller._reconcile_enabled_plugins()
        controller._release_on_demand_plugins()

        controller.plugin_manager.unload_plugin.assert_called_once_with('preview-me')
        assert controller.available_modes == ['clock']
        assert controller._on_demand_loaded_plugins == set()

    def test_a_config_save_mid_session_keeps_the_instance_enabled(self, controller):
        """on_config_change would otherwise read enabled: false and switch it off."""
        controller.config_service.subscribe = MagicMock()
        _start(controller)
        callback = controller._plugin_config_callbacks['preview-me']
        controller.plugin_manager.prepare_plugin_config = None

        callback({}, {'enabled': False, 'color': 'red'})

        # The change goes through the manager's locked apply_config_change
        # (which calls on_config_change under the plugin's lock).
        plugin = controller.plugin_modes['preview_a']
        controller.plugin_manager.apply_config_change.assert_called_once_with(
            'preview-me', {'enabled': True, 'color': 'red'}, plugin_instance=plugin)


class TestRestoredSession:
    """A restart during a session for a disabled plugin restores it the same way."""

    def test_the_plugin_is_tracked_and_config_is_left_alone(self, test_display_controller):
        c = test_display_controller
        c.config.update({'clock': {'enabled': True}, 'disabled-one': {'enabled': False}})

        selected = c._select_startup_plugins(
            ['clock', 'disabled-one'], {'plugin_id': 'disabled-one', 'mode': 'x'})

        assert 'disabled-one' in selected
        assert c._on_demand_loaded_plugins == {'disabled-one'}
        assert c.config['disabled-one']['enabled'] is False


class TestResumingAfterTheSession:
    """Ending a session never resumes the rotation onto the plugin that is
    about to be unloaded."""

    def _restored_session(self, c, other_modes=('clock',)):
        """As after a restart: no saved resume index, and the plugin's modes
        ordered in ahead of the rest (load order is not deterministic)."""
        c._on_demand_loaded_plugins.add('preview-me')
        c.plugin_manager.load_plugin('preview-me', force_enabled=True)
        c._register_loaded_plugin('preview-me')
        c.available_modes = ['preview_a', 'preview_b'] + list(other_modes)
        c.on_demand_active = True
        c.on_demand_status = 'active'
        c.on_demand_plugin_id = 'preview-me'
        c.on_demand_modes = ['preview_a', 'preview_b']
        c.rotation_resume_index = None
        c.current_mode_index = 0
        c.current_display_mode = 'preview_a'

    def test_a_restored_session_resumes_on_an_enabled_mode(self, controller):
        self._restored_session(controller)

        controller._clear_on_demand(reason='requested-stop')

        assert controller.current_display_mode == 'clock'
        controller._release_on_demand_plugins()
        assert controller.available_modes == ['clock']
        assert controller.current_display_mode == 'clock'

    def test_with_nothing_else_enabled_the_display_goes_idle(self, controller):
        controller._unregister_plugin('clock')
        self._restored_session(controller, other_modes=())

        controller._clear_on_demand(reason='requested-stop')
        assert controller.current_display_mode is None

        controller._release_on_demand_plugins()
        assert controller.available_modes == []
        assert controller.current_display_mode is None

    def test_a_saved_resume_index_is_still_used(self, controller):
        c = controller
        c.available_modes = ['clock', 'other']
        c.plugin_modes['other'] = MagicMock()
        c.current_mode_index = 1
        c.current_display_mode = 'other'

        _start(c)
        c._clear_on_demand(reason='requested-stop')

        assert c.current_display_mode == 'other'


class TestStopClearsAnError:
    def _post_stop(self, c):
        stop = {'request_id': 'S1', 'action': 'stop'}
        c._last_on_demand_poll = None
        c.cache_manager.get = MagicMock(
            side_effect=lambda key, *a, **kw:
                stop if key == 'display_on_demand_request' else None)
        c.cache_manager.delete = MagicMock()
        c._poll_on_demand_requests()

    def test_a_stop_after_a_failed_request_clears_the_error(self, controller):
        _start(controller, plugin_id='uninstalled')
        assert controller.on_demand_status == 'error'

        self._post_stop(controller)

        assert controller.on_demand_status == 'idle'
        assert controller.on_demand_last_error is None
        state = controller.cache_manager.set.call_args_list[-1].args[1]
        assert state['status'] == 'idle'
        assert state['error'] is None

    def test_clearing_the_error_leaves_the_rotation_alone(self, controller):
        _start(controller, plugin_id='uninstalled')
        controller.force_change = False

        self._post_stop(controller)

        assert controller.current_display_mode == 'clock'
        assert controller.force_change is False

    def test_a_stop_while_idle_is_still_just_acknowledged(self, controller):
        controller._clear_on_demand = MagicMock()

        self._post_stop(controller)

        assert controller.on_demand_status == 'idle'
        assert controller.on_demand_request_id == 'S1'
        controller._clear_on_demand.assert_not_called()


class TestForceEnabledLoad:
    """PluginManager.load_plugin(force_enabled=True) runs the plugin enabled
    without touching the config it read."""

    class _Plugin:
        def __init__(self, config):
            self.config = config
            self.enabled_calls = 0

        def on_enable(self):
            self.enabled_calls += 1

    @pytest.fixture
    def pm(self, tmp_path):
        plugins_dir = tmp_path / 'plugins'
        (plugins_dir / 'demo').mkdir(parents=True)
        manager = PluginManager(plugins_dir=str(plugins_dir))
        manager.plugin_manifests['demo'] = {'id': 'demo', 'name': 'Demo'}
        manager.schema_manager = MagicMock()
        manager.schema_manager.get_schema_path.return_value = None
        # Hand the section back as-is, as the fallback path can: the copy in
        # load_plugin is what keeps the cached config clean.
        manager.schema_manager.prepare_plugin_config.side_effect = (
            lambda pid, cfg, schema=None, changed_paths=None: cfg)
        manager.plugin_loader = MagicMock()
        manager.plugin_loader.find_plugin_directory.return_value = plugins_dir / 'demo'
        manager.plugin_loader.load_plugin.side_effect = (
            lambda **kw: (self._Plugin(kw['config']), None))
        manager.config_manager = MagicMock()
        manager.cached_config = {'demo': {'enabled': False, 'color': 'red'}}
        manager.config_manager.load_config.return_value = manager.cached_config
        return manager

    def test_a_disabled_plugin_loads_disabled_by_default(self, pm):
        assert pm.load_plugin('demo') is True
        assert pm.plugins['demo'].enabled_calls == 0
        assert pm.state_manager.get_state('demo') == PluginState.DISABLED

    def test_force_enabled_runs_it_enabled(self, pm):
        assert pm.load_plugin('demo', force_enabled=True) is True
        plugin = pm.plugins['demo']
        assert plugin.config == {'enabled': True, 'color': 'red'}
        assert plugin.enabled_calls == 1
        assert pm.state_manager.get_state('demo') == PluginState.ENABLED

    def test_force_enabled_does_not_touch_the_cached_config(self, pm):
        pm.load_plugin('demo', force_enabled=True)
        assert pm.cached_config['demo'] == {'enabled': False, 'color': 'red'}
