#!/usr/bin/env python3
"""
Test script to check cache permissions for map tiles.
"""

import os
import sys
from pathlib import Path

def test_cache_permissions():
    """Test cache directory permissions."""
    print("Testing Cache Permissions for Map Tiles")
    print("=" * 40)
    
    # Check common cache directories
    cache_dirs = [
        "/var/cache/ledmatrix",
        os.path.expanduser("~/.ledmatrix_cache"),
        "/tmp/ledmatrix_cache"
    ]
    
    for cache_dir in cache_dirs:
        print(f"\nTesting cache directory: {cache_dir}")
        
        if not os.path.exists(cache_dir):
            print(f"  ✗ Directory does not exist")
            continue
        
        print(f"  ✓ Directory exists")
        
        # Check permissions
        if os.access(cache_dir, os.R_OK):
            print(f"  ✓ Directory is readable")
        else:
            print(f"  ✗ Directory is not readable")
        
        if os.access(cache_dir, os.W_OK):
            print(f"  ✓ Directory is writable")
        else:
            print(f"  ✗ Directory is not writable")
        
        # Test map_tiles subdirectory
        map_tiles_dir = Path(cache_dir) / "map_tiles"
        print(f"  Testing map_tiles subdirectory: {map_tiles_dir}")
        
        try:
            map_tiles_dir.mkdir(parents=True, exist_ok=True)
            print(f"    ✓ Can create map_tiles directory")
            
            # Test write access
            test_file = map_tiles_dir / "test_write.tmp"
            test_file.write_text("test")
            test_file.unlink()
            print(f"    ✓ Can write to map_tiles directory")
            
        except PermissionError as e:
            print(f"    ✗ Cannot create/write to map_tiles directory: {e}")
        except Exception as e:
            print(f"    ✗ Error with map_tiles directory: {e}")
    
    print("\n" + "=" * 40)
    print("Cache Permission Test Complete")
    print("\nIf you see permission errors, run:")
    print("  chmod +x fix_map_cache_permissions.sh")
    print("  ./fix_map_cache_permissions.sh")

if __name__ == "__main__":
    test_cache_permissions()
