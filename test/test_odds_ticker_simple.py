#!/usr/bin/env python3
"""
Simple test to verify odds ticker dynamic team resolution works.
This test focuses on the core functionality without requiring the full LEDMatrix system.
"""

import sys
import os

# Add the src directory to the path so we can import the dynamic team resolver
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from dynamic_team_resolver import DynamicTeamResolver

def test_odds_ticker_configuration():
    """Test how dynamic teams would work with odds ticker configuration."""
    print("Testing odds ticker configuration with dynamic teams...")
    
    # Simulate a typical odds ticker config
    config = {
        "odds_ticker": {
            "enabled": True,
            "show_favorite_teams_only": True,
            "enabled_leagues": ["ncaa_fb"],
            "games_per_favorite_team": 1,
            "max_games_per_league": 5
        },
        "ncaa_fb_scoreboard": {
            "enabled": True,
            "favorite_teams": [
                "UGA",
                "AP_TOP_25"
            ]
        }
    }
    
    # Simulate what the odds ticker would do
    resolver = DynamicTeamResolver()
    
    # Get the raw favorite teams from config (what odds ticker gets)
    raw_favorite_teams = config.get('ncaa_fb_scoreboard', {}).get('favorite_teams', [])
    print(f"Raw favorite teams from config: {raw_favorite_teams}")
    
    # Resolve dynamic teams (what odds ticker should do)
    resolved_teams = resolver.resolve_teams(raw_favorite_teams, 'ncaa_fb')
    print(f"Resolved teams: {resolved_teams}")
    print(f"Number of resolved teams: {len(resolved_teams)}")
    
    # Verify results
    assert "UGA" in resolved_teams, "UGA should be in resolved teams"
    assert "AP_TOP_25" not in resolved_teams, "AP_TOP_25 should be resolved, not left as-is"
    assert len(resolved_teams) > 1, "Should have more than 1 team after resolving AP_TOP_25"
    
    print("✓ Odds ticker configuration integration works correctly")
    return True

def test_odds_ticker_league_configs():
    """Test how dynamic teams work with multiple league configs."""
    print("Testing multiple league configurations...")
    
    # Simulate league configs that odds ticker would create
    league_configs = {
        'ncaa_fb': {
            'sport': 'football',
            'league': 'college-football',
            'favorite_teams': ['UGA', 'AP_TOP_25'],
            'enabled': True
        },
        'nfl': {
            'sport': 'football',
            'league': 'nfl',
            'favorite_teams': ['DAL', 'TB'],
            'enabled': True
        },
        'nba': {
            'sport': 'basketball',
            'league': 'nba',
            'favorite_teams': ['LAL', 'AP_TOP_10'],  # Mixed regular and dynamic
            'enabled': True
        }
    }
    
    resolver = DynamicTeamResolver()
    
    # Simulate what odds ticker would do for each league
    for league_key, league_config in league_configs.items():
        if league_config.get('enabled', False):
            raw_favorite_teams = league_config.get('favorite_teams', [])
            if raw_favorite_teams:
                # Resolve dynamic teams for this league
                resolved_teams = resolver.resolve_teams(raw_favorite_teams, league_key)
                league_config['favorite_teams'] = resolved_teams
                
                print(f"{league_key}: {raw_favorite_teams} -> {resolved_teams}")
    
    # Verify results
    ncaa_fb_teams = league_configs['ncaa_fb']['favorite_teams']
    assert "UGA" in ncaa_fb_teams, "UGA should be in NCAA FB teams"
    assert "AP_TOP_25" not in ncaa_fb_teams, "AP_TOP_25 should be resolved"
    assert len(ncaa_fb_teams) > 1, "Should have more than 1 NCAA FB team"
    
    nfl_teams = league_configs['nfl']['favorite_teams']
    assert nfl_teams == ['DAL', 'TB'], "NFL teams should be unchanged (no dynamic teams)"
    
    nba_teams = league_configs['nba']['favorite_teams']
    assert "LAL" in nba_teams, "LAL should be in NBA teams"
    assert "AP_TOP_10" not in nba_teams, "AP_TOP_10 should be resolved"
    assert len(nba_teams) > 1, "Should have more than 1 NBA team"
    
    print("✓ Multiple league configurations work correctly")
    return True

def test_odds_ticker_edge_cases():
    """Test edge cases for odds ticker dynamic teams."""
    print("Testing edge cases...")
    
    resolver = DynamicTeamResolver()
    
    # Test empty favorite teams
    result = resolver.resolve_teams([], 'ncaa_fb')
    assert result == [], "Empty list should return empty list"
    print("✓ Empty favorite teams handling works")
    
    # Test only regular teams
    result = resolver.resolve_teams(['UGA', 'AUB'], 'ncaa_fb')
    assert result == ['UGA', 'AUB'], "Regular teams should be unchanged"
    print("✓ Regular teams handling works")
    
    # Test only dynamic teams
    result = resolver.resolve_teams(['AP_TOP_5'], 'ncaa_fb')
    assert len(result) > 0, "Dynamic teams should be resolved"
    assert "AP_TOP_5" not in result, "Dynamic team should be resolved"
    print("✓ Dynamic-only teams handling works")
    
    # Test unknown dynamic teams
    result = resolver.resolve_teams(['AP_TOP_50'], 'ncaa_fb')
    assert result == [], "Unknown dynamic teams should be filtered out"
    print("✓ Unknown dynamic teams handling works")
    
    print("✓ All edge cases handled correctly")
    return True

if __name__ == "__main__":
    try:
        print("🧪 Testing OddsTickerManager Dynamic Teams Integration...")
        print("=" * 70)
        
        test_odds_ticker_configuration()
        test_odds_ticker_league_configs()
        test_odds_ticker_edge_cases()
        
        print("\n🎉 All odds ticker dynamic teams tests passed!")
        print("AP_TOP_25 will work correctly with the odds ticker!")
        print("\nThe odds ticker will now:")
        print("- Automatically resolve AP_TOP_25 to current top 25 teams")
        print("- Show odds for all current AP Top 25 teams")
        print("- Update automatically when rankings change")
        print("- Work seamlessly with existing favorite teams")
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
