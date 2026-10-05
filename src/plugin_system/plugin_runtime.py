"""The display's plugin runtime snapshot, shared with the web interface.

Only the display process runs plugins, so only it knows which ones it has
loaded, where each is in its lifecycle (``plugin_state.PluginStateManager``),
why one failed and which version it is running. It publishes that to the
shared cache directory -- the channel, and the file permissions, that the
error snapshot, plugin health and ``display_current_state`` already use --
and the web interface reads it back for ``/api/v3/plugins/installed``,
``/api/v3/plugins/state`` and state reconciliation.

    PLUGIN_RUNTIME_KEY   written by the display service only

Writes. The cache lives on disk, usually the SD card, so the snapshot is
written when something a reader would see changes, at most once every
``MIN_INTERVAL`` seconds, and otherwise once every ``REFRESH_INTERVAL``
seconds as a heartbeat. An ordinary plugin update is not a change: the
RUNNING state it passes through is published as ENABLED
(``plugin_state.published_state``). A display with nothing changing writes
this one small file once a minute.

Staleness. Every snapshot carries ``published_at`` (wall clock) and
``stale_after``. A reader treats a snapshot older than that as unknown, not
as the truth: a display that died without cleaning up leaves its last
snapshot behind. A display that stops cleanly publishes ``running: false``
on the way out, so readers see "stopped" at once rather than after the
stale window. Nothing on the reading side reports a runtime fact from a
snapshot that is not live.

Render-loop liveness. The snapshot is written from its own thread, which
keeps going when the render loop hangs inside a plugin. So the reader also
checks the render loop's heartbeat (``src/display_watchdog.py``, the file
``/api/v3/health`` reports as ``checks.display_loop``): a live snapshot from
the process whose heartbeat has gone stale is ``stalled``, as the health
check says, not ``live``. No extra writes: the heartbeat already exists, on
tmpfs. A missing heartbeat (dev server, emulator, Windows, a display still
starting up) or one from another process (a display restarted after a
watchdog kill) says nothing, and the snapshot is judged on its own.

The control socket. Where the display serves its state stream (stage 3,
docs/IPC_CONTROL_SOCKET.md), every tick also hands the snapshot to it, in
memory, and the web interface reads it there first
(``view_from_socket_state``, judged by the same rules). While the socket
serves those readers, the cache copy is their fallback and an unchanged
snapshot is rewritten every ``RELAXED_REFRESH_INTERVAL`` instead.

A dead publisher. systemd removes the heartbeat's directory when the
service stops, so after a watchdog kill there is no heartbeat to go stale.
The reader then asks whether the snapshot's ``pid`` still exists (POSIX
``kill(pid, 0)``, which sends nothing): a running snapshot from a process
that is gone is ``stale`` at once rather than ``live`` for the rest of its
``stale_after`` window.
"""

import math
import os
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional

from src import display_watchdog
from src.logging_config import get_logger
from src.redaction import redact_credentials

logger = get_logger(__name__)

PLUGIN_RUNTIME_KEY = "plugin_runtime_snapshot"
SNAPSHOT_SCHEMA = 1

#: Shortest gap, in seconds, between two change-driven writes. Startup loads
#: every plugin in a burst, and a plugin failing each update cycle changes its
#: error info each time; either is written at most this often.
MIN_INTERVAL = 10.0

#: An unchanged snapshot is rewritten this often so readers can tell a quiet
#: display from a dead one.
REFRESH_INTERVAL = 60.0

#: How often the publisher thread looks for changes: an in-memory comparison.
TICK_INTERVAL = 5.0

#: A snapshot older than this is stale: three missed refreshes.
STALE_AFTER = 3 * REFRESH_INTERVAL

#: The refresh while the control socket serves the web interface's readers
#: (``StateHub.readers_active``). The cache copy is then only their fallback,
#: so an unchanged snapshot is rewritten half as often; the snapshot says so
#: in its own ``refresh_interval`` and ``stale_after``.
RELAXED_REFRESH_INTERVAL = 2 * REFRESH_INTERVAL

#: The control socket's section for this snapshot (``state.plugins``).
STATE_SECTION = "plugins"

#: Bounds on a published ``stale_after``, so a corrupt value can make a
#: reader neither trust a dead display for hours nor distrust a live one.
_STALE_AFTER_MIN = 30.0
_STALE_AFTER_MAX = 3600.0

_ERROR_MESSAGE_CHARS = 200
_ERROR_TYPE_CHARS = 80
_ID_CHARS = 100
_VERSION_CHARS = 40
#: Bounds on a plugin's published ``modes``: a plugin computes them, so a
#: runaway list must not bloat a file written to the SD card.
_MAX_MODES = 200

#: Reader statuses. Only LIVE carries runtime facts.
LIVE = "live"
STALE = "stale"
#: The snapshot is fresh but the render loop's heartbeat is not: the display
#: is hung (or was just killed by the watchdog), as /api/v3/health reports.
STALLED = "stalled"
STOPPED = "stopped"
UNKNOWN = "unknown"


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _epoch(value: Any) -> Optional[float]:
    """Seconds since the epoch for a float or a datetime; None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    timestamp = getattr(value, "timestamp", None)
    if callable(timestamp):
        try:
            number = float(timestamp())
        except (TypeError, ValueError, OverflowError, OSError):
            return None
        return number if math.isfinite(number) else None
    return None


def summarize_error(error_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A short, redacted summary of the state machine's error info.

    ``message`` is redacted before it is clipped: clipping first could cut a
    ``token=`` marker off and keep the secret after it. No stack trace: the
    full error, with its trace, is in the error snapshot (/api/v3/errors).
    """
    if not isinstance(error_info, dict):
        return None
    message = error_info.get("error")
    error_type = error_info.get("error_type")
    return {
        "type": _clip(error_type, _ERROR_TYPE_CHARS) if error_type else None,
        "message": _clip(redact_credentials(message if isinstance(message, str)
                                            else str(message or "")),
                         _ERROR_MESSAGE_CHARS),
        "at": _epoch(error_info.get("timestamp")),
        "recoverable": bool(error_info.get("recoverable", False)),
    }


def _published_modes(modes: Any) -> Optional[List[str]]:
    """The registered display modes as a snapshot carries them, or None."""
    if not isinstance(modes, list):
        return None
    return [_clip(m, _ID_CHARS) for m in modes[:_MAX_MODES] if isinstance(m, str)]


def build_runtime_snapshot(state_manager: Any, *, started_at: float,
                           now: Optional[float] = None,
                           running: bool = True,
                           refresh_interval: float = REFRESH_INTERVAL) -> Dict[str, Any]:
    """The snapshot for ``state_manager`` (a plugin_state.PluginStateManager).

    A stopped snapshot (``running=False``) lists no plugins: nothing is
    loaded once the display has gone.
    """
    plugins: Dict[str, Dict[str, Any]] = {}
    if running:
        for plugin_id, record in state_manager.runtime_records().items():
            version = record.get("version")
            plugins[_clip(plugin_id, _ID_CHARS)] = {
                "loaded": bool(record.get("loaded")),
                "state": record.get("state"),
                "error": summarize_error(record.get("error_info")),
                "version": _clip(version, _VERSION_CHARS) if version else None,
                "loaded_at": _epoch(record.get("loaded_at")),
                "modes": _published_modes(record.get("modes")),
            }
    return {
        "schema": SNAPSHOT_SCHEMA,
        "running": running,
        "published_at": time.time() if now is None else now,
        "started_at": started_at,
        "refresh_interval": refresh_interval,
        "stale_after": 3 * refresh_interval,
        "pid": os.getpid(),
        "plugins": plugins,
    }


class PluginRuntimePublisher:
    """Publishes the display's plugin state machine to the shared cache.

    Runs in the display service only. tick() is the whole job; start() calls
    it from a daemon thread every TICK_INTERVAL seconds. Nothing here raises:
    a failed write is logged at debug and retried on a later tick, at the
    throttled rate.
    """

    def __init__(self, cache_manager: Any, state_manager: Any,
                 min_interval: float = MIN_INTERVAL,
                 refresh_interval: float = REFRESH_INTERVAL,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time) -> None:
        self.cache_manager = cache_manager
        self.state_manager = state_manager
        self.min_interval = min_interval
        self.refresh_interval = refresh_interval
        self._clock = clock
        self._wall_clock = wall_clock
        self.started_at = wall_clock()
        # None forces a first publish, which replaces whatever a previous run
        # of the service left behind.
        self._published_change: Optional[int] = None
        self._last_attempt: Optional[float] = None
        self._tick_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # The control socket's state stream (src/ipc/server.StateHub), when
        # the display serves one: every tick also hands it the snapshot, in
        # memory, and the cache refresh relaxes while it has readers.
        self._hub: Any = None
        self._hub_change: Optional[int] = None
        self._hub_snapshot: Optional[Dict[str, Any]] = None
        self.relaxed_refresh_interval = RELAXED_REFRESH_INTERVAL

    def attach_hub(self, hub: Any) -> None:
        """Also publish to the control socket's state hub, starting now."""
        with self._tick_lock:
            self._hub = hub
            self._hub_change = None
            self._hub_snapshot = None
            try:
                self._push_to_hub(self.state_manager.change_count)
            except Exception as err:  # never let reporting break the display
                logger.debug("Could not publish the plugin runtime state: %s", err,
                             exc_info=True)

    def _push_to_hub(self, change: int) -> None:
        """The snapshot to the state hub: rebuilt when the state machine
        changed, otherwise the last one with a new ``published_at``, which
        the hub does not count as a new version. In memory, every tick, so
        the socket's copy is never more than a tick old."""
        hub = self._hub
        if hub is None:
            return
        now = self._wall_clock()
        if self._hub_snapshot is None or change != self._hub_change:
            snapshot = build_runtime_snapshot(self.state_manager, started_at=self.started_at,
                                              now=now)
        else:
            snapshot = dict(self._hub_snapshot, published_at=now)
        hub.publish(STATE_SECTION, snapshot, volatile=("published_at",))
        self._hub_snapshot = snapshot
        self._hub_change = change

    def _cache_refresh_interval(self) -> float:
        """The cache refresh: relaxed while the socket serves the readers."""
        hub = self._hub
        try:
            if hub is not None and hub.readers_active():
                return self.relaxed_refresh_interval
        except Exception:  # pylint: disable=broad-except
            # The normal interval is the safe answer: it only writes more.
            logger.debug("State hub readers_active() failed; using the normal refresh", exc_info=True)
        return self.refresh_interval

    def _write(self, running: bool, refresh_interval: Optional[float] = None) -> None:
        snapshot = build_runtime_snapshot(
            self.state_manager, started_at=self.started_at, now=self._wall_clock(),
            running=running,
            refresh_interval=self.refresh_interval if refresh_interval is None
            else refresh_interval)
        self.cache_manager.set(PLUGIN_RUNTIME_KEY, snapshot)

    def tick(self) -> bool:
        """Publish if something changed (throttled) or the refresh is due.
        True if a snapshot was written to the cache."""
        with self._tick_lock:
            try:
                change = self.state_manager.change_count
                try:
                    self._push_to_hub(change)
                except Exception as err:  # the cache copy still goes out below
                    logger.debug("Could not publish the plugin runtime state: %s", err,
                                 exc_info=True)
                now = self._clock()
                refresh = self._cache_refresh_interval()
                since = None if self._last_attempt is None else now - self._last_attempt
                if since is not None:
                    if change == self._published_change:
                        if since < refresh:
                            return False
                    elif since < self.min_interval:
                        return False
                # Stamp the attempt before writing: a cache that keeps failing
                # is retried at the throttled rate, not on every tick.
                self._last_attempt = now
                self._write(running=True, refresh_interval=refresh)
                self._published_change = change
                return True
            except Exception as err:  # never let reporting break the display
                logger.debug("Could not publish the plugin runtime snapshot: %s",
                             err, exc_info=True)
                return False

    def start(self, interval: float = TICK_INTERVAL) -> None:
        """Tick from a daemon thread until stop(). A no-op while running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def run() -> None:
            self.tick()
            while not self._stop.wait(interval):
                self.tick()

        self._thread = threading.Thread(target=run, name="plugin-runtime-publisher",
                                        daemon=True)
        self._thread.start()

    def stop(self, publish_stopped: bool = True) -> None:
        """Stop ticking and, by default, publish ``running: false`` so readers
        see the display as stopped now rather than after the stale window."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if publish_stopped:
            with self._tick_lock:
                try:
                    self._write(running=False)
                except Exception as err:
                    logger.debug("Could not publish the stopped plugin runtime snapshot: %s",
                                 err, exc_info=True)


def start_plugin_runtime_publisher(cache_manager: Any,
                                   state_manager: Any) -> Optional[PluginRuntimePublisher]:
    """Start publishing the display's plugin runtime state. Display service
    only: whichever process calls it becomes the source readers trust.
    Never raises."""
    try:
        publisher = PluginRuntimePublisher(cache_manager, state_manager)
        publisher.start()
        return publisher
    except Exception as err:
        logger.warning("Plugin runtime reporting to the web interface is unavailable: %s", err)
        return None


# --- Reading side (web interface) -------------------------------------------

#: What a reader reports for a plugin when it does not know.
_UNKNOWN_PLUGIN: Dict[str, Any] = {
    "loaded": None,
    "state": None,
    "error_info": None,
    "loaded_version": None,
    "loaded_at": None,
}

#: A plugin a live snapshot does not list: the display has not loaded it
#: (never enabled, or unloaded since), which is what its state machine
#: reports for an id it has no record of.
_NOT_LOADED_PLUGIN: Dict[str, Any] = {
    "loaded": False,
    "state": "unloaded",
    "error_info": None,
    "loaded_version": None,
    "loaded_at": None,
}


@dataclass(frozen=True)
class PluginRuntimeView:
    """What a reader may say about the display's plugins right now.

    ``status``: ``live`` (a fresh snapshot from a running display),
    ``stale`` (the last snapshot is older than its ``stale_after``: the
    display is hung or died without cleaning up), ``stalled`` (the snapshot
    is fresh but the same process's render-loop heartbeat is stale: the
    render loop is hung), ``stopped`` (the display
    said so on its way out) or ``unknown`` (no readable snapshot). Only a
    live view reports per-plugin facts; every other status answers None for
    them, so a caller cannot pass stale truth on by accident.
    """

    status: str
    published_at: Optional[float] = None
    age_seconds: Optional[float] = None
    stale_after: float = STALE_AFTER
    plugins: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Age of the render loop's heartbeat, when it was taken into account.
    heartbeat_age_seconds: Optional[float] = None
    #: Where the snapshot came from: ``cache`` (the shared cache file and the
    #: heartbeat file) or ``socket`` (the control socket's state stream).
    source: str = "cache"

    @property
    def live(self) -> bool:
        return self.status == LIVE

    def plugin(self, plugin_id: str) -> Dict[str, Any]:
        """``loaded``, ``state``, ``error_info``, ``loaded_version`` and
        ``loaded_at`` for one plugin; all None unless the view is live."""
        if not self.live:
            return dict(_UNKNOWN_PLUGIN)
        record = self.plugins.get(plugin_id)
        if not isinstance(record, dict):
            return dict(_NOT_LOADED_PLUGIN)
        error = record.get("error")
        return {
            "loaded": bool(record.get("loaded")),
            "state": record.get("state") if isinstance(record.get("state"), str) else None,
            "error_info": dict(error) if isinstance(error, dict) else None,
            "loaded_version": record.get("version"),
            "loaded_at": record.get("loaded_at"),
        }

    def display_modes(self, plugin_id: str) -> Optional[List[str]]:
        """The display modes the display registered for ``plugin_id``: what
        it rotates and accepts on-demand, including modes a plugin computes
        from its config. None unless the view is live and the plugin is
        loaded with its modes registered -- the caller then falls back to
        the manifest's ``display_modes``."""
        if not self.live:
            return None
        record = self.plugins.get(plugin_id)
        modes = record.get("modes") if isinstance(record, dict) else None
        if not isinstance(modes, list):
            return None
        modes = [m for m in modes if isinstance(m, str)]
        return modes or None

    def describe(self) -> Dict[str, Any]:
        """The view's own status, for a response to carry beside the facts."""
        return {
            "status": self.status,
            "published_at": self.published_at,
            "age_seconds": None if self.age_seconds is None else round(self.age_seconds, 1),
            "stale_after": self.stale_after,
            "heartbeat_age_seconds": (None if self.heartbeat_age_seconds is None
                                      else round(self.heartbeat_age_seconds, 1)),
            "source": self.source,
        }


def _stale_after_of(snapshot: Dict[str, Any]) -> float:
    value = snapshot.get("stale_after")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return STALE_AFTER
    number = float(value)
    if not math.isfinite(number):
        return STALE_AFTER
    return min(max(number, _STALE_AFTER_MIN), _STALE_AFTER_MAX)


def _heartbeat_age_for(snapshot: Dict[str, Any], heartbeat: Any,
                       now_mono: Optional[float]) -> Optional[float]:
    """Age of ``heartbeat`` if it comes from the process that published
    ``snapshot``; None when there is none, it has no time, or it belongs to
    another process (a restarted display, or a heartbeat left by a killed one)."""
    if not isinstance(heartbeat, dict):
        return None
    beat_pid = heartbeat.get("pid")
    snap_pid = snapshot.get("pid")
    if (isinstance(beat_pid, bool) or not isinstance(beat_pid, int)
            or isinstance(snap_pid, bool) or not isinstance(snap_pid, int)
            or beat_pid != snap_pid):
        return None
    return display_watchdog.heartbeat_age(heartbeat, now_mono=now_mono)


def process_exists(pid: int) -> Optional[bool]:
    """Whether process ``pid`` exists: True, False, or None when this
    platform cannot tell. POSIX only -- on Windows ``os.kill`` terminates.
    Signal 0 sends nothing; EPERM (the display runs as root, the web
    interface does not) still means the process is there."""
    if os.name != "posix" or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def view_from_snapshot(snapshot: Any, now: Optional[float] = None,
                       heartbeat: Any = None,
                       now_mono: Optional[float] = None,
                       process_alive: Optional[Callable[[int], Optional[bool]]] = None,
                       ) -> PluginRuntimeView:
    """Judge a snapshot read from the cache; never raises.

    ``heartbeat`` is the render loop's heartbeat
    (``display_watchdog.read_heartbeat()``), or None when there is none. A
    live snapshot whose process's heartbeat is at least
    ``display_watchdog.HEARTBEAT_STALE_SECONDS`` old is ``stalled``: the
    threshold /api/v3/health uses for ``checks.display_loop``.
    ``process_alive`` (``process_exists`` when reading the real cache) says
    whether the snapshot's publisher still exists; a running snapshot from
    one that is gone is ``stale``.
    """
    if not isinstance(snapshot, dict) or snapshot.get("schema") != SNAPSHOT_SCHEMA:
        return PluginRuntimeView(status=UNKNOWN)
    published_at = _epoch(snapshot.get("published_at"))
    if published_at is None:
        return PluginRuntimeView(status=UNKNOWN)
    stale_after = _stale_after_of(snapshot)
    age = (time.time() if now is None else now) - published_at
    if snapshot.get("running") is not True:
        return PluginRuntimeView(status=STOPPED, published_at=published_at,
                                 age_seconds=max(age, 0.0), stale_after=stale_after)
    # A snapshot from the future is trusted a little: the Pi has no RTC and
    # its clock steps when NTP syncs. Far in the future, it cannot be dated.
    if age > stale_after or age < -stale_after:
        return PluginRuntimeView(status=STALE, published_at=published_at,
                                 age_seconds=age, stale_after=stale_after)
    pid = snapshot.get("pid")
    if (process_alive is not None and isinstance(pid, int) and not isinstance(pid, bool)
            and process_alive(pid) is False):
        return PluginRuntimeView(status=STALE, published_at=published_at,
                                 age_seconds=max(age, 0.0), stale_after=stale_after)
    beat_age = _heartbeat_age_for(snapshot, heartbeat, now_mono)
    if beat_age is not None and beat_age >= display_watchdog.HEARTBEAT_STALE_SECONDS:
        return PluginRuntimeView(status=STALLED, published_at=published_at,
                                 age_seconds=max(age, 0.0), stale_after=stale_after,
                                 heartbeat_age_seconds=beat_age)
    plugins = snapshot.get("plugins")
    return PluginRuntimeView(
        status=LIVE, published_at=published_at, age_seconds=max(age, 0.0),
        stale_after=stale_after, heartbeat_age_seconds=beat_age,
        plugins={k: v for k, v in plugins.items() if isinstance(v, dict)}
        if isinstance(plugins, dict) else {},
    )


def view_from_socket_state(snapshot: Any, now: Optional[float] = None,
                           now_mono: Optional[float] = None) -> Optional[PluginRuntimeView]:
    """Judge the ``plugins`` section of a control-socket state snapshot by
    the same rules as the cache copy; None when it has none (an older
    display, or a snapshot too large to carry it), so the caller reads the
    cache instead.

    The display measured its render loop's heartbeat age when it answered
    (``state.loop``); that is the heartbeat here, aged by the time since the
    answer arrived. A live snapshot with a stalled loop is ``stalled``, and a
    snapshot older than its ``stale_after`` (the publisher thread stopped)
    is ``stale``, exactly as for the cache. The display answered, so its
    process is alive: there is no pid check.
    """
    from src.ipc.client import snapshot_loop_age  # stdlib-only module
    if not isinstance(snapshot, dict):
        return None
    state = snapshot.get("state")
    plugins = state.get(STATE_SECTION) if isinstance(state, dict) else None
    if not isinstance(plugins, dict):
        return None
    now_mono = time.monotonic() if now_mono is None else now_mono
    beat_age = snapshot_loop_age(snapshot, now_mono=now_mono)
    heartbeat = None
    if beat_age is not None:
        heartbeat = {"pid": plugins.get("pid"), "mono": now_mono - beat_age}
    view = view_from_snapshot(plugins, now=now, heartbeat=heartbeat, now_mono=now_mono)
    return replace(view, source="socket")


def read_plugin_runtime(cache_manager: Any, now: Optional[float] = None,
                        heartbeat_path: Optional[str] = None) -> PluginRuntimeView:
    """The display's latest snapshot, judged for staleness and against the
    render loop's heartbeat. Never raises; a missing cache manager or an
    unreadable snapshot is ``unknown``.

    memory_ttl=0: the key is written by the other process, so only the file
    is current. ``heartbeat_path`` defaults to
    ``display_watchdog.HEARTBEAT_PATH``.
    """
    if cache_manager is None:
        return PluginRuntimeView(status=UNKNOWN)
    try:
        snapshot = cache_manager.get(PLUGIN_RUNTIME_KEY, max_age=None, memory_ttl=0)
    except Exception as err:
        logger.debug("Could not read the plugin runtime snapshot: %s", err, exc_info=True)
        return PluginRuntimeView(status=UNKNOWN)
    try:
        heartbeat = display_watchdog.read_heartbeat(
            heartbeat_path or display_watchdog.HEARTBEAT_PATH)
    except Exception as err:  # read_heartbeat does not raise; belt and braces
        logger.debug("Could not read the display heartbeat: %s", err, exc_info=True)
        heartbeat = None
    return view_from_snapshot(snapshot, now=now, heartbeat=heartbeat,
                              process_alive=process_exists)
