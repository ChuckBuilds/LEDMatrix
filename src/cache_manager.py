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
        
        # Get the actual user's home directory, even when running with sudo
        try:
            # Try to get the real user's home directory
            real_user = os.environ.get('SUDO_USER') or os.environ.get('USER')
            if real_user:
                home_dir = f"/home/{real_user}"
            else:
                home_dir = os.path.expanduser('~')
        except Exception:
            home_dir = os.path.expanduser('~')
            
        # Determine the appropriate cache directory
        if os.geteuid() == 0:  # Running as root/sudo
            self.cache_dir = "/var/cache/ledmatrix"
        else:
            self.cache_dir = os.path.join(home_dir, '.ledmatrix_cache')
            
        self._memory_cache = {}  # In-memory cache for faster access
        self._memory_cache_timestamps = {}
        self._cache_lock = threading.Lock()
        
        # Ensure cache directory exists after logger is initialized
        self._ensure_cache_dir()
        
    def _ensure_cache_dir(self):
        """Ensure the cache directory exists with proper permissions."""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            # Set permissions to allow both root and the user to access
            if os.geteuid() == 0:  # Running as root/sudo
                os.chmod(self.cache_dir, 0o777)  # Full permissions for all users
                # Also set ownership to the real user if we're running as root
                real_user = os.environ.get('SUDO_USER')
                if real_user:
                    try:
                        import pwd
                        uid = pwd.getpwnam(real_user).pw_uid
                        gid = pwd.getpwnam(real_user).pw_gid
                        os.chown(self.cache_dir, uid, gid)
                    except Exception as e:
                        self.logger.warning(f"Could not set cache directory ownership: {e}")
        except Exception as e:
            self.logger.error(f"Failed to create cache directory: {e}")
            # Fallback to temp directory if we can't create the cache directory
            self.cache_dir = os.path.join(tempfile.gettempdir(), 'ledmatrix_cache')
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                self.logger.info(f"Using fallback cache directory: {self.cache_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create fallback cache directory: {e}")
                raise  # Re-raise if we can't create any cache directory
            
    def _get_cache_path(self, key: str) -> str:
        """Get the path for a cache file."""
        return os.path.join(self.cache_dir, f"{key}.json")
        
    def get_cached_data(self, key: str, max_age: int = 300) -> Optional[Dict]:
        """Get data from cache if it exists and is not stale."""
        if key not in self._memory_cache:
            return None
            
        timestamp = self._memory_cache_timestamps.get(key)
        if timestamp is None:
            return None
            
        # Convert timestamp to float if it's a string
        if isinstance(timestamp, str):
            try:
                timestamp = float(timestamp)
            except ValueError:
                self.logger.error(f"Invalid timestamp format for key {key}: {timestamp}")
                return None
                
        if time.time() - timestamp <= max_age:
            return self._memory_cache[key]
        else:
            # Data is stale, remove it
            self._memory_cache.pop(key, None)
            self._memory_cache_timestamps.pop(key, None)
            return None
            
    def save_cache(self, key: str, data: Dict) -> None:
        """
        Save data to cache.
        Args:
            key: Cache key
            data: Data to cache
        """
        try:
            # Save to file
            cache_path = self._get_cache_path(key)
            with self._cache_lock:
                with open(cache_path, 'w') as f:
                    json.dump(data, f)
                
            # Update memory cache
            self._memory_cache[key] = data
            self._memory_cache_timestamps[key] = time.time()
            
        except Exception:
            pass  # Silently fail if cache save fails

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
        if not os.path.exists(cache_path):
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
                if os.path.exists(cache_path):
                    os.remove(cache_path)
            else:
                # Clear all keys
                self._memory_cache.clear()
                self._memory_cache_timestamps.clear()
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