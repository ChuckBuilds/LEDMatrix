"""
Simple in-memory cache for expensive operations.
Separated from app.py to avoid circular import issues.
"""
import time
from typing import Any, Optional


# Simple in-memory cache for expensive operations
_cache = {}
_cache_timestamps = {}


def get_cached(key: str, ttl_seconds: int = 60) -> Optional[Any]:
    """Get value from cache if not expired."""
    if key in _cache:
        if time.time() - _cache_timestamps[key] < ttl_seconds:
            return _cache[key]
        else:
            # Expired, remove
            del _cache[key]
            del _cache_timestamps[key]
    return None


def set_cached(key: str, value: Any, ttl_seconds: int = 60) -> None:
    """Set value in cache with TTL."""
    _cache[key] = value
    _cache_timestamps[key] = time.time()


def invalidate_cache(pattern: Optional[str] = None) -> None:
    """Invalidate cache entries matching pattern, or all if pattern is None."""
    if pattern is None:
        _cache.clear()
        _cache_timestamps.clear()
    else:
        keys_to_remove = [k for k in _cache.keys() if pattern in k]
        for key in keys_to_remove:
            del _cache[key]
            del _cache_timestamps[key]

