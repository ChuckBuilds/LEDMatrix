#!/usr/bin/env python3
"""
Debug script for map background to see what tiles are being fetched and how they look.
"""

import sys
import os
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

def test_map_background_debug():
    """Test and debug the map background generation."""
    print("Testing Map Background Generation (Debug Mode)")
    print("=" * 50)
    
    # Create mock components
    display_manager = MockDisplayManager(192, 96)
    cache_manager = CacheManager()
    
    # Test configuration for Tampa, FL area
    config = {
        'flight_tracker': {
            'enabled': True,
            'center_latitude': 27.9506,  # Tampa, FL
            'center_longitude': -82.4572,
            'map_radius_miles': 10,  # 10 mile radius
            'zoom_factor': 1.0,
            'map_background': {
                'enabled': True,
                'tile_provider': 'osm',
                'tile_size': 256,
                'cache_ttl_hours': 24,
                'fade_intensity': 0.7,  # More visible
                'update_on_location_change': True,
                'disable_on_cache_error': False
            }
        }
    }
    
    # Create flight manager
    flight_manager = FlightMapManager(config, display_manager, cache_manager)
    
    print(f"Center: ({flight_manager.center_lat}, {flight_manager.center_lon})")
    print(f"Radius: {flight_manager.map_radius_miles} miles")
    print(f"Display: {flight_manager.display_width}x{flight_manager.display_height}")
    print(f"Tile provider: {flight_manager.tile_provider}")
    print(f"Fade intensity: {flight_manager.fade_intensity}")
    
    # Test tile coordinate conversion
    print("\nTesting tile coordinate conversion...")
    zoom = 11  # Good zoom for 10 mile radius
    center_x, center_y = flight_manager._latlon_to_tile_coords(flight_manager.center_lat, flight_manager.center_lon, zoom)
    print(f"Center tile coordinates: ({center_x}, {center_y}) at zoom {zoom}")
    
    # Test tile URL generation
    print("\nTesting tile URL generation...")
    test_tiles = [
        (center_x, center_y, zoom),
        (center_x - 1, center_y, zoom),
        (center_x, center_y - 1, zoom),
        (center_x + 1, center_y + 1, zoom)
    ]
    
    for x, y, z in test_tiles:
        url = flight_manager._get_tile_url(x, y, z)
        print(f"  Tile ({x},{y},{z}): {url}")
    
    # Test map background generation
    print("\nGenerating map background...")
    try:
        map_bg = flight_manager._get_map_background(flight_manager.center_lat, flight_manager.center_lon)
        
        if map_bg:
            print(f"✓ Map background generated successfully")
            print(f"  Size: {map_bg.size}")
            print(f"  Mode: {map_bg.mode}")
            
            # Save the background for inspection
            output_file = Path("debug_map_background.png")
            map_bg.save(output_file)
            print(f"  Saved to: {output_file}")
            
            # Test individual tile fetching
            print("\nTesting individual tile fetching...")
            for i, (x, y, z) in enumerate(test_tiles[:2]):  # Test first 2 tiles
                print(f"  Fetching tile {i+1}: ({x},{y},{z})")
                tile = flight_manager._fetch_tile(x, y, z)
                if tile:
                    print(f"    ✓ Tile fetched successfully: {tile.size}")
                    tile_output = Path(f"debug_tile_{i+1}_{x}_{y}_{z}.png")
                    tile.save(tile_output)
                    print(f"    Saved to: {tile_output}")
                else:
                    print(f"    ✗ Failed to fetch tile")
            
        else:
            print("✗ Failed to generate map background")
            
    except Exception as e:
        print(f"✗ Error generating map background: {e}")
        import traceback
        traceback.print_exc()
    
    print("\nDebug test completed!")

def test_different_zoom_levels():
    """Test different zoom levels to see which shows Florida best."""
    print("\nTesting Different Zoom Levels")
    print("=" * 30)
    
    display_manager = MockDisplayManager(192, 96)
    cache_manager = CacheManager()
    
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
    
    flight_manager = FlightMapManager(config, display_manager, cache_manager)
    
    # Test different zoom levels
    zoom_levels = [8, 9, 10, 11, 12]
    
    for zoom in zoom_levels:
        print(f"\nTesting zoom level {zoom}...")
        
        # Calculate tile coordinates
        center_x, center_y = flight_manager._latlon_to_tile_coords(flight_manager.center_lat, flight_manager.center_lon, zoom)
        print(f"  Center tile: ({center_x}, {center_y})")
        
        # Test fetching center tile
        tile = flight_manager._fetch_tile(center_x, center_y, zoom)
        if tile:
            print(f"  ✓ Center tile fetched: {tile.size}")
            output_file = Path(f"debug_zoom_{zoom}_center.png")
            tile.save(output_file)
            print(f"  Saved to: {output_file}")
        else:
            print(f"  ✗ Failed to fetch center tile")

if __name__ == "__main__":
    try:
        test_map_background_debug()
        test_different_zoom_levels()
    except Exception as e:
        print(f"Debug test failed: {e}")
        import traceback
        traceback.print_exc()
