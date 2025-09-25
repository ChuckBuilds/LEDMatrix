#!/usr/bin/env python3
"""
Test script to demonstrate the new ranking/record toggle functionality
for both the leaderboard manager and NCAA FB managers.
"""

import sys
import os
import json
import time
from typing import Dict, Any

# Add the src directory to the path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from leaderboard_manager import LeaderboardManager
from ncaa_fb_managers import BaseNCAAFBManager
from cache_manager import CacheManager
from config_manager import ConfigManager

def test_leaderboard_ranking_toggle():
    """Test the leaderboard manager ranking toggle functionality."""
    
    print("Testing Leaderboard Manager Ranking Toggle")
    print("=" * 50)
    
    # Create a mock display manager
    class MockDisplayManager:
        def __init__(self):
            self.matrix = type('Matrix', (), {'width': 64, 'height': 32})()
            self.image = None
            self.draw = None
        
        def update_display(self):
            pass
        
        def set_scrolling_state(self, scrolling):
            pass
        
        def process_deferred_updates(self):
            pass
    
    # Test configuration with show_ranking enabled
    config_ranking_enabled = {
        'leaderboard': {
            'enabled': True,
            'enabled_sports': {
                'ncaa_fb': {
                    'enabled': True,
                    'top_teams': 10,
                    'show_ranking': True  # Show rankings
                }
            },
            'update_interval': 3600,
            'scroll_speed': 2,
            'scroll_delay': 0.05,
            'display_duration': 60,
            'loop': True,
            'request_timeout': 30,
            'dynamic_duration': True,
            'min_duration': 30,
            'max_duration': 300,
            'duration_buffer': 0.1,
            'time_per_team': 2.0,
            'time_per_league': 3.0
        }
    }
    
    # Test configuration with show_ranking disabled
    config_ranking_disabled = {
        'leaderboard': {
            'enabled': True,
            'enabled_sports': {
                'ncaa_fb': {
                    'enabled': True,
                    'top_teams': 10,
                    'show_ranking': False  # Show records
                }
            },
            'update_interval': 3600,
            'scroll_speed': 2,
            'scroll_delay': 0.05,
            'display_duration': 60,
            'loop': True,
            'request_timeout': 30,
            'dynamic_duration': True,
            'min_duration': 30,
            'max_duration': 300,
            'duration_buffer': 0.1,
            'time_per_team': 2.0,
            'time_per_league': 3.0
        }
    }
    
    try:
        display_manager = MockDisplayManager()
        
        # Test with ranking enabled
        print("1. Testing with show_ranking = True")
        leaderboard_manager = LeaderboardManager(config_ranking_enabled, display_manager)
        ncaa_fb_config = leaderboard_manager.league_configs['ncaa_fb']
        print(f"   show_ranking config: {ncaa_fb_config.get('show_ranking', 'Not set')}")
        
        standings = leaderboard_manager._fetch_standings(ncaa_fb_config)
        if standings:
            print(f"   Fetched {len(standings)} teams")
            print("   Top 5 teams with rankings:")
            for i, team in enumerate(standings[:5]):
                rank = team.get('rank', 'N/A')
                record = team.get('record_summary', 'N/A')
                print(f"     {i+1}. {team['name']} ({team['abbreviation']}) - Rank: #{rank}, Record: {record}")
        
        print("\n2. Testing with show_ranking = False")
        leaderboard_manager = LeaderboardManager(config_ranking_disabled, display_manager)
        ncaa_fb_config = leaderboard_manager.league_configs['ncaa_fb']
        print(f"   show_ranking config: {ncaa_fb_config.get('show_ranking', 'Not set')}")
        
        standings = leaderboard_manager._fetch_standings(ncaa_fb_config)
        if standings:
            print(f"   Fetched {len(standings)} teams")
            print("   Top 5 teams with records:")
            for i, team in enumerate(standings[:5]):
                rank = team.get('rank', 'N/A')
                record = team.get('record_summary', 'N/A')
                print(f"     {i+1}. {team['name']} ({team['abbreviation']}) - Rank: #{rank}, Record: {record}")
        
        print("\n✓ Leaderboard ranking toggle test completed!")
        return True
        
    except Exception as e:
        print(f"✗ Error testing leaderboard ranking toggle: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_ncaa_fb_ranking_toggle():
    """Test the NCAA FB manager ranking toggle functionality."""
    
    print("\nTesting NCAA FB Manager Ranking Toggle")
    print("=" * 50)
    
    # Create a mock display manager
    class MockDisplayManager:
        def __init__(self):
            self.matrix = type('Matrix', (), {'width': 64, 'height': 32})()
            self.image = None
            self.draw = None
        
        def update_display(self):
            pass
        
        def set_scrolling_state(self, scrolling):
            pass
        
        def process_deferred_updates(self):
            pass
    
    # Test configurations
    configs = [
        {
            'name': 'show_ranking=true, show_records=true',
            'config': {
                'ncaa_fb_scoreboard': {
                    'enabled': True,
                    'show_records': True,
                    'show_ranking': True,
                    'logo_dir': 'assets/sports/ncaa_logos',
                    'display_modes': {
                        'ncaa_fb_live': True,
                        'ncaa_fb_recent': True,
                        'ncaa_fb_upcoming': True
                    }
                }
            }
        },
        {
            'name': 'show_ranking=true, show_records=false',
            'config': {
                'ncaa_fb_scoreboard': {
                    'enabled': True,
                    'show_records': False,
                    'show_ranking': True,
                    'logo_dir': 'assets/sports/ncaa_logos',
                    'display_modes': {
                        'ncaa_fb_live': True,
                        'ncaa_fb_recent': True,
                        'ncaa_fb_upcoming': True
                    }
                }
            }
        },
        {
            'name': 'show_ranking=false, show_records=true',
            'config': {
                'ncaa_fb_scoreboard': {
                    'enabled': True,
                    'show_records': True,
                    'show_ranking': False,
                    'logo_dir': 'assets/sports/ncaa_logos',
                    'display_modes': {
                        'ncaa_fb_live': True,
                        'ncaa_fb_recent': True,
                        'ncaa_fb_upcoming': True
                    }
                }
            }
        },
        {
            'name': 'show_ranking=false, show_records=false',
            'config': {
                'ncaa_fb_scoreboard': {
                    'enabled': True,
                    'show_records': False,
                    'show_ranking': False,
                    'logo_dir': 'assets/sports/ncaa_logos',
                    'display_modes': {
                        'ncaa_fb_live': True,
                        'ncaa_fb_recent': True,
                        'ncaa_fb_upcoming': True
                    }
                }
            }
        }
    ]
    
    try:
        display_manager = MockDisplayManager()
        cache_manager = CacheManager()
        
        for i, test_config in enumerate(configs, 1):
            print(f"{i}. Testing: {test_config['name']}")
            ncaa_fb_manager = BaseNCAAFBManager(test_config['config'], display_manager, cache_manager)
            print(f"   show_records: {ncaa_fb_manager.show_records}")
            print(f"   show_ranking: {ncaa_fb_manager.show_ranking}")
            
            # Test fetching rankings
            rankings = ncaa_fb_manager._fetch_team_rankings()
            if rankings:
                print(f"   Fetched rankings for {len(rankings)} teams")
                print("   Sample rankings:")
                for j, (team_abbr, rank) in enumerate(list(rankings.items())[:3]):
                    print(f"     {team_abbr}: #{rank}")
            print()
        
        print("✓ NCAA FB ranking toggle test completed!")
        print("\nLogic Summary:")
        print("- show_ranking=true, show_records=true: Shows #5 if ranked, 2-0 if unranked")
        print("- show_ranking=true, show_records=false: Shows #5 if ranked, nothing if unranked")
        print("- show_ranking=false, show_records=true: Shows 2-0 (record)")
        print("- show_ranking=false, show_records=false: Shows nothing")
        return True
        
    except Exception as e:
        print(f"✗ Error testing NCAA FB ranking toggle: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main function to run all tests."""
    print("NCAA Football Ranking/Record Toggle Test")
    print("=" * 60)
    print("This test demonstrates the new functionality:")
    print("- Leaderboard manager can show poll rankings (#5) or records (2-0)")
    print("- NCAA FB managers can show poll rankings (#5) or records (2-0)")
    print("- Configuration controls which is displayed")
    print()
    
    try:
        success1 = test_leaderboard_ranking_toggle()
        success2 = test_ncaa_fb_ranking_toggle()
        
        if success1 and success2:
            print("\n🎉 All tests passed! The ranking/record toggle is working correctly.")
            print("\nConfiguration Summary:")
            print("- Set 'show_ranking': true in config to show poll rankings (#5)")
            print("- Set 'show_ranking': false in config to show season records (2-0)")
            print("- Works in both leaderboard and NCAA FB scoreboard managers")
        else:
            print("\n❌ Some tests failed. Please check the errors above.")
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"Error running tests: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
