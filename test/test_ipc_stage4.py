"""Stages 4 and 5 of the control socket: the socket is the only way in.

* The client knows whether the display had the request (``ControlError.sent``)
  and ``display_not_listening`` says when no display was there to take it
  (stopped or still starting), the one case a later retry can fix.
* ``errors.clear`` is answered on the connection thread by a handler the
  display registers; a display without one answers like an older display.
* Stage 5: the display no longer reads the ``display_on_demand_request``
  mailbox at all, and the cache refuses writes to the retired mailbox keys,
  warning once per writer.

The web routes are covered in test_api_v3_on_demand_socket.py and
test_error_snapshot_cross_process.py.
"""

import json
import logging
import os
import socket
import time
from unittest.mock import MagicMock, patch

import pytest

from src import cache_manager as cache_module
from src.cache_manager import CacheManager
from src.ipc import client
from src.ipc import contract as c
from src.ipc.contract import Command, ErrorsClearArgs, OnDemandStartArgs, ProtocolError
from src.ipc.server import ControlServer, QueuedCommand

MAILBOX = 'display_on_demand_request'


# -- the client: was the request sent? ---------------------------------------------

class FakeSock:
    """Stands in for a connected socket in client._exchange."""

    def __init__(self, replies=(), send_error=None, recv_error=None):
        self.replies = list(replies)
        self.send_error = send_error
        self.recv_error = recv_error
        self.sent = b''

    def settimeout(self, _t):
        pass

    def sendall(self, data):
        if self.send_error is not None:
            raise self.send_error
        self.sent += data

    def recv(self, _n):
        if self.recv_error is not None:
            raise self.recv_error
        return self.replies.pop(0) if self.replies else b''

    def close(self):
        pass


def _reply(request_id, **body):
    return (json.dumps(dict({'v': 1, 'id': request_id}, **body)) + '\n').encode()


def _call(sock=None, connect_error=None, request_id='rid-1'):
    with patch.object(client, 'socket_supported', return_value=True), \
         patch.object(client, '_connect',
                      side_effect=connect_error, return_value=sock):
        return client.request(Command.PING, {}, request_id=request_id, paths=['/x.sock'])


def _error(**kw):
    with pytest.raises(client.ControlError) as e:
        _call(**kw)
    return e.value


class TestSent:
    def test_an_answer_is_returned(self):
        assert _call(FakeSock([_reply('rid-1', ok=True, result={'pong': True})])) == {'pong': True}

    @pytest.mark.parametrize('reason,listening', [('no_socket', False), ('refused', False),
                                                  ('timeout', True), ('busy', True)])
    def test_a_failed_connect_was_not_sent(self, reason, listening):
        e = _error(connect_error=client.ControlError(reason))
        assert e.reason == reason and e.sent is False
        # Only "nothing there" is worth waiting for: a timeout or a full
        # backlog is a display that is there and stuck.
        assert client.display_not_listening(e) is not listening

    def test_a_send_that_timed_out_was_not_sent(self):
        e = _error(sock=FakeSock(send_error=socket.timeout()))
        assert e.reason == 'timeout' and e.sent is False
        assert not client.display_not_listening(e)

    def test_silence_after_the_request_was_sent(self):
        e = _error(sock=FakeSock(recv_error=socket.timeout()))
        assert e.reason == 'timeout' and e.sent is True
        assert not client.display_not_listening(e)

    def test_a_hang_up_after_the_request_was_sent(self):
        e = _error(sock=FakeSock([]))
        assert e.reason == 'closed' and e.sent is True
        assert not client.display_not_listening(e)

    def test_a_garbled_reply(self):
        e = _error(sock=FakeSock([b'not json\n']))
        assert e.reason == 'bad_response' and e.sent is True
        assert not client.display_not_listening(e)

    @pytest.mark.parametrize('code', ['busy', 'invalid_args', 'internal', 'pending', 'failed'])
    def test_a_display_error_with_an_id_was_sent(self, code):
        sock = FakeSock([_reply('rid-1', ok=False, error={'code': code, 'message': 'x'})])
        e = _error(sock=sock)
        assert e.reason == code and e.sent is True
        assert not client.display_not_listening(e)

    @pytest.mark.parametrize('code', ['forbidden', 'busy'])
    def test_a_refusal_at_the_door_was_not_sent(self, code):
        # forbidden, or too many connections: answered before the request
        # was read, so with no id.
        sock = FakeSock([(json.dumps({'v': 1, 'id': None, 'ok': False,
                                      'error': {'code': code, 'message': 'x'}}) + '\n').encode()])
        e = _error(sock=sock)
        assert e.reason == code and e.sent is False
        assert not client.display_not_listening(e)

    @pytest.mark.parametrize('code', ['unknown_command', 'unsupported_version'])
    def test_an_older_display_is_listening(self, code):
        sock = FakeSock([_reply('rid-1', ok=False, error={'code': code, 'message': 'x'})])
        e = _error(sock=sock)
        assert e.sent is True
        assert not client.display_not_listening(e)

    def test_a_request_refused_locally_never_left(self):
        with pytest.raises(client.ControlError) as e:
            client.request(Command.ERRORS_CLEAR, {'cutoff': 'soon'}, paths=['/x.sock'])
        assert e.value.reason == 'invalid_request' and e.value.sent is False
        assert not client.display_not_listening(e.value)

    @pytest.mark.parametrize('reason', ['disabled', 'unsupported'])
    def test_a_client_without_the_socket_is_not_waiting_for_a_display(self, reason):
        assert not client.display_not_listening(client.ControlError(reason))

    def test_a_client_bug_is_not_a_missing_display(self):
        assert not client.display_not_listening(RuntimeError('boom'))


# -- errors.clear on the server ------------------------------------------------------

def _line(cmd, args, rid='r1'):
    return c.encode_message({'v': 1, 'id': rid, 'cmd': cmd, 'args': args})


class TestErrorsClearOnTheServer:
    def test_the_handler_answers_it(self, tmp_path):
        seen = []

        def handler(request_id, args):
            seen.append((request_id, args))
            return {'request_id': request_id, 'cutoff': args.cutoff, 'cleared': 4}

        server = ControlServer(str(tmp_path / 's.sock'),
                               handlers={Command.ERRORS_CLEAR: handler})
        response = server.handle_line(_line(Command.ERRORS_CLEAR, {'cutoff': 123}))
        assert response.ok and response.result == {'request_id': 'r1', 'cutoff': 123.0,
                                                   'cleared': 4}
        assert seen == [('r1', ErrorsClearArgs(cutoff=123.0))]
        assert not server.has_pending   # not queued for the render thread

    def test_a_display_without_a_handler_answers_like_an_older_one(self, tmp_path):
        server = ControlServer(str(tmp_path / 's.sock'))
        response = server.handle_line(_line(Command.ERRORS_CLEAR, {'cutoff': 1}))
        assert not response.ok and response.error.code == c.ErrorCode.UNKNOWN_COMMAND

    def test_only_direct_commands_take_a_handler(self, tmp_path):
        server = ControlServer(str(tmp_path / 's.sock'),
                               handlers={Command.ON_DEMAND_START: lambda *a: {}})
        response = server.handle_line(_line(Command.ON_DEMAND_START, {'plugin_id': 'p'}))
        assert response.ok and response.result['accepted'] is True   # still queued
        assert server.has_pending

    def test_a_handler_error_is_contained(self, tmp_path):
        def boom(*_a):
            raise ValueError('disk gone')

        server = ControlServer(str(tmp_path / 's.sock'), handlers={Command.ERRORS_CLEAR: boom})
        response = server.handle_line(_line(Command.ERRORS_CLEAR, {'cutoff': 1}))
        assert response.error.code == c.ErrorCode.INTERNAL
        assert 'disk gone' not in response.error.message

    def test_a_handler_can_refuse_with_a_code(self, tmp_path):
        def refuse(*_a):
            raise ProtocolError(c.ErrorCode.BUSY, 'later')

        server = ControlServer(str(tmp_path / 's.sock'), handlers={Command.ERRORS_CLEAR: refuse})
        assert server.handle_line(
            _line(Command.ERRORS_CLEAR, {'cutoff': 1})).error.code == c.ErrorCode.BUSY

    @pytest.mark.parametrize('cutoff', ['1', None, True, float('inf'), -1])
    def test_bad_cutoffs_are_refused(self, cutoff):
        with pytest.raises(ProtocolError) as e:
            ErrorsClearArgs.from_dict({'cutoff': cutoff})
        assert e.value.code == c.ErrorCode.INVALID_ARGS

    def test_hello_lists_it(self, tmp_path):
        server = ControlServer(str(tmp_path / 's.sock'))
        result = server.handle_line(_line(Command.HELLO, {'versions': [1]})).result
        assert Command.ERRORS_CLEAR in result['commands']


# -- stage 5: the display reads no mailbox --------------------------------------------

class CountingCache:
    """The slice of CacheManager the on-demand path uses, counting every call."""

    def __init__(self):
        self.data = {}
        self.calls = []

    def __getattr__(self, name):
        # Any other method (file_signature, delete, ...) is recorded too.
        def call(*a, **kw):
            self.calls.append((name, a[0] if a else None))
        return call

    def get(self, key, *a, **kw):
        self.calls.append(('get', key))
        return self.data.get(key)

    def set(self, key, value, *a, **kw):
        self.calls.append(('set', key))
        self.data[key] = value


class FakeServer:
    def __init__(self):
        self.commands = []

    @property
    def has_pending(self):
        return bool(self.commands)

    def drain(self):
        out, self.commands = self.commands, []
        return out


@pytest.fixture
def controller(test_display_controller):
    dc = test_display_controller
    dc.cache_manager = CountingCache()
    dc._activate_on_demand = MagicMock()
    dc.on_demand_active = False
    dc.on_demand_request_id = None
    return dc


class TestNoMailbox:
    @pytest.mark.parametrize('with_socket', [False, True])
    def test_polling_touches_no_cache_key(self, controller, with_socket):
        controller._control_server = FakeServer() if with_socket else None
        controller.cache_manager.data[MAILBOX] = {'request_id': 'left', 'action': 'start',
                                                  'plugin_id': 'clock'}
        for _ in range(200):
            controller._poll_on_demand_requests()
        assert controller.cache_manager.calls == []
        controller._activate_on_demand.assert_not_called()

    def test_socket_commands_land_at_once(self, controller):
        server = controller._control_server = FakeServer()
        server.commands.append(QueuedCommand('sock', Command.ON_DEMAND_START,
                                             OnDemandStartArgs(plugin_id='clock'), time.time()))
        controller._poll_on_demand_requests()
        controller._activate_on_demand.assert_called_once()

    def test_a_start_is_applied_once_and_writes_no_processed_id(self, controller):
        server = controller._control_server = FakeServer()
        for _ in range(2):
            server.commands.append(QueuedCommand('same', Command.ON_DEMAND_START,
                                                 OnDemandStartArgs(plugin_id='clock'), time.time()))
            controller._poll_on_demand_requests()
        controller._activate_on_demand.assert_called_once()
        assert controller.cache_manager.calls == []

    def test_a_socket_stop_touches_no_cache_key(self, controller):
        from src.ipc.contract import OnDemandStopArgs
        controller.on_demand_active = True
        controller._clear_on_demand = MagicMock()
        server = controller._control_server = FakeServer()
        server.commands.append(QueuedCommand('s2', Command.ON_DEMAND_STOP,
                                             OnDemandStopArgs(), time.time()))
        controller._drain_control_commands()
        controller._clear_on_demand.assert_called_once()
        assert controller.cache_manager.calls == []

    def test_the_mailbox_helpers_are_gone(self):
        from src import display_controller as dcm
        from src import error_aggregator as ea
        for name in ('ON_DEMAND_MAILBOX_KEY', 'MailboxWatch'):
            assert not hasattr(dcm, name)
        assert not hasattr(ea, 'ERROR_CLEAR_REQUEST_KEY')
        assert not hasattr(cache_module, 'MailboxWatch')
        assert not hasattr(CacheManager, 'file_signature')
        assert not hasattr(client, 'should_fall_back')
        for name in ('_consume_on_demand_request', '_note_mailbox_request',
                     '_mailbox_poll_interval', 'MAILBOX_POLL_INTERVAL_WITH_SOCKET'):
            assert not hasattr(dcm.DisplayController, name)


# -- stage 5: writes to the retired keys are refused, with one warning per writer -----

@pytest.fixture
def real_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(CacheManager, '_get_writable_cache_dir', lambda self: str(tmp_path))
    monkeypatch.setattr(cache_module, '_retired_writers_warned', set())
    cache = CacheManager()
    yield cache
    cache.stop_cleanup_thread()


class OldPlugin:
    """What an old plugin looks like on the stack: BasePlugin gives every
    plugin ``plugin_id`` and ``cache_manager``."""

    def __init__(self, plugin_id, cache):
        self.plugin_id = plugin_id
        self.cache_manager = cache

    def trigger(self, target=None):
        self.cache_manager.set(MAILBOX, {'request_id': 'r', 'action': 'start',
                                         'plugin_id': target or self.plugin_id})


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and 'retired' in r.getMessage()]


class TestRetiredKeys:
    @pytest.mark.parametrize('key', sorted(cache_module.RETIRED_MAILBOX_KEYS))
    def test_a_write_stores_nothing(self, real_cache, key):
        real_cache.set(key, {'request_id': 'r'})
        real_cache.save_cache(key, {'request_id': 'r2'})
        assert real_cache.get(key, max_age=None, memory_ttl=0) is None
        assert not [f for f in os.listdir(real_cache.cache_dir) if key in f]

    def test_other_keys_are_unaffected(self, real_cache):
        real_cache.set('display_on_demand_state', {'active': True})
        assert real_cache.get('display_on_demand_state', max_age=None,
                              memory_ttl=0) == {'active': True}

    def test_the_writing_plugin_is_named_once(self, real_cache, caplog):
        caplog.set_level(logging.WARNING)
        on_air = OldPlugin('on-air', real_cache)
        for _ in range(3):
            on_air.trigger()
        OldPlugin('pomodoro-timer', real_cache).trigger()
        lines = _warnings(caplog)
        assert len(lines) == 2
        assert "plugin 'on-air'" in lines[0] and 'display_on_demand_request' in lines[0]
        assert 'request_on_demand' in lines[0]
        assert "plugin 'pomodoro-timer'" in lines[1]

    def test_the_writer_is_the_caller_not_the_target(self, real_cache, caplog):
        caplog.set_level(logging.WARNING)
        OldPlugin('mqtt-notifications', real_cache).trigger(target='clock')
        (line,) = _warnings(caplog)
        assert "plugin 'mqtt-notifications'" in line and 'clock' not in line

    def test_without_a_plugin_on_the_stack_the_request_names_it(self, real_cache, caplog):
        caplog.set_level(logging.WARNING)
        real_cache.set(MAILBOX, {'request_id': 'r', 'action': 'start', 'plugin_id': 'gif-player'})
        (line,) = _warnings(caplog)
        assert "plugin 'gif-player' (named in the request)" in line

    def test_otherwise_unknown(self, real_cache, caplog):
        caplog.set_level(logging.WARNING)
        real_cache.set('plugin_error_clear_request', {'request_id': 'r', 'cutoff': 1.0})
        real_cache.set('plugin_error_clear_request', {'request_id': 'r2', 'cutoff': 2.0})
        (line,) = _warnings(caplog)
        assert 'by unknown' in line and '/api/v3/errors/clear' in line


# -- end to end over a real socket ---------------------------------------------------

@pytest.mark.skipif(not c.socket_supported(), reason='AF_UNIX sockets are Linux/macOS only')
class TestOverTheSocket:
    @pytest.fixture
    def sock_path(self):
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix='lmipc-')
        yield os.path.join(d, 'control.sock')
        shutil.rmtree(d, ignore_errors=True)

    def test_errors_clear_round_trip(self, sock_path):
        def handler(request_id, args):
            return {'request_id': request_id, 'cutoff': args.cutoff, 'cleared': 2}

        server = ControlServer(sock_path, handlers={Command.ERRORS_CLEAR: handler})
        assert server.start()
        try:
            result = client.errors_clear('clr-1', 1790000000.0, paths=[sock_path])
            assert result == {'request_id': 'clr-1', 'cutoff': 1790000000.0, 'cleared': 2}
        finally:
            server.close()

    def test_an_older_display_is_listening(self, sock_path):
        server = ControlServer(sock_path)   # no errors.clear handler
        assert server.start()
        try:
            with pytest.raises(client.ControlError) as e:
                client.errors_clear('clr-2', 1.0, paths=[sock_path])
            assert e.value.reason == 'unknown_command' and e.value.sent is True
            assert not client.display_not_listening(e.value)
        finally:
            server.close()

    def test_no_display_is_not_listening(self, sock_path):
        with pytest.raises(client.ControlError) as e:
            client.errors_clear('clr-3', 1.0, paths=[sock_path])
        assert e.value.reason == 'no_socket' and e.value.sent is False
        assert client.display_not_listening(e.value)

    def test_a_full_queue_is_listening(self, sock_path):
        server = ControlServer(sock_path, queue_size=1)
        assert server.start()
        try:
            client.on_demand_start('q1', 'clock', None, paths=[sock_path])
            with pytest.raises(client.ControlError) as e:
                client.on_demand_start('q2', 'clock', None, paths=[sock_path])
            assert e.value.reason == 'busy' and e.value.sent is True
            assert not client.display_not_listening(e.value)
        finally:
            server.close()
