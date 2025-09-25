#!/usr/bin/env python3
"""
Simple test script for the LeaderboardManager (without display dependencies)
"""

import sys
import os
import json
import logging
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFont

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def test_espn_api():
    """Test ESPN API endpoints for standings."""
    
    # Test different league endpoints
    test_leagues = [
        {
            'name': 'NFL',
            'url': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/standings'
        },
        {
            'name': 'NBA',
            'url': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/standings'
        },
        {
            'name': 'MLB',
            'url': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/standings'
        }
    ]
    
    for league in test_leagues:
        print(f"\nTesting {league['name']} API...")
        try:
            response = requests.get(league['url'], timeout=30)
            response.raise_for_status()
            data = response.json()
            
            print(f"✓ {league['name']} API response successful")
            
            # Check if we have groups data
            groups = data.get('groups', [])
            print(f"  Groups found: {len(groups)}")
            
            # Try to extract some team data
            total_teams = 0
            for group in groups:
                if 'standings' in group:
                    total_teams += len(group['standings'])
                elif 'groups' in group:
                    # Handle nested groups (like NFL conferences/divisions)
                    for sub_group in group['groups']:
                        if 'standings' in sub_group:
                            total_teams += len(sub_group['standings'])
                        elif 'groups' in sub_group:
                            for sub_sub_group in sub_group['groups']:
                                if 'standings' in sub_sub_group:
                                    total_teams += len(sub_sub_group['standings'])
            
            print(f"  Total teams found: {total_teams}")
            
        except Exception as e:
            print(f"✗ {league['name']} API failed: {e}")

def test_standings_parsing():
    """Test parsing standings data."""
    
    # Test NFL standings parsing using teams endpoint
    print("\nTesting NFL standings parsing...")
    try:
        # First get all teams
        teams_url = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams'
        response = requests.get(teams_url, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        sports = data.get('sports', [])
        if not sports:
            print("✗ No sports data found")
            return
        
        leagues = sports[0].get('leagues', [])
        if not leagues:
            print("✗ No leagues data found")
            return
        
        teams = leagues[0].get('teams', [])
        if not teams:
            print("✗ No teams data found")
            return
        
        print(f"Found {len(teams)} NFL teams")
        
        # Test fetching individual team records
        standings = []
        test_teams = teams[:5]  # Test first 5 teams to avoid too many API calls
        
        for team_data in test_teams:
            team = team_data.get('team', {})
            team_abbr = team.get('abbreviation')
            team_name = team.get('name', 'Unknown')
            
            if not team_abbr:
                continue
            
            print(f"  Fetching record for {team_abbr}...")
            
            # Fetch individual team record
            team_url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_abbr}"
            team_response = requests.get(team_url, timeout=30)
            team_response.raise_for_status()
            team_data = team_response.json()
            
            team_info = team_data.get('team', {})
            stats = team_info.get('stats', [])
            
            # Find wins and losses
            wins = 0
            losses = 0
            ties = 0
            
            for stat in stats:
                if stat.get('name') == 'wins':
                    wins = stat.get('value', 0)
                elif stat.get('name') == 'losses':
                    losses = stat.get('value', 0)
                elif stat.get('name') == 'ties':
                    ties = stat.get('value', 0)
            
            # Calculate win percentage
            total_games = wins + losses + ties
            win_percentage = wins / total_games if total_games > 0 else 0
            
            standings.append({
                'name': team_name,
                'abbreviation': team_abbr,
                'wins': wins,
                'losses': losses,
                'ties': ties,
                'win_percentage': win_percentage
            })
        
        # Sort by win percentage and show results
        standings.sort(key=lambda x: x['win_percentage'], reverse=True)
        
        print("NFL team records:")
        for i, team in enumerate(standings):
            record = f"{team['wins']}-{team['losses']}"
            if team['ties'] > 0:
                record += f"-{team['ties']}"
            print(f"  {i+1}. {team['abbreviation']} {record} ({team['win_percentage']:.3f})")
            
    except Exception as e:
        print(f"✗ NFL standings parsing failed: {e}")

def test_logo_loading():
    """Test logo loading functionality."""
    
    print("\nTesting logo loading...")
    
    # Test team logo loading
    logo_dir = "assets/sports/nfl_logos"
    test_teams = ["TB", "DAL", "NE"]
    
    for team in test_teams:
        logo_path = os.path.join(logo_dir, f"{team}.png")
        if os.path.exists(logo_path):
            print(f"✓ {team} logo found: {logo_path}")
        else:
            print(f"✗ {team} logo not found: {logo_path}")
    
    # Test league logo loading
    league_logos = [
        "assets/sports/nfl_logos/nfl.png",
        "assets/sports/nba_logos/nba.png",
        "assets/sports/mlb_logos/mlb.png",
        "assets/sports/nhl_logos/nhl.png",
        "assets/sports/ncaa_logos/ncaa_fb.png",
        "assets/sports/ncaa_logos/ncaam.png"
    ]
    
    for logo_path in league_logos:
        if os.path.exists(logo_path):
            print(f"✓ League logo found: {logo_path}")
        else:
            print(f"✗ League logo not found: {logo_path}")

if __name__ == "__main__":
    print("Testing LeaderboardManager components...")
    
    test_espn_api()
    test_standings_parsing()
    test_logo_loading()
    
    print("\nTest completed!")
