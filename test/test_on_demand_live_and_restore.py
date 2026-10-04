"""Two on-demand edges seen on a rig.

  * A request naming a ``*_live`` mode got HTTP 200 and a different mode on
    the panel. The session's mode list kept live modes only when the plugin's
    has_live_content() said so, and that is the live-priority question,
    which the sports plugins answer for favourite teams only: fifteen college
    games on, no favourite playing, and ``ncaa_fb_live`` became
    ``nfl_recent``.
  * A restart during a session whose plugin then failed to load (its config
    no longer validated) logged "No valid display modes found ... after
    restoration" and left the session active with no modes: published as
    active for a plugin that was not running, with its cached request kept
    for the next restart.
"""

from unittest.mock import MagicMock

import pytest

SPORTS_MODES = ['nfl_live', 'nfl_recent', 'nfl_upcoming',
                'ncaa_fb_live', 'ncaa_fb_recent', 'ncaa_fb_upcoming']


def _sports_plugin(has_live_content=False):
    plugin = MagicMock(spec=['display', 'has_live_content', 'has_live_priority',
                             'get_live_modes'])
    plugin.has_live_content.return_value = has_live_content
    plugin.has_live_priority.return_value = True
    plugin.get_live_modes.return_value = []
    return plugin


def _register(controller, plugin_id, modes, plugin):
    controller.plugin_display_modes[plugin_id] = list(modes)
    for mode in modes:
        controller.plugin_modes[mode] = plugin
        controller.mode_to_plugin_id[mode] = plugin_id
        if mode not in controller.available_modes:
            controller.available_modes.append(mode)


@pytest.fixture
def football(test_display_controller):
    c = test_display_controller
    _register(c, 'football-scoreboard', SPORTS_MODES, _sports_plugin())
    return c


class TestANamedLiveModeIsShown:

    def test_it_is_the_first_screen(self, football):
        football._activate_on_demand({'plugin_id': 'football-scoreboard',
                                      'mode': 'ncaa_fb_live'})
        assert football.on_demand_active
        assert football.current_display_mode == 'ncaa_fb_live'
        assert football.on_demand_mode == 'ncaa_fb_live'

    def test_the_plugins_other_modes_follow_it(self, football):
        football._activate_on_demand({'plugin_id': 'football-scoreboard',
                                      'mode': 'ncaa_fb_live'})
        assert football.on_demand_modes[0] == 'ncaa_fb_live'
        assert set(football.on_demand_modes[1:]) == {
            'nfl_recent', 'nfl_upcoming', 'ncaa_fb_recent', 'ncaa_fb_upcoming'}

    def test_pinned_holds_it(self, football):
        football._activate_on_demand({'plugin_id': 'football-scoreboard',
                                      'mode': 'ncaa_fb_live', 'pinned': True})
        assert football.on_demand_modes == ['ncaa_fb_live']

    def test_a_bare_plugin_request_still_skips_quiet_live_modes(self, football):
        """Only a mode asked for by name is kept: a plugin-only request
        resolves to the plugin's first mode (nfl_live), and opening on an
        empty live screen there is what the ordering exists to avoid."""
        football._activate_on_demand({'plugin_id': 'football-scoreboard'})
        assert not any(m.endswith('_live') for m in football.on_demand_modes)

    def test_a_named_second_live_mode_with_content_leads(self, test_display_controller):
        """With live content both live modes are kept, nfl_live first; a
        request naming ncaa_fb_live must still open on it, not rotate away."""
        c = test_display_controller
        _register(c, 'football-scoreboard', SPORTS_MODES, _sports_plugin(has_live_content=True))
        c._activate_on_demand({'plugin_id': 'football-scoreboard', 'mode': 'ncaa_fb_live'})
        assert c.on_demand_modes[0] == 'ncaa_fb_live'
        assert c.on_demand_modes.count('ncaa_fb_live') == 1
        assert 'nfl_live' in c.on_demand_modes[1:]

    def test_the_named_mode_survives_a_restart(self, football):
        football._activate_on_demand({'plugin_id': 'football-scoreboard',
                                      'mode': 'ncaa_fb_live'})
        saved = football.cache_manager.set.call_args_list[-1]
        assert saved.args[0] == 'display_on_demand_config'
        config = saved.args[1]
        assert config['named_mode'] == 'ncaa_fb_live'

        football._reset_on_demand_fields()
        football._select_startup_plugins(['football-scoreboard'], config)
        football._populate_on_demand_modes_from_plugin()
        assert football.on_demand_modes[football.on_demand_mode_index] == 'ncaa_fb_live'


class TestARestoreWithNothingToResume:

    @pytest.fixture
    def restored(self, test_display_controller):
        c = test_display_controller
        c.config['clock-simple'] = {'enabled': True}
        c._select_startup_plugins(['clock-simple'],
                                  {'plugin_id': 'clock-simple', 'mode': 'clock-simple'})
        assert c.on_demand_active
        # The plugin's load then fails: nothing is registered for it.
        c.cache_manager.clear_cache.reset_mock()
        c._populate_on_demand_modes_from_plugin()
        return c

    def test_the_session_ends(self, restored):
        assert not restored.on_demand_active
        assert restored.on_demand_plugin_id is None
        assert not restored.on_demand_schedule_override

    def test_it_is_reported_as_an_error(self, restored):
        assert restored.on_demand_status == 'error'
        assert restored.on_demand_last_error == 'restore-failed'
        published = restored.cache_manager.set.call_args_list[-1]
        assert published.args[0] == 'display_on_demand_state'
        assert published.args[1]['status'] == 'error'
        assert published.args[1]['error'] == 'restore-failed'

    def test_the_cached_request_is_dropped(self, restored):
        restored.cache_manager.clear_cache.assert_any_call('display_on_demand_config')


def test_a_plugin_system_failure_ends_a_cached_session_not_yet_restored(
        mock_config_manager, mock_display_manager, mock_cache_manager,
        test_config_with_plugins, emulator_mode):
    """Initialization can fail before the cached session is read, with
    on_demand_active still False: the session must still end, visibly."""
    from unittest.mock import patch
    from src.display_controller import DisplayController

    mock_config_manager.get_config.return_value = test_config_with_plugins
    mock_config_manager.load_config.return_value = test_config_with_plugins
    mock_cache_manager._memory_cache['display_on_demand_config'] = {
        'plugin_id': 'clock-simple', 'mode': 'clock-simple'}
    with patch('src.display_controller.ConfigManager', return_value=mock_config_manager), \
         patch('src.display_controller.DisplayManager', return_value=mock_display_manager), \
         patch('src.display_controller.CacheManager', return_value=mock_cache_manager), \
         patch('src.display_controller.FontManager'), \
         patch('src.plugin_system.PluginManager', side_effect=RuntimeError("boom")):
        controller = DisplayController()
        try:
            assert controller.plugin_manager is None
            assert not controller.on_demand_active
            assert controller.on_demand_status == 'error'
            assert controller.on_demand_last_error == 'restore-failed'
            mock_cache_manager.clear_cache.assert_any_call('display_on_demand_config')
        finally:
            try:
                controller.cleanup()
            except Exception:
                pass
