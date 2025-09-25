"""
Background Data Service for LEDMatrix

This service provides background threading capabilities for season data fetching
to prevent blocking the main display loop. It's designed to be used across
all sport managers for consistent background data management.

Key Features:
- Thread-safe data caching
- Automatic retry logic with exponential backoff
- Configurable timeouts and intervals
- Graceful error handling
- Progress tracking and logging
- Memory-efficient data storage
- Integration with new API extractor system
"""

import os
import time
import logging
import threading
import requests
from typing import Dict, Any, Optional, List, Callable, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
import json
import queue
from concurrent.futures import ThreadPoolExecutor, Future
import weakref
from src.cache_manager import CacheManager
from src.base_classes.data_sources import ESPNDataSource, MLBAPIDataSource, MILBAPIDataSource, SoccerAPIDataSource
from src.base_classes.api_extractors import ESPNFootballExtractor, ESPNBaseballExtractor, ESPNHockeyExtractor, SoccerAPIExtractor
# Configure logging
logger = logging.getLogger(__name__)

class FetchStatus(Enum):
    """Status of background fetch operations."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class FetchRequest:
    """Represents a background fetch request."""
    id: str
    sport: str
    year: int
    cache_key: str
    url: str
    params: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    retry_count: int = 0
    max_retries: int = 3
    priority: int = 1  # Higher number = higher priority
    callback: Optional[Callable] = None
    created_at: float = field(default_factory=time.time)
    status: FetchStatus = FetchStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None

@dataclass
class FetchResult:
    """Result of a background fetch operation."""
    request_id: str
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    cached: bool = False
    fetch_time: float = 0.0
    retry_count: int = 0

class BackgroundDataService:
    """
    Background data service for fetching season data without blocking the main thread.
    
    This service manages a pool of background threads to fetch data asynchronously,
    with intelligent caching, retry logic, and progress tracking.
    """
    
    def __init__(self, cache_manager: CacheManager, max_workers: int = 3, request_timeout: int = 30):
        """
        Initialize the background data service.
        
        Args:
            cache_manager: Cache manager instance for storing fetched data
            max_workers: Maximum number of background threads
            request_timeout: Default timeout for HTTP requests
        """
        self.cache_manager = cache_manager
        self.max_workers = max_workers
        self.request_timeout = request_timeout
        
        # Thread management
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="BackgroundData")
        self.active_requests: Dict[str, FetchRequest] = {}
        self.completed_requests: Dict[str, FetchResult] = {}
        self.request_queue = queue.PriorityQueue()
        
        # Thread safety
        self._lock = threading.RLock()
        self._shutdown = False
        
        # Statistics
        self.stats = {
            'total_requests': 0,
            'completed_requests': 0,
            'failed_requests': 0,
            'cached_hits': 0,
            'cache_misses': 0,
            'total_fetch_time': 0.0,
            'average_fetch_time': 0.0
        }
        
        # Session for HTTP requests
        self.session = requests.Session()
        self.session.mount('http://', requests.adapters.HTTPAdapter(max_retries=3))
        self.session.mount('https://', requests.adapters.HTTPAdapter(max_retries=3))
        
        # Default headers
        self.default_headers = {
            'User-Agent': 'LEDMatrix/1.0 (https://github.com/yourusername/LEDMatrix)',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }
        
        # Initialize data sources and extractors
        self._initialize_data_sources()
        self._initialize_api_extractors()
        
        logger.info(f"BackgroundDataService initialized with {max_workers} workers")
    
    def _initialize_data_sources(self):
        """Initialize data sources for different sports."""
        self.data_sources = {
            'espn': ESPNDataSource(logger),
            'mlb_api': MLBAPIDataSource(logger),
            'milb_api': MILBAPIDataSource(logger),
            'soccer_api': SoccerAPIDataSource(logger)
        }
        logger.debug("Initialized data sources for ESPN, MLB API, MILB API, and Soccer API")
    
    def _initialize_api_extractors(self):
        """Initialize API extractors for different sports."""
        self.api_extractors = {
            'football': ESPNFootballExtractor(logger),
            'baseball': ESPNBaseballExtractor(logger),
            'hockey': ESPNHockeyExtractor(logger),
            'soccer': SoccerAPIExtractor(logger)
        }
        logger.debug("Initialized API extractors for football, baseball, hockey, and soccer")
    
    def submit_extractor_fetch_request(self, 
                                     sport: str, 
                                     league: str,
                                     data_source_type: str = 'espn',
                                     fetch_type: str = 'live_games',
                                     cache_key: str = None,
                                     priority: int = 1,
                                     callback: Optional[Callable] = None) -> str:
        """
        Submit a fetch request using the new API extractor system.
        
        Args:
            sport: Sport identifier (e.g., 'football', 'baseball', 'hockey')
            league: League identifier (e.g., 'nfl', 'mlb', 'nhl')
            data_source_type: Type of data source ('espn', 'mlb_api', 'soccer_api')
            fetch_type: Type of data to fetch ('live_games', 'schedule', 'standings')
            cache_key: Cache key for storing results
            priority: Priority for the request (1=highest, 5=lowest)
            callback: Optional callback function
            
        Returns:
            Request ID for tracking
        """
        request_id = f"{sport}_{league}_{fetch_type}_{int(time.time())}"
        
        if cache_key is None:
            cache_key = f"{sport}_{league}_{fetch_type}_{datetime.now().strftime('%Y%m%d')}"
        
        # Check cache first
        if self.cache_manager.get(cache_key):
            logger.debug(f"Cache hit for {sport} {league} {fetch_type} data")
            return request_id
        
        # Create fetch request with extractor info
        request = FetchRequest(
            id=request_id,
            sport=sport,
            year=datetime.now().year,
            cache_key=cache_key,
            url="",  # Not used for extractor-based fetching
            params={'league': league, 'data_source_type': data_source_type, 'fetch_type': fetch_type},
            headers={},
            timeout=self.request_timeout,
            max_retries=3,
            priority=priority,
            callback=callback
        )
        
        with self._lock:
            self.active_requests[request_id] = request
            self.stats['total_requests'] += 1
            self.stats['cache_misses'] += 1
        
        # Submit to executor
        future = self.executor.submit(self._fetch_data_with_extractor, request)
        
        logger.info(f"Submitted extractor-based fetch request {request_id} for {sport} {league} {fetch_type}")
        return request_id
    
    def _fetch_data_with_extractor(self, request: FetchRequest) -> FetchResult:
        """
        Worker function that performs data fetching using API extractors.
        
        Args:
            request: Fetch request to process
            
        Returns:
            Fetch result with processed data or error information
        """
        start_time = time.time()
        result = FetchResult(request_id=request.id, success=False, retry_count=request.retry_count)
        
        try:
            with self._lock:
                request.status = FetchStatus.IN_PROGRESS
            
            logger.info(f"Starting extractor-based fetch for {request.sport} {request.params.get('league', '')}")
            
            # Get data source and extractor
            data_source_type = request.params.get('data_source_type', 'espn')
            league = request.params.get('league', '')
            fetch_type = request.params.get('fetch_type', 'live_games')
            
            data_source = self.data_sources.get(data_source_type)
            extractor = self.api_extractors.get(request.sport)
            
            if not data_source:
                raise ValueError(f"Unknown data source type: {data_source_type}")
            if not extractor:
                raise ValueError(f"No extractor available for sport: {request.sport}")
            
            # Fetch raw data based on type
            raw_data = []
            if fetch_type == 'live_games':
                raw_data = data_source.fetch_live_games(request.sport, league)
            elif fetch_type == 'schedule':
                # For schedule, we need date range - using today for now
                today = datetime.now()
                raw_data = data_source.fetch_schedule(request.sport, league, (today, today))
            elif fetch_type == 'standings':
                standings_data = data_source.fetch_standings(request.sport, league)
                raw_data = [standings_data] if standings_data else []
            
            # Process data through extractor
            processed_data = []
            for item in raw_data:
                if fetch_type == 'standings':
                    # Standings don't need extraction, use as-is
                    processed_data.append(item)
                else:
                    # Extract game details
                    extracted = extractor.extract_game_details(item)
                    if extracted:
                        processed_data.append(extracted)
            
            # Create final data structure
            final_data = {
                'events': processed_data,
                'metadata': {
                    'sport': request.sport,
                    'league': league,
                    'data_source_type': data_source_type,
                    'fetch_type': fetch_type,
                    'timestamp': datetime.now().isoformat(),
                    'count': len(processed_data)
                }
            }
            
            # Cache the processed data
            self.cache_manager.set(request.cache_key, final_data)
            
            # Update request status
            with self._lock:
                request.status = FetchStatus.COMPLETED
                request.result = final_data
            
            # Create successful result
            fetch_time = time.time() - start_time
            result = FetchResult(
                request_id=request.id,
                success=True,
                data=final_data,
                fetch_time=fetch_time,
                retry_count=request.retry_count
            )
            
            logger.info(f"Successfully fetched and processed {request.sport} {league} {fetch_type} data in {fetch_time:.2f}s ({len(processed_data)} items)")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to fetch {request.sport} {request.params.get('league', '')} data: {error_msg}")
            
            with self._lock:
                request.status = FetchStatus.FAILED
                request.error = error_msg
            
            result = FetchResult(
                request_id=request.id,
                success=False,
                error=error_msg,
                fetch_time=time.time() - start_time,
                retry_count=request.retry_count
            )
            
            self.stats['failed_requests'] += 1
        
        # Store result
        with self._lock:
            self.completed_requests[request.id] = result
            if request.id in self.active_requests:
                del self.active_requests[request.id]
        
        # Update statistics
        self.stats['total_fetch_time'] += result.fetch_time
        self.stats['average_fetch_time'] = self.stats['total_fetch_time'] / max(1, self.stats['completed_requests'] + self.stats['failed_requests'])
        
        # Call callback if provided
        if request.callback:
            try:
                request.callback(result)
            except Exception as e:
                logger.error(f"Error in fetch callback: {e}")
        
        return result
    
    def get_sport_cache_key(self, sport: str, date_str: str = None) -> str:
        """
        Generate consistent cache keys for sports data.
        This ensures Recent/Upcoming managers and background service
        use the same cache keys.
        """
        # Use the centralized cache key generation from CacheManager
        from src.cache_manager import CacheManager
        cache_manager = CacheManager()
        return cache_manager.generate_sport_cache_key(sport, date_str)

    def submit_fetch_request(self, 
                           sport: str, 
                           year: int, 
                           url: str,
                           cache_key: str = None,
                           params: Optional[Dict[str, Any]] = None,
                           headers: Optional[Dict[str, str]] = None,
                           timeout: Optional[int] = None,
                           max_retries: int = 3,
                           priority: int = 1,
                           callback: Optional[Callable] = None) -> str:
        """
        Submit a background fetch request.
        
        Args:
            sport: Sport identifier (e.g., 'nfl', 'ncaafb')
            year: Year to fetch data for
            url: URL to fetch data from
            cache_key: Cache key for storing/retrieving data
            params: URL parameters
            headers: HTTP headers
            timeout: Request timeout
            max_retries: Maximum number of retries
            priority: Request priority (higher = more important)
            callback: Optional callback function when request completes
            
        Returns:
            Request ID for tracking the fetch operation
        """
        if self._shutdown:
            raise RuntimeError("BackgroundDataService is shutting down")
        
        # Generate cache key if not provided
        if cache_key is None:
            cache_key = self.get_sport_cache_key(sport)
        
        request_id = f"{sport}_{year}_{int(time.time() * 1000)}"
        
        # Check cache first
        cached_data = self.cache_manager.get(cache_key)
        if cached_data:
            with self._lock:
                self.stats['cached_hits'] += 1
                result = FetchResult(
                    request_id=request_id,
                    success=True,
                    data=cached_data,
                    cached=True,
                    fetch_time=0.0
                )
                self.completed_requests[request_id] = result
                
                if callback:
                    try:
                        callback(result)
                    except Exception as e:
                        logger.error(f"Error in callback for request {request_id}: {e}")
                
                logger.debug(f"Cache hit for {sport} {year} data")
                return request_id
        
        # Create fetch request
        request = FetchRequest(
            id=request_id,
            sport=sport,
            year=year,
            cache_key=cache_key,
            url=url,
            params=params or {},
            headers={**self.default_headers, **(headers or {})},
            timeout=timeout or self.request_timeout,
            max_retries=max_retries,
            priority=priority,
            callback=callback
        )
        
        with self._lock:
            self.active_requests[request_id] = request
            self.stats['total_requests'] += 1
            self.stats['cache_misses'] += 1
        
        # Submit to executor
        future = self.executor.submit(self._fetch_data_worker, request)
        
        logger.info(f"Submitted background fetch request {request_id} for {sport} {year}")
        return request_id
    
    def _fetch_data_worker(self, request: FetchRequest) -> FetchResult:
        """
        Worker function that performs the actual data fetching.
        
        Args:
            request: Fetch request to process
            
        Returns:
            Fetch result with data or error information
        """
        start_time = time.time()
        result = FetchResult(request_id=request.id, success=False, retry_count=request.retry_count)
        
        try:
            with self._lock:
                request.status = FetchStatus.IN_PROGRESS
            
            logger.info(f"Starting background fetch for {request.sport} {request.year}")
            
            # Perform HTTP request with retry logic
            response = self._make_request_with_retry(request)
            response.raise_for_status()
            
            # Parse response
            data = response.json()
            
            # Validate data structure
            if not isinstance(data, dict):
                raise ValueError(f"Expected dict response, got {type(data)}")
            
            if 'events' not in data:
                raise ValueError("Response missing 'events' field")
            
            # Validate events structure
            events = data.get('events', [])
            if not isinstance(events, list):
                raise ValueError(f"Expected events to be list, got {type(events)}")
            
            # Log data validation
            logger.debug(f"Validated {len(events)} events for {request.sport} {request.year}")
            
            # Cache the data
            self.cache_manager.set(request.cache_key, data)
            
            # Update request status
            with self._lock:
                request.status = FetchStatus.COMPLETED
                request.result = data
            
            # Create successful result
            fetch_time = time.time() - start_time
            result = FetchResult(
                request_id=request.id,
                success=True,
                data=data,
                fetch_time=fetch_time,
                retry_count=request.retry_count
            )
            
            logger.info(f"Successfully fetched {request.sport} {request.year} data in {fetch_time:.2f}s")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to fetch {request.sport} {request.year} data: {error_msg}")
            
            with self._lock:
                request.status = FetchStatus.FAILED
                request.error = error_msg
            
            result = FetchResult(
                request_id=request.id,
                success=False,
                error=error_msg,
                fetch_time=time.time() - start_time,
                retry_count=request.retry_count
            )
        
        finally:
            # Store result and clean up
            with self._lock:
                self.completed_requests[request.id] = result
                if request.id in self.active_requests:
                    del self.active_requests[request.id]
                
                # Update statistics
                if result.success:
                    self.stats['completed_requests'] += 1
                else:
                    self.stats['failed_requests'] += 1
                
                self.stats['total_fetch_time'] += result.fetch_time
                self.stats['average_fetch_time'] = (
                    self.stats['total_fetch_time'] / 
                    (self.stats['completed_requests'] + self.stats['failed_requests'])
                )
            
            # Call callback if provided
            if request.callback:
                try:
                    request.callback(result)
                except Exception as e:
                    logger.error(f"Error in callback for request {request.id}: {e}")
        
        return result
    
    def _make_request_with_retry(self, request: FetchRequest) -> requests.Response:
        """
        Make HTTP request with retry logic and exponential backoff.
        
        Args:
            request: Fetch request containing request details
            
        Returns:
            HTTP response
            
        Raises:
            requests.RequestException: If all retries fail
        """
        last_exception = None
        
        for attempt in range(request.max_retries + 1):
            try:
                response = self.session.get(
                    request.url,
                    params=request.params,
                    headers=request.headers,
                    timeout=request.timeout
                )
                return response
                
            except requests.RequestException as e:
                last_exception = e
                request.retry_count = attempt + 1
                
                if attempt < request.max_retries:
                    # Exponential backoff: 1s, 2s, 4s, 8s...
                    delay = 2 ** attempt
                    logger.warning(f"Request failed (attempt {attempt + 1}/{request.max_retries + 1}), retrying in {delay}s: {e}")
                    time.sleep(delay)
                else:
                    logger.error(f"All {request.max_retries + 1} attempts failed for {request.sport} {request.year}")
        
        raise last_exception
    
    def get_result(self, request_id: str) -> Optional[FetchResult]:
        """
        Get the result of a fetch request.
        
        Args:
            request_id: Request ID to get result for
            
        Returns:
            Fetch result if available, None otherwise
        """
        with self._lock:
            return self.completed_requests.get(request_id)
    
    def is_request_complete(self, request_id: str) -> bool:
        """
        Check if a request has completed.
        
        Args:
            request_id: Request ID to check
            
        Returns:
            True if request is complete, False otherwise
        """
        with self._lock:
            return request_id in self.completed_requests
    
    def get_request_status(self, request_id: str) -> Optional[FetchStatus]:
        """
        Get the status of a fetch request.
        
        Args:
            request_id: Request ID to get status for
            
        Returns:
            Request status if found, None otherwise
        """
        with self._lock:
            if request_id in self.active_requests:
                return self.active_requests[request_id].status
            elif request_id in self.completed_requests:
                result = self.completed_requests[request_id]
                return FetchStatus.COMPLETED if result.success else FetchStatus.FAILED
            return None
    
    def cancel_request(self, request_id: str) -> bool:
        """
        Cancel a pending or in-progress request.
        
        Args:
            request_id: Request ID to cancel
            
        Returns:
            True if request was cancelled, False if not found or already complete
        """
        with self._lock:
            if request_id in self.active_requests:
                request = self.active_requests[request_id]
                request.status = FetchStatus.CANCELLED
                del self.active_requests[request_id]
                logger.info(f"Cancelled request {request_id}")
                return True
            return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get service statistics.
        
        Returns:
            Dictionary containing service statistics
        """
        with self._lock:
            return {
                **self.stats,
                'active_requests': len(self.active_requests),
                'completed_requests_count': len(self.completed_requests),
                'queue_size': self.request_queue.qsize()
            }
    
    def clear_completed_requests(self, older_than_hours: int = 24):
        """
        Clear completed requests older than specified time.
        
        Args:
            older_than_hours: Clear requests older than this many hours
        """
        cutoff_time = time.time() - (older_than_hours * 3600)
        
        with self._lock:
            to_remove = []
            for request_id, result in self.completed_requests.items():
                # We don't store creation time in results, so we'll use a simple count-based approach
                # In a real implementation, you'd want to store timestamps
                if len(self.completed_requests) > 1000:  # Keep last 1000 results
                    to_remove.append(request_id)
            
            for request_id in to_remove:
                del self.completed_requests[request_id]
            
            if to_remove:
                logger.info(f"Cleared {len(to_remove)} old completed requests")
    
    def shutdown(self, wait: bool = True, timeout: int = 30):
        """
        Shutdown the background data service.
        
        Args:
            wait: Whether to wait for active requests to complete
            timeout: Maximum time to wait for shutdown
        """
        logger.info("Shutting down BackgroundDataService...")
        
        self._shutdown = True
        
        # Cancel all active requests
        with self._lock:
            for request_id in list(self.active_requests.keys()):
                self.cancel_request(request_id)
        
        # Shutdown executor with compatibility for older Python versions
        try:
            # Try with timeout parameter (Python 3.9+)
            self.executor.shutdown(wait=wait, timeout=timeout)
        except TypeError:
            # Fallback for older Python versions that don't support timeout
            if wait and timeout:
                # For older versions, we can't specify timeout, so just wait
                self.executor.shutdown(wait=True)
            else:
                self.executor.shutdown(wait=wait)
        
        logger.info("BackgroundDataService shutdown complete")
    
    def __del__(self):
        """Cleanup when service is destroyed."""
        if not self._shutdown:
            self.shutdown(wait=False, timeout=None)

# Global service instance
_background_service: Optional[BackgroundDataService] = None
_service_lock = threading.Lock()

def get_background_service(cache_manager=None, max_workers: int = 3) -> BackgroundDataService:
    """
    Get the global background data service instance.
    
    Args:
        cache_manager: Cache manager instance (required for first call)
        max_workers: Maximum number of background threads
        
    Returns:
        Background data service instance
    """
    global _background_service
    
    with _service_lock:
        if _background_service is None:
            if cache_manager is None:
                raise ValueError("cache_manager is required for first call to get_background_service")
            _background_service = BackgroundDataService(cache_manager, max_workers)
        
        return _background_service

def shutdown_background_service():
    """Shutdown the global background data service."""
    global _background_service
    
    with _service_lock:
        if _background_service is not None:
            _background_service.shutdown()
            _background_service = None
