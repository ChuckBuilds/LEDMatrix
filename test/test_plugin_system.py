#!/usr/bin/env python3
"""
Test script for Plugin System validation.

This script tests the plugin system functionality without needing
the actual LED matrix hardware.
"""

import sys
import os
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.plugin_system import PluginManager, PluginStoreManager, BasePlugin
from unittest.mock import Mock


def print_header(text):
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def test_plugin_discovery():
    """Test plugin discovery functionality."""
    print_header("Test 1: Plugin Discovery")
    
    try:
        # Create mock managers
        mock_config_manager = Mock()
        mock_config_manager.load_config.return_value = {}
        mock_display_manager = Mock()
        mock_display_manager.width = 128
        mock_display_manager.height = 64
        mock_cache_manager = Mock()
        
        # Initialize plugin manager
        plugin_manager = PluginManager(
            plugins_dir="plugins",
            config_manager=mock_config_manager,
            display_manager=mock_display_manager,
            cache_manager=mock_cache_manager
        )
        
        # Discover plugins
        discovered = plugin_manager.discover_plugins()
        
        print(f"[OK] Discovered {len(discovered)} plugin(s): {', '.join(discovered)}")
        
        # Show plugin manifests
        for plugin_id in discovered:
            manifest = plugin_manager.plugin_manifests.get(plugin_id, {})
            print(f"\n  Plugin: {manifest.get('name', plugin_id)}")
            print(f"    ID: {plugin_id}")
            print(f"    Last Updated: {manifest.get('last_updated', 'unknown')}")
            print(f"    Author: {manifest.get('author', 'unknown')}")
            print(f"    Description: {manifest.get('description', 'N/A')}")
        
        return True
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_plugin_loading():
    """Test plugin loading functionality."""
    print_header("Test 2: Plugin Loading")
    
    try:
        # Create mock managers
        mock_config_manager = Mock()
        mock_config_manager.load_config.return_value = {
            "hello-world": {
                "enabled": True,
                "message": "Test Message",
                "show_time": True,
                "color": [255, 0, 0],
                "display_duration": 10
            }
        }
        mock_display_manager = Mock()
        mock_display_manager.width = 128
        mock_display_manager.height = 64
        mock_cache_manager = Mock()
        
        # Initialize plugin manager
        plugin_manager = PluginManager(
            plugins_dir="plugins",
            config_manager=mock_config_manager,
            display_manager=mock_display_manager,
            cache_manager=mock_cache_manager
        )
        
        # Discover and load plugins
        discovered = plugin_manager.discover_plugins()
        
        if "hello-world" not in discovered:
            print("[WARN] Hello World plugin not found. Skipping load test.")
            return True
        
        # Load the hello-world plugin
        success = plugin_manager.load_plugin("hello-world")
        
        if success:
            print("[OK] Hello World plugin loaded successfully")
            
            # Get plugin instance
            plugin = plugin_manager.get_plugin("hello-world")
            if plugin:
                print(f"  Plugin instance: {type(plugin).__name__}")
                print(f"  Plugin ID: {plugin.plugin_id}")
                print(f"  Enabled: {plugin.enabled}")
                print(f"  Message: {plugin.message}")
                
                # Test update method
                print("\n  Testing update() method...")
                plugin.update()
                print("  [OK] Update completed")
                
                # Test display method (won't actually display without real hardware)
                print("\n  Testing display() method...")
                plugin.display(force_clear=True)
                print("  [OK] Display completed (mock)")
                
                # Test get_info method
                info = plugin.get_info()
                print(f"\n  Plugin info: {json.dumps(info, indent=2)}")
                
            return True
        else:
            print("[FAIL] Failed to load Hello World plugin")
            return False
            
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_plugin_manifest_validation():
    """Test manifest validation."""
    print_header("Test 3: Manifest Validation")
    
    try:
        manifest_path = Path("plugins/hello-world/manifest.json")
        
        if not manifest_path.exists():
            print("[WARN] Hello World manifest not found")
            return True
        
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
        
        # Check required fields
        required_fields = ['id', 'name', 'entry_point', 'class_name']
        missing = [field for field in required_fields if field not in manifest]
        
        if missing:
            print(f"[FAIL] Missing required fields: {', '.join(missing)}")
            return False
        
        print("[OK] All required fields present")
        
        # Validate field types
        if not isinstance(manifest.get('id'), str):
            print("[FAIL] 'id' must be a string")
            return False
        
        print("[OK] Field types valid")
        
        # Check entry point exists
        entry_point = Path("plugins/hello-world") / manifest['entry_point']
        if not entry_point.exists():
            print(f"[FAIL] Entry point not found: {entry_point}")
            return False
        
        print(f"[OK] Entry point exists: {manifest['entry_point']}")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_base_plugin_interface():
    """Test BasePlugin interface."""
    print_header("Test 4: BasePlugin Interface")
    
    try:
        # Import hello-world plugin
        sys.path.insert(0, "plugins/hello-world")
        from manager import HelloWorldPlugin
        
        # Create mock managers
        mock_config = {
            "enabled": True,
            "message": "Test",
            "display_duration": 10
        }
        mock_display_manager = Mock()
        mock_display_manager.width = 128
        mock_display_manager.height = 64
        mock_cache_manager = Mock()
        mock_plugin_manager = Mock()
        
        # Instantiate plugin
        plugin = HelloWorldPlugin(
            plugin_id="hello-world",
            config=mock_config,
            display_manager=mock_display_manager,
            cache_manager=mock_cache_manager,
            plugin_manager=mock_plugin_manager
        )
        
        # Check that it's a BasePlugin
        if not isinstance(plugin, BasePlugin):
            print("[FAIL] Plugin does not inherit from BasePlugin")
            return False
        
        print("[OK] Plugin inherits from BasePlugin")
        
        # Check required methods exist
        required_methods = ['update', 'display', 'validate_config', 'cleanup', 'get_info']
        missing_methods = [m for m in required_methods if not hasattr(plugin, m)]
        
        if missing_methods:
            print(f"[FAIL] Missing methods: {', '.join(missing_methods)}")
            return False
        
        print("[OK] All required methods present")
        
        # Test validate_config
        if not plugin.validate_config():
            print("[FAIL] Configuration validation failed")
            return False
        
        print("[OK] Configuration validation passed")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_store_manager():
    """Test PluginStoreManager basic functionality."""
    print_header("Test 5: Plugin Store Manager")
    
    try:
        store_manager = PluginStoreManager(plugins_dir="plugins")
        
        print("[OK] Store manager initialized")
        
        # Note: We don't actually fetch from network in tests
        # Just verify the manager was created successfully
        print("  Store manager ready for plugin installation")
        
        return True
        
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("  LEDMatrix Plugin System Test Suite")
    print("=" * 60)
    
    results = {
        "Plugin Discovery": test_plugin_discovery(),
        "Plugin Loading": test_plugin_loading(),
        "Manifest Validation": test_plugin_manifest_validation(),
        "BasePlugin Interface": test_base_plugin_interface(),
        "Store Manager": test_store_manager()
    }
    
    # Print summary
    print_header("Test Results Summary")
    
    passed = sum(1 for result in results.values() if result)
    total = len(results)
    
    for test_name, result in results.items():
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status} - {test_name}")
    
    print(f"\n  Total: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n  All tests passed!")
        return 0
    else:
        print(f"\n  WARNING: {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())

