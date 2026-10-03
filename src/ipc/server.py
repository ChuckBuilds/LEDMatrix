"""The display side of the control socket.

A small threaded server on a Unix stream socket (``/run/ledmatrix/control.sock``
by default; see :mod:`src.ipc.contract` for the protocol). It never touches
rendering: a command that changes the panel is validated, put on a bounded
queue and acknowledged, and the render thread drains that queue at the point
where it reads the file mailbox (``DisplayController._poll_on_demand_requests``),
handing each command to the same code. Queries (``on_demand.status``) are
answered from a snapshot callable the display provides.

The queue also wakes the render thread: :meth:`ControlServer.wait_for_command`
is what it waits on in place of a sleep, so a command lands within a frame on
every kind of screen. An awaited command (``brightness.set``,
``plugin.reload``) carries a :class:`CommandOutcome` that the render thread
fills in; its connection thread waits for that, bounded, before answering.

The state stream (stage 3): the display publishes what it is doing into a
:class:`StateHub`, in memory, and ``state.get`` / ``state.subscribe`` read
it. A subscriber's connection gives back its request slot, takes one of
:data:`~src.ipc.contract.MAX_SUBSCRIBERS`, and is pushed the latest version
on every change plus a keepalive tick, from its own thread: publishing never
waits for a reader, and a reader that stops reading is dropped.

Robustness rules, because this runs inside the display process:

* every connection has its own daemon thread, at most :data:`MAX_CLIENTS` at
  once; one more is told ``busy`` and closed;
* every read and write has a timeout, a message must arrive whole within
  :data:`MESSAGE_TIMEOUT_SECONDS`, and an idle connection is closed after
  :data:`IDLE_TIMEOUT_SECONDS` -- a slow or stuck client costs one thread for
  a few seconds, never the render loop;
* a line that is not JSON is answered with ``bad_json`` and the connection
  carries on; a line over the size limit closes the connection; a client
  that disconnects mid-message is simply dropped;
* no exception from a handler leaves the connection thread.

Who may connect (see docs/IPC_CONTROL_SOCKET.md, "Security model"): the
socket file is ``0660`` and group-owned by the group the display and the web
interface share -- the cache directory's group, the same rule DiskCache uses
for the files it shares -- so the kernel refuses everyone else at connect().
Where the kernel reports the peer's credentials (``SO_PEERCRED``, Linux) the
server checks them again: root, its own user, or a member of that group.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
import stat
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

from src.ipc.contract import (
    AWAIT_SECONDS,
    AWAITED_COMMANDS,
    COMMANDS,
    DEFAULT_SOCKET_DIR,
    DEFAULT_SOCKET_PATH,
    MAX_MESSAGE_BYTES,
    MAX_SUBSCRIBERS,
    PROTOCOL_VERSION,
    QUEUED_COMMANDS,
    STATE_SCHEMA,
    STATE_SECTIONS,
    SUBSCRIBE_KEEPALIVE_SECONDS,
    SUPPORTED_VERSIONS,
    AckResult,
    BrightnessSetArgs,
    Command,
    ErrorCode,
    FrameReader,
    HelloArgs,
    HelloResult,
    OnDemandStartArgs,
    OnDemandStopArgs,
    PluginReloadArgs,
    ProtocolError,
    QueuedArgs,
    Request,
    Response,
    StateEvent,
    StateEventKind,
    StateGetArgs,
    configured_socket_path,
    decode_message,
    dev_socket_path,
    encode_message,
    negotiate_version,
    on_demand_request,
    parse_args,
    socket_disabled,
    socket_supported,
)

logger = logging.getLogger(__name__)

#: Concurrent connections served. The web interface opens one per request
#: and closes it; this only bounds a misbehaving client.
MAX_CLIENTS = 8

#: Commands waiting for the render thread. It drains them at least every
#: 0.25 s, so a full queue means the render thread is stuck, and the client
#: is told ``busy`` (and falls back to the mailbox) instead of piling up work.
QUEUE_SIZE = 16

#: Timeout for one recv()/send() on a connection.
IO_TIMEOUT_SECONDS = 2.0

#: A message must arrive whole within this long of its first byte.
MESSAGE_TIMEOUT_SECONDS = 5.0

#: A connection with no message in progress is closed after this long.
IDLE_TIMEOUT_SECONDS = 10.0

#: How often the accept loop wakes to notice close().
_ACCEPT_POLL_SECONDS = 0.5

_LISTEN_BACKLOG = 64


# -- queued work ---------------------------------------------------------------------

class CommandOutcome:
    """How an awaited command turned out, handed from the render thread back
    to the connection thread that is waiting to answer.

    The render thread calls :meth:`succeed` or :meth:`fail` once; the first
    call wins. The connection thread may have stopped waiting already (it
    answered ``pending``), and then nobody reads it.
    """

    def __init__(self) -> None:
        self._done = threading.Event()
        self._lock = threading.Lock()
        self.result: Optional[Dict[str, Any]] = None
        self.error_code: Optional[str] = None
        self.error_message = ''

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def succeed(self, result: Mapping[str, Any]) -> None:
        with self._lock:
            if self._done.is_set():
                return
            self.result = dict(result)
            self._done.set()

    def fail(self, code: str, message: str) -> None:
        with self._lock:
            if self._done.is_set():
                return
            self.error_code = code
            self.error_message = message
            self._done.set()

    def wait(self, timeout: float) -> bool:
        return self._done.wait(timeout)


@dataclass(frozen=True)
class QueuedCommand:
    """A command waiting for the render thread.

    ``outcome`` is set for an awaited command (``AWAITED_COMMANDS``): the
    render thread reports through it, and the client's answer waits for it.
    """
    request_id: str
    cmd: str
    args: QueuedArgs
    received_at: float          # time.time() when it was accepted
    peer_uid: Optional[int] = None
    outcome: Optional[CommandOutcome] = field(default=None, compare=False, repr=False)

    def as_on_demand_request(self) -> Dict[str, Any]:
        """The mailbox-shaped payload the display's on-demand handler takes."""
        if not isinstance(self.args, (OnDemandStartArgs, OnDemandStopArgs)):
            raise TypeError(f'{self.cmd} is not an on-demand command')
        return on_demand_request(self.request_id, self.args, self.received_at)

    def succeed(self, result: Mapping[str, Any]) -> None:
        """Report success to a waiting client (a no-op for an acked command)."""
        if self.outcome is not None:
            self.outcome.succeed(result)

    def fail(self, code: str, message: str) -> None:
        """Report failure to a waiting client (a no-op for an acked command)."""
        if self.outcome is not None:
            self.outcome.fail(code, message)


# -- the state stream (stage 3) ----------------------------------------------------------

#: How long a ``state.get`` keeps the display counting its readers as served
#: over the socket (:meth:`StateHub.readers_active`). A subscriber counts for
#: as long as it is connected.
READER_WINDOW_SECONDS = 60.0

#: Room kept for the envelope (``v``, ``id``, ``event``) around a snapshot,
#: within MAX_MESSAGE_BYTES.
_ENVELOPE_ROOM = 512

_MISSING = object()

LoopProbe = Callable[[], Mapping[str, Any]]


def _fingerprint(value: Optional[Mapping[str, Any]], volatile: Iterable[str]) -> Any:
    """What a section's version is judged on: the value minus its volatile keys
    (timestamps that move on every publish without anything changing)."""
    if value is None:
        return None
    skip = frozenset(volatile)
    return {k: v for k, v in value.items() if k not in skip} if skip else dict(value)


def _unknown_loop() -> Dict[str, Any]:
    return {'heartbeat_age_seconds': None, 'armed': False, 'stale_after': None}


def fit_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """``snapshot``, or a copy without the plugin runtime section when the
    message would be over MAX_MESSAGE_BYTES (hundreds of plugins). The
    reader then falls back to the cache for that section only; ``truncated``
    says which was left out."""
    state = snapshot.get('state')
    if not isinstance(state, dict) or state.get('plugins') is None:
        return snapshot
    try:
        size = len(json.dumps(snapshot, separators=(',', ':'), ensure_ascii=True,
                              allow_nan=False))
    except (TypeError, ValueError):
        size = MAX_MESSAGE_BYTES
    if size <= MAX_MESSAGE_BYTES - _ENVELOPE_ROOM:
        return snapshot
    logger.warning("State snapshot is %d bytes; sending it without the plugin runtime "
                   "section", size)
    trimmed = dict(snapshot)
    trimmed['state'] = dict(state, plugins=None)
    trimmed['truncated'] = ['plugins']
    return trimmed


class StateHub:
    """The display's live state, in memory, for ``state.get`` and ``state.subscribe``.

    Writers publish whole sections (:meth:`publish`): the render thread
    publishes ``display``, ``on_demand`` and ``brightness``, and the plugin
    runtime publisher's thread publishes ``plugins``. Each section has one
    writer. ``loop`` is not published: it is measured when a reader asks
    (``loop_probe``), so it keeps ageing while the render thread is stuck.

    The version goes up when a section's value changes, ignoring the keys
    the publisher names as volatile (timestamps). Publishing never blocks on
    a reader: the lock is held only to swap a dict reference and compare it,
    and every socket write happens on the reader's own thread, outside it.
    A reader that is slow gets the latest version when it next asks, not
    every version in between.
    """

    def __init__(self, loop_probe: Optional[LoopProbe] = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time,
                 epoch: Optional[str] = None, pid: Optional[int] = None,
                 reader_window: float = READER_WINDOW_SECONDS):
        self._cond = threading.Condition(threading.Lock())
        self._sections: Dict[str, Optional[Dict[str, Any]]] = {}
        self._fingerprints: Dict[str, Any] = {}
        self._version = 0
        self.epoch = epoch or uuid.uuid4().hex[:16]
        self.pid = os.getpid() if pid is None else pid
        self._loop_probe = loop_probe
        self._clock = clock
        self._wall_clock = wall_clock
        self._reader_window = reader_window
        self._last_read: Optional[float] = None
        self._subscribers = 0

    @property
    def version(self) -> int:
        return self._version

    @property
    def subscribers(self) -> int:
        return self._subscribers

    # -- writers -------------------------------------------------------------

    def publish(self, section: str, value: Optional[Mapping[str, Any]],
                volatile: Iterable[str] = ()) -> bool:
        """Store a section's latest value; True when that is a new version.

        The value is copied (one level), so the caller may reuse its dict.
        """
        stored = None if value is None else dict(value)
        fingerprint = _fingerprint(stored, volatile)
        with self._cond:
            self._sections[section] = stored
            if self._fingerprints.get(section, _MISSING) == fingerprint:
                return False
            self._fingerprints[section] = fingerprint
            self._version += 1
            self._cond.notify_all()
        return True

    def wake(self) -> None:
        """Wake every waiting reader (the server is closing)."""
        with self._cond:
            self._cond.notify_all()

    # -- readers -------------------------------------------------------------

    def loop(self) -> Dict[str, Any]:
        """The render loop's liveness now. Never raises."""
        if self._loop_probe is None:
            return _unknown_loop()
        try:
            return dict(self._loop_probe())
        except Exception:  # pylint: disable=broad-except
            logger.debug("Render loop liveness probe failed", exc_info=True)
            return _unknown_loop()

    def snapshot(self, since: Optional[int] = None,
                 epoch: Optional[str] = None) -> Dict[str, Any]:
        """The :class:`~src.ipc.contract.StateSnapshot` now.

        ``since`` with this hub's ``epoch``, still the current version, gives
        the short ``changed: false`` form.
        """
        with self._cond:
            version = self._version
            sections = dict(self._sections)
        loop = self.loop()
        result: Dict[str, Any] = {
            'schema': STATE_SCHEMA,
            'version': version,
            'epoch': self.epoch,
            'pid': self.pid,
            'served_at': self._wall_clock(),
            'loop': loop,
        }
        if since is not None and epoch == self.epoch and since == version:
            result['changed'] = False
            return result
        state: Dict[str, Any] = {name: sections.get(name) for name in STATE_SECTIONS
                                 if name != 'loop'}
        state['loop'] = loop
        result['changed'] = True
        result['state'] = state
        return result

    def wait_for_change(self, version: int, timeout: float,
                        stop: Optional[threading.Event] = None) -> bool:
        """Block up to ``timeout`` for a version other than ``version``."""
        with self._cond:
            self._cond.wait_for(
                lambda: self._version != version or (stop is not None and stop.is_set()),
                timeout)
            return self._version != version

    # -- who is reading ------------------------------------------------------

    def note_read(self) -> None:
        self._last_read = self._clock()

    def subscriber_joined(self) -> None:
        with self._cond:
            self._subscribers += 1

    def subscriber_left(self) -> None:
        with self._cond:
            self._subscribers = max(0, self._subscribers - 1)
            self._last_read = self._clock()

    def readers_active(self) -> bool:
        """Is the socket serving state readers? A subscriber is connected, or a
        ``state.get`` came within the reader window. The display uses this to
        write the cache copies of the same state less often."""
        if self._subscribers > 0:
            return True
        last = self._last_read
        return last is not None and self._clock() - last < self._reader_window


class _Slot:
    """A connection slot, released once (a subscriber gives its back early)."""

    def __init__(self, semaphore: threading.BoundedSemaphore):
        self._semaphore = semaphore
        self._held = True
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._held:
                self._held = False
                self._semaphore.release()


# -- peer credentials ------------------------------------------------------------------

@dataclass(frozen=True)
class PeerCredentials:
    pid: int
    uid: int
    gid: int


def peer_credentials(conn: socket.socket) -> Optional[PeerCredentials]:
    """The connecting process's pid/uid/gid, where the kernel reports them.

    ``SO_PEERCRED`` is Linux's; elsewhere this is None and the socket file's
    mode is the only gate.
    """
    option = getattr(socket, 'SO_PEERCRED', None)
    if option is None:
        return None
    try:
        raw = conn.getsockopt(socket.SOL_SOCKET, option, struct.calcsize('3i'))
        pid, uid, gid = struct.unpack('3i', raw)
    except (OSError, struct.error):
        return None
    return PeerCredentials(pid=pid, uid=uid, gid=gid)


def process_groups(pid: int) -> Optional[FrozenSet[int]]:
    """A process's supplementary groups, from /proc; None when unreadable.

    The web service's primary group is normally its user's own; the shared
    group is a supplementary one, which ``SO_PEERCRED`` does not report.
    """
    try:
        with open(f'/proc/{int(pid)}/status', 'r', encoding='ascii', errors='replace') as f:
            for line in f:
                if line.startswith('Groups:'):
                    return frozenset(int(g) for g in line.split()[1:] if g.isdigit())
    except (OSError, ValueError):
        return None
    return frozenset()


def user_in_group(uid: int, gid: int) -> bool:
    """Whether the account ``uid`` is listed in group ``gid`` (the group database)."""
    try:
        import grp
        import pwd
        name = pwd.getpwuid(uid).pw_name
        group = grp.getgrgid(gid)
    except (ImportError, KeyError, OSError):
        return False
    return name in group.gr_mem or pwd.getpwuid(uid).pw_gid == gid


def peer_allowed(cred: PeerCredentials, own_uid: int, allowed_gid: Optional[int],
                 groups: Optional[FrozenSet[int]] = None,
                 in_group: Callable[[int, int], bool] = user_in_group) -> bool:
    """The permission model: root, the server's own user, or the shared group.

    ``groups`` are the peer's supplementary groups (from /proc); when they
    could not be read the group database decides instead.
    """
    if cred.uid == 0 or cred.uid == own_uid:
        return True
    if allowed_gid is None:
        return False
    if cred.gid == allowed_gid:
        return True
    if groups is not None:
        return allowed_gid in groups
    return in_group(cred.uid, allowed_gid)


def resolve_socket_group(cache_dir: Optional[str]) -> Optional[int]:
    """The group the socket should belong to: the one the two services share.

    The cache directory's group when the directory is group-writable --
    the rule DiskCache applies to every file the display shares with the web
    interface (``root:ledmatrix 2775`` on an installed device). Otherwise the
    project directory's group (``get_shared_group_gid``), which config files
    use. None when neither is known: then only root and the display's own
    user can connect.
    """
    if cache_dir:
        try:
            st = os.stat(cache_dir)
            if st.st_mode & stat.S_IWGRP:
                return st.st_gid
        except OSError:
            pass
    try:
        from src.common.permission_utils import get_shared_group_gid
        return get_shared_group_gid()
    except ImportError:  # pragma: no cover - src is always importable here
        return None


def server_socket_path(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Where the display should serve the socket; None when it should not.

    :data:`~src.ipc.contract.SOCKET_PATH_ENV` wins. Otherwise
    /run/ledmatrix/control.sock when the display can create or write that
    directory (root, which an installed display always is), and the per-user
    dev path otherwise (an emulator run from a checkout).
    """
    if not socket_supported() or socket_disabled(environ):
        return None
    configured = configured_socket_path(environ)
    if configured:
        return configured
    geteuid = getattr(os, 'geteuid', None)
    if (geteuid is not None and geteuid() == 0) or os.access(DEFAULT_SOCKET_DIR, os.W_OK):
        return DEFAULT_SOCKET_PATH
    return dev_socket_path()


# -- the server ------------------------------------------------------------------------

StatusProvider = Callable[[], Dict[str, Any]]


class ControlServer:
    """Serves the control socket on background threads.

    ``start()`` binds and starts accepting; ``drain()`` (render thread) takes
    the queued commands; ``close()`` stops and removes the socket file.
    """

    def __init__(self, path: str, status_provider: Optional[StatusProvider] = None,
                 group: Optional[int] = None, *, queue_size: int = QUEUE_SIZE,
                 max_clients: int = MAX_CLIENTS, io_timeout: float = IO_TIMEOUT_SECONDS,
                 message_timeout: float = MESSAGE_TIMEOUT_SECONDS,
                 idle_timeout: float = IDLE_TIMEOUT_SECONDS,
                 check_peer: bool = True,
                 await_seconds: Optional[Mapping[str, float]] = None,
                 state_hub: Optional[StateHub] = None,
                 max_subscribers: int = MAX_SUBSCRIBERS,
                 keepalive: float = SUBSCRIBE_KEEPALIVE_SECONDS):
        self.path = path
        self.state_hub = state_hub
        self._subscriber_slots = threading.BoundedSemaphore(max_subscribers)
        self._keepalive = keepalive
        self._await_seconds: Dict[str, float] = dict(AWAIT_SECONDS)
        if await_seconds:
            self._await_seconds.update(await_seconds)
        self._status_provider = status_provider
        self._group = group
        self._queue: 'queue.Queue[QueuedCommand]' = queue.Queue(maxsize=queue_size)
        self._pending = threading.Event()
        self._slots = threading.BoundedSemaphore(max_clients)
        self._io_timeout = io_timeout
        self._message_timeout = message_timeout
        self._idle_timeout = idle_timeout
        self._check_peer = check_peer
        self._sock: Optional[socket.socket] = None
        self._identity: Optional[tuple] = None   # (st_dev, st_ino) of our socket file
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._own_uid = os.geteuid() if hasattr(os, 'geteuid') else -1

    # -- lifecycle -------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def socket_mode(self) -> int:
        """0660 with a shared group; 0600 (the display's user only) without one."""
        return 0o660 if self._group is not None else 0o600

    def start(self) -> bool:
        """Bind and start serving. False (logged) when the socket cannot be served.

        Never raises: without the socket the web interface uses the file
        mailbox, exactly as before.
        """
        if not socket_supported():
            logger.debug("Control socket not started: no Unix sockets on this platform")
            return False
        try:
            self._prepare_directory()
            if not self._clear_stale_socket():
                return False
            self._bind()
        except OSError as e:
            logger.warning("Control socket not started at %s (%s); the web interface "
                           "will use the file mailbox", self.path, e)
            self._close_socket()
            return False
        self._stopping.clear()
        self._thread = threading.Thread(target=self._accept_loop, name='ledmatrix-ipc',
                                        daemon=True)
        self._thread.start()
        logger.info("Control socket listening at %s (mode %o, group %s)",
                    self.path, self.socket_mode,
                    self._group if self._group is not None else 'none')
        return True

    def close(self) -> None:
        """Stop accepting and remove the socket file (only if it is still ours)."""
        self._stopping.set()
        self._close_socket()
        if self.state_hub is not None:
            self.state_hub.wake()   # subscribers see _stopping and hang up
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        if self._identity is not None:
            try:
                st = os.lstat(self.path)
                if (st.st_dev, st.st_ino) == self._identity:
                    os.unlink(self.path)
            except OSError:
                pass
            self._identity = None

    def _close_socket(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _prepare_directory(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        if self.path == dev_socket_path():
            # The dev path is in the shared temp dir: private to this user,
            # and refused if someone else got there first.
            os.makedirs(directory, mode=0o700, exist_ok=True)
            self._check_private_directory(directory)
        elif not os.path.isdir(directory):
            # /run/ledmatrix under a unit that predates RuntimeDirectory= (the
            # display is root and makes it, as it does for the heartbeat), or
            # a configured path. 0755: the web interface only needs to reach
            # the socket; the socket's own mode decides who may connect.
            os.makedirs(directory, mode=0o755, exist_ok=True)

    def _check_private_directory(self, directory: str) -> None:
        """Refuse a dev directory someone else made (it lives in a shared /tmp)."""
        st = os.lstat(directory)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise OSError(f'{directory} is not a plain directory')
        if hasattr(os, 'geteuid') and st.st_uid != os.geteuid():
            raise OSError(f'{directory} belongs to uid {st.st_uid}, not this user')

    def _clear_stale_socket(self) -> bool:
        """Remove a socket left by a display that died; never a live or foreign file."""
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            return True
        if not stat.S_ISSOCK(st.st_mode):
            logger.error("Control socket not started: %s exists and is not a socket", self.path)
            return False
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect(self.path)
        except OSError:
            os.unlink(self.path)   # nothing listening: a previous display's leftover
            return True
        finally:
            probe.close()
        logger.warning("Control socket not started: another process is serving %s", self.path)
        return False

    def _bind(self) -> None:
        """Bind under a temporary name, set mode and group, then rename into place.

        The rename makes the socket appear with its final permissions, never
        briefly with the process umask's.
        """
        tmp = f'{self.path}.{os.getpid()}.tmp'
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock = sock
        try:
            sock.bind(tmp)
            os.chmod(tmp, self.socket_mode)
            if self._group is not None and hasattr(os, 'chown'):
                try:
                    os.chown(tmp, -1, self._group)
                except OSError as e:
                    # Not root and not in the group (a dev run): only this user
                    # (and root) can connect, which is what a dev run needs.
                    logger.debug("Could not give the control socket group %s: %s",
                                 self._group, e)
            # The backlog is only the kernel's queue in front of accept();
            # MAX_CLIENTS still bounds what is served. A short one makes a
            # burst of clients fail connect() with EAGAIN instead of being
            # answered (busy or otherwise).
            sock.listen(_LISTEN_BACKLOG)
            sock.settimeout(_ACCEPT_POLL_SECONDS)
            os.rename(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        st = os.lstat(self.path)
        self._identity = (st.st_dev, st.st_ino)

    # -- the render thread's side ------------------------------------------------

    @property
    def has_pending(self) -> bool:
        """Cheap check for queued commands, for the render thread's fast path."""
        return self._pending.is_set()

    def wait_for_command(self, timeout: float) -> bool:
        """Block up to ``timeout`` seconds for a queued command; True if one is.

        The render thread waits here instead of sleeping, in the dwell and
        on a static screen, so a command wakes it at once. It is a timed
        wait on an Event: no polling, and nothing more than the sleep it
        replaces when no command comes. The flag stays set until drain(),
        so a caller that does not drain would return at once every time.
        """
        return self._pending.wait(timeout)

    def drain(self) -> List[QueuedCommand]:
        """Every queued command, oldest first. Called from the render thread."""
        commands: List[QueuedCommand] = []
        self._pending.clear()
        while True:
            try:
                commands.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return commands

    # -- serving -------------------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError as e:
                if self._stopping.is_set():
                    break
                logger.warning("Control socket accept failed: %s", e)
                time.sleep(0.1)
                continue
            if not self._slots.acquire(blocking=False):
                self._refuse(conn, ErrorCode.BUSY, 'too many connections')
                continue
            try:
                threading.Thread(target=self._serve, args=(conn,), name='ledmatrix-ipc-conn',
                                 daemon=True).start()
            except RuntimeError:  # can't start a thread: shed the client
                self._slots.release()
                self._refuse(conn, ErrorCode.BUSY, 'server overloaded')

    def _refuse(self, conn: socket.socket, code: str, message: str) -> None:
        try:
            conn.settimeout(0.2)
            conn.sendall(encode_message(Response.failure(None, code, message).to_dict()))
        except OSError:
            pass
        finally:
            conn.close()

    def _serve(self, conn: socket.socket) -> None:
        """One connection: authenticate, then answer requests until it ends."""
        slot = _Slot(self._slots)
        try:
            conn.settimeout(self._io_timeout)
            peer = peer_credentials(conn)
            if self._check_peer and peer is not None and not self._peer_ok(peer):
                logger.warning("Control socket refused pid %d (uid %d, gid %d): not root, "
                               "this user or group %s", peer.pid, peer.uid, peer.gid, self._group)
                self._send(conn, Response.failure(None, ErrorCode.FORBIDDEN, 'not permitted'))
                return
            self._read_requests(conn, peer, slot)
        except Exception:  # pylint: disable=broad-except
            logger.exception("Control socket connection failed")
        finally:
            try:
                conn.close()
            except OSError:
                pass
            slot.release()

    def _peer_ok(self, peer: PeerCredentials) -> bool:
        groups = None
        if peer.uid not in (0, self._own_uid) and self._group is not None:
            groups = process_groups(peer.pid)
        return peer_allowed(peer, self._own_uid, self._group, groups)

    def _read_requests(self, conn: socket.socket, peer: Optional[PeerCredentials],
                       slot: Optional[_Slot] = None) -> None:
        reader = FrameReader(MAX_MESSAGE_BYTES)
        idle_since = time.monotonic()
        message_started: Optional[float] = None
        while not self._stopping.is_set():
            now = time.monotonic()
            if message_started is not None and now - message_started > self._message_timeout:
                logger.debug("Control socket: dropping a client too slow to send a message")
                return
            if message_started is None and now - idle_since > self._idle_timeout:
                return
            try:
                data = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return  # closed, possibly mid-message: nothing to answer
            try:
                lines = reader.feed(data)
            except ProtocolError as e:
                self._send(conn, Response.failure(None, e.code, e.message))
                return  # can't find the next message boundary: hang up
            for line in lines:
                response, cmd = self._handle(line, peer)
                if cmd == Command.STATE_SUBSCRIBE and response.ok:
                    # The connection becomes a one-way stream; anything the
                    # client sent after the subscribe is ignored.
                    self._subscribe(conn, response, slot)
                    return
                if not self._send(conn, response):
                    return
            if reader.pending:
                if message_started is None or lines:
                    message_started = time.monotonic()
            else:
                message_started = None
                idle_since = time.monotonic()

    def _send(self, conn: socket.socket, response: Response) -> bool:
        try:
            data = encode_message(response.to_dict())
        except ProtocolError as e:
            # A status snapshot too big (or not JSON) to send is a display bug.
            logger.error("Control socket response not sent: %s", e.message)
            data = encode_message(Response.failure(
                response.id, ErrorCode.INTERNAL, 'response could not be encoded').to_dict())
        try:
            conn.sendall(data)
            return True
        except OSError:
            return False

    # -- requests --------------------------------------------------------------------

    def handle_line(self, line: bytes, peer: Optional[PeerCredentials] = None) -> Response:
        """Answer one request line. Never raises.

        A ``state.subscribe`` answered here gets its snapshot only; the
        stream that follows needs a connection (``_read_requests``).
        """
        return self._handle(line, peer)[0]

    def _handle(self, line: bytes,
                peer: Optional[PeerCredentials]) -> Tuple[Response, Optional[str]]:
        """The response to one line, and the command it answered (when known)."""
        request_id: Optional[str] = None
        cmd: Optional[str] = None
        try:
            obj = decode_message(line)
            raw_id = obj.get('id')
            request_id = raw_id if isinstance(raw_id, str) and len(raw_id) <= 128 else None
            request = Request.from_dict(obj)
            request_id = request.id
            cmd = request.cmd
            return self._dispatch(request, peer), cmd
        except ProtocolError as e:
            return Response.failure(e.request_id or request_id, e.code, e.message), cmd
        except Exception:  # pylint: disable=broad-except
            logger.exception("Control socket handler failed")
            return Response.failure(request_id, ErrorCode.INTERNAL, 'internal error'), cmd

    # -- the state stream ----------------------------------------------------------

    def _subscribe(self, conn: socket.socket, response: Response,
                   slot: Optional[_Slot]) -> None:
        """Answer a ``state.subscribe`` and push state events until it ends.

        Subscribers have their own bound (MAX_SUBSCRIBERS) and give their
        request slot back, so a few browsers watching never use up the slots
        commands need. Everything here runs on this connection's thread: a
        reader that does not keep up only stalls its own sends, and one that
        stops reading for a whole IO timeout is dropped. The render thread
        only ever publishes into the hub.
        """
        hub = self.state_hub
        if hub is None or not self._subscriber_slots.acquire(blocking=False):
            self._send(conn, Response.failure(response.id, ErrorCode.BUSY,
                                              'too many state subscribers', v=response.v))
            return
        if slot is not None:
            slot.release()
        hub.subscriber_joined()
        try:
            if not self._send(conn, response):
                return
            result = response.result or {}
            version = result.get('version', -1)
            sub_id = response.id or ''
            logger.debug("Control socket: state subscriber joined at version %s", version)
            while not self._stopping.is_set():
                hub.wait_for_change(version, self._keepalive, self._stopping)
                if self._stopping.is_set():
                    return
                snap = hub.snapshot(since=version, epoch=hub.epoch)
                if snap.get('changed'):
                    version = snap['version']
                    event = StateEvent(sub_id, StateEventKind.STATE, fit_snapshot(snap),
                                       v=response.v)
                else:
                    event = StateEvent(sub_id, StateEventKind.TICK, snap, v=response.v)
                if not self._send_event(conn, event):
                    return
        finally:
            hub.subscriber_left()
            self._subscriber_slots.release()

    def _send_event(self, conn: socket.socket, event: StateEvent) -> bool:
        try:
            data = encode_message(event.to_dict())
        except ProtocolError as e:
            logger.error("Control socket state event not sent: %s", e.message)
            return False
        try:
            conn.sendall(data)
            return True
        except socket.timeout:
            logger.info("Control socket: dropping a state subscriber that stopped reading")
            return False
        except OSError:
            return False

    def _dispatch(self, request: Request, peer: Optional[PeerCredentials]) -> Response:
        if request.cmd == Command.HELLO:
            # Exempt from the envelope version check: this is how a client
            # that speaks other versions finds out which ones we share.
            hello = HelloArgs.from_dict(request.args)
            version = negotiate_version(hello.versions)
            if version is None:
                return Response.failure(
                    request.id, ErrorCode.UNSUPPORTED_VERSION,
                    f'no common protocol version; this display speaks {list(SUPPORTED_VERSIONS)}')
            result: HelloResult = {
                'version': version,
                'versions': list(SUPPORTED_VERSIONS),
                'commands': list(COMMANDS),
                'max_message_bytes': MAX_MESSAGE_BYTES,
                'server': 'ledmatrix-display',
            }
            return Response.success(request.id, dict(result), v=version)

        if request.v not in SUPPORTED_VERSIONS:
            return Response.failure(
                request.id, ErrorCode.UNSUPPORTED_VERSION,
                f'protocol version {request.v} is not supported; '
                f'this display speaks {list(SUPPORTED_VERSIONS)}')

        try:
            args = parse_args(request.cmd, request.args)
        except ProtocolError as e:
            return Response.failure(request.id, e.code, e.message, v=request.v)

        if request.cmd == Command.PING:
            return Response.success(request.id, {'pong': True}, v=request.v)

        if request.cmd == Command.ON_DEMAND_STATUS:
            if self._status_provider is None:
                return Response.failure(request.id, ErrorCode.INTERNAL, 'no status available',
                                        v=request.v)
            return Response.success(request.id, self._status_provider(), v=request.v)

        if request.cmd in (Command.STATE_GET, Command.STATE_SUBSCRIBE):
            hub = self.state_hub
            if hub is None:
                return Response.failure(request.id, ErrorCode.INTERNAL, 'no state available',
                                        v=request.v)
            if isinstance(args, StateGetArgs):
                hub.note_read()
                snap = hub.snapshot(since=args.since, epoch=args.epoch)
            else:
                snap = hub.snapshot()
            return Response.success(request.id, fit_snapshot(snap), v=request.v)

        if request.cmd in QUEUED_COMMANDS and isinstance(args, (
                OnDemandStartArgs, OnDemandStopArgs, BrightnessSetArgs, PluginReloadArgs)):
            awaited = request.cmd in AWAITED_COMMANDS
            command = QueuedCommand(request_id=request.id, cmd=request.cmd, args=args,
                                    received_at=time.time(),
                                    peer_uid=peer.uid if peer is not None else None,
                                    outcome=CommandOutcome() if awaited else None)
            try:
                self._queue.put_nowait(command)
            except queue.Full:
                logger.warning("Control socket queue full; refusing %s %s",
                               request.cmd, request.id)
                return Response.failure(request.id, ErrorCode.BUSY,
                                        'the display is not taking commands right now',
                                        v=request.v)
            self._pending.set()
            logger.info("Control socket accepted %s %s", request.cmd, request.id)
            if command.outcome is not None:
                return self._await_outcome(request, command.outcome)
            ack: AckResult = {'accepted': True, 'request_id': request.id,
                              'queued': self._queue.qsize()}
            return Response.success(request.id, dict(ack), v=request.v)

        # A command in COMMANDS with no handler here is a bug in this module.
        return Response.failure(request.id, ErrorCode.INTERNAL,
                                f'{request.cmd} is not implemented', v=request.v)

    def _await_outcome(self, request: Request, outcome: CommandOutcome) -> Response:
        """Answer an awaited command once the render thread has applied it.

        Waits on this connection's thread, never the render thread's. If the
        render thread does not get to it in time, the answer is ``pending``:
        the command stays queued and is still applied, so a client treats
        that as "not known to be done" rather than as a refusal.
        """
        timeout = self._await_seconds.get(request.cmd, 0.0)
        if not outcome.wait(timeout):
            logger.warning("Control socket: %s %s not applied within %.1fs; answering pending",
                           request.cmd, request.id, timeout)
            return Response.failure(request.id, ErrorCode.PENDING,
                                    f'accepted, but not applied within {timeout:g}s; '
                                    'the display will still apply it', v=request.v)
        if outcome.error_code is not None:
            return Response.failure(request.id, outcome.error_code, outcome.error_message,
                                    v=request.v)
        return Response.success(request.id, outcome.result or {}, v=request.v)


def start_control_server(status_provider: Optional[StatusProvider] = None,
                         cache_dir: Optional[str] = None,
                         environ: Optional[Mapping[str, str]] = None,
                         state_hub: Optional[StateHub] = None) -> Optional[ControlServer]:
    """Start the display's control socket, or return None when it can't run.

    None covers Windows, ``LEDMATRIX_CONTROL_SOCKET=off`` and any failure to
    bind; in every case the web interface falls back to the file mailbox
    and to the cache keys the display still writes.
    """
    path = server_socket_path(environ)
    if path is None:
        logger.debug("Control socket disabled or unsupported here; using the file mailbox only")
        return None
    server = ControlServer(path, status_provider, resolve_socket_group(cache_dir),
                           state_hub=state_hub)
    return server if server.start() else None


__all__ = [
    'CommandOutcome', 'ControlServer', 'PeerCredentials', 'QueuedCommand', 'StateHub',
    'StatusProvider', 'fit_snapshot', 'peer_allowed', 'peer_credentials', 'process_groups',
    'resolve_socket_group', 'server_socket_path', 'start_control_server', 'PROTOCOL_VERSION',
]
