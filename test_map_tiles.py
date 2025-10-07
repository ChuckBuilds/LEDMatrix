#!/usr/bin/env python3
"""
Simple test to check if map tiles are being fetched correctly.
"""

import requests
import sys
from pathlib import Path

def test_tile_urls():
    """Test different tile URLs to see which ones work."""
    print("Testing Map Tile URLs")
    print("=" * 30)
    
    # Test coordinates for Tampa, FL area
    x, y, zoom = 135, 211, 9  # Approximate tile coordinates for Tampa area
    
    tile_providers = {
        'osm': f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png",
        'carto_a': f"https://cartodb-basemaps-a.global.ssl.fastly.net/light_all/{zoom}/{x}/{y}.png",
        'carto_b': f"https://cartodb-basemaps-b.global.ssl.fastly.net/light_all/{zoom}/{x}/{y}.png",
        'carto_c': f"https://cartodb-basemaps-c.global.ssl.fastly.net/light_all/{zoom}/{x}/{y}.png",
        'stamen': f"https://stamen-tiles.a.ssl.fastly.net/terrain/{zoom}/{x}/{y}.png"
    }
    
    for provider, url in tile_providers.items():
        print(f"\nTesting {provider}:")
        print(f"  URL: {url}")
        
        try:
            response = requests.get(url, timeout=10)
            print(f"  Status: {response.status_code}")
            print(f"  Content-Type: {response.headers.get('content-type', 'unknown')}")
            print(f"  Size: {len(response.content)} bytes")
            
            if response.status_code == 200:
                # Check if it's actually an image
                content_type = response.headers.get('content-type', '').lower()
                if 'image' in content_type:
                    print(f"  ✓ Valid image tile")
                    
                    # Save the tile for inspection
                    output_file = Path(f"test_tile_{provider}_{x}_{y}_{zoom}.png")
                    with open(output_file, 'wb') as f:
                        f.write(response.content)
                    print(f"  ✓ Saved to: {output_file}")
                else:
                    print(f"  ✗ Not an image: {content_type}")
                    
                    # Check if it's an error page
                    if len(response.content) < 1000:
                        try:
                            text_content = response.content.decode('utf-8', errors='ignore')
                            if 'age policy' in text_content.lower() or 'error' in text_content.lower():
                                print(f"  ✗ Error page detected: {text_content[:100]}...")
                        except:
                            pass
            else:
                print(f"  ✗ HTTP Error: {response.status_code}")
                
        except Exception as e:
            print(f"  ✗ Request failed: {e}")

def test_different_coordinates():
    """Test with different coordinates to see if it's a location issue."""
    print("\n" + "=" * 50)
    print("Testing Different Coordinates")
    print("=" * 30)
    
    # Test different areas
    test_coords = [
        ("Tampa, FL", 135, 211, 9),
        ("New York, NY", 192, 245, 9),
        ("Los Angeles, CA", 88, 204, 9),
        ("London, UK", 256, 170, 9)
    ]
    
    for name, x, y, zoom in test_coords:
        print(f"\nTesting {name} ({x}, {y}, {zoom}):")
        
        url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"
        try:
            response = requests.get(url, timeout=10)
            print(f"  Status: {response.status_code}, Size: {len(response.content)} bytes")
            
            if response.status_code == 200 and len(response.content) > 1000:
                print(f"  ✓ Valid tile")
            else:
                print(f"  ✗ Invalid response")
                
        except Exception as e:
            print(f"  ✗ Failed: {e}")

if __name__ == "__main__":
    test_tile_urls()
    test_different_coordinates()
    print("\nTest completed!")
