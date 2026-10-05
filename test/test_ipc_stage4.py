"""Stage 4 of the control socket: the file mailboxes are only a fallback.

* The client knows whether the display had the request (``ControlError.sent``)
  and ``should_fall_back`` allows a mailbox write only when it did not, or
  when the display is too old to know the command (the upgrade case).
* ``errors.clear`` is answered on the connection thread by a handler the
  display registers; a display without one answers like an older display.
* The display looks at the on-demand mailbox once a second while the socket
  is up (0.25 s without it), reads it only when its file changed, never
  touches it for a socket command, and logs who still writes it.
* ``CacheManager.file_signature`` / ``MailboxWatch`` make a look one stat().

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

from src.cache_manager import CacheManager, MailboxWatch
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

    @pytest.mark.parametrize('reason', ['no_socket', 'refused', 'timeout', 'busy'])
    def test_a_failed_connect_was_not_sent(self, reason):
        e = _error(connect_error=client.ControlError(reason))
        assert e.reason == reason and e.sent is False
        assert client.should_fall_back(e)

    def test_a_send_that_timed_out_was_not_sent(self):
        e = _error(sock=FakeSock(send_error=socket.timeout()))
        assert e.reason == 'timeout' and e.sent is False
        assert client.should_fall_back(e)

    def test_silence_after_the_request_was_sent(self):
        e = _error(sock=FakeSock(recv_error=socket.timeout()))
        assert e.reason == 'timeout' and e.sent is True
        assert not client.should_fall_back(e)

    def test_a_hang_up_after_the_request_was_sent(self):
        e = _error(sock=FakeSock([]))
        assert e.reason == 'closed' and e.sent is True
        assert not client.should_fall_back(e)

    def test_a_garbled_reply(self):
        e = _error(sock=FakeSock([b'not json\n']))
        assert e.reason == 'bad_response' and e.sent is True
        assert not client.should_fall_back(e)

    @pytest.mark.parametrize('code', ['busy', 'invalid_args', 'internal', 'pending', 'failed'])
    def test_a_display_error_with_an_id_was_sent(self, code):
        sock = FakeSock([_reply('rid-1', ok=False, error={'code': code, 'message': 'x'})])
        e = _error(sock=sock)
        assert e.reason == code and e.sent is True
        assert not client.should_fall_back(e)

    @pytest.mark.parametrize('code', ['forbidden', 'busy'])
    def test_a_refusal_at_the_door_was_not_sent(self, code):
        # forbidden, or too many connections: answered before the request
        # was read, so with no id.
        sock = FakeSock([(json.dumps({'v': 1, 'id': None, 'ok': False,
                                      'error': {'code': code, 'message': 'x'}}) + '\n').encode()])
        e = _error(sock=sock)
        assert e.reason == code and e.sent is False
        assert client.should_fall_back(e)

    @pytest.mark.parametrize('code', ['unknown_command', 'unsupported_version'])
    def test_an_older_display_is_fallen_back_from(self, code):
        sock = FakeSock([_reply('rid-1', ok=False, error={'code': code, 'message': 'x'})])
        e = _error(sock=sock)
        assert e.sent is True
        assert client.should_fall_back(e)

    def test_a_request_refused_locally_never_left(self):
        with pytest.raises(client.ControlError) as e:
            client.request(Command.ERRORS_CLEAR, {'cutoff': 'soon'}, paths=['/x.sock'])
        assert e.value.reason == 'invalid_request' and e.value.sent is False
        assert client.should_fall_back(e.value)

    def test_a_client_bug_falls_back(self):
        assert client.should_fall_back(RuntimeError('boom'))


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


# -- the display's mailbox poll ------------------------------------------------------

class SignedCache:
    """The slice of CacheManager the poll uses, counting what it costs."""

    def __init__(self):
        self.data = {}
        self.writes = 0
        self.version = {}
        self.reads = []
        self.stats = 0
        self.deletes = []
        self.sets = []

    def file_signature(self, key):
        self.stats += 1
        return (self.version[key], 0, 0) if key in self.data else None

    def get(self, key, *a, **kw):
        self.reads.append(key)
        return self.data.get(key)

    def set(self, key, value, *a, **kw):
        self.sets.append(key)
        self.data[key] = value
        self.writes += 1
        self.version[key] = self.writes

    def delete(self, key):
        self.deletes.append(key)
        self.data.pop(key, None)


class FakeServer:
    def __init__(self):
        self.commands = []

    @property
    def has_pending(self):
        return bool(self.commands)

    def drain(self):
        out, self.commands = self.commands, []
        return out


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def controller(test_display_controller, monkeypatch):
    dc = test_display_controller
    dc.cache_manager = SignedCache()
    dc._activate_on_demand = MagicMock()
    dc.on_demand_active = False
    dc.on_demand_request_id = None
    dc._last_on_demand_poll = None
    dc._on_demand_mailbox = None
    dc._mailbox_writers_logged = frozenset()
    clock = Clock()
    monkeypatch.setattr('src.display_controller.time.monotonic', clock)
    dc.clock = clock
    return dc


def _post(dc, rid, action='start', **fields):
    dc.cache_manager.set(MAILBOX, dict({'request_id': rid, 'action': action}, **fields))


def _poll_for(dc, seconds, step=1 / 16):   # exact in binary: no drift past a floor
    end = dc.clock.t + seconds
    while dc.clock.t < end:
        dc._poll_on_demand_requests()
        dc.clock.t += step


class TestMailboxCadence:
    def test_without_a_socket_it_is_looked_at_every_quarter_second(self, controller):
        controller._control_server = None
        _poll_for(controller, 10.0)
        assert 38 <= controller.cache_manager.stats <= 42

    def test_with_a_socket_it_is_looked_at_once_a_second(self, controller):
        controller._control_server = FakeServer()
        _poll_for(controller, 10.0)
        assert 9 <= controller.cache_manager.stats <= 11

    def test_a_look_that_finds_nothing_reads_nothing(self, controller):
        controller._control_server = FakeServer()
        _poll_for(controller, 10.0)
        assert controller.cache_manager.reads == []

    def test_an_unchanged_mailbox_is_not_read_again(self, controller):
        # An already-processed start the delete could not remove, say.
        controller._control_server = FakeServer()
        controller.cache_manager.delete = MagicMock()   # the file stays
        _post(controller, 'once', plugin_id='clock')
        _poll_for(controller, 10.0)
        assert controller.cache_manager.reads.count(MAILBOX) <= 2   # the read + the re-check
        controller._activate_on_demand.assert_called_once()

    def test_a_mailbox_request_lands_within_a_second_with_the_socket_up(self, controller):
        # The upgrade case the other way round: a new display, and a web
        # interface (or a plugin) that still writes the mailbox.
        controller._control_server = FakeServer()
        controller._poll_on_demand_requests()
        controller.clock.t += 0.1
        _post(controller, 'old-web', plugin_id='clock')
        posted = controller.clock.t
        while not controller._activate_on_demand.called:
            controller._poll_on_demand_requests()
            controller.clock.t += 0.05
            assert controller.clock.t - posted < 1.5
        assert controller.clock.t - posted <= controller.MAILBOX_POLL_INTERVAL_WITH_SOCKET + 0.06
        assert MAILBOX in controller.cache_manager.deletes   # consumed

    def test_socket_commands_still_land_at_once(self, controller):
        server = controller._control_server = FakeServer()
        controller._poll_on_demand_requests()
        server.commands.append(QueuedCommand('sock', Command.ON_DEMAND_START,
                                             OnDemandStartArgs(plugin_id='clock'), time.time()))
        controller._poll_on_demand_requests()   # inside the mailbox interval
        controller._activate_on_demand.assert_called_once()


class TestSocketCommandsLeaveTheMailboxAlone:
    def test_a_socket_start_reads_and_deletes_no_mailbox(self, controller):
        server = controller._control_server = FakeServer()
        controller._poll_on_demand_requests()
        before = list(controller.cache_manager.reads)
        server.commands.append(QueuedCommand('s1', Command.ON_DEMAND_START,
                                             OnDemandStartArgs(plugin_id='clock'), time.time()))
        controller._poll_on_demand_requests()
        controller._activate_on_demand.assert_called_once()
        assert MAILBOX not in controller.cache_manager.reads[len(before):]
        assert controller.cache_manager.deletes == []

    def test_a_socket_stop_reads_and_deletes_no_mailbox(self, controller):
        from src.ipc.contract import OnDemandStopArgs
        controller.on_demand_active = True
        controller._clear_on_demand = MagicMock()
        server = controller._control_server = FakeServer()
        server.commands.append(QueuedCommand('s2', Command.ON_DEMAND_STOP,
                                             OnDemandStopArgs(), time.time()))
        controller.clock.t += 5
        controller._drain_control_commands()
        controller._clear_on_demand.assert_called_once()
        assert MAILBOX not in controller.cache_manager.reads
        assert controller.cache_manager.deletes == []

    def test_a_mailbox_copy_of_a_socket_command_is_dropped(self, controller):
        # An older web interface timed out after the display queued the
        # command, then wrote the mailbox too.
        server = controller._control_server = FakeServer()
        server.commands.append(QueuedCommand('both', Command.ON_DEMAND_START,
                                             OnDemandStartArgs(plugin_id='clock'), time.time()))
        controller._poll_on_demand_requests()
        _post(controller, 'both', plugin_id='clock')
        _poll_for(controller, 2.0)
        controller._activate_on_demand.assert_called_once()
        assert MAILBOX not in controller.cache_manager.data


class TestDeprecationLog:
    def test_each_mailbox_writer_is_logged_once(self, controller, caplog):
        controller._control_server = FakeServer()
        caplog.set_level(logging.INFO, logger='src.display_controller')
        for i, plugin in enumerate(['on-air', 'on-air', 'pomodoro-timer']):
            _post(controller, f'r{i}', plugin_id=plugin)
            _poll_for(controller, 1.2)
        lines = [r.getMessage() for r in caplog.records if 'file mailbox' in r.getMessage()]
        assert len(lines) == 2
        assert 'on-air' in lines[0] and 'pomodoro-timer' in lines[1]

    def test_nothing_is_logged_without_a_socket(self, controller, caplog):
        controller._control_server = None
        caplog.set_level(logging.INFO, logger='src.display_controller')
        _post(controller, 'r', plugin_id='on-air')
        _poll_for(controller, 1.0)
        controller._activate_on_demand.assert_called_once()
        assert not [r for r in caplog.records if 'file mailbox' in r.getMessage()]


# -- file_signature and MailboxWatch -------------------------------------------------

@pytest.fixture
def real_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(CacheManager, '_get_writable_cache_dir', lambda self: str(tmp_path))
    cache = CacheManager()
    yield cache
    cache.stop_cleanup_thread()


class TestFileSignature:
    def test_absent_key(self, real_cache):
        assert real_cache.file_signature('nothing') is None

    def test_every_write_is_a_new_signature(self, real_cache):
        seen = set()
        for i in range(20):
            # Same size each time, written as fast as possible.
            real_cache.set(MAILBOX, {'request_id': f'r{i:02d}'})
            sig = real_cache.file_signature(MAILBOX)
            assert isinstance(sig, tuple)
            seen.add(sig)
        assert len(seen) == 20

    def test_gone_after_a_delete(self, real_cache):
        real_cache.set(MAILBOX, {'a': 1})
        real_cache.delete(MAILBOX)
        assert real_cache.file_signature(MAILBOX) is None


class TestMailboxWatch:
    def test_reads_once_per_write(self, real_cache):
        watch = MailboxWatch(MAILBOX)
        assert watch.changed(real_cache) is False       # no file
        real_cache.set(MAILBOX, {'request_id': 'a'})
        assert watch.changed(real_cache) is True
        assert watch.changed(real_cache) is False
        real_cache.set(MAILBOX, {'request_id': 'b'})
        assert watch.changed(real_cache) is True

    def test_forget_reads_again(self, real_cache):
        watch = MailboxWatch(MAILBOX)
        real_cache.set(MAILBOX, {'request_id': 'a'})
        assert watch.changed(real_cache) is True
        watch.forget()
        assert watch.changed(real_cache) is True

    def test_a_rewrite_after_a_delete_is_seen(self, real_cache):
        watch = MailboxWatch(MAILBOX)
        real_cache.set(MAILBOX, {'request_id': 'a'})
        assert watch.changed(real_cache)
        real_cache.delete(MAILBOX)
        assert watch.changed(real_cache) is False
        real_cache.set(MAILBOX, {'request_id': 'a'})
        assert watch.changed(real_cache) is True

    def test_a_cache_that_cannot_tell_is_read_every_time(self):
        watch = MailboxWatch(MAILBOX)
        assert watch.changed(MagicMock()) is True
        assert watch.changed(MagicMock()) is True
        assert watch.changed(object()) is True


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

    def test_an_older_display_is_an_upgrade_fallback(self, sock_path):
        server = ControlServer(sock_path)   # no errors.clear handler
        assert server.start()
        try:
            with pytest.raises(client.ControlError) as e:
                client.errors_clear('clr-2', 1.0, paths=[sock_path])
            assert e.value.reason == 'unknown_command' and e.value.sent is True
            assert client.should_fall_back(e.value)
        finally:
            server.close()

    def test_no_display_is_a_fallback(self, sock_path):
        with pytest.raises(client.ControlError) as e:
            client.errors_clear('clr-3', 1.0, paths=[sock_path])
        assert e.value.reason == 'no_socket' and e.value.sent is False
        assert client.should_fall_back(e.value)

    def test_a_full_queue_is_not_a_fallback(self, sock_path):
        server = ControlServer(sock_path, queue_size=1)
        assert server.start()
        try:
            client.on_demand_start('q1', 'clock', None, paths=[sock_path])
            with pytest.raises(client.ControlError) as e:
                client.on_demand_start('q2', 'clock', None, paths=[sock_path])
            assert e.value.reason == 'busy' and e.value.sent is True
            assert not client.should_fall_back(e.value)
        finally:
            server.close()
