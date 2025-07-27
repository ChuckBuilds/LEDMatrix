#!/usr/bin/env python3

import json
import sys
import os

def add_custom_feed(feed_name, feed_url):
    """Add a custom RSS feed to the news manager configuration"""
    config_path = "config/config.json"
    
    try:
        # Load current config
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Ensure news_manager section exists
        if 'news_manager' not in config:
            print("❌ News manager configuration not found!")
            return False
        
        # Add custom feed
        if 'custom_feeds' not in config['news_manager']:
            config['news_manager']['custom_feeds'] = {}
        
        config['news_manager']['custom_feeds'][feed_name] = feed_url
        
        # Add to enabled feeds if not already there
        if feed_name not in config['news_manager']['enabled_feeds']:
            config['news_manager']['enabled_feeds'].append(feed_name)
        
        # Save updated config
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=4)
        
        print(f"✅ Successfully added custom feed: {feed_name}")
        print(f"   URL: {feed_url}")
        print(f"   Feed is now enabled and will appear in rotation")
        return True
        
    except Exception as e:
        print(f"❌ Error adding custom feed: {e}")
        return False

def list_all_feeds():
    """List all available feeds (default + custom)"""
    config_path = "config/config.json"
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        news_config = config.get('news_manager', {})
        custom_feeds = news_config.get('custom_feeds', {})
        enabled_feeds = news_config.get('enabled_feeds', [])
        
        print("\n📰 Available News Feeds:")
        print("=" * 50)
        
        # Default feeds (hardcoded in news_manager.py)
        default_feeds = {
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
        
        print("\n🏈 Default Sports Feeds:")
        for name, url in default_feeds.items():
            status = "✅ ENABLED" if name in enabled_feeds else "⭕ DISABLED"
            print(f"  {name}: {status}")
            print(f"    {url}")
        
        if custom_feeds:
            print("\n🏎️ Custom Feeds:")
            for name, url in custom_feeds.items():
                status = "✅ ENABLED" if name in enabled_feeds else "⭕ DISABLED"
                print(f"  {name}: {status}")
                print(f"    {url}")
        else:
            print("\n🏎️ Custom Feeds: None added yet")
        
        print(f"\n📊 Currently Enabled Feeds: {len(enabled_feeds)}")
        print(f"   {', '.join(enabled_feeds)}")
        
    except Exception as e:
        print(f"❌ Error listing feeds: {e}")

def remove_custom_feed(feed_name):
    """Remove a custom RSS feed"""
    config_path = "config/config.json"
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        news_config = config.get('news_manager', {})
        custom_feeds = news_config.get('custom_feeds', {})
        
        if feed_name not in custom_feeds:
            print(f"❌ Custom feed '{feed_name}' not found!")
            return False
        
        # Remove from custom feeds
        del config['news_manager']['custom_feeds'][feed_name]
        
        # Remove from enabled feeds if present
        if feed_name in config['news_manager']['enabled_feeds']:
            config['news_manager']['enabled_feeds'].remove(feed_name)
        
        # Save updated config
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=4)
        
        print(f"✅ Successfully removed custom feed: {feed_name}")
        return True
        
    except Exception as e:
        print(f"❌ Error removing custom feed: {e}")
        return False

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 add_custom_feed_example.py list")
        print("  python3 add_custom_feed_example.py add <feed_name> <feed_url>")
        print("  python3 add_custom_feed_example.py remove <feed_name>")
        print("\nExamples:")
        print("  # Add F1 news feed")
        print("  python3 add_custom_feed_example.py add 'F1' 'https://www.espn.com/espn/rss/rpm/news'")
        print("  # Add BBC F1 feed")
        print("  python3 add_custom_feed_example.py add 'BBC F1' 'http://feeds.bbci.co.uk/sport/formula1/rss.xml'")
        print("  # Add personal blog feed")
        print("  python3 add_custom_feed_example.py add 'My Blog' 'https://myblog.com/rss.xml'")
        return
    
    command = sys.argv[1].lower()
    
    if command == 'list':
        list_all_feeds()
    elif command == 'add':
        if len(sys.argv) != 4:
            print("❌ Usage: python3 add_custom_feed_example.py add <feed_name> <feed_url>")
            return
        feed_name = sys.argv[2]
        feed_url = sys.argv[3]
        add_custom_feed(feed_name, feed_url)
    elif command == 'remove':
        if len(sys.argv) != 3:
            print("❌ Usage: python3 add_custom_feed_example.py remove <feed_name>")
            return
        feed_name = sys.argv[2]
        remove_custom_feed(feed_name)
    else:
        print(f"❌ Unknown command: {command}")

if __name__ == "__main__":
    main()