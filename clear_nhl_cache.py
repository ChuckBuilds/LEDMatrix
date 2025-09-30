#!/usr/bin/env python3
"""
Script to clear NHL cache so managers will fetch fresh data.
"""

import sys
import os
import json
from datetime import datetime

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def clear_nhl_cache():
    """Clear NHL cache to force fresh data fetch."""
    print("Clearing NHL cache...")
    
    try:
        from cache_manager import CacheManager
        
        # Create cache manager
        cache_manager = CacheManager()
        
        # Clear NHL cache for current season
        now = datetime.now()
        season_year = now.year
        if now.month < 9:
            season_year = now.year - 1
        
        cache_key = f"nhl_api_data_{season_year}"
        print(f"Clearing cache key: {cache_key}")
        
        # Clear the cache
        cache_manager.clear_cache(cache_key)
        print(f"Successfully cleared cache for {cache_key}")
        
        # Also clear any other NHL-related cache keys
        nhl_keys = [
            f"nhl_api_data_{season_year}",
            f"nhl_api_data_{season_year-1}",
            f"nhl_api_data_{season_year+1}",
            "nhl_live_games",
            "nhl_recent_games", 
            "nhl_upcoming_games"
        ]
        
        for key in nhl_keys:
            try:
                cache_manager.clear_cache(key)
                print(f"Cleared cache key: {key}")
            except:
                pass  # Key might not exist
        
        print("NHL cache cleared successfully!")
        print("NHL managers will now fetch fresh data from ESPN API.")
        
    except ImportError as e:
        print(f"Could not import cache manager: {e}")
        print("This script needs to be run on the Raspberry Pi where the cache manager is available.")
    except Exception as e:
        print(f"Error clearing cache: {e}")

def main():
    """Main function."""
    print("=" * 50)
    print("NHL Cache Clearer")
    print("=" * 50)
    
    clear_nhl_cache()
    
    print("\n" + "=" * 50)
    print("Cache clearing complete!")
    print("=" * 50)

if __name__ == "__main__":
    main()
