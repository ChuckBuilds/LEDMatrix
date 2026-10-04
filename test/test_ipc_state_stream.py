"""Control socket stage 3: the display's state over the socket (state.get,
state.subscribe) instead of polled cache keys.

* ``StateHub`` and the request handling are plain Python, tested on every
  platform: versions move only when something a reader sees changes, the
  ``since``/``epoch`` short answer, an oversized snapshot drops only its
  plugin section, and publishing never waits for a reader.
* ``TestLiveStream`` needs AF_UNIX (Linux, WSL, a Pi; skipped on Windows): a
  real server pushing to real subscribers -- several at once, one that never
  reads, one whose display goes away and comes back.
"""

import json
import socket
import threading
import time

import pytest

from src.ipc import client
from src.ipc import contract as c
from src.ipc import server as srv
from src.ipc.contract import Command, ErrorCode
from src.ipc.server import ControlServer, StateHub, fit_snapshot

needs_unix_sockets = pytest.mark.skipif(not c.socket_supported(),
                                        reason='AF_UNIX sockets are Linux/macOS only')


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def _req(cmd, args=None, rid='r1', v=1):
    return json.dumps({'v': v, 'id': rid, 'cmd': cmd, 'args': args or {}}).encode()


def _loop(age=1.0):
    return lambda: {'heartbeat_age_seconds': age, 'armed': age is not None,
                    'stale_after': 60.0}


def _display(mode='clock', active=True, updated=None):
    return {'mode': mode, 'plugin_id': mode, 'mode_index': 0, 'total_modes': 2,
            'on_demand_active': False, 'is_display_active': active,
            'last_updated': time.time() if updated is None else updated}


@pytest.fixture
def hub():
    h = StateHub(loop_probe=_loop(), epoch='e1', pid=4242)
    h.publish('display', _display(), volatile=('last_updated',))
    return h


# --- the hub ---------------------------------------------------------------------

class TestStateHub:
    def test_snapshot_has_every_section_and_the_envelope(self, hub):
        snap = hub.snapshot()
        assert snap['schema'] == c.STATE_SCHEMA
        assert (snap['version'], snap['epoch'], snap['pid']) == (1, 'e1', 4242)
        assert snap['changed'] is True
        assert list(snap['state']) == list(c.STATE_SECTIONS)
        assert snap['state']['display']['mode'] == 'clock'
        assert snap['state']['on_demand'] is None       # not published yet
        assert snap['state']['loop'] == snap['loop'] == _loop()()

    def test_only_a_real_change_is_a_new_version(self, hub):
        assert not hub.publish('display', _display(updated=1.0), volatile=('last_updated',))
        assert hub.version == 1
        # ...but the latest timestamp is what a reader gets.
        assert hub.snapshot()['state']['display']['last_updated'] == 1.0
        assert hub.publish('display', _display(mode='weather'), volatile=('last_updated',))
        assert hub.version == 2
        assert hub.publish('brightness', {'brightness': 50})
        assert not hub.publish('brightness', {'brightness': 50})
        assert hub.version == 3

    def test_the_published_dict_is_copied(self, hub):
        value = {'brightness': 50}
        hub.publish('brightness', value)
        value['brightness'] = 10
        assert hub.snapshot()['state']['brightness'] == {'brightness': 50}

    def test_since_the_current_version_is_the_short_answer(self, hub):
        short = hub.snapshot(since=1, epoch='e1')
        assert short['changed'] is False and 'state' not in short
        assert short['version'] == 1 and short['loop']['heartbeat_age_seconds'] == 1.0
        # An older version, or another epoch (a restarted display): the full state.
        assert hub.snapshot(since=0, epoch='e1')['changed'] is True
        assert hub.snapshot(since=1, epoch='other')['changed'] is True
        assert hub.snapshot(since=1)['changed'] is True

    def test_the_short_answer_carries_the_latest_volatile_values(self, hub):
        """A version that stays put must not freeze the timestamps a reader
        judges freshness by: the short answer (a tick) carries them."""
        hub.publish('on_demand', {'active': False, 'last_updated': 2.0, 'remaining': 0.0},
                    volatile=('last_updated', 'remaining'))
        hub.publish('brightness', {'brightness': 50})
        version = hub.version
        assert not hub.publish('display', _display(updated=1234.5), volatile=('last_updated',))
        short = hub.snapshot(since=version, epoch='e1')
        assert short['changed'] is False and 'state' not in short
        assert short['volatile'] == {'display': {'last_updated': 1234.5},
                                     'on_demand': {'last_updated': 2.0, 'remaining': 0.0}}
        # The full answer has them in place already.
        assert 'volatile' not in hub.snapshot()

    def test_volatile_values_skip_sections_without_any(self):
        hub = StateHub(epoch='e1')
        hub.publish('brightness', {'brightness': 50})
        hub.publish('plugins', None, volatile=('published_at',))
        assert hub.snapshot(since=hub.version, epoch='e1')['volatile'] == {}

    def test_each_display_run_has_its_own_epoch(self):
        assert StateHub().epoch != StateHub().epoch

    def test_the_loop_is_measured_when_asked(self):
        ages = iter([1.0, 61.0])
        hub = StateHub(loop_probe=lambda: {'heartbeat_age_seconds': next(ages),
                                           'armed': True, 'stale_after': 60.0})
        assert hub.snapshot()['loop']['heartbeat_age_seconds'] == 1.0
        # Nothing was published -- a stuck render thread publishes nothing --
        # and the age still moves.
        assert hub.snapshot()['loop']['heartbeat_age_seconds'] == 61.0

    def test_a_failing_probe_is_unknown_not_raised(self):
        def boom():
            raise RuntimeError('x')
        assert StateHub(loop_probe=boom).loop()['heartbeat_age_seconds'] is None
        assert StateHub().loop()['armed'] is False

    def test_wait_for_change(self, hub):
        assert hub.wait_for_change(1, 0.01) is False
        threading.Timer(0.05, lambda: hub.publish('brightness', {'b': 1})).start()
        start = time.monotonic()
        assert hub.wait_for_change(1, 5.0) is True
        assert time.monotonic() - start < 2.0

    def test_wait_ends_on_stop(self, hub):
        stop = threading.Event()

        def end():
            stop.set()
            hub.wake()
        threading.Timer(0.05, end).start()
        start = time.monotonic()
        assert hub.wait_for_change(1, 5.0, stop) is False
        assert time.monotonic() - start < 2.0

    def test_readers_active(self):
        clock = FakeClock()
        hub = StateHub(clock=clock, reader_window=60.0)
        assert not hub.readers_active()
        hub.note_read()
        clock.now += 59
        assert hub.readers_active()
        clock.now += 2
        assert not hub.readers_active()
        hub.subscriber_joined()
        clock.now += 1000
        assert hub.readers_active()            # for as long as it is connected
        hub.subscriber_left()
        assert hub.readers_active()            # and one window after
        clock.now += 61
        assert not hub.readers_active()

    def test_publishing_never_waits_for_readers(self, hub):
        """Many readers snapshotting and waiting at once: every publish from
        the 'render thread' still returns in well under a frame."""
        stop = threading.Event()

        def reader():
            version = 0
            while not stop.is_set():
                hub.wait_for_change(version, 0.01)
                version = hub.snapshot()['version']
        threads = [threading.Thread(target=reader, daemon=True) for _ in range(8)]
        for t in threads:
            t.start()
        worst = 0.0
        try:
            for i in range(500):
                start = time.perf_counter()
                hub.publish('display', _display(mode=f'm{i}'), volatile=('last_updated',))
                worst = max(worst, time.perf_counter() - start)
        finally:
            stop.set()
            for t in threads:
                t.join(2)
        assert worst < 0.05


class TestFitSnapshot:
    def test_small_is_unchanged(self, hub):
        snap = hub.snapshot()
        assert fit_snapshot(snap) is snap

    def test_too_large_drops_only_the_plugins(self, hub):
        plugins = {f'p{i}': {'error': {'message': 'x' * 200}} for i in range(400)}
        hub.publish('plugins', {'schema': 1, 'plugins': plugins})
        fitted = fit_snapshot(hub.snapshot())
        assert fitted['state']['plugins'] is None
        assert fitted['truncated'] == ['plugins']
        assert fitted['state']['display']['mode'] == 'clock'
        c.encode_message({'v': 1, 'id': 'x' * 128, 'ok': True, 'result': fitted})


# --- the commands, without a socket ------------------------------------------------

class TestHandleLine:
    def test_state_get(self, hub):
        server = ControlServer('/unused.sock', state_hub=hub)
        resp = server.handle_line(_req(Command.STATE_GET))
        assert resp.ok and resp.result['version'] == 1
        assert resp.result['state']['display']['mode'] == 'clock'
        assert hub.readers_active()

    def test_state_get_since(self, hub):
        server = ControlServer('/unused.sock', state_hub=hub)
        resp = server.handle_line(_req(Command.STATE_GET, {'since': 1, 'epoch': 'e1'}))
        assert resp.ok and resp.result['changed'] is False and 'state' not in resp.result

    @pytest.mark.parametrize('args', [{'since': -1}, {'since': 'x'}, {'since': True},
                                      {'epoch': 5}, {'epoch': 'x' * 200}])
    def test_bad_args(self, hub, args):
        server = ControlServer('/unused.sock', state_hub=hub)
        resp = server.handle_line(_req(Command.STATE_GET, args))
        assert not resp.ok and resp.error.code == ErrorCode.INVALID_ARGS

    def test_without_a_hub_it_is_an_error_not_a_crash(self):
        server = ControlServer('/unused.sock')
        for cmd in (Command.STATE_GET, Command.STATE_SUBSCRIBE):
            resp = server.handle_line(_req(cmd))
            assert not resp.ok and resp.error.code == ErrorCode.INTERNAL

    def test_hello_lists_the_new_commands(self, hub):
        resp = ControlServer('/unused.sock', state_hub=hub).handle_line(
            _req(Command.HELLO, {'versions': [1]}))
        assert {Command.STATE_GET, Command.STATE_SUBSCRIBE} <= set(resp.result['commands'])

    def test_state_commands_are_not_queued(self, hub):
        server = ControlServer('/unused.sock', state_hub=hub)
        server.handle_line(_req(Command.STATE_GET))
        assert not server.has_pending and server.drain() == []


class TestEvents:
    def test_round_trip(self):
        event = c.StateEvent('s1', c.StateEventKind.TICK, {'version': 3})
        obj = json.loads(c.encode_message(event.to_dict()))
        assert c.is_event(obj)
        assert c.StateEvent.from_dict(obj) == event

    def test_a_response_is_not_an_event(self):
        assert not c.is_event(c.Response.success('x', {}).to_dict())

    @pytest.mark.parametrize('obj', [
        [], {'v': 1, 'id': 's', 'event': 'other', 'result': {}},
        {'v': 1, 'id': 's', 'event': 'state', 'result': []},
        {'v': '1', 'id': 's', 'event': 'state', 'result': {}},
        {'v': 1, 'id': None, 'event': 'state', 'result': {}},
    ])
    def test_bad_events_are_refused(self, obj):
        with pytest.raises(c.ProtocolError):
            c.StateEvent.from_dict(obj)


class TestSubscriptionStore:
    """StateSubscription's bookkeeping, without a socket."""

    def test_latest_is_none_until_a_snapshot_and_after_silence(self, hub):
        clock = FakeClock()
        sub = client.StateSubscription(paths=['/nowhere'], silence=15.0, clock=clock)
        assert sub.latest() is None
        sub._store(hub.snapshot(), full=True)
        latest = sub.latest()
        assert latest['version'] == 1 and latest['received_mono'] == clock.now
        clock.now += 16
        assert sub.latest() is None

    def test_a_tick_refreshes_the_loop_and_keeps_the_state(self, hub):
        clock = FakeClock()
        sub = client.StateSubscription(paths=['/nowhere'], clock=clock)
        sub._store(hub.snapshot(), full=True)
        clock.now += 10
        tick = {'version': 1, 'epoch': 'e1', 'served_at': 5.0,
                'loop': {'heartbeat_age_seconds': 70.0, 'armed': True, 'stale_after': 60.0}}
        sub._store(tick, full=False)
        latest = sub.latest()
        assert latest['state']['display']['mode'] == 'clock'
        assert latest['state']['loop']['heartbeat_age_seconds'] == 70.0
        assert latest['received_mono'] == clock.now

    def test_a_tick_refreshes_the_volatile_timestamps(self, hub):
        """The bug from the ledpi rig: the same mode on screen for minutes
        left the reader's display.last_updated at the last real change."""
        clock = FakeClock()
        sub = client.StateSubscription(paths=['/nowhere'], clock=clock)
        sub._store(hub.snapshot(), full=True)
        hub.publish('display', _display(updated=9999.0), volatile=('last_updated',))
        sub._store(hub.snapshot(since=1, epoch='e1'), full=False)
        latest = sub.latest()
        assert latest['state']['display']['last_updated'] == 9999.0
        assert latest['state']['display']['mode'] == 'clock'
        assert latest['version'] == 1

    def test_a_tick_adds_no_section_or_key_and_ignores_another_version(self, hub):
        clock = FakeClock()
        sub = client.StateSubscription(paths=['/nowhere'], clock=clock)
        snap = hub.snapshot()
        sub._store(snap, full=True)
        updated = snap['state']['display']['last_updated']
        sub._store({'version': 1, 'epoch': 'e1',
                    'volatile': {'display': {'new_key': 1}, 'plugins': {'published_at': 5.0},
                                 'on_demand': 'junk'}}, full=False)
        state = sub.latest()['state']
        assert 'new_key' not in state['display'] and state['plugins'] is None
        assert state['on_demand'] is None
        # A tick for a version this copy is not at says nothing about it.
        sub._store({'version': 7, 'epoch': 'e1',
                    'volatile': {'display': {'last_updated': 5.0}}}, full=False)
        assert sub.latest()['state']['display']['last_updated'] == updated
        # A display from before ticks carried them: the loop still refreshes.
        sub._store({'version': 1, 'epoch': 'e1', 'loop': {'heartbeat_age_seconds': 3.0}},
                   full=False)
        assert sub.latest()['loop'] == {'heartbeat_age_seconds': 3.0}

    def test_a_tick_from_another_epoch_is_ignored(self, hub):
        clock = FakeClock()
        sub = client.StateSubscription(paths=['/nowhere'], clock=clock)
        sub._store(hub.snapshot(), full=True)
        clock.now += 10
        sub._store({'version': 9, 'epoch': 'other', 'loop': {}}, full=False)
        assert sub.latest()['received_mono'] == clock.now - 10

    def test_loop_age_counts_the_time_since_it_arrived(self, hub):
        snap = dict(hub.snapshot(), received_mono=100.0)
        assert client.snapshot_loop_age(snap, now_mono=104.0) == 5.0
        snap['loop'] = {'heartbeat_age_seconds': None}
        assert client.snapshot_loop_age(snap, now_mono=104.0) is None


class TestReconnectBackoff:
    """StateSubscription._run's waits between connections, without a socket."""

    def test_a_connection_that_got_a_snapshot_starts_the_backoff_over(self, hub,
                                                                       monkeypatch):
        """Three failed tries, then the display is back twice, restarting
        each time, then gone again. Each restart is retried after the
        shortest wait, not after whatever the waits had grown to."""
        sub = client.StateSubscription(paths=['/nowhere'])
        script = ['refused', 'refused', 'refused', 'snapshot', 'snapshot', 'refused']
        waits = []

        def follow():
            step = script.pop(0)
            if step == 'snapshot':      # subscribed, then the display restarted
                sub._store(hub.snapshot(), full=True)
                raise client.ControlError('closed', 'the display closed the connection')
            raise client.ControlError(step)

        def wait(seconds):
            waits.append(seconds)
            return not script           # True ends _run, as stop() would

        monkeypatch.setattr(sub, '_follow', follow)
        monkeypatch.setattr(sub._stop, 'wait', wait)
        sub._run()
        first = client._RECONNECT_MIN_SECONDS
        assert waits == [first, 2 * first, 4 * first, first, first, 2 * first]

    def test_a_display_without_the_stream_is_still_retried_slowly(self, monkeypatch):
        sub = client.StateSubscription(paths=['/nowhere'])
        waits = []

        def follow():
            raise client.ControlError('unknown_command')

        def wait(seconds):
            waits.append(seconds)
            return len(waits) == 2

        monkeypatch.setattr(sub, '_follow', follow)
        monkeypatch.setattr(sub._stop, 'wait', wait)
        sub._run()
        assert waits == [client._RECONNECT_MAX_SECONDS] * 2


# --- a real socket ------------------------------------------------------------------

def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@needs_unix_sockets
class TestLiveStream:
    @pytest.fixture
    def path(self, tmp_path):
        return str(tmp_path / 'control.sock')

    @pytest.fixture
    def live(self, path, hub):
        server = ControlServer(path, state_hub=hub, keepalive=0.2, io_timeout=0.5,
                               max_clients=2, max_subscribers=3)
        assert server.start()
        yield server, hub
        server.close()

    def _subscribe(self, path):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(path)
        sock.sendall(c.encode_message({'v': 1, 'id': 's1', 'cmd': Command.STATE_SUBSCRIBE,
                                       'args': {}}))
        return sock, c.FrameReader()

    def _next(self, sock, reader, pending):
        while not pending:
            data = sock.recv(65536)
            assert data, 'the display hung up'
            pending.extend(reader.feed(data))
        return json.loads(pending.pop(0))

    def test_state_get_over_the_socket(self, live, path):
        snap = client.state_get(paths=[path])
        assert snap['version'] == 1 and snap['state']['display']['mode'] == 'clock'
        short = client.state_get(since=snap['version'], epoch=snap['epoch'], paths=[path])
        assert short['changed'] is False

    def test_subscribe_answers_then_pushes_changes_in_order(self, live, path):
        _, hub = live
        sock, reader = self._subscribe(path)
        pending = []
        first = self._next(sock, reader, pending)
        assert first['ok'] is True and first['id'] == 's1' and first['result']['version'] == 1
        hub.publish('display', _display(mode='weather'), volatile=('last_updated',))
        versions = []
        while True:
            msg = self._next(sock, reader, pending)
            assert c.is_event(msg) and msg['id'] == 's1'
            if msg['event'] == 'state':
                versions.append(msg['result']['version'])
                assert msg['result']['state']['display']['mode'] == 'weather'
                break
        hub.publish('brightness', {'brightness': 30})
        while True:
            msg = self._next(sock, reader, pending)
            if msg['event'] == 'state':
                versions.append(msg['result']['version'])
                break
        assert versions == [2, 3]
        sock.close()

    def test_ticks_keep_a_quiet_subscription_alive(self, live, path):
        sock, reader = self._subscribe(path)
        pending = []
        self._next(sock, reader, pending)
        kinds = [self._next(sock, reader, pending)['event'] for _ in range(3)]
        assert kinds == ['tick', 'tick', 'tick']
        sock.close()

    def test_ticks_carry_the_timestamps_the_version_ignores(self, live, path):
        _, hub = live
        sock, reader = self._subscribe(path)
        pending = []
        self._next(sock, reader, pending)
        hub.publish('display', _display(updated=4242.0), volatile=('last_updated',))
        for _ in range(3):   # a tick already on its way may predate the publish
            tick = self._next(sock, reader, pending)
            assert tick['event'] == 'tick'
            if tick['result']['volatile'] == {'display': {'last_updated': 4242.0}}:
                break
        else:
            pytest.fail(f'no tick carried the new last_updated: {tick}')
        sock.close()

    def test_a_subscription_keeps_last_updated_current(self, live, path):
        _, hub = live
        sub = client.StateSubscription(paths=[path]).start()
        try:
            assert _wait_until(lambda: sub.latest() is not None)
            hub.publish('display', _display(updated=4242.0), volatile=('last_updated',))
            assert _wait_until(lambda: (sub.latest() or {}).get('state', {})
                               .get('display', {}).get('last_updated') == 4242.0)
            assert sub.snapshots == 1        # ticks, not new snapshots
        finally:
            sub.stop()

    def test_a_burst_is_coalesced_to_the_latest(self, live, path):
        _, hub = live
        sock, reader = self._subscribe(path)
        pending = []
        self._next(sock, reader, pending)
        for i in range(200):
            hub.publish('display', _display(mode=f'm{i}'), volatile=('last_updated',))
        seen = []
        while not seen or seen[-1] != hub.version:
            msg = self._next(sock, reader, pending)
            if msg['event'] == 'state':
                seen.append(msg['result']['version'])
        assert seen == sorted(seen) and len(seen) < 200
        assert msg['result']['state']['display']['mode'] == 'm199'
        sock.close()

    def test_several_subscribers_and_commands_still_get_a_slot(self, live, path):
        server, hub = live
        subs = [client.StateSubscription(paths=[path]).start() for _ in range(3)]
        try:
            assert _wait_until(lambda: all(s.latest() for s in subs))
            assert hub.subscribers == 3
            # max_clients is 2 and three streams are open: subscribers gave
            # their request slots back.
            for _ in range(4):
                assert client.ping(paths=[path]) == {'pong': True}
            hub.publish('display', _display(mode='weather'), volatile=('last_updated',))
            assert _wait_until(lambda: all(
                s.latest()['state']['display']['mode'] == 'weather' for s in subs))
            # A fourth is over the subscriber bound.
            sock, reader = self._subscribe(path)
            refused = self._next(sock, reader, [])
            assert refused['ok'] is False and refused['error']['code'] == ErrorCode.BUSY
            sock.close()
        finally:
            for s in subs:
                s.stop()
        assert _wait_until(lambda: hub.subscribers == 0)

    def test_a_subscriber_that_never_reads_blocks_nobody(self, live, path):
        """The render thread keeps publishing at full speed, a reading
        subscriber keeps up, and the stuck one is dropped."""
        _, hub = live
        stuck = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        stuck.connect(path)
        stuck.sendall(c.encode_message({'v': 1, 'id': 'stuck',
                                        'cmd': Command.STATE_SUBSCRIBE, 'args': {}}))
        good = client.StateSubscription(paths=[path]).start()
        try:
            assert _wait_until(lambda: good.latest() is not None)
            assert _wait_until(lambda: hub.subscribers == 2)
            big = {f'p{i}': {'state': 'enabled', 'note': 'x' * 100} for i in range(100)}
            worst = 0.0
            deadline = time.monotonic() + 3.0
            i = 0
            while time.monotonic() < deadline:
                i += 1
                start = time.perf_counter()
                hub.publish('plugins', {'schema': 1, 'n': i, 'plugins': big})
                worst = max(worst, time.perf_counter() - start)
                time.sleep(0.005)
            assert worst < 0.05, f'a publish took {worst:.3f}s'
            # io_timeout is 0.5 s: the stuck one is gone, the good one is not.
            assert _wait_until(lambda: hub.subscribers == 1)
            assert _wait_until(lambda: (good.latest() or {}).get('state', {})
                               .get('plugins', {}).get('n') == i)
        finally:
            stuck.close()
            good.stop()

    def test_close_ends_the_streams(self, path, hub):
        server = ControlServer(path, state_hub=hub, keepalive=30.0)
        assert server.start()
        sub = client.StateSubscription(paths=[path]).start()
        try:
            assert _wait_until(lambda: sub.latest() is not None)
            start = time.monotonic()
            server.close()
            assert _wait_until(lambda: hub.subscribers == 0, timeout=3.0)
            assert time.monotonic() - start < 3.0
            assert _wait_until(lambda: sub.latest() is None, timeout=3.0)
        finally:
            sub.stop()

    def test_the_subscription_follows_a_restarted_display(self, path, hub):
        server = ControlServer(path, state_hub=hub, keepalive=0.2)
        assert server.start()
        sub = client.StateSubscription(paths=[path]).start()
        try:
            assert _wait_until(lambda: sub.latest() is not None)
            server.close()
            assert _wait_until(lambda: sub.latest() is None)
            hub2 = StateHub(loop_probe=_loop(), epoch='e2')
            hub2.publish('display', _display(mode='restarted'), volatile=('last_updated',))
            server2 = ControlServer(path, state_hub=hub2, keepalive=0.2)
            assert server2.start()
            try:
                assert _wait_until(lambda: (sub.latest() or {}).get('epoch') == 'e2',
                                   timeout=8.0)
                assert sub.latest()['state']['display']['mode'] == 'restarted'
            finally:
                server2.close()
        finally:
            sub.stop()

    def test_a_server_without_state_answers_an_error(self, path):
        server = ControlServer(path)    # no hub
        assert server.start()
        try:
            with pytest.raises(client.ControlError) as err:
                client.state_get(paths=[path])
            assert err.value.reason == ErrorCode.INTERNAL
        finally:
            server.close()

    def test_no_display_is_no_socket(self, path):
        with pytest.raises(client.ControlError) as err:
            client.state_get(paths=[path])
        assert err.value.reason == 'no_socket'
