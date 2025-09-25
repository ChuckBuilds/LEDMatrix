#!/usr/bin/env python3
"""
Integration test to verify dynamic team resolver works with sports managers.
This test checks that the SportsCore class properly resolves dynamic teams.
"""

import sys
import os
import json
from datetime import datetime, timedelta
import pytz

# Add the project root to the path so we can import the modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.base_classes.sports import SportsCore
from src.display_manager import DisplayManager
from src.cache_manager import CacheManager

def create_test_config():
    """Create a test configuration with dynamic teams."""
    config = {
        "ncaa_fb_scoreboard": {
            "enabled": True,
            "show_favorite_teams_only": True,
            "favorite_teams": [
                "UGA",
                "AP_TOP_25"
            ],
            "logo_dir": "assets/sports/ncaa_logos",
            "show_records": True,
            "show_ranking": True,
            "update_interval_seconds": 3600
        },
        "display": {
            "hardware": {
                "rows": 32,
                "cols": 64,
                "chain_length": 1
            }
        },
        "timezone": "America/Chicago"
    }
    return config

def test_sports_core_integration():
    """Test that SportsCore properly resolves dynamic teams."""
    print("Testing SportsCore integration with dynamic teams...")
    
    # Create test configuration
    config = create_test_config()
    
    # Create mock display manager and cache manager
    display_manager = DisplayManager(config)
    cache_manager = CacheManager(config)
    
    # Create SportsCore instance
    sports_core = SportsCore(config, display_manager, cache_manager, 
                            __import__('logging').getLogger(__name__), "ncaa_fb")
    
    # Check that favorite_teams were resolved
    print(f"Raw favorite teams from config: {config['ncaa_fb_scoreboard']['favorite_teams']}")
    print(f"Resolved favorite teams: {sports_core.favorite_teams}")
    
    # Verify that UGA is still in the list
    assert "UGA" in sports_core.favorite_teams, "UGA should be in resolved teams"
    
    # Verify that AP_TOP_25 was resolved to actual teams
    assert len(sports_core.favorite_teams) > 1, "Should have more than 1 team after resolving AP_TOP_25"
    
    # Verify that AP_TOP_25 is not in the final list (should be resolved)
    assert "AP_TOP_25" not in sports_core.favorite_teams, "AP_TOP_25 should be resolved, not left as-is"
    
    print(f"✓ SportsCore successfully resolved dynamic teams")
    print(f"✓ Final favorite teams: {sports_core.favorite_teams[:10]}{'...' if len(sports_core.favorite_teams) > 10 else ''}")
    
    return True

def test_dynamic_resolver_availability():
    """Test that the dynamic resolver is available in SportsCore."""
    print("Testing dynamic resolver availability...")
    
    config = create_test_config()
    display_manager = DisplayManager(config)
    cache_manager = CacheManager(config)
    
    sports_core = SportsCore(config, display_manager, cache_manager, 
                            __import__('logging').getLogger(__name__), "ncaa_fb")
    
    # Check that dynamic resolver is available
    assert hasattr(sports_core, 'dynamic_resolver'), "SportsCore should have dynamic_resolver attribute"
    assert sports_core.dynamic_resolver is not None, "Dynamic resolver should be initialized"
    
    # Test dynamic resolver methods
    assert sports_core.dynamic_resolver.is_dynamic_team("AP_TOP_25"), "Should detect AP_TOP_25 as dynamic"
    assert not sports_core.dynamic_resolver.is_dynamic_team("UGA"), "Should not detect UGA as dynamic"
    
    print("✓ Dynamic resolver is properly integrated")
    
    return True

if __name__ == "__main__":
    try:
        print("🧪 Testing Sports Integration with Dynamic Teams...")
        print("=" * 50)
        
        test_sports_core_integration()
        test_dynamic_resolver_availability()
        
        print("\n🎉 All integration tests passed!")
        print("Dynamic team resolver is successfully integrated with SportsCore!")
        
    except Exception as e:
        print(f"\n❌ Integration test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)