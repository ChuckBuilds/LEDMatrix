"""
API Helper

Handles HTTP requests, caching, and ESPN API integration for LED matrix plugins.
Extracted from LEDMatrix core to provide reusable functionality for plugins.
"""

import logging
import time
from datetime import datetime
from types import MappingProxyType
from src.common.espn_dates import ESPN_MAX_LIMIT
from typing import Any, Dict, Mapping, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


#: The User-Agent core sends to ESPN and other data APIs. It names the client
#: and links to it: around 2026-08-04 ESPN began 403ing bare custom tokens
#: (and browser strings), and this form is what it accepts.
USER_AGENT = 'LEDMatrix/1.0 (+https://github.com/ChuckBuilds/LEDMatrix)'

#: Base headers for core's JSON API requests. Read-only; pass
#: ``{**DEFAULT_HTTP_HEADERS, ...}`` to add to it. There is deliberately no
#: Accept-Encoding: requests advertises only what urllib3 can decode here
#: (``br`` needs the optional brotli package, which is not a requirement), so a
#: hand-set ``br`` invites a body the client cannot read.
DEFAULT_HTTP_HEADERS: Mapping[str, str] = MappingProxyType({
    'User-Agent': USER_AGENT,
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
})


class APIHelper:
    """
    HTTP requests with retries, response caching and ESPN helpers.

    - Requests go through one ``requests.Session`` that retries GET, HEAD
      and OPTIONS on 429 and 5xx with exponential backoff, and sends
      :data:`DEFAULT_HTTP_HEADERS`.
    - Consecutive requests from one helper are spaced at least
      ``set_rate_limit()`` seconds apart (1 second by default). A cache hit
      does not count.
    - With a ``cache_manager``, :meth:`get` caches the parsed JSON under
      ``cache_key`` for ``cache_ttl`` seconds. The lifetime is stored with
      the entry, so CacheManager honours it on every later read, whatever
      max_age that read asks for.
    - Failed requests are logged and return None; nothing here raises for a
      network or HTTP error.
    """
    
    def __init__(self, cache_manager=None, default_timeout: int = 30,
                 max_retries: int = 3, logger: Optional[logging.Logger] = None):
        """
        Initialize the APIHelper.
        
        Args:
            cache_manager: Optional cache manager for response caching
            default_timeout: Default timeout for requests in seconds
            max_retries: Maximum number of retry attempts
            logger: Optional logger instance
        """
        self.cache_manager = cache_manager
        self.default_timeout = default_timeout
        self.max_retries = max_retries
        self.logger = logger or logging.getLogger(__name__)
        
        # Setup session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        # Default headers
        self.session.headers.update({
            'User-Agent': USER_AGENT,
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive'
        })
        
        # Rate limiting
        self._last_request_time = 0
        self._min_request_interval = 1.0  # Minimum seconds between requests
    
    def get(self, url: str, params: Optional[Dict] = None, 
            headers: Optional[Dict] = None, timeout: Optional[int] = None,
            cache_key: Optional[str] = None, cache_ttl: int = 3600) -> Optional[Dict]:
        """
        Make a GET request with optional caching.
        
        Args:
            url: URL to request
            params: Query parameters
            headers: Additional headers
            timeout: Request timeout (uses default if None)
            cache_key: Key for caching response
            cache_ttl: Cache time-to-live in seconds
            
        Returns:
            Response data as dictionary or None if request fails
        """
        if cache_key and self.cache_manager:
            cached = self._get_from_cache(cache_key, cache_ttl)
            if cached is not None:
                self.logger.debug(f"Using cached response for {cache_key}")
                return cached
        
        # Rate limiting
        self._enforce_rate_limit()
        
        try:
            # Prepare request
            request_headers = self.session.headers.copy()
            if headers:
                request_headers.update(headers)
            
            # Make request
            response = self.session.get(
                url, 
                params=params,
                headers=request_headers,
                timeout=timeout or self.default_timeout
            )
            response.raise_for_status()
            
            # Parse JSON response
            data = response.json()
            
            # Cache response if cache key provided
            if cache_key and self.cache_manager:
                self._set_cache(cache_key, data, cache_ttl)
            
            self.logger.debug(f"Successfully fetched {url}")
            return data
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed for {url}: {e}")
            return None
    
    def fetch_espn_scoreboard(self, sport: str, league: str, 
                             date: Optional[str] = None,
                             cache_key: Optional[str] = None,
                             cache_ttl: int = 300) -> Optional[Dict]:
        """
        Fetch ESPN scoreboard data for a specific sport and league.
        
        Args:
            sport: Sport name (e.g., 'basketball', 'football')
            league: League name (e.g., 'nba', 'nfl')
            date: Date in YYYYMMDD format (defaults to today)
            cache_key: Cache key for response
            cache_ttl: Cache time-to-live in seconds
            
        Returns:
            ESPN API response data or None if request fails
        """
        if date is None:
            date = datetime.now().strftime('%Y%m%d')
        
        # Build URL
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
        
        # Build cache key if not provided
        if cache_key is None:
            cache_key = f"espn_{sport}_{league}_{date}"
        
        # Set parameters
        # limit above 500 makes ESPN truncate instead of erroring: college
        # football came back with 25 of 68 games. See src/common/espn_dates.py.
        params = {
            'dates': date,
            'limit': ESPN_MAX_LIMIT
        }
        
        return self.get(url, params=params, cache_key=cache_key, cache_ttl=cache_ttl)
    
    def fetch_espn_standings(self, sport: str, league: str,
                            cache_key: Optional[str] = None,
                            cache_ttl: int = 3600) -> Optional[Dict]:
        """
        Fetch ESPN standings data for a specific sport and league.
        
        Args:
            sport: Sport name
            league: League name
            cache_key: Cache key for response
            cache_ttl: Cache time-to-live in seconds
            
        Returns:
            ESPN standings data or None if request fails
        """
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/standings"
        
        if cache_key is None:
            cache_key = f"espn_standings_{sport}_{league}"
        
        return self.get(url, cache_key=cache_key, cache_ttl=cache_ttl)
    
    def fetch_espn_rankings(self, sport: str, league: str,
                           cache_key: Optional[str] = None,
                           cache_ttl: int = 3600) -> Optional[Dict]:
        """
        Fetch ESPN rankings data for a specific sport and league.
        
        Args:
            sport: Sport name
            league: League name
            cache_key: Cache key for response
            cache_ttl: Cache time-to-live in seconds
            
        Returns:
            ESPN rankings data or None if request fails
        """
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/rankings"
        
        if cache_key is None:
            cache_key = f"espn_rankings_{sport}_{league}"
        
        return self.get(url, cache_key=cache_key, cache_ttl=cache_ttl)
    
    def post(self, url: str, data: Optional[Dict] = None,
             json_data: Optional[Dict] = None,
             headers: Optional[Dict] = None,
             timeout: Optional[int] = None) -> Optional[Dict]:
        """
        Make a POST request.
        
        Args:
            url: URL to request
            data: Form data
            json_data: JSON data
            headers: Additional headers
            timeout: Request timeout
            
        Returns:
            Response data as dictionary or None if request fails
        """
        self._enforce_rate_limit()
        
        try:
            request_headers = self.session.headers.copy()
            if headers:
                request_headers.update(headers)
            
            response = self.session.post(
                url,
                data=data,
                json=json_data,
                headers=request_headers,
                timeout=timeout or self.default_timeout
            )
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"POST request failed for {url}: {e}")
            return None
    
    def set_cache(self, key: str, data: Any, ttl: int = 3600) -> None:
        """
        Set cache data.
        
        Args:
            key: Cache key
            data: Data to cache
            ttl: Seconds the entry stays valid. Stored with the entry, so
                it applies to every later read of ``key``.
        """
        self._set_cache(key, data, ttl)
    
    def get_cache(self, key: str) -> Optional[Any]:
        """
        Get cached data.

        Args:
            key: Cache key

        Returns:
            Cached data, or None if there is none or it has expired. An
            entry written with a ttl (set_cache, get) expires after that ttl;
            one written without expires after CacheManager's default max_age.
        """
        return self._get_from_cache(key)
    
    def clear_cache(self, pattern: Optional[str] = None) -> None:
        """
        Clear cache data.

        Uses CacheManager's real surface (clear_cache / delete /
        list_cache_files); safely no-ops on managers without it. The old
        implementation guarded on a nonexistent ``clear`` method, so it
        silently never cleared anything.

        Args:
            pattern: Optional substring to match cache keys; only matching
                entries are deleted.
        """
        if not self.cache_manager:
            return
        if pattern:
            if (hasattr(self.cache_manager, 'list_cache_files')
                    and hasattr(self.cache_manager, 'delete')):
                for entry in self.cache_manager.list_cache_files():
                    key = entry.get('key') if isinstance(entry, dict) else None
                    if key and pattern in key:
                        self.cache_manager.delete(key)
            else:
                self.logger.debug(
                    "Cache manager lacks list_cache_files/delete; "
                    "cannot clear by pattern")
        elif hasattr(self.cache_manager, 'clear_cache'):
            self.cache_manager.clear_cache()
        elif hasattr(self.cache_manager, 'clear'):
            self.cache_manager.clear()
        else:
            self.logger.debug("Cache manager exposes no clear method; no-op")
    
    def _get_from_cache(self, key: str, max_age: Optional[int] = None) -> Optional[Any]:
        """Cached data for ``key``, or None. ``max_age`` only matters for an
        entry stored without a ttl; one stored with a ttl uses that."""
        if not self.cache_manager:
            return None
        if max_age is None:
            return self.cache_manager.get(key)
        return self.cache_manager.get(key, max_age=max_age)

    def _set_cache(self, key: str, data: Any, ttl: Optional[int]) -> None:
        """Store ``data`` under ``key`` for ``ttl`` seconds."""
        if self.cache_manager:
            self.cache_manager.set(key, data, ttl=ttl)
    
    def _enforce_rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        current_time = time.time()
        time_since_last = current_time - self._last_request_time
        
        if time_since_last < self._min_request_interval:
            sleep_time = self._min_request_interval - time_since_last
            time.sleep(sleep_time)
        
        self._last_request_time = time.time()
    
    def set_rate_limit(self, min_interval: float) -> None:
        """
        Set minimum interval between requests.
        
        Args:
            min_interval: Minimum seconds between requests
        """
        self._min_request_interval = min_interval
        self.logger.debug(f"Rate limit set to {min_interval} seconds")
    
    def get_request_stats(self) -> Dict[str, Any]:
        """
        Get request statistics.
        
        Returns:
            Dictionary with request statistics
        """
        return {
            'min_request_interval': self._min_request_interval,
            'last_request_time': self._last_request_time,
            'time_since_last_request': time.time() - self._last_request_time
        }
