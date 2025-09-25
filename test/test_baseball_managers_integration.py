#!/usr/bin/env python3
"""
Test Baseball Managers Integration

This test validates that MILB and NCAA Baseball managers work with the new
baseball base class architecture.
"""

import sys
import os
import logging
from typing import Dict, Any

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

def test_milb_manager_imports():
    """Test that MILB managers can be imported."""
    print("🧪 Testing MILB Manager Imports...")
    
    try:
        # Test that we can import the new MILB managers
        from src.milb_managers_v2 import BaseMiLBManager, MiLBLiveManager, MiLBRecentManager, MiLBUpcomingManager
        print("✅ MILB managers imported successfully")
        
        # Test that classes are properly defined
        assert BaseMiLBManager is not None
        assert MiLBLiveManager is not None
        assert MiLBRecentManager is not None
        assert MiLBUpcomingManager is not None
        
        print("✅ MILB managers are properly defined")
        return True
        
    except Exception as e:
        print(f"❌ MILB manager import test failed: {e}")
        return False

def test_ncaa_baseball_manager_imports():
    """Test that NCAA Baseball managers can be imported."""
    print("\n🧪 Testing NCAA Baseball Manager Imports...")
    
    try:
        # Test that we can import the new NCAA Baseball managers
        from src.ncaa_baseball_managers_v2 import BaseNCAABaseballManager, NCAABaseballLiveManager, NCAABaseballRecentManager, NCAABaseballUpcomingManager
        print("✅ NCAA Baseball managers imported successfully")
        
        # Test that classes are properly defined
        assert BaseNCAABaseballManager is not None
        assert NCAABaseballLiveManager is not None
        assert NCAABaseballRecentManager is not None
        assert NCAABaseballUpcomingManager is not None
        
        print("✅ NCAA Baseball managers are properly defined")
        return True
        
    except Exception as e:
        print(f"❌ NCAA Baseball manager import test failed: {e}")
        return False

def test_milb_manager_inheritance():
    """Test that MILB managers properly inherit from baseball base classes."""
    print("\n🧪 Testing MILB Manager Inheritance...")
    
    try:
        from src.milb_managers_v2 import BaseMiLBManager, MiLBLiveManager, MiLBRecentManager, MiLBUpcomingManager
        from src.base_classes.baseball import Baseball, BaseballLive, BaseballRecent, BaseballUpcoming
        
        # Test inheritance
        assert issubclass(BaseMiLBManager, Baseball), "BaseMiLBManager should inherit from Baseball"
        assert issubclass(MiLBLiveManager, BaseballLive), "MiLBLiveManager should inherit from BaseballLive"
        assert issubclass(MiLBRecentManager, BaseballRecent), "MiLBRecentManager should inherit from BaseballRecent"
        assert issubclass(MiLBUpcomingManager, BaseballUpcoming), "MiLBUpcomingManager should inherit from BaseballUpcoming"
        
        print("✅ MILB managers properly inherit from baseball base classes")
        return True
        
    except Exception as e:
        print(f"❌ MILB manager inheritance test failed: {e}")
        return False

def test_ncaa_baseball_manager_inheritance():
    """Test that NCAA Baseball managers properly inherit from baseball base classes."""
    print("\n🧪 Testing NCAA Baseball Manager Inheritance...")
    
    try:
        from src.ncaa_baseball_managers_v2 import BaseNCAABaseballManager, NCAABaseballLiveManager, NCAABaseballRecentManager, NCAABaseballUpcomingManager
        from src.base_classes.baseball import Baseball, BaseballLive, BaseballRecent, BaseballUpcoming
        
        # Test inheritance
        assert issubclass(BaseNCAABaseballManager, Baseball), "BaseNCAABaseballManager should inherit from Baseball"
        assert issubclass(NCAABaseballLiveManager, BaseballLive), "NCAABaseballLiveManager should inherit from BaseballLive"
        assert issubclass(NCAABaseballRecentManager, BaseballRecent), "NCAABaseballRecentManager should inherit from BaseballRecent"
        assert issubclass(NCAABaseballUpcomingManager, BaseballUpcoming), "NCAABaseballUpcomingManager should inherit from BaseballUpcoming"
        
        print("✅ NCAA Baseball managers properly inherit from baseball base classes")
        return True
        
    except Exception as e:
        print(f"❌ NCAA Baseball manager inheritance test failed: {e}")
        return False

def test_milb_manager_methods():
    """Test that MILB managers have required methods."""
    print("\n🧪 Testing MILB Manager Methods...")
    
    try:
        from src.milb_managers_v2 import BaseMiLBManager, MiLBLiveManager, MiLBRecentManager, MiLBUpcomingManager
        
        # Test that managers have required methods
        required_methods = ['get_duration', 'display', '_display_single_game']
        
        for manager_class in [MiLBLiveManager, MiLBRecentManager, MiLBUpcomingManager]:
            for method in required_methods:
                assert hasattr(manager_class, method), f"{manager_class.__name__} should have {method} method"
                assert callable(getattr(manager_class, method)), f"{manager_class.__name__}.{method} should be callable"
        
        print("✅ MILB managers have all required methods")
        return True
        
    except Exception as e:
        print(f"❌ MILB manager methods test failed: {e}")
        return False

def test_ncaa_baseball_manager_methods():
    """Test that NCAA Baseball managers have required methods."""
    print("\n🧪 Testing NCAA Baseball Manager Methods...")
    
    try:
        from src.ncaa_baseball_managers_v2 import BaseNCAABaseballManager, NCAABaseballLiveManager, NCAABaseballRecentManager, NCAABaseballUpcomingManager
        
        # Test that managers have required methods
        required_methods = ['get_duration', 'display', '_display_single_game']
        
        for manager_class in [NCAABaseballLiveManager, NCAABaseballRecentManager, NCAABaseballUpcomingManager]:
            for method in required_methods:
                assert hasattr(manager_class, method), f"{manager_class.__name__} should have {method} method"
                assert callable(getattr(manager_class, method)), f"{manager_class.__name__}.{method} should be callable"
        
        print("✅ NCAA Baseball managers have all required methods")
        return True
        
    except Exception as e:
        print(f"❌ NCAA Baseball manager methods test failed: {e}")
        return False

def test_baseball_sport_specific_features():
    """Test that managers have baseball-specific features."""
    print("\n🧪 Testing Baseball Sport-Specific Features...")
    
    try:
        from src.milb_managers_v2 import BaseMiLBManager
        from src.ncaa_baseball_managers_v2 import BaseNCAABaseballManager
        
        # Test that managers have baseball-specific methods
        baseball_methods = ['_get_baseball_display_text', '_is_baseball_game_live', '_get_baseball_game_status']
        
        for manager_class in [BaseMiLBManager, BaseNCAABaseballManager]:
            for method in baseball_methods:
                assert hasattr(manager_class, method), f"{manager_class.__name__} should have {method} method"
                assert callable(getattr(manager_class, method)), f"{manager_class.__name__}.{method} should be callable"
        
        print("✅ Baseball managers have sport-specific features")
        return True
        
    except Exception as e:
        print(f"❌ Baseball sport-specific features test failed: {e}")
        return False

def test_manager_configuration():
    """Test that managers use proper sport configuration."""
    print("\n🧪 Testing Manager Configuration...")
    
    try:
        from src.base_classes.sport_configs import get_sport_config
        
        # Test MILB configuration
        milb_config = get_sport_config('milb', None)
        assert milb_config is not None, "MILB should have configuration"
        assert milb_config.sport_specific_fields, "MILB should have sport-specific fields"
        
        # Test NCAA Baseball configuration
        ncaa_baseball_config = get_sport_config('ncaa_baseball', None)
        assert ncaa_baseball_config is not None, "NCAA Baseball should have configuration"
        assert ncaa_baseball_config.sport_specific_fields, "NCAA Baseball should have sport-specific fields"
        
        print("✅ Managers use proper sport configuration")
        return True
        
    except Exception as e:
        print(f"❌ Manager configuration test failed: {e}")
        return False

def main():
    """Run all baseball manager integration tests."""
    print("⚾ Testing Baseball Managers Integration")
    print("=" * 50)
    
    # Configure logging
    logging.basicConfig(level=logging.WARNING)
    
    # Run all tests
    tests = [
        test_milb_manager_imports,
        test_ncaa_baseball_manager_imports,
        test_milb_manager_inheritance,
        test_ncaa_baseball_manager_inheritance,
        test_milb_manager_methods,
        test_ncaa_baseball_manager_methods,
        test_baseball_sport_specific_features,
        test_manager_configuration
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"❌ Test {test.__name__} failed with exception: {e}")
    
    print("\n" + "=" * 50)
    print(f"🏁 Baseball Manager Integration Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All baseball manager integration tests passed! MILB and NCAA Baseball work with the new architecture.")
        return True
    else:
        print("❌ Some baseball manager integration tests failed. Please check the errors above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
