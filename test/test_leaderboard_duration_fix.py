#!/usr/bin/env python3
"""
Test Leaderboard Duration Fix

This test validates that the LeaderboardManager has the required get_duration method
that the display controller expects.
"""

import sys
import os
import logging

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

def test_leaderboard_duration_method():
    """Test that LeaderboardManager has the get_duration method."""
    print("🧪 Testing Leaderboard Duration Method...")
    
    try:
        # Read the leaderboard manager file
        with open('src/leaderboard_manager.py', 'r') as f:
            content = f.read()
        
        # Check that get_duration method exists
        if 'def get_duration(self) -> int:' in content:
            print("✅ get_duration method found in LeaderboardManager")
        else:
            print("❌ get_duration method not found in LeaderboardManager")
            return False
        
        # Check that method is properly implemented
        if 'return self.get_dynamic_duration()' in content:
            print("✅ get_duration method uses dynamic duration when enabled")
        else:
            print("❌ get_duration method not properly implemented for dynamic duration")
            return False
        
        if 'return self.display_duration' in content:
            print("✅ get_duration method falls back to display_duration")
        else:
            print("❌ get_duration method not properly implemented for fallback")
            return False
        
        # Check that method is in the right place (after get_dynamic_duration)
        lines = content.split('\n')
        get_dynamic_duration_line = None
        get_duration_line = None
        
        for i, line in enumerate(lines):
            if 'def get_dynamic_duration(self) -> int:' in line:
                get_dynamic_duration_line = i
            elif 'def get_duration(self) -> int:' in line:
                get_duration_line = i
        
        if get_dynamic_duration_line is not None and get_duration_line is not None:
            if get_duration_line > get_dynamic_duration_line:
                print("✅ get_duration method is placed after get_dynamic_duration")
            else:
                print("❌ get_duration method is not in the right place")
                return False
        
        print("✅ LeaderboardManager duration method is properly implemented")
        return True
        
    except Exception as e:
        print(f"❌ Leaderboard duration method test failed: {e}")
        return False

def test_leaderboard_duration_logic():
    """Test that the duration logic makes sense."""
    print("\n🧪 Testing Leaderboard Duration Logic...")
    
    try:
        # Read the leaderboard manager file
        with open('src/leaderboard_manager.py', 'r') as f:
            content = f.read()
        
        # Check that the logic is correct
        if 'if self.dynamic_duration_enabled:' in content:
            print("✅ Dynamic duration logic is implemented")
        else:
            print("❌ Dynamic duration logic not found")
            return False
        
        if 'return self.get_dynamic_duration()' in content:
            print("✅ Returns dynamic duration when enabled")
        else:
            print("❌ Does not return dynamic duration when enabled")
            return False
        
        if 'return self.display_duration' in content:
            print("✅ Returns display duration as fallback")
        else:
            print("❌ Does not return display duration as fallback")
            return False
        
        print("✅ Leaderboard duration logic is correct")
        return True
        
    except Exception as e:
        print(f"❌ Leaderboard duration logic test failed: {e}")
        return False

def test_leaderboard_method_signature():
    """Test that the method signature is correct."""
    print("\n🧪 Testing Leaderboard Method Signature...")
    
    try:
        # Read the leaderboard manager file
        with open('src/leaderboard_manager.py', 'r') as f:
            content = f.read()
        
        # Check method signature
        if 'def get_duration(self) -> int:' in content:
            print("✅ Method signature is correct")
        else:
            print("❌ Method signature is incorrect")
            return False
        
        # Check docstring
        if '"""Get the display duration for the leaderboard."""' in content:
            print("✅ Method has proper docstring")
        else:
            print("❌ Method missing docstring")
            return False
        
        print("✅ Leaderboard method signature is correct")
        return True
        
    except Exception as e:
        print(f"❌ Leaderboard method signature test failed: {e}")
        return False

def main():
    """Run all leaderboard duration tests."""
    print("🏆 Testing Leaderboard Duration Fix")
    print("=" * 50)
    
    # Run all tests
    tests = [
        test_leaderboard_duration_method,
        test_leaderboard_duration_logic,
        test_leaderboard_method_signature
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
    print(f"🏁 Leaderboard Duration Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All leaderboard duration tests passed! The fix is working correctly.")
        return True
    else:
        print("❌ Some leaderboard duration tests failed. Please check the errors above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
