"""On-demand behaviour that a person can ask for but the controller ignored.

Three separate gaps, all reachable from the web UI's force-display dialog:

  * `pinned` was accepted by the API, stored on the controller and published
    back in the status payload, but never narrowed the rotation -- a pinned
    request still cycled every mode its plugin owns;
  * restarting while on-demand was active loaded *only* the on-demand plugin,
    so normal rotation had nothing to return to for the life of the process;
  * a stop request was exempt from the duplicate guards on purpose and was
    never removed from the file mailbox, so it was re-processed on every poll
    forever. The mailbox is gone (stage 5); a stop still skips the guards,
    so a second click stops a session a race left running.
"""

from unittest.mock import MagicMock

import pytest


def _plugin_with_modes(controller, plugin_id, modes):
    """Register `modes` as loaded modes belonging to `plugin_id`."""
    controller.plugin_display_modes[plugin_id] = list(modes)
    for mode in modes:
        controller.plugin_modes[mode] = MagicMock(spec=[])
        controller.mode_to_plugin_id[mode] = plugin_id


class TestPinnedNarrowsTheRotation:
    """A pinned request shows the one mode that was asked for.

    Unpinned stays the default: a sports plugin's modes are views of one
    subject, so rotating them is right. A Starlark plugin's modes are
    unrelated widgets, so it is not.
    """

    MODES = ['app_a', 'app_b', 'app_c']

    def _activate(self, controller, pinned):
        _plugin_with_modes(controller, 'starlark-apps', self.MODES)
        controller.available_modes = list(self.MODES)
        controller._activate_on_demand({
            'request_id': 'r1',
            'action': 'start',
            'plugin_id': 'starlark-apps',
            'mode': 'app_b',
            'pinned': pinned,
        })

    def test_pinned_rotation_holds_the_requested_mode(self, test_display_controller):
        c = test_display_controller
        self._activate(c, pinned=True)
        assert c.on_demand_modes == ['app_b']

    def test_unpinned_still_rotates_the_whole_plugin(self, test_display_controller):
        c = test_display_controller
        self._activate(c, pinned=False)
        assert set(c.on_demand_modes) == set(self.MODES)

    def test_pinned_still_starts_on_the_requested_mode(self, test_display_controller):
        c = test_display_controller
        self._activate(c, pinned=True)
        assert c.current_display_mode == 'app_b'

    def test_the_pin_is_recorded_for_the_status_payload(self, test_display_controller):
        c = test_display_controller
        self._activate(c, pinned=True)
        assert c.on_demand_pinned is True

    def test_an_unresolvable_pin_does_not_empty_the_rotation(self, test_display_controller):
        """A pin naming a mode outside the plugin must not leave nothing to show."""
        c = test_display_controller
        _plugin_with_modes(c, 'starlark-apps', self.MODES)
        assert c._apply_on_demand_pin(list(self.MODES), 'not_a_mode', True) == self.MODES


class TestPinSurvivesARestart:
    """The pin is part of the request being resumed, not a per-session flag."""

    def test_restored_pinned_state_narrows_the_rotation(self, test_display_controller):
        c = test_display_controller
        _plugin_with_modes(c, 'starlark-apps', ['app_a', 'app_b', 'app_c'])
        c.on_demand_active = True
        c.on_demand_plugin_id = 'starlark-apps'
        c.on_demand_mode = 'app_c'
        c.on_demand_pinned = True

        c._populate_on_demand_modes_from_plugin()
        assert c.on_demand_modes == ['app_c']

    def test_restored_unpinned_state_keeps_every_mode(self, test_display_controller):
        c = test_display_controller
        _plugin_with_modes(c, 'starlark-apps', ['app_a', 'app_b', 'app_c'])
        c.on_demand_active = True
        c.on_demand_plugin_id = 'starlark-apps'
        c.on_demand_mode = 'app_c'
        c.on_demand_pinned = False

        c._populate_on_demand_modes_from_plugin()
        assert set(c.on_demand_modes) == {'app_a', 'app_b', 'app_c'}


class TestASecondRequestKeepsTheResumePoint:
    """Clearing returns to where the normal rotation was, not the last request.

    A second start while on-demand was showing overwrote the saved resume
    index with the first request's mode, so stopping resumed rotation there.
    """

    def test_clear_resumes_the_original_rotation(self, test_display_controller):
        c = test_display_controller
        _plugin_with_modes(c, 'clock', ['clock'])
        _plugin_with_modes(c, 'weather', ['weather'])
        _plugin_with_modes(c, 'stocks', ['stocks'])
        c.available_modes = ['clock', 'weather', 'stocks']
        c.current_mode_index = 0
        c.current_display_mode = 'clock'

        c._activate_on_demand({'plugin_id': 'weather', 'mode': 'weather'})
        c._activate_on_demand({'plugin_id': 'stocks', 'mode': 'stocks'})
        assert c.current_display_mode == 'stocks'

        c._clear_on_demand(reason='requested-stop')
        assert c.current_mode_index == 0
        assert c.current_display_mode == 'clock'


class TestRestartDoesNotStarveTheOtherPlugins:
    """Restarting mid-on-demand used to load only the on-demand plugin.

    Restarts during an on-demand session are routine -- it is how an update or
    a config change is applied -- and the panel came back cycling one plugin's
    modes and nothing else until the on-demand cache was cleared by hand.
    """

    DISCOVERED = ['clock', 'weather', 'starlark-apps', 'disabled-one']

    @pytest.fixture
    def controller(self, test_display_controller):
        c = test_display_controller
        c.config.update({
            'clock': {'enabled': True},
            'weather': {'enabled': True},
            'starlark-apps': {'enabled': True},
            'disabled-one': {'enabled': False},
        })
        return c

    def test_every_enabled_plugin_still_loads(self, controller):
        selected = controller._select_startup_plugins(
            self.DISCOVERED, {'plugin_id': 'starlark-apps', 'mode': 'app_a'})
        assert set(selected) == {'clock', 'weather', 'starlark-apps'}

    def test_disabled_plugins_are_still_left_out(self, controller):
        selected = controller._select_startup_plugins(
            self.DISCOVERED, {'plugin_id': 'starlark-apps', 'mode': 'app_a'})
        assert 'disabled-one' not in selected

    def test_the_on_demand_state_is_still_restored(self, controller):
        controller._select_startup_plugins(
            self.DISCOVERED,
            {'plugin_id': 'starlark-apps', 'mode': 'app_a', 'pinned': True})
        assert controller.on_demand_active is True
        assert controller.on_demand_plugin_id == 'starlark-apps'
        assert controller.on_demand_mode == 'app_a'
        assert controller.on_demand_pinned is True

    def test_a_disabled_on_demand_plugin_is_still_loaded(self, controller):
        """Otherwise the mode being resumed has nothing behind it. It loads
        for on-demand only; its config section is left disabled."""
        selected = controller._select_startup_plugins(
            self.DISCOVERED, {'plugin_id': 'disabled-one', 'mode': 'x'})
        assert 'disabled-one' in selected
        assert controller._on_demand_loaded_plugins == {'disabled-one'}
        assert controller.config['disabled-one']['enabled'] is False

    def test_an_unknown_on_demand_plugin_falls_back_to_normal(self, controller):
        selected = controller._select_startup_plugins(
            self.DISCOVERED, {'plugin_id': 'uninstalled', 'mode': 'x'})
        assert set(selected) == {'clock', 'weather', 'starlark-apps'}
        assert controller.on_demand_active is False

    def test_no_on_demand_config_is_a_normal_startup(self, controller):
        selected = controller._select_startup_plugins(self.DISCOVERED, None)
        assert set(selected) == {'clock', 'weather', 'starlark-apps'}
        assert controller.on_demand_active is False


class TestStopRequestsSkipTheDuplicateGuard:
    """A stop is exempt from the request-id guard: every one is acted on."""

    def _arrange(self, controller, active):
        controller.on_demand_active = active
        controller.on_demand_status = 'active' if active else 'idle'
        controller._clear_on_demand = MagicMock()

    def test_the_stop_is_acted_on(self, test_display_controller):
        c = test_display_controller
        self._arrange(c, active=True)
        c._handle_on_demand_request({'request_id': 'S1', 'action': 'stop',
                                     'source': 'socket'})
        c._clear_on_demand.assert_called_once_with(reason='requested-stop')

    def test_the_same_stop_twice_is_acted_on_twice(self, test_display_controller):
        c = test_display_controller
        self._arrange(c, active=True)
        for _ in range(2):
            c._handle_on_demand_request({'request_id': 'S1', 'action': 'stop',
                                         'source': 'socket'})
        assert c._clear_on_demand.call_count == 2
