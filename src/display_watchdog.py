"""Render-loop liveness: systemd watchdog pings and a heartbeat file.

A panel can freeze while ``ledmatrix.service`` stays "active": a plugin's
``display()`` that never returns, a deadlock, a stuck hardware swap. Nothing
outside the process could tell, so nothing restarted it. This module lets the
render loop prove it is still going round, in two ways:

* **systemd watchdog.** The unit sets ``WatchdogSec=`` and
  ``NotifyAccess=main``; this sends ``WATCHDOG=1`` over ``$NOTIFY_SOCKET``.
  When the pings stop, systemd kills the process (SIGABRT, so faulthandler
  prints every thread's stack to the journal first) and ``Restart=`` brings it
  back.
* **Heartbeat file**, ``/run/ledmatrix/display-heartbeat.json``, for the web
  interface's ``/api/v3/health`` and the automatic update's health check.
  ``/run`` is tmpfs, so the writes never reach the SD card.

Both are driven only from the render thread -- ``beat()`` from any other
thread is ignored -- so a render thread stuck inside a plugin stops them even
while every other thread carries on. The unit's ``WatchdogSec=`` is the
steady-state limit; start-up (plugin loads, the 20s initial update budget,
dependency installs) is far longer and happens before the render loop exists,
so ``begin_startup()`` widens the limit for it and the render loop narrows it
back, sends ``READY=1`` and starts pinging once its first frame is on the
panel. See docs/ARCHITECTURE.md ("Liveness") for the unit settings.

Standard library only, and no import of the rest of ``src``: ``run.py`` loads
this before anything heavy so the start-up allowance is in place long before
the unit's own ``WatchdogSec`` could expire. Without ``$NOTIFY_SOCKET`` (dev
server, emulator, Windows, an older unit) every call is a cheap no-op, and the
heartbeat is written only where ``/run/ledmatrix`` exists or can be created.
"""
import contextlib
import json
import logging
import os
import socket
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Iterator, Mapping, Optional

logger = logging.getLogger(__name__)

#: Where the display writes its heartbeat. ``RuntimeDirectory=ledmatrix`` in
#: the unit creates the directory; a display running under an older unit
#: creates it itself (it runs as root). The web interface, which is not root,
#: only reads it: the directory is 0755 and the file 0644.
HEARTBEAT_DIR = '/run/ledmatrix'
HEARTBEAT_NAME = 'display-heartbeat.json'
HEARTBEAT_PATH = HEARTBEAT_DIR + '/' + HEARTBEAT_NAME

#: How often the render loop pings systemd and rewrites the heartbeat. Beats
#: come many times a second; this is the rate limit on the side effects.
BEAT_INTERVAL_SECONDS = 5.0

#: A heartbeat older than this means the render loop has stopped. Above the
#: longest gap a healthy loop has (the executor's 30s display() timeout), so a
#: slow plugin does not read as a frozen panel.
HEARTBEAT_STALE_SECONDS = 60.0

#: The watchdog limit while the process starts, before the render loop runs.
#: Start-up loads every plugin (pip included, when a dependency is missing:
#: up to 300s a try), then spends up to 20s on initial updates. A hang in
#: there is still caught, just later.
STARTUP_ALLOWANCE_SECONDS = 15 * 60

#: The watchdog limit while the render thread loads a plugin that was just
#: enabled from the web UI: loading can run pip, on this thread.
PLUGIN_LOAD_ALLOWANCE_SECONDS = 15 * 60


# -- sd_notify -------------------------------------------------------------

def notify(message: str, environ: Optional[Mapping[str, str]] = None,
           socket_factory: Optional[Callable[..., Any]] = None) -> bool:
    """Send ``message`` to systemd over ``$NOTIFY_SOCKET``; True if it was sent.

    The same protocol as libsystemd's ``sd_notify()``: one datagram of
    newline-separated ``KEY=VALUE`` lines to an AF_UNIX socket. An address
    starting with ``@`` is in the abstract namespace (a leading NUL byte).
    Never raises: a missing socket or a failed send is simply False, so a
    display run outside systemd behaves exactly as before.
    """
    env = os.environ if environ is None else environ
    address = env.get('NOTIFY_SOCKET') or ''
    if address.startswith('@'):
        address = '\0' + address[1:]
    elif not address.startswith('/'):
        # Unset, or a vsock: address (systemd 253+, VMs only).
        return False
    family = getattr(socket, 'AF_UNIX', None)
    if family is None:
        return False
    factory = socket_factory or socket.socket
    try:
        sock = factory(family, socket.SOCK_DGRAM | getattr(socket, 'SOCK_CLOEXEC', 0))
        try:
            sock.connect(address)
            sock.sendall(message.encode('utf-8'))
        finally:
            sock.close()
        return True
    except OSError as e:
        logger.debug("sd_notify(%r) failed: %s", message, e)
        return False


def watchdog_usec(environ: Optional[Mapping[str, str]] = None) -> Optional[int]:
    """The unit's ``WatchdogSec`` in microseconds, or None when it has none.

    systemd passes it as ``$WATCHDOG_USEC``, with ``$WATCHDOG_PID`` naming the
    process it is meant for (a child that inherited the environment must not
    think the watchdog is its own).
    """
    env = os.environ if environ is None else environ
    pid = env.get('WATCHDOG_PID')
    if pid and pid != str(os.getpid()):
        return None
    try:
        usec = int(env.get('WATCHDOG_USEC', ''))
    except ValueError:
        return None
    return usec if usec > 0 else None


# -- heartbeat reading (web interface) ---------------------------------------

def read_heartbeat(path: str = HEARTBEAT_PATH) -> Optional[Dict[str, Any]]:
    """The heartbeat the display last wrote, or None when there is none.

    None covers a display that does not write one -- dev server, emulator,
    Windows, a display that has not drawn its first frame yet -- as well as an
    unreadable file, so callers fall back to whatever they did before.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def heartbeat_age(data: Mapping[str, Any], now_mono: Optional[float] = None,
                  now_wall: Optional[float] = None) -> Optional[float]:
    """Seconds since the heartbeat in ``data`` was written, or None if it has no time.

    Measured on the monotonic clock when it can be: on Linux that is
    CLOCK_MONOTONIC, shared by every process, and it does not jump when NTP
    first corrects the clock of a Pi with no RTC. /run is emptied at boot, so
    a heartbeat always comes from this boot. Falls back to the wall clock.
    """
    now_mono = time.monotonic() if now_mono is None else now_mono
    now_wall = time.time() if now_wall is None else now_wall
    mono = data.get('mono')
    if isinstance(mono, (int, float)) and not isinstance(mono, bool):
        age = now_mono - mono
        if age >= -1.0:  # a clock this far behind is not the same clock
            return max(age, 0.0)
    wall = data.get('wall')
    if isinstance(wall, (int, float)) and not isinstance(wall, bool):
        return max(now_wall - wall, 0.0)
    return None


# -- the render loop's side ----------------------------------------------------

_DEFAULT_DIR = object()


class RenderWatchdog:
    """Pings systemd and writes the heartbeat, from the render thread only.

    Lifecycle: ``begin_startup()`` as the process starts, ``bind_render_thread()``
    when ``DisplayController.run()`` starts, then ``note_frame()`` for every
    frame pushed to the panel and ``beat()`` / ``loop_pass()`` from every place
    the render loop reliably comes back to. The first beat after the first
    frame (or after the loop's first full pass, when there is nothing to draw)
    arms it: ``READY=1``, the unit's own ``WatchdogSec``, and the heartbeat.
    """

    def __init__(self, environ: Optional[Mapping[str, str]] = None,
                 send: Optional[Callable[[str], bool]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time,
                 heartbeat_dir: Any = _DEFAULT_DIR,
                 enable_faulthandler: bool = True):
        env = dict(os.environ if environ is None else environ)
        self._send = send or (lambda message: notify(message, env))
        self._clock = clock
        self._wall_clock = wall_clock
        self._usec = watchdog_usec(env)
        if heartbeat_dir is _DEFAULT_DIR:
            # /run exists only on Linux; elsewhere (Windows dev) there is no
            # heartbeat rather than a C:\run folder.
            heartbeat_dir = HEARTBEAT_DIR if os.name == 'posix' else None
        self._heartbeat_dir: Optional[str] = heartbeat_dir
        # None until the first write; False for good if that one failed
        # (nowhere to write: not root, no /run); True once one landed.
        self._heartbeat_ok: Optional[bool] = None
        self._heartbeat_warned = False
        self._enable_faulthandler = enable_faulthandler
        self._render_thread: Optional[int] = None
        self._frame_pushed = False
        self._passes = 0
        self._armed = False
        self._last_beat: Optional[float] = None
        self._extend_depth = 0
        interval = BEAT_INTERVAL_SECONDS
        if self._usec:
            # systemd's advice is to ping at half the limit; a third leaves
            # room for one late beat even if someone sets a very short one.
            interval = min(interval, self._usec / 1e6 / 3)
        self._interval = interval

    @property
    def armed(self) -> bool:
        return self._armed

    def liveness(self) -> Dict[str, Any]:
        """The heartbeat, in memory: what the control socket's state stream
        reports as ``loop``.

        ``heartbeat_age_seconds`` is the age of the render thread's last beat,
        the beat that writes the heartbeat file, so it ages at the same rate
        and is judged by the same ``HEARTBEAT_STALE_SECONDS``. None until the
        loop has drawn its first frame. Any thread may call this: it only
        reads two attributes.
        """
        last = self._last_beat
        age = None
        if self._armed and last is not None:
            age = max(self._clock() - last, 0.0)
        return {'heartbeat_age_seconds': age, 'armed': self._armed,
                'stale_after': HEARTBEAT_STALE_SECONDS}

    def _on_render_thread(self) -> bool:
        return self._render_thread is not None and threading.get_ident() == self._render_thread

    def begin_startup(self) -> None:
        """Widen the watchdog to cover start-up. Call as early as possible.

        systemd starts the watchdog clock when a Type=simple service starts,
        and start-up routinely takes longer than the render loop's limit.
        Only widens: an operator who set a longer ``WatchdogSec`` keeps it.
        """
        if not self._usec:
            return
        allowance = max(self._usec, int(STARTUP_ALLOWANCE_SECONDS * 1e6))
        self._send(f'WATCHDOG_USEC={allowance}\nSTATUS=Starting: loading plugins')

    def bind_render_thread(self) -> None:
        """Mark the calling thread as the render thread; beats from others are ignored."""
        self._render_thread = threading.get_ident()
        self._frame_pushed = False
        self._passes = 0

    def note_frame(self) -> None:
        """A frame was pushed to the panel (DisplayManager.update_display).

        Any thread may push the first one -- the first dispatch of a screen
        runs on PluginExecutor's thread -- so this only records it; the
        render thread's next beat arms the watchdog.
        """
        if self._render_thread is None:
            return  # start-up screens, before the render loop exists
        self._frame_pushed = True
        if self._on_render_thread():
            self.beat()

    def loop_pass(self) -> None:
        """The top of the render loop's ``while True``.

        A second arrival here means a whole pass finished. That counts as the
        first frame when there was nothing to draw (no plugins enabled, every
        screen empty): the loop is plainly alive, and a watchdog that never
        armed would leave a later hang uncaught.
        """
        if not self._on_render_thread():
            return
        self._passes += 1
        if self._passes > 1:
            self._frame_pushed = True
        self.beat()

    def beat(self) -> None:
        """The render loop is still going round. Cheap; call it freely."""
        if not self._on_render_thread():
            return
        if not self._armed:
            if not self._frame_pushed:
                return
            self._arm()
            return
        now = self._clock()
        if self._last_beat is not None and now - self._last_beat < self._interval:
            return
        self._last_beat = now
        if self._usec:
            self._send('WATCHDOG=1')
        self._write_heartbeat(now)

    def _arm(self) -> None:
        self._armed = True
        self._last_beat = self._clock()
        if self._usec:
            # Back from the start-up allowance to the unit's own limit.
            self._send(f'READY=1\nWATCHDOG_USEC={self._usec}\nWATCHDOG=1\nSTATUS=Rendering')
            self._install_faulthandler()
            logger.info("systemd watchdog armed: the render loop must check in every %.0fs",
                        self._usec / 1e6)
        else:
            self._send('READY=1\nSTATUS=Rendering')
        self._write_heartbeat(self._last_beat)

    def _install_faulthandler(self) -> None:
        """Dump every thread's stack when the watchdog's SIGABRT arrives.

        That trace, in the journal, is what says which plugin the render
        thread was stuck in.
        """
        if not self._enable_faulthandler:
            return
        try:
            import faulthandler
            import sys
            if not faulthandler.is_enabled() and sys.stderr is not None:
                faulthandler.enable(all_threads=True)
        except (ImportError, RuntimeError, ValueError, OSError, AttributeError) as e:
            logger.debug("faulthandler not enabled: %s", e)

    @contextlib.contextmanager
    def extended(self, seconds: float, reason: str = '') -> Iterator[None]:
        """Allow the render thread ``seconds`` for one blocking job.

        For the few legitimate jobs that can outlast the watchdog, such as
        loading a newly enabled plugin, which can run pip on this thread.
        Nests; the unit's limit comes back when the outermost one ends.
        """
        if not (self._armed and self._usec and self._on_render_thread()):
            yield
            return
        usec = max(self._usec, int(seconds * 1e6))
        if self._extend_depth == 0:
            self._send(f'WATCHDOG_USEC={usec}\nWATCHDOG=1'
                       + (f'\nSTATUS=Busy: {reason}' if reason else ''))
        self._extend_depth += 1
        try:
            yield
        finally:
            self._extend_depth -= 1
            if self._extend_depth == 0:
                self._send(f'WATCHDOG_USEC={self._usec}\nWATCHDOG=1\nSTATUS=Rendering')
                self._last_beat = self._clock()
                self._write_heartbeat(self._last_beat)

    def stopping(self) -> None:
        """Clean shutdown: tell systemd, and take the heartbeat down with us.

        A heartbeat left behind by a stopped display would read as a frozen
        one to the web interface.
        """
        if self._usec or self._armed:
            self._send('STOPPING=1')
        path = self._heartbeat_path()
        if path and self._heartbeat_ok:
            try:
                os.unlink(path)
            except OSError:
                pass

    # -- heartbeat file ----------------------------------------------------

    def _heartbeat_path(self) -> Optional[str]:
        if not self._heartbeat_dir:
            return None
        return os.path.join(self._heartbeat_dir, HEARTBEAT_NAME)

    def _write_heartbeat(self, now_mono: float) -> None:
        path = self._heartbeat_path()
        if path is None or self._heartbeat_ok is False:
            return
        directory = self._heartbeat_dir
        try:
            if not os.path.isdir(directory):
                # An install whose unit predates RuntimeDirectory=: the
                # display runs as root and can make it. Anyone else cannot,
                # and gets no heartbeat -- which readers treat as "unknown".
                os.makedirs(directory, mode=0o755, exist_ok=True)
            payload = json.dumps({'pid': os.getpid(), 'mono': now_mono,
                                  'wall': self._wall_clock()})
            fd, tmp = tempfile.mkstemp(dir=directory, prefix='.heartbeat-')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(payload)
                os.chmod(tmp, 0o644)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            if self._heartbeat_ok is None:
                logger.info("Writing the display heartbeat to %s", path)
            self._heartbeat_ok = True
        except OSError as e:
            if self._heartbeat_ok is None:
                logger.info("Not writing a display heartbeat (%s: %s); health checks "
                            "fall back to their older signals", directory, e)
                self._heartbeat_ok = False
            elif not self._heartbeat_warned:
                # It worked before, so keep trying, but say so only once.
                logger.warning("Could not update the display heartbeat: %s", e)
                self._heartbeat_warned = True


#: The process-wide instance: one display process, one render loop.
watchdog = RenderWatchdog()


def beat() -> None:
    """Module-level shortcut so the Vegas loop and the plugin manager need no reference."""
    watchdog.beat()


def note_frame() -> None:
    watchdog.note_frame()


def extended(seconds: float, reason: str = ''):
    return watchdog.extended(seconds, reason)
