"""
Memory Cache

Handles in-memory caching with TTL support, size limits, and automatic cleanup.
"""

import time
import threading
import logging
from typing import Dict, Any, Optional


class MemoryCache:
    """Manages in-memory cache with TTL and size limits."""
    
    def __init__(self, max_size: int = 1000, cleanup_interval: float = 300.0) -> None:
        """
        Initialize memory cache.
        
        Args:
            max_size: Maximum number of entries in cache
            cleanup_interval: Seconds between automatic cleanups
        """
        self.logger = logging.getLogger(__name__)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._timestamps: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._max_size = max_size
        self._cleanup_interval = cleanup_interval
        self._last_cleanup = time.time()
    
    def get(self, key: str, max_age: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Get value from memory cache.
        
        Args:
            key: Cache key
            max_age: Maximum age in seconds (None = no expiration)
            
        Returns:
            Cached value or None if not found or expired
        """
        now = time.time()
        
        with self._lock:
            if key not in self._cache:
                return None
            
            timestamp = self._timestamps.get(key)
            if isinstance(timestamp, str):
                try:
                    timestamp = float(timestamp)
                except ValueError:
                    self.logger.error(f"Invalid timestamp format for key {key}: {timestamp}")
                    timestamp = None
            
            if timestamp is None:
                return None
            
            # Check expiration
            if max_age is not None and (now - timestamp) > max_age:
                # Expired - remove it
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
                return None
            
            return self._cache[key]
    
    def set(self, key: str, value: Dict[str, Any]) -> None:
        """
        Set value in memory cache.
        
        Args:
            key: Cache key
            value: Value to cache
        """
        with self._lock:
            self._cache[key] = value
            self._timestamps[key] = time.time()
    
    def clear(self, key: Optional[str] = None) -> None:
        """
        Clear cache entry or all entries.
        
        Args:
            key: Specific key to clear, or None to clear all
        """
        with self._lock:
            if key:
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
            else:
                self._cache.clear()
                self._timestamps.clear()
    
    def cleanup(self, force: bool = False) -> int:
        """
        Clean up expired entries and enforce size limits.
        
        Args:
            force: If True, perform cleanup regardless of time interval
            
        Returns:
            Number of entries removed
        """
        now = time.time()
        
        # Check if cleanup is needed
        if not force and (now - self._last_cleanup) < self._cleanup_interval:
            return 0
        
        with self._lock:
            removed_count = 0
            current_time = time.time()
            
            # Remove expired entries (entries older than 1 hour without access are considered expired)
            max_age_for_cleanup = 3600  # 1 hour
            
            expired_keys = []
            for key, timestamp in list(self._timestamps.items()):
                if isinstance(timestamp, str):
                    try:
                        timestamp = float(timestamp)
                    except ValueError:
                        timestamp = None
                
                if timestamp is None or (current_time - timestamp) > max_age_for_cleanup:
                    expired_keys.append(key)
            
            # Remove expired entries
            for key in expired_keys:
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
                removed_count += 1
            
            # Enforce size limit by removing oldest entries if cache is too large
            if len(self._cache) > self._max_size:
                # Sort by timestamp (oldest first)
                sorted_entries = sorted(
                    self._timestamps.items(),
                    key=lambda x: float(x[1]) if isinstance(x[1], (int, float)) else 0
                )
                
                # Remove oldest entries until we're under the limit
                excess_count = len(self._cache) - self._max_size
                for i in range(excess_count):
                    if i < len(sorted_entries):
                        key = sorted_entries[i][0]
                        self._cache.pop(key, None)
                        self._timestamps.pop(key, None)
                        removed_count += 1
            
            self._last_cleanup = current_time
            
            if removed_count > 0:
                self.logger.debug("Memory cache cleanup: removed %d entries (current size: %d)", 
                                removed_count, len(self._cache))
            
            return removed_count
    
    def size(self) -> int:
        """Get current cache size."""
        with self._lock:
            return len(self._cache)
    
    def max_size(self) -> int:
        """Get maximum cache size."""
        return self._max_size
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        with self._lock:
            return {
                'size': len(self._cache),
                'max_size': self._max_size,
                'usage_percent': (len(self._cache) / self._max_size * 100) if self._max_size > 0 else 0,
                'last_cleanup': self._last_cleanup,
                'cleanup_interval': self._cleanup_interval
            }

