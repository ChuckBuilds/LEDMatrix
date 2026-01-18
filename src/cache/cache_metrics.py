"""
Cache Metrics

Tracks cache performance metrics including hit rates, miss rates, and fetch times.
"""

import threading
import logging
from typing import Dict, Any, Optional


class CacheMetrics:
    """Tracks cache performance metrics."""
    
    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize cache metrics tracker.
        
        Args:
            logger: Optional logger instance
        """
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._metrics: Dict[str, Any] = {
            'hits': 0,
            'misses': 0,
            'api_calls_saved': 0,
            'background_hits': 0,
            'background_misses': 0,
            'total_fetch_time': 0.0,
            'fetch_count': 0,
            # Disk cleanup metrics
            'last_disk_cleanup': 0.0,
            'total_files_cleaned': 0,
            'total_space_freed_mb': 0.0,
            'last_cleanup_duration_sec': 0.0
        }
    
    def record_hit(self, cache_type: str = 'regular') -> None:
        """
        Record a cache hit.
        
        Args:
            cache_type: Type of cache hit ('regular' or 'background')
        """
        with self._lock:
            if cache_type == 'background':
                self._metrics['background_hits'] += 1
            else:
                self._metrics['hits'] += 1
    
    def record_miss(self, cache_type: str = 'regular') -> None:
        """
        Record a cache miss.
        
        Args:
            cache_type: Type of cache miss ('regular' or 'background')
        """
        with self._lock:
            if cache_type == 'background':
                self._metrics['background_misses'] += 1
            else:
                self._metrics['misses'] += 1
            self._metrics['api_calls_saved'] += 1
    
    def record_fetch_time(self, duration: float) -> None:
        """
        Record fetch operation duration.
        
        Args:
            duration: Duration in seconds
        """
        with self._lock:
            self._metrics['total_fetch_time'] += duration
            self._metrics['fetch_count'] += 1
    
    def record_disk_cleanup(self, files_cleaned: int, space_freed_mb: float, duration_sec: float) -> None:
        """
        Record disk cleanup operation results.
        
        Args:
            files_cleaned: Number of files deleted
            space_freed_mb: Space freed in megabytes
            duration_sec: Duration of cleanup operation in seconds
        """
        import time
        with self._lock:
            self._metrics['last_disk_cleanup'] = time.time()
            self._metrics['total_files_cleaned'] += files_cleaned
            self._metrics['total_space_freed_mb'] += space_freed_mb
            self._metrics['last_cleanup_duration_sec'] = duration_sec
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get current cache performance metrics.
        
        Returns:
            Dictionary with cache metrics
        """
        with self._lock:
            total_hits = self._metrics['hits'] + self._metrics['background_hits']
            total_misses = self._metrics['misses'] + self._metrics['background_misses']
            total_requests = total_hits + total_misses
            
            avg_fetch_time = (self._metrics['total_fetch_time'] / 
                             self._metrics['fetch_count']) if self._metrics['fetch_count'] > 0 else 0.0
            
            return {
                'total_requests': total_requests,
                'cache_hit_rate': total_hits / total_requests if total_requests > 0 else 0.0,
                'background_hit_rate': (self._metrics['background_hits'] / 
                                       (self._metrics['background_hits'] + self._metrics['background_misses'])
                                       if (self._metrics['background_hits'] + self._metrics['background_misses']) > 0 else 0.0),
                'api_calls_saved': self._metrics['api_calls_saved'],
                'average_fetch_time': avg_fetch_time,
                'total_fetch_time': self._metrics['total_fetch_time'],
                'fetch_count': self._metrics['fetch_count'],
                # Disk cleanup metrics
                'last_disk_cleanup': self._metrics['last_disk_cleanup'],
                'total_files_cleaned': self._metrics['total_files_cleaned'],
                'total_space_freed_mb': self._metrics['total_space_freed_mb'],
                'last_cleanup_duration_sec': self._metrics['last_cleanup_duration_sec']
            }
    
    def log_metrics(self) -> None:
        """Log current cache performance metrics."""
        metrics = self.get_metrics()
        self.logger.info("Cache Performance - Hit Rate: %.2f%%, Background Hit Rate: %.2f%%, "
                        "API Calls Saved: %d, Avg Fetch Time: %.2fs",
                        metrics['cache_hit_rate'] * 100,
                        metrics['background_hit_rate'] * 100,
                        metrics['api_calls_saved'],
                        metrics['average_fetch_time'])

