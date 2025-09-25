#!/usr/bin/env python3
"""
Test script to verify dynamic team resolver functionality.
This test checks that AP_TOP_25 and other dynamic team names are resolved correctly.
"""

import sys
import os
import json
from datetime import datetime, timedelta
import pytz

# Add the src directory to the path so we can import the dynamic team resolver
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from dynamic_team_resolver import DynamicTeamResolver, resolve_dynamic_teams

def test_dynamic_team_resolver():
    """Test the dynamic team resolver functionality."""
    print("Testing Dynamic Team Resolver...")
    
    # Test 1: Basic dynamic team resolution
    print("\n1. Testing basic dynamic team resolution...")
    resolver = DynamicTeamResolver()
    
    # Test with mixed regular and dynamic teams
    test_teams = ["UGA", "AP_TOP_25", "AUB", "AP_TOP_10"]
    resolved_teams = resolver.resolve_teams(test_teams, 'ncaa_fb')
    
    print(f"Input teams: {test_teams}")
    print(f"Resolved teams: {resolved_teams}")
    print(f"Number of resolved teams: {len(resolved_teams)}")
    
    # Verify that UGA and AUB are still in the list
    assert "UGA" in resolved_teams, "UGA should be in resolved teams"
    assert "AUB" in resolved_teams, "AUB should be in resolved teams"
    
    # Verify that AP_TOP_25 and AP_TOP_10 are resolved to actual teams
    assert len(resolved_teams) > 4, "Should have more than 4 teams after resolving dynamic teams"
    
    print("✓ Basic dynamic team resolution works")
    
    # Test 2: Test dynamic team detection
    print("\n2. Testing dynamic team detection...")
    assert resolver.is_dynamic_team("AP_TOP_25"), "AP_TOP_25 should be detected as dynamic"
    assert resolver.is_dynamic_team("AP_TOP_10"), "AP_TOP_10 should be detected as dynamic"
    assert resolver.is_dynamic_team("AP_TOP_5"), "AP_TOP_5 should be detected as dynamic"
    assert not resolver.is_dynamic_team("UGA"), "UGA should not be detected as dynamic"
    assert not resolver.is_dynamic_team("AUB"), "AUB should not be detected as dynamic"
    
    print("✓ Dynamic team detection works")
    
    # Test 3: Test available dynamic teams
    print("\n3. Testing available dynamic teams...")
    available_teams = resolver.get_available_dynamic_teams()
    expected_teams = ["AP_TOP_25", "AP_TOP_10", "AP_TOP_5"]
    
    for team in expected_teams:
        assert team in available_teams, f"{team} should be in available dynamic teams"
    
    print(f"Available dynamic teams: {available_teams}")
    print("✓ Available dynamic teams list works")
    
    # Test 4: Test convenience function
    print("\n4. Testing convenience function...")
    convenience_result = resolve_dynamic_teams(["UGA", "AP_TOP_5"], 'ncaa_fb')
    assert "UGA" in convenience_result, "Convenience function should include UGA"
    assert len(convenience_result) > 1, "Convenience function should resolve AP_TOP_5"
    
    print(f"Convenience function result: {convenience_result}")
    print("✓ Convenience function works")
    
    # Test 5: Test cache functionality
    print("\n5. Testing cache functionality...")
    # First call should populate cache
    start_time = datetime.now()
    result1 = resolver.resolve_teams(["AP_TOP_25"], 'ncaa_fb')
    first_call_time = (datetime.now() - start_time).total_seconds()
    
    # Second call should use cache (should be faster)
    start_time = datetime.now()
    result2 = resolver.resolve_teams(["AP_TOP_25"], 'ncaa_fb')
    second_call_time = (datetime.now() - start_time).total_seconds()
    
    assert result1 == result2, "Cached results should be identical"
    print(f"First call time: {first_call_time:.3f}s")
    print(f"Second call time: {second_call_time:.3f}s")
    print("✓ Cache functionality works")
    
    # Test 6: Test cache clearing
    print("\n6. Testing cache clearing...")
    resolver.clear_cache()
    assert not resolver._rankings_cache, "Cache should be empty after clearing"
    print("✓ Cache clearing works")
    
    print("\n🎉 All tests passed! Dynamic team resolver is working correctly.")

def test_edge_cases():
    """Test edge cases for the dynamic team resolver."""
    print("\nTesting edge cases...")
    
    resolver = DynamicTeamResolver()
    
    # Test empty list
    result = resolver.resolve_teams([], 'ncaa_fb')
    assert result == [], "Empty list should return empty list"
    print("✓ Empty list handling works")
    
    # Test list with only regular teams
    result = resolver.resolve_teams(["UGA", "AUB"], 'ncaa_fb')
    assert result == ["UGA", "AUB"], "Regular teams should be returned unchanged"
    print("✓ Regular teams handling works")
    
    # Test list with only dynamic teams
    result = resolver.resolve_teams(["AP_TOP_25"], 'ncaa_fb')
    assert len(result) > 0, "Dynamic teams should be resolved"
    print("✓ Dynamic-only teams handling works")
    
    # Test unknown dynamic team
    result = resolver.resolve_teams(["AP_TOP_50"], 'ncaa_fb')
    assert result == [], "Unknown dynamic teams should return empty list"
    print("✓ Unknown dynamic teams handling works")
    
    print("✓ All edge cases handled correctly")

if __name__ == "__main__":
    try:
        test_dynamic_team_resolver()
        test_edge_cases()
        print("\n🎉 All dynamic team resolver tests passed!")
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
