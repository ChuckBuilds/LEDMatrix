"""The display's live state, as the web interface reads it.

The display process serves its state over the control socket (stage 3, see
docs/IPC_CONTROL_SOCKET.md): what it is showing, the on-demand session, the
brightness, its plugin runtime snapshot and whether its render loop is still
going round. This module holds one ``state.subscribe`` connection for the
web process (:class:`src.ipc.client.StateSubscription`, started on first
use), so a route answers from memory rather than reading a file the display
had to write to the SD card.

Every reader here returns None when the socket cannot vouch for the answer
-- the socket is off or missing (a stopped display, an older one, Windows,
the test suite), the subscription went quiet, or the display's copy is too
old by the same rules the cache readers apply -- and the route then reads
the cache keys and the heartbeat file exactly as it did before.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from src.ipc import client as control_client
from src.ipc.contract import client_socket_paths, socket_supported

logger = logging.getLogger(__name__)

#: The cache readers' max_age for display_current_state: a ``display``
#: section the render thread has not refreshed for this long is unknown, as
#: the cache key would be.
CURRENT_STATE_MAX_AGE_SECONDS = 120.0

#: A one-shot ``state.get``, for a request that arrives before the
#: subscription has its first snapshot. Short: the display answers it from
#: its socket thread, never the render thread.
ONE_SHOT_TIMEOUT_SECONDS = 0.5

_feed: Optional[control_client.StateSubscription] = None
_feed_lock = threading.Lock()


def _subscription() -> Optional[control_client.StateSubscription]:
    """This process's subscription, started on first use; None without a socket."""
    global _feed
    if not socket_supported() or not client_socket_paths():
        return None
    with _feed_lock:
        if _feed is None:
            _feed = control_client.StateSubscription().start()
        return _feed


def stop_subscription() -> None:
    """Stop the subscription (tests; a process that is shutting down)."""
    global _feed
    with _feed_lock:
        feed, _feed = _feed, None
    if feed is not None:
        feed.stop()


def read_state() -> Optional[Dict[str, Any]]:
    """The display's latest state snapshot, or None (read the cache instead).

    From the subscription when it is live; otherwise one ``state.get``.
    """
    feed = _subscription()
    if feed is None:
        return None
    snapshot = feed.latest()
    if snapshot is not None:
        return snapshot
    if feed.last_error == 'unknown_command':
        # A display older than stage 3: a one-shot would fail the same way
        # on every request. The subscription retries every 30 s.
        return None
    try:
        snapshot = control_client.state_get(timeout=ONE_SHOT_TIMEOUT_SECONDS)
    except control_client.ControlError as e:
        logger.debug("Display state not available over the control socket: %s", e)
        return None
    if not isinstance(snapshot.get('state'), dict):
        return None
    snapshot['received_mono'] = time.monotonic()
    return snapshot


def _section(snapshot: Optional[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return None
    state = snapshot.get('state')
    value = state.get(name) if isinstance(state, dict) else None
    return dict(value) if isinstance(value, dict) else None


def current_status(snapshot: Optional[Dict[str, Any]],
                   now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """``/display/current-status``'s data from a snapshot.

    None when the snapshot has no ``display`` section (fall back to the
    cache). A section the render thread last refreshed more than
    CURRENT_STATE_MAX_AGE_SECONDS ago -- the loop is stuck -- is reported
    as unknown, the same answer the cache key gives once it ages out.
    """
    display = _section(snapshot, 'display')
    if display is None:
        return None
    now = time.time() if now is None else now
    updated = display.get('last_updated')
    if (not isinstance(updated, (int, float)) or isinstance(updated, bool)
            or now - updated > CURRENT_STATE_MAX_AGE_SECONDS):
        return {'mode': None, 'plugin_id': None, 'last_updated': None}
    return display


def on_demand_state(snapshot: Optional[Dict[str, Any]],
                    now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """``/display/on-demand/status``'s state from a snapshot; None to fall back.

    ``remaining`` is worked out again from ``expires_at``: the display set it
    when it last published, which may have been minutes ago.
    """
    state = _section(snapshot, 'on_demand')
    if state is None:
        return None
    expires_at = state.get('expires_at')
    if (state.get('active') and isinstance(expires_at, (int, float))
            and not isinstance(expires_at, bool)):
        now = time.time() if now is None else now
        state['remaining'] = max(0.0, expires_at - now)
    return state


def display_gone(snapshot: Optional[Dict[str, Any]]) -> bool:
    """Is there positively no display behind a fallback to the cache?

    True only when the socket should be there (this platform has one and it
    is not switched off) but gave no ``snapshot``, and the render loop's
    heartbeat file says nothing is running either: it is absent (systemd
    removes its directory when the service stops), stale, or written by a
    process that no longer exists -- #726's rules for the runtime snapshot.
    Then what the display last left in the cache is a dead process's answer.

    False whenever the answer is in doubt: a snapshot came in, the socket is
    off or unsupported (Windows, the test suite, a deliberate ``off``), or a
    live heartbeat says the display is running without a socket (an older
    display). Those read the cache exactly as before.
    """
    if snapshot is not None:
        return False
    if not socket_supported() or not client_socket_paths():
        return False
    from src import display_watchdog
    from src.plugin_system.plugin_runtime import process_exists
    heartbeat = display_watchdog.read_heartbeat(display_watchdog.HEARTBEAT_PATH)
    if heartbeat is None:
        return True
    age = display_watchdog.heartbeat_age(heartbeat)
    if age is None or age >= display_watchdog.HEARTBEAT_STALE_SECONDS:
        return True
    pid = heartbeat.get('pid')
    if isinstance(pid, int) and not isinstance(pid, bool) and process_exists(pid) is False:
        return True
    return False


def loop_heartbeat_age(snapshot: Optional[Dict[str, Any]]) -> Optional[float]:
    """The render loop's heartbeat age now; None when the display has no
    beat to report yet (or there is no snapshot)."""
    if not isinstance(snapshot, dict):
        return None
    return control_client.snapshot_loop_age(snapshot)


__all__ = ['current_status', 'display_gone', 'loop_heartbeat_age', 'on_demand_state',
           'read_state', 'stop_subscription']
