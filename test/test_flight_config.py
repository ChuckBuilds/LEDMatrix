#!/usr/bin/env python3
"""
Test script for flight tracker configuration with map background.
"""

import json
import sys
import os
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def test_config_loading():
    """Test loading the updated configuration."""
    print("Testing Flight Tracker Configuration...")
    
    # Load the template config
    config_path = Path("../config/config.template.json")
    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        return False
    
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    # Check if flight_tracker section exists
    if 'flight_tracker' not in config:
        print("✗ flight_tracker section not found in config")
        return False
    
    flight_config = config['flight_tracker']
    print("✓ flight_tracker section found")
    
    # Check for map_background section
    if 'map_background' not in flight_config:
        print("✗ map_background section not found")
        return False
    
    map_bg_config = flight_config['map_background']
    print("✓ map_background section found")
    
    # Check required fields
    required_fields = [
        'enabled',
        'tile_provider', 
        'tile_size',
        'cache_ttl_hours',
        'fade_intensity',
        'update_on_location_change'
    ]
    
    for field in required_fields:
        if field not in map_bg_config:
            print(f"✗ Missing required field: {field}")
            return False
        print(f"✓ {field}: {map_bg_config[field]}")
    
    # Test different tile providers
    providers = ['osm', 'carto', 'carto_dark']
    current_provider = map_bg_config.get('tile_provider', 'osm')
    if current_provider in providers:
        print(f"✓ Valid tile provider: {current_provider}")
    else:
        print(f"⚠ Unknown tile provider: {current_provider}")
    
    # Test fade intensity range
    fade_intensity = map_bg_config.get('fade_intensity', 0.3)
    if 0.0 <= fade_intensity <= 1.0:
        print(f"✓ Valid fade intensity: {fade_intensity}")
    else:
        print(f"⚠ Fade intensity should be between 0.0 and 1.0: {fade_intensity}")
    
    print("\nConfiguration test completed successfully!")
    return True

def test_config_validation():
    """Test configuration validation logic."""
    print("\nTesting Configuration Validation...")
    
    # Test valid configurations
    valid_configs = [
        {
            'map_background': {
                'enabled': True,
                'tile_provider': 'osm',
                'tile_size': 256,
                'cache_ttl_hours': 24,
                'fade_intensity': 0.3,
                'update_on_location_change': True
            }
        },
        {
            'map_background': {
                'enabled': False,
                'tile_provider': 'carto',
                'tile_size': 512,
                'cache_ttl_hours': 12,
                'fade_intensity': 0.5,
                'update_on_location_change': False
            }
        }
    ]
    
    for i, config in enumerate(valid_configs):
        print(f"\nTesting valid config {i+1}:")
        map_bg = config['map_background']
        
        # Simulate the configuration loading logic
        enabled = map_bg.get('enabled', True)
        provider = map_bg.get('tile_provider', 'osm')
        tile_size = map_bg.get('tile_size', 256)
        cache_ttl = map_bg.get('cache_ttl_hours', 24)
        fade = map_bg.get('fade_intensity', 0.3)
        update_on_change = map_bg.get('update_on_location_change', True)
        
        print(f"  enabled: {enabled}")
        print(f"  provider: {provider}")
        print(f"  tile_size: {tile_size}")
        print(f"  cache_ttl: {cache_ttl}")
        print(f"  fade_intensity: {fade}")
        print(f"  update_on_change: {update_on_change}")
        
        # Validate ranges
        if not (0.0 <= fade <= 1.0):
            print(f"  ✗ Invalid fade intensity: {fade}")
        else:
            print(f"  ✓ Valid fade intensity: {fade}")
        
        if tile_size not in [256, 512]:
            print(f"  ⚠ Unusual tile size: {tile_size}")
        else:
            print(f"  ✓ Standard tile size: {tile_size}")
    
    print("\nConfiguration validation completed!")

if __name__ == "__main__":
    print("Flight Tracker Configuration Test")
    print("=" * 40)
    
    try:
        success = test_config_loading()
        if success:
            test_config_validation()
        else:
            print("Configuration loading failed!")
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
