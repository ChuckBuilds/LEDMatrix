"""Deliver an on-demand start to a display that is not listening yet.

``POST /api/v3/display/on-demand/start`` can find no display on the control
socket: the service is stopped (the route starts it) or still loading its
plugins. The socket comes up only when the display's run loop starts, which
can take longer than a client waits -- the MQTT bridge gives up after 15 s.
So the route answers at once (``202``, ``status: "starting"``) and hands
the request to the one :class:`OnDemandDispatcher` of the web process, whose
worker thread sends it again until the display acknowledges it or
:data:`START_WAIT_SECONDS` pass.

* One request at a time: a newer start replaces the pending one, and a
  stop cancels it (:meth:`OnDemandDispatcher.cancel`).
* Its outcome is :meth:`OnDemandDispatcher.status`, which
  ``GET /display/on-demand/status`` and ``/display/current-status`` report:
  ``starting`` while it waits, ``delivered`` once acknowledged (the
  display's own state takes over from there), or ``error`` with
  ``start-timeout`` or the socket's reason.

Nothing is written to disk: the file mailbox that once carried such a
request is gone (docs/IPC_CONTROL_SOCKET.md, stage 5).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from src.ipc import client as control_client
from src.logging_config import get_logger

logger = get_logger(__name__)

#: How long the worker keeps sending a start before it gives up
#: (``start-timeout``). The socket comes up when the display's run loop
#: starts, after every plugin has loaded.
START_WAIT_SECONDS = 45.0

#: Gap between two sends while nothing is listening.
RETRY_INTERVAL = 0.5

#: How long a finished outcome (delivered, error, cancelled) is still
#: reported, so a client polling every few seconds sees it.
OUTCOME_SECONDS = 120.0

#: ``send(payload)`` hands the request to the display (the route's
#: ``_send_on_demand``) and raises ``ControlError`` when it does not take it.
Sender = Callable[[Dict[str, Any]], Any]


class OnDemandDispatcher:
    """One pending on-demand start, and the worker thread that delivers it."""

    def __init__(self, send: Sender, *,
                 wait_seconds: Optional[float] = None,
                 retry_interval: Optional[float] = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time):
        self._send = send
        self.wait_seconds = START_WAIT_SECONDS if wait_seconds is None else wait_seconds
        self.retry_interval = RETRY_INTERVAL if retry_interval is None else retry_interval
        self._clock = clock
        self._wall = wall_clock
        self._lock = threading.Lock()
        self._wake = threading.Event()
        # Bumped by every submit and cancel: a send that started under an
        # older generation does not report its result as the current one.
        self._generation = 0
        self._pending: Optional[Dict[str, Any]] = None
        self._deadline = 0.0
        self._status: Optional[Dict[str, Any]] = None
        self._finished_at: Optional[float] = None
        self._thread: Optional[threading.Thread] = None

    # -- the routes' side ------------------------------------------------------

    def submit(self, payload: Dict[str, Any], wait_seconds: Optional[float] = None) -> None:
        """Deliver ``payload`` (an on-demand start) in the background,
        replacing any start still pending."""
        with self._lock:
            self._generation += 1
            superseded = self._pending
            self._pending = dict(payload)
            self._deadline = self._clock() + (self.wait_seconds if wait_seconds is None
                                              else wait_seconds)
            self._status = self._describe('starting', payload)
            self._finished_at = None
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name='on-demand-dispatch',
                                                daemon=True)
                self._thread.start()
        if superseded is not None:
            logger.info("On-demand start %s superseded by %s before the display took it",
                        superseded.get('request_id'), payload.get('request_id'))
        self._wake.set()

    def cancel(self, reason: str = 'cancelled') -> Optional[str]:
        """Drop the pending start (a stop arrived). Returns its request id,
        or None when nothing was pending."""
        with self._lock:
            pending = self._pending
            if pending is None:
                return None
            self._generation += 1
            self._pending = None
            self._finish(self._describe('idle', pending, last_event=reason))
        self._wake.set()
        logger.info("On-demand start %s cancelled before the display took it (%s)",
                    pending.get('request_id'), reason)
        return pending.get('request_id')

    def status(self) -> Optional[Dict[str, Any]]:
        """The pending start's state, or its outcome for OUTCOME_SECONDS
        after it finished; None otherwise. In the shape of the display's
        on-demand state (``active``, ``status``, ``error``, ...), plus
        ``source: "web"``."""
        with self._lock:
            if self._status is None:
                return None
            if (self._finished_at is not None
                    and self._clock() - self._finished_at > OUTCOME_SECONDS):
                return None
            return dict(self._status)

    def pending(self) -> bool:
        with self._lock:
            return self._pending is not None

    # -- the worker ------------------------------------------------------------

    def _describe(self, status: str, payload: Dict[str, Any], error: Optional[str] = None,
                  last_event: Optional[str] = None) -> Dict[str, Any]:
        return {
            'active': False,
            'status': status,
            'error': error,
            'last_event': last_event,
            'request_id': payload.get('request_id'),
            'plugin_id': payload.get('plugin_id'),
            'mode': payload.get('mode'),
            'duration': payload.get('duration'),
            'pinned': bool(payload.get('pinned', False)),
            'last_updated': self._wall(),
            'source': 'web',
        }

    def _finish(self, status: Dict[str, Any]) -> None:
        """Record an outcome. Caller holds _lock."""
        self._status = status
        self._finished_at = self._clock()

    def _run(self) -> None:
        while True:
            with self._lock:
                payload, generation = self._pending, self._generation
                deadline = self._deadline
                if payload is None:
                    self._thread = None
                    return
            outcome, error = self._attempt(payload)
            with self._lock:
                if generation != self._generation:
                    continue          # superseded or cancelled meanwhile
                if outcome == 'retry' and self._clock() + self.retry_interval > deadline:
                    outcome, error = 'error', 'start-timeout'
                if outcome == 'delivered':
                    self._pending = None
                    self._finish(self._describe('delivered', payload,
                                                last_event='delivered'))
                elif outcome == 'error':
                    self._pending = None
                    self._finish(self._describe('error', payload, error=error))
                else:
                    self._wake.clear()
            if outcome == 'delivered':
                logger.info("On-demand start %s delivered once the display was listening",
                            payload.get('request_id'))
            elif outcome == 'error':
                logger.warning("On-demand start %s not delivered: %s",
                               payload.get('request_id'), error)
            else:
                # A submit or cancel wakes the wait at once.
                self._wake.wait(self.retry_interval)

    def _attempt(self, payload: Dict[str, Any]):
        try:
            self._send(payload)
        except control_client.ControlError as e:
            if control_client.display_not_listening(e):
                return 'retry', None
            return 'error', str(e.reason)
        except Exception:  # pylint: disable=broad-except
            logger.exception("On-demand start %s: the control socket client failed",
                             payload.get('request_id'))
            return 'error', 'internal'
        return 'delivered', None


_dispatcher: Optional[OnDemandDispatcher] = None
_dispatcher_lock = threading.Lock()


def get_dispatcher(send: Sender) -> OnDemandDispatcher:
    """The web process's dispatcher, created on first use."""
    global _dispatcher
    with _dispatcher_lock:
        if _dispatcher is None:
            _dispatcher = OnDemandDispatcher(send)
        return _dispatcher


def current() -> Optional[OnDemandDispatcher]:
    """The dispatcher if one was created, without creating one."""
    return _dispatcher


def reset_for_tests() -> None:
    global _dispatcher
    with _dispatcher_lock:
        _dispatcher = None
