#!/usr/bin/env python3
"""
Test script to debug NHL manager data fetching issues.
This will help us understand why NHL managers aren't finding games.
"""

import sys
import os
from datetime import datetime, timedelta
import pytz

# Add the src directory to the path so we can import the managers
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_nhl_season_logic():
    """Test the NHL season logic."""
    print("Testing NHL season logic...")
    
    now = datetime.now(pytz.utc)
    print(f"Current date: {now}")
    print(f"Current month: {now.month}")
    
    # Test the off-season logic
    if now.month in [6, 7, 8]:  # Off-season months (June, July, August)
        print("Status: Off-season")
    elif now.month == 9:  # September
        print("Status: Pre-season (should have games)")
    elif now.month == 10 and now.day < 15:  # Early October
        print("Status: Early season")
    else:
        print("Status: Regular season")
    
    # Test season year calculation
    season_year = now.year
    if now.month < 9:
        season_year = now.year - 1
    
    print(f"Season year: {season_year}")
    print(f"Cache key would be: nhl_api_data_{season_year}")

def test_espn_api_direct():
    """Test the ESPN API directly to see what data is available."""
    print("\nTesting ESPN API directly...")
    
    import requests
    
    url = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    # Test with current date range
    now = datetime.now(pytz.utc)
    start_date = (now - timedelta(days=30)).strftime("%Y%m%d")
    end_date = (now + timedelta(days=30)).strftime("%Y%m%d")
    date_range = f"{start_date}-{end_date}"
    
    params = {
        "dates": date_range,
        "limit": 1000
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        events = data.get('events', [])
        print(f"Found {len(events)} events in API response")
        
        if events:
            print("Sample events:")
            for i, event in enumerate(events[:3]):
                print(f"  {i+1}. {event.get('name', 'Unknown')} on {event.get('date', 'Unknown')}")
                
            # Check status distribution
            status_counts = {}
            for event in events:
                competitions = event.get('competitions', [])
                if competitions:
                    status = competitions[0].get('status', {}).get('type', {})
                state = status.get('state', 'unknown')
                status_counts[state] = status_counts.get(state, 0) + 1
            
            print(f"\nStatus distribution:")
            for status, count in status_counts.items():
                print(f"  {status}: {count} games")
        else:
            print("No events found in API response")
            
    except Exception as e:
        print(f"Error testing API: {e}")

def main():
    """Run all tests."""
    print("=" * 60)
    print("NHL Manager Debug Test")
    print("=" * 60)
    
    test_nhl_season_logic()
    test_espn_api_direct()
    
    print("\n" + "=" * 60)
    print("Debug test complete!")
    print("=" * 60)

if __name__ == "__main__":
    main()
