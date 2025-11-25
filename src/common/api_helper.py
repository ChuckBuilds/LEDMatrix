"""
API Helper

Handles HTTP requests, caching, and ESPN API integration for LED matrix plugins.
Extracted from LEDMatrix core to provide reusable functionality for plugins.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class APIHelper:
    """
    Helper class for HTTP requests, caching, and ESPN API integration.
    
    Provides functionality for:
    - HTTP requests with retry logic and timeouts
    - Response caching with TTL support
    - ESPN API integration for sports data
    - Request rate limiting and throttling
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
            'User-Agent': 'LEDMatrix-Common/1.0',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
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
        # Check cache first
        if cache_key and self.cache_manager:
            cached = self._get_from_cache(cache_key)
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
        params = {
            'dates': date,
            'limit': 1000
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
            ttl: Time-to-live in seconds (ignored - CacheManager doesn't support TTL)
        """
        if self.cache_manager:
            self.cache_manager.set(key, data)
    
    def get_cache(self, key: str) -> Optional[Any]:
        """
        Get cached data.
        
        Args:
            key: Cache key
            
        Returns:
            Cached data or None if not found
        """
        if self.cache_manager:
            return self.cache_manager.get(key)
        return None
    
    def clear_cache(self, pattern: Optional[str] = None) -> None:
        """
        Clear cache data.
        
        Args:
            pattern: Optional pattern to match cache keys
        """
        if self.cache_manager:
            if hasattr(self.cache_manager, 'clear'):
                if pattern:
                    # Clear only keys matching pattern
                    keys = self.cache_manager.keys()
                    for key in keys:
                        if pattern in key:
                            self.cache_manager.delete(key)
                else:
                    self.cache_manager.clear()
    
    def _get_from_cache(self, key: str) -> Optional[Any]:
        """Get data from cache."""
        if self.cache_manager:
            return self.cache_manager.get(key)
        return None
    
    def _set_cache(self, key: str, data: Any, ttl: int) -> None:
        """Set data in cache."""
        if self.cache_manager:
            self.cache_manager.set(key, data)
    
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
