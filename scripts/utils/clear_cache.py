#!/usr/bin/env python3
"""
Cache clearing utility for LEDMatrix
This script allows manual clearing of specific cache keys or all cache data.
"""

import os
import sys
import json
import argparse
from pathlib import Path

# Add the src directory to the path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from cache_manager import CacheManager

def list_cache_keys(cache_manager):
    """List all available cache keys."""
    cache_dir = cache_manager.cache_dir
    if not cache_dir or not os.path.exists(cache_dir):
        print(f"Cache directory does not exist: {cache_dir}")
        return []
    
    cache_files = []
    for file in os.listdir(cache_dir):
        if file.endswith('.json'):
            cache_files.append(file[:-5])  # Remove .json extension
    
    return cache_files

def clear_specific_cache(cache_manager, key):
    """Clear a specific cache key."""
    try:
        cache_manager.clear_cache(key)
        print(f"✓ Cleared cache key: {key}")
        return True
    except Exception as e:
        print(f"✗ Error clearing cache key '{key}': {e}")
        return False

def clear_all_cache(cache_manager):
    """Clear all cache data."""
    try:
        cache_manager.clear_cache()
        print("✓ Cleared all cache data")
        return True
    except Exception as e:
        print(f"✗ Error clearing all cache: {e}")
        return False

def show_cache_info(cache_manager, key=None):
    """Show information about cache entries."""
    if key:
        try:
            data = cache_manager.get(key)
            if data is not None:
                print(f"Cache key '{key}' exists with data type: {type(data)}")
                if isinstance(data, dict):
                    print(f"  Keys: {list(data.keys())}")
                    if 'games' in data:
                        print(f"  Number of games: {len(data['games']) if isinstance(data['games'], dict) else 'N/A'}")
                elif isinstance(data, list):
                    print(f"  Number of items: {len(data)}")
                else:
                    print(f"  Data: {str(data)[:100]}...")
            else:
                print(f"Cache key '{key}' does not exist or is expired")
        except Exception as e:
            print(f"Error checking cache key '{key}': {e}")
    else:
        # Show all cache keys
        keys = list_cache_keys(cache_manager)
        if keys:
            print("Available cache keys:")
            for key in sorted(keys):
                print(f"  - {key}")
        else:
            print("No cache keys found")

def main():
    parser = argparse.ArgumentParser(description='Clear LEDMatrix cache data')
    parser.add_argument('--list', '-l', action='store_true', 
                       help='List all available cache keys')
    parser.add_argument('--clear-all', '-a', action='store_true',
                       help='Clear all cache data')
    parser.add_argument('--clear', '-c', type=str, metavar='KEY',
                       help='Clear a specific cache key')
    parser.add_argument('--info', '-i', type=str, metavar='KEY',
                       help='Show information about a specific cache key')
    
    args = parser.parse_args()
    
    # Initialize cache manager
    cache_manager = CacheManager()
    
    if args.list:
        show_cache_info(cache_manager)
    elif args.clear_all:
        print("Clearing all cache data...")
        clear_all_cache(cache_manager)
    elif args.clear:
        print(f"Clearing cache key: {args.clear}")
        clear_specific_cache(cache_manager, args.clear)
    elif args.info:
        show_cache_info(cache_manager, args.info)
    else:
        # Default: show available options
        print("LEDMatrix Cache Utility")
        print("=" * 30)
        print()
        print("Available commands:")
        print("  --list, -l          List all cache keys")
        print("  --clear-all, -a     Clear all cache data")
        print("  --clear KEY, -c     Clear specific cache key")
        print("  --info KEY, -i      Show info about cache key")
        print()
        print("Examples:")
        print("  python clear_cache.py --list")
        print("  python clear_cache.py --clear milb_live_api_data")
        print("  python clear_cache.py --clear-all")
        print("  python clear_cache.py --info milb_upcoming_api_data")
        print()
        
        # Show current cache status
        show_cache_info(cache_manager)

if __name__ == "__main__":
    main()
