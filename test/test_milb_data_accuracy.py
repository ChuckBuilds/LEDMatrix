#!/usr/bin/env python3
"""
Test script to check the accuracy of MiLB game data being returned.
This focuses on verifying that live games and favorite team games have complete,
accurate information including scores, innings, counts, etc.
"""

import requests
import json
from datetime import datetime, timedelta
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

def test_milb_api_accuracy():
    """Test the accuracy of MiLB API data for live and favorite team games."""
    print("MiLB Data Accuracy Test")
    print("=" * 60)
    
    # Load configuration
    try:
        with open('config/config.json', 'r') as f:
            config = json.load(f)
        milb_config = config.get('milb_scoreboard', {})
        favorite_teams = milb_config.get('favorite_teams', [])
        print(f"Favorite teams: {favorite_teams}")
    except Exception as e:
        print(f"❌ Error loading config: {e}")
        return
    
    # Test dates (today and a few days around)
    test_dates = [
        datetime.now().strftime('%Y-%m-%d'),
        (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
        (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
    ]
    
    base_url = "http://statsapi.mlb.com/api/v1/schedule"
    
    for date in test_dates:
        print(f"\n--- Testing date: {date} ---")
        
        # Test all sport IDs
        sport_ids = [10, 11, 12, 13, 14, 15]  # Mexican, AAA, AA, A+, A, Rookie
        
        for sport_id in sport_ids:
            print(f"\nSport ID {sport_id}:")
            
            url = f"{base_url}?sportId={sport_id}&date={date}"
            print(f"URL: {url}")
            
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                if 'dates' not in data or not data['dates']:
                    print(f"  ❌ No dates data for sport ID {sport_id}")
                    continue
                
                total_games = 0
                live_games = 0
                favorite_games = 0
                
                for date_data in data['dates']:
                    games = date_data.get('games', [])
                    total_games += len(games)
                    
                    for game in games:
                        game_status = game.get('status', {}).get('detailedState', 'unknown')
                        teams = game.get('teams', {})
                        
                        # Check if it's a live game
                        if game_status in ['In Progress', 'Live']:
                            live_games += 1
                            print(f"  🟢 LIVE GAME: {game.get('gamePk', 'N/A')}")
                            print(f"    Status: {game_status}")
                            print(f"    Teams: {teams.get('away', {}).get('team', {}).get('name', 'Unknown')} @ {teams.get('home', {}).get('team', {}).get('name', 'Unknown')}")
                            
                            # Check for detailed game data
                            away_team = teams.get('away', {})
                            home_team = teams.get('home', {})
                            
                            print(f"    Away Score: {away_team.get('score', 'N/A')}")
                            print(f"    Home Score: {home_team.get('score', 'N/A')}")
                            
                            # Check for inning info
                            linescore = game.get('linescore', {})
                            if linescore:
                                current_inning = linescore.get('currentInning', 'N/A')
                                inning_state = linescore.get('inningState', 'N/A')
                                print(f"    Inning: {current_inning} ({inning_state})")
                                
                                # Check for count data
                                balls = linescore.get('balls', 'N/A')
                                strikes = linescore.get('strikes', 'N/A')
                                outs = linescore.get('outs', 'N/A')
                                print(f"    Count: {balls}-{strikes}, Outs: {outs}")
                                
                                # Check for base runners
                                bases = linescore.get('bases', [])
                                if bases:
                                    print(f"    Bases: {bases}")
                            
                            # Check for detailed status
                            detailed_status = game.get('status', {})
                            print(f"    Detailed Status: {detailed_status}")
                            
                            print()
                        
                        # Check if it's a favorite team game
                        away_team_name = teams.get('away', {}).get('team', {}).get('name', '')
                        home_team_name = teams.get('home', {}).get('team', {}).get('name', '')
                        
                        for favorite_team in favorite_teams:
                            if favorite_team in away_team_name or favorite_team in home_team_name:
                                favorite_games += 1
                                print(f"  ⭐ FAVORITE TEAM GAME: {game.get('gamePk', 'N/A')}")
                                print(f"    Status: {game_status}")
                                print(f"    Teams: {away_team_name} @ {home_team_name}")
                                print(f"    Away Score: {away_team.get('score', 'N/A')}")
                                print(f"    Home Score: {home_team.get('score', 'N/A')}")
                                
                                # Check for detailed game data
                                linescore = game.get('linescore', {})
                                if linescore:
                                    current_inning = linescore.get('currentInning', 'N/A')
                                    inning_state = linescore.get('inningState', 'N/A')
                                    print(f"    Inning: {current_inning} ({inning_state})")
                                
                                print()
                
                print(f"  Total games: {total_games}")
                print(f"  Live games: {live_games}")
                print(f"  Favorite team games: {favorite_games}")
                
            except requests.exceptions.RequestException as e:
                print(f"  ❌ Request error: {e}")
            except json.JSONDecodeError as e:
                print(f"  ❌ JSON decode error: {e}")
            except Exception as e:
                print(f"  ❌ Unexpected error: {e}")

def test_specific_game_accuracy():
    """Test the accuracy of a specific game by its gamePk."""
    print("\n" + "=" * 60)
    print("TESTING SPECIFIC GAME ACCURACY")
    print("=" * 60)
    
    # Test with a specific game ID if available
    # You can replace this with an actual gamePk from the API
    test_game_pk = None
    
    if test_game_pk:
        url = f"http://statsapi.mlb.com/api/v1/game/{test_game_pk}/feed/live"
        print(f"Testing specific game: {test_game_pk}")
        print(f"URL: {url}")
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            print("Game data structure:")
            print(json.dumps(data, indent=2)[:1000] + "...")
            
        except Exception as e:
            print(f"❌ Error testing specific game: {e}")

def main():
    """Run the accuracy tests."""
    test_milb_api_accuracy()
    test_specific_game_accuracy()
    
    print("\n" + "=" * 60)
    print("ACCURACY TEST SUMMARY")
    print("=" * 60)
    print("This test checks:")
    print("✅ Whether live games have complete data (scores, innings, counts)")
    print("✅ Whether favorite team games are properly identified")
    print("✅ Whether game status information is accurate")
    print("✅ Whether detailed game data (linescore) is available")
    print("\nIf you see 'N/A' values for scores, innings, or counts,")
    print("this indicates the API data may be incomplete or inaccurate.")

if __name__ == "__main__":
    main() 