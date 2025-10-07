#!/usr/bin/env python3
"""
Test the improved tile fetching with fallback URLs.
"""

import sys
import os
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

def test_tile_fallback():
    """Test tile fetching with fallback URLs."""
    print("Testing Tile Fallback System")
    print("=" * 30)
    
    try:
        from src.flight_manager import BaseFlightManager
        
        # Mock the dependencies
        class MockDisplayManager:
            def __init__(self):
                self.matrix = type('Matrix', (), {'width': 192, 'height': 96})()
        
        class MockCacheManager:
            def __init__(self):
                self.cache_dir = "/tmp/ledmatrix_cache"
        
        # Test with CartoDB provider (which has issues)
        config = {
            'flight_tracker': {
                'enabled': True,
                'center_latitude': 27.9506,
                'center_longitude': -82.4572,
                'map_radius_miles': 10,
                'map_background': {
                    'enabled': True,
                    'tile_provider': 'carto',  # This has issues, should fallback to OSM
                    'fade_intensity': 0.7
                }
            }
        }
        
        # Create flight manager
        display_manager = MockDisplayManager()
        cache_manager = MockCacheManager()
        
        flight_manager = BaseFlightManager(config, display_manager, cache_manager)
        
        print(f"✓ Flight manager created with {flight_manager.tile_provider} provider")
        
        # Test tile coordinate conversion
        zoom = 9
        x, y = flight_manager._latlon_to_tile_coords(flight_manager.center_lat, flight_manager.center_lon, zoom)
        print(f"  Tile coordinates: ({x}, {y}) at zoom {zoom}")
        
        # Test URL generation
        urls = flight_manager._get_tile_urls(x, y, zoom)
        print(f"  Fallback URLs ({len(urls)}):")
        for i, url in enumerate(urls):
            print(f"    {i+1}. {url}")
        
        # Test fetching a single tile
        print("\nTesting tile fetch with fallback...")
        tile = flight_manager._fetch_tile(x, y, zoom)
        if tile:
            print(f"✓ Tile fetched successfully: {tile.size}")
            
            # Save the tile
            output_file = Path("test_fallback_tile.png")
            tile.save(output_file)
            print(f"✓ Saved to: {output_file}")
        else:
            print("✗ Failed to fetch tile with all fallback URLs")
        
        # Test map background generation
        print("\nTesting map background generation...")
        map_bg = flight_manager._get_map_background(flight_manager.center_lat, flight_manager.center_lon)
        if map_bg:
            print(f"✓ Map background generated: {map_bg.size}")
            
            # Save the background
            output_file = Path("test_fallback_map.png")
            map_bg.save(output_file)
            print(f"✓ Saved to: {output_file}")
        else:
            print("✗ Failed to generate map background")
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_tile_fallback()
