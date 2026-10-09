"""Plugins asking for the screen in-process: BasePlugin.request_on_demand()
and end_on_demand().

A plugin running in the display process used to write the
``display_on_demand_request`` mailbox, which the display no longer reads
(stage 5). These tests pin the way in that replaced it:

* BasePlugin -> PluginManager -> DisplayController.submit_plugin_on_demand,
  which only queues, from any thread;
* the render thread applies the queue where it applies socket commands,
  through _handle_on_demand_request, at once, and woken by the control
  socket when it is up;
* a plugin's stop ends only its own session;
* no display to ask (the web interface's plugin manager, an old core's
  plugin manager) answers None.
"""

import logging
import threading
import time
from unittest.mock import MagicMock

import pytest

from src.ipc.server import ControlServer
from src.plugin_system.base_plugin import BasePlugin
from src.plugin_system.plugin_manager import PluginManager


class _Plugin(BasePlugin):
    def update(self):
        pass

    def display(self, force_clear=False):
        pass


def _plugin(plugin_id, manager):
    plugin = _Plugin.__new__(_Plugin)
    plugin.plugin_id = plugin_id
    plugin.plugin_manager = manager
    return plugin


def _manager(handler=None):
    manager = PluginManager.__new__(PluginManager)
    manager.logger = logging.getLogger('test.plugin_on_demand')
    if handler is not None:
        manager.set_on_demand_handler(handler)
    return manager


class _WakeServer:
    """The parts of ControlServer the controller uses, with no socket."""

    def __init__(self):
        self.woken = 0
        self.has_pending = False

    def wake(self):
        self.woken += 1
        self.has_pending = True

    def drain(self):
        self.has_pending = False
        return []


@pytest.fixture
def controller(test_display_controller):
    c_ = test_display_controller
    c_.on_demand_active = False
    c_.on_demand_request_id = None
    c_.cache_manager.get = MagicMock(return_value=None)
    c_.cache_manager.set = MagicMock()
    c_.cache_manager.delete = MagicMock()
    c_._activate_on_demand = MagicMock()
    return c_


@pytest.fixture
def wired(controller):
    """A real PluginManager wired to the controller, as __init__ wires it."""
    manager = _manager(controller.submit_plugin_on_demand)
    return controller, manager


class TestWiring:
    def test_the_controller_wires_its_plugin_manager(self, controller):
        controller.plugin_manager.set_on_demand_handler.assert_called_once_with(
            controller.submit_plugin_on_demand)

    def test_a_start_reaches_the_on_demand_handler(self, wired):
        controller, manager = wired
        rid = _plugin('pomodoro-timer', manager).request_on_demand(
            mode='pomodoro', duration=30, pinned=True)
        assert isinstance(rid, str) and rid
        controller._activate_on_demand.assert_not_called()   # only queued
        controller._poll_on_demand_requests()
        controller._activate_on_demand.assert_called_once()
        request = controller._activate_on_demand.call_args.args[0]
        assert request['request_id'] == rid
        assert request['action'] == 'start'
        assert request['plugin_id'] == 'pomodoro-timer'
        assert request['mode'] == 'pomodoro'
        assert request['duration'] == 30.0 and request['pinned'] is True
        assert request['source'] == 'plugin'
        assert controller.on_demand_request_id == rid

    def test_a_plugin_request_never_touches_the_mailbox(self, wired):
        controller, manager = wired
        _plugin('on-air', manager).request_on_demand(mode='on_air')
        controller._drain_control_commands()
        controller._activate_on_demand.assert_called_once()
        mailbox_reads = [call for call in controller.cache_manager.get.call_args_list
                         if call.args[0] == 'display_on_demand_request']
        assert mailbox_reads == []
        controller.cache_manager.delete.assert_not_called()

    def test_requests_apply_in_order(self, wired):
        controller, manager = wired
        seen = []
        controller._activate_on_demand = MagicMock(
            side_effect=lambda r: seen.append(r['mode']))
        plugin = _plugin('p', manager)
        for mode in ('a', 'b', 'c'):
            plugin.request_on_demand(mode=mode)
        controller._poll_on_demand_requests()
        assert seen == ['a', 'b', 'c']

    def test_a_failing_request_is_contained(self, wired):
        controller, manager = wired
        calls = []

        def activate(request):
            calls.append(request['mode'])
            if request['mode'] == 'bad':
                raise RuntimeError('plugin exploded')

        controller._activate_on_demand = MagicMock(side_effect=activate)
        plugin = _plugin('p', manager)
        plugin.request_on_demand(mode='bad')
        plugin.request_on_demand(mode='good')
        controller._poll_on_demand_requests()
        assert calls == ['bad', 'good']


class TestPromptness:
    def test_a_plugin_request_skips_the_pending_changes_floor(self, wired):
        controller, manager = wired
        controller._control_server = None
        controller._service_pending_changes()
        _plugin('p', manager).request_on_demand()
        controller._service_pending_changes()        # well inside the 0.25 s floor
        controller._activate_on_demand.assert_called_once()

    def test_a_plugin_request_lands_on_the_next_poll(self, wired):
        controller, manager = wired
        controller._control_server = _WakeServer()
        controller._poll_on_demand_requests()
        _plugin('p', manager).request_on_demand()
        controller._poll_on_demand_requests()
        controller._activate_on_demand.assert_called_once()

    def test_it_wakes_the_control_socket_wait(self, wired):
        controller, manager = wired
        server = ControlServer('/nonexistent/control.sock')   # never started
        controller._control_server = server
        assert not server.wait_for_command(0)
        _plugin('p', manager).request_on_demand()
        assert server.has_pending
        assert controller._wait_for_control(5.0) is True     # returns at once
        assert controller._control_command_pending()
        controller._poll_on_demand_requests()
        controller._activate_on_demand.assert_called_once()
        assert not server.has_pending
        assert not controller._control_command_pending()

    def test_without_a_socket_a_waiting_request_cuts_the_sleep(self, wired):
        controller, manager = wired
        controller._control_server = None
        _plugin('p', manager).request_on_demand()
        started = time.monotonic()
        assert controller._wait_for_control(5.0) is True
        assert time.monotonic() - started < 1.0
        assert controller._control_command_pending()

    def test_nothing_waiting_keeps_the_floor(self, controller):
        controller._control_server = None
        controller._poll_on_demand_requests = MagicMock()
        controller._service_pending_changes()
        controller._service_pending_changes()
        assert controller._poll_on_demand_requests.call_count == 1


class TestThreads:
    def test_requests_from_many_threads_all_land_in_order_per_thread(self, wired):
        controller, manager = wired
        seen = []
        controller._activate_on_demand = MagicMock(
            side_effect=lambda r: seen.append(r['mode']))
        controller.PLUGIN_ON_DEMAND_QUEUE_SIZE = 10_000
        threads_n, each = 8, 50
        barrier = threading.Barrier(threads_n)

        def ask(n):
            plugin = _plugin(f'p{n}', manager)
            barrier.wait()
            for i in range(each):
                assert plugin.request_on_demand(mode=f'{n}:{i}')

        threads = [threading.Thread(target=ask, args=(n,)) for n in range(threads_n)]
        for t in threads:
            t.start()
        # Drain while they ask, as the render thread would.
        while any(t.is_alive() for t in threads):
            controller._drain_control_commands()
        for t in threads:
            t.join()
        controller._drain_control_commands()
        assert len(seen) == threads_n * each
        for n in range(threads_n):
            mine = [int(m.split(':')[1]) for m in seen if m.startswith(f'{n}:')]
            assert mine == list(range(each))

    def test_a_full_queue_refuses(self, wired, caplog):
        controller, manager = wired
        controller.PLUGIN_ON_DEMAND_QUEUE_SIZE = 2
        plugin = _plugin('p', manager)
        assert plugin.request_on_demand()
        assert plugin.request_on_demand()
        assert plugin.request_on_demand() is None
        assert 'queue full' in caplog.text
        controller._poll_on_demand_requests()
        assert controller._activate_on_demand.call_count == 2
        assert plugin.request_on_demand()              # room again


class TestStop:
    def test_a_plugin_ends_its_own_session(self, wired):
        controller, manager = wired
        controller.on_demand_active = True
        controller.on_demand_plugin_id = 'on-air'
        controller._clear_on_demand = MagicMock()
        assert _plugin('on-air', manager).end_on_demand()
        controller._poll_on_demand_requests()
        controller._clear_on_demand.assert_called_once_with(reason='requested-stop')
        controller.cache_manager.delete.assert_not_called()

    def test_a_plugin_cannot_end_another_plugins_session(self, wired):
        controller, manager = wired
        controller.on_demand_active = True
        controller.on_demand_plugin_id = 'clock'       # the user started it
        controller.on_demand_request_id = 'user'
        controller._clear_on_demand = MagicMock()
        _plugin('pomodoro-timer', manager).end_on_demand()
        controller._poll_on_demand_requests()
        controller._clear_on_demand.assert_not_called()
        assert controller.on_demand_request_id == 'user'

    def test_a_stop_with_no_session_does_nothing(self, wired):
        controller, manager = wired
        controller.on_demand_status = 'error'
        controller._clear_on_demand = MagicMock()
        _plugin('on-air', manager).end_on_demand()
        controller._poll_on_demand_requests()
        controller._clear_on_demand.assert_not_called()

    def test_a_socket_stop_still_ends_any_session(self, wired):
        controller, _ = wired
        controller.on_demand_active = True
        controller.on_demand_plugin_id = 'clock'
        controller._clear_on_demand = MagicMock()
        controller._handle_on_demand_request({'request_id': 's', 'action': 'stop',
                                              'source': 'socket'})
        controller._clear_on_demand.assert_called_once_with(reason='requested-stop')

    def test_start_then_stop_from_one_thread_ends_the_session(self, wired):
        controller, manager = wired

        def activate(request):
            controller.on_demand_active = True
            controller.on_demand_plugin_id = request['plugin_id']

        controller._activate_on_demand = MagicMock(side_effect=activate)
        controller._clear_on_demand = MagicMock()
        plugin = _plugin('pomodoro-timer', manager)
        plugin.request_on_demand(mode='pomodoro', pinned=True)
        plugin.end_on_demand()
        controller._poll_on_demand_requests()
        controller._activate_on_demand.assert_called_once()
        controller._clear_on_demand.assert_called_once_with(reason='requested-stop')


class TestNoDisplay:
    """None: no display in this process took the request."""

    def test_a_manager_with_no_handler_answers_none(self):
        plugin = _plugin('p', _manager())
        assert plugin.request_on_demand() is None
        assert plugin.end_on_demand() is None

    def test_no_plugin_manager_answers_none(self):
        plugin = _plugin('p', None)
        assert plugin.request_on_demand() is None
        assert plugin.end_on_demand() is None

    def test_an_old_cores_plugin_manager_answers_none(self):
        class OldManager:
            plugin_manifests = {}

        plugin = _plugin('p', OldManager())
        assert plugin.request_on_demand() is None
        assert plugin.end_on_demand() is None

    def test_a_handler_that_raises_answers_none(self):
        def broken(request):
            raise RuntimeError('boom')

        plugin = _plugin('p', _manager(broken))
        assert plugin.request_on_demand() is None
        assert plugin.end_on_demand() is None

    def test_a_handler_that_refuses_answers_none(self):
        plugin = _plugin('p', _manager(lambda request: False))
        assert plugin.request_on_demand() is None

    def test_a_controller_built_without_init_refuses(self):
        from src.display_controller import DisplayController
        bare = DisplayController.__new__(DisplayController)
        assert bare.submit_plugin_on_demand({'action': 'start'}) is False
        assert bare._plugin_on_demand_pending() is False
        bare._drain_plugin_on_demand()                 # nothing to do, no error

    def test_a_mailbox_write_after_none_is_dropped_with_a_warning(self, tmp_path,
                                                                    monkeypatch, caplog):
        """What a plugin written for older cores does on None now: its
        fallback write to the retired key stores nothing, and the log names
        it once."""
        from src import cache_manager as cache_module
        from src.cache_manager import CacheManager
        monkeypatch.setattr(CacheManager, '_get_writable_cache_dir',
                            lambda self: str(tmp_path))
        monkeypatch.setattr(cache_module, '_retired_writers_warned', set())
        cache = CacheManager()
        try:
            plugin = _plugin('birdnet-go', _manager())
            plugin.cache_manager = cache
            caplog.set_level(logging.WARNING)
            for _ in range(2):
                if plugin.request_on_demand(mode='m') is None:
                    plugin.cache_manager.set('display_on_demand_request', {
                        'request_id': 'r', 'action': 'start', 'plugin_id': 'birdnet-go'})
            assert cache.get('display_on_demand_request', max_age=None, memory_ttl=0) is None
            lines = [r.getMessage() for r in caplog.records if 'retired' in r.getMessage()]
            assert len(lines) == 1 and "plugin 'birdnet-go'" in lines[0]
        finally:
            cache.stop_cleanup_thread()


class TestArguments:
    def test_the_manager_shapes_the_request(self):
        got = []
        plugin = _plugin('p', _manager(lambda r: got.append(r) or True))
        plugin.request_on_demand()
        plugin.end_on_demand()
        start, stop = got
        assert start['plugin_id'] == 'p' and start['mode'] is None
        assert start['duration'] is None and start['pinned'] is False
        assert start['source'] == 'plugin' and start['timestamp'] > 0
        assert stop == {'action': 'stop', 'plugin_id': 'p', 'request_id': stop['request_id'],
                        'timestamp': stop['timestamp'], 'source': 'plugin'}
        assert start['request_id'] != stop['request_id']

    @pytest.mark.parametrize('duration', [0, -5, float('inf'), float('nan')])
    def test_no_positive_duration_means_no_limit(self, duration):
        got = []
        _plugin('p', _manager(lambda r: got.append(r) or True)).request_on_demand(
            duration=duration)
        assert got[0]['duration'] is None

    @pytest.mark.parametrize('kwargs', [{'mode': 5}, {'mode': ''}, {'duration': '30'},
                                        {'duration': True}])
    def test_bad_arguments_raise(self, kwargs):
        plugin = _plugin('p', _manager(lambda r: True))
        with pytest.raises(ValueError):
            plugin.request_on_demand(**kwargs)


class TestMockManagers:
    def test_a_magicmock_manager_reads_as_not_taken(self):
        """A plugin's test with a MagicMock manager reads as "not taken"."""
        plugin = _plugin('p', MagicMock())
        assert plugin.request_on_demand(mode='m') is None
        assert plugin.end_on_demand() is None
        plugin.plugin_manager.request_on_demand.assert_called_once_with(
            'p', mode='m', duration=None, pinned=False)
        plugin.plugin_manager.end_on_demand.assert_called_once_with('p')

    def test_a_mocked_id_is_passed_through(self):
        manager = MagicMock()
        manager.request_on_demand.return_value = 'rid'
        manager.end_on_demand.return_value = 'rid2'
        plugin = _plugin('p', manager)
        assert plugin.request_on_demand() == 'rid'
        assert plugin.end_on_demand() == 'rid2'
