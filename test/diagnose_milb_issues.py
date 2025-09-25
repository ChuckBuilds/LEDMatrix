#!/usr/bin/env python3
"""
Comprehensive diagnostic script for MiLB manager issues
"""

import requests
import json
import sys
import os
from datetime import datetime, timedelta, timezone

# Add the src directory to the path so we can import the managers
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def test_milb_api_directly():
    """Test the MiLB API directly to see what's available."""
    print("=" * 60)
    print("TESTING MiLB API DIRECTLY")
    print("=" * 60)
    
    # MiLB league sport IDs
    sport_ids = [10, 11, 12, 13, 14, 15]  # Mexican, AAA, AA, A+, A, Rookie
    
    # Get dates for the next 7 days
    now = datetime.now(timezone.utc)
    dates = []
    for i in range(-1, 8):  # Yesterday + 7 days
        date = now + timedelta(days=i)
        dates.append(date.strftime("%Y-%m-%d"))
    
    print(f"Checking dates: {dates}")
    print(f"Checking sport IDs: {sport_ids}")
    
    all_games = {}
    api_errors = []
    
    for date in dates:
        for sport_id in sport_ids:
            try:
                url = f"http://statsapi.mlb.com/api/v1/schedule?sportId={sport_id}&date={date}"
                print(f"\nFetching MiLB games for sport ID {sport_id}, date: {date}")
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                
                data = response.json()
                
                if not data.get('dates'):
                    print(f"  ❌ No dates data for sport ID {sport_id}")
                    continue
                
                if not data['dates'][0].get('games'):
                    print(f"  ❌ No games found for sport ID {sport_id}")
                    continue
                
                games = data['dates'][0]['games']
                print(f"  ✅ Found {len(games)} games for sport ID {sport_id}")
                
                for game in games:
                    game_pk = game['gamePk']
                    
                    home_team_name = game['teams']['home']['team']['name']
                    away_team_name = game['teams']['away']['team']['name']
                    
                    home_abbr = game['teams']['home']['team'].get('abbreviation', home_team_name[:3].upper())
                    away_abbr = game['teams']['away']['team'].get('abbreviation', away_team_name[:3].upper())
                    
                    status_obj = game['status']
                    status_state = status_obj.get('abstractGameState', 'Preview')
                    detailed_state = status_obj.get('detailedState', '').lower()
                    
                    # Check if it's a favorite team (TAM from config)
                    favorite_teams = ['TAM']
                    is_favorite = (home_abbr in favorite_teams or away_abbr in favorite_teams)
                    
                    if is_favorite:
                        print(f"    ⭐ FAVORITE TEAM GAME: {away_abbr} @ {home_abbr}")
                        print(f"      Status: {detailed_state} -> {status_state}")
                        print(f"      Scores: {game['teams']['away'].get('score', 0)} - {game['teams']['home'].get('score', 0)}")
                    
                    # Store game data
                    game_data = {
                        'id': game_pk,
                        'away_team': away_abbr,
                        'home_team': home_abbr,
                        'away_score': game['teams']['away'].get('score', 0),
                        'home_score': game['teams']['home'].get('score', 0),
                        'status': detailed_state,
                        'status_state': status_state,
                        'start_time': game['gameDate'],
                        'is_favorite': is_favorite,
                        'sport_id': sport_id
                    }
                    
                    all_games[game_pk] = game_data
                
            except Exception as e:
                error_msg = f"Error fetching MiLB games for sport ID {sport_id}, date {date}: {e}"
                print(f"  ❌ {error_msg}")
                api_errors.append(error_msg)
    
    # Summary
    print(f"\n{'='*60}")
    print(f"API TEST SUMMARY:")
    print(f"Total games found: {len(all_games)}")
    print(f"API errors: {len(api_errors)}")
    
    favorite_games = [g for g in all_games.values() if g['is_favorite']]
    print(f"Favorite team games: {len(favorite_games)}")
    
    live_games = [g for g in all_games.values() if g['status'] == 'in progress']
    print(f"Live games: {len(live_games)}")
    
    upcoming_games = [g for g in all_games.values() if g['status'] in ['scheduled', 'preview']]
    print(f"Upcoming games: {len(upcoming_games)}")
    
    final_games = [g for g in all_games.values() if g['status'] == 'final']
    print(f"Final games: {len(final_games)}")
    
    if favorite_games:
        print(f"\nFavorite team games:")
        for game in favorite_games:
            print(f"  {game['away_team']} @ {game['home_team']} - {game['status']} ({game['status_state']})")
    
    if api_errors:
        print(f"\nAPI Errors:")
        for error in api_errors[:5]:  # Show first 5 errors
            print(f"  {error}")
    
    return all_games, api_errors

def test_team_mapping():
    """Test the team mapping file."""
    print("\n" + "=" * 60)
    print("TESTING TEAM MAPPING")
    print("=" * 60)
    
    try:
        mapping_path = os.path.join('assets', 'sports', 'milb_logos', 'milb_team_mapping.json')
        with open(mapping_path, 'r') as f:
            team_mapping = json.load(f)
        
        print(f"✅ Team mapping file loaded successfully")
        print(f"Total teams in mapping: {len(team_mapping)}")
        
        # Check for TAM team
        tam_found = False
        for team_name, data in team_mapping.items():
            if data.get('abbreviation') == 'TAM':
                print(f"✅ Found TAM team: {team_name}")
                tam_found = True
                break
        
        if not tam_found:
            print(f"❌ TAM team not found in mapping!")
        
        # Check for some common teams
        common_teams = ['Toledo Mud Hens', 'Buffalo Bisons', 'Tampa Tarpons']
        for team in common_teams:
            if team in team_mapping:
                abbr = team_mapping[team]['abbreviation']
                print(f"✅ Found {team}: {abbr}")
            else:
                print(f"❌ Not found: {team}")
        
        return team_mapping
        
    except Exception as e:
        print(f"❌ Error loading team mapping: {e}")
        return None

def test_configuration():
    """Test the configuration settings."""
    print("\n" + "=" * 60)
    print("TESTING CONFIGURATION")
    print("=" * 60)
    
    try:
        config_path = os.path.join('config', 'config.json')
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        milb_config = config.get('milb_scoreboard', {})
        
        print(f"✅ Configuration file loaded successfully")
        print(f"MiLB enabled: {milb_config.get('enabled', False)}")
        print(f"Favorite teams: {milb_config.get('favorite_teams', [])}")
        print(f"Test mode: {milb_config.get('test_mode', False)}")
        print(f"Sport IDs: {milb_config.get('sport_ids', [10, 11, 12, 13, 14, 15])}")
        print(f"Live update interval: {milb_config.get('live_update_interval', 30)}")
        print(f"Recent update interval: {milb_config.get('recent_update_interval', 3600)}")
        print(f"Upcoming update interval: {milb_config.get('upcoming_update_interval', 3600)}")
        
        # Check display modes
        display_modes = milb_config.get('display_modes', {})
        print(f"Display modes:")
        for mode, enabled in display_modes.items():
            print(f"  {mode}: {enabled}")
        
        return milb_config
        
    except Exception as e:
        print(f"❌ Error loading configuration: {e}")
        return None

def test_season_timing():
    """Check if we're in MiLB season."""
    print("\n" + "=" * 60)
    print("TESTING SEASON TIMING")
    print("=" * 60)
    
    now = datetime.now()
    current_month = now.month
    current_year = now.year
    
    print(f"Current date: {now.strftime('%Y-%m-%d')}")
    print(f"Current month: {current_month}")
    
    # MiLB season typically runs from April to September
    if 4 <= current_month <= 9:
        print(f"✅ Currently in MiLB season (April-September)")
    else:
        print(f"❌ Currently OUTSIDE MiLB season (April-September)")
        print(f"   This could explain why no games are found!")
    
    # Check if we're in offseason
    if current_month in [1, 2, 3, 10, 11, 12]:
        print(f"⚠️  MiLB is likely in offseason - no games expected")
    
    return 4 <= current_month <= 9

def test_cache_manager():
    """Test the cache manager functionality."""
    print("\n" + "=" * 60)
    print("TESTING CACHE MANAGER")
    print("=" * 60)
    
    try:
        from cache_manager import CacheManager
        
        cache_manager = CacheManager()
        print(f"✅ Cache manager initialized successfully")
        
        # Test cache operations
        test_key = "test_milb_cache"
        test_data = {"test": "data"}
        
        cache_manager.set(test_key, test_data)
        print(f"✅ Cache set operation successful")
        
        retrieved_data = cache_manager.get(test_key)
        if retrieved_data == test_data:
            print(f"✅ Cache get operation successful")
        else:
            print(f"❌ Cache get operation failed - data mismatch")
        
        # Clean up test data
        cache_manager.clear_cache(test_key)
        print(f"✅ Cache clear operation successful")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing cache manager: {e}")
        return False

def main():
    """Run all diagnostic tests."""
    print("MiLB Manager Diagnostic Tool")
    print("=" * 60)
    
    # Test 1: API directly
    api_games, api_errors = test_milb_api_directly()
    
    # Test 2: Team mapping
    team_mapping = test_team_mapping()
    
    # Test 3: Configuration
    milb_config = test_configuration()
    
    # Test 4: Season timing
    in_season = test_season_timing()
    
    # Test 5: Cache manager
    cache_ok = test_cache_manager()
    
    # Final summary
    print("\n" + "=" * 60)
    print("FINAL DIAGNOSIS")
    print("=" * 60)
    
    issues = []
    
    if not api_games:
        issues.append("No games found from API")
    
    if api_errors:
        issues.append(f"API errors: {len(api_errors)}")
    
    if not team_mapping:
        issues.append("Team mapping file issues")
    
    if not milb_config:
        issues.append("Configuration file issues")
    
    if not in_season:
        issues.append("Currently outside MiLB season")
    
    if not cache_ok:
        issues.append("Cache manager issues")
    
    if issues:
        print(f"❌ Issues found:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print(f"✅ No obvious issues found")
    
    # Recommendations
    print(f"\nRECOMMENDATIONS:")
    
    if not in_season:
        print(f"  - MiLB is currently in offseason - no games expected")
        print(f"  - Consider enabling test_mode in config for testing")
    
    if not api_games:
        print(f"  - No games found from API - check API endpoints")
        print(f"  - Verify sport IDs are correct")
    
    if api_errors:
        print(f"  - API errors detected - check network connectivity")
        print(f"  - Verify API endpoints are accessible")
    
    print(f"\nTo enable test mode, set 'test_mode': true in config/config.json milb section")

if __name__ == "__main__":
    main() 