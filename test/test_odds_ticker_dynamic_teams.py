#!/usr/bin/env python3
"""
Test script to verify odds ticker works with dynamic teams.
This test checks that AP_TOP_25 is properly resolved in the odds ticker.
"""

import sys
import os
import json
from datetime import datetime, timedelta
import pytz

# Add the project root to the path so we can import the modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.odds_ticker_manager import OddsTickerManager
from src.display_manager import DisplayManager

def create_test_config():
    """Create a test configuration with dynamic teams for odds ticker."""
    config = {
        "odds_ticker": {
            "enabled": True,
            "show_favorite_teams_only": True,
            "enabled_leagues": ["ncaa_fb"],
            "games_per_favorite_team": 1,
            "max_games_per_league": 5,
            "update_interval": 3600
        },
        "ncaa_fb_scoreboard": {
            "enabled": True,
            "favorite_teams": [
                "UGA",
                "AP_TOP_25"
            ]
        },
        "display": {
            "hardware": {
                "rows": 32,
                "cols": 64,
                "chain_length": 1
            }
        },
        "timezone": "America/Chicago"
    }
    return config

def test_odds_ticker_dynamic_teams():
    """Test that odds ticker properly resolves dynamic teams."""
    print("Testing OddsTickerManager with dynamic teams...")
    
    # Create test configuration
    config = create_test_config()
    
    # Create mock display manager
    display_manager = DisplayManager(config)
    
    # Create OddsTickerManager instance
    odds_ticker = OddsTickerManager(config, display_manager)
    
    # Check that dynamic resolver is available
    assert hasattr(odds_ticker, 'dynamic_resolver'), "OddsTickerManager should have dynamic_resolver attribute"
    assert odds_ticker.dynamic_resolver is not None, "Dynamic resolver should be initialized"
    
    # Check that NCAA FB league config has resolved teams
    ncaa_fb_config = odds_ticker.league_configs.get('ncaa_fb', {})
    assert ncaa_fb_config.get('enabled', False), "NCAA FB should be enabled"
    
    favorite_teams = ncaa_fb_config.get('favorite_teams', [])
    print(f"NCAA FB favorite teams: {favorite_teams}")
    
    # Verify that UGA is still in the list
    assert "UGA" in favorite_teams, "UGA should be in resolved teams"
    
    # Verify that AP_TOP_25 was resolved to actual teams
    assert len(favorite_teams) > 1, "Should have more than 1 team after resolving AP_TOP_25"
    
    # Verify that AP_TOP_25 is not in the final list (should be resolved)
    assert "AP_TOP_25" not in favorite_teams, "AP_TOP_25 should be resolved, not left as-is"
    
    print(f"✓ OddsTickerManager successfully resolved dynamic teams")
    print(f"✓ Final favorite teams: {favorite_teams[:10]}{'...' if len(favorite_teams) > 10 else ''}")
    
    return True

def test_odds_ticker_regular_teams():
    """Test that odds ticker works with regular teams (no dynamic teams)."""
    print("Testing OddsTickerManager with regular teams...")
    
    config = {
        "odds_ticker": {
            "enabled": True,
            "show_favorite_teams_only": True,
            "enabled_leagues": ["ncaa_fb"],
            "games_per_favorite_team": 1,
            "max_games_per_league": 5,
            "update_interval": 3600
        },
        "ncaa_fb_scoreboard": {
            "enabled": True,
            "favorite_teams": [
                "UGA",
                "AUB"
            ]
        },
        "display": {
            "hardware": {
                "rows": 32,
                "cols": 64,
                "chain_length": 1
            }
        },
        "timezone": "America/Chicago"
    }
    
    display_manager = DisplayManager(config)
    odds_ticker = OddsTickerManager(config, display_manager)
    
    # Check that regular teams are preserved
    ncaa_fb_config = odds_ticker.league_configs.get('ncaa_fb', {})
    favorite_teams = ncaa_fb_config.get('favorite_teams', [])
    
    assert favorite_teams == ["UGA", "AUB"], "Regular teams should be preserved unchanged"
    print("✓ Regular teams work correctly")
    
    return True

def test_odds_ticker_mixed_teams():
    """Test odds ticker with mixed regular and dynamic teams."""
    print("Testing OddsTickerManager with mixed teams...")
    
    config = {
        "odds_ticker": {
            "enabled": True,
            "show_favorite_teams_only": True,
            "enabled_leagues": ["ncaa_fb"],
            "games_per_favorite_team": 1,
            "max_games_per_league": 5,
            "update_interval": 3600
        },
        "ncaa_fb_scoreboard": {
            "enabled": True,
            "favorite_teams": [
                "UGA",
                "AP_TOP_10",
                "AUB"
            ]
        },
        "display": {
            "hardware": {
                "rows": 32,
                "cols": 64,
                "chain_length": 1
            }
        },
        "timezone": "America/Chicago"
    }
    
    display_manager = DisplayManager(config)
    odds_ticker = OddsTickerManager(config, display_manager)
    
    ncaa_fb_config = odds_ticker.league_configs.get('ncaa_fb', {})
    favorite_teams = ncaa_fb_config.get('favorite_teams', [])
    
    # Verify that UGA and AUB are still in the list
    assert "UGA" in favorite_teams, "UGA should be in resolved teams"
    assert "AUB" in favorite_teams, "AUB should be in resolved teams"
    
    # Verify that AP_TOP_10 was resolved to actual teams
    assert len(favorite_teams) > 2, "Should have more than 2 teams after resolving AP_TOP_10"
    
    # Verify that AP_TOP_10 is not in the final list (should be resolved)
    assert "AP_TOP_10" not in favorite_teams, "AP_TOP_10 should be resolved, not left as-is"
    
    print(f"✓ Mixed teams work correctly: {favorite_teams[:10]}{'...' if len(favorite_teams) > 10 else ''}")
    
    return True

if __name__ == "__main__":
    try:
        print("🧪 Testing OddsTickerManager with Dynamic Teams...")
        print("=" * 60)
        
        test_odds_ticker_dynamic_teams()
        test_odds_ticker_regular_teams()
        test_odds_ticker_mixed_teams()
        
        print("\n🎉 All odds ticker dynamic teams tests passed!")
        print("AP_TOP_25 will work correctly with the odds ticker!")
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
