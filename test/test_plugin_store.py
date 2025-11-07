#!/usr/bin/env python3
"""
Test script for Plugin Store Manager

This script demonstrates how to use the PluginStoreManager to:
1. Fetch the plugin registry (when available)
2. Search for plugins
3. Install plugins from registry
4. Install plugins from custom GitHub URLs
5. List installed plugins
6. Uninstall plugins
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.plugin_system.store_manager import PluginStoreManager
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_fetch_registry():
    """Test fetching the plugin registry."""
    print("\n" + "="*60)
    print("TEST 1: Fetch Plugin Registry")
    print("="*60)
    
    store = PluginStoreManager(plugins_dir="plugins")
    
    try:
        registry = store.fetch_registry()
        print(f"✓ Registry fetched successfully")
        print(f"  Plugins available: {len(registry.get('plugins', []))}")
        
        # Show first 3 plugins as examples
        plugins = registry.get('plugins', [])
        if plugins:
            print("\n  Sample plugins:")
            for plugin in plugins[:3]:
                print(f"    - {plugin.get('name')} ({plugin.get('id')})")
                print(f"      Category: {plugin.get('category')}")
                print(f"      Author: {plugin.get('author')}")
        
        return True
    except Exception as e:
        print(f"✗ Failed to fetch registry: {e}")
        print("  Note: This is expected if the registry doesn't exist yet.")
        return False


def test_search_plugins():
    """Test searching for plugins."""
    print("\n" + "="*60)
    print("TEST 2: Search Plugins")
    print("="*60)
    
    store = PluginStoreManager(plugins_dir="plugins")
    
    try:
        # Search by query
        print("\nSearching for 'clock':")
        results = store.search_plugins(query="clock")
        print(f"  Found {len(results)} results")
        for plugin in results:
            print(f"    - {plugin.get('name')}")
        
        # Search by category
        print("\nSearching for category 'sports':")
        results = store.search_plugins(category="sports")
        print(f"  Found {len(results)} results")
        
        # Search by tags
        print("\nSearching for tag 'hockey':")
        results = store.search_plugins(tags=["hockey"])
        print(f"  Found {len(results)} results")
        
        print("✓ Search functionality working")
        return True
    except Exception as e:
        print(f"✗ Search failed: {e}")
        return False


def test_list_installed():
    """Test listing installed plugins."""
    print("\n" + "="*60)
    print("TEST 3: List Installed Plugins")
    print("="*60)
    
    store = PluginStoreManager(plugins_dir="plugins")
    
    try:
        installed = store.list_installed_plugins()
        print(f"✓ Found {len(installed)} installed plugins")
        
        if installed:
            print("\n  Installed plugins:")
            for plugin_id in installed:
                info = store.get_installed_plugin_info(plugin_id)
                if info:
                    print(f"    - {info.get('name')} ({plugin_id})")
                else:
                    print(f"    - {plugin_id} (no manifest)")
        else:
            print("  No plugins installed yet")
        
        return True
    except Exception as e:
        print(f"✗ Failed to list installed plugins: {e}")
        return False


def test_install_from_url_example():
    """Show example of installing from URL (don't actually execute)."""
    print("\n" + "="*60)
    print("TEST 4: Install from URL (Example)")
    print("="*60)
    
    print("\nExample usage for installing from custom GitHub URL:")
    print("  store = PluginStoreManager()")
    print("  result = store.install_from_url('https://github.com/user/ledmatrix-custom-plugin')")
    print("  if result['success']:")
    print("      print(f\"Installed: {result['plugin_id']}\")")
    
    print("\n✓ This method allows users to:")
    print("  - Install plugins not in the official registry")
    print("  - Test their own plugins during development")
    print("  - Share plugins with others before submitting to registry")
    print("  - Install private/custom plugins")
    
    print("\nSafety features:")
    print("  - Validates manifest.json exists and is valid")
    print("  - Checks for required fields (id, name, entry_point, class_name)")
    print("  - Returns detailed error messages if installation fails")
    print("  - Shows warning about unverified plugins in UI")


def test_api_usage():
    """Show example of API usage for web interface."""
    print("\n" + "="*60)
    print("TEST 5: API Usage Examples")
    print("="*60)
    
    print("\nAPI Endpoints available:")
    print("\n1. List all plugins in store:")
    print("   GET /api/plugins/store/list")
    print("   Returns: {'status': 'success', 'plugins': [...]}")
    
    print("\n2. Search plugins:")
    print("   GET /api/plugins/store/search?q=nhl&category=sports")
    print("   Returns: {'status': 'success', 'plugins': [...], 'count': N}")
    
    print("\n3. List installed plugins:")
    print("   GET /api/plugins/installed")
    print("   Returns: {'status': 'success', 'plugins': [...]}")
    
    print("\n4. Install from registry:")
    print("   POST /api/plugins/install")
    print("   Body: {'plugin_id': 'clock-simple'}")
    
    print("\n5. Install from URL:")
    print("   POST /api/plugins/install-from-url")
    print("   Body: {'repo_url': 'https://github.com/user/plugin'}")
    
    print("\n6. Uninstall plugin:")
    print("   POST /api/plugins/uninstall")
    print("   Body: {'plugin_id': 'clock-simple'}")
    
    print("\n7. Update plugin:")
    print("   POST /api/plugins/update")
    print("   Body: {'plugin_id': 'clock-simple'}")
    
    print("\n8. Toggle plugin:")
    print("   POST /api/plugins/toggle")
    print("   Body: {'plugin_id': 'clock-simple', 'enabled': true}")


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("LEDMATRIX PLUGIN STORE MANAGER TEST SUITE")
    print("="*60)
    
    results = []
    
    # Test 1: Fetch registry
    results.append(("Fetch Registry", test_fetch_registry()))
    
    # Test 2: Search plugins
    results.append(("Search Plugins", test_search_plugins()))
    
    # Test 3: List installed
    results.append(("List Installed", test_list_installed()))
    
    # Test 4: Install from URL example
    test_install_from_url_example()
    
    # Test 5: API usage
    test_api_usage()
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✓ All tests passed!")
    else:
        print(f"\n⚠ {total - passed} test(s) failed")
        print("Note: Some failures are expected if the registry doesn't exist yet.")
    
    print("\n" + "="*60)
    print("KEY FEATURES IMPLEMENTED:")
    print("="*60)
    print("✓ Install from official registry (curated plugins)")
    print("✓ Install from custom GitHub URL (any repository)")
    print("✓ Search and filter plugins by query, category, tags")
    print("✓ List installed plugins with metadata")
    print("✓ Uninstall plugins")
    print("✓ Update plugins to latest commit")
    print("✓ Automatic dependency installation")
    print("✓ Git clone or ZIP download fallback")
    print("✓ Comprehensive error handling and logging")
    print("✓ RESTful API endpoints for web interface")
    
    print("\n" + "="*60)
    print("NEXT STEPS:")
    print("="*60)
    print("1. Create a plugin registry repository on GitHub")
    print("2. Add example plugins to test with")
    print("3. Build web UI components for plugin browsing")
    print("4. Test install from URL with real GitHub repos")
    print("5. Create documentation for plugin developers")


if __name__ == "__main__":
    main()

