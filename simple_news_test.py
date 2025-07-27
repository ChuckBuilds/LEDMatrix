#!/usr/bin/env python3

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import requests
import xml.etree.ElementTree as ET
import feedparser
import json

def test_rss_feeds():
    """Test the RSS feeds directly"""
    print("Testing RSS feeds...")
    
    # ESPN feeds from user's request
    feeds = {
        'MLB': 'http://espn.com/espn/rss/mlb/news',
        'NFL': 'http://espn.go.com/espn/rss/nfl/news',
        'NCAA FB': 'https://www.espn.com/espn/rss/ncf/news',
        'NHL': 'https://www.espn.com/espn/rss/nhl/news',
        'NBA': 'https://www.espn.com/espn/rss/nba/news',
        'TOP SPORTS': 'https://www.espn.com/espn/rss/news',
        'BIG10': 'https://www.espn.com/blog/feed?blog=bigten',
        'NCAA': 'https://www.espn.com/espn/rss/ncaa/news',
        'Other': 'https://www.coveringthecorner.com/rss/current.xml'
    }
    
    # Test a few feeds
    test_feeds = ['NFL', 'NCAA FB']
    
    for feed_name in test_feeds:
        url = feeds[feed_name]
        print(f"\nTesting {feed_name} feed: {url}")
        
        try:
            # Try with feedparser first
            feed = feedparser.parse(url)
            
            if feed.entries:
                print(f"✓ Successfully parsed {len(feed.entries)} entries")
                print(f"  First headline: {feed.entries[0].title[:80]}...")
                
                # Test headline length calculation
                total_length = 0
                for i, entry in enumerate(feed.entries[:2]):  # Test with 2 headlines
                    headline = entry.title
                    length = len(headline)
                    total_length += length
                    print(f"  Headline {i+1}: {length} chars - {headline[:50]}...")
                
                print(f"  Total length for 2 headlines: {total_length} chars")
                
                # Calculate scroll timing (example calculation)
                scroll_speed = 2  # pixels per frame
                scroll_delay = 0.02  # seconds per frame
                display_width = 128  # example display width
                
                # Time to scroll one headline across display
                scroll_time_per_headline = (total_length * 8 + display_width) * scroll_delay / scroll_speed
                print(f"  Estimated scroll time: {scroll_time_per_headline:.2f} seconds")
                
            else:
                print(f"✗ No entries found in feed")
                
        except Exception as e:
            print(f"✗ Error parsing feed: {e}")
    
    print("\n" + "="*50)
    print("RSS Feed Test Complete")

def test_news_manager_logic():
    """Test the core news manager logic without display dependencies"""
    print("\nTesting News Manager Logic...")
    
    # Simulate news data
    sample_headlines = [
        "Breaking: Major trade shakes up NFL draft prospects",
        "College football playoff rankings released",
        "Star quarterback announces retirement after 15 seasons",
        "New stadium construction begins in downtown area"
    ]
    
    # Test headline rotation logic
    print("Testing headline rotation:")
    rotation_count = {}
    
    for i in range(12):  # Simulate 12 display cycles
        # Simple rotation logic - cycle through headlines
        headline_index = i % len(sample_headlines)
        headline = sample_headlines[headline_index]
        
        if headline not in rotation_count:
            rotation_count[headline] = 0
        rotation_count[headline] += 1
        
        print(f"  Cycle {i+1}: {headline[:40]}...")
    
    print("\nRotation statistics:")
    for headline, count in rotation_count.items():
        print(f"  '{headline[:30]}...': shown {count} times")
    
    # Test dynamic length calculation
    print("\nTesting dynamic length calculation:")
    for headline in sample_headlines:
        char_count = len(headline)
        # Estimate pixel width (assuming 6 pixels per character average)
        pixel_width = char_count * 6
        # Calculate scroll time
        display_width = 128
        scroll_speed = 2
        scroll_delay = 0.02
        
        total_scroll_distance = pixel_width + display_width
        scroll_time = (total_scroll_distance / scroll_speed) * scroll_delay
        
        print(f"  '{headline[:30]}...': {char_count} chars, ~{pixel_width}px, {scroll_time:.2f}s")

if __name__ == "__main__":
    print("Sports News Manager Test")
    print("=" * 50)
    
    test_rss_feeds()
    test_news_manager_logic()
    
    print("\n" + "="*50)
    print("All tests completed!")