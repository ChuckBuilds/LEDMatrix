#!/usr/bin/env python3
"""
Check current FlightAware API usage from the flight tracker.
"""

import json
import time
from datetime import datetime
from pathlib import Path

def check_api_usage():
    """Check current API usage and rate limiting status."""
    
    print("🔍 FlightAware API Usage Check")
    print("=" * 40)
    
    # Load config
    config_path = Path("config/config.json")
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    flight_config = config.get('flight_tracker', {})
    
    # Get rate limiting settings
    max_calls_per_hour = flight_config.get('max_api_calls_per_hour', 20)
    daily_api_budget = flight_config.get('daily_api_budget', 60)
    
    print(f"📊 Rate Limits:")
    print(f"   Hourly: {max_calls_per_hour} calls")
    print(f"   Daily: {daily_api_budget} calls")
    
    # Check if there are any cached flight plans
    flight_plan_files = []
    cache_dir = Path("cache") if Path("cache").exists() else None
    if cache_dir:
        flight_plan_files = list(cache_dir.glob("flight_plan_*"))
        print(f"\n💾 Cached Flight Plans: {len(flight_plan_files)}")
        
        if flight_plan_files:
            print("   Recent cached aircraft:")
            for file in sorted(flight_plan_files, key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
                callsign = file.name.replace("flight_plan_", "")
                mtime = datetime.fromtimestamp(file.stat().st_mtime)
                age_hours = (time.time() - file.stat().st_mtime) / 3600
                print(f"   - {callsign} (cached {age_hours:.1f}h ago)")
    else:
        print(f"\n💾 Cached Flight Plans: 0 (no cache directory found)")
    
    print(f"\n💡 Recommendations:")
    if len(flight_plan_files) < 10:
        print("   - Few cached flight plans found")
        print("   - API calls might be rate limited")
        print("   - Try running the flight tracker for a few hours to build cache")
    else:
        print("   - Good number of cached flight plans")
        print("   - System should be working well")
    
    print(f"\n🔧 To improve performance:")
    print("   1. Let the system run for a few hours to build cache")
    print("   2. Consider reducing update intervals if hitting rate limits")
    print("   3. Check logs for 'Rate limit reached' messages")

if __name__ == "__main__":
    check_api_usage()
