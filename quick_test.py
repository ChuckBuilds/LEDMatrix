#!/usr/bin/env python3
"""
Quick test script to verify Phase 1 and Phase 2 improvements.

Run this to quickly verify that all the improvements are working.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Test that all new modules can be imported."""
    print("Testing imports...")
    
    try:
        from src.exceptions import CacheError, ConfigError, PluginError
        print("✓ Custom exceptions imported")
    except ImportError as e:
        print(f"✗ Failed to import exceptions: {e}")
        return False
    
    try:
        from src.cache.memory_cache import MemoryCache
        from src.cache.disk_cache import DiskCache
        from src.cache.cache_strategy import CacheStrategy
        from src.cache.cache_metrics import CacheMetrics
        print("✓ Cache components imported")
    except ImportError as e:
        print(f"✗ Failed to import cache components: {e}")
        return False
    
    try:
        from src.logging_config import setup_logging, get_logger
        print("✓ Logging config imported")
    except ImportError as e:
        print(f"✗ Failed to import logging config: {e}")
        return False
    
    try:
        from src.common.error_handler import handle_file_operation
        print("✓ Error handler imported")
    except ImportError as e:
        print(f"✗ Failed to import error handler: {e}")
        return False
    
    try:
        from src.startup_validator import StartupValidator
        print("✓ Startup validator imported")
    except ImportError as e:
        print(f"✗ Failed to import startup validator: {e}")
        return False
    
    return True


def test_cache_components():
    """Test cache components functionality."""
    print("\nTesting cache components...")
    
    try:
        from src.cache.memory_cache import MemoryCache
        
        # Test MemoryCache
        cache = MemoryCache(max_size=10)
        cache.set("test_key", {"data": "value"})
        result = cache.get("test_key")
        assert result == {"data": "value"}, "MemoryCache get/set failed"
        print("✓ MemoryCache works")
        
        # Test CacheStrategy
        from src.cache.cache_strategy import CacheStrategy
        strategy = CacheStrategy()
        result = strategy.get_cache_strategy("live_scores")
        assert "max_age" in result, "CacheStrategy failed"
        print("✓ CacheStrategy works")
        
        # Test CacheMetrics
        from src.cache.cache_metrics import CacheMetrics
        metrics = CacheMetrics()
        metrics.record_hit()
        metrics.record_miss()
        stats = metrics.get_metrics()
        assert stats['total_requests'] == 2, "CacheMetrics failed"
        print("✓ CacheMetrics works")
        
        return True
    except Exception as e:
        print(f"✗ Cache components test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_logging():
    """Test logging configuration."""
    print("\nTesting logging...")
    
    try:
        from src.logging_config import setup_logging, get_logger
        import logging
        
        # Setup logging
        setup_logging(level=logging.INFO, format_type='readable')
        
        # Get logger
        logger = get_logger("test")
        logger.info("Test log message")
        print("✓ Logging setup works")
        
        # Test plugin logger
        plugin_logger = get_logger("plugin.test", plugin_id="test_plugin")
        plugin_logger.info("Plugin test message")
        print("✓ Plugin logging works")
        
        return True
    except Exception as e:
        print(f"✗ Logging test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_error_handling():
    """Test error handling utilities."""
    print("\nTesting error handling...")
    
    try:
        from src.common.error_handler import safe_execute
        from src.logging_config import get_logger
        import logging
        
        logger = get_logger(__name__)
        
        # Test safe_execute with success
        result = safe_execute(
            lambda: "success",
            "Test operation",
            logger,
            default="failed"
        )
        assert result == "success", "safe_execute success case failed"
        print("✓ safe_execute works")
        
        # Test safe_execute with failure
        result = safe_execute(
            lambda: 1/0,  # Will raise ZeroDivisionError
            "Test operation",
            logger,
            default="failed"
        )
        assert result == "failed", "safe_execute failure case failed"
        print("✓ safe_execute error handling works")
        
        return True
    except Exception as e:
        print(f"✗ Error handling test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cache_manager_integration():
    """Test that CacheManager uses new components."""
    print("\nTesting CacheManager integration...")
    
    try:
        from src.cache_manager import CacheManager
        
        manager = CacheManager()
        
        # Check that components exist
        assert hasattr(manager, '_memory_cache_component'), "MemoryCache component missing"
        assert hasattr(manager, '_disk_cache_component'), "DiskCache component missing"
        assert hasattr(manager, '_strategy_component'), "CacheStrategy component missing"
        assert hasattr(manager, '_metrics_component'), "CacheMetrics component missing"
        print("✓ CacheManager has all components")
        
        # Test backward compatibility
        manager.set("test_key", {"data": "value"})
        result = manager.get("test_key")
        assert result == {"data": "value"}, "Backward compatibility failed"
        print("✓ CacheManager backward compatibility works")
        
        return True
    except Exception as e:
        print(f"✗ CacheManager integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_type_hints():
    """Test that type hints are present."""
    print("\nTesting type hints...")
    
    try:
        import inspect
        from src.cache_manager import CacheManager
        from src.config_manager import ConfigManager
        
        # Check that methods have type hints
        sig = inspect.signature(CacheManager.get)
        assert sig.return_annotation != inspect.Signature.empty, "CacheManager.get missing return type"
        print("✓ CacheManager has type hints")
        
        sig = inspect.signature(ConfigManager.load_config)
        assert sig.return_annotation != inspect.Signature.empty, "ConfigManager.load_config missing return type"
        print("✓ ConfigManager has type hints")
        
        return True
    except Exception as e:
        print(f"✗ Type hints test failed: {e}")
        return False


def main():
    """Run all quick tests."""
    print("=" * 60)
    print("Quick Test: Phase 1 & 2 Improvements")
    print("=" * 60)
    
    tests = [
        ("Imports", test_imports),
        ("Cache Components", test_cache_components),
        ("Logging", test_logging),
        ("Error Handling", test_error_handling),
        ("CacheManager Integration", test_cache_manager_integration),
        ("Type Hints", test_type_hints),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ {name} test crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("Test Results:")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    print("=" * 60)
    print(f"Total: {passed}/{total} tests passed")
    print("=" * 60)
    
    if passed == total:
        print("\n🎉 All tests passed! Phase 1 & 2 improvements are working.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please review the output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

