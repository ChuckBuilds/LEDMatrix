"""The control socket's contract: versioned messages, framing and location.

Both processes import this module -- the display serves the socket
(:mod:`src.ipc.server`) and the web interface calls it
(:mod:`src.ipc.client`) -- so it is the one definition of what goes over the
wire. Standard library only, and no import of the rest of ``src``.

Wire format (protocol version 1)
--------------------------------
One JSON object per line (newline-delimited JSON), UTF-8, at most
:data:`MAX_MESSAGE_BYTES` per line including the newline. Messages are
encoded with ``ensure_ascii``, so a newline never appears inside one.

Request::

    {"v": 1, "id": "<1-128 chars>", "cmd": "on_demand.start", "args": {...}}

Response, always carrying the request's ``id`` (``null`` when the request
could not be parsed far enough to have one)::

    {"v": 1, "id": "...", "ok": true,  "result": {...}}
    {"v": 1, "id": "...", "ok": false, "error": {"code": "...", "message": "..."}}

A connection may carry several requests; each gets exactly one response, in
order. Commands that change what the panel shows are *acknowledged*, not
completed: ``{"accepted": true, "request_id": ...}`` means the render thread
has the command queued and will apply it at its next on-demand check. Its
outcome is published the way it always was (``display_on_demand_state``,
later the state stream). A few commands (:data:`AWAITED_COMMANDS`) are
answered only once the render thread has applied them, or with ``pending``
when it has not within :data:`AWAIT_SECONDS`.

``state.subscribe`` is the one exception to "one response per request": its
response is followed, on the same connection, by :class:`StateEvent` lines
the display pushes until either side hangs up. Events carry ``event``
instead of ``ok``.

New commands are added within a protocol version: a display that does not
know one answers ``unknown_command``, the client falls back, and ``hello``
lists the commands a display knows. The version changes only when the
envelope or the meaning of an existing command changes.

See docs/IPC_CONTROL_SOCKET.md for the full description.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple, TypedDict, TypeGuard, Union


# -- versions and limits -------------------------------------------------------

#: The protocol version this code speaks by default.
PROTOCOL_VERSION = 1

#: Every version this code can speak; ``hello`` picks the highest common one.
SUPPORTED_VERSIONS: Tuple[int, ...] = (1,)

#: The largest message either side sends or accepts, newline included. A
#: stage-1 message is well under 1 KiB; this only bounds a broken or hostile
#: peer, so a reader never buffers more than this per connection.
MAX_MESSAGE_BYTES = 64 * 1024

#: Longest request id. Ids are also the on-demand ``request_id``, which the
#: display logs and stores, so they are kept short.
MAX_ID_LENGTH = 128

#: Longest plugin id or mode name an on-demand command may carry.
MAX_NAME_LENGTH = 128


# -- where the socket lives ------------------------------------------------------

#: ``RuntimeDirectory=ledmatrix`` in ledmatrix.service creates this (tmpfs,
#: root-owned, 0755); a display under an older unit creates it itself, as it
#: does for the heartbeat (src/display_watchdog.py).
DEFAULT_SOCKET_DIR = '/run/ledmatrix'
SOCKET_NAME = 'control.sock'
DEFAULT_SOCKET_PATH = DEFAULT_SOCKET_DIR + '/' + SOCKET_NAME

#: Overrides the socket path for both processes (a dev checkout, a second
#: instance, tests). One of :data:`DISABLED_VALUES` turns the socket off: the
#: display does not serve it, and the web interface cannot send it commands
#: (it still reads the state the display writes to the cache).
SOCKET_PATH_ENV = 'LEDMATRIX_CONTROL_SOCKET'
DISABLED_VALUES = frozenset({'off', '0', 'false', 'no', 'none', 'disabled'})


def socket_supported() -> bool:
    """Whether this platform has Unix sockets at all (Windows Python does not)."""
    import socket
    return os.name == 'posix' and hasattr(socket, 'AF_UNIX')


def socket_disabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when :data:`SOCKET_PATH_ENV` switches the socket off."""
    env = os.environ if environ is None else environ
    value = (env.get(SOCKET_PATH_ENV) or '').strip()
    return value.lower() in DISABLED_VALUES


def configured_socket_path(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The path :data:`SOCKET_PATH_ENV` names, or None when it is unset or 'off'."""
    env = os.environ if environ is None else environ
    value = (env.get(SOCKET_PATH_ENV) or '').strip()
    if not value or value.lower() in DISABLED_VALUES:
        return None
    return value


def dev_socket_path(uid: Optional[int] = None) -> str:
    """Where a display that cannot use /run/ledmatrix serves the socket.

    A per-user directory under the temp dir, so a dev checkout run as an
    ordinary user (``python3 run.py -e``) and its web interface, run by the
    same user, find each other with no configuration.
    """
    if uid is None:
        getuid = getattr(os, 'getuid', None)
        uid = getuid() if getuid is not None else 0
    return os.path.join(tempfile.gettempdir(), f'ledmatrix-{uid}', SOCKET_NAME)


def client_socket_paths(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    """The paths a client tries, in order; empty when the socket is off."""
    if socket_disabled(environ):
        return []
    configured = configured_socket_path(environ)
    if configured:
        return [configured]
    return [DEFAULT_SOCKET_PATH, dev_socket_path()]


# -- commands and error codes ----------------------------------------------------

class Command:
    """Command names. Dotted names group a feature's commands."""
    HELLO = 'hello'
    PING = 'ping'
    ON_DEMAND_START = 'on_demand.start'
    ON_DEMAND_STOP = 'on_demand.stop'
    ON_DEMAND_STATUS = 'on_demand.status'
    BRIGHTNESS_SET = 'brightness.set'
    PLUGIN_RELOAD = 'plugin.reload'
    STATE_GET = 'state.get'
    STATE_SUBSCRIBE = 'state.subscribe'
    ERRORS_CLEAR = 'errors.clear'


#: Every command version 1 defines, in the order ``hello`` reports them.
#: ``brightness.set`` and ``plugin.reload`` came in stage 2, ``state.get``
#: and ``state.subscribe`` in stage 3, and ``errors.clear`` in stage 4, all
#: within version 1 (see the module docstring on adding commands).
COMMANDS: Tuple[str, ...] = (
    Command.HELLO,
    Command.PING,
    Command.ON_DEMAND_START,
    Command.ON_DEMAND_STOP,
    Command.ON_DEMAND_STATUS,
    Command.BRIGHTNESS_SET,
    Command.PLUGIN_RELOAD,
    Command.STATE_GET,
    Command.STATE_SUBSCRIBE,
    Command.ERRORS_CLEAR,
)

#: Commands the connection thread answers itself, through a handler the
#: display registers (``ControlServer(handlers=...)``), because they touch
#: nothing the render thread owns. A display that registered none answers
#: ``unknown_command``, and the client falls back as from an older display.
DIRECT_COMMANDS = frozenset({Command.ERRORS_CLEAR})

#: Commands that are queued for the render thread.
QUEUED_COMMANDS = frozenset({Command.ON_DEMAND_START, Command.ON_DEMAND_STOP,
                             Command.BRIGHTNESS_SET, Command.PLUGIN_RELOAD})

#: Queued commands whose answer waits for the render thread's outcome
#: instead of being an ack. The value is how long the display waits before
#: answering ``pending``; the command stays queued and is still applied.
#: A plugin reload first lets the current screen end (within a frame on a
#: scrolling screen, at once on a static one) and then imports the plugin,
#: which can take a few seconds on a slow board.
AWAIT_SECONDS: Dict[str, float] = {
    Command.BRIGHTNESS_SET: 2.0,
    Command.PLUGIN_RELOAD: 10.0,
}
AWAITED_COMMANDS = frozenset(AWAIT_SECONDS)

#: The state stream (stage 3). ``state.subscribe`` turns its connection into
#: a one-way stream of :class:`StateEvent` lines. Subscribers have their own
#: bound, separate from the short request connections, so they can never
#: take the slots a command needs.
MAX_SUBSCRIBERS = 4

#: A subscriber hears from the display at least this often: a ``state``
#: event when something changed, else a ``tick`` carrying the render loop's
#: liveness and the latest volatile timestamps. A client that has heard nothing for a few of these treats its
#: copy as unknown.
SUBSCRIBE_KEEPALIVE_SECONDS = 5.0

#: The shape of the ``state`` object in a state snapshot. Bumped only when a
#: field changes meaning; new fields are added within a schema.
STATE_SCHEMA = 1

#: The sections of a state snapshot, in the order they are documented.
STATE_SECTIONS: Tuple[str, ...] = ('display', 'on_demand', 'brightness', 'plugins', 'loop')

#: Brightness, in percent, as the display's hardware setting takes it.
MIN_BRIGHTNESS = 0
MAX_BRIGHTNESS = 100


class ErrorCode:
    """``error.code`` values. Clients branch on these, never on the message."""
    BAD_JSON = 'bad_json'                        # a line that is not a JSON object
    BAD_REQUEST = 'bad_request'                  # the envelope is malformed
    MESSAGE_TOO_LARGE = 'message_too_large'      # over MAX_MESSAGE_BYTES
    UNSUPPORTED_VERSION = 'unsupported_version'  # no version in common
    UNKNOWN_COMMAND = 'unknown_command'
    INVALID_ARGS = 'invalid_args'
    BUSY = 'busy'                                # queue full / too many clients
    FORBIDDEN = 'forbidden'                      # peer credentials refused
    INTERNAL = 'internal'                        # a bug on the display side
    # From the awaited commands (stage 2):
    PENDING = 'pending'          # accepted, not applied within AWAIT_SECONDS; still queued
    NOT_LOADED = 'not_loaded'    # plugin.reload: the display is not running that plugin
    FAILED = 'failed'            # the render thread tried, and it did not work


class ProtocolError(Exception):
    """A message that breaks the contract. ``code`` is an :class:`ErrorCode`."""

    def __init__(self, code: str, message: str, request_id: Optional[str] = None):
        super().__init__(code, message, request_id)
        self.code = code
        self.message = message
        self.request_id = request_id

    def __str__(self) -> str:
        return f'{self.code}: {self.message}'


# -- the envelope ------------------------------------------------------------------

def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_id(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= MAX_ID_LENGTH
            and value.isprintable())


@dataclass(frozen=True)
class Request:
    """``{v, id, cmd, args}``."""
    id: str
    cmd: str
    args: Dict[str, Any] = field(default_factory=dict)
    v: int = PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {'v': self.v, 'id': self.id, 'cmd': self.cmd, 'args': dict(self.args)}

    @classmethod
    def from_dict(cls, obj: Any) -> 'Request':
        """Validate an envelope. Raises :class:`ProtocolError`.

        The version is checked by the server, not here, so that ``hello``
        can negotiate across versions.
        """
        if not isinstance(obj, dict):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'a request must be a JSON object')
        raw_id = obj.get('id')
        request_id = raw_id if _valid_id(raw_id) else None
        if request_id is None:
            raise ProtocolError(ErrorCode.BAD_REQUEST,
                                f'id must be a printable string of 1-{MAX_ID_LENGTH} characters')
        version = obj.get('v')
        if not _is_int(version):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'v must be an integer', request_id)
        cmd = obj.get('cmd')
        if not isinstance(cmd, str) or not cmd:
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'cmd must be a non-empty string', request_id)
        args = obj.get('args', {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'args must be a JSON object', request_id)
        return cls(id=request_id, cmd=cmd, args=args, v=version)


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {'code': self.code, 'message': self.message}


@dataclass(frozen=True)
class Response:
    """``{v, id, ok, result}`` or ``{v, id, ok: false, error: {code, message}}``."""
    id: Optional[str]
    ok: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[ErrorInfo] = None
    v: int = PROTOCOL_VERSION

    @classmethod
    def success(cls, request_id: Optional[str], result: Mapping[str, Any],
                v: int = PROTOCOL_VERSION) -> 'Response':
        return cls(id=request_id, ok=True, result=dict(result), v=v)

    @classmethod
    def failure(cls, request_id: Optional[str], code: str, message: str,
                v: int = PROTOCOL_VERSION) -> 'Response':
        return cls(id=request_id, ok=False, error=ErrorInfo(code, message), v=v)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {'v': self.v, 'id': self.id, 'ok': self.ok}
        if self.ok:
            out['result'] = dict(self.result or {})
        else:
            error = self.error or ErrorInfo(ErrorCode.INTERNAL, 'unknown error')
            out['error'] = error.to_dict()
        return out

    @classmethod
    def from_dict(cls, obj: Any) -> 'Response':
        """Validate a response. Raises :class:`ProtocolError` (BAD_REQUEST)."""
        if not isinstance(obj, dict):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'a response must be a JSON object')
        version = obj.get('v')
        if not _is_int(version):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'v must be an integer')
        raw_id = obj.get('id')
        if raw_id is not None and not isinstance(raw_id, str):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'id must be a string or null')
        ok = obj.get('ok')
        if not isinstance(ok, bool):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'ok must be a boolean')
        if ok:
            result = obj.get('result', {})
            if not isinstance(result, dict):
                raise ProtocolError(ErrorCode.BAD_REQUEST, 'result must be a JSON object')
            return cls(id=raw_id, ok=True, result=result, v=version)
        error = obj.get('error')
        if (not isinstance(error, dict) or not isinstance(error.get('code'), str)
                or not isinstance(error.get('message', ''), str)):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'error must be {code, message}')
        return cls(id=raw_id, ok=False,
                   error=ErrorInfo(error['code'], error.get('message', '')), v=version)


# -- command arguments -------------------------------------------------------------

def _optional_name(args: Mapping[str, Any], key: str) -> Optional[str]:
    value = args.get(key)
    if value is None or value == '':
        return None
    if not isinstance(value, str) or len(value) > MAX_NAME_LENGTH or not value.isprintable():
        raise ProtocolError(ErrorCode.INVALID_ARGS,
                            f'{key} must be a printable string of at most '
                            f'{MAX_NAME_LENGTH} characters')
    return value


def _optional_duration(value: Any) -> Optional[float]:
    """Seconds, or None for "until stopped". 0 means the same as None.

    Numbers and numeric strings are accepted, the same as the REST route
    takes them; anything else is refused rather than guessed.
    """
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ProtocolError(ErrorCode.INVALID_ARGS, 'duration must be a number of seconds')
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ProtocolError(ErrorCode.INVALID_ARGS,
                            'duration must be a number of seconds') from None
    if not math.isfinite(seconds) or seconds < 0:
        raise ProtocolError(ErrorCode.INVALID_ARGS,
                            'duration must be a finite, non-negative number of seconds')
    return seconds or None


@dataclass(frozen=True)
class HelloArgs:
    """``hello``: the versions the client speaks, and a name for the logs."""
    versions: Tuple[int, ...] = (PROTOCOL_VERSION,)
    client: str = ''

    @classmethod
    def from_dict(cls, args: Mapping[str, Any]) -> 'HelloArgs':
        versions = args.get('versions', [PROTOCOL_VERSION])
        if (not isinstance(versions, list) or not versions or len(versions) > 32
                or not all(_is_int(v) for v in versions)):
            raise ProtocolError(ErrorCode.INVALID_ARGS, 'versions must be a list of integers')
        client = args.get('client', '')
        if not isinstance(client, str) or len(client) > MAX_NAME_LENGTH:
            raise ProtocolError(ErrorCode.INVALID_ARGS, 'client must be a short string')
        return cls(versions=tuple(versions), client=client)


@dataclass(frozen=True)
class OnDemandStartArgs:
    """``on_demand.start``: show a plugin (or one of its modes) now.

    The same fields as the REST route's body. At least one of ``plugin_id``
    and ``mode`` is required; the display resolves the other.
    """
    plugin_id: Optional[str] = None
    mode: Optional[str] = None
    duration: Optional[float] = None
    pinned: bool = False

    @classmethod
    def from_dict(cls, args: Mapping[str, Any]) -> 'OnDemandStartArgs':
        plugin_id = _optional_name(args, 'plugin_id')
        mode = _optional_name(args, 'mode')
        if plugin_id is None and mode is None:
            raise ProtocolError(ErrorCode.INVALID_ARGS, 'plugin_id or mode is required')
        pinned = args.get('pinned', False)
        if pinned is None:
            pinned = False
        if not isinstance(pinned, bool):
            raise ProtocolError(ErrorCode.INVALID_ARGS, 'pinned must be a boolean')
        return cls(plugin_id=plugin_id, mode=mode,
                   duration=_optional_duration(args.get('duration')), pinned=pinned)


@dataclass(frozen=True)
class OnDemandStopArgs:
    """``on_demand.stop``: end the on-demand session and resume rotation."""

    @classmethod
    def from_dict(cls, args: Mapping[str, Any]) -> 'OnDemandStopArgs':
        return cls()


@dataclass(frozen=True)
class NoArgs:
    """``ping`` and ``on_demand.status`` take no arguments (extra ones are ignored)."""

    @classmethod
    def from_dict(cls, args: Mapping[str, Any]) -> 'NoArgs':
        return cls()


@dataclass(frozen=True)
class BrightnessSetArgs:
    """``brightness.set``: the panel's normal brightness, in percent, now.

    Transient: nothing is written to config.json, and the next config change
    the display picks up (or a restart) goes back to the configured value.
    The web interface sends it after saving the setting, so the two agree.
    The dim schedule still applies on top, as it does to the saved value.
    """
    brightness: int

    @classmethod
    def from_dict(cls, args: Mapping[str, Any]) -> 'BrightnessSetArgs':
        value = args.get('brightness')
        if not _is_int(value) or not MIN_BRIGHTNESS <= value <= MAX_BRIGHTNESS:
            raise ProtocolError(ErrorCode.INVALID_ARGS,
                                f'brightness must be an integer from {MIN_BRIGHTNESS} '
                                f'to {MAX_BRIGHTNESS}')
        return cls(brightness=value)


@dataclass(frozen=True)
class PluginReloadArgs:
    """``plugin.reload``: load a running plugin again from disk.

    For a plugin the store has just updated. Only a plugin the display is
    running can be reloaded (``not_loaded`` otherwise), so the id never
    makes the display import anything it was not already running.
    """
    plugin_id: str

    @classmethod
    def from_dict(cls, args: Mapping[str, Any]) -> 'PluginReloadArgs':
        plugin_id = _optional_name(args, 'plugin_id')
        if plugin_id is None:
            raise ProtocolError(ErrorCode.INVALID_ARGS, 'plugin_id is required')
        return cls(plugin_id=plugin_id)


def _optional_version(args: Mapping[str, Any], key: str) -> Optional[int]:
    value = args.get(key)
    if value is None:
        return None
    if not _is_int(value) or value < 0:
        raise ProtocolError(ErrorCode.INVALID_ARGS, f'{key} must be a non-negative integer')
    return value


def _optional_epoch(args: Mapping[str, Any]) -> Optional[str]:
    value = args.get('epoch')
    if value is None or value == '':
        return None
    if not _valid_id(value):
        raise ProtocolError(ErrorCode.INVALID_ARGS,
                            f'epoch must be a printable string of 1-{MAX_ID_LENGTH} characters')
    return str(value)


@dataclass(frozen=True)
class StateGetArgs:
    """``state.get``: the display's state, as a versioned snapshot.

    With ``since`` and the ``epoch`` it came from, the answer is only
    ``{changed: false, version, epoch, served_at, loop, volatile}`` while the
    state is still at that version, so a poller that already has it is sent
    no state -- only the latest values of the keys that do not count as a
    change (``volatile``, see :class:`StateSnapshot`).
    """
    since: Optional[int] = None
    epoch: Optional[str] = None

    @classmethod
    def from_dict(cls, args: Mapping[str, Any]) -> 'StateGetArgs':
        return cls(since=_optional_version(args, 'since'), epoch=_optional_epoch(args))


@dataclass(frozen=True)
class StateSubscribeArgs:
    """``state.subscribe``: the snapshot now, then a push stream of changes.

    The response is the snapshot ``state.get`` returns. After it the
    connection carries only :class:`StateEvent` lines from the display: a
    ``state`` event whenever the state changes (always the latest version,
    so a reader that falls behind skips versions instead of queueing them),
    and a ``tick`` at least every :data:`SUBSCRIBE_KEEPALIVE_SECONDS`.
    """

    @classmethod
    def from_dict(cls, args: Mapping[str, Any]) -> 'StateSubscribeArgs':
        return cls()


@dataclass(frozen=True)
class ErrorsClearArgs:
    """``errors.clear``: forget the plugin errors recorded at or before
    ``cutoff`` (seconds since the epoch), as ``POST /api/v3/errors/clear``
    asks. The request id is the clear's id, which the display's error
    snapshot then reports as ``applied_clear_id``.
    """
    cutoff: float

    @classmethod
    def from_dict(cls, args: Mapping[str, Any]) -> 'ErrorsClearArgs':
        value = args.get('cutoff')
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProtocolError(ErrorCode.INVALID_ARGS, 'cutoff must be a number of seconds')
        if not math.isfinite(value) or value < 0:
            raise ProtocolError(ErrorCode.INVALID_ARGS,
                                'cutoff must be a finite, non-negative number of seconds')
        return cls(cutoff=float(value))


CommandArgs = Union[HelloArgs, OnDemandStartArgs, OnDemandStopArgs, NoArgs,
                    BrightnessSetArgs, PluginReloadArgs, StateGetArgs, StateSubscribeArgs,
                    ErrorsClearArgs]

#: The arguments of a command that goes on the render thread's queue.
QueuedArgs = Union[OnDemandStartArgs, OnDemandStopArgs, BrightnessSetArgs, PluginReloadArgs]

_ARG_TYPES: Dict[str, Any] = {
    Command.HELLO: HelloArgs,
    Command.PING: NoArgs,
    Command.ON_DEMAND_START: OnDemandStartArgs,
    Command.ON_DEMAND_STOP: OnDemandStopArgs,
    Command.ON_DEMAND_STATUS: NoArgs,
    Command.BRIGHTNESS_SET: BrightnessSetArgs,
    Command.PLUGIN_RELOAD: PluginReloadArgs,
    Command.STATE_GET: StateGetArgs,
    Command.STATE_SUBSCRIBE: StateSubscribeArgs,
    Command.ERRORS_CLEAR: ErrorsClearArgs,
}


def parse_args(cmd: str, args: Mapping[str, Any]) -> CommandArgs:
    """Typed arguments for ``cmd``. Raises :class:`ProtocolError`."""
    arg_type = _ARG_TYPES.get(cmd)
    if arg_type is None:
        raise ProtocolError(ErrorCode.UNKNOWN_COMMAND, f'unknown command: {cmd[:64]}')
    parsed: CommandArgs = arg_type.from_dict(args)
    return parsed


def on_demand_request(request_id: str, args: Union[OnDemandStartArgs, OnDemandStopArgs],
                      timestamp: float) -> Dict[str, Any]:
    """The on-demand request dict for a queued on-demand command.

    The display hands socket commands to the same code that handles
    plugins' own requests (``DisplayController._handle_on_demand_request``),
    so a command behaves identically whichever way it arrived. (This was
    the file mailbox's payload, which the display no longer reads.)
    """
    if isinstance(args, OnDemandStartArgs):
        return {'request_id': request_id, 'action': 'start', 'plugin_id': args.plugin_id,
                'mode': args.mode, 'duration': args.duration, 'pinned': args.pinned,
                'timestamp': timestamp, 'source': 'socket'}
    return {'request_id': request_id, 'action': 'stop', 'timestamp': timestamp,
            'source': 'socket'}


# -- results -----------------------------------------------------------------------

class HelloResult(TypedDict):
    version: int
    versions: List[int]
    commands: List[str]
    max_message_bytes: int
    server: str


class PingResult(TypedDict):
    pong: bool


class AckResult(TypedDict):
    """The answer to a queued command: the render thread will apply it."""
    accepted: bool
    request_id: str
    queued: int


class BrightnessResult(TypedDict):
    """``brightness.set``, once applied.

    ``panel_brightness`` is what the panel shows now: the dim schedule's
    level while it dims, and unchanged while the schedule has the display
    off (the new level applies when it comes back on).
    """
    brightness: int
    panel_brightness: int
    dimmed: bool
    display_active: bool


class PluginReloadResult(TypedDict):
    """``plugin.reload``, once the plugin is running again."""
    plugin_id: str
    reloaded: bool
    version: Optional[str]
    modes: List[str]


class ErrorsClearResult(TypedDict):
    """``errors.clear``, once applied and the error snapshot republished."""
    request_id: str
    cutoff: float
    cleared: int


class LoopState(TypedDict):
    """``loop``: is the render loop still going round?

    ``heartbeat_age_seconds`` is the age of the render thread's last beat,
    measured in memory by the display when it answered -- the same beat that
    writes ``display-heartbeat.json``. None until the loop has drawn its first
    frame. At ``stale_after`` or more the loop is stalled: the threshold
    ``/api/v3/health`` uses.
    """
    heartbeat_age_seconds: Optional[float]
    armed: bool
    stale_after: float


class StateSnapshot(TypedDict, total=False):
    """The answer to ``state.get`` and ``state.subscribe``, and the
    ``result`` of a ``state`` event.

    ``version`` counts changes to the state within one ``epoch`` (one run of
    the display process): a reader that sees a new epoch starts over.
    ``changed`` is False only for a ``state.get`` whose ``since`` is still
    current, and then ``state`` is absent and ``volatile`` is there instead:
    ``{section: {key: value}}``, the current values of the keys the version
    ignores (``display.last_updated``, ``on_demand.last_updated`` and
    ``remaining``, ``plugins.published_at``). A reader merges them into the
    copy it has; they are how it can tell the writers are still publishing.
    ``served_at`` is the display's wall clock when it answered. ``loop`` is
    measured at that moment, so it is also inside ``state``.

    ``state`` holds the sections in :data:`STATE_SECTIONS`:

    * ``display``: what ``display_current_state`` holds (mode, plugin_id,
      mode_index, total_modes, on_demand_active, is_display_active,
      last_updated);
    * ``on_demand``: what ``display_on_demand_state`` holds;
    * ``brightness``: ``{brightness, panel_brightness, dimmed}``;
    * ``plugins``: the plugin runtime snapshot (``plugin_runtime_snapshot``),
      or None when there is none (or it was too large to send);
    * ``loop``: :class:`LoopState`.

    A section the display has not published yet is None.
    """
    schema: int
    version: int
    epoch: str
    pid: int
    served_at: float
    changed: bool
    state: Dict[str, Any]
    volatile: Dict[str, Dict[str, Any]]
    loop: LoopState


class StateEventKind:
    STATE = 'state'   # result: a full StateSnapshot, the latest version
    TICK = 'tick'     # result: {version, epoch, pid, served_at, loop, volatile}; nothing changed


@dataclass(frozen=True)
class StateEvent:
    """One message the display pushes to a subscriber.

    ``{"v": 1, "id": "<the subscribe request's id>", "event": "state" | "tick",
    "result": {...}}``. It has no ``ok``, which is how a reader tells it from
    a response.
    """
    id: str
    event: str
    result: Dict[str, Any]
    v: int = PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {'v': self.v, 'id': self.id, 'event': self.event, 'result': dict(self.result)}

    @classmethod
    def from_dict(cls, obj: Any) -> 'StateEvent':
        """Validate an event. Raises :class:`ProtocolError` (BAD_REQUEST)."""
        if not isinstance(obj, dict):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'an event must be a JSON object')
        version = obj.get('v')
        if not _is_int(version):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'v must be an integer')
        raw_id = obj.get('id')
        if not isinstance(raw_id, str):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'id must be a string')
        event = obj.get('event')
        if event not in (StateEventKind.STATE, StateEventKind.TICK):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'event must be "state" or "tick"')
        result = obj.get('result')
        if not isinstance(result, dict):
            raise ProtocolError(ErrorCode.BAD_REQUEST, 'result must be a JSON object')
        return cls(id=raw_id, event=event, result=result, v=version)


def is_event(obj: Any) -> bool:
    """Whether a decoded message is a pushed event rather than a response."""
    return isinstance(obj, dict) and 'event' in obj and 'ok' not in obj


def negotiate_version(client_versions: Tuple[int, ...]) -> Optional[int]:
    """The highest version both sides speak, or None."""
    common = set(client_versions) & set(SUPPORTED_VERSIONS)
    return max(common) if common else None


# -- framing -----------------------------------------------------------------------

def encode_message(obj: Mapping[str, Any]) -> bytes:
    """One newline-terminated JSON line. Raises :class:`ProtocolError` when too big."""
    try:
        text = json.dumps(obj, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ProtocolError(ErrorCode.BAD_REQUEST, f'message is not JSON-serialisable: {e}') from None
    data = text.encode('ascii') + b'\n'
    if len(data) > MAX_MESSAGE_BYTES:
        raise ProtocolError(ErrorCode.MESSAGE_TOO_LARGE,
                            f'message is {len(data)} bytes; the limit is {MAX_MESSAGE_BYTES}')
    return data


def decode_message(line: bytes) -> Dict[str, Any]:
    """Parse one line (newline optional). Raises :class:`ProtocolError` (BAD_JSON)."""
    try:
        obj = json.loads(line.decode('utf-8'))
    except ValueError:  # UnicodeDecodeError and JSONDecodeError are both ValueErrors
        raise ProtocolError(ErrorCode.BAD_JSON, 'not valid UTF-8 JSON') from None
    if not isinstance(obj, dict):
        raise ProtocolError(ErrorCode.BAD_JSON, 'a message must be a JSON object')
    return obj


class FrameReader:
    """Splits a byte stream into lines, never holding more than one message.

    ``feed()`` returns the complete lines (without their newlines) the new
    bytes finished, and raises :class:`ProtocolError` (MESSAGE_TOO_LARGE) as
    soon as a line is longer than the limit, newline or not, so a peer that
    never sends one cannot make the reader buffer without bound.
    """

    def __init__(self, max_bytes: int = MAX_MESSAGE_BYTES):
        self._max = max_bytes
        self._buffer = bytearray()

    @property
    def pending(self) -> int:
        """Bytes of an unfinished message held."""
        return len(self._buffer)

    def feed(self, data: bytes) -> List[bytes]:
        self._buffer.extend(data)
        lines: List[bytes] = []
        while True:
            newline = self._buffer.find(b'\n')
            if newline < 0:
                break
            if newline + 1 > self._max:
                raise ProtocolError(ErrorCode.MESSAGE_TOO_LARGE,
                                    f'message exceeds {self._max} bytes')
            line = bytes(self._buffer[:newline])
            del self._buffer[:newline + 1]
            if line.strip():
                lines.append(line)
        if len(self._buffer) >= self._max:
            raise ProtocolError(ErrorCode.MESSAGE_TOO_LARGE,
                                f'message exceeds {self._max} bytes')
        return lines
