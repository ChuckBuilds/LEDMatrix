import json
import os
import time
from datetime import datetime
import pytz
from typing import Any, Dict, Optional
import logging
import stat
import threading
import tempfile
from pathlib import Path

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

class CacheManager:
    """Manages caching of API responses to reduce API calls."""
    
    def __init__(self):
        # Initialize logger first
        self.logger = logging.getLogger(__name__)
        
        # Determine the most reliable writable directory
        self.cache_dir = self._get_writable_cache_dir()
        if self.cache_dir:
            self.logger.info(f"Using cache directory: {self.cache_dir}")
        else:
            # This is a critical failure, as caching is essential.
            self.logger.error("Could not find or create a writable cache directory. Caching will be disabled.")
            self.cache_dir = None

        self._memory_cache = {}  # In-memory cache for faster access
        self._memory_cache_timestamps = {}
        self._cache_lock = threading.Lock()
        
        # Initialize config manager for sport-specific intervals
        try:
            from src.config_manager import ConfigManager
            self.config_manager = ConfigManager()
            self.config_manager.load_config()
        except ImportError:
            self.config_manager = None
            self.logger.warning("ConfigManager not available, using default cache intervals")
        
        # Initialize performance metrics
        self._cache_metrics = {
            'hits': 0,
            'misses': 0,
            'api_calls_saved': 0,
            'background_hits': 0,
            'background_misses': 0,
            'total_fetch_time': 0.0,
            'fetch_count': 0
        }

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
        except Exception as e:
            self.logger.warning(f"Could not use /var/cache/ledmatrix: {e}")

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
        except Exception as e:
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
                os.makedirs(opt_cache_dir, exist_ok=True)
                if os.access(opt_cache_dir, os.W_OK):
                    return opt_cache_dir
        except Exception as e:
            self.logger.warning(f"Could not use /opt/ledmatrix/cache: {e}")

        # Attempt 4: System-wide temporary directory (fallback, not persistent)
        try:
            temp_cache_dir = os.path.join(tempfile.gettempdir(), 'ledmatrix_cache')
            os.makedirs(temp_cache_dir, exist_ok=True)
            if os.access(temp_cache_dir, os.W_OK):
                self.logger.warning("Using temporary cache directory - cache will NOT persist across restarts")
                return temp_cache_dir
        except Exception as e:
            self.logger.warning(f"Could not use system-wide temporary cache directory: {e}")

        # Return None if no directory is writable
        return None

    def _ensure_cache_dir(self):
        """This method is deprecated and no longer needed."""
        pass
            
    def _get_cache_path(self, key: str) -> Optional[str]:
        """Get the path for a cache file."""
        if not self.cache_dir:
            return None
        return os.path.join(self.cache_dir, f"{key}.json")
        
    def get_cached_data(self, key: str, max_age: int = 300, memory_ttl: Optional[int] = None) -> Optional[Dict]:
        """Get data from cache (memory first, then disk) honoring TTLs.

        - memory_ttl: TTL for in-memory entry; defaults to max_age if not provided
        - max_age: TTL for persisted (on-disk) entry based on the stored timestamp
        """
        now = time.time()
        in_memory_ttl = memory_ttl if memory_ttl is not None else max_age

        # 1) Memory cache
        if key in self._memory_cache:
            timestamp = self._memory_cache_timestamps.get(key)
            if isinstance(timestamp, str):
                try:
                    timestamp = float(timestamp)
                except ValueError:
                    self.logger.error(f"Invalid timestamp format for key {key}: {timestamp}")
                    timestamp = None
            if timestamp is not None and (now - float(timestamp) <= in_memory_ttl):
                return self._memory_cache[key]
            # Expired memory entry → evict and fall through to disk
            self._memory_cache.pop(key, None)
            self._memory_cache_timestamps.pop(key, None)

        # 2) Disk cache
        cache_path = self._get_cache_path(key)
        if cache_path and os.path.exists(cache_path):
            try:
                with self._cache_lock:
                    with open(cache_path, 'r') as f:
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

                if record_ts is None or (now - record_ts) <= max_age:
                    # Hydrate memory cache (use current time to start memory TTL window)
                    self._memory_cache[key] = record
                    self._memory_cache_timestamps[key] = now
                    return record
                else:
                    # Stale on disk; keep file for potential diagnostics but treat as miss
                    return None
            except json.JSONDecodeError as e:
                self.logger.error(f"Error parsing cache file for {key}: {e}")
                # If the file is corrupted, remove it
                try:
                    os.remove(cache_path)
                except OSError:
                    pass
                return None
            except Exception as e:
                self.logger.error(f"Error loading cache for {key}: {e}")
                return None

        # 3) Miss
        return None
            
    def save_cache(self, key: str, data: Dict) -> None:
        """
        Save data to cache.
        Args:
            key: Cache key
            data: Data to cache
        """
        try:
            # Update memory cache first
            self._memory_cache[key] = data
            self._memory_cache_timestamps[key] = time.time()

            # Save to file if a cache directory is available
            cache_path = self._get_cache_path(key)
            if cache_path:
                # Atomic write to avoid partial/corrupt files
                with self._cache_lock:
                    tmp_dir = os.path.dirname(cache_path)
                    try:
                        fd, tmp_path = tempfile.mkstemp(prefix=f".{os.path.basename(cache_path)}.", dir=tmp_dir)
                        try:
                            with os.fdopen(fd, 'w') as tmp_file:
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
                    except Exception as e:
                        self.logger.error(f"Atomic write failed for key '{key}': {e}")
                        # Attempt one-time fallback write directly into /var/cache/ledmatrix if available
                        try:
                            fallback_dir = '/var/cache/ledmatrix'
                            if os.path.isdir(fallback_dir) and os.access(fallback_dir, os.W_OK):
                                fallback_path = os.path.join(fallback_dir, os.path.basename(cache_path))
                                with open(fallback_path, 'w') as tmp_file:
                                    json.dump(data, tmp_file, indent=4, cls=DateTimeEncoder)
                                self.logger.warning(f"Cache wrote to fallback location: {fallback_path}")
                        except Exception as e2:
                            self.logger.error(f"Fallback cache write also failed: {e2}")
            
        except (IOError, OSError) as e:
            self.logger.error(f"Failed to save cache for key '{key}': {e}")
        except Exception as e:
            self.logger.error(f"An unexpected error occurred while saving cache for key '{key}': {e}")

    def load_cache(self, key: str) -> Optional[Dict[str, Any]]:
        """Load data from cache with memory caching."""
        current_time = time.time()
        
        # Check memory cache first
        if key in self._memory_cache:
            if current_time - self._memory_cache_timestamps.get(key, 0) < 60:  # 1 minute TTL
                return self._memory_cache[key]
            else:
                # Clear expired memory cache
                if key in self._memory_cache:
                    del self._memory_cache[key]
                if key in self._memory_cache_timestamps:
                    del self._memory_cache_timestamps[key]

        cache_path = self._get_cache_path(key)
        if not cache_path or not os.path.exists(cache_path):
            return None

        try:
            with self._cache_lock:
                with open(cache_path, 'r') as f:
                    try:
                        data = json.load(f)
                        # Update memory cache
                        self._memory_cache[key] = data
                        self._memory_cache_timestamps[key] = current_time
                        return data
                    except json.JSONDecodeError as e:
                        self.logger.error(f"Error parsing cache file for {key}: {e}")
                        # If the file is corrupted, remove it
                        os.remove(cache_path)
                        return None
        except Exception as e:
            self.logger.error(f"Error loading cache for {key}: {e}")
            return None

    def clear_cache(self, key: Optional[str] = None) -> None:
        """Clear cache for a specific key or all keys."""
        with self._cache_lock:
            if key:
                # Clear specific key
                if key in self._memory_cache:
                    del self._memory_cache[key]
                    del self._memory_cache_timestamps[key]
                cache_path = self._get_cache_path(key)
                if cache_path and os.path.exists(cache_path):
                    os.remove(cache_path)
            else:
                # Clear all keys
                self._memory_cache.clear()
                self._memory_cache_timestamps.clear()
                if self.cache_dir:
                    for file in os.listdir(self.cache_dir):
                        if file.endswith('.json'):
                            os.remove(os.path.join(self.cache_dir, file))

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
        et_tz = pytz.timezone('America/New_York')
        now = datetime.now(et_tz)
        
        # Check if it's a weekday
        if now.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
            return False
            
        # Convert current time to ET
        current_time = now.time()
        market_open = datetime.strptime('09:30', '%H:%M').time()
        market_close = datetime.strptime('16:00', '%H:%M').time()
        
        return market_open <= current_time <= market_close

    def update_cache(self, data_type: str, data: Dict[str, Any]) -> bool:
        """Update cache with new data."""
        cache_data = {
            'data': data,
            'timestamp': time.time()
        }
        return self.save_cache(data_type, cache_data)

    def get(self, key: str, max_age: int = 300) -> Optional[Dict]:
        """Get data from cache if it exists and is not stale."""
        cached_data = self.get_cached_data(key, max_age)
        if cached_data and 'data' in cached_data:
            return cached_data['data']
        return cached_data

    def set(self, key: str, data: Dict) -> None:
        """Store data in cache with current timestamp."""
        cache_data = {
            'data': data,
            'timestamp': time.time()
        }
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
                except Exception as e:
                    self.logger.warning(f"Could not set ownership: {e}")
            
            # Set permissions to 755 (rwxr-xr-x)
            os.chmod(cache_dir, 0o755)
            
            self.logger.info(f"Successfully set up persistent cache directory: {cache_dir}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to set up persistent cache directory: {e}")
            return False 

    def get_sport_live_interval(self, sport_key: str) -> int:
        """
        Get the live_update_interval for a specific sport from config.
        Falls back to default values if config is not available.
        """
        if not self.config_manager:
            # Default intervals - all sports use 60 seconds as default
            default_intervals = {
                'soccer': 60,      # Soccer default
                'nfl': 60,         # NFL default
                'nhl': 60,         # NHL default
                'nba': 60,         # NBA default
                'mlb': 60,         # MLB default
                'milb': 60,        # Minor league default
                'ncaa_fb': 60,    # College football default
                'ncaa_baseball': 60,  # College baseball default
                'ncaam_basketball': 60,  # College basketball default
            }
            return default_intervals.get(sport_key, 60)
        
        try:
            config = self.config_manager.config
            # For MiLB, look for "milb" config instead of "milb_scoreboard"
            if sport_key == 'milb':
                sport_config = config.get("milb", {})
            else:
                sport_config = config.get(f"{sport_key}_scoreboard", {})
            return sport_config.get("live_update_interval", 60)  # Default to 60 seconds
        except Exception as e:
            self.logger.warning(f"Could not get live_update_interval for {sport_key}: {e}")
            return 60  # Default to 60 seconds

    def get_cache_strategy(self, data_type: str, sport_key: str = None) -> Dict[str, Any]:
        """
        Get cache strategy for different data types.
        Now respects sport-specific live_update_interval configurations.
        """
        # Get sport-specific live interval if provided
        live_interval = None
        if sport_key and data_type in ['sports_live', 'live_scores']:
            live_interval = self.get_sport_live_interval(sport_key)
        
        # Try to read sport-specific config for recent/upcoming
        recent_interval = None
        upcoming_interval = None
        if self.config_manager and sport_key:
            try:
                if sport_key == 'milb':
                    sport_cfg = self.config_manager.config.get('milb', {})
                else:
                    sport_cfg = self.config_manager.config.get(f"{sport_key}_scoreboard", {})
                recent_interval = sport_cfg.get('recent_update_interval')
                upcoming_interval = sport_cfg.get('upcoming_update_interval')
            except Exception as e:
                self.logger.debug(f"Could not read sport-specific recent/upcoming intervals for {sport_key}: {e}")

        strategies = {
            # Ultra time-sensitive data (live scores, current weather)
            'live_scores': {
                'max_age': live_interval or 15,  # Use sport-specific interval
                'memory_ttl': (live_interval or 15) * 2,  # 2x for memory cache
                'force_refresh': True
            },
            'sports_live': {
                'max_age': live_interval or 30,  # Use sport-specific interval
                'memory_ttl': (live_interval or 30) * 2,
                'force_refresh': True
            },
            'weather_current': {
                'max_age': 300,  # 5 minutes
                'memory_ttl': 600,
                'force_refresh': False
            },
            
            # Market data (stocks, crypto)
            'stocks': {
                'max_age': 600,  # 10 minutes
                'memory_ttl': 1200,
                'market_hours_only': True,
                'force_refresh': False
            },
            'crypto': {
                'max_age': 300,  # 5 minutes (crypto trades 24/7)
                'memory_ttl': 600,
                'force_refresh': False
            },
            
            # Sports data
            'sports_recent': {
                'max_age': recent_interval or 1800,  # 30 minutes default; override by config
                'memory_ttl': (recent_interval or 1800) * 2,
                'force_refresh': False
            },
            'sports_upcoming': {
                'max_age': upcoming_interval or 10800,  # 3 hours default; override by config
                'memory_ttl': (upcoming_interval or 10800) * 2,
                'force_refresh': False
            },
            'sports_schedules': {
                'max_age': 86400,  # 24 hours
                'memory_ttl': 172800,
                'force_refresh': False
            },
            'leaderboard': {
                'max_age': 604800,  # 7 days (1 week) - football rankings updated weekly
                'memory_ttl': 1209600,  # 14 days in memory
                'force_refresh': False
            },
            
            # News and odds
            'news': {
                'max_age': 3600,  # 1 hour
                'memory_ttl': 7200,
                'force_refresh': False
            },
            'odds': {
                'max_age': 1800,  # 30 minutes for upcoming games
                'memory_ttl': 3600,
                'force_refresh': False
            },
            'odds_live': {
                'max_age': 120,  # 2 minutes for live games (odds change rapidly)
                'memory_ttl': 240,
                'force_refresh': False
            },
            
            # Static/stable data
            'team_info': {
                'max_age': 604800,  # 1 week
                'memory_ttl': 1209600,
                'force_refresh': False
            },
            'logos': {
                'max_age': 2592000,  # 30 days
                'memory_ttl': 5184000,
                'force_refresh': False
            },
            
            # Default fallback
            'default': {
                'max_age': 300,  # 5 minutes
                'memory_ttl': 600,
                'force_refresh': False
            }
        }
        
        return strategies.get(data_type, strategies['default'])

    def get_data_type_from_key(self, key: str) -> str:
        """
        Determine the appropriate cache strategy based on the cache key.
        This helps automatically select the right cache duration.
        """
        key_lower = key.lower()
        
        # Live sports data
        if any(x in key_lower for x in ['live', 'current', 'scoreboard']):
            if 'soccer' in key_lower:
                return 'sports_live'  # Soccer live data is very time-sensitive
            return 'sports_live'
        
        # Weather data
        if 'weather' in key_lower:
            return 'weather_current'
        
        # Market data
        if 'stock' in key_lower or 'crypto' in key_lower:
            if 'crypto' in key_lower:
                return 'crypto'
            return 'stocks'
        
        # News data
        if 'news' in key_lower:
            return 'news'
        
        # Odds data - differentiate between live and upcoming games
        if 'odds' in key_lower:
            # For live games, use shorter cache; for upcoming games, use longer cache
            if any(x in key_lower for x in ['live', 'current']):
                return 'odds_live'  # Live odds change more frequently
            return 'odds'  # Regular odds for upcoming games
        
        # Sports schedules and team info
        if any(x in key_lower for x in ['schedule', 'team_map', 'league']):
            return 'sports_schedules'
        
        # Recent games (last few hours)
        if 'recent' in key_lower:
            return 'sports_recent'
        
        # Upcoming games
        if 'upcoming' in key_lower:
            return 'sports_upcoming'
        
        # Static data like logos, team info
        if any(x in key_lower for x in ['logo', 'team_info', 'config']):
            return 'team_info'
        
        # Default fallback
        return 'default'

    def get_sport_key_from_cache_key(self, key: str) -> Optional[str]:
        """
        Extract sport key from cache key to determine appropriate live_update_interval.
        """
        key_lower = key.lower()
        
        # Map cache key patterns to sport keys
        sport_patterns = {
            'nfl': ['nfl'],
            'nba': ['nba', 'basketball'],
            'mlb': ['mlb', 'baseball'],
            'nhl': ['nhl', 'hockey'],
            'soccer': ['soccer'],
            'ncaa_fb': ['ncaa_fb', 'ncaafb', 'college_football'],
            'ncaa_baseball': ['ncaa_baseball', 'college_baseball'],
            'ncaam_basketball': ['ncaam_basketball', 'college_basketball'],
            'milb': ['milb', 'minor_league'],
        }
        
        for sport_key, patterns in sport_patterns.items():
            if any(pattern in key_lower for pattern in patterns):
                return sport_key
        
        return None

    def get_cached_data_with_strategy(self, key: str, data_type: str = 'default') -> Optional[Dict]:
        """
        Get data from cache using data-type-specific strategy.
        Now respects sport-specific live_update_interval configurations.
        """
        # Extract sport key for live sports data
        sport_key = None
        if data_type in ['sports_live', 'live_scores']:
            sport_key = self.get_sport_key_from_cache_key(key)
        
        strategy = self.get_cache_strategy(data_type, sport_key)
        max_age = strategy['max_age']
        memory_ttl = strategy.get('memory_ttl', max_age)
        
        # For market data, check if market is open
        if strategy.get('market_hours_only', False) and not self._is_market_open():
            # During off-hours, extend cache duration
            max_age *= 4  # 4x longer cache during off-hours
        
        record = self.get_cached_data(key, max_age, memory_ttl)
        # Unwrap if stored in { 'data': ..., 'timestamp': ... }
        if isinstance(record, dict) and 'data' in record:
            return record['data']
        return record

    def get_with_auto_strategy(self, key: str) -> Optional[Dict]:
        """
        Get cached data using automatically determined strategy.
        Now respects sport-specific live_update_interval configurations.
        """
        data_type = self.get_data_type_from_key(key)
        return self.get_cached_data_with_strategy(key, data_type)

    def get_background_cached_data(self, key: str, sport_key: str = None) -> Optional[Dict]:
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

    def is_background_data_available(self, key: str, sport_key: str = None) -> bool:
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

    def generate_sport_cache_key(self, sport: str, date_str: str = None) -> str:
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

    def record_cache_hit(self, cache_type: str = 'regular'):
        """Record a cache hit for performance monitoring."""
        with self._cache_lock:
            if cache_type == 'background':
                self._cache_metrics['background_hits'] += 1
            else:
                self._cache_metrics['hits'] += 1

    def record_cache_miss(self, cache_type: str = 'regular'):
        """Record a cache miss for performance monitoring."""
        with self._cache_lock:
            if cache_type == 'background':
                self._cache_metrics['background_misses'] += 1
            else:
                self._cache_metrics['misses'] += 1
            self._cache_metrics['api_calls_saved'] += 1

    def record_fetch_time(self, duration: float):
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

    def log_cache_metrics(self):
        """Log current cache performance metrics."""
        metrics = self.get_cache_metrics()
        self.logger.info(f"Cache Performance - Hit Rate: {metrics['cache_hit_rate']:.2%}, "
                        f"Background Hit Rate: {metrics['background_hit_rate']:.2%}, "
                        f"API Calls Saved: {metrics['api_calls_saved']}, "
                        f"Avg Fetch Time: {metrics['average_fetch_time']:.2f}s")