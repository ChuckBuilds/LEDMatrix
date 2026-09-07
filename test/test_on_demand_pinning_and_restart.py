"""On-demand behaviour that a person can ask for but the controller ignored.

Three separate gaps, all reachable from the web UI's force-display dialog:

  * `pinned` was accepted by the API, stored on the controller and published
    back in the status payload, but never narrowed the rotation -- a pinned
    request still cycled every mode its plugin owns;
  * restarting while on-demand was active loaded *only* the on-demand plugin,
    so normal rotation had nothing to return to for the life of the process;
  * a stop request was exempt from the duplicate guards on purpose and was
    never removed from the mailbox, so it was re-processed on every poll
    forever.
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

    def test_a_disabled_on_demand_plugin_is_enabled_and_loaded(self, controller):
        """Otherwise the mode being resumed has nothing behind it."""
        selected = controller._select_startup_plugins(
            self.DISCOVERED, {'plugin_id': 'disabled-one', 'mode': 'x'})
        assert 'disabled-one' in selected
        assert controller.config['disabled-one']['enabled'] is True

    def test_an_unknown_on_demand_plugin_falls_back_to_normal(self, controller):
        selected = controller._select_startup_plugins(
            self.DISCOVERED, {'plugin_id': 'uninstalled', 'mode': 'x'})
        assert set(selected) == {'clock', 'weather', 'starlark-apps'}
        assert controller.on_demand_active is False

    def test_no_on_demand_config_is_a_normal_startup(self, controller):
        selected = controller._select_startup_plugins(self.DISCOVERED, None)
        assert set(selected) == {'clock', 'weather', 'starlark-apps'}
        assert controller.on_demand_active is False


class TestStopRequestsAreConsumed:
    """A stop request is exempt from the duplicate guards, so the mailbox
    delete is the only thing that ends it."""

    STOP = {'request_id': 'S1', 'action': 'stop'}

    def _arrange(self, controller, active):
        controller.on_demand_active = active
        controller.on_demand_status = 'active' if active else 'idle'
        controller._last_on_demand_poll = None
        controller.cache_manager.get = MagicMock(
            side_effect=lambda key, *a, **kw:
                self.STOP if key == 'display_on_demand_request' else None)
        controller.cache_manager.set = MagicMock()
        controller.cache_manager.delete = MagicMock()
        controller._clear_on_demand = MagicMock()

    def test_a_handled_stop_is_removed_from_the_mailbox(self, test_display_controller):
        c = test_display_controller
        self._arrange(c, active=True)
        c._poll_on_demand_requests()
        c.cache_manager.delete.assert_called_once_with('display_on_demand_request')

    def test_a_stop_arriving_while_idle_is_also_removed(self, test_display_controller):
        """Otherwise a stop sent to an idle display re-fires forever."""
        c = test_display_controller
        self._arrange(c, active=False)
        c._poll_on_demand_requests()
        c.cache_manager.delete.assert_called_once_with('display_on_demand_request')

    def test_the_stop_is_still_acted_on(self, test_display_controller):
        c = test_display_controller
        self._arrange(c, active=True)
        c._poll_on_demand_requests()
        c._clear_on_demand.assert_called_once_with(reason='requested-stop')

    def test_a_start_racing_in_behind_a_stop_is_not_discarded(self, test_display_controller):
        """The compare-before-delete applies to stops too."""
        c = test_display_controller
        self._arrange(c, active=True)
        newer = {'request_id': 'S2', 'action': 'start', 'plugin_id': 'p', 'mode': 'm'}
        reads = iter([self.STOP, newer])
        c.cache_manager.get = MagicMock(
            side_effect=lambda key, *a, **kw:
                next(reads, newer) if key == 'display_on_demand_request' else None)

        c._poll_on_demand_requests()
        assert c.cache_manager.delete.call_count == 0
