"""
Generic Cache Mixin for Any Manager

This mixin provides caching functionality that can be used by any manager
that needs to cache data, not just sports managers. It's a more general
version of BackgroundCacheMixin that works for weather, stocks, news, etc.
"""

import time
import logging
from typing import Dict, Optional, Any, Callable


class GenericCacheMixin:
    """
    Generic mixin class that provides caching functionality to any manager.
    
    This mixin can be used by weather, stock, news, or any other manager
    that needs to cache data with performance monitoring.
    """
    
    def _fetch_data_with_cache(self, 
                             cache_key: str,
                             api_fetch_method: Callable,
                             cache_ttl: int = 300,
                             force_refresh: bool = False) -> Optional[Dict]:
        """
        Generic caching pattern for any manager.
        
        Args:
            cache_key: Unique cache key for this data
            api_fetch_method: Method to call for fresh data
            cache_ttl: Time-to-live in seconds (default: 5 minutes)
            force_refresh: Skip cache and fetch fresh data
            
        Returns:
            Cached or fresh data from API
        """
        start_time = time.time()
        cache_hit = False
        cache_source = None
        
        try:
            # Check cache first (unless forcing refresh)
            if not force_refresh:
                cached_data = self.cache_manager.get_cached_data(cache_key, cache_ttl)
                if cached_data:
                    self.logger.info(f"Using cached data for {cache_key}")
                    cache_hit = True
                    cache_source = "cache"
                    self.cache_manager.record_cache_hit('regular')
                    
                    # Record performance metrics
                    duration = time.time() - start_time
                    self.cache_manager.record_fetch_time(duration)
                    self._log_fetch_performance(cache_key, duration, cache_hit, cache_source)
                    
                    return cached_data
            
            # Fetch fresh data
            self.logger.info(f"Fetching fresh data for {cache_key}")
            result = api_fetch_method()
            cache_source = "api_fresh"
            
            # Store in cache if we got data
            if result:
                self.cache_manager.save_cache(cache_key, result)
                self.cache_manager.record_cache_miss('regular')
            else:
                self.logger.warning(f"No data returned for {cache_key}")
            
            # Record performance metrics
            duration = time.time() - start_time
            self.cache_manager.record_fetch_time(duration)
            
            # Log performance
            self._log_fetch_performance(cache_key, duration, cache_hit, cache_source)
            
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            self.logger.error(f"Error fetching data for {cache_key} after {duration:.2f}s: {e}")
            self.cache_manager.record_fetch_time(duration)
            raise
    
    def _log_fetch_performance(self, cache_key: str, duration: float, cache_hit: bool, cache_source: str):
        """
        Log detailed performance metrics for fetch operations.
        
        Args:
            cache_key: Cache key that was accessed
            duration: Fetch operation duration in seconds
            cache_hit: Whether this was a cache hit
            cache_source: Source of the data (cache, api_fresh, etc.)
        """
        # Log basic performance info
        self.logger.info(f"Fetch completed for {cache_key} in {duration:.2f}s "
                        f"(cache_hit={cache_hit}, source={cache_source})")
        
        # Log detailed metrics every 10 operations
        if hasattr(self, '_fetch_count'):
            self._fetch_count += 1
        else:
            self._fetch_count = 1
            
        if self._fetch_count % 10 == 0:
            metrics = self.cache_manager.get_cache_metrics()
            self.logger.info(f"Cache Performance Summary - "
                           f"Hit Rate: {metrics['cache_hit_rate']:.2%}, "
                           f"API Calls Saved: {metrics['api_calls_saved']}, "
                           f"Avg Fetch Time: {metrics['average_fetch_time']:.2f}s")
    
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
    
    def clear_cache_for_key(self, cache_key: str):
        """Clear cache for a specific key."""
        self.cache_manager.clear_cache(cache_key)
        self.logger.info(f"Cleared cache for {cache_key}")
    
    def get_cache_info(self, cache_key: str) -> Dict[str, Any]:
        """
        Get information about a cached item.
        
        Args:
            cache_key: Cache key to check
            
        Returns:
            Dictionary with cache information
        """
        # This would need to be implemented in CacheManager
        # For now, just return basic info
        return {
            'key': cache_key,
            'exists': self.cache_manager.get_cached_data(cache_key, 0) is not None,
            'ttl': 'unknown'  # Would need to be implemented
        }
