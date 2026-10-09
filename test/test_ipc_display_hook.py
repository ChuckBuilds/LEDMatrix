"""DisplayController's side of the control socket.

The server's handlers only queue; the render thread drains the queue
(_poll_on_demand_requests) and hands each on-demand command to
_handle_on_demand_request, which plugins' own requests use too. These tests
pin that hook:

* a socket command is applied with its request id, at once, and touches no
  cache key (the file mailbox and the persisted processed id are gone);
* a start sent twice with one request id is activated once;
* a command that fails is contained, and the ones after it still run;
* cleanup closes the socket; a disabled socket changes nothing.
"""

import os
import time
from unittest.mock import MagicMock

import pytest

from src.ipc import client
from src.ipc import contract as c
from src.ipc.contract import Command, OnDemandStartArgs, OnDemandStopArgs
from src.ipc.server import QueuedCommand


def _start(rid, plugin_id='clock', **kw):
    return QueuedCommand(request_id=rid, cmd=Command.ON_DEMAND_START,
                         args=OnDemandStartArgs(plugin_id=plugin_id, **kw),
                         received_at=time.time())


def _stop(rid):
    return QueuedCommand(request_id=rid, cmd=Command.ON_DEMAND_STOP,
                         args=OnDemandStopArgs(), received_at=time.time())


class FakeServer:
    def __init__(self, *commands):
        self.commands = list(commands)
        self.closed = False

    @property
    def has_pending(self):
        return bool(self.commands)

    def drain(self):
        out, self.commands = self.commands, []
        return out

    def close(self):
        self.closed = True


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


class TestDrain:
    def test_a_socket_start_is_activated_with_its_request_id(self, controller):
        controller._control_server = FakeServer(_start('sock-1', duration=30.0, pinned=True))
        controller._poll_on_demand_requests()
        controller._activate_on_demand.assert_called_once()
        request = controller._activate_on_demand.call_args.args[0]
        assert request['request_id'] == 'sock-1'
        assert request['action'] == 'start'
        assert request['plugin_id'] == 'clock'
        assert request['duration'] == 30.0 and request['pinned'] is True
        assert controller.on_demand_request_id == 'sock-1'
        # No mailbox, and no persisted processed id.
        keys = {call.args[0] for m in (controller.cache_manager.get,
                                       controller.cache_manager.set,
                                       controller.cache_manager.delete)
                for call in m.call_args_list}
        assert not keys & {'display_on_demand_request', 'display_on_demand_processed_id'}

    def test_socket_commands_land_at_once(self, controller):
        server = FakeServer()
        controller._control_server = server
        controller._poll_on_demand_requests()
        server.commands.append(_start('quick'))
        controller._poll_on_demand_requests()
        controller._activate_on_demand.assert_called_once()

    def test_a_start_sent_twice_is_activated_once(self, controller):
        server = FakeServer(_start('dup'))
        controller._control_server = server
        controller._poll_on_demand_requests()
        server.commands.append(_start('dup'))
        controller._poll_on_demand_requests()
        controller._activate_on_demand.assert_called_once()

    def test_a_socket_stop_ends_on_demand(self, controller):
        controller.on_demand_active = True
        controller._clear_on_demand = MagicMock()
        controller._control_server = FakeServer(_stop('halt'))
        controller._poll_on_demand_requests()
        controller._clear_on_demand.assert_called_once_with(reason='requested-stop')

    def test_commands_run_in_arrival_order(self, controller):
        seen = []
        controller._activate_on_demand = MagicMock(
            side_effect=lambda r: seen.append(r['request_id']))
        controller._control_server = FakeServer(_start('a'), _start('b'), _start('c'))
        controller._poll_on_demand_requests()
        assert seen == ['a', 'b', 'c']

    def test_a_failing_command_is_contained(self, controller):
        calls = []

        def activate(request):
            calls.append(request['request_id'])
            if request['request_id'] == 'bad':
                raise RuntimeError('plugin exploded')

        controller._activate_on_demand = MagicMock(side_effect=activate)
        controller._control_server = FakeServer(_start('bad'), _start('good'))
        controller._poll_on_demand_requests()
        assert calls == ['bad', 'good']

    def test_no_server_means_nothing_to_apply(self, controller):
        controller._control_server = None
        controller._poll_on_demand_requests()
        controller._activate_on_demand.assert_not_called()
        controller.cache_manager.get.assert_not_called()


class TestPendingChangesFloor:
    def test_a_queued_command_skips_the_floor(self, controller):
        server = FakeServer()
        controller._control_server = server
        controller._service_pending_changes()
        server.commands.append(_start('now'))
        controller._service_pending_changes()        # well inside the 0.25 s floor
        controller._activate_on_demand.assert_called_once()

    def test_nothing_queued_keeps_the_floor(self, controller):
        controller._control_server = FakeServer()
        controller._poll_on_demand_requests = MagicMock()
        controller._service_pending_changes()
        controller._service_pending_changes()
        assert controller._poll_on_demand_requests.call_count == 1


class TestLifecycle:
    def test_status_snapshot(self, controller):
        controller.current_display_mode = 'clock_main'
        controller.on_demand_active = True
        controller.on_demand_plugin_id = 'clock'
        controller.on_demand_expires_at = None
        status = controller._control_status()
        assert status['current_mode'] == 'clock_main'
        assert status['on_demand']['active'] is True
        assert status['on_demand']['plugin_id'] == 'clock'
        c.encode_message(status)   # it has to fit on the wire

    def test_the_state_names_the_request_it_answers(self, controller):
        # The web interface keeps reporting a delivered start as "starting"
        # until the display publishes state for that request id.
        assert controller._on_demand_state()['request_id'] is None
        controller._control_server = FakeServer(_start('sock-7'))
        controller._poll_on_demand_requests()
        assert controller._on_demand_state()['request_id'] == 'sock-7'

    def test_cleanup_closes_the_socket(self, controller):
        server = FakeServer()
        controller._control_server = server
        controller.cleanup()
        assert server.closed
        assert controller._control_server is None

    def test_disabled_socket_starts_nothing(self, controller):
        # conftest sets LEDMATRIX_CONTROL_SOCKET=off for every test.
        controller._start_control_server()
        assert controller._control_server is None

    @pytest.mark.skipif(not c.socket_supported(), reason='AF_UNIX sockets are Linux/macOS only')
    def test_end_to_end(self, controller, monkeypatch):
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix='lmipc-')
        path = os.path.join(d, 'control.sock')
        monkeypatch.setenv(c.SOCKET_PATH_ENV, path)
        try:
            controller._start_control_server()
            assert controller._control_server is not None
            ack = client.on_demand_start('e2e', 'clock', None, 15, False, paths=[path])
            assert ack['accepted'] is True and ack['request_id'] == 'e2e'
            status = client.on_demand_status(paths=[path])
            assert 'on_demand' in status and 'current_mode' in status
            controller._service_pending_changes()
            request = controller._activate_on_demand.call_args.args[0]
            assert request['request_id'] == 'e2e' and request['duration'] == 15.0
            controller.cleanup()
            assert not os.path.exists(path)
        finally:
            shutil.rmtree(d, ignore_errors=True)
