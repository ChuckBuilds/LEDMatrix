"""
Background Cache Mixin for Sports Managers

This mixin provides common caching functionality to eliminate code duplication
across all sports managers. It implements the background service cache pattern
where Recent/Upcoming managers consume data from the background service cache.
"""

import time
import logging
from typing import Dict, Optional, Any, Callable
from datetime import datetime
import pytz


class BackgroundCacheMixin:
    """
    Mixin class that provides background service cache functionality to sports managers.
    
    This mixin eliminates code duplication by providing a common implementation
    for the background service cache pattern used across all sports managers.
    
    Note: For non-sports managers (weather, stocks, news, etc.), use
    GenericCacheMixin instead. See src/generic_cache_mixin.py for details.
    """
    
    def _fetch_data_with_background_cache(self, 
                                        sport_key: str, 
                                        api_fetch_method: Callable,
                                        live_manager_class: type = None) -> Optional[Dict]:
        """
        Common logic for fetching data with background service cache support.
        
        This method implements the background service cache pattern:
        1. Live managers always fetch fresh data
        2. Recent/Upcoming managers try background cache first
        3. Fallback to direct API call if background data unavailable
        
        Args:
            sport_key: Sport identifier (e.g., 'nba', 'nfl', 'ncaa_fb')
            api_fetch_method: Method to call for direct API fetch
            live_manager_class: Class to check if this is a live manager
            
        Returns:
            Cached or fresh data from API
        """
        start_time = time.time()
        cache_hit = False
        cache_source = None
        
        try:
            # For Live managers, always fetch fresh data
            if live_manager_class and isinstance(self, live_manager_class):
                self.logger.info(f"[{sport_key.upper()}] Live manager - fetching fresh data")
                result = api_fetch_method(use_cache=False)
                cache_source = "live_fresh"
            else:
                # For Recent/Upcoming managers, try background service cache first
                cache_key = self.cache_manager.generate_sport_cache_key(sport_key)
                
                # Check if background service has fresh data
                if self.cache_manager.is_background_data_available(cache_key, sport_key):
                    cached_data = self.cache_manager.get_background_cached_data(cache_key, sport_key)
                    if cached_data:
                        self.logger.info(f"[{sport_key.upper()}] Using background service cache for {cache_key}")
                        result = cached_data
                        cache_hit = True
                        cache_source = "background_cache"
                    else:
                        self.logger.warning(f"[{sport_key.upper()}] Background cache check passed but no data returned for {cache_key}")
                        result = None
                        cache_source = "background_miss"
                else:
                    self.logger.info(f"[{sport_key.upper()}] Background data not available for {cache_key}")
                    result = None
                    cache_source = "background_unavailable"
                
                # Fallback to direct API call if background data not available
                if result is None:
                    self.logger.info(f"[{sport_key.upper()}] Fetching directly from API for {cache_key}")
                    result = api_fetch_method(use_cache=True)
                    cache_source = "api_fallback"
            
            # Record performance metrics
            duration = time.time() - start_time
            self.cache_manager.record_fetch_time(duration)
            
            # Log performance metrics
            self._log_fetch_performance(sport_key, duration, cache_hit, cache_source)
            
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            self.logger.error(f"[{sport_key.upper()}] Error in background cache fetch after {duration:.2f}s: {e}")
            self.cache_manager.record_fetch_time(duration)
            raise
    
    def _log_fetch_performance(self, sport_key: str, duration: float, cache_hit: bool, cache_source: str):
        """
        Log detailed performance metrics for fetch operations.
        
        Args:
            sport_key: Sport identifier
            duration: Fetch operation duration in seconds
            cache_hit: Whether this was a cache hit
            cache_source: Source of the data (background_cache, api_fallback, etc.)
        """
        # Log basic performance info
        self.logger.info(f"[{sport_key.upper()}] Fetch completed in {duration:.2f}s "
                        f"(cache_hit={cache_hit}, source={cache_source})")
        
        # Log detailed metrics every 10 operations
        if hasattr(self, '_fetch_count'):
            self._fetch_count += 1
        else:
            self._fetch_count = 1
            
        if self._fetch_count % 10 == 0:
            metrics = self.cache_manager.get_cache_metrics()
            self.logger.info(f"[{sport_key.upper()}] Cache Performance Summary - "
                           f"Hit Rate: {metrics['cache_hit_rate']:.2%}, "
                           f"Background Hit Rate: {metrics['background_hit_rate']:.2%}, "
                           f"API Calls Saved: {metrics['api_calls_saved']}")
    
    def get_cache_performance_summary(self) -> Dict[str, Any]:
        """
        Get cache performance summary for this manager.
        
        Returns:
            Dictionary containing cache performance metrics
        """
        return self.cache_manager.get_cache_metrics()
    
    def log_cache_performance(self):
        """Log current cache performance metrics."""
        self.cache_manager.log_cache_metrics()
