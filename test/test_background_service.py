#!/usr/bin/env python3
"""
Test script for Background Data Service with NFL Manager

This script tests the background threading functionality for NFL season data fetching.
It demonstrates how the background service prevents blocking the main display loop.
"""

import os
import sys
import time
import logging
from datetime import datetime

# Add src directory to path (go up one level from test/ to find src/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from background_data_service import BackgroundDataService, get_background_service
from cache_manager import CacheManager
from config_manager import ConfigManager
from nfl_managers import BaseNFLManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s:%(name)s:%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

class MockDisplayManager:
    """Mock display manager for testing."""
    def __init__(self):
        self.matrix = type('Matrix', (), {'width': 64, 'height': 32})()
        self.image = None
    
    def update_display(self):
        pass
    
    def format_date_with_ordinal(self, date):
        return date.strftime("%B %d")

def test_background_service():
    """Test the background data service functionality."""
    logger.info("Starting Background Data Service Test")
    
    # Initialize components
    config_manager = ConfigManager()
    cache_manager = CacheManager()
    
    # Test configuration for NFL
    test_config = {
        "nfl_scoreboard": {
            "enabled": True,
            "test_mode": False,
            "background_service": {
                "enabled": True,
                "max_workers": 2,
                "request_timeout": 15,
                "max_retries": 2,
                "priority": 2
            },
            "favorite_teams": ["TB", "DAL"],
            "display_modes": {
                "nfl_live": True,
                "nfl_recent": True,
                "nfl_upcoming": True
            }
        },
        "timezone": "America/Chicago"
    }
    
    # Initialize mock display manager
    display_manager = MockDisplayManager()
    
    # Initialize NFL manager
    nfl_manager = BaseNFLManager(test_config, display_manager, cache_manager)
    
    logger.info("NFL Manager initialized with background service")
    
    # Test 1: Check if background service is enabled
    logger.info(f"Background service enabled: {nfl_manager.background_enabled}")
    if nfl_manager.background_service:
        logger.info(f"Background service workers: {nfl_manager.background_service.max_workers}")
    
    # Test 2: Test data fetching with background service
    logger.info("Testing NFL data fetch with background service...")
    start_time = time.time()
    
    # This should start a background fetch and return partial data immediately
    data = nfl_manager._fetch_nfl_api_data(use_cache=False)
    
    fetch_time = time.time() - start_time
    logger.info(f"Initial fetch completed in {fetch_time:.2f} seconds")
    
    if data and 'events' in data:
        logger.info(f"Received {len(data['events'])} events (partial data)")
        
        # Show some sample events
        for i, event in enumerate(data['events'][:3]):
            logger.info(f"  Event {i+1}: {event.get('id', 'N/A')}")
    else:
        logger.warning("No data received from initial fetch")
    
    # Test 3: Wait for background fetch to complete
    logger.info("Waiting for background fetch to complete...")
    max_wait_time = 30  # 30 seconds max wait
    wait_start = time.time()
    
    while time.time() - wait_start < max_wait_time:
        # Check if background fetch is complete
        current_year = datetime.now().year
        if current_year in nfl_manager.background_fetch_requests:
            request_id = nfl_manager.background_fetch_requests[current_year]
            result = nfl_manager.background_service.get_result(request_id)
            
            if result and result.success:
                logger.info(f"Background fetch completed successfully in {result.fetch_time:.2f}s")
                logger.info(f"Full dataset contains {len(result.data)} events")
                break
            elif result and not result.success:
                logger.error(f"Background fetch failed: {result.error}")
                break
        else:
            # Check if we have cached data now
            cached_data = cache_manager.get(f"nfl_schedule_{current_year}")
            if cached_data:
                logger.info(f"Found cached data with {len(cached_data)} events")
                break
        
        time.sleep(1)
        logger.info("Still waiting for background fetch...")
    
    # Test 4: Test subsequent fetch (should use cache)
    logger.info("Testing subsequent fetch (should use cache)...")
    start_time = time.time()
    
    data2 = nfl_manager._fetch_nfl_api_data(use_cache=True)
    
    fetch_time2 = time.time() - start_time
    logger.info(f"Subsequent fetch completed in {fetch_time2:.2f} seconds")
    
    if data2 and 'events' in data2:
        logger.info(f"Received {len(data2['events'])} events from cache")
    
    # Test 5: Show service statistics
    if nfl_manager.background_service:
        stats = nfl_manager.background_service.get_statistics()
        logger.info("Background Service Statistics:")
        for key, value in stats.items():
            logger.info(f"  {key}: {value}")
    
    # Test 6: Test with background service disabled
    logger.info("Testing with background service disabled...")
    
    test_config_disabled = test_config.copy()
    test_config_disabled["nfl_scoreboard"]["background_service"]["enabled"] = False
    
    nfl_manager_disabled = BaseNFLManager(test_config_disabled, display_manager, cache_manager)
    logger.info(f"Background service enabled: {nfl_manager_disabled.background_enabled}")
    
    start_time = time.time()
    data3 = nfl_manager_disabled._fetch_nfl_api_data(use_cache=False)
    fetch_time3 = time.time() - start_time
    
    logger.info(f"Synchronous fetch completed in {fetch_time3:.2f} seconds")
    if data3 and 'events' in data3:
        logger.info(f"Received {len(data3['events'])} events synchronously")
    
    logger.info("Background Data Service Test Complete!")
    
    # Cleanup
    if nfl_manager.background_service:
        nfl_manager.background_service.shutdown(wait=True, timeout=10)

if __name__ == "__main__":
    try:
        test_background_service()
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)
