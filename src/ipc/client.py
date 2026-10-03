"""The web side of the control socket: one request, a short timeout, no retries.

Every failure -- no socket (the display is stopped, or predates the socket),
a refused or timed-out connection, a reply that breaks the contract, or an
error the display returned -- raises :class:`ControlError` with a short
``reason``, and the caller falls back to the file mailbox. Nothing here
blocks for longer than ``timeout`` in total.
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from src.ipc.contract import (
    AWAIT_SECONDS,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    SUBSCRIBE_KEEPALIVE_SECONDS,
    SUPPORTED_VERSIONS,
    Command,
    FrameReader,
    ProtocolError,
    Request,
    Response,
    StateEvent,
    StateEventKind,
    client_socket_paths,
    decode_message,
    encode_message,
    parse_args,
    socket_supported,
)

#: Total budget for one request: connect, send and the reply. The display
#: answers from a thread that does no rendering, normally within a few
#: milliseconds; this only bounds a wedged one. The web route then falls back
#: to the mailbox, so a timeout costs this much latency and nothing else.
DEFAULT_TIMEOUT_SECONDS = 1.0


class ControlError(Exception):
    """The socket could not carry the request. ``reason`` is a short code.

    Transport reasons: ``disabled``, ``unsupported``, ``no_socket``,
    ``refused``, ``timeout``, ``closed``, ``bad_response``, ``invalid_request``.
    When the display answered with an error, ``reason`` is that error's
    :class:`~src.ipc.contract.ErrorCode` (``busy``, ``unknown_command``, ...).
    """

    def __init__(self, reason: str, message: str = ''):
        super().__init__(reason, message)
        self.reason = reason
        self.message = message

    def __str__(self) -> str:
        return f'{self.reason}: {self.message}' if self.message else self.reason


def request(cmd: str, args: Optional[Mapping[str, Any]] = None, *,
            request_id: Optional[str] = None,
            timeout: float = DEFAULT_TIMEOUT_SECONDS,
            paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Send one command and return its ``result``. Raises :class:`ControlError`."""
    args = dict(args or {})
    request_id = request_id or str(uuid.uuid4())
    try:
        # Refuse locally what the display would refuse: a malformed id
        # (callers may pass their own) or arguments that break the contract.
        envelope = Request.from_dict({'v': PROTOCOL_VERSION, 'id': request_id,
                                      'cmd': cmd, 'args': args})
        parse_args(cmd, args)
        payload = encode_message(envelope.to_dict())
    except ProtocolError as e:
        raise ControlError('invalid_request', e.message) from None

    if not socket_supported():
        raise ControlError('unsupported', 'no Unix sockets on this platform')
    candidates: List[str] = list(paths) if paths is not None else client_socket_paths()
    if not candidates:
        raise ControlError('disabled', 'the control socket is turned off')

    deadline = time.monotonic() + timeout
    sock = _connect(candidates, deadline)
    try:
        response = _exchange(sock, payload, deadline)
    finally:
        sock.close()

    # A refusal before the request was read (forbidden, too many
    # connections) carries no id.
    if response.id != request_id and not (response.id is None and not response.ok):
        raise ControlError('bad_response', 'the reply is for a different request')
    if not response.ok:
        error = response.error
        raise ControlError(error.code if error else 'bad_response',
                           error.message if error else '')
    return dict(response.result or {})


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise ControlError('timeout', 'no reply in time')
    return left


def _connect(paths: Sequence[str], deadline: float) -> socket.socket:
    last = ControlError('no_socket', 'the display is not serving the control socket')
    for path in paths:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(_remaining(deadline))
            sock.connect(path)
            return sock
        except (FileNotFoundError, NotADirectoryError):
            sock.close()
            continue
        except ConnectionRefusedError:
            sock.close()
            last = ControlError('refused', f'nothing is listening at {path}')
        except BlockingIOError:
            # EAGAIN: the listen backlog is full -- a live but swamped display.
            sock.close()
            raise ControlError('busy', 'the display is not accepting connections') from None
        except PermissionError:
            sock.close()
            last = ControlError('refused', f'no permission to connect to {path}')
        except socket.timeout:
            sock.close()
            raise ControlError('timeout', 'connect timed out') from None
        except ControlError:
            sock.close()
            raise
        except OSError as e:
            sock.close()
            last = ControlError('refused', f'{path}: {e}')
    raise last


def _exchange(sock: socket.socket, payload: bytes, deadline: float) -> Response:
    try:
        sock.settimeout(_remaining(deadline))
        sock.sendall(payload)
        reader = FrameReader(MAX_MESSAGE_BYTES)
        while True:
            sock.settimeout(_remaining(deadline))
            data = sock.recv(4096)
            if not data:
                raise ControlError('closed', 'the display closed the connection')
            lines = reader.feed(data)
            if lines:
                return Response.from_dict(decode_message(lines[0]))
    except socket.timeout:
        raise ControlError('timeout', 'no reply in time') from None
    except ProtocolError as e:
        raise ControlError('bad_response', e.message) from None
    except ControlError:
        raise
    except OSError as e:
        raise ControlError('closed', str(e)) from None


# -- commands ---------------------------------------------------------------------------

def on_demand_start(request_id: str, plugin_id: Optional[str], mode: Optional[str],
                    duration: Any = None, pinned: bool = False, *,
                    timeout: float = DEFAULT_TIMEOUT_SECONDS,
                    paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Ask the display to show a plugin now. Returns the ack; raises :class:`ControlError`.

    ``request_id`` doubles as the on-demand request id, so a request that a
    timed-out caller then also writes to the mailbox is processed only once.
    """
    args = {'plugin_id': plugin_id, 'mode': mode, 'duration': duration, 'pinned': pinned}
    return request(Command.ON_DEMAND_START, args, request_id=request_id,
                   timeout=timeout, paths=paths)


def on_demand_stop(request_id: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                   paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Ask the display to end on-demand. Returns the ack; raises :class:`ControlError`."""
    return request(Command.ON_DEMAND_STOP, {}, request_id=request_id,
                   timeout=timeout, paths=paths)


def on_demand_status(*, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                     paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """The display's live on-demand state. Raises :class:`ControlError`."""
    return request(Command.ON_DEMAND_STATUS, {}, timeout=timeout, paths=paths)


#: Headroom over the display's own wait for an awaited command, so its
#: ``pending`` answer arrives before the client gives up.
_AWAIT_MARGIN_SECONDS = 1.0


def _awaited_timeout(cmd: str) -> float:
    return AWAIT_SECONDS[cmd] + _AWAIT_MARGIN_SECONDS


def brightness_set(brightness: int, *, timeout: Optional[float] = None,
                   paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Set the panel's normal brightness now (transient: config.json is not
    written). Returns the applied :class:`~src.ipc.contract.BrightnessResult`;
    raises :class:`ControlError`.
    """
    return request(Command.BRIGHTNESS_SET, {'brightness': brightness},
                   timeout=_awaited_timeout(Command.BRIGHTNESS_SET) if timeout is None
                   else timeout, paths=paths)


def plugin_reload(plugin_id: str, *, timeout: Optional[float] = None,
                  paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Have the display reload a running plugin from disk.

    Returns :class:`~src.ipc.contract.PluginReloadResult` once the new code is
    running. Raises :class:`ControlError`: ``not_loaded`` (not running it),
    ``failed`` (the new version did not load), ``pending`` (not done in
    time; it will still happen), or a transport reason.
    """
    return request(Command.PLUGIN_RELOAD, {'plugin_id': plugin_id},
                   timeout=_awaited_timeout(Command.PLUGIN_RELOAD) if timeout is None
                   else timeout, paths=paths)


def ping(*, timeout: float = DEFAULT_TIMEOUT_SECONDS,
         paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    return request(Command.PING, {}, timeout=timeout, paths=paths)


def hello(client: str = 'web', *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
          paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Version negotiation: the result's ``version`` is the one both sides speak."""
    return request(Command.HELLO, {'versions': list(SUPPORTED_VERSIONS), 'client': client},
                   timeout=timeout, paths=paths)


# -- the state stream (stage 3) ---------------------------------------------------------

def state_get(since: Optional[int] = None, epoch: Optional[str] = None, *,
              timeout: float = DEFAULT_TIMEOUT_SECONDS,
              paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """The display's state now, as a :class:`~src.ipc.contract.StateSnapshot`.

    With ``since``/``epoch`` from an earlier answer, an unchanged state comes
    back in the short ``changed: false`` form. Raises :class:`ControlError`
    (``unknown_command`` from a display older than stage 3).
    """
    args: Dict[str, Any] = {}
    if since is not None:
        args['since'] = since
    if epoch is not None:
        args['epoch'] = epoch
    return request(Command.STATE_GET, args, timeout=timeout, paths=paths)


def snapshot_age(snapshot: Mapping[str, Any], now_mono: Optional[float] = None) -> float:
    """Seconds since ``snapshot`` arrived: ``received_mono`` (set by
    :meth:`StateSubscription.latest`) to now; 0 for a one-shot answer."""
    received = snapshot.get('received_mono')
    if isinstance(received, (int, float)) and not isinstance(received, bool):
        now_mono = time.monotonic() if now_mono is None else now_mono
        return max(now_mono - float(received), 0.0)
    return 0.0


def snapshot_loop_age(snapshot: Mapping[str, Any],
                      now_mono: Optional[float] = None) -> Optional[float]:
    """The render loop's heartbeat age now, from a state snapshot: the age the
    display measured when it answered, plus the time since the answer
    arrived. None when the display has no beat to report yet."""
    loop = snapshot.get('loop')
    if not isinstance(loop, dict):
        state = snapshot.get('state')
        loop = state.get('loop') if isinstance(state, dict) else None
    age = loop.get('heartbeat_age_seconds') if isinstance(loop, dict) else None
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        return None
    return max(float(age), 0.0) + snapshot_age(snapshot, now_mono)


#: A subscription that has heard nothing for this long is not trusted: the
#: display sends a tick at least every SUBSCRIBE_KEEPALIVE_SECONDS.
SUBSCRIPTION_SILENCE_SECONDS = 3 * SUBSCRIBE_KEEPALIVE_SECONDS

#: Reconnect backoff: the first retry, and the cap. A display that does not
#: know state.subscribe (stage 2 or older) is retried at the cap.
_RECONNECT_MIN_SECONDS = 1.0
_RECONNECT_MAX_SECONDS = 30.0

#: Failures that another try soon will not fix.
_SLOW_RETRY_REASONS = frozenset({'unknown_command', 'unsupported_version', 'disabled',
                                 'unsupported'})


class StateSubscription:
    """One ``state.subscribe`` connection, held on a daemon thread.

    Keeps the latest snapshot the display pushed, so a reader answers from
    memory (:meth:`latest`). Reconnects with a backoff when the display goes
    away. Never raises into the caller: :meth:`latest` is None whenever the
    copy cannot be vouched for (not connected, or silent for longer than
    ``silence``), and the caller falls back.
    """

    def __init__(self, paths: Optional[Sequence[str]] = None, *,
                 silence: float = SUBSCRIPTION_SILENCE_SECONDS,
                 connect_timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self._paths = list(paths) if paths is not None else None
        self._silence = silence
        self._connect_timeout = connect_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._snapshot: Optional[Dict[str, Any]] = None
        self._received: Optional[float] = None
        self._connected = False
        self._stop = threading.Event()
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        #: The reason the last connection ended (a ControlError reason).
        self.last_error: Optional[str] = None
        #: Full snapshots received: the subscribe answer and each state event.
        self.snapshots = 0

    # -- the reader's side ---------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    def latest(self) -> Optional[Dict[str, Any]]:
        """A copy of the latest snapshot, with ``received_mono`` (this
        process's monotonic clock when it arrived); None when not trusted."""
        with self._lock:
            if not self._connected or self._snapshot is None or self._received is None:
                return None
            if self._clock() - self._received > self._silence:
                return None
            snap = dict(self._snapshot)
            snap['received_mono'] = self._received
            return snap

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> 'StateSubscription':
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name='ledmatrix-state-feed',
                                            daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None

    # -- the feed thread -----------------------------------------------------

    def _run(self) -> None:
        backoff = _RECONNECT_MIN_SECONDS
        while not self._stop.is_set():
            try:
                self._follow()
                backoff = _RECONNECT_MIN_SECONDS
            except ControlError as e:
                self.last_error = e.reason
                if e.reason in _SLOW_RETRY_REASONS:
                    backoff = _RECONNECT_MAX_SECONDS
            except Exception as e:  # pylint: disable=broad-except
                self.last_error = type(e).__name__
            finally:
                with self._lock:
                    self._connected = False
                sock, self._sock = self._sock, None
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, _RECONNECT_MAX_SECONDS)

    def _follow(self) -> None:
        """Subscribe, then read events until the connection ends. Raises ControlError."""
        if not socket_supported():
            raise ControlError('unsupported', 'no Unix sockets on this platform')
        candidates = list(self._paths) if self._paths is not None else client_socket_paths()
        if not candidates:
            raise ControlError('disabled', 'the control socket is turned off')
        request_id = str(uuid.uuid4())
        payload = encode_message(Request(id=request_id, cmd=Command.STATE_SUBSCRIBE,
                                         args={}).to_dict())
        sock = _connect(candidates, time.monotonic() + self._connect_timeout)
        self._sock = sock
        try:
            sock.settimeout(self._connect_timeout)
            sock.sendall(payload)
            # A read waits for the next event; the display sends one at least
            # every keepalive, so this much silence means it is gone.
            sock.settimeout(self._silence)
            reader = FrameReader(MAX_MESSAGE_BYTES)
            first = True
            while not self._stop.is_set():
                data = sock.recv(65536)
                if not data:
                    raise ControlError('closed', 'the display closed the connection')
                for line in reader.feed(data):
                    obj = decode_message(line)
                    if first:
                        response = Response.from_dict(obj)
                        if not response.ok:
                            error = response.error
                            raise ControlError(error.code if error else 'bad_response',
                                               error.message if error else '')
                        self._store(dict(response.result or {}), full=True)
                        first = False
                        continue
                    event = StateEvent.from_dict(obj)
                    self._store(event.result, full=event.event == StateEventKind.STATE)
        except socket.timeout:
            raise ControlError('timeout', 'the display went quiet') from None
        except ProtocolError as e:
            raise ControlError('bad_response', e.message) from None
        except OSError as e:
            if self._stop.is_set():
                return
            raise ControlError('closed', str(e)) from None

    def _store(self, result: Dict[str, Any], full: bool) -> None:
        now = self._clock()
        with self._lock:
            if full and isinstance(result.get('state'), dict):
                self._snapshot = result
                self.snapshots += 1
            elif (self._snapshot is not None
                  and result.get('epoch') == self._snapshot.get('epoch')):
                # A tick: nothing changed but the render loop's liveness.
                snap = dict(self._snapshot)
                loop = result.get('loop')
                if isinstance(loop, dict):
                    snap['state'] = dict(snap.get('state') or {}, loop=loop)
                    snap['loop'] = loop
                snap['served_at'] = result.get('served_at', snap.get('served_at'))
                self._snapshot = snap
            else:
                return  # a tick before any state, or from another epoch
            self._received = now
            self._connected = True
