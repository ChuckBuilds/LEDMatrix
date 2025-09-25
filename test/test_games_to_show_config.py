#!/usr/bin/env python3
"""
Test script to verify that *_games_to_show configuration settings are working correctly
across all sports managers.
"""

import json
import sys
import os

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def load_config():
    """Load the configuration file."""
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')
    with open(config_path, 'r') as f:
        return json.load(f)

def test_config_values():
    """Test that config values are set correctly."""
    config = load_config()
    
    print("Testing *_games_to_show configuration values:")
    print("=" * 50)
    
    sports_configs = [
        ("NHL", config.get('nhl_scoreboard', {})),
        ("NBA", config.get('nba_scoreboard', {})),
        ("NFL", config.get('nfl_scoreboard', {})),
        ("NCAA Football", config.get('ncaa_fb_scoreboard', {})),
        ("NCAA Baseball", config.get('ncaa_baseball_scoreboard', {})),
        ("NCAA Basketball", config.get('ncaam_basketball_scoreboard', {})),
        ("MLB", config.get('mlb_scoreboard', {})),
        ("MiLB", config.get('milb_scoreboard', {})),
        ("Soccer", config.get('soccer_scoreboard', {}))
    ]
    
    for sport_name, sport_config in sports_configs:
        recent_games = sport_config.get('recent_games_to_show', 'NOT_SET')
        upcoming_games = sport_config.get('upcoming_games_to_show', 'NOT_SET')
        
        print(f"{sport_name:15} | Recent: {recent_games:2} | Upcoming: {upcoming_games:2}")
    
    print("\nExpected behavior:")
    print("- When recent_games_to_show = 1: Only show 1 most recent game")
    print("- When upcoming_games_to_show = 1: Only show 1 next upcoming game")
    print("- When values > 1: Show multiple games and rotate through them")

def test_manager_defaults():
    """Test that managers have correct default values."""
    print("\n" + "=" * 50)
    print("Testing manager default values:")
    print("=" * 50)
    
    # Test the default values that managers use when config is not set
    manager_defaults = {
        "NHL": {"recent": 5, "upcoming": 5},
        "NBA": {"recent": 5, "upcoming": 5},
        "NFL": {"recent": 5, "upcoming": 10},
        "NCAA Football": {"recent": 5, "upcoming": 10},
        "NCAA Baseball": {"recent": 5, "upcoming": 5},
        "NCAA Basketball": {"recent": 5, "upcoming": 5},
        "MLB": {"recent": 5, "upcoming": 10},
        "MiLB": {"recent": 5, "upcoming": 10},
        "Soccer": {"recent": 5, "upcoming": 5}
    }
    
    for sport_name, defaults in manager_defaults.items():
        print(f"{sport_name:15} | Recent default: {defaults['recent']:2} | Upcoming default: {defaults['upcoming']:2}")

def test_config_consistency():
    """Test for consistency between config values and expected behavior."""
    config = load_config()
    
    print("\n" + "=" * 50)
    print("Testing config consistency:")
    print("=" * 50)
    
    sports_configs = [
        ("NHL", config.get('nhl_scoreboard', {})),
        ("NBA", config.get('nba_scoreboard', {})),
        ("NFL", config.get('nfl_scoreboard', {})),
        ("NCAA Football", config.get('ncaa_fb_scoreboard', {})),
        ("NCAA Baseball", config.get('ncaa_baseball_scoreboard', {})),
        ("NCAA Basketball", config.get('ncaam_basketball_scoreboard', {})),
        ("MLB", config.get('mlb_scoreboard', {})),
        ("MiLB", config.get('milb_scoreboard', {})),
        ("Soccer", config.get('soccer_scoreboard', {}))
    ]
    
    issues_found = []
    
    for sport_name, sport_config in sports_configs:
        recent_games = sport_config.get('recent_games_to_show')
        upcoming_games = sport_config.get('upcoming_games_to_show')
        
        if recent_games is None:
            issues_found.append(f"{sport_name}: recent_games_to_show not set")
        if upcoming_games is None:
            issues_found.append(f"{sport_name}: upcoming_games_to_show not set")
        
        if recent_games == 1:
            print(f"{sport_name:15} | Recent: {recent_games} (Single game mode)")
        elif recent_games > 1:
            print(f"{sport_name:15} | Recent: {recent_games} (Multi-game rotation)")
        else:
            issues_found.append(f"{sport_name}: Invalid recent_games_to_show value: {recent_games}")
        
        if upcoming_games == 1:
            print(f"{sport_name:15} | Upcoming: {upcoming_games} (Single game mode)")
        elif upcoming_games > 1:
            print(f"{sport_name:15} | Upcoming: {upcoming_games} (Multi-game rotation)")
        else:
            issues_found.append(f"{sport_name}: Invalid upcoming_games_to_show value: {upcoming_games}")
    
    if issues_found:
        print("\nIssues found:")
        for issue in issues_found:
            print(f"  - {issue}")
    else:
        print("\nNo configuration issues found!")

if __name__ == "__main__":
    test_config_values()
    test_manager_defaults()
    test_config_consistency()
