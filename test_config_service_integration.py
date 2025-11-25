#!/usr/bin/env python3
"""
Integration test for ConfigService with DisplayController pattern

Simulates how DisplayController uses ConfigService in production.
"""

import json
import os
import tempfile
import shutil
import time

from src.config_service import ConfigService
from src.config_manager import ConfigManager


def test_display_controller_integration():
    """Test ConfigService integration pattern used by DisplayController."""
    print("\n" + "="*60)
    print("Integration Test: DisplayController Pattern")
    print("="*60)
    
    temp_dir = tempfile.mkdtemp()
    config_path = os.path.join(temp_dir, "config.json")
    secrets_path = os.path.join(temp_dir, "config_secrets.json")
    template_path = os.path.join(temp_dir, "config.template.json")
    
    try:
        # Create initial config (simulating real config structure)
        initial_config = {
            "plugin_system": {
                "plugins_directory": "plugins"
            },
            "hockey-scoreboard": {
                "enabled": True,
                "display_duration": 15,
                "update_interval": 60
            },
            "weather": {
                "enabled": True,
                "display_duration": 20
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        with open(secrets_path, 'w') as f:
            json.dump({}, f)
        
        with open(template_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        # Simulate DisplayController initialization
        print("1. Initializing ConfigService (DisplayController pattern)...")
        config_manager = ConfigManager(config_path=config_path, secrets_path=secrets_path)
        enable_hot_reload = os.environ.get('LEDMATRIX_HOT_RELOAD', 'true').lower() == 'true'
        config_service = ConfigService(
            config_manager=config_manager,
            enable_hot_reload=enable_hot_reload
        )
        config = config_service.get_config()
        print(f"   ✓ ConfigService initialized (hot-reload: {enable_hot_reload})")
        print(f"   ✓ Config loaded: {len(config)} top-level keys")
        print(f"   ✓ Version: {config_service.get_version()}")
        
        # Simulate plugin loading and subscription
        print("\n2. Simulating plugin loading and subscription...")
        
        class MockPlugin:
            def __init__(self, plugin_id):
                self.plugin_id = plugin_id
                self.config = {}
                self.notifications = []
            
            def on_config_change(self, new_config):
                old_enabled = self.config.get('enabled')
                self.config = new_config
                self.notifications.append({
                    'old_enabled': old_enabled,
                    'new_enabled': new_config.get('enabled'),
                    'timestamp': time.time()
                })
                print(f"   → {self.plugin_id}: enabled {old_enabled} → {new_config.get('enabled')}")
        
        # Load plugins (simulate)
        plugins = {}
        for plugin_id in ["hockey-scoreboard", "weather"]:
            plugin = MockPlugin(plugin_id)
            plugins[plugin_id] = plugin
            
            # Get initial config
            plugin.config = config_service.get_plugin_config(plugin_id)
            
            # Subscribe to changes (DisplayController pattern)
            def make_callback(plugin_instance):
                def callback(old_config, new_config):
                    plugin_instance.on_config_change(new_config)
                return callback
            
            config_service.subscribe(make_callback(plugin), plugin_id=plugin_id)
            print(f"   ✓ Loaded and subscribed: {plugin_id} (enabled={plugin.config.get('enabled')})")
        
        # Verify initial state
        print("\n3. Verifying initial state...")
        for plugin_id, plugin in plugins.items():
            assert plugin.config.get('enabled') is True, f"{plugin_id} should be enabled"
        print("   ✓ All plugins enabled initially")
        
        # Simulate config change via web UI or file edit
        print("\n4. Simulating config change (disable hockey-scoreboard)...")
        modified_config = initial_config.copy()
        modified_config["hockey-scoreboard"]["enabled"] = False
        modified_config["hockey-scoreboard"]["display_duration"] = 30  # Also change duration
        
        with open(config_path, 'w') as f:
            json.dump(modified_config, f, indent=2)
        
        # If hot-reload enabled, wait for it; otherwise manual reload
        if enable_hot_reload:
            print("   → Waiting for hot-reload to detect change...")
            time.sleep(3.5)  # Wait for file watcher
        else:
            print("   → Manually reloading config...")
            config_service.reload()
        
        # Verify changes
        print("\n5. Verifying changes were applied...")
        new_version = config_service.get_version()
        print(f"   ✓ Config version: {new_version}")
        
        # Check plugin notifications
        hockey_plugin = plugins["hockey-scoreboard"]
        weather_plugin = plugins["weather"]
        
        assert len(hockey_plugin.notifications) > 0, "Hockey plugin should be notified"
        assert hockey_plugin.config.get('enabled') is False, "Hockey plugin should be disabled"
        assert hockey_plugin.config.get('display_duration') == 30, "Hockey plugin duration should be updated"
        print(f"   ✓ Hockey plugin notified: {len(hockey_plugin.notifications)} time(s)")
        print(f"   ✓ Hockey plugin config updated: enabled=False, duration=30")
        
        assert len(weather_plugin.notifications) == 0, "Weather plugin should NOT be notified (no changes)"
        assert weather_plugin.config.get('enabled') is True, "Weather plugin should still be enabled"
        print(f"   ✓ Weather plugin correctly NOT notified (no changes)")
        
        # Test rollback
        print("\n6. Testing rollback capability...")
        old_version = new_version - 1
        success = config_service.rollback(old_version)
        assert success, "Rollback should succeed"
        
        time.sleep(0.5)  # Wait for file write
        config_service.reload()
        
        rolled_back_config = config_service.get_plugin_config("hockey-scoreboard")
        assert rolled_back_config.get('enabled') is True, "Rolled back config should have enabled=True"
        print(f"   ✓ Rolled back to version {old_version}")
        print(f"   ✓ Hockey plugin config restored: enabled={rolled_back_config.get('enabled')}")
        
        # Cleanup
        print("\n7. Cleaning up...")
        config_service.shutdown()
        print("   ✓ ConfigService shutdown successfully")
        
        print("\n" + "="*60)
        print("✓ Integration test PASSED")
        print("="*60)
        return True
        
    except Exception as e:
        print(f"\n✗ Integration test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    success = test_display_controller_integration()
    exit(0 if success else 1)

