"""
In-process TTL cache for the web interface.

The one place the web process memoises cheap-to-recompute values for a few
seconds or minutes (the font catalog, the system-status snapshot, systemctl
checks). It is per-process and in-memory only; data shared with the display
service goes through ``src.cache_manager.CacheManager`` instead.

Separate from app.py so the blueprints can import it without importing the
app; it imports nothing from the project, so they import it at module top.
"""
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


class TTLCache:
    """A small thread-safe key/value store whose entries expire.

    Each entry keeps the TTL it was stored with. A reader may additionally
    pass ``max_age`` to ask for something fresher than that; an entry is only
    returned while it is younger than both.

    Expired entries are not dropped on read: :meth:`peek` still returns them,
    which is what a "keep the last known answer if the refresh fails" caller
    needs. They are replaced by the next :meth:`set` of the same key, so this
    is meant for a small, fixed set of keys, not an unbounded key space.

    Ages are measured with ``time.monotonic`` so a wall-clock jump (NTP sync
    on a Pi that booted without an RTC) neither expires nor immortalises
    everything at once.
    """

    def __init__(self, default_ttl: float = 60,
                 clock: Callable[[], float] = time.monotonic):
        self._default_ttl = default_ttl
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (value, stored_at, ttl)
        self._entries: Dict[str, Tuple[Any, float, float]] = {}

    def get(self, key: str, default: Any = None,
            max_age: Optional[float] = None) -> Any:
        """The value for ``key`` if it is still fresh, else ``default``."""
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return default
        value, stored_at, ttl = entry
        age = self._clock() - stored_at
        if age >= ttl or (max_age is not None and age >= max_age):
            return default
        return value

    def peek(self, key: str, default: Any = None) -> Any:
        """The last value stored for ``key``, fresh or not."""
        with self._lock:
            entry = self._entries.get(key)
        return default if entry is None else entry[0]

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Store ``value`` for ``ttl`` seconds (the cache default if None)."""
        ttl = self._default_ttl if ttl is None else ttl
        with self._lock:
            self._entries[key] = (value, self._clock(), ttl)

    def delete(self, key: str) -> None:
        """Remove ``key`` if present."""
        with self._lock:
            self._entries.pop(key, None)

    def clear(self, pattern: Optional[str] = None) -> None:
        """Remove every entry, or only those whose key contains ``pattern``."""
        with self._lock:
            if pattern is None:
                self._entries.clear()
            else:
                for key in [k for k in self._entries if pattern in k]:
                    del self._entries[key]


# The shared cache behind the functional helpers the blueprints use.
_default_cache = TTLCache(default_ttl=60)


def get_cached(key: str, ttl_seconds: Optional[float] = None) -> Optional[Any]:
    """Get a value from the cache if it has not expired.

    The entry expires after the TTL it was stored with; ``ttl_seconds``, when
    given, is an extra upper bound on its age for this read.
    """
    return _default_cache.get(key, max_age=ttl_seconds)


def set_cached(key: str, value: Any, ttl_seconds: float = 60) -> None:
    """Store a value in the cache for ``ttl_seconds``."""
    _default_cache.set(key, value, ttl=ttl_seconds)


def delete_cached(key: str) -> None:
    """Remove a single key from the cache if present."""
    _default_cache.delete(key)


def invalidate_cache(pattern: Optional[str] = None) -> None:
    """Invalidate cache entries matching pattern, or all if pattern is None."""
    _default_cache.clear(pattern)
