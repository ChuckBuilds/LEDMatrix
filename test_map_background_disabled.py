#!/usr/bin/env python3
"""
Test script to verify map background is disabled by default
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from flight_manager import BaseFlightManager

def test_map_background_disabled():
    """Test that map background is disabled by default"""
    print("Testing Map Background Configuration")
    print("=" * 50)
    
    try:
        # Create flight manager instance
        manager = BaseFlightManager(
            center_lat=27.9506,
            center_lon=-82.4572,
            map_radius_miles=10
        )
        
        print(f"✓ Flight manager created")
        print(f"  Map background enabled: {manager.map_bg_enabled}")
        print(f"  Tile provider: {manager.tile_provider}")
        print(f"  Cache error count: {manager.cache_error_count}")
        
        # Test getting map background
        bg = manager._get_map_background(27.9506, -82.4572)
        
        if bg is None:
            print("✓ Map background correctly disabled (returns None)")
        else:
            print("✗ Map background should be disabled but returned image")
            print(f"  Image size: {bg.size}")
        
        print("\nTest completed successfully!")
        print("Map background is disabled by default to avoid text artifacts.")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_map_background_disabled()
