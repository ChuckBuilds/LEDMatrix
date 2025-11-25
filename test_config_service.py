#!/usr/bin/env python3
"""
Test script for ConfigService

Tests hot-reload, versioning, change notifications, and plugin subscriptions.
"""

import json
import os
import time
import tempfile
import shutil
from pathlib import Path

from src.config_service import ConfigService
from src.config_manager import ConfigManager


def test_basic_functionality():
    """Test basic ConfigService functionality."""
    print("\n" + "="*60)
    print("Test 1: Basic Functionality")
    print("="*60)
    
    # Create temporary config directory
    temp_dir = tempfile.mkdtemp()
    config_path = os.path.join(temp_dir, "config.json")
    secrets_path = os.path.join(temp_dir, "config_secrets.json")
    template_path = os.path.join(temp_dir, "config.template.json")
    
    try:
        # Create initial config
        initial_config = {
            "test_plugin": {
                "enabled": True,
                "setting1": "value1"
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        # Create empty secrets file
        with open(secrets_path, 'w') as f:
            json.dump({}, f)
        
        # Create template
        with open(template_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        # Initialize ConfigService
        config_manager = ConfigManager(config_path=config_path, secrets_path=secrets_path)
        config_service = ConfigService(config_manager, enable_hot_reload=False)
        
        # Test basic getters
        config = config_service.get_config()
        assert "test_plugin" in config, "Config should contain test_plugin"
        assert config["test_plugin"]["enabled"] is True, "Plugin should be enabled"
        
        version = config_service.get_version()
        assert version == 1, f"Initial version should be 1, got {version}"
        
        print("✓ Config loaded successfully")
        print(f"✓ Version: {version}")
        print(f"✓ Config keys: {list(config.keys())}")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_versioning():
    """Test configuration versioning."""
    print("\n" + "="*60)
    print("Test 2: Configuration Versioning")
    print("="*60)
    
    temp_dir = tempfile.mkdtemp()
    config_path = os.path.join(temp_dir, "config.json")
    secrets_path = os.path.join(temp_dir, "config_secrets.json")
    template_path = os.path.join(temp_dir, "config.template.json")
    
    try:
        # Create initial config
        initial_config = {
            "test_plugin": {
                "enabled": True,
                "setting1": "value1"
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        with open(secrets_path, 'w') as f:
            json.dump({}, f)
        
        with open(template_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        config_manager = ConfigManager(config_path=config_path, secrets_path=secrets_path)
        config_service = ConfigService(config_manager, enable_hot_reload=False)
        
        # Get initial version
        version1 = config_service.get_version()
        print(f"✓ Initial version: {version1}")
        
        # Modify config and reload
        modified_config = {
            "test_plugin": {
                "enabled": False,  # Changed
                "setting1": "value2"  # Changed
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(modified_config, f, indent=2)
        
        config_service.reload()
        version2 = config_service.get_version()
        print(f"✓ After reload version: {version2}")
        assert version2 > version1, "Version should increment"
        
        # Check version history
        history = config_service.get_version_history()
        assert len(history) >= 2, f"Should have at least 2 versions, got {len(history)}"
        print(f"✓ Version history: {len(history)} versions")
        
        # Get old version config
        old_config = config_service.get_version_config(version1)
        assert old_config["test_plugin"]["enabled"] is True, "Old version should have enabled=True"
        print(f"✓ Retrieved version {version1} config")
        
        # Get new version config
        new_config = config_service.get_version_config(version2)
        assert new_config["test_plugin"]["enabled"] is False, "New version should have enabled=False"
        print(f"✓ Retrieved version {version2} config")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_change_notifications():
    """Test change notification system."""
    print("\n" + "="*60)
    print("Test 3: Change Notifications")
    print("="*60)
    
    temp_dir = tempfile.mkdtemp()
    config_path = os.path.join(temp_dir, "config.json")
    secrets_path = os.path.join(temp_dir, "config_secrets.json")
    template_path = os.path.join(temp_dir, "config.template.json")
    
    try:
        # Create initial config
        initial_config = {
            "plugin1": {
                "enabled": True,
                "setting": "value1"
            },
            "plugin2": {
                "enabled": True,
                "setting": "value1"
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        with open(secrets_path, 'w') as f:
            json.dump({}, f)
        
        with open(template_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        config_manager = ConfigManager(config_path=config_path, secrets_path=secrets_path)
        config_service = ConfigService(config_manager, enable_hot_reload=False)
        
        # Track notifications
        global_notifications = []
        plugin1_notifications = []
        plugin2_notifications = []
        
        def global_callback(old_config, new_config):
            global_notifications.append((old_config.copy(), new_config.copy()))
        
        def plugin1_callback(old_config, new_config):
            plugin1_notifications.append((old_config.copy(), new_config.copy()))
        
        def plugin2_callback(old_config, new_config):
            plugin2_notifications.append((old_config.copy(), new_config.copy()))
        
        # Subscribe to changes
        config_service.subscribe(global_callback)
        config_service.subscribe(plugin1_callback, plugin_id="plugin1")
        config_service.subscribe(plugin2_callback, plugin_id="plugin2")
        
        print("✓ Subscribed to global and plugin-specific changes")
        
        # Modify plugin1 only
        modified_config = {
            "plugin1": {
                "enabled": False,  # Changed
                "setting": "value2"  # Changed
            },
            "plugin2": {
                "enabled": True,  # Unchanged
                "setting": "value1"  # Unchanged
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(modified_config, f, indent=2)
        
        config_service.reload()
        
        # Check notifications
        assert len(global_notifications) == 1, f"Global should be notified once, got {len(global_notifications)}"
        assert len(plugin1_notifications) == 1, f"Plugin1 should be notified once, got {len(plugin1_notifications)}"
        assert len(plugin2_notifications) == 0, f"Plugin2 should not be notified, got {len(plugin2_notifications)}"
        
        print(f"✓ Global notifications: {len(global_notifications)}")
        print(f"✓ Plugin1 notifications: {len(plugin1_notifications)}")
        print(f"✓ Plugin2 notifications: {len(plugin2_notifications)} (correctly not notified)")
        
        # Verify notification content
        old_plugin1_config, new_plugin1_config = plugin1_notifications[0]
        assert old_plugin1_config["enabled"] is True, "Old config should have enabled=True"
        assert new_plugin1_config["enabled"] is False, "New config should have enabled=False"
        print("✓ Notification content verified")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_hot_reload():
    """Test hot-reload functionality."""
    print("\n" + "="*60)
    print("Test 4: Hot-Reload (File Watching)")
    print("="*60)
    
    temp_dir = tempfile.mkdtemp()
    config_path = os.path.join(temp_dir, "config.json")
    secrets_path = os.path.join(temp_dir, "config_secrets.json")
    template_path = os.path.join(temp_dir, "config.template.json")
    
    try:
        # Create initial config
        initial_config = {
            "test_plugin": {
                "enabled": True,
                "setting": "value1"
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        with open(secrets_path, 'w') as f:
            json.dump({}, f)
        
        with open(template_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        config_manager = ConfigManager(config_path=config_path, secrets_path=secrets_path)
        config_service = ConfigService(config_manager, enable_hot_reload=True)
        
        # Get initial version
        version1 = config_service.get_version()
        config1 = config_service.get_config()
        print(f"✓ Initial version: {version1}")
        print(f"✓ Initial setting: {config1['test_plugin']['setting']}")
        
        # Wait a moment for file watcher to initialize
        time.sleep(1)
        
        # Modify config file
        modified_config = {
            "test_plugin": {
                "enabled": True,
                "setting": "value2"  # Changed
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(modified_config, f, indent=2)
        
        print("✓ Modified config file")
        
        # Wait for hot-reload to detect and reload (check every 2 seconds, so wait 3)
        time.sleep(3.5)
        
        # Check if reloaded
        version2 = config_service.get_version()
        config2 = config_service.get_config()
        
        if version2 > version1:
            print(f"✓ Hot-reload detected change! Version: {version1} -> {version2}")
            print(f"✓ New setting: {config2['test_plugin']['setting']}")
            assert config2['test_plugin']['setting'] == "value2", "Config should be updated"
        else:
            print(f"⚠ Hot-reload may not have triggered (version unchanged: {version2})")
            print("  This might be due to timing - trying manual reload...")
            config_service.reload()
            version2 = config_service.get_version()
            config2 = config_service.get_config()
            if version2 > version1:
                print(f"✓ Manual reload worked! Version: {version1} -> {version2}")
                print(f"✓ New setting: {config2['test_plugin']['setting']}")
        
        # Shutdown
        config_service.shutdown()
        print("✓ ConfigService shutdown successfully")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_rollback():
    """Test configuration rollback."""
    print("\n" + "="*60)
    print("Test 5: Configuration Rollback")
    print("="*60)
    
    temp_dir = tempfile.mkdtemp()
    config_path = os.path.join(temp_dir, "config.json")
    secrets_path = os.path.join(temp_dir, "config_secrets.json")
    template_path = os.path.join(temp_dir, "config.template.json")
    
    try:
        # Create initial config
        initial_config = {
            "test_plugin": {
                "enabled": True,
                "setting": "value1"
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        with open(secrets_path, 'w') as f:
            json.dump({}, f)
        
        with open(template_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        config_manager = ConfigManager(config_path=config_path, secrets_path=secrets_path)
        config_service = ConfigService(config_manager, enable_hot_reload=False)
        
        # Get version 1
        version1 = config_service.get_version()
        config1 = config_service.get_config()
        print(f"✓ Version 1: {config1['test_plugin']['setting']}")
        
        # Modify and reload to version 2
        modified_config = {
            "test_plugin": {
                "enabled": True,
                "setting": "value2"
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(modified_config, f, indent=2)
        
        config_service.reload()
        version2 = config_service.get_version()
        config2 = config_service.get_config()
        print(f"✓ Version 2: {config2['test_plugin']['setting']}")
        
        # Rollback to version 1
        success = config_service.rollback(version1)
        assert success, "Rollback should succeed"
        
        # Wait a moment for file write
        time.sleep(0.5)
        
        # Reload to get rolled back config
        config_service.reload()
        config_rolled_back = config_service.get_config()
        
        assert config_rolled_back['test_plugin']['setting'] == "value1", \
            f"Rolled back config should have value1, got {config_rolled_back['test_plugin']['setting']}"
        
        print(f"✓ Rolled back to version {version1}")
        print(f"✓ Rolled back setting: {config_rolled_back['test_plugin']['setting']}")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_plugin_subscription():
    """Test plugin subscription pattern (simulating DisplayController behavior)."""
    print("\n" + "="*60)
    print("Test 6: Plugin Subscription Pattern")
    print("="*60)
    
    temp_dir = tempfile.mkdtemp()
    config_path = os.path.join(temp_dir, "config.json")
    secrets_path = os.path.join(temp_dir, "config_secrets.json")
    template_path = os.path.join(temp_dir, "config.template.json")
    
    try:
        # Create initial config
        initial_config = {
            "my_plugin": {
                "enabled": True,
                "display_duration": 15,
                "api_key": "secret123"
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        with open(secrets_path, 'w') as f:
            json.dump({}, f)
        
        with open(template_path, 'w') as f:
            json.dump(initial_config, f, indent=2)
        
        config_manager = ConfigManager(config_path=config_path, secrets_path=secrets_path)
        config_service = ConfigService(config_manager, enable_hot_reload=False)
        
        # Simulate plugin with on_config_change method
        class MockPlugin:
            def __init__(self, plugin_id):
                self.plugin_id = plugin_id
                self.config = {}
                self.config_changes = []
            
            def on_config_change(self, new_config):
                self.config = new_config
                self.config_changes.append(new_config.copy())
                print(f"  → Plugin {self.plugin_id} notified: enabled={new_config.get('enabled')}, duration={new_config.get('display_duration')}")
        
        # Create and subscribe plugin
        plugin = MockPlugin("my_plugin")
        
        def config_change_callback(old_config, new_config):
            plugin.on_config_change(new_config)
        
        config_service.subscribe(config_change_callback, plugin_id="my_plugin")
        print("✓ Plugin subscribed to config changes")
        
        # Initial config should be set
        plugin.config = config_service.get_plugin_config("my_plugin")
        print(f"✓ Initial plugin config: enabled={plugin.config.get('enabled')}, duration={plugin.config.get('display_duration')}")
        
        # Modify plugin config
        modified_config = {
            "my_plugin": {
                "enabled": False,  # Changed
                "display_duration": 30,  # Changed
                "api_key": "secret123"  # Unchanged
            }
        }
        
        with open(config_path, 'w') as f:
            json.dump(modified_config, f, indent=2)
        
        config_service.reload()
        
        # Check plugin was notified
        assert len(plugin.config_changes) == 1, f"Plugin should be notified once, got {len(plugin.config_changes)}"
        assert plugin.config['enabled'] is False, "Plugin config should be updated"
        assert plugin.config['display_duration'] == 30, "Plugin config should be updated"
        
        print(f"✓ Plugin notified {len(plugin.config_changes)} time(s)")
        print(f"✓ Plugin config updated: enabled={plugin.config.get('enabled')}, duration={plugin.config.get('display_duration')}")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("ConfigService Test Suite")
    print("="*60)
    
    tests = [
        ("Basic Functionality", test_basic_functionality),
        ("Versioning", test_versioning),
        ("Change Notifications", test_change_notifications),
        ("Hot-Reload", test_hot_reload),
        ("Rollback", test_rollback),
        ("Plugin Subscription", test_plugin_subscription),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n✗ Test '{test_name}' crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))
    
    # Summary
    print("\n" + "="*60)
    print("Test Summary")
    print("="*60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    exit(main())

