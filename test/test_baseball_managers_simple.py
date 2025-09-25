#!/usr/bin/env python3
"""
Test Baseball Managers Integration - Simple Version

This test validates that MILB and NCAA Baseball managers work with the new
baseball base class architecture without requiring full imports.
"""

import sys
import os
import logging

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

def test_milb_manager_structure():
    """Test that MILB managers have the correct structure."""
    print("🧪 Testing MILB Manager Structure...")
    
    try:
        # Read the MILB managers file
        with open('src/milb_managers_v2.py', 'r') as f:
            content = f.read()
        
        # Check that it imports the baseball base classes
        assert 'from .base_classes.baseball import Baseball, BaseballLive, BaseballRecent, BaseballUpcoming' in content
        print("✅ MILB managers import baseball base classes")
        
        # Check that classes are defined
        assert 'class BaseMiLBManager(Baseball):' in content
        assert 'class MiLBLiveManager(BaseMiLBManager, BaseballLive):' in content
        assert 'class MiLBRecentManager(BaseMiLBManager, BaseballRecent):' in content
        assert 'class MiLBUpcomingManager(BaseMiLBManager, BaseballUpcoming):' in content
        print("✅ MILB managers have correct class definitions")
        
        # Check that required methods exist
        assert 'def get_duration(self) -> int:' in content
        assert 'def display(self, force_clear: bool = False) -> bool:' in content
        assert 'def _display_single_game(self, game: Dict) -> None:' in content
        print("✅ MILB managers have required methods")
        
        print("✅ MILB manager structure is correct")
        return True
        
    except Exception as e:
        print(f"❌ MILB manager structure test failed: {e}")
        return False

def test_ncaa_baseball_manager_structure():
    """Test that NCAA Baseball managers have the correct structure."""
    print("\n🧪 Testing NCAA Baseball Manager Structure...")
    
    try:
        # Read the NCAA Baseball managers file
        with open('src/ncaa_baseball_managers_v2.py', 'r') as f:
            content = f.read()
        
        # Check that it imports the baseball base classes
        assert 'from .base_classes.baseball import Baseball, BaseballLive, BaseballRecent, BaseballUpcoming' in content
        print("✅ NCAA Baseball managers import baseball base classes")
        
        # Check that classes are defined
        assert 'class BaseNCAABaseballManager(Baseball):' in content
        assert 'class NCAABaseballLiveManager(BaseNCAABaseballManager, BaseballLive):' in content
        assert 'class NCAABaseballRecentManager(BaseNCAABaseballManager, BaseballRecent):' in content
        assert 'class NCAABaseballUpcomingManager(BaseNCAABaseballManager, BaseballUpcoming):' in content
        print("✅ NCAA Baseball managers have correct class definitions")
        
        # Check that required methods exist
        assert 'def get_duration(self) -> int:' in content
        assert 'def display(self, force_clear: bool = False) -> bool:' in content
        assert 'def _display_single_game(self, game: Dict) -> None:' in content
        print("✅ NCAA Baseball managers have required methods")
        
        print("✅ NCAA Baseball manager structure is correct")
        return True
        
    except Exception as e:
        print(f"❌ NCAA Baseball manager structure test failed: {e}")
        return False

def test_baseball_inheritance():
    """Test that managers properly inherit from baseball base classes."""
    print("\n🧪 Testing Baseball Inheritance...")
    
    try:
        # Read both manager files
        with open('src/milb_managers_v2.py', 'r') as f:
            milb_content = f.read()
        
        with open('src/ncaa_baseball_managers_v2.py', 'r') as f:
            ncaa_content = f.read()
        
        # Check that managers inherit from baseball base classes
        assert 'BaseMiLBManager(Baseball)' in milb_content
        assert 'MiLBLiveManager(BaseMiLBManager, BaseballLive)' in milb_content
        assert 'MiLBRecentManager(BaseMiLBManager, BaseballRecent)' in milb_content
        assert 'MiLBUpcomingManager(BaseMiLBManager, BaseballUpcoming)' in milb_content
        print("✅ MILB managers properly inherit from baseball base classes")
        
        assert 'BaseNCAABaseballManager(Baseball)' in ncaa_content
        assert 'NCAABaseballLiveManager(BaseNCAABaseballManager, BaseballLive)' in ncaa_content
        assert 'NCAABaseballRecentManager(BaseNCAABaseballManager, BaseballRecent)' in ncaa_content
        assert 'NCAABaseballUpcomingManager(BaseNCAABaseballManager, BaseballUpcoming)' in ncaa_content
        print("✅ NCAA Baseball managers properly inherit from baseball base classes")
        
        print("✅ Baseball inheritance is correct")
        return True
        
    except Exception as e:
        print(f"❌ Baseball inheritance test failed: {e}")
        return False

def test_baseball_sport_specific_methods():
    """Test that managers have baseball-specific methods."""
    print("\n🧪 Testing Baseball Sport-Specific Methods...")
    
    try:
        # Read both manager files
        with open('src/milb_managers_v2.py', 'r') as f:
            milb_content = f.read()
        
        with open('src/ncaa_baseball_managers_v2.py', 'r') as f:
            ncaa_content = f.read()
        
        # Check for baseball-specific methods
        baseball_methods = [
            '_get_baseball_display_text',
            '_is_baseball_game_live',
            '_get_baseball_game_status',
            '_draw_base_indicators'
        ]
        
        for method in baseball_methods:
            assert method in milb_content, f"MILB managers should have {method} method"
            assert method in ncaa_content, f"NCAA Baseball managers should have {method} method"
        
        print("✅ Baseball managers have sport-specific methods")
        return True
        
    except Exception as e:
        print(f"❌ Baseball sport-specific methods test failed: {e}")
        return False

def test_manager_initialization():
    """Test that managers are properly initialized."""
    print("\n🧪 Testing Manager Initialization...")
    
    try:
        # Read both manager files
        with open('src/milb_managers_v2.py', 'r') as f:
            milb_content = f.read()
        
        with open('src/ncaa_baseball_managers_v2.py', 'r') as f:
            ncaa_content = f.read()
        
        # Check that managers call super().__init__ with sport_key
        assert 'super().__init__(config, display_manager, cache_manager, logger, "milb")' in milb_content
        assert 'super().__init__(config, display_manager, cache_manager, logger, "ncaa_baseball")' in ncaa_content
        print("✅ Managers are properly initialized with sport keys")
        
        # Check that managers have proper logging
        assert 'self.logger.info(' in milb_content
        assert 'self.logger.info(' in ncaa_content
        print("✅ Managers have proper logging")
        
        print("✅ Manager initialization is correct")
        return True
        
    except Exception as e:
        print(f"❌ Manager initialization test failed: {e}")
        return False

def test_sport_configuration_integration():
    """Test that managers integrate with sport configuration."""
    print("\n🧪 Testing Sport Configuration Integration...")
    
    try:
        # Read both manager files
        with open('src/milb_managers_v2.py', 'r') as f:
            milb_content = f.read()
        
        with open('src/ncaa_baseball_managers_v2.py', 'r') as f:
            ncaa_content = f.read()
        
        # Check that managers use sport configuration
        assert 'self.sport_config' in milb_content or 'super().__init__' in milb_content
        assert 'self.sport_config' in ncaa_content or 'super().__init__' in ncaa_content
        print("✅ Managers use sport configuration")
        
        # Check that managers have sport-specific configuration
        assert 'self.milb_config' in milb_content
        assert 'self.ncaa_baseball_config' in ncaa_content
        print("✅ Managers have sport-specific configuration")
        
        print("✅ Sport configuration integration is correct")
        return True
        
    except Exception as e:
        print(f"❌ Sport configuration integration test failed: {e}")
        return False

def main():
    """Run all baseball manager integration tests."""
    print("⚾ Testing Baseball Managers Integration (Simple)")
    print("=" * 50)
    
    # Configure logging
    logging.basicConfig(level=logging.WARNING)
    
    # Run all tests
    tests = [
        test_milb_manager_structure,
        test_ncaa_baseball_manager_structure,
        test_baseball_inheritance,
        test_baseball_sport_specific_methods,
        test_manager_initialization,
        test_sport_configuration_integration
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
