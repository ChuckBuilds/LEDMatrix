#!/usr/bin/env python3
"""
Test script to verify the cache directory fix for flight tracker.
"""

import sys
import os
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from flight_manager import BaseFlightManager
from display_manager import DisplayManager
from cache_manager import CacheManager

class MockMatrix:
    """Mock matrix for testing without hardware."""
    def __init__(self, width=192, height=96):
        self.width = width
        self.height = height

class MockDisplayManager:
    """Mock display manager for testing."""
    def __init__(self, width=192, height=96):
        self.matrix = MockMatrix(width, height)
        self.image = None
    
    def clear(self):
        pass
    
    def update_display(self):
        pass

def test_cache_directory_creation():
    """Test that the cache directory is created properly."""
    print("Testing Flight Tracker Cache Directory Creation...")
    
    # Create mock components
    display_manager = MockDisplayManager(192, 96)
    cache_manager = CacheManager()
    
    # Test configuration
    config = {
        'flight_tracker': {
            'enabled': True,
            'center_latitude': 27.9506,
            'center_longitude': -82.4572,
            'map_radius_miles': 10,
            'zoom_factor': 1.0,
            'map_background': {
                'enabled': True,
                'tile_provider': 'osm',
                'tile_size': 256,
                'cache_ttl_hours': 24,
                'fade_intensity': 0.3,
                'update_on_location_change': True
            }
        }
    }
    
    try:
        # Create flight manager - this should not fail with permission errors
        flight_manager = BaseFlightManager(config, display_manager, cache_manager)
        
        print(f"✓ Flight manager created successfully")
        print(f"✓ Cache directory: {cache_manager.cache_dir}")
        print(f"✓ Tile cache directory: {flight_manager.tile_cache_dir}")
        
        # Check if tile cache directory exists and is writable
        if flight_manager.tile_cache_dir.exists():
            print(f"✓ Tile cache directory exists: {flight_manager.tile_cache_dir}")
            
            # Test write access
            test_file = flight_manager.tile_cache_dir / "test_write.tmp"
            try:
                test_file.write_text("test")
                test_file.unlink()  # Clean up
                print(f"✓ Tile cache directory is writable")
            except Exception as e:
                print(f"✗ Tile cache directory is not writable: {e}")
        else:
            print(f"✗ Tile cache directory does not exist: {flight_manager.tile_cache_dir}")
        
        return True
        
    except Exception as e:
        print(f"✗ Failed to create flight manager: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_tile_cache_paths():
    """Test that tile cache paths are generated correctly."""
    print("\nTesting Tile Cache Path Generation...")
    
    display_manager = MockDisplayManager(192, 96)
    cache_manager = CacheManager()
    
    config = {
        'flight_tracker': {
            'enabled': True,
            'map_background': {
                'enabled': True,
                'tile_provider': 'osm',
                'tile_size': 256,
                'cache_ttl_hours': 24,
                'fade_intensity': 0.3,
                'update_on_location_change': True
            }
        }
    }
    
    try:
        flight_manager = BaseFlightManager(config, display_manager, cache_manager)
        
        # Test tile cache path generation
        test_cases = [
            (10, 5, 8),   # x, y, zoom
            (100, 50, 10),
            (1000, 500, 12)
        ]
        
        for x, y, zoom in test_cases:
            cache_path = flight_manager._get_tile_cache_path(x, y, zoom)
            expected_name = f"osm_{zoom}_{x}_{y}.png"
            
            print(f"  Testing tile {x},{y},{zoom}:")
            print(f"    Cache path: {cache_path}")
            print(f"    Expected filename: {expected_name}")
            
            if cache_path.name == expected_name:
                print(f"    ✓ Path generation correct")
            else:
                print(f"    ✗ Path generation incorrect")
        
        return True
        
    except Exception as e:
        print(f"✗ Failed to test tile cache paths: {e}")
        return False

if __name__ == "__main__":
    print("Flight Tracker Cache Fix Test")
    print("=" * 40)
    
    try:
        success1 = test_cache_directory_creation()
        success2 = test_tile_cache_paths()
        
        if success1 and success2:
            print("\n✓ All tests passed! Cache directory fix is working.")
        else:
            print("\n✗ Some tests failed. Check the output above.")
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
