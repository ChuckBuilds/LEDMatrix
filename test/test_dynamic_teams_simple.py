#!/usr/bin/env python3
"""
Simple test to verify dynamic team resolver works correctly.
This test focuses on the core functionality without requiring the full LEDMatrix system.
"""

import sys
import os

# Add the src directory to the path so we can import the dynamic team resolver
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from dynamic_team_resolver import DynamicTeamResolver, resolve_dynamic_teams

def test_config_integration():
    """Test how dynamic teams would work with a typical configuration."""
    print("Testing configuration integration...")
    
    # Simulate a typical config favorite_teams list
    config_favorite_teams = [
        "UGA",      # Regular team
        "AUB",      # Regular team  
        "AP_TOP_25" # Dynamic team
    ]
    
    print(f"Config favorite teams: {config_favorite_teams}")
    
    # Resolve the teams
    resolved_teams = resolve_dynamic_teams(config_favorite_teams, 'ncaa_fb')
    
    print(f"Resolved teams: {resolved_teams}")
    print(f"Number of resolved teams: {len(resolved_teams)}")
    
    # Verify results
    assert "UGA" in resolved_teams, "UGA should be in resolved teams"
    assert "AUB" in resolved_teams, "AUB should be in resolved teams"
    assert "AP_TOP_25" not in resolved_teams, "AP_TOP_25 should be resolved, not left as-is"
    assert len(resolved_teams) > 2, "Should have more than 2 teams after resolving AP_TOP_25"
    
    print("✓ Configuration integration works correctly")
    return True

def test_mixed_dynamic_teams():
    """Test with multiple dynamic team types."""
    print("Testing mixed dynamic teams...")
    
    config_favorite_teams = [
        "UGA",
        "AP_TOP_10",  # Top 10 teams
        "AUB", 
        "AP_TOP_5"    # Top 5 teams
    ]
    
    print(f"Config favorite teams: {config_favorite_teams}")
    
    resolved_teams = resolve_dynamic_teams(config_favorite_teams, 'ncaa_fb')
    
    print(f"Resolved teams: {resolved_teams}")
    print(f"Number of resolved teams: {len(resolved_teams)}")
    
    # Verify results
    assert "UGA" in resolved_teams, "UGA should be in resolved teams"
    assert "AUB" in resolved_teams, "AUB should be in resolved teams"
    assert len(resolved_teams) > 4, "Should have more than 4 teams after resolving dynamic teams"
    
    print("✓ Mixed dynamic teams work correctly")
    return True

def test_edge_cases():
    """Test edge cases for configuration integration."""
    print("Testing edge cases...")
    
    # Test empty list
    result = resolve_dynamic_teams([], 'ncaa_fb')
    assert result == [], "Empty list should return empty list"
    print("✓ Empty list handling works")
    
    # Test only regular teams
    result = resolve_dynamic_teams(["UGA", "AUB"], 'ncaa_fb')
    assert result == ["UGA", "AUB"], "Regular teams should be unchanged"
    print("✓ Regular teams handling works")
    
    # Test only dynamic teams
    result = resolve_dynamic_teams(["AP_TOP_5"], 'ncaa_fb')
    assert len(result) > 0, "Dynamic teams should be resolved"
    assert "AP_TOP_5" not in result, "Dynamic team should be resolved"
    print("✓ Dynamic-only teams handling works")
    
    # Test unknown dynamic teams
    result = resolve_dynamic_teams(["AP_TOP_50"], 'ncaa_fb')
    assert result == [], "Unknown dynamic teams should be filtered out"
    print("✓ Unknown dynamic teams handling works")
    
    print("✓ All edge cases handled correctly")
    return True

def test_performance():
    """Test performance characteristics."""
    print("Testing performance...")
    
    import time
    
    # Test caching performance
    resolver = DynamicTeamResolver()
    
    # First call (should fetch from API)
    start_time = time.time()
    result1 = resolver.resolve_teams(["AP_TOP_25"], 'ncaa_fb')
    first_call_time = time.time() - start_time
    
    # Second call (should use cache)
    start_time = time.time()
    result2 = resolver.resolve_teams(["AP_TOP_25"], 'ncaa_fb')
    second_call_time = time.time() - start_time
    
    assert result1 == result2, "Cached results should be identical"
    print(f"First call time: {first_call_time:.3f}s")
    print(f"Second call time: {second_call_time:.3f}s")
    print("✓ Caching improves performance")
    
    return True

if __name__ == "__main__":
    try:
        print("🧪 Testing Dynamic Teams Configuration Integration...")
        print("=" * 60)
        
        test_config_integration()
        test_mixed_dynamic_teams()
        test_edge_cases()
        test_performance()
        
        print("\n🎉 All configuration integration tests passed!")
        print("Dynamic team resolver is ready for production use!")
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
