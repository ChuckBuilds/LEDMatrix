"""
Cache Manager — multi-tier response cache for the LEDMatrix application.

:class:`CacheManager` provides a unified caching layer used by all plugins
to reduce external API calls and survive network outages gracefully.

Two storage tiers
-----------------
* **Memory tier** (:class:`~src.cache.memory_cache.MemoryCache`): fast LRU
  cache (up to 1 000 entries by default).  Hit on this tier before touching
  disk.
* **Disk tier** (:class:`~src.cache.disk_cache.DiskCache`): filesystem-backed
  persistent store that survives process restarts.

Data written to cache is serialised as JSON.  :class:`DateTimeEncoder` handles
``datetime`` objects transparently so callers don't have to pre-serialise them.

Typical plugin usage::

    data = self.cache_manager.get_cached_data('my_key', max_age=300)
    if data is None:
        data = fetch_from_api()
        self.cache_manager.save_cache('my_key', data)
"""

import json
import os
import sys
import time
from datetime import datetime
import pytz
from typing import Any, Dict, List, Optional
import logging
import threading
import tempfile
from src.cache.memory_cache import MemoryCache, default_max_size
from src.cache.disk_cache import DiskCache
from src.cache.cache_strategy import CacheStrategy
from src.logging_config import get_logger

# Canonical implementation lives in src.cache.disk_cache; re-exported here
# because this module's docstring documents it and external code may import
# it from either path.
from src.cache.disk_cache import DateTimeEncoder  # noqa: F401 - deliberate re-export
from src.deprecation import deprecated

# CacheManager.config_manager not built yet (None means "not available").
_UNSET: Any = object()


def _outlived(record: Any, max_age: Optional[float], now: float) -> bool:
    """Whether a record's own timestamp puts it past max_age.

    The memory tier times an entry from when it was put there, and a record
    loaded from disk is put there when it is read, not when it was written: a
    record 290 s old, read after a restart, could be served for another
    max_age from memory. This is the age check DiskCache.get makes, with the
    same rule that a stored ttl wins over the caller's max_age. A record that
    carries no timestamp is left to the memory tier's own clock.
    """
    if not isinstance(record, dict):
        return False
    stored_ttl = record.get('ttl')
    if isinstance(stored_ttl, (int, float)) and not isinstance(stored_ttl, bool) \
            and stored_ttl >= 0:
        max_age = stored_ttl
    stamp = record.get('timestamp')
    if max_age is None or stamp is None or isinstance(stamp, bool):
        return False
    try:
        return now - float(stamp) > max_age
    except (TypeError, ValueError):
        return False


#: Cache keys that were file "mailboxes" from the web interface (and some
#: plugins) to the display. The control socket replaced them, and nothing
#: reads them any more, so a write is refused rather than left on the SD card
#: for nobody: see :func:`_refuse_retired_mailbox_write`.
RETIRED_MAILBOX_KEYS = frozenset({'display_on_demand_request', 'plugin_error_clear_request'})

#: (key, writer) pairs already warned about, so a plugin that writes on every
#: event logs once per process, not once per write.
_retired_writers_warned: set = set()
_retired_writers_lock = threading.Lock()


def _retired_mailbox_writer(data: Any) -> str:
    """Name whoever is writing a retired mailbox key, as well as can be told.

    The plugin instance on the call stack when there is one (a ``self`` with
    a string ``plugin_id`` and a ``cache_manager``: what BasePlugin gives
    every plugin), else the ``plugin_id`` the request itself names, else
    ``'unknown'``.
    """
    frame = sys._getframe(2)  # pylint: disable=protected-access
    depth = 0
    while frame is not None and depth < 25:
        owner = frame.f_locals.get('self')
        plugin_id = getattr(owner, 'plugin_id', None) if owner is not None else None
        if isinstance(plugin_id, str) and plugin_id and hasattr(owner, 'cache_manager'):
            return f"plugin '{plugin_id}'"
        frame = frame.f_back
        depth += 1
    payload = data.get('data', data) if isinstance(data, dict) else None
    named = payload.get('plugin_id') if isinstance(payload, dict) else None
    if isinstance(named, str) and named:
        return f"plugin '{named}' (named in the request)"
    return 'unknown'


def _refuse_retired_mailbox_write(logger: logging.Logger, key: str, data: Any) -> None:
    """Warn, once per writer, that a write to a retired mailbox key was dropped."""
    try:
        writer = _retired_mailbox_writer(data)
    except Exception:  # pylint: disable=broad-except
        writer = 'unknown'
    with _retired_writers_lock:
        if (key, writer) in _retired_writers_warned:
            return
        _retired_writers_warned.add((key, writer))
    if key == 'display_on_demand_request':
        hint = ("call self.request_on_demand() / self.end_on_demand() instead "
                "(BasePlugin, LEDMatrix 3.8.1 and later)")
    else:
        hint = "clear errors through POST /api/v3/errors/clear instead"
    logger.warning("Ignored a write to the retired '%s' cache key by %s: the display no "
                   "longer reads this file mailbox. Update it to %s. (Logged once per writer.)",
                   key, writer, hint)


class CacheManager:
    """Manages caching of API responses to reduce API calls."""

    # Which cache directories already have a cleanup thread in this process.
    #
    # The sweep is directory-scoped work -- it lists a directory and deletes
    # from it -- so one per directory is the right number no matter how many
    # managers exist. Nothing enforced that before: every instance started its
    # own, and because the loop closes over `self`, a discarded manager could
    # never be collected and its thread woke to re-scan the same directory
    # every 24 hours for the life of the process. Startup validation runs
    # twice and built a throwaway manager each time, so a display process
    # carried three threads for one cache.
    _cleanup_owners: Dict[str, 'CacheManager'] = {}
    _cleanup_owners_lock = threading.Lock()


    def __init__(self) -> None:
        # Initialize logger first
        self.logger: logging.Logger = get_logger(__name__)
        
        # Determine the most reliable writable directory
        self.cache_dir: Optional[str] = self._get_writable_cache_dir()
        if self.cache_dir:
            self.logger.info(f"Using cache directory: {self.cache_dir}")
        else:
            # This is a critical failure, as caching is essential.
            self.logger.error("Could not find or create a writable cache directory. Caching will be disabled.")
            self.cache_dir = None

        # The config manager is built on first use of self.config_manager; see
        # the property. Nothing in the cache reads it any more.
        self._config_manager: Any = _UNSET
        self._config_manager_lock = threading.Lock()

        # Initialize cache components using composition
        self._memory_cache_component = MemoryCache(
            max_size=default_max_size(), cleanup_interval=300.0
        )
        self._disk_cache_component = DiskCache(cache_dir=self.cache_dir, logger=self.logger)
        self._strategy_component = CacheStrategy()
        
        # Disk cleanup configuration
        self._disk_cleanup_interval_hours = 24  # Run cleanup every 24 hours
        self._disk_cleanup_interval = 3600.0  # Minimum interval between cleanups (1 hour) for throttle
        self._last_disk_cleanup = 0.0  # Timestamp of last disk cleanup
        self._cleanup_thread: Optional[threading.Thread] = None
        self._cleanup_stop_event = threading.Event()  # Event to signal thread shutdown
        self._retention_policies = {
            'odds': 2,              # Odds data: 2 days (lines move frequently)
            'odds_live': 2,         # Live odds: 2 days
            'sports_live': 7,       # Live sports: 7 days
            'weather_current': 7,   # Current weather: 7 days
            'sports_recent': 7,     # Recent games: 7 days
            'news': 14,             # News: 14 days
            'sports_upcoming': 60,  # Upcoming games: 60 days (schedules stable)
            'sports_schedules': 60, # Schedules: 60 days
            'team_info': 60,        # Team info: 60 days
            'stocks': 14,           # Stock data: 14 days
            'crypto': 14,           # Crypto data: 14 days
            'default': 30           # Default: 30 days
        }
        
        # Start background cleanup thread only if disk caching is enabled
        if self.cache_dir:
            self.start_cleanup_thread()

    @property
    def config_manager(self) -> Optional[Any]:
        """A loaded ConfigManager, built the first time it is asked for.

        Every CacheManager used to build one and load the whole config in
        __init__, for a cache strategy that stopped reading it -- startup paid
        a config load (and the web interface another) per manager for nothing.
        It is still public: the sports plugins resolve the global timezone and
        display settings through ``cache_manager.config_manager``, and they get
        the same object they always did, on first access instead of at
        construction. None when ConfigManager cannot be imported, as before.
        Assigning replaces it, as assigning the attribute always did.
        """
        # getattr: a manager made with __new__ (some tests) has no slot yet.
        value = getattr(self, '_config_manager', _UNSET)
        if value is not _UNSET:
            return value
        lock = getattr(self, '_config_manager_lock', None) or threading.Lock()
        with lock:
            value = getattr(self, '_config_manager', _UNSET)
            if value is _UNSET:
                try:
                    from src.config_manager import ConfigManager
                except ImportError:
                    self.logger.warning("ConfigManager not available, using default cache intervals")
                    value = None
                else:
                    value = ConfigManager()
                    # Raises as it did from __init__; nothing is kept, so the
                    # next access tries again.
                    value.load_config()
                self._config_manager = value
        return value

    @config_manager.setter
    def config_manager(self, value: Optional[Any]) -> None:
        self._config_manager = value

    def _get_writable_cache_dir(self) -> Optional[str]:
        """Tries to find or create a writable cache directory, preferring a system path when available."""
        # Attempt 1: System-wide persistent cache directory (preferred for services)
        try:
            system_cache_dir = '/var/cache/ledmatrix'
            if os.path.exists(system_cache_dir):
                test_file = os.path.join(system_cache_dir, '.writetest')
                try:
                    with open(test_file, 'w') as f:
                        f.write('test')
                    os.remove(test_file)
                    self.logger.info(f"Using system cache directory: {system_cache_dir}")
                    return system_cache_dir
                except (IOError, OSError):
                    self.logger.debug(f"System cache directory exists but is not writable: {system_cache_dir}")
            else:
                from pathlib import Path
                from src.common.permission_utils import (
                    ensure_directory_permissions,
                    get_cache_dir_mode
                )
                try:
                    ensure_directory_permissions(Path(system_cache_dir), get_cache_dir_mode())
                    if os.access(system_cache_dir, os.W_OK):
                        self.logger.info(f"Using system cache directory: {system_cache_dir}")
                        return system_cache_dir
                except (OSError, IOError, PermissionError):
                    # Permission errors are expected when running as non-root
                    self.logger.debug(f"Could not create system cache directory (permission denied): {system_cache_dir}")
        except (OSError, IOError, PermissionError) as e:
            # Permission errors are expected when running as non-root, log at DEBUG level
            self.logger.debug(f"System cache directory not available: {e}")

        # Attempt 2: User's home directory (handling sudo), but avoid /root preference
        try:
            real_user = os.environ.get('SUDO_USER') or os.environ.get('USER', 'default')
            if real_user and real_user != 'root':
                home_dir = os.path.expanduser(f"~{real_user}")
            else:
                # When running as root and /var/cache/ledmatrix failed, still allow fallback to /root
                home_dir = os.path.expanduser('~')
            user_cache_dir = os.path.join(home_dir, '.ledmatrix_cache')
            from pathlib import Path
            from src.common.permission_utils import (
                ensure_directory_permissions,
                get_cache_dir_mode
            )
            ensure_directory_permissions(Path(user_cache_dir), get_cache_dir_mode())
            test_file = os.path.join(user_cache_dir, '.writetest')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            self.logger.info(f"Using user cache directory: {user_cache_dir}")
            return user_cache_dir
        except (OSError, IOError, PermissionError) as e:
            self.logger.warning(f"Could not use user-specific cache directory: {e}")

        # Attempt 3: /opt/ledmatrix/cache (alternative persistent location)
        try:
            opt_cache_dir = '/opt/ledmatrix/cache'
            
            # Check if directory exists and we can write to it
            if os.path.exists(opt_cache_dir):
                # Test if we can write to the existing directory
                test_file = os.path.join(opt_cache_dir, '.writetest')
                try:
                    with open(test_file, 'w') as f:
                        f.write('test')
                    os.remove(test_file)
                    return opt_cache_dir
                except (IOError, OSError):
                    self.logger.warning(f"Directory exists but is not writable: {opt_cache_dir}")
            else:
                # Try to create the directory
                from pathlib import Path
                from src.common.permission_utils import (
                    ensure_directory_permissions,
                    get_cache_dir_mode
                )
                ensure_directory_permissions(Path(opt_cache_dir), get_cache_dir_mode())
                if os.access(opt_cache_dir, os.W_OK):
                    return opt_cache_dir
        except (OSError, IOError, PermissionError) as e:
            self.logger.warning(f"Could not use /opt/ledmatrix/cache: {e}", exc_info=True)

        # Attempt 4: System-wide temporary directory (fallback, not persistent)
        try:
            temp_cache_dir = os.path.join(tempfile.gettempdir(), 'ledmatrix_cache')
            from pathlib import Path
            from src.common.permission_utils import (
                ensure_directory_permissions,
                get_cache_dir_mode
            )
            ensure_directory_permissions(Path(temp_cache_dir), get_cache_dir_mode())
            if os.access(temp_cache_dir, os.W_OK):
                self.logger.warning("Using temporary cache directory - cache will NOT persist across restarts")
                return temp_cache_dir
        except (OSError, IOError, PermissionError) as e:
            self.logger.warning(f"Could not use system-wide temporary cache directory: {e}", exc_info=True)

        # Return None if no directory is writable
        return None
    
    def _cleanup_memory_cache(self, force: bool = False) -> int:
        """Sweep the memory tier: drop entries older than an hour and trim it
        to its size ceiling, at most once per cleanup interval unless forced.

        Returns:
            Number of entries removed
        """
        return self._memory_cache_component.cleanup(force=force)

    def _get_cache_path(self, key: str) -> Optional[str]:
        """Get the path for a cache file."""
        return self._disk_cache_component.get_cache_path(key)

    def get_cached_data(self, key: str, max_age: int = 300, memory_ttl: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Get data from cache (memory first, then disk) honoring TTLs.

        - memory_ttl: TTL for in-memory entry; defaults to max_age if not provided
        - max_age: TTL for persisted (on-disk) entry based on the stored timestamp
        """
        # Periodic cleanup of memory cache
        self._cleanup_memory_cache()
        
        in_memory_ttl = memory_ttl if memory_ttl is not None else max_age

        # 1) Memory cache
        cached = self._memory_cache_component.get(key, max_age=in_memory_ttl)
        if cached is not None:
            if not _outlived(cached, max_age, time.time()):
                return cached
            # Too old for this reader. Disk may hold a newer write (from the
            # other process), and if it does not, the miss is the right answer.
            self._memory_cache_component.clear(key)

        # 2) Disk cache
        record = self._disk_cache_component.get(key, max_age=max_age)
        if record is not None:
            # Hydrate memory cache (use current time to start memory TTL window)
            self._memory_cache_component.set(key, record)
            return record

        # 3) Miss
        return None
            
    def save_cache(self, key: str, data: Dict[str, Any]) -> None:
        """
        Save data to cache.
        Args:
            key: Cache key
            data: Data to cache
        """
        if key in RETIRED_MAILBOX_KEYS:
            _refuse_retired_mailbox_write(self.logger, key, data)
            return

        # Periodic cleanup before adding new entries
        self._cleanup_memory_cache()
        
        # Update memory cache first
        self._memory_cache_component.set(key, data)
        
        # DiskCache logs a failed write and raises CacheError, which the
        # caller gets as is.
        self._disk_cache_component.set(key, data)

    @deprecated("3.10.0", "use get(key, max_age=3600)")
    def load_cache(self, key: str) -> Optional[Dict[str, Any]]:
        """Load data from cache with memory caching."""
        # Check memory cache first (1 minute TTL)
        cached = self._memory_cache_component.get(key, max_age=60)
        if cached is not None:
            if not _outlived(cached, 3600, time.time()):
                return cached
            self._memory_cache_component.clear(key)
        
        # Check disk cache
        data = self._disk_cache_component.get(key, max_age=3600)  # 1 hour for load_cache
        if data is not None:
            # Update memory cache
            self._memory_cache_component.set(key, data)
            return data
        
        return None

    def clear_cache(self, key: Optional[str] = None) -> None:
        """Clear cache entries.

        Pass a non-empty ``key`` to remove a single entry, or pass
        ``None`` (the default) to clear every cached entry. An empty
        string is rejected to prevent accidental whole-cache wipes
        from callers that pass through unvalidated input.
        """
        if key is None:
            # Clear all keys
            memory_count = self._memory_cache_component.size()
            self._memory_cache_component.clear()
            self._disk_cache_component.clear()
            self.logger.info("Cleared all cache: %d memory entries", memory_count)
            return

        if not isinstance(key, str) or not key:
            raise ValueError(
                "clear_cache(key) requires a non-empty string; "
                "pass key=None to clear all entries"
            )

        # Clear specific key
        self._memory_cache_component.clear(key)
        self._disk_cache_component.clear(key)
        self.logger.info("Cleared cache for key: %s", key)

    def delete(self, key: str) -> None:
        """Remove a single cache entry.

        Thin wrapper around :meth:`clear_cache` that **requires** a
        non-empty string key — unlike ``clear_cache(None)`` it never
        wipes every entry. Raises ``ValueError`` on ``None`` or an
        empty string.
        """
        if key is None or not isinstance(key, str) or not key:
            raise ValueError("delete(key) requires a non-empty string key")
        self.clear_cache(key)

    def list_cache_files(self) -> List[Dict[str, Any]]:
        """List all cache files with metadata (key, age, size, path).
        
        Returns:
            List of dicts with keys: 'key', 'filename', 'age_seconds', 'age_display', 
            'size_bytes', 'size_display', 'path', 'modified_time'
        """
        if not self.cache_dir or not os.path.exists(self.cache_dir):
            return []
        
        cache_files = []
        current_time = time.time()
        
        try:
            # No lock: this is disk-only work, and the memory-tier lock it used
            # to hold would stall every get/set while thousands of files are
            # stat'd. A file deleted mid-scan is skipped below.
            for filename in os.listdir(self.cache_dir):
                if not filename.endswith('.json'):
                    continue
                
                # Extract key from filename (remove .json extension)
                key = filename[:-5]  # Remove '.json'
                
                file_path = os.path.join(self.cache_dir, filename)
                
                try:
                    # Get file stats
                    stat_info = os.stat(file_path)
                    size_bytes = stat_info.st_size
                    modified_time = stat_info.st_mtime
                    age_seconds = current_time - modified_time
                    
                    # Format age display
                    if age_seconds < 60:
                        age_display = f"{int(age_seconds)}s"
                    elif age_seconds < 3600:
                        age_display = f"{int(age_seconds / 60)}m"
                    elif age_seconds < 86400:
                        age_display = f"{int(age_seconds / 3600)}h"
                    else:
                        age_display = f"{int(age_seconds / 86400)}d"
                    
                    # Format size display
                    if size_bytes < 1024:
                        size_display = f"{size_bytes}B"
                    elif size_bytes < 1024 * 1024:
                        size_display = f"{size_bytes / 1024:.1f}KB"
                    else:
                        size_display = f"{size_bytes / (1024 * 1024):.1f}MB"
                    
                    cache_files.append({
                        'key': key,
                        'filename': filename,
                        'age_seconds': age_seconds,
                        'age_display': age_display,
                        'size_bytes': size_bytes,
                        'size_display': size_display,
                        'path': file_path,
                        'modified_time': modified_time,
                        'modified_datetime': datetime.fromtimestamp(modified_time).isoformat()
                    })
                except OSError as e:
                    self.logger.warning(f"Error getting stats for cache file {filename} at {file_path}: {e}", exc_info=True)
                    continue
                    
        except OSError as e:
            self.logger.error(f"Error listing cache directory {self.cache_dir}: {e}", exc_info=True)
            return []
        
        # Sort by modified time (newest first)
        cache_files.sort(key=lambda x: x['modified_time'], reverse=True)
        return cache_files

    def get_cache_dir(self) -> Optional[str]:
        """Get the cache directory path."""
        return self.cache_dir

    def get(self, key: str, max_age: Optional[int] = 300,
            memory_ttl: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Get data from cache if it exists and is not stale.

        Args:
            key: Cache key
            max_age: Max age (seconds) for the on-disk entry; None never expires.
            memory_ttl: Max age (seconds) for the in-memory entry. Pass 0 to
                bypass the memory tier and force a fresh read from disk — used by
                cross-process readers that must observe another process's latest
                write rather than a stale first snapshot. Defaults to max_age.
        """
        cached_data = self.get_cached_data(key, max_age, memory_ttl=memory_ttl)
        if cached_data and 'data' in cached_data:
            return cached_data['data']
        return cached_data

    def set(self, key: str, data: Dict[str, Any], ttl: Optional[int] = None) -> None:
        """
        Store data in cache with current timestamp.
        
        Args:
            key: Cache key
            data: Data to cache
            ttl: Time-to-live in seconds for this entry. Takes precedence over
                 the max_age a reader would otherwise apply, which is inferred
                 from the key and is only a fallback for entries that did not
                 say. Omit it to keep that inferred behaviour.
        """
        # timestamp and ttl before data, so they are the first bytes on disk:
        # DiskCache.get reads them from the head of the file and can call a
        # record stale without parsing it. That matters for the big ones -- a
        # whole MLB season is 53MB and ~1.8s of orjson.loads with the GIL held,
        # paid in full only to learn the record had expired.
        cache_data: Dict[str, Any] = {'timestamp': time.time()}
        if ttl is not None:
            cache_data['ttl'] = ttl
        cache_data['data'] = data
        self.save_cache(key, cache_data)

    def cleanup_disk_cache(self, force: bool = False) -> Dict[str, Any]:
        """
        Clean up expired disk cache files based on retention policies.
        
        Args:
            force: If True, run cleanup regardless of last cleanup time
            
        Returns:
            Dictionary with cleanup statistics
        """
        now = time.time()
        
        # Check if cleanup is needed (throttle to prevent too-frequent cleanups)
        if not force and (now - self._last_disk_cleanup) < self._disk_cleanup_interval:
            return {
                'files_scanned': 0,
                'files_deleted': 0,
                'space_freed_mb': 0.0,
                'errors': 0,
                'duration_sec': 0.0
            }
        
        start_time = time.time()
        
        try:
            # Perform cleanup
            stats = self._disk_cache_component.cleanup_expired_files(
                cache_strategy=self._strategy_component,
                retention_policies=self._retention_policies
            )
            
            duration = time.time() - start_time
            space_freed_mb = stats['space_freed_bytes'] / (1024 * 1024)
            
            # Log summary
            if stats['files_deleted'] > 0:
                self.logger.info(
                    "Disk cache cleanup completed: %d/%d files deleted, %.2f MB freed, %d errors, took %.2fs",
                    stats['files_deleted'], stats['files_scanned'], space_freed_mb, 
                    stats['errors'], duration
                )
            else:
                self.logger.debug(
                    "Disk cache cleanup completed: no files to delete (%d files scanned)",
                    stats['files_scanned']
                )
            
            # Update last cleanup time
            self._last_disk_cleanup = time.time()
            
            return {
                'files_scanned': stats['files_scanned'],
                'files_deleted': stats['files_deleted'],
                'space_freed_mb': space_freed_mb,
                'errors': stats['errors'],
                'duration_sec': duration
            }
            
        except Exception as e:
            self.logger.error("Error during disk cache cleanup: %s", e, exc_info=True)
            return {
                'files_scanned': 0,
                'files_deleted': 0,
                'space_freed_mb': 0.0,
                'errors': 1,
                'duration_sec': time.time() - start_time
            }
    
    def start_cleanup_thread(self) -> None:
        """Start background thread for periodic disk cache cleanup.

        At most one thread per cache directory per process: the sweep is
        directory-scoped, so a second one only duplicates the scan.
        """
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self.logger.debug("Cleanup thread already running")
            return

        with CacheManager._cleanup_owners_lock:
            owner = CacheManager._cleanup_owners.get(self.cache_dir)
            if owner is not None and owner is not self:
                thread = owner._cleanup_thread
                if thread is not None and thread.is_alive():
                    self.logger.debug(
                        "Cleanup thread for %s already owned by another cache "
                        "manager in this process; not starting a second",
                        self.cache_dir)
                    return
                # The owner's thread died or was stopped -- take over.
            CacheManager._cleanup_owners[self.cache_dir] = self


        def cleanup_loop():
            """Background loop that runs cleanup periodically."""
            self.logger.info("Disk cache cleanup thread started (interval: %d hours)", 
                           self._disk_cleanup_interval_hours)
            
            # Repair files an older version wrote unreadable by the web
            # interface (see disk_cache.py, "SHARING CACHE FILES"). Once per
            # directory per process, which is what this thread already is.
            try:
                self._disk_cache_component.share_existing_files()
            except Exception as e:
                self.logger.error("Error sharing existing cache files: %s", e, exc_info=True)

            # Run initial cleanup on startup (deferred from __init__ to avoid blocking)
            try:
                self.logger.debug("Running initial disk cache cleanup")
                self.cleanup_disk_cache()
            except Exception as e:
                self.logger.error("Error in initial cleanup: %s", e, exc_info=True)
            
            # Main cleanup loop
            while not self._cleanup_stop_event.is_set():
                try:
                    # Sleep for the configured interval (interruptible)
                    sleep_seconds = self._disk_cleanup_interval_hours * 3600
                    if self._cleanup_stop_event.wait(timeout=sleep_seconds):
                        # Event was set, exit loop
                        break
                    
                    # Run cleanup if not stopped
                    if not self._cleanup_stop_event.is_set():
                        self.logger.debug("Running scheduled disk cache cleanup")
                        self.cleanup_disk_cache()
                    
                except Exception as e:
                    self.logger.error("Error in cleanup thread: %s", e, exc_info=True)
                    # Continue running despite errors, but use interruptible sleep
                    if self._cleanup_stop_event.wait(timeout=60):
                        # Event was set during error recovery sleep, exit loop
                        break
            
            self.logger.info("Disk cache cleanup thread stopped")
        
        self._cleanup_stop_event.clear()  # Reset event before starting thread
        self._cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True, name="DiskCacheCleanup")
        self._cleanup_thread.start()
        self.logger.info("Started disk cache cleanup background thread")
    
    def stop_cleanup_thread(self) -> None:
        """
        Stop the background cleanup thread gracefully.
        
        Signals the thread to stop and waits for it to finish (with timeout).
        This allows for clean shutdown during testing or application termination.
        """
        # Release ownership first and unconditionally, so a manager that never
        # started a thread (or whose thread already exited) cannot keep the
        # directory claimed and block a live manager from sweeping it.
        with CacheManager._cleanup_owners_lock:
            if CacheManager._cleanup_owners.get(self.cache_dir) is self:
                del CacheManager._cleanup_owners[self.cache_dir]

        if not self._cleanup_thread or not self._cleanup_thread.is_alive():
            self.logger.debug("Cleanup thread not running")
            return

        self.logger.info("Stopping disk cache cleanup thread...")
        self._cleanup_stop_event.set()  # Signal thread to stop
        
        # Wait for thread to finish (with timeout to avoid hanging)
        self._cleanup_thread.join(timeout=5.0)
        
        if self._cleanup_thread.is_alive():
            self.logger.warning("Cleanup thread did not stop within timeout, thread may still be running")
        else:
            self.logger.info("Disk cache cleanup thread stopped successfully") 

    def get_cache_strategy(self, data_type: str, sport_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Get cache strategy for different data types.
        Now respects sport-specific live_update_interval configurations.
        """
        return self._strategy_component.get_cache_strategy(data_type, sport_key)

    def get_data_type_from_key(self, key: str) -> str:
        """
        Determine the appropriate cache strategy based on the cache key.
        This helps automatically select the right cache duration.
        """
        return self._strategy_component.get_data_type_from_key(key)

    def get_cached_data_with_strategy(self, key: str, data_type: str = 'default') -> Optional[Dict[str, Any]]:
        """
        Get data from cache using data-type-specific strategy.
        Now respects sport-specific live_update_interval configurations.
        """
        # Extract sport key for live sports data
        sport_key = None
        if data_type in ['sports_live', 'live_scores']:
            sport_key = self._strategy_component.get_sport_key_from_cache_key(key)
        
        strategy = self._strategy_component.get_cache_strategy(data_type, sport_key)
        max_age = strategy['max_age']
        memory_ttl = strategy.get('memory_ttl', max_age)
        
        # For market data, check if market is open
        if strategy.get('market_hours_only', False) and not self._strategy_component.is_market_open():
            # During off-hours, extend cache duration
            max_age *= 4  # 4x longer cache during off-hours
        
        record = self.get_cached_data(key, max_age, memory_ttl)
        # Unwrap if stored in { 'data': ..., 'timestamp': ... }
        if isinstance(record, dict) and 'data' in record:
            return record['data']
        return record

    def get_with_auto_strategy(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get cached data using automatically determined strategy.
        Now respects sport-specific live_update_interval configurations.
        """
        data_type = self.get_data_type_from_key(key)
        return self.get_cached_data_with_strategy(key, data_type)

    @deprecated("3.10.0")
    def generate_sport_cache_key(self, sport: str, date_str: Optional[str] = None) -> str:
        """
        Centralized cache key generation for sports data.
        This ensures consistent cache keys across background service and managers.
        
        Args:
            sport: Sport identifier (e.g., 'nba', 'nfl', 'ncaa_fb')
            date_str: Date string in YYYYMMDD format. If None, uses current UTC date.
            
        Returns:
            Cache key in format: {sport}_{date}
        """
        if date_str is None:
            date_str = datetime.now(pytz.utc).strftime('%Y%m%d')
        return f"{sport}_{date_str}"

    def log_memory_cache_stats(self) -> None:
        """Log current memory cache statistics."""
        stats = self._memory_cache_component.get_stats()
        self.logger.info(f"Memory Cache - Size: {stats['size']}/{stats['max_size']} "
                        f"({stats['usage_percent']:.1f}%), "
                        f"Last cleanup: {time.time() - stats['last_cleanup']:.1f}s ago")