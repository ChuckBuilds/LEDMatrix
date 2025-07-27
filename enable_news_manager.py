#!/usr/bin/env python3

import json
import sys
import os

def enable_news_manager():
    """Enable the news manager in the configuration"""
    config_path = "config/config.json"
    
    try:
        # Load current config
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Enable news manager
        if 'news_manager' not in config:
            print("News manager configuration not found!")
            return False
        
        config['news_manager']['enabled'] = True
        
        # Save updated config
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=4)
        
        print("✓ News manager enabled successfully!")
        print(f"✓ Enabled feeds: {config['news_manager']['enabled_feeds']}")
        print(f"✓ Headlines per feed: {config['news_manager']['headlines_per_feed']}")
        print(f"✓ Update interval: {config['news_manager']['update_interval']} seconds")
        
        return True
        
    except Exception as e:
        print(f"✗ Error enabling news manager: {e}")
        return False

def disable_news_manager():
    """Disable the news manager in the configuration"""
    config_path = "config/config.json"
    
    try:
        # Load current config
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Disable news manager
        if 'news_manager' in config:
            config['news_manager']['enabled'] = False
            
            # Save updated config
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=4)
            
            print("✓ News manager disabled successfully!")
        else:
            print("News manager configuration not found!")
            
        return True
        
    except Exception as e:
        print(f"✗ Error disabling news manager: {e}")
        return False

def show_status():
    """Show current news manager status"""
    config_path = "config/config.json"
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        if 'news_manager' not in config:
            print("News manager configuration not found!")
            return
        
        news_config = config['news_manager']
        
        print("News Manager Status:")
        print("=" * 30)
        print(f"Enabled: {news_config.get('enabled', False)}")
        print(f"Update Interval: {news_config.get('update_interval', 300)} seconds")
        print(f"Scroll Speed: {news_config.get('scroll_speed', 2)} pixels/frame")
        print(f"Scroll Delay: {news_config.get('scroll_delay', 0.02)} seconds/frame")
        print(f"Headlines per Feed: {news_config.get('headlines_per_feed', 2)}")
        print(f"Enabled Feeds: {news_config.get('enabled_feeds', [])}")
        print(f"Rotation Enabled: {news_config.get('rotation_enabled', True)}")
        print(f"Rotation Threshold: {news_config.get('rotation_threshold', 3)}")
        print(f"Font Size: {news_config.get('font_size', 12)}")
        
        custom_feeds = news_config.get('custom_feeds', {})
        if custom_feeds:
            print("Custom Feeds:")
            for name, url in custom_feeds.items():
                print(f"  {name}: {url}")
        else:
            print("No custom feeds configured")
            
    except Exception as e:
        print(f"✗ Error reading configuration: {e}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 enable_news_manager.py [enable|disable|status]")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == "enable":
        enable_news_manager()
    elif command == "disable":
        disable_news_manager()
    elif command == "status":
        show_status()
    else:
        print("Invalid command. Use: enable, disable, or status")
        sys.exit(1)

if __name__ == "__main__":
    main()