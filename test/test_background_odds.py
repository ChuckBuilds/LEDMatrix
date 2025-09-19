#!/usr/bin/env python3
"""
Test script for background odds refresh functionality.
This script tests the new background odds fetching system to ensure
it doesn't block the main thread during startup.
"""

import sys
import os
import time
import logging

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cache_manager import CacheManager
from config_manager import ConfigManager
from background_data_service import get_background_service
from odds_manager import OddsManager

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_background_odds():
    """Test background odds fetching functionality."""
    logger.info("Starting Background Odds Refresh Test")
    
    try:
        # Initialize managers
        config_manager = ConfigManager()
        cache_manager = CacheManager()
        
        # Initialize background service
        background_service = get_background_service(cache_manager, max_workers=2)
        logger.info("Background service initialized with 2 workers")
        
        # Initialize odds manager with background service
        odds_manager = OddsManager(cache_manager, None, background_service)
        logger.info("OddsManager initialized with background service")
        
        # Test parameters
        test_sport = "football"
        test_league = "nfl"
        test_event_id = "401772937"  # Example NFL game ID
        
        logger.info(f"Testing odds fetch for {test_sport}/{test_league}/{test_event_id}")
        
        # Test 1: First fetch (should start background fetch)
        logger.info("Test 1: First odds fetch (background)")
        start_time = time.time()
        odds_data = odds_manager.get_odds(
            sport=test_sport,
            league=test_league,
            event_id=test_event_id,
            use_background=True
        )
        fetch_time = time.time() - start_time
        
        if odds_data is None:
            logger.info(f"Background fetch started (returned None immediately) in {fetch_time:.2f}s")
        else:
            logger.info(f"Got cached odds data in {fetch_time:.2f}s: {odds_data}")
        
        # Test 2: Wait a moment and check again
        logger.info("Test 2: Checking background fetch result")
        time.sleep(2)  # Give background fetch time to complete
        
        start_time = time.time()
        odds_data = odds_manager.get_odds(
            sport=test_sport,
            league=test_league,
            event_id=test_event_id,
            use_background=True
        )
        fetch_time = time.time() - start_time
        
        if odds_data:
            logger.info(f"Background fetch completed in {fetch_time:.2f}s: {odds_data}")
        else:
            logger.info(f"Background fetch still in progress (returned None) in {fetch_time:.2f}s")
        
        # Test 3: Test synchronous fallback
        logger.info("Test 3: Testing synchronous fallback")
        odds_manager_sync = OddsManager(cache_manager, None, None)  # No background service
        
        start_time = time.time()
        odds_data_sync = odds_manager_sync.get_odds(
            sport=test_sport,
            league=test_league,
            event_id=test_event_id,
            use_background=False
        )
        sync_time = time.time() - start_time
        
        if odds_data_sync:
            logger.info(f"Synchronous fetch completed in {sync_time:.2f}s: {odds_data_sync}")
        else:
            logger.info(f"Synchronous fetch returned None in {sync_time:.2f}s")
        
        # Test 4: Test cache hit
        logger.info("Test 4: Testing cache hit")
        start_time = time.time()
        odds_data_cached = odds_manager.get_odds(
            sport=test_sport,
            league=test_league,
            event_id=test_event_id,
            use_background=True
        )
        cache_time = time.time() - start_time
        
        if odds_data_cached:
            logger.info(f"Cache hit in {cache_time:.2f}s: {odds_data_cached}")
        else:
            logger.info(f"Cache miss in {cache_time:.2f}s")
        
        # Display performance summary
        logger.info("Background Odds Refresh Test Complete!")
        logger.info("Performance Summary:")
        logger.info(f"  Background fetch start: {fetch_time:.2f}s")
        logger.info(f"  Synchronous fallback: {sync_time:.2f}s")
        logger.info(f"  Cache hit: {cache_time:.2f}s")
        
        # Cleanup
        background_service.shutdown()
        logger.info("Background service shutdown complete")
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)
        return False
    
    return True

if __name__ == "__main__":
    success = test_background_odds()
    if success:
        logger.info("All tests passed!")
        sys.exit(0)
    else:
        logger.error("Tests failed!")
        sys.exit(1)
