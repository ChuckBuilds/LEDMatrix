"""Control socket stage 2 (src/ipc): waking the render thread, and the
awaited commands ``brightness.set`` and ``plugin.reload``.

What these pin, on every platform unless marked:

* the new commands' arguments refuse what the display could not act on;
* an awaited command is answered with the render thread's outcome -- its
  result, its error code, or ``pending`` when the render thread did not get
  to it in time (the command stays queued and is still applied);
* ``wait_for_command`` returns as soon as a command is queued, and otherwise
  sleeps for the whole timeout without spinning;
* over a real socket (Linux/macOS), the client waits for that outcome and
  maps each error code to its ``ControlError`` reason.
"""

import json
import os
import threading
import time

import pytest

from src.ipc import client
from src.ipc import contract as c
from src.ipc.contract import (
    BrightnessSetArgs, Command, ErrorCode, PluginReloadArgs, ProtocolError,
)
from src.ipc.server import CommandOutcome, ControlServer

needs_unix_sockets = pytest.mark.skipif(not c.socket_supported(),
                                        reason='AF_UNIX sockets are Linux/macOS only')


def _req(cmd, args=None, rid='r1'):
    return json.dumps({'v': 1, 'id': rid, 'cmd': cmd, 'args': args or {}}).encode()


def _server(tmp_path, **kw):
    kw.setdefault('await_seconds', {Command.BRIGHTNESS_SET: 2.0, Command.PLUGIN_RELOAD: 2.0})
    return ControlServer(str(tmp_path / 'unused.sock'), **kw)


class RenderThread:
    """Stands in for the display's render thread: waits on the server the
    way DisplayController does and settles each command with ``handler``."""

    def __init__(self, server, handler):
        self.server = server
        self.handler = handler
        self.stop = threading.Event()
        self.woke_at = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.is_set():
            if self.server.wait_for_command(0.05):
                self.woke_at.append(time.monotonic())
                for command in self.server.drain():
                    self.handler(command)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(2)


class TestArguments:
    @pytest.mark.parametrize('value', [0, 1, 55, 100])
    def test_brightness_in_range(self, value):
        assert BrightnessSetArgs.from_dict({'brightness': value}).brightness == value

    @pytest.mark.parametrize('value', [-1, 101, 50.0, '50', True, None, [50]])
    def test_brightness_refused(self, value):
        with pytest.raises(ProtocolError) as e:
            BrightnessSetArgs.from_dict({'brightness': value})
        assert e.value.code == ErrorCode.INVALID_ARGS

    def test_reload_needs_a_plugin_id(self):
        assert PluginReloadArgs.from_dict({'plugin_id': 'clock'}).plugin_id == 'clock'
        for bad in ({}, {'plugin_id': ''}, {'plugin_id': 5}, {'plugin_id': 'x' * 200},
                    {'plugin_id': 'a\nb'}):
            with pytest.raises(ProtocolError) as e:
                PluginReloadArgs.from_dict(bad)
            assert e.value.code == ErrorCode.INVALID_ARGS

    def test_the_new_commands_are_queued_and_awaited(self):
        for cmd in (Command.BRIGHTNESS_SET, Command.PLUGIN_RELOAD):
            assert cmd in c.COMMANDS and cmd in c.QUEUED_COMMANDS and cmd in c.AWAITED_COMMANDS
        # On-demand stays an ack: its outcome is published, not awaited.
        assert Command.ON_DEMAND_START not in c.AWAITED_COMMANDS
        # Additive within version 1 (see the contract's docstring).
        assert c.SUPPORTED_VERSIONS == (1,)

    def test_the_client_outwaits_the_display(self):
        """The client's timeout must cover the display's wait, or a slow
        reload would be reported as a timeout instead of ``pending``."""
        for cmd in c.AWAITED_COMMANDS:
            assert client._awaited_timeout(cmd) > c.AWAIT_SECONDS[cmd]


class TestOutcome:
    def test_first_settlement_wins(self):
        outcome = CommandOutcome()
        outcome.succeed({'a': 1})
        outcome.fail(ErrorCode.FAILED, 'late')
        assert outcome.done and outcome.result == {'a': 1} and outcome.error_code is None

    def test_wait_times_out(self):
        assert CommandOutcome().wait(0.01) is False


class TestAwaitedCommands:
    def test_brightness_answers_with_the_render_thread_result(self, tmp_path):
        server = _server(tmp_path)

        def apply(command):
            assert command.cmd == Command.BRIGHTNESS_SET
            command.succeed({'brightness': command.args.brightness, 'panel_brightness': 40,
                             'dimmed': False, 'display_active': True})

        with RenderThread(server, apply):
            resp = server.handle_line(_req(Command.BRIGHTNESS_SET, {'brightness': 40}))
        assert resp.ok, resp.error
        assert resp.result == {'brightness': 40, 'panel_brightness': 40,
                               'dimmed': False, 'display_active': True}

    @pytest.mark.parametrize('code', [ErrorCode.NOT_LOADED, ErrorCode.FAILED, ErrorCode.BUSY])
    def test_a_failure_carries_its_code(self, tmp_path, code):
        server = _server(tmp_path)
        with RenderThread(server, lambda command: command.fail(code, 'no')):
            resp = server.handle_line(_req(Command.PLUGIN_RELOAD, {'plugin_id': 'clock'}))
        assert not resp.ok and resp.error.code == code and resp.id == 'r1'

    def test_no_render_thread_means_pending_and_the_command_stays_queued(self, tmp_path):
        server = _server(tmp_path, await_seconds={Command.PLUGIN_RELOAD: 0.05})
        started = time.monotonic()
        resp = server.handle_line(_req(Command.PLUGIN_RELOAD, {'plugin_id': 'clock'}))
        assert time.monotonic() - started < 1.0
        assert not resp.ok and resp.error.code == ErrorCode.PENDING
        # Still applied when the render thread gets there; nobody reads it.
        queued = server.drain()
        assert [(q.cmd, q.args.plugin_id) for q in queued] == [(Command.PLUGIN_RELOAD, 'clock')]
        queued[0].succeed({'reloaded': True})

    def test_bad_arguments_are_refused_before_queueing(self, tmp_path):
        server = _server(tmp_path)
        resp = server.handle_line(_req(Command.BRIGHTNESS_SET, {'brightness': 400}))
        assert not resp.ok and resp.error.code == ErrorCode.INVALID_ARGS
        assert not server.has_pending and server.drain() == []

    def test_a_full_queue_is_busy_without_waiting(self, tmp_path):
        server = _server(tmp_path, queue_size=1)
        server.handle_line(_req(Command.ON_DEMAND_STOP, rid='fill'))
        started = time.monotonic()
        resp = server.handle_line(_req(Command.BRIGHTNESS_SET, {'brightness': 10}))
        assert not resp.ok and resp.error.code == ErrorCode.BUSY
        assert time.monotonic() - started < 0.5

    def test_on_demand_is_still_acked_without_waiting(self, tmp_path):
        server = _server(tmp_path)
        resp = server.handle_line(_req(Command.ON_DEMAND_STOP))
        assert resp.ok and resp.result['accepted'] is True
        (queued,) = server.drain()
        assert queued.outcome is None
        queued.succeed({})          # a no-op for an acked command

    def test_an_on_demand_payload_is_only_built_for_on_demand(self, tmp_path):
        server = _server(tmp_path, await_seconds={Command.BRIGHTNESS_SET: 0.01})
        server.handle_line(_req(Command.BRIGHTNESS_SET, {'brightness': 10}))
        (queued,) = server.drain()
        with pytest.raises(TypeError):
            queued.as_on_demand_request()


class TestWake:
    """``wait_for_command`` is what the render thread waits on in place of a
    sleep: it must return as soon as a command is queued, and not before."""

    def test_returns_at_once_when_a_command_is_queued(self, tmp_path):
        server = _server(tmp_path)
        latencies = []
        for _ in range(20):
            queued_at = []

            def post():
                time.sleep(0.02)
                queued_at.append(time.monotonic())
                server.handle_line(_req(Command.ON_DEMAND_STOP))

            t = threading.Thread(target=post)
            t.start()
            assert server.wait_for_command(2.0) is True
            woke = time.monotonic()
            t.join()
            latencies.append(woke - queued_at[0])
            server.drain()
        latencies.sort()
        # Typically well under a millisecond; 50 ms is the budget for a
        # static screen, and a loaded CI box needs the slack.
        assert latencies[len(latencies) // 2] < 0.01, latencies
        assert latencies[-1] < 0.05, latencies

    def test_sleeps_the_whole_timeout_when_nothing_comes(self, tmp_path):
        server = _server(tmp_path)
        started = time.monotonic()
        cpu = time.process_time()
        assert server.wait_for_command(0.3) is False
        assert time.monotonic() - started >= 0.29
        # A timed wait, not a polling loop.
        assert time.process_time() - cpu < 0.05

    def test_drain_rearms_it(self, tmp_path):
        server = _server(tmp_path)
        server.handle_line(_req(Command.ON_DEMAND_STOP))
        assert server.wait_for_command(0) is True
        server.drain()
        assert server.wait_for_command(0.01) is False


# -- the real socket ------------------------------------------------------------------

@pytest.fixture
def sock_path():
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix='lmipc2-')
    yield os.path.join(d, 'control.sock')
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def live(sock_path):
    servers = []

    def make(**kwargs):
        s = ControlServer(sock_path, status_provider=lambda: {}, **kwargs)
        assert s.start()
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


@needs_unix_sockets
class TestOverTheSocket:
    def test_brightness_round_trip(self, live, sock_path):
        server = live()

        def apply(command):
            command.succeed({'brightness': command.args.brightness, 'panel_brightness': 30,
                             'dimmed': True, 'display_active': True})

        with RenderThread(server, apply):
            started = time.monotonic()
            result = client.brightness_set(70, paths=[sock_path])
            elapsed = time.monotonic() - started
        assert result == {'brightness': 70, 'panel_brightness': 30, 'dimmed': True,
                          'display_active': True}
        assert elapsed < 0.5, elapsed

    def test_reload_round_trip(self, live, sock_path):
        server = live()

        def apply(command):
            command.succeed({'plugin_id': command.args.plugin_id, 'reloaded': True,
                             'version': '2.0.0', 'modes': ['clock']})

        with RenderThread(server, apply):
            result = client.plugin_reload('clock', paths=[sock_path])
        assert result['reloaded'] is True and result['version'] == '2.0.0'

    @pytest.mark.parametrize('code', [ErrorCode.NOT_LOADED, ErrorCode.FAILED])
    def test_reload_errors_reach_the_client(self, live, sock_path, code):
        server = live()
        with RenderThread(server, lambda command: command.fail(code, 'x')):
            with pytest.raises(client.ControlError) as e:
                client.plugin_reload('clock', paths=[sock_path])
        assert e.value.reason == code

    def test_pending_reaches_the_client_before_its_own_timeout(self, live, sock_path):
        live(await_seconds={Command.PLUGIN_RELOAD: 0.1})
        with pytest.raises(client.ControlError) as e:
            client.plugin_reload('clock', paths=[sock_path], timeout=2.0)
        assert e.value.reason == ErrorCode.PENDING

    def test_invalid_arguments_never_leave_the_client(self, sock_path):
        with pytest.raises(client.ControlError) as e:
            client.brightness_set(500, paths=[sock_path])
        assert e.value.reason == 'invalid_request'

    def test_hello_lists_the_new_commands(self, live, sock_path):
        live()
        commands = client.hello(paths=[sock_path])['commands']
        assert Command.BRIGHTNESS_SET in commands and Command.PLUGIN_RELOAD in commands
