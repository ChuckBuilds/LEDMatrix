"""Per-plugin schedules (config['plugin_schedules'], src/plugin_schedule.py).

The window rule itself is the display schedule's, covered case by case in
test_display_controller_schedule.py through _check_schedule; here: the
plugin-level answer, and the three places it is applied -- the rotation,
the live scan, and the Vegas ticker -- plus the save endpoint.
"""

import copy
import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("EMULATOR", "true")

from src.display_arbiter import ScreenPlan, Source  # noqa: E402
from src.display_controller import DisplayController  # noqa: E402
from src.plugin_schedule import hidden_plugins, schedule_state, wants_live_scan  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.stream_manager import StreamManager  # noqa: E402
from test._api_v3_test_helpers import api_v3_client, api_v3_module  # noqa: E402,F401

DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
SUNDAY_1PM = datetime(2026, 10, 18, 13, 0)  # a Sunday
MONDAY_1PM = datetime(2026, 10, 19, 13, 0)


def sundays_off(**extra):
    """The gameday schedule: every day all day, except Sunday."""
    days = {d: {'enabled': d != 'sunday', 'start_time': '00:00', 'end_time': '23:59'}
            for d in DAYS}
    return {'enabled': True, 'mode': 'per-day', 'days': days, **extra}


ALWAYS_OFF = {'enabled': True, 'mode': 'per-day',
              'days': {d: {'enabled': False} for d in DAYS}}


class TestHiddenPlugins:
    def test_outside_window_is_hidden(self):
        schedules = {'news': sundays_off(), 'stocks': sundays_off()}
        assert hidden_plugins(schedules, SUNDAY_1PM, False) == {'news', 'stocks'}
        assert hidden_plugins(schedules, MONDAY_1PM, False) == frozenset()

    def test_hide_when_live_only_while_live(self):
        schedules = {'news': {'enabled': False, 'hide_when_live': True}}
        assert hidden_plugins(schedules, MONDAY_1PM, True) == {'news'}
        assert hidden_plugins(schedules, MONDAY_1PM, False) == frozenset()

    def test_disabled_schedule_and_junk_entries_hide_nothing(self):
        schedules = {'news': {**ALWAYS_OFF, 'enabled': False}, 'bad': 'x', 'none': None}
        assert hidden_plugins(schedules, SUNDAY_1PM, True) == frozenset()

    def test_bad_time_shows_rather_than_hides(self):
        assert schedule_state({'mode': 'global', 'start_time': '25:00'}, MONDAY_1PM)[0]

    @pytest.mark.parametrize('schedule', [
        {'mode': 1},
        {'mode': 'per-day', 'days': 5},
        {'mode': 'per-day', 'days': {'sunday': False}},
        {'mode': 'per-day', 'days': ['sunday']},
    ])
    def test_wrong_shapes_show_rather_than_raise(self, schedule):
        # Falls back to the global 07:00-23:00 window, which 13:00 is inside.
        assert schedule_state(schedule, SUNDAY_1PM)[0]

    def test_global_mode_ignores_days(self):
        schedule = {**sundays_off(), 'mode': 'global', 'start_time': '12:00', 'end_time': '14:00'}
        assert schedule_state(schedule, SUNDAY_1PM)[0]

    def test_wants_live_scan(self):
        assert not wants_live_scan({'news': sundays_off()})
        assert wants_live_scan({'news': sundays_off(), 'stocks': {'hide_when_live': True}})


def plugin(plugin_id, live=False, priority=False):
    return SimpleNamespace(plugin_id=plugin_id, display=lambda **_: True,
                           has_live_content=lambda: live,
                           has_live_priority=lambda: priority,
                           get_live_modes=lambda: [f'{plugin_id}_live'])


def controller(schedules, plugin_modes):
    dc = object.__new__(DisplayController)
    dc.config = {'plugin_schedules': schedules}
    dc._tz = None
    dc.plugin_modes = plugin_modes
    dc._plugin_hidden_cache = (None, frozenset())
    dc._game_live = False
    dc._game_live_at = float('-inf')
    dc._health_tracker = lambda: None
    return dc


class TestController:
    def test_rotation_skips_hidden_but_on_demand_shows_it(self):
        news = plugin('news')
        dc = controller({'news': ALWAYS_OFF}, {'news': news})
        assert dc._plugin_to_draw(ScreenPlan(Source.ROTATION, mode='news')) is None
        assert dc._plugin_to_draw(ScreenPlan(Source.ON_DEMAND, mode='news')) is news

    def test_no_schedules_changes_nothing(self):
        news = plugin('news')
        dc = controller({}, {'news': news})
        assert dc._plugin_to_draw(ScreenPlan(Source.ROTATION, mode='news')) is news

    def test_any_live_game_hides_news_even_without_live_priority(self):
        modes = {'news': plugin('news'), 'nfl_live': plugin('nfl', live=True)}
        dc = controller({'news': {'enabled': False, 'hide_when_live': True}}, modes)
        assert dc._hidden_plugins() == {'news'}

    def test_scoreboard_scheduled_off_does_not_win_live(self):
        modes = {'nfl_live': plugin('nfl', live=True, priority=True)}
        dc = controller({'nfl': ALWAYS_OFF}, modes)
        assert dc._collect_live_modes() == []
        dc.config = {}
        assert dc._collect_live_modes() == ['nfl_live']

    def test_a_scheduled_off_live_mode_does_not_hold_the_rotation(self):
        modes = {'nfl_live': plugin('nfl', live=True, priority=True)}
        dc = controller({'nfl': ALWAYS_OFF}, modes)
        assert not dc._still_live('nfl_live')
        dc.config = {}
        assert dc._still_live('nfl_live')

    @pytest.mark.parametrize('schedules', [['news'], 'news', 5])
    def test_wrong_shaped_plugin_schedules_hide_nothing(self, schedules):
        dc = controller(schedules, {'news': plugin('news')})
        assert dc._hidden_plugins() == frozenset()

    def test_a_reload_is_never_answered_from_the_old_schedules(self):
        dc = controller({'news': ALWAYS_OFF}, {'news': plugin('news')})
        assert dc._hidden_plugins() == {'news'}
        # A new config in the same minute, with no cache reset.
        dc.config = {'plugin_schedules': {'news': {'enabled': False}}}
        assert dc._hidden_plugins() == frozenset()


class TestVegas:
    def test_hidden_plugins_sit_out_the_ticker(self):
        plugins = {pid: SimpleNamespace(enabled=True, config={}) for pid in ('news', 'nfl')}
        stream = StreamManager(VegasModeConfig(), SimpleNamespace(plugins=plugins), MagicMock())
        stream._refresh_plugin_list()
        assert set(stream._ordered_plugins) == {'news', 'nfl'}
        stream.hidden_plugins = lambda: frozenset({'news'})
        stream._refresh_plugin_list()
        assert set(stream._ordered_plugins) == {'nfl'}


class TestSaveEndpoint:
    @pytest.fixture
    def store(self, api_v3_module, monkeypatch):
        state = {'config': {}}
        api_v3_module.api_v3.config_manager.load_config.side_effect = \
            lambda *a, **k: copy.deepcopy(state['config'])
        api_v3_module.api_v3.plugin_catalog.plugin_manifests = {'news': {}}

        def fake_save(_manager, config, **_kwargs):
            state['config'] = copy.deepcopy(config)
            return True, ''

        monkeypatch.setattr(api_v3_module, '_save_config_atomic', fake_save)
        return state

    def test_round_trip(self, api_v3_client, store):
        body = {'plugin_id': 'news', 'hide_when_live': 'on', **sundays_off()}
        resp = api_v3_client.post('/api/v3/config/plugin-schedules', json=body)
        assert resp.status_code == 200, resp.get_json()
        saved = store['config']['plugin_schedules']['news']
        assert saved['hide_when_live'] is True
        assert saved['days']['sunday']['enabled'] is False
        got = api_v3_client.get('/api/v3/config/plugin-schedules').get_json()['data']
        assert got == {'news': saved}

    def test_schedule_that_does_nothing_is_removed(self, api_v3_client, store):
        store['config'] = {'plugin_schedules': {'news': sundays_off()}}
        resp = api_v3_client.post('/api/v3/config/plugin-schedules',
                                  json={'plugin_id': 'news', 'enabled': False})
        assert resp.status_code == 200, resp.get_json()
        assert store['config']['plugin_schedules'] == {}

    @pytest.mark.parametrize('body,status', [
        ({'plugin_id': 'display', 'enabled': True}, 400),     # a core section
        ({'plugin_id': 'nope', 'enabled': True}, 404),        # not installed
        ({'plugin_id': 'news', 'enabled': True, 'mode': 'global',
          'start_time': '25:00', 'end_time': '23:00'}, 400),  # bad time
        ({'enabled': True}, 400),                              # no id
    ])
    def test_refusals(self, api_v3_client, store, body, status):
        resp = api_v3_client.post('/api/v3/config/plugin-schedules', json=body)
        assert resp.status_code == status, resp.get_json()
        assert 'plugin_schedules' not in store['config']
