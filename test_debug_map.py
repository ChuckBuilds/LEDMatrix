#!/usr/bin/env python3
"""
Debug test to see what's happening with the map background.
"""

import sys
import os
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

def test_debug_map():
    """Test map background with debug output."""
    print("Debug Map Background Test")
    print("=" * 30)
    
    try:
        from src.flight_manager import FlightMapManager
        
        # Mock the dependencies
        class MockDisplayManager:
            def __init__(self):
                self.matrix = type('Matrix', (), {'width': 192, 'height': 96})()
        
        class MockCacheManager:
            def __init__(self):
                self.cache_dir = "/tmp/ledmatrix_cache"
        
        # Configuration
        config = {
            'flight_tracker': {
                'enabled': True,
                'center_latitude': 27.9506,
                'center_longitude': -82.4572,
                'map_radius_miles': 10,
                'map_background': {
                    'enabled': True,
                    'tile_provider': 'osm',
                    'fade_intensity': 0.7
                }
            }
        }
        
        # Create flight manager
        display_manager = MockDisplayManager()
        cache_manager = MockCacheManager()
        
        flight_manager = FlightMapManager(config, display_manager, cache_manager)
        
        print(f"✓ Flight manager created")
        print(f"  Center: ({flight_manager.center_lat}, {flight_manager.center_lon})")
        print(f"  Radius: {flight_manager.map_radius_miles} miles")
        print(f"  Display: {flight_manager.display_width}x{flight_manager.display_height}")
        
        # Test map background generation
        print("\nGenerating map background...")
        map_bg = flight_manager._get_map_background(flight_manager.center_lat, flight_manager.center_lon)
        
        if map_bg:
            print(f"✓ Map background generated: {map_bg.size}")
            
            # Save the final result
            output_file = Path("debug_final_map.png")
            map_bg.save(output_file)
            print(f"✓ Saved final map to: {output_file}")
            
            # Check for debug files
            debug_files = ["debug_composite.png", "debug_cropped.png"]
            for debug_file in debug_files:
                if Path(debug_file).exists():
                    print(f"✓ Found debug file: {debug_file}")
                else:
                    print(f"✗ Debug file not found: {debug_file}")
        else:
            print("✗ Failed to generate map background")
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_debug_map()
