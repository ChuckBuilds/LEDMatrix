"""Let a background thread run Python only while the render thread waits on vsync.

With plugin rendering moved to Vegas's prefetch thread (DisplayManager.offscreen,
#630) the render thread no longer stops for it, but it still shares the GIL
with it. The render thread spends most of each refresh inside SwapOnVSync,
which releases the GIL, and needs it back the moment the swap returns. If the
prefetch thread is running Python right then, the render thread waits: up to
the switch interval (5ms) behind bytecode, and for as long as a C call that
keeps the GIL takes. On hdpi that showed up as frames 2-5 refreshes late while
a group was being prepared.

The gate turns that around. The display manager opens it just before each swap,
with a deadline shortly ahead of the refresh the swap will return on, and
closes it when the swap returns. A thread inside ``gate.yielding()`` checks it on
every Python and C call through a profile hook, and once the window has closed
it parks -- blocked on a condition, GIL released -- until the next swap opens
it. The render thread then finds the GIL free when its refresh arrives, and the
background work runs in time the render thread was only spending waiting.

Parking a thread is only safe if nothing the render thread needs is stuck
behind it, so it is never parked:

* while it holds a lock registered with ``guard()`` (the Vegas buffers and
  caches the render thread also takes);
* inside logging, threading, importlib or the cache, all of which take locks the
  render thread can take too;
* when there is no render loop to protect -- no swap for ``STALE_SECONDS``, as
  on a static screen or a stalled frame.

And a parked thread is never held more than ``MAX_WAIT_SECONDS`` at a time, so
whatever the gate gets wrong costs a frame, not a freeze. The render thread
itself is never gated, whatever it calls.

The prefetch thread is not the only one that competes. Once an hour the sports
plugins refresh their schedules together, about twenty ESPN chunk-fetch threads
at once (hdpi, 2026-09-24), and the render thread queued behind all of them for
a 1.9s freeze. So Vegas also makes its gate the *active* one, and code that runs
background fetches wraps them in the module-level ``yielding()``, which uses the
active gate if there is one and does nothing otherwise: ``espn_dates``' chunk
fetches and the background data service's workers.

Only worth enabling on a binding whose SwapOnVSync releases the GIL (see
scripts/build_rgbmatrix_nogil.sh). With one that keeps it, the window never
lets the background thread run and it only makes progress in the timeouts.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from collections import deque
from contextlib import nullcontext
from typing import Any, Callable, ContextManager, Deque, List, Optional

#: Park background threads this long before the refresh a swap will return on,
#: so a short C call already under way has finished by then.
MARGIN_SECONDS = 0.002

#: The longest a background thread is parked in one go.
MAX_WAIT_SECONDS = 0.05

#: No swap for this long means there is no render loop running to protect.
STALE_SECONDS = 0.05

#: Swaps needed before the refresh period is trusted enough to open a window.
MIN_SAMPLES = 8

#: Parking inside any of these modules could hold a lock the render thread
#: takes: logging handler locks, Condition and Event internals, the module
#: import locks, and the disk and memory cache locks. Matched by module name,
#: not file path: a path can say "cache" or "logging" for reasons of its own --
#: a virtualenv under ~/.cache, or GitHub's /opt/hostedtoolcache, where every
#: stdlib frame would otherwise count and the gate would never park anything.
_UNSAFE_MODULES = frozenset({
    "logging", "threading", "importlib", "src.cache_manager", "src.cache",
})
_UNSAFE_PREFIXES = ("logging.", "importlib.", "_frozen_importlib", "src.cache.")


def _unsafe(frame: Any, base: Any) -> bool:
    """True if a frame above ``base`` comes from somewhere parking could deadlock.

    ``base`` is the frame that entered ``yielding()``; what lies below it (the
    thread's own bootstrap in threading.py) holds nothing.
    """
    while frame is not None and frame is not base:
        name = frame.f_globals.get("__name__") or ""
        if name in _UNSAFE_MODULES or name.startswith(_UNSAFE_PREFIXES):
            return True
        frame = frame.f_back
    return False


def swap_releases_gil() -> Optional[bool]:
    """Whether the loaded rgbmatrix binding releases the GIL, or None if none is loaded.

    The rebuilt binding links PyEval_SaveThread and the stock one never does.
    The same test as src.common.frame_timing.binding_releases_gil (#629); one
    of the two goes once both have landed.
    """
    module = sys.modules.get("rgbmatrix.core")
    path = getattr(module, "__file__", None)
    if not path:
        return None
    try:
        with open(path, "rb") as handle:
            return b"PyEval_SaveThread" in handle.read()
    except OSError:
        return None


def _held(lock: Any) -> bool:
    """Is ``lock`` held? RLocks report this thread's ownership; plain locks, anyone's."""
    is_owned = getattr(lock, "_is_owned", None)
    if is_owned is not None:
        return is_owned()
    return lock.locked()


class RenderGate:
    """Opened by the render thread around each swap; honoured by background threads."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._cond = threading.Condition()
        self._generation = 0
        self._open_until = 0.0
        self._last_return: Optional[float] = None
        self._periods: Deque[float] = deque(maxlen=64)
        self._period: Optional[float] = None
        self._guarded: List[Any] = []
        self._local = threading.local()
        self._render_ident: Optional[int] = None
        #: How often, and for how long in all, background threads were parked.
        self.parks = 0
        self.parked_seconds = 0.0

    def guard(self, *locks: Any) -> None:
        """Never park a thread while it holds (or, for a plain Lock, anyone holds) these."""
        self._guarded.extend(lock for lock in locks if lock is not None)

    # -- render thread -----------------------------------------------------

    def refresh_period(self) -> Optional[float]:
        """The panel's refresh period from recent swaps, or None until known.

        The 10th percentile of the gaps between swap returns, each divided by
        the hold: a late frame only ever lengthens a gap, so the low end is
        the panel's own period.
        """
        return self._period

    def before_swap(self, hold: int) -> None:
        """The render thread is about to block in SwapOnVSync: open the window."""
        hold = max(1, int(hold))
        period = self._period
        now = self.clock()
        last = self._last_return
        if period and last is not None and now - last < STALE_SECONDS:
            # The swap returns on the first refresh boundary after both the
            # current frame's hold is up and this frame has been handed over;
            # boundaries fall a whole period apart from the last return.
            refreshes = max(hold, math.ceil((now - last) / period))
            open_until = last + refreshes * period - MARGIN_SECONDS
        else:
            open_until = 0.0     # no rhythm to predict from: leave threads be
        with self._cond:
            self._open_until = open_until
            self._generation += 1
            self._cond.notify_all()

    def after_swap(self, hold: int) -> None:
        """The swap returned and the render thread needs the GIL: close the window."""
        now = self.clock()
        self._open_until = 0.0
        if self._render_ident is None:
            # The first thread to swap is the render loop. A plugin pushing a
            # live refresh from its update thread swaps too, but must not take
            # over its exemption.
            self._render_ident = threading.get_ident()
        last = self._last_return
        if last is not None and now - last < STALE_SECONDS:
            self._periods.append((now - last) / max(1, int(hold)))
            if len(self._periods) >= MIN_SAMPLES:
                ordered = sorted(self._periods)
                self._period = ordered[len(ordered) // 10]
        self._last_return = now

    # -- background threads ------------------------------------------------

    def _should_park(self, frame: Any, now: float) -> bool:
        if now < self._open_until:
            return False                 # inside the window
        last = self._last_return
        if last is None or now - last > STALE_SECONDS or self._period is None:
            return False                 # no render loop to protect
        for lock in self._guarded:
            if _held(lock):
                return False
        return not _unsafe(frame, getattr(self._local, "base", None))

    def _hook(self, frame: Any, _event: str, _arg: Any) -> None:
        now = self.clock()
        if not self._should_park(frame, now):
            return
        generation = self._generation
        with self._cond:
            self._cond.wait_for(lambda: self._generation != generation,
                                timeout=MAX_WAIT_SECONDS)
        self.parks += 1
        self.parked_seconds += self.clock() - now

    def yielding(self) -> "_Yielding":
        """``with gate.yielding():`` runs the block giving way to the render thread."""
        return _Yielding(self)


class _Yielding:
    """Installs a gate's profile hook on the thread for the length of a block."""

    def __init__(self, gate: RenderGate):
        self.gate = gate
        self._previous: Any = None
        self._previous_base: Any = None
        self._skipped = False

    def __enter__(self) -> RenderGate:
        gate = self.gate
        # pylint: disable=protected-access
        if threading.get_ident() == gate._render_ident:
            self._skipped = True        # parking the render thread parks the display
            return gate
        local = gate._local
        self._previous_base = getattr(local, "base", None)
        if self._previous_base is None:
            # Nested blocks keep the outermost frame, so everything the thread
            # entered since it first gave way is still checked for locks.
            local.base = sys._getframe(1)
        self._previous = sys.getprofile()
        sys.setprofile(gate._hook)
        return gate

    def __exit__(self, *_exc: Any) -> None:
        if self._skipped:
            return
        sys.setprofile(self._previous)
        self.gate._local.base = self._previous_base  # pylint: disable=protected-access


#: The gate of the Vegas run in progress, if any; see the module docstring.
_active: Optional[RenderGate] = None


def set_active(gate: Optional[RenderGate]) -> None:
    """Make ``gate`` the one ``yielding()`` uses (None: no gate, run freely)."""
    global _active  # pylint: disable=global-statement
    _active = gate


def active() -> Optional[RenderGate]:
    """The gate ``yielding()`` currently uses, if any."""
    return _active


def yielding() -> ContextManager[Any]:
    """``with render_gate.yielding():`` gives way to the render thread while a
    Vegas run has a gate, and does nothing otherwise. For background work that
    does not know whether Vegas is running."""
    gate = _active
    return gate.yielding() if gate is not None else nullcontext()
