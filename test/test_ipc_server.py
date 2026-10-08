"""The display side of the control socket (src/ipc/server.py).

Two layers:

* ``handle_line`` and the permission model are plain functions of their
  input, tested on every platform: every request gets exactly one answer,
  garbage is answered rather than raised, queued commands are acked with
  their request id, and the render thread drains them in order.
* The socket itself (``TestLiveSocket``, ``TestPermissions``) needs AF_UNIX,
  so those tests are skipped on Windows and run on Linux (CI, WSL, a Pi): a
  real server on a tmp_path socket, driven by the real client and by raw
  sockets that misbehave -- garbage, oversize lines, a client that hangs up
  mid-message, one that never finishes -- while the server keeps serving.
"""

import json
import os
import socket
import stat
import threading
import time

import pytest

from src.ipc import client
from src.ipc import contract as c
from src.ipc import server as srv
from src.ipc.contract import Command, ErrorCode
from src.ipc.server import ControlServer, PeerCredentials, peer_allowed

needs_unix_sockets = pytest.mark.skipif(not c.socket_supported(),
                                        reason='AF_UNIX sockets are Linux/macOS only')


def _line(obj):
    return json.dumps(obj).encode()


def _req(cmd, args=None, rid='r1', v=1):
    return _line({'v': v, 'id': rid, 'cmd': cmd, 'args': args or {}})


@pytest.fixture
def status():
    return {'on_demand': {'active': False, 'status': 'idle'}, 'current_mode': 'clock'}


@pytest.fixture
def server(status, tmp_path):
    """A server that is never started: handle_line and drain only."""
    return ControlServer(str(tmp_path / 'unused.sock'), status_provider=lambda: dict(status),
                         queue_size=3)


class TestHandleLine:
    def test_ping(self, server):
        resp = server.handle_line(_req(Command.PING))
        assert resp.ok and resp.id == 'r1' and resp.result == {'pong': True}

    def test_hello_negotiates(self, server):
        resp = server.handle_line(_req(Command.HELLO, {'versions': [1, 5], 'client': 't'}, v=5))
        assert resp.ok
        assert resp.result['version'] == 1
        assert resp.result['commands'] == list(c.COMMANDS)
        assert resp.result['max_message_bytes'] == c.MAX_MESSAGE_BYTES
        assert resp.v == 1

    def test_hello_with_nothing_in_common(self, server):
        resp = server.handle_line(_req(Command.HELLO, {'versions': [9]}, v=9))
        assert not resp.ok and resp.error.code == ErrorCode.UNSUPPORTED_VERSION

    def test_other_commands_need_a_supported_version(self, server):
        resp = server.handle_line(_req(Command.PING, v=2))
        assert not resp.ok and resp.error.code == ErrorCode.UNSUPPORTED_VERSION
        assert resp.id == 'r1'

    @pytest.mark.parametrize('line, code, rid', [
        (b'not json', ErrorCode.BAD_JSON, None),
        (b'[1]', ErrorCode.BAD_JSON, None),
        (b'\xff', ErrorCode.BAD_JSON, None),
        (_line({'v': 1, 'cmd': 'ping'}), ErrorCode.BAD_REQUEST, None),
        (_line({'v': 'one', 'id': 'q', 'cmd': 'ping'}), ErrorCode.BAD_REQUEST, 'q'),
        (_req('shutdown_the_pi'), ErrorCode.UNKNOWN_COMMAND, 'r1'),
        (_req(Command.ON_DEMAND_START, {}), ErrorCode.INVALID_ARGS, 'r1'),
        (_req(Command.ON_DEMAND_START, {'plugin_id': 'p', 'duration': 'x'}),
         ErrorCode.INVALID_ARGS, 'r1'),
    ])
    def test_garbage_is_answered_not_raised(self, server, line, code, rid):
        resp = server.handle_line(line)
        assert not resp.ok
        assert resp.error.code == code
        assert resp.id == rid
        assert server.drain() == []

    def test_start_is_queued_and_acked(self, server):
        resp = server.handle_line(_req(Command.ON_DEMAND_START,
                                       {'plugin_id': 'clock', 'duration': 30, 'pinned': True},
                                       rid='abc'))
        assert resp.ok
        assert resp.result == {'accepted': True, 'request_id': 'abc', 'queued': 1}
        assert server.has_pending
        [cmd] = server.drain()
        assert not server.has_pending
        payload = cmd.as_on_demand_request()
        assert payload['request_id'] == 'abc'
        assert payload['action'] == 'start'
        assert payload['plugin_id'] == 'clock'
        assert payload['duration'] == 30.0 and payload['pinned'] is True

    def test_stop_is_queued_and_acked(self, server):
        resp = server.handle_line(_req(Command.ON_DEMAND_STOP, rid='s1'))
        assert resp.ok and resp.result['request_id'] == 's1'
        assert [x.as_on_demand_request()['action'] for x in server.drain()] == ['stop']

    def test_drain_keeps_arrival_order(self, server):
        for rid in ('a', 'b', 'c'):
            server.handle_line(_req(Command.ON_DEMAND_START, {'plugin_id': 'p'}, rid=rid))
        assert [x.request_id for x in server.drain()] == ['a', 'b', 'c']
        assert server.drain() == []

    def test_a_full_queue_says_busy_and_queues_nothing_more(self, server):
        for rid in ('a', 'b', 'c'):
            assert server.handle_line(_req(Command.ON_DEMAND_STOP, rid=rid)).ok
        resp = server.handle_line(_req(Command.ON_DEMAND_STOP, rid='d'))
        assert not resp.ok and resp.error.code == ErrorCode.BUSY and resp.id == 'd'
        assert [x.request_id for x in server.drain()] == ['a', 'b', 'c']

    def test_status_answers_from_the_provider_without_queueing(self, server, status):
        status['on_demand']['active'] = True
        resp = server.handle_line(_req(Command.ON_DEMAND_STATUS))
        assert resp.ok and resp.result['on_demand']['active'] is True
        assert not server.has_pending

    def test_a_failing_status_provider_is_an_internal_error(self, tmp_path):
        def boom():
            raise RuntimeError('render thread mid-update')
        s = ControlServer(str(tmp_path / 'x.sock'), status_provider=boom)
        resp = s.handle_line(_req(Command.ON_DEMAND_STATUS, rid='z'))
        assert not resp.ok and resp.error.code == ErrorCode.INTERNAL and resp.id == 'z'


class TestPermissionModel:
    """root, the display's own user, or the shared group -- nobody else."""
    OWN, GROUP = 0, 990

    @pytest.mark.parametrize('cred, groups, allowed', [
        (PeerCredentials(1, 0, 0), None, True),                      # root
        (PeerCredentials(1, 1000, 1000), frozenset({990}), True),    # web user, in group
        (PeerCredentials(1, 1000, 990), frozenset(), True),          # primary group
        (PeerCredentials(1, 1001, 1001), frozenset({27, 44}), False),
        (PeerCredentials(1, 65534, 65534), frozenset(), False),       # nobody
    ])
    def test_model(self, cred, groups, allowed):
        assert peer_allowed(cred, self.OWN, self.GROUP, groups) is allowed

    def test_own_user_without_a_group(self):
        assert peer_allowed(PeerCredentials(1, 1000, 1000), 1000, None, frozenset())
        assert not peer_allowed(PeerCredentials(1, 1001, 1001), 1000, None, frozenset({1}))

    def test_group_database_decides_when_proc_is_unreadable(self):
        seen = []

        def in_group(uid, gid):
            seen.append((uid, gid))
            return uid == 1000

        cred = PeerCredentials(1, 1000, 1000)
        assert peer_allowed(cred, 0, 990, None, in_group=in_group)
        assert not peer_allowed(PeerCredentials(1, 1001, 1001), 0, 990, None, in_group=in_group)
        assert seen == [(1000, 990), (1001, 990)]

    @pytest.mark.skipif(os.name != 'posix', reason='POSIX permission bits')
    def test_socket_group_follows_a_shared_cache_dir(self, tmp_path):
        shared = tmp_path / 'cache'
        shared.mkdir()
        os.chmod(shared, 0o2775)
        assert srv.resolve_socket_group(str(shared)) == shared.stat().st_gid

    @pytest.mark.skipif(os.name != 'posix', reason='POSIX permission bits')
    def test_a_private_cache_dir_falls_back_to_the_project_group(self, tmp_path, monkeypatch):
        private = tmp_path / 'cache'
        private.mkdir()
        os.chmod(private, 0o755)
        from src.common import permission_utils
        monkeypatch.setattr(permission_utils, 'get_shared_group_gid', lambda: 4242)
        assert srv.resolve_socket_group(str(private)) == 4242

    def test_mode_is_group_only_with_a_group(self, tmp_path):
        assert ControlServer(str(tmp_path / 'a'), group=990).socket_mode == 0o660
        assert ControlServer(str(tmp_path / 'b'), group=None).socket_mode == 0o600


class TestWhereTheServerListens:
    def test_off_means_no_server(self):
        assert srv.server_socket_path({c.SOCKET_PATH_ENV: 'off'}) is None
        assert srv.start_control_server(environ={c.SOCKET_PATH_ENV: 'off'}) is None

    @needs_unix_sockets
    def test_configured(self):
        assert srv.server_socket_path({c.SOCKET_PATH_ENV: '/tmp/x.sock'}) == '/tmp/x.sock'

    @needs_unix_sockets
    def test_unprivileged_dev_run_uses_the_per_user_path(self, monkeypatch):
        monkeypatch.setattr(os, 'geteuid', lambda: 1000)
        monkeypatch.setattr(os, 'access', lambda p, m: False)
        assert srv.server_socket_path({}) == c.dev_socket_path()

    @needs_unix_sockets
    def test_root_uses_run(self, monkeypatch):
        monkeypatch.setattr(os, 'geteuid', lambda: 0)
        assert srv.server_socket_path({}) == c.DEFAULT_SOCKET_PATH

    @pytest.mark.skipif(c.socket_supported(), reason='Windows only')
    def test_windows_skips_cleanly(self):
        assert srv.server_socket_path({}) is None
        assert ControlServer('x.sock').start() is False
        with pytest.raises(client.ControlError) as e:
            client.request(Command.PING, paths=['x.sock'])
        assert e.value.reason == 'unsupported'


# -- the real socket --------------------------------------------------------------------

@pytest.fixture
def sock_path(tmp_path_factory):
    # AF_UNIX paths are limited to ~107 bytes; pytest's tmp_path can be longer.
    import tempfile
    d = tempfile.mkdtemp(prefix='lmipc-')
    yield os.path.join(d, 'control.sock')
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def live(sock_path, status):
    servers = []

    def make(**kwargs):
        kwargs.setdefault('status_provider', lambda: dict(status))
        s = ControlServer(sock_path, **kwargs)
        assert s.start()
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


def _raw(path, timeout=2.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(path)
    return s


def _read_line(s):
    buf = b''
    while not buf.endswith(b'\n'):
        try:
            chunk = s.recv(1)   # one byte at a time: never eat the next line
        except ConnectionResetError:
            break
        if not chunk:
            break
        buf += chunk
    return json.loads(buf) if buf.endswith(b'\n') else None


@needs_unix_sockets
class TestLiveSocket:
    def test_client_round_trip(self, live, sock_path):
        live()
        assert client.request(Command.PING, paths=[sock_path]) == {'pong': True}
        assert client.hello(paths=[sock_path])['version'] == 1
        assert client.on_demand_status(paths=[sock_path])['current_mode'] == 'clock'

    def test_ack_path(self, live, sock_path):
        server = live()
        ack = client.on_demand_start('req-1', 'clock', None, 20, True, paths=[sock_path])
        assert ack == {'accepted': True, 'request_id': 'req-1', 'queued': 1}
        ack = client.on_demand_stop('req-2', paths=[sock_path])
        assert ack['request_id'] == 'req-2'
        assert [(x.request_id, x.cmd) for x in server.drain()] == [
            ('req-1', Command.ON_DEMAND_START), ('req-2', Command.ON_DEMAND_STOP)]

    def test_socket_file_mode_and_cleanup(self, live, sock_path):
        server = live(group=os.getgid())
        st = os.lstat(sock_path)
        assert stat.S_ISSOCK(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o660
        assert st.st_gid == os.getgid()
        assert not [f for f in os.listdir(os.path.dirname(sock_path)) if f.endswith('.tmp')]
        server.close()
        assert not os.path.exists(sock_path)

    def test_without_a_group_only_the_owner_may_connect(self, live, sock_path):
        live(group=None)
        assert stat.S_IMODE(os.lstat(sock_path).st_mode) == 0o600

    def test_garbage_then_a_good_request_on_one_connection(self, live, sock_path):
        live()
        s = _raw(sock_path)
        try:
            s.sendall(b'this is not json\n')
            assert _read_line(s)['error']['code'] == ErrorCode.BAD_JSON
            s.sendall(_req(Command.PING, rid='after') + b'\n')
            resp = _read_line(s)
            assert resp['ok'] and resp['id'] == 'after'
        finally:
            s.close()

    def test_two_requests_in_one_write(self, live, sock_path):
        live()
        s = _raw(sock_path)
        try:
            s.sendall(_req(Command.PING, rid='a') + b'\n' + _req(Command.PING, rid='b') + b'\n')
            assert _read_line(s)['id'] == 'a'
            assert _read_line(s)['id'] == 'b'
        finally:
            s.close()

    def test_oversize_is_refused_and_the_server_lives_on(self, live, sock_path):
        live()
        s = _raw(sock_path)
        try:
            try:
                # Exactly the limit with no newline: the server has read it
                # all when it refuses, so its answer is not lost to a reset.
                s.sendall(b'{"pad":"' + b'x' * (c.MAX_MESSAGE_BYTES - 8))
            except OSError:
                pass  # the server may hang up before we finish writing
            resp = _read_line(s)
            assert resp['error']['code'] == ErrorCode.MESSAGE_TOO_LARGE
            assert s.recv(10) == b''          # and hung up
        finally:
            s.close()
        assert client.request(Command.PING, paths=[sock_path]) == {'pong': True}

    def test_a_client_that_hangs_up_mid_message(self, live, sock_path):
        server = live()
        s = _raw(sock_path)
        s.sendall(b'{"v":1,"id":"half","cmd":"on_demand.st')
        s.close()
        time.sleep(0.2)
        assert client.request(Command.PING, paths=[sock_path]) == {'pong': True}
        assert server.drain() == []

    def test_a_slow_client_is_dropped_and_blocks_nobody(self, live, sock_path):
        live(io_timeout=0.2, message_timeout=0.5)
        slow = _raw(sock_path, timeout=3)
        try:
            slow.sendall(b'{"v":1,')           # ...and never finishes
            t0 = time.monotonic()
            assert client.request(Command.PING, paths=[sock_path]) == {'pong': True}
            assert time.monotonic() - t0 < 0.5, 'a slow client held up another'
            assert slow.recv(100) == b''       # hung up on, not answered
        finally:
            slow.close()

    def test_an_idle_connection_is_closed(self, live, sock_path):
        live(io_timeout=0.1, idle_timeout=0.3)
        s = _raw(sock_path, timeout=3)
        try:
            assert s.recv(100) == b''
        finally:
            s.close()

    def test_too_many_clients_are_told_busy(self, live, sock_path):
        live(max_clients=2, io_timeout=0.2, idle_timeout=5)
        held = [_raw(sock_path) for _ in range(2)]
        try:
            time.sleep(0.1)
            extra = _raw(sock_path)
            try:
                assert _read_line(extra)['error']['code'] == ErrorCode.BUSY
            finally:
                extra.close()
        finally:
            for s in held:
                s.close()
        time.sleep(0.3)
        assert client.request(Command.PING, paths=[sock_path]) == {'pong': True}

    def test_many_concurrent_clients(self, live, sock_path):
        server = live(queue_size=64)
        errors = []

        def go(n):
            try:
                client.on_demand_start(f'r{n}', 'p', None, paths=[sock_path], timeout=3)
            except client.ControlError as e:  # busy is allowed under load
                if e.reason != ErrorCode.BUSY:
                    errors.append(e)

        threads = [threading.Thread(target=go, args=(n,)) for n in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert 0 < len(server.drain()) <= 20

    def test_a_stale_socket_is_replaced(self, sock_path, status):
        dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dead.bind(sock_path)
        dead.close()                    # file left behind, nothing listening
        s = ControlServer(sock_path, status_provider=lambda: status)
        try:
            assert s.start()
            assert client.request(Command.PING, paths=[sock_path]) == {'pong': True}
        finally:
            s.close()

    def test_a_live_socket_is_not_stolen(self, live, sock_path, status):
        live()
        second = ControlServer(sock_path, status_provider=lambda: status)
        assert second.start() is False
        assert client.request(Command.PING, paths=[sock_path]) == {'pong': True}

    def test_a_regular_file_is_never_removed(self, sock_path):
        with open(sock_path, 'w') as f:
            f.write('precious')
        assert ControlServer(sock_path).start() is False
        with open(sock_path) as f:
            assert f.read() == 'precious'

    def test_close_leaves_a_successor_s_socket_alone(self, sock_path, status):
        first = ControlServer(sock_path, status_provider=lambda: status)
        assert first.start()
        first._close_socket()                 # dead, but still owns the path
        os.unlink(sock_path)
        second = ControlServer(sock_path, status_provider=lambda: status)
        assert second.start()
        try:
            first.close()                     # must not unlink second's file
            assert client.request(Command.PING, paths=[sock_path]) == {'pong': True}
        finally:
            second.close()

    def test_client_reasons(self, sock_path, tmp_path):
        with pytest.raises(client.ControlError) as e:
            client.request(Command.PING, paths=[sock_path])
        assert e.value.reason == 'no_socket'
        dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dead.bind(sock_path)
        try:
            with pytest.raises(client.ControlError) as e:
                client.request(Command.PING, paths=[sock_path])
            assert e.value.reason == 'refused'
        finally:
            dead.close()
        with pytest.raises(client.ControlError) as e:
            client.request(Command.PING, paths=[])
        assert e.value.reason == 'disabled'
        with pytest.raises(client.ControlError) as e:
            client.on_demand_start('x', None, None, paths=[sock_path])
        assert e.value.reason == 'invalid_request'

    def test_a_display_that_never_answers_times_out(self, sock_path):
        mute = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        mute.bind(sock_path)
        mute.listen(1)                  # accepts at the kernel, never replies
        try:
            t0 = time.monotonic()
            with pytest.raises(client.ControlError) as e:
                client.request(Command.PING, paths=[sock_path], timeout=0.3)
            assert e.value.reason == 'timeout'
            assert time.monotonic() - t0 < 1.0
        finally:
            mute.close()

    def test_the_dev_directory_must_be_private(self, monkeypatch, tmp_path):
        d = tmp_path / 'shared'
        d.mkdir()
        target = d / 'control.sock'
        monkeypatch.setattr(srv, 'dev_socket_path', lambda: str(target))
        monkeypatch.setattr(os, 'geteuid', lambda: os.getuid() + 1)   # "someone else's"
        assert ControlServer(str(target)).start() is False


@needs_unix_sockets
@pytest.mark.skipif(not hasattr(socket, 'SO_PEERCRED'), reason='SO_PEERCRED is Linux-only')
class TestPermissions:
    def test_peer_credentials_are_read(self, live, sock_path):
        server = live()
        s = _raw(sock_path)
        try:
            s.sendall(_req(Command.PING) + b'\n')
            assert _read_line(s)['ok']
        finally:
            s.close()
        a, b = socket.socketpair(socket.AF_UNIX)
        try:
            cred = srv.peer_credentials(a)
            assert cred.uid == os.geteuid() and cred.pid == os.getpid()
        finally:
            a.close()
            b.close()
        assert srv.process_groups(os.getpid()) == frozenset(os.getgroups())
        assert server.running

    @pytest.mark.skipif(hasattr(os, 'geteuid') and os.geteuid() == 0,
                        reason='root may always connect')
    def test_a_peer_outside_the_model_is_refused(self, live, sock_path):
        server = live(group=None)
        # Pretend the display runs as someone else: this process is then
        # neither root, the display's user, nor in its (absent) group.
        server._own_uid = os.geteuid() + 12345
        s = _raw(sock_path)
        try:
            resp = _read_line(s)
            assert resp['error']['code'] == ErrorCode.FORBIDDEN
            assert s.recv(10) == b''
        finally:
            s.close()
        with pytest.raises(client.ControlError) as e:
            client.on_demand_stop('nope', paths=[sock_path])
        assert e.value.reason == ErrorCode.FORBIDDEN
        assert server.drain() == []

    @pytest.mark.skipif(not (hasattr(os, 'geteuid') and os.geteuid() == 0),
                        reason='needs root to switch users (run under WSL as root, or on a Pi)')
    def test_the_kernel_enforces_the_group(self, live, sock_path):
        """The real deployment shape: root serves, an unprivileged user connects.

        nobody in the socket's group gets in; nobody outside it gets EACCES
        from connect() -- the kernel's check, before any byte is read.
        """
        import pwd
        nobody = pwd.getpwnam('nobody')
        allowed_gid = nobody.pw_gid
        live(group=allowed_gid)
        os.chmod(os.path.dirname(sock_path), 0o755)

        def try_as(gid):
            r, w = os.pipe()
            pid = os.fork()
            if pid == 0:  # child: drop to nobody with only `gid`
                os.close(r)
                try:
                    os.setgroups([])
                    os.setgid(gid)
                    os.setuid(nobody.pw_uid)
                    result = json.dumps(client.request(Command.PING, paths=[sock_path]))
                except client.ControlError as e:
                    result = 'error:' + e.reason
                except Exception as e:  # report anything else to the parent
                    result = 'crash:' + repr(e)
                os.write(w, result.encode())
                os._exit(0)
            os.close(w)
            out = b''
            while True:
                chunk = os.read(r, 4096)
                if not chunk:
                    break
                out += chunk
            os.close(r)
            os.waitpid(pid, 0)
            return out.decode()

        assert try_as(allowed_gid) == '{"pong": true}'
        other_gid = allowed_gid - 1 if allowed_gid > 1 else allowed_gid + 1
        assert try_as(other_gid) == 'error:refused'
        # Someone loosens the mode by hand: the kernel lets the outsider
        # connect, and SO_PEERCRED still turns it away.
        os.chmod(sock_path, 0o666)
        assert try_as(other_gid) == 'error:forbidden'
        assert try_as(allowed_gid) == '{"pong": true}'
