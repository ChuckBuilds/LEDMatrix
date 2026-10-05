"""Keep glibc's malloc from holding on to memory the display has freed.

The display process allocates and frees PIL images and numpy buffers all day
from a dozen threads. glibc gives each allocating thread its own malloc arena
(up to 8 x CPU count) and returns little of what is freed inside them to the
OS, so resident memory climbs for hours while the live data stays flat. Two
in-process remedies, both standard library only (ctypes) and both no-ops off
Linux/glibc:

* :func:`cap_arenas` -- ``mallopt(M_ARENA_MAX, 2)``, the in-process twin of the
  unit's ``Environment=MALLOC_ARENA_MAX=2``. Units installed before that line
  existed never got it (systemd runs the copy in /etc/systemd/system), so the
  process applies it itself. Call it before any other thread starts: arenas
  already created stay. A ``MALLOC_ARENA_MAX`` set in the environment wins.
* :class:`MallocTrimmer` -- ``malloc_trim(0)`` at most every few minutes,
  called from the render loop between screens, where no frame is being drawn.
  glibc 2.8+ releases free pages from the middle of every arena, not only the
  top of the main heap.

Without glibc (macOS, Windows, musl, the dev server on any of them) nothing is
loaded and every call returns False.
"""
import ctypes
import logging
import os
import sys
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: glibc's mallopt() parameter number for the arena cap (malloc.h).
M_ARENA_MAX = -8

#: The arena cap applied when the environment does not set one; the same value
#: as the unit's ``MALLOC_ARENA_MAX``.
DEFAULT_ARENA_MAX = 2

#: Seconds between malloc_trim() calls. A trim takes about 1-20 ms on a Pi 4,
#: so this keeps it far from frame timing while still returning memory long
#: before it piles up.
TRIM_INTERVAL_SECONDS = 300.0

_UNLOADED = object()
_libc: Any = _UNLOADED


def _load_libc() -> Optional[Any]:
    """The process's C library if it is glibc with malloc_trim, else None."""
    global _libc
    if _libc is _UNLOADED:
        _libc = None
        if sys.platform.startswith('linux'):
            try:
                libc = ctypes.CDLL(None)
                # gnu_get_libc_version is glibc-only: musl also lacks
                # malloc_trim, but this says why without guessing.
                libc.gnu_get_libc_version
                libc.malloc_trim.argtypes = [ctypes.c_size_t]
                libc.malloc_trim.restype = ctypes.c_int
                libc.mallopt.argtypes = [ctypes.c_int, ctypes.c_int]
                libc.mallopt.restype = ctypes.c_int
                _libc = libc
            except (OSError, AttributeError, TypeError):
                logger.debug("glibc malloc controls unavailable", exc_info=True)
    return _libc


def cap_arenas(max_arenas: int = DEFAULT_ARENA_MAX) -> bool:
    """Cap glibc's malloc arenas at ``max_arenas``. True when the cap was set.

    Skipped when ``MALLOC_ARENA_MAX`` is in the environment: glibc has read it
    already, and an operator who set it chose that value.
    """
    if os.environ.get('MALLOC_ARENA_MAX'):
        return False
    libc = _load_libc()
    if libc is None:
        return False
    try:
        return bool(libc.mallopt(M_ARENA_MAX, int(max_arenas)))
    except Exception:  # pylint: disable=broad-except
        logger.debug("mallopt(M_ARENA_MAX) failed", exc_info=True)
        return False


class MallocTrimmer:
    """Calls ``malloc_trim(0)`` at most once per ``interval`` seconds.

    :meth:`maybe_trim` is meant for an idle point of the render loop; it costs
    one clock read when no trim is due. The first trim comes one interval
    after construction, so start-up's allocations have settled.
    """

    def __init__(self, interval: float = TRIM_INTERVAL_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._interval = interval
        self._clock = clock
        self._libc = _load_libc()
        self._next = clock() + interval

    @property
    def available(self) -> bool:
        return self._libc is not None

    def maybe_trim(self) -> bool:
        """Trim if one is due. True when malloc_trim ran and released memory."""
        if self._libc is None:
            return False
        now = self._clock()
        if now < self._next:
            return False
        self._next = now + self._interval
        try:
            released = bool(self._libc.malloc_trim(0))
        except Exception:  # pylint: disable=broad-except
            logger.debug("malloc_trim failed; not trying again", exc_info=True)
            self._libc = None
            return False
        logger.debug("malloc_trim(0) took %.1f ms, released=%s",
                     (self._clock() - now) * 1000.0, released)
        return released
