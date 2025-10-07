#!/usr/bin/env python3
"""
Simple test for flight manager map background.
"""

import sys
import os
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

def test_flight_manager():
    """Test flight manager with minimal setup."""
    print("Simple Flight Manager Test")
    print("=" * 30)
    
    try:
        # Import the flight manager directly
        from src.flight_manager import BaseFlightManager
        
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
        
        flight_manager = BaseFlightManager(config, display_manager, cache_manager)
        
        print(f"✓ Flight manager created")
        print(f"  Center: ({flight_manager.center_lat}, {flight_manager.center_lon})")
        print(f"  Radius: {flight_manager.map_radius_miles} miles")
        print(f"  Tile provider: {flight_manager.tile_provider}")
        
        # Test tile coordinate conversion
        zoom = 9
        x, y = flight_manager._latlon_to_tile_coords(flight_manager.center_lat, flight_manager.center_lon, zoom)
        print(f"  Tile coordinates: ({x}, {y}) at zoom {zoom}")
        
        # Test tile URL
        url = flight_manager._get_tile_url(x, y, zoom)
        print(f"  Tile URL: {url}")
        
        # Test fetching a single tile
        print("\nTesting single tile fetch...")
        tile = flight_manager._fetch_tile(x, y, zoom)
        if tile:
            print(f"✓ Tile fetched successfully: {tile.size}")
            
            # Save the tile
            output_file = Path("test_single_tile.png")
            tile.save(output_file)
            print(f"✓ Saved to: {output_file}")
        else:
            print("✗ Failed to fetch tile")
        
        # Test map background generation
        print("\nTesting map background generation...")
        map_bg = flight_manager._get_map_background(flight_manager.center_lat, flight_manager.center_lon)
        if map_bg:
            print(f"✓ Map background generated: {map_bg.size}")
            
            # Save the background
            output_file = Path("test_map_background.png")
            map_bg.save(output_file)
            print(f"✓ Saved to: {output_file}")
        else:
            print("✗ Failed to generate map background")
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_flight_manager()
