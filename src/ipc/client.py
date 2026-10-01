"""The web side of the control socket: one request, a short timeout, no retries.

Every failure -- no socket (the display is stopped, or predates the socket),
a refused or timed-out connection, a reply that breaks the contract, or an
error the display returned -- raises :class:`ControlError` with a short
``reason``, and the caller falls back to the file mailbox. Nothing here
blocks for longer than ``timeout`` in total.
"""

from __future__ import annotations

import socket
import time
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.ipc.contract import (
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    SUPPORTED_VERSIONS,
    Command,
    FrameReader,
    ProtocolError,
    Request,
    Response,
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


def ping(*, timeout: float = DEFAULT_TIMEOUT_SECONDS,
         paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    return request(Command.PING, {}, timeout=timeout, paths=paths)


def hello(client: str = 'web', *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
          paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Version negotiation: the result's ``version`` is the one both sides speak."""
    return request(Command.HELLO, {'versions': list(SUPPORTED_VERSIONS), 'client': client},
                   timeout=timeout, paths=paths)
