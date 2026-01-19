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
from typing import Dict, Any, Optional, Protocol
from datetime import datetime

from src.exceptions import CacheError


class CacheStrategyProtocol(Protocol):
    """Protocol for cache strategy objects that categorize cache keys."""
    
    def get_data_type_from_key(self, key: str) -> str:
        """
        Determine the data type from a cache key.
        
        Args:
            key: Cache key
            
        Returns:
            Data type string for strategy lookup
        """
        ...


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
                            # Set proper permissions: 660 (rw-rw----) for group-readable cache files
                            try:
                                os.chmod(cache_path, 0o660)
                            except OSError:
                                pass  # Non-critical if chmod fails
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
                            # Set proper permissions: 660 (rw-rw----) for group-readable cache files
                            try:
                                os.chmod(cache_path, 0o660)
                            except OSError:
                                pass  # Non-critical if chmod fails
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
                            # Set proper permissions: 660 (rw-rw----) for group-readable cache files
                            try:
                                os.chmod(fallback_path, 0o660)
                            except OSError:
                                pass  # Non-critical if chmod fails
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
    
    def cleanup_expired_files(self, cache_strategy: CacheStrategyProtocol, retention_policies: Dict[str, int]) -> Dict[str, Any]:
        """
        Clean up expired cache files based on retention policies.
        
        Args:
            cache_strategy: Object implementing CacheStrategyProtocol for categorizing files
            retention_policies: Dict mapping data types to retention days
            
        Returns:
            Dictionary with cleanup statistics:
                - files_scanned: Total files checked
                - files_deleted: Files removed
                - space_freed_bytes: Bytes freed
                - errors: Number of errors encountered
        """
        if not self.cache_dir or not os.path.exists(self.cache_dir):
            self.logger.warning("Cache directory not available for cleanup")
            return {'files_scanned': 0, 'files_deleted': 0, 'space_freed_bytes': 0, 'errors': 0}
        
        stats = {
            'files_scanned': 0,
            'files_deleted': 0,
            'space_freed_bytes': 0,
            'errors': 0
        }
        
        current_time = time.time()
        
        try:
            # Collect files to process outside the lock to avoid blocking cache operations
            # Only hold lock during directory listing to get snapshot of files
            try:
                with self._lock:
                    # Get snapshot of files while holding lock briefly
                    filenames = [f for f in os.listdir(self.cache_dir) if f.endswith('.json')]
            except OSError as list_error:
                self.logger.error("Error listing cache directory %s: %s", self.cache_dir, list_error, exc_info=True)
                stats['errors'] += 1
                return stats
            
            # Process files outside the lock to avoid blocking get/set operations
            for filename in filenames:
                stats['files_scanned'] += 1
                file_path = os.path.join(self.cache_dir, filename)
                
                try:
                    # Get file age (outside lock - stat operations are generally atomic)
                    file_mtime = os.path.getmtime(file_path)
                    file_age_days = (current_time - file_mtime) / 86400  # Convert to days
                    
                    # Extract cache key from filename (remove .json extension)
                    cache_key = filename[:-5]
                    
                    # Determine data type and retention policy
                    data_type = cache_strategy.get_data_type_from_key(cache_key)
                    retention_days = retention_policies.get(data_type, retention_policies.get('default', 30))
                    
                    # Delete if older than retention period
                    # Only hold lock during actual file deletion to ensure atomicity
                    if file_age_days > retention_days:
                        try:
                            # Hold lock only during delete operation (get size and remove atomically)
                            with self._lock:
                                # Double-check file still exists (may have been deleted by another process)
                                if os.path.exists(file_path):
                                    try:
                                        file_size = os.path.getsize(file_path)
                                        os.remove(file_path)
                                        # Only increment stats if removal succeeded
                                        stats['files_deleted'] += 1
                                        stats['space_freed_bytes'] += file_size
                                        self.logger.debug(
                                            "Deleted expired cache file: %s (age: %.1f days, type: %s, retention: %d days)",
                                            filename, file_age_days, data_type, retention_days
                                        )
                                    except FileNotFoundError:
                                        # File was deleted by another process between exists check and remove
                                        # This is a benign race condition, silently continue
                                        pass
                                else:
                                    # File was deleted by another process before lock was acquired
                                    # This is a benign race condition, silently continue
                                    pass
                        except FileNotFoundError:
                            # File was already deleted by another process, skip it
                            # This is a benign race condition, silently continue
                            continue
                        except OSError as e:
                            # Other file system errors, log but don't fail the entire cleanup
                            stats['errors'] += 1
                            self.logger.warning("Error deleting cache file %s: %s", filename, e)
                            continue
                
                except FileNotFoundError:
                    # File was deleted by another process between listing and processing
                    # This is a benign race condition, silently continue
                    continue
                except OSError as e:
                    stats['errors'] += 1
                    self.logger.warning("Error processing cache file %s: %s", filename, e)
                    continue
                except Exception as e:
                    stats['errors'] += 1
                    self.logger.error("Unexpected error processing cache file %s: %s", filename, e, exc_info=True)
                    continue
        
        except OSError as e:
            self.logger.error("Error listing cache directory %s: %s", self.cache_dir, e, exc_info=True)
            stats['errors'] += 1
        
        return stats

