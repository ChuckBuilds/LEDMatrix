"""
Disk Cache

Handles persistent disk-based caching with atomic writes and error recovery.
"""

import json
import os
import time
import tempfile
import logging
import threading
from typing import Dict, Any, Optional
from datetime import datetime

from src.exceptions import CacheError


class DateTimeEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime objects."""
    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class DiskCache:
    """Manages persistent disk-based cache."""
    
    def __init__(self, cache_dir: Optional[str], logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize disk cache.
        
        Args:
            cache_dir: Directory for cache files (None = disabled)
            logger: Optional logger instance
        """
        self.cache_dir = cache_dir
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
    
    def get_cache_path(self, key: str) -> Optional[str]:
        """
        Get the path for a cache file.
        
        Args:
            key: Cache key
            
        Returns:
            Path to cache file or None if cache is disabled
        """
        if not self.cache_dir:
            return None
        return os.path.join(self.cache_dir, f"{key}.json")
    
    def get(self, key: str, max_age: int = 300) -> Optional[Dict[str, Any]]:
        """
        Get data from disk cache.
        
        Args:
            key: Cache key
            max_age: Maximum age in seconds
            
        Returns:
            Cached data or None if not found or expired
        """
        cache_path = self.get_cache_path(key)
        if not cache_path or not os.path.exists(cache_path):
            return None
        
        try:
            with self._lock:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    record = json.load(f)
            
            # Determine record timestamp (prefer embedded, else file mtime)
            record_ts = None
            if isinstance(record, dict):
                record_ts = record.get('timestamp')
            if record_ts is None:
                try:
                    record_ts = os.path.getmtime(cache_path)
                except OSError:
                    record_ts = None
            
            if record_ts is not None:
                try:
                    record_ts = float(record_ts)
                except (TypeError, ValueError):
                    record_ts = None
            
            now = time.time()
            if record_ts is None or (now - record_ts) <= max_age:
                return record
            else:
                # Stale on disk; keep file for potential diagnostics but treat as miss
                return None
                
        except json.JSONDecodeError as e:
            self.logger.error("Error parsing cache file for %s at %s: %s", key, cache_path, e, exc_info=True)
            # If the file is corrupted, remove it
            try:
                os.remove(cache_path)
                self.logger.info("Removed corrupted cache file: %s", cache_path)
            except OSError as remove_error:
                self.logger.warning("Could not remove corrupted cache file %s: %s", cache_path, remove_error)
            return None
        except PermissionError as e:
            # Permission errors are recoverable - cache just won't be available
            self.logger.warning("Permission denied loading cache for %s from %s: %s. Cache unavailable for this key.", key, cache_path, e)
            return None
        except (IOError, OSError) as e:
            self.logger.error("Error loading cache for %s from %s: %s", key, cache_path, e, exc_info=True)
            return None
        except Exception as e:
            self.logger.error("Unexpected error loading cache for %s from %s: %s", key, cache_path, e, exc_info=True)
            return None
    
    def set(self, key: str, data: Dict[str, Any]) -> None:
        """
        Save data to disk cache with atomic write.
        
        This method gracefully handles permission errors. If the cache directory
        is not writable, it will log a warning and return silently rather than
        raising an exception. This allows the application to continue functioning
        even when running as a non-root user without write access to system cache
        directories.
        
        Args:
            key: Cache key
            data: Data to cache
        """
        cache_path = self.get_cache_path(key)
        if not cache_path:
            return
        
        try:
            # Atomic write to avoid partial/corrupt files
            with self._lock:
                tmp_dir = os.path.dirname(cache_path)
                # Try to create temp file in cache directory first
                # If that fails due to permissions, fall back to direct write
                tmp_path = None
                fd = None
                try:
                    # First try the cache directory
                    if os.access(tmp_dir, os.W_OK):
                        try:
                            fd, tmp_path = tempfile.mkstemp(prefix=f".{os.path.basename(cache_path)}.", dir=tmp_dir)
                        except (IOError, OSError, PermissionError):
                            # If temp file creation fails, try direct write as fallback
                            self.logger.warning("Could not create temp file in %s, using direct write for %s", tmp_dir, key)
                            tmp_path = None
                            fd = None
                    else:
                        # Directory not writable, use direct write
                        self.logger.warning("Cache directory %s not writable, using direct write for %s", tmp_dir, key)
                        tmp_path = None
                        fd = None
                    
                    if tmp_path and fd is not None:
                        # Use atomic write with temp file
                        try:
                            with os.fdopen(fd, 'w', encoding='utf-8') as tmp_file:
                                json.dump(data, tmp_file, indent=4, cls=DateTimeEncoder)
                                tmp_file.flush()
                                os.fsync(tmp_file.fileno())
                            os.replace(tmp_path, cache_path)
                        finally:
                            if os.path.exists(tmp_path):
                                try:
                                    os.remove(tmp_path)
                                except OSError:
                                    pass
                    else:
                        # Fallback: direct write (not atomic, but better than failing)
                        try:
                            with open(cache_path, 'w', encoding='utf-8') as cache_file:
                                json.dump(data, cache_file, indent=4, cls=DateTimeEncoder)
                                cache_file.flush()
                                os.fsync(cache_file.fileno())
                            self.logger.debug("Wrote cache for %s directly (non-atomic)", key)
                        except (IOError, OSError, PermissionError) as write_error:
                            # If direct write also fails, try fallback location
                            self.logger.warning("Direct write failed for key '%s' to %s: %s", key, cache_path, write_error)
                            raise  # Re-raise to trigger fallback logic
                except (IOError, OSError, PermissionError) as e:
                    # Attempt one-time fallback write to user's home cache directory
                    try:
                        # Try user's home cache directory as fallback
                        home_dir = os.path.expanduser('~')
                        fallback_dir = os.path.join(home_dir, '.ledmatrix_cache')
                        # Ensure fallback directory exists
                        try:
                            os.makedirs(fallback_dir, exist_ok=True)
                        except (OSError, PermissionError):
                            pass
                        
                        if os.path.isdir(fallback_dir) and os.access(fallback_dir, os.W_OK):
                            fallback_path = os.path.join(fallback_dir, os.path.basename(cache_path))
                            with open(fallback_path, 'w', encoding='utf-8') as tmp_file:
                                json.dump(data, tmp_file, indent=4, cls=DateTimeEncoder)
                            self.logger.debug("Cache wrote to fallback location: %s", fallback_path)
                            return  # Successfully wrote to fallback, exit gracefully
                    except (IOError, OSError, PermissionError) as e2:
                        self.logger.debug("Fallback cache write also failed for key '%s': %s", key, e2)
                    
                    # If all write attempts failed, log warning but don't raise exception
                    # Cache is a performance optimization, not critical for operation
                    self.logger.warning(
                        "Could not write cache for key '%s' to %s (permission denied). "
                        "Cache will be unavailable for this key, but application will continue.",
                        key, cache_path
                    )
                    return  # Exit gracefully without raising exception
        
        except Exception as e:
            # For any other unexpected errors, log but don't crash
            self.logger.warning(
                "Unexpected error saving cache for key '%s' to %s: %s. "
                "Application will continue without caching for this key.",
                key, cache_path, e, exc_info=True
            )
            return  # Exit gracefully without raising exception
    
    def clear(self, key: Optional[str] = None) -> None:
        """
        Clear cache entry or all entries.
        
        Args:
            key: Specific key to clear, or None to clear all
        """
        if not self.cache_dir:
            return
        
        with self._lock:
            if key:
                cache_path = self.get_cache_path(key)
                if cache_path and os.path.exists(cache_path):
                    try:
                        os.remove(cache_path)
                    except OSError as e:
                        self.logger.warning("Could not remove cache file %s: %s", cache_path, e)
            else:
                # Clear all cache files
                if os.path.exists(self.cache_dir):
                    for filename in os.listdir(self.cache_dir):
                        if filename.endswith('.json'):
                            try:
                                os.remove(os.path.join(self.cache_dir, filename))
                            except OSError as e:
                                self.logger.warning("Could not remove cache file %s: %s", filename, e)
    
    def get_cache_dir(self) -> Optional[str]:
        """Get the cache directory path."""
        return self.cache_dir

