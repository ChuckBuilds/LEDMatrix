import json
import os
import time
from datetime import datetime
import pytz
from typing import Any, Dict, List, Optional
import logging
import threading
import tempfile
from pathlib import Path
from src.exceptions import CacheError
from src.cache.memory_cache import MemoryCache
from src.cache.disk_cache import DiskCache
from src.cache.cache_strategy import CacheStrategy
from src.cache.cache_metrics import CacheMetrics
from src.logging_config import get_logger

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

class CacheManager:
    """Manages caching of API responses to reduce API calls."""
    
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

        # Initialize config manager for sport-specific intervals
        try:
            from src.config_manager import ConfigManager
            self.config_manager: Optional[Any] = ConfigManager()
            self.config_manager.load_config()
        except ImportError:
            self.config_manager: Optional[Any] = None
            self.logger.warning("ConfigManager not available, using default cache intervals")
        
        # Initialize cache components using composition
        self._memory_cache_component = MemoryCache(max_size=1000, cleanup_interval=300.0)
        self._disk_cache_component = DiskCache(cache_dir=self.cache_dir, logger=self.logger)
        self._strategy_component = CacheStrategy(config_manager=self.config_manager, logger=self.logger)
        self._metrics_component = CacheMetrics(logger=self.logger)
        
        # Keep old attributes for backward compatibility (delegated to components)
        self._memory_cache = self._memory_cache_component._cache
        self._memory_cache_timestamps = self._memory_cache_component._timestamps
        self._cache_lock = self._memory_cache_component._lock
        self._max_memory_cache_size = self._memory_cache_component._max_size
        self._memory_cache_cleanup_interval = self._memory_cache_component._cleanup_interval
        self._last_memory_cache_cleanup = self._memory_cache_component._last_cleanup

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
                    return system_cache_dir
                except (IOError, OSError):
                    self.logger.warning(f"Directory exists but is not writable: {system_cache_dir}")
            else:
                os.makedirs(system_cache_dir, exist_ok=True)
                if os.access(system_cache_dir, os.W_OK):
                    return system_cache_dir
        except (OSError, IOError, PermissionError) as e:
            self.logger.warning(f"Could not use /var/cache/ledmatrix: {e}", exc_info=True)

        # Attempt 2: User's home directory (handling sudo), but avoid /root preference
        try:
            real_user = os.environ.get('SUDO_USER') or os.environ.get('USER', 'default')
            if real_user and real_user != 'root':
                home_dir = os.path.expanduser(f"~{real_user}")
            else:
                # When running as root and /var/cache/ledmatrix failed, still allow fallback to /root
                home_dir = os.path.expanduser('~')
            user_cache_dir = os.path.join(home_dir, '.ledmatrix_cache')
            os.makedirs(user_cache_dir, exist_ok=True)
            test_file = os.path.join(user_cache_dir, '.writetest')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            return user_cache_dir
        except (OSError, IOError, PermissionError) as e:
            self.logger.warning(f"Could not use user-specific cache directory: {e}", exc_info=True)

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
                os.makedirs(opt_cache_dir, exist_ok=True)
                if os.access(opt_cache_dir, os.W_OK):
                    return opt_cache_dir
        except (OSError, IOError, PermissionError) as e:
            self.logger.warning(f"Could not use /opt/ledmatrix/cache: {e}", exc_info=True)

        # Attempt 4: System-wide temporary directory (fallback, not persistent)
        try:
            temp_cache_dir = os.path.join(tempfile.gettempdir(), 'ledmatrix_cache')
            os.makedirs(temp_cache_dir, exist_ok=True)
            if os.access(temp_cache_dir, os.W_OK):
                self.logger.warning("Using temporary cache directory - cache will NOT persist across restarts")
                return temp_cache_dir
        except (OSError, IOError, PermissionError) as e:
            self.logger.warning(f"Could not use system-wide temporary cache directory: {e}", exc_info=True)

        # Return None if no directory is writable
        return None
    
    def _cleanup_memory_cache(self, force: bool = False) -> int:
        """
        Clean up expired entries from memory cache and enforce size limits.
        
        Args:
            force: If True, perform cleanup regardless of time interval
            
        Returns:
            Number of entries removed
        """
        now = time.time()
        
        # Check if cleanup is needed
        if not force and (now - self._last_memory_cache_cleanup) < self._memory_cache_cleanup_interval:
            return 0
        
        with self._cache_lock:
            removed_count = 0
            current_time = time.time()
            
            # Remove expired entries (entries older than 1 hour without access are considered expired)
            # We use a conservative TTL of 1 hour for cleanup
            max_age_for_cleanup = 3600  # 1 hour
            
            expired_keys = []
            for key, timestamp in list(self._memory_cache_timestamps.items()):
                if isinstance(timestamp, str):
                    try:
                        timestamp = float(timestamp)
                    except ValueError:
                        timestamp = None
                
                if timestamp is None or (current_time - timestamp) > max_age_for_cleanup:
                    expired_keys.append(key)
            
            # Remove expired entries
            for key in expired_keys:
                self._memory_cache.pop(key, None)
                self._memory_cache_timestamps.pop(key, None)
                removed_count += 1
            
            # Enforce size limit by removing oldest entries if cache is too large
            if len(self._memory_cache) > self._max_memory_cache_size:
                # Sort by timestamp (oldest first)
                sorted_entries = sorted(
                    self._memory_cache_timestamps.items(),
                    key=lambda x: float(x[1]) if isinstance(x[1], (int, float)) else 0
                )
                
                # Remove oldest entries until we're under the limit
                excess_count = len(self._memory_cache) - self._max_memory_cache_size
                for i in range(excess_count):
                    if i < len(sorted_entries):
                        key = sorted_entries[i][0]
                        self._memory_cache.pop(key, None)
                        self._memory_cache_timestamps.pop(key, None)
                        removed_count += 1
            
            self._last_memory_cache_cleanup = current_time
            
            if removed_count > 0:
                self.logger.debug(f"Memory cache cleanup: removed {removed_count} entries (current size: {len(self._memory_cache)})")
            
            return removed_count
            
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
            return cached

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
        # Periodic cleanup before adding new entries
        self._cleanup_memory_cache()
        
        # Update memory cache first
        self._memory_cache_component.set(key, data)
        
        # Save to disk cache
        try:
            self._disk_cache_component.set(key, data)
        except CacheError:
            # Disk cache errors are already logged and raised by DiskCache
            raise

    def load_cache(self, key: str) -> Optional[Dict[str, Any]]:
        """Load data from cache with memory caching."""
        # Check memory cache first (1 minute TTL)
        cached = self._memory_cache_component.get(key, max_age=60)
        if cached is not None:
            return cached
        
        # Check disk cache
        data = self._disk_cache_component.get(key, max_age=3600)  # 1 hour for load_cache
        if data is not None:
            # Update memory cache
            self._memory_cache_component.set(key, data)
            return data
        
        return None

    def clear_cache(self, key: Optional[str] = None) -> None:
        """Clear cache for a specific key or all keys."""
        if key:
            # Clear specific key
            self._memory_cache_component.clear(key)
            self._disk_cache_component.clear(key)
            self.logger.info("Cleared cache for key: %s", key)
        else:
            # Clear all keys
            memory_count = self._memory_cache_component.size()
            self._memory_cache_component.clear()
            self._disk_cache_component.clear()
            self.logger.info("Cleared all cache: %d memory entries", memory_count)

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
            with self._cache_lock:
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

    def has_data_changed(self, data_type: str, new_data: Dict[str, Any]) -> bool:
        """Check if data has changed from cached version."""
        cached_data = self.load_cache(data_type)
        if not cached_data:
            return True

        if data_type == 'weather':
            return self._has_weather_changed(cached_data, new_data)
        elif data_type == 'stocks':
            return self._has_stocks_changed(cached_data, new_data)
        elif data_type == 'stock_news':
            return self._has_news_changed(cached_data, new_data)
        elif data_type == 'nhl':
            return self._has_nhl_changed(cached_data, new_data)
        elif data_type == 'mlb':
            return self._has_mlb_changed(cached_data, new_data)
        
        return True

    def _has_weather_changed(self, cached: Dict[str, Any], new: Dict[str, Any]) -> bool:
        """Check if weather data has changed."""
        # Handle new cache structure where data is nested under 'data' key
        if 'data' in cached:
            cached = cached['data']
        
        # Handle case where cached data might be the weather data directly
        if 'current' in cached:
            # This is the new structure with 'current' and 'forecast' keys
            current_weather = cached.get('current', {})
            if current_weather and 'main' in current_weather and 'weather' in current_weather:
                cached_temp = round(current_weather['main']['temp'])
                cached_condition = current_weather['weather'][0]['main']
                return (cached_temp != new.get('temp') or 
                        cached_condition != new.get('condition'))
        
        # Handle old structure where temp and condition are directly accessible
        return (cached.get('temp') != new.get('temp') or 
                cached.get('condition') != new.get('condition'))

    def _has_stocks_changed(self, cached: Dict[str, Any], new: Dict[str, Any]) -> bool:
        """Check if stock data has changed."""
        if not self._is_market_open():
            return False
        return cached.get('price') != new.get('price')

    def _has_news_changed(self, cached: Dict[str, Any], new: Dict[str, Any]) -> bool:
        """Check if news data has changed."""
        # Handle both dictionary and list formats
        if isinstance(new, list):
            # If new data is a list, cached data should also be a list
            if not isinstance(cached, list):
                return True
            # Compare lengths and content
            if len(cached) != len(new):
                return True
            # Compare titles since they're unique enough for our purposes
            cached_titles = set(item.get('title', '') for item in cached)
            new_titles = set(item.get('title', '') for item in new)
            return cached_titles != new_titles
        else:
            # Original dictionary format handling
            cached_headlines = set(h.get('id') for h in cached.get('headlines', []))
            new_headlines = set(h.get('id') for h in new.get('headlines', []))
            return not cached_headlines.issuperset(new_headlines)

    def _has_nhl_changed(self, cached: Dict[str, Any], new: Dict[str, Any]) -> bool:
        """Check if NHL data has changed."""
        return (cached.get('game_status') != new.get('game_status') or
                cached.get('score') != new.get('score'))

    def _has_mlb_changed(self, cached: Dict[str, Any], new: Dict[str, Any]) -> bool:
        """Check if MLB game data has changed."""
        if not cached or not new:
            return True
            
        # Check if any games have changed status or score
        for game_id, new_game in new.items():
            cached_game = cached.get(game_id)
            if not cached_game:
                return True
                
            # Check for score changes
            if (new_game['away_score'] != cached_game['away_score'] or 
                new_game['home_score'] != cached_game['home_score']):
                return True
                
            # Check for status changes
            if new_game['status'] != cached_game['status']:
                return True
                
            # For live games, check inning and count
            if new_game['status'] == 'in':
                if (new_game['inning'] != cached_game['inning'] or 
                    new_game['inning_half'] != cached_game['inning_half'] or
                    new_game['balls'] != cached_game['balls'] or 
                    new_game['strikes'] != cached_game['strikes'] or
                    new_game['bases_occupied'] != cached_game['bases_occupied']):
                    return True
                    
        return False

    def _is_market_open(self) -> bool:
        """Check if the US stock market is currently open."""
        return self._strategy_component.is_market_open()

    def update_cache(self, data_type: str, data: Dict[str, Any]) -> bool:
        """Update cache with new data."""
        cache_data = {
            'data': data,
            'timestamp': time.time()
        }
        return self.save_cache(data_type, cache_data)

    def get(self, key: str, max_age: int = 300) -> Optional[Dict[str, Any]]:
        """Get data from cache if it exists and is not stale."""
        cached_data = self.get_cached_data(key, max_age)
        if cached_data and 'data' in cached_data:
            return cached_data['data']
        return cached_data

    def set(self, key: str, data: Dict[str, Any], ttl: Optional[int] = None) -> None:
        """
        Store data in cache with current timestamp.
        
        Args:
            key: Cache key
            data: Data to cache
            ttl: Optional time-to-live in seconds (stored for compatibility but
                 expiration is still controlled via max_age when reading)
        """
        cache_data = {
            'data': data,
            'timestamp': time.time()
        }
        if ttl is not None:
            cache_data['ttl'] = ttl
        self.save_cache(key, cache_data)

    def setup_persistent_cache(self) -> bool:
        """
        Set up a persistent cache directory with proper permissions.
        This should be run once with sudo to create the directory.
        """
        try:
            # Try to create /var/cache/ledmatrix with proper permissions
            cache_dir = '/var/cache/ledmatrix'
            os.makedirs(cache_dir, exist_ok=True)
            
            # Set ownership to the real user (not root)
            real_user = os.environ.get('SUDO_USER')
            if real_user:
                import pwd
                try:
                    uid = pwd.getpwnam(real_user).pw_uid
                    gid = pwd.getpwnam(real_user).pw_gid
                    os.chown(cache_dir, uid, gid)
                    self.logger.info(f"Set ownership of {cache_dir} to {real_user}")
                except (OSError, KeyError) as e:
                    self.logger.warning(f"Could not set ownership for {cache_dir}: {e}", exc_info=True)
            
            # Set permissions to 755 (rwxr-xr-x)
            os.chmod(cache_dir, 0o755)
            
            self.logger.info(f"Successfully set up persistent cache directory: {cache_dir}")
            return True
            
        except (OSError, IOError, PermissionError) as e:
            self.logger.error(f"Failed to set up persistent cache directory {cache_dir}: {e}", exc_info=True)
            return False 

    def get_sport_live_interval(self, sport_key: str) -> int:
        """
        Get the live_update_interval for a specific sport from config.
        Falls back to default values if config is not available.
        """
        return self._strategy_component.get_sport_live_interval(sport_key)

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

    def get_sport_key_from_cache_key(self, key: str) -> Optional[str]:
        """
        Extract sport key from cache key to determine appropriate live_update_interval.
        """
        return self._strategy_component.get_sport_key_from_cache_key(key)

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

    def get_background_cached_data(self, key: str, sport_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get data from background service cache with appropriate strategy.
        This method is specifically designed for Recent/Upcoming managers
        to use data cached by the background service.
        
        Args:
            key: Cache key to retrieve
            sport_key: Sport key for determining appropriate cache strategy
            
        Returns:
            Cached data if available and fresh, None otherwise
        """
        # Determine the appropriate cache strategy
        data_type = self.get_data_type_from_key(key)
        strategy = self.get_cache_strategy(data_type, sport_key)
        
        # For Recent/Upcoming managers, we want to use the background service cache
        # which should have longer TTLs than the individual manager caches
        max_age = strategy['max_age']
        memory_ttl = strategy.get('memory_ttl', max_age)
        
        # Get the cached data
        cached_data = self.get_cached_data(key, max_age, memory_ttl)
        
        if cached_data:
            # Record cache hit for performance monitoring
            self.record_cache_hit('background')
            # Unwrap if stored in { 'data': ..., 'timestamp': ... } format
            if isinstance(cached_data, dict) and 'data' in cached_data:
                return cached_data['data']
            return cached_data
        
        # Record cache miss for performance monitoring
        self.record_cache_miss('background')
        return None

    def is_background_data_available(self, key: str, sport_key: Optional[str] = None) -> bool:
        """
        Check if background service has fresh data available.
        This helps Recent/Upcoming managers determine if they should
        wait for background data or fetch immediately.
        """
        data_type = self.get_data_type_from_key(key)
        strategy = self.get_cache_strategy(data_type, sport_key)
        
        # Check if we have data that's still fresh according to background service TTL
        cached_data = self.get_cached_data(key, strategy['max_age'])
        return cached_data is not None

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

    def record_cache_hit(self, cache_type: str = 'regular') -> None:
        """Record a cache hit for performance monitoring."""
        with self._cache_lock:
            if cache_type == 'background':
                self._cache_metrics['background_hits'] += 1
            else:
                self._cache_metrics['hits'] += 1

    def record_cache_miss(self, cache_type: str = 'regular') -> None:
        """Record a cache miss for performance monitoring."""
        with self._cache_lock:
            if cache_type == 'background':
                self._cache_metrics['background_misses'] += 1
            else:
                self._cache_metrics['misses'] += 1
            self._cache_metrics['api_calls_saved'] += 1

    def record_fetch_time(self, duration: float) -> None:
        """Record fetch operation duration for performance monitoring."""
        with self._cache_lock:
            self._cache_metrics['total_fetch_time'] += duration
            self._cache_metrics['fetch_count'] += 1

    def get_cache_metrics(self) -> Dict[str, Any]:
        """Get current cache performance metrics."""
        with self._cache_lock:
            total_hits = self._cache_metrics['hits'] + self._cache_metrics['background_hits']
            total_misses = self._cache_metrics['misses'] + self._cache_metrics['background_misses']
            total_requests = total_hits + total_misses
            
            avg_fetch_time = (self._cache_metrics['total_fetch_time'] / 
                             self._cache_metrics['fetch_count']) if self._cache_metrics['fetch_count'] > 0 else 0.0
            
            return {
                'total_requests': total_requests,
                'cache_hit_rate': total_hits / total_requests if total_requests > 0 else 0.0,
                'background_hit_rate': (self._cache_metrics['background_hits'] / 
                                       (self._cache_metrics['background_hits'] + self._cache_metrics['background_misses'])
                                       if (self._cache_metrics['background_hits'] + self._cache_metrics['background_misses']) > 0 else 0.0),
                'api_calls_saved': self._cache_metrics['api_calls_saved'],
                'average_fetch_time': avg_fetch_time,
                'total_fetch_time': self._cache_metrics['total_fetch_time'],
                'fetch_count': self._cache_metrics['fetch_count']
            }

    def log_cache_metrics(self) -> None:
        """Log current cache performance metrics."""
        metrics = self.get_cache_metrics()
        self.logger.info(f"Cache Performance - Hit Rate: {metrics['cache_hit_rate']:.2%}, "
                        f"Background Hit Rate: {metrics['background_hit_rate']:.2%}, "
                        f"API Calls Saved: {metrics['api_calls_saved']}, "
                        f"Avg Fetch Time: {metrics['average_fetch_time']:.2f}s")
    
    def get_memory_cache_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the memory cache.
        
        Returns:
            Dictionary with memory cache statistics
        """
        with self._cache_lock:
            return {
                'size': len(self._memory_cache),
                'max_size': self._max_memory_cache_size,
                'usage_percent': (len(self._memory_cache) / self._max_memory_cache_size * 100) if self._max_memory_cache_size > 0 else 0,
                'last_cleanup': self._last_memory_cache_cleanup,
                'cleanup_interval': self._memory_cache_cleanup_interval
            }
    
    def log_memory_cache_stats(self) -> None:
        """Log current memory cache statistics."""
        stats = self.get_memory_cache_stats()
        self.logger.info(f"Memory Cache - Size: {stats['size']}/{stats['max_size']} "
                        f"({stats['usage_percent']:.1f}%), "
                        f"Last cleanup: {time.time() - stats['last_cleanup']:.1f}s ago")