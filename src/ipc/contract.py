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
later the state stream).

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
#: display does not serve it and the web interface goes straight to the
#: file mailbox.
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


#: Every command version 1 defines, in the order ``hello`` reports them.
COMMANDS: Tuple[str, ...] = (
    Command.HELLO,
    Command.PING,
    Command.ON_DEMAND_START,
    Command.ON_DEMAND_STOP,
    Command.ON_DEMAND_STATUS,
)

#: Commands that are queued for the render thread and answered with an ack.
QUEUED_COMMANDS = frozenset({Command.ON_DEMAND_START, Command.ON_DEMAND_STOP})


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

    Numbers and numeric strings are accepted, the same as the REST route and
    the file mailbox take them; anything else is refused rather than guessed.
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

    def to_dict(self) -> Dict[str, Any]:
        return {'versions': list(self.versions), 'client': self.client}

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

    The same fields the file mailbox carries. At least one of ``plugin_id``
    and ``mode`` is required; the display resolves the other.
    """
    plugin_id: Optional[str] = None
    mode: Optional[str] = None
    duration: Optional[float] = None
    pinned: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {'plugin_id': self.plugin_id, 'mode': self.mode,
                'duration': self.duration, 'pinned': self.pinned}

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

    def to_dict(self) -> Dict[str, Any]:
        return {}

    @classmethod
    def from_dict(cls, args: Mapping[str, Any]) -> 'OnDemandStopArgs':
        return cls()


@dataclass(frozen=True)
class NoArgs:
    """``ping`` and ``on_demand.status`` take no arguments (extra ones are ignored)."""

    def to_dict(self) -> Dict[str, Any]:
        return {}

    @classmethod
    def from_dict(cls, args: Mapping[str, Any]) -> 'NoArgs':
        return cls()


CommandArgs = Union[HelloArgs, OnDemandStartArgs, OnDemandStopArgs, NoArgs]

_ARG_TYPES: Dict[str, Any] = {
    Command.HELLO: HelloArgs,
    Command.PING: NoArgs,
    Command.ON_DEMAND_START: OnDemandStartArgs,
    Command.ON_DEMAND_STOP: OnDemandStopArgs,
    Command.ON_DEMAND_STATUS: NoArgs,
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
    """The file-mailbox payload for a queued on-demand command.

    The display hands socket commands to the same code that handles the
    mailbox (``DisplayController._handle_on_demand_request``), so a command
    behaves identically whichever way it arrived, and a request that came
    both ways (a client that timed out and fell back) is processed once: the
    request id is the same.
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
