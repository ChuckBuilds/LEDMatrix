#!/usr/bin/env python3
"""
Simple test to verify the flight map background is working.
"""

import sys
import os
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_simple_map_background():
    """Simple test of map background generation."""
    print("Simple Flight Map Background Test")
    print("=" * 40)
    
    try:
        from flight_manager import FlightMapManager
        from display_manager import DisplayManager
        from cache_manager import CacheManager
        
        class MockMatrix:
            def __init__(self, width=192, height=96):
                self.width = width
                self.height = height

        class MockDisplayManager:
            def __init__(self, width=192, height=96):
                self.matrix = MockMatrix(width, height)
                self.image = None
            
            def clear(self):
                pass
            
            def update_display(self):
                pass
        
        # Create components
        display_manager = MockDisplayManager(192, 96)
        cache_manager = CacheManager()
        
        # Configuration for Tampa, FL
        config = {
            'flight_tracker': {
                'enabled': True,
                'center_latitude': 27.9506,
                'center_longitude': -82.4572,
                'map_radius_miles': 10,
                'map_background': {
                    'enabled': True,
                    'tile_provider': 'carto',
                    'fade_intensity': 0.7
                }
            }
        }
        
        # Create flight manager
        flight_manager = FlightMapManager(config, display_manager, cache_manager)
        
        print(f"✓ Flight manager created")
        print(f"  Center: ({flight_manager.center_lat}, {flight_manager.center_lon})")
        print(f"  Radius: {flight_manager.map_radius_miles} miles")
        print(f"  Tile provider: {flight_manager.tile_provider}")
        print(f"  Fade intensity: {flight_manager.fade_intensity}")
        
        # Test map background generation
        print("\nGenerating map background...")
        map_bg = flight_manager._get_map_background(flight_manager.center_lat, flight_manager.center_lon)
        
        if map_bg:
            print(f"✓ Map background generated: {map_bg.size}")
            
            # Save for inspection
            output_file = Path("simple_map_test.png")
            map_bg.save(output_file)
            print(f"✓ Saved to: {output_file}")
            
            # Test display method
            print("\nTesting display method...")
            flight_manager.display()
            
            if display_manager.image:
                display_output = Path("simple_display_test.png")
                display_manager.image.save(display_output)
                print(f"✓ Display saved to: {display_output}")
            else:
                print("✗ No display image generated")
        else:
            print("✗ Failed to generate map background")
        
        print("\nTest completed!")
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_simple_map_background()
