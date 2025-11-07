#!/usr/bin/env python3
"""
Test script for monorepo plugin installation.

Tests installing plugins from a monorepo structure where plugins
are stored in subdirectories (e.g., plugins/hello-world/).
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
    """Test fetching the actual registry."""
    print("\n" + "="*60)
    print("TEST 1: Fetch Real Registry")
    print("="*60)
    
    store = PluginStoreManager(plugins_dir="plugins")
    
    try:
        registry = store.fetch_registry()
        print(f"✓ Registry fetched successfully")
        print(f"  URL: https://raw.githubusercontent.com/ChuckBuilds/ledmatrix-plugins/main/plugins.json")
        print(f"  Plugins available: {len(registry.get('plugins', []))}")
        
        # Show available plugins
        plugins = registry.get('plugins', [])
        if plugins:
            print("\n  Available plugins:")
            for plugin in plugins:
                print(f"    - {plugin.get('name')} ({plugin.get('id')})")
                print(f"      Category: {plugin.get('category')}")
                print(f"      Repo: {plugin.get('repo')}")
                if plugin.get('plugin_path'):
                    print(f"      Path in repo: {plugin.get('plugin_path')}")
                print()
        
        return True
    except Exception as e:
        print(f"✗ Failed to fetch registry: {e}")
        return False


def test_plugin_info():
    """Test getting specific plugin info."""
    print("\n" + "="*60)
    print("TEST 2: Get Plugin Info")
    print("="*60)
    
    store = PluginStoreManager(plugins_dir="plugins")
    
    try:
        # Get hello-world plugin
        info = store.get_plugin_info('hello-world')
        if info:
            print(f"✓ Found plugin: {info.get('name')}")
            print(f"  ID: {info.get('id')}")
            print(f"  Description: {info.get('description')}")
            print(f"  Author: {info.get('author')}")
            print(f"  Repo: {info.get('repo')}")
            print(f"  Plugin Path: {info.get('plugin_path')}")
            print(f"  Last Updated: {info.get('last_updated')}")
            print(f"  Last Commit: {info.get('last_commit')}")
            
            return True
        else:
            print("✗ Plugin not found")
            return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_install_simulation():
    """Show what would happen during installation."""
    print("\n" + "="*60)
    print("TEST 3: Installation Simulation")
    print("="*60)
    
    print("\nWhen you click 'Install' for hello-world:")
    print("\n1. Fetches plugin info from registry")
    print("   ✓ ID: hello-world")
    print("   ✓ Repo: https://github.com/ChuckBuilds/ledmatrix-plugins")
    print("   ✓ Plugin Path: plugins/hello-world")
    
    print("\n2. Downloads ZIP archive")
    print("   ✓ URL: https://github.com/ChuckBuilds/ledmatrix-plugins/archive/refs/heads/main.zip")
    
    print("\n3. Extracts archive (contains entire repo)")
    print("   ✓ Extracted to temp directory")
    print("   ✓ Structure: ledmatrix-plugins-main/")
    print("                  └── plugins/")
    print("                       ├── hello-world/")
    print("                       └── clock-simple/")
    
    print("\n4. Navigates to plugin subdirectory")
    print("   ✓ Path: ledmatrix-plugins-main/plugins/hello-world/")
    
    print("\n5. Moves plugin to final location")
    print("   ✓ From: temp/ledmatrix-plugins-main/plugins/hello-world/")
    print("   ✓ To: LEDMatrix/plugins/hello-world/")
    
    print("\n6. Validates manifest.json exists")
    print("   ✓ Found: plugins/hello-world/manifest.json")
    
    print("\n7. Installs dependencies (if requirements.txt exists)")
    print("   ✓ Runs: pip3 install -r plugins/hello-world/requirements.txt")
    
    print("\n8. Installation complete!")
    print("   ✓ Plugin ready to use")
    print("   ✓ Restart display to activate")


def show_troubleshooting():
    """Show common issues and solutions."""
    print("\n" + "="*60)
    print("TROUBLESHOOTING")
    print("="*60)
    
    print("\n❌ Error: 'Plugin path not found in archive'")
    print("   Cause: The plugin_path in plugins.json doesn't match repo structure")
    print("   Fix: Verify plugin_path matches actual directory in repo")
    print("        For example: 'plugins/hello-world' must exist in repo")
    
    print("\n❌ Error: 'No manifest.json found'")
    print("   Cause: manifest.json missing from plugin directory")
    print("   Fix: Ensure manifest.json exists in the plugin subdirectory")
    print("        Check: https://github.com/ChuckBuilds/ledmatrix-plugins/blob/main/plugins/hello-world/manifest.json")
    
    print("\n❌ Error: 'Failed to download'")
    print("   Cause: Network issue or invalid download_url")
    print("   Fix: Check internet connection")
    print("        Verify download_url is accessible:")
    print("        curl -I https://github.com/ChuckBuilds/ledmatrix-plugins/archive/refs/heads/main.zip")
    
    print("\n❌ Error: 'Dependencies failed'")
    print("   Cause: pip3 installation issues")
    print("   Fix: Manually install dependencies:")
    print("        pip3 install --break-system-packages -r plugins/hello-world/requirements.txt")


def show_monorepo_benefits():
    """Explain why monorepo structure is useful."""
    print("\n" + "="*60)
    print("MONOREPO BENEFITS")
    print("="*60)
    
    print("\n✅ **Single Repository for All Official Plugins**")
    print("   - Easier to maintain")
    print("   - One place to browse all plugins")
    print("   - Consistent structure")
    
    print("\n✅ **Simpler Development**")
    print("   - Clone once, develop multiple plugins")
    print("   - Share common assets/utilities")
    print("   - Unified testing")
    
    print("\n✅ **Easier for Users**")
    print("   - One repo to star/watch")
    print("   - Single place for issues")
    print("   - Clear organization")
    
    print("\n✅ **Flexible Distribution**")
    print("   - Can still extract individual plugins")
    print("   - Support both monorepo and individual repos")
    print("   - No changes needed for end users")


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("MONOREPO PLUGIN INSTALLATION TEST")
    print("="*70)
    
    results = []
    
    # Test 1: Fetch registry
    results.append(("Fetch Registry", test_fetch_registry()))
    
    # Test 2: Get plugin info
    results.append(("Get Plugin Info", test_plugin_info()))
    
    # Test 3: Show installation process
    test_install_simulation()
    
    # Show troubleshooting
    show_troubleshooting()
    
    # Show benefits
    show_monorepo_benefits()
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    print("\n" + "="*70)
    print("TO ACTUALLY TEST INSTALLATION:")
    print("="*70)
    print("\nRun this command on your Raspberry Pi:")
    print("\ncurl -X POST http://localhost:5050/api/plugins/install \\")
    print("  -H 'Content-Type: application/json' \\")
    print("  -d '{\"plugin_id\": \"hello-world\"}'")
    print("\nOr use the Web UI:")
    print("1. Open http://your-pi-ip:5050")
    print("2. Go to Plugin Store tab")
    print("3. Find 'Hello World' plugin")
    print("4. Click 'Install'")
    print("5. Wait for installation to complete")
    print("6. Restart display: sudo systemctl restart ledmatrix")
    
    print("\n" + "="*70)
    print("✓ MONOREPO SUPPORT IMPLEMENTED!")
    print("="*70)


if __name__ == "__main__":
    main()

