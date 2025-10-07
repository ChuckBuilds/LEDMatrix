#!/usr/bin/env python3
"""
Test script for flight map background functionality.
This tests the geographical background rendering without requiring actual hardware.
"""

import sys
import os
import time
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from flight_manager import FlightMapManager
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

def test_map_background():
    """Test the map background functionality."""
    print("Testing Flight Map Background...")
    
    # Create mock components
    display_manager = MockDisplayManager(192, 96)
    cache_manager = CacheManager()
    
    # Test configuration
    config = {
        'flight_tracker': {
            'enabled': True,
            'center_latitude': 27.9506,  # Tampa, FL
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
    
    # Create flight manager
    flight_manager = FlightMapManager(config, display_manager, cache_manager)
    
    print(f"Map background enabled: {flight_manager.map_bg_enabled}")
    print(f"Tile provider: {flight_manager.tile_provider}")
    print(f"Center: ({flight_manager.center_lat}, {flight_manager.center_lon})")
    print(f"Map radius: {flight_manager.map_radius_miles} miles")
    
    # Test map background generation
    print("\nGenerating map background...")
    start_time = time.time()
    
    map_bg = flight_manager._get_map_background(flight_manager.center_lat, flight_manager.center_lon)
    
    end_time = time.time()
    print(f"Map background generation took {end_time - start_time:.2f} seconds")
    
    if map_bg:
        print(f"Map background size: {map_bg.size}")
        print(f"Map background mode: {map_bg.mode}")
        
        # Save test image
        test_output = Path("test_map_background.png")
        map_bg.save(test_output)
        print(f"Saved test map background to: {test_output}")
        
        # Test display method
        print("\nTesting display method...")
        flight_manager.display()
        
        if display_manager.image:
            print(f"Display image size: {display_manager.image.size}")
            display_output = Path("test_flight_display.png")
            display_manager.image.save(display_output)
            print(f"Saved test display to: {display_output}")
        else:
            print("No display image generated")
    else:
        print("Failed to generate map background")
    
    print("\nTest completed!")

def test_different_locations():
    """Test map background with different locations."""
    print("\nTesting different locations...")
    
    # Test locations
    locations = [
        ("Tampa, FL", 27.9506, -82.4572),
        ("New York, NY", 40.7128, -74.0060),
        ("Los Angeles, CA", 34.0522, -118.2437),
        ("London, UK", 51.5074, -0.1278),
        ("Tokyo, Japan", 35.6762, 139.6503)
    ]
    
    display_manager = MockDisplayManager(192, 96)
    cache_manager = CacheManager()
    
    for name, lat, lon in locations:
        print(f"\nTesting {name} ({lat}, {lon})...")
        
        config = {
            'flight_tracker': {
                'enabled': True,
                'center_latitude': lat,
                'center_longitude': lon,
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
        
        flight_manager = FlightMapManager(config, display_manager, cache_manager)
        
        start_time = time.time()
        map_bg = flight_manager._get_map_background(lat, lon)
        end_time = time.time()
        
        if map_bg:
            print(f"  ✓ Generated map background in {end_time - start_time:.2f}s")
            
            # Save location-specific test
            safe_name = name.replace(", ", "_").replace(" ", "_")
            output_file = Path(f"test_map_{safe_name}.png")
            map_bg.save(output_file)
            print(f"  ✓ Saved to: {output_file}")
        else:
            print(f"  ✗ Failed to generate map background")

if __name__ == "__main__":
    print("Flight Map Background Test")
    print("=" * 40)
    
    try:
        test_map_background()
        test_different_locations()
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
