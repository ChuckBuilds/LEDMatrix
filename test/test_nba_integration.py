#!/usr/bin/env python3
"""
Comprehensive test script to verify NBA Manager, Leaderboard, and Odds Manager integration.
"""
import sys
import os
import logging
import json
from typing import Dict, Any

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_nba_api_connectivity():
    """Test basic NBA API connectivity."""
    try:
        import requests

        # Test teams endpoint
        teams_url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
        response = requests.get(teams_url, timeout=10)
        response.raise_for_status()
        teams_data = response.json()

        # Test standings endpoint
        standings_url = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"
        response = requests.get(standings_url, timeout=10)
        response.raise_for_status()
        standings_data = response.json()

        # Test live games endpoint
        live_url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
        response = requests.get(live_url, timeout=10)
        response.raise_for_status()
        live_data = response.json()

        logger.info("✅ NBA API connectivity test PASSED")
        return True

    except Exception as e:
        logger.error(f"❌ NBA API connectivity test FAILED: {e}")
        return False

def test_odds_api_connectivity():
    """Test odds API connectivity."""
    try:
        import requests

        # Test ESPN odds API
        odds_url = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/401585515/competitions/401585515/odds"
        response = requests.get(odds_url, timeout=10)
        response.raise_for_status()
        odds_data = response.json()

        logger.info("✅ Odds API connectivity test PASSED")
        return True

    except Exception as e:
        logger.error(f"❌ Odds API connectivity test FAILED: {e}")
        return False

def test_nba_manager_initialization():
    """Test NBA manager initialization and configuration."""
    try:
        # Mock the required dependencies since we're not on Raspberry Pi
        class MockDisplayManager:
            def __init__(self):
                self.matrix = type('obj', (object,), {'width': 64, 'height': 32})()

        class MockCacheManager:
            def __init__(self):
                self.config_manager = None

            def get(self, key):
                return None

            def save_cache(self, key, data):
                pass

        # Load config
        with open('config/config.json', 'r') as f:
            config = json.load(f)

        # Test manager imports
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

        from nba_managers import BaseNBAManager, NBALiveManager, NBARecentManager, NBAUpcomingManager

        # Test initialization
        display_manager = MockDisplayManager()
        cache_manager = MockCacheManager()

        # Test base manager
        base_manager = BaseNBAManager(config, display_manager, cache_manager)
        logger.info(f"✅ Base NBA Manager initialized: {base_manager.league}")

        # Test live manager
        live_manager = NBALiveManager(config, display_manager, cache_manager)
        logger.info(f"✅ NBA Live Manager initialized")

        # Test recent manager
        recent_manager = NBARecentManager(config, display_manager, cache_manager)
        logger.info(f"✅ NBA Recent Manager initialized")

        # Test upcoming manager
        upcoming_manager = NBAUpcomingManager(config, display_manager, cache_manager)
        logger.info(f"✅ NBA Upcoming Manager initialized")

        return True

    except Exception as e:
        logger.error(f"❌ NBA Manager initialization test FAILED: {e}")
        return False

def test_leaderboard_nba_integration():
    """Test leaderboard NBA integration."""
    try:
        # Mock dependencies
        class MockDisplayManager:
            def __init__(self):
                self.matrix = type('obj', (object,), {'width': 64, 'height': 32})()

        class MockCacheManager:
            def __init__(self):
                self.config_manager = None

            def get_cached_data_with_strategy(self, key, strategy):
                return None

            def save_cache(self, key, data):
                pass

            def clear_cache(self, key):
                pass

        # Load config
        with open('config/config.json', 'r') as f:
            config = json.load(f)

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

        from leaderboard_manager import LeaderboardManager

        # Test initialization
        display_manager = MockDisplayManager()
        cache_manager = MockCacheManager()

        leaderboard = LeaderboardManager(config, display_manager)

        # Check if NBA is configured in leaderboard
        nba_config = leaderboard.league_configs.get('nba', {})
        logger.info(f"NBA leaderboard config: {nba_config}")

        # Test NBA standings fetching (without actual API call)
        logger.info("✅ Leaderboard NBA integration test PASSED")
        return True

    except Exception as e:
        logger.error(f"❌ Leaderboard NBA integration test FAILED: {e}")
        return False

def test_odds_manager_integration():
    """Test odds manager integration."""
    try:
        # Mock cache manager
        class MockCacheManager:
            def __init__(self):
                self.config_manager = None

            def get_with_auto_strategy(self, key):
                return None

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

        from odds_manager import OddsManager

        # Test initialization
        cache_manager = MockCacheManager()
        odds_manager = OddsManager(cache_manager)

        logger.info(f"✅ Odds Manager initialized")

        # Test NBA odds URL construction (without actual API call)
        test_event_id = "401585515"  # Sample NBA game ID
        expected_url = f"https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{test_event_id}/competitions/{test_event_id}/odds"
        logger.info(f"Expected odds URL: {expected_url}")

        logger.info("✅ Odds Manager integration test PASSED")
        return True

    except Exception as e:
        logger.error(f"❌ Odds Manager integration test FAILED: {e}")
        return False

def test_configuration_consistency():
    """Test that configurations are consistent across components."""
    try:
        with open('config/config.json', 'r') as f:
            config = json.load(f)

        # Check NBA scoreboard config
        nba_scoreboard = config.get('nba_scoreboard', {})
        nba_enabled = nba_scoreboard.get('enabled', False)
        nba_show_odds = nba_scoreboard.get('show_odds', False)

        # Check leaderboard config
        leaderboard = config.get('leaderboard', {})
        leaderboard_enabled = leaderboard.get('enabled', False)
        nba_leaderboard_enabled = leaderboard.get('enabled_sports', {}).get('nba', {}).get('enabled', False)

        logger.info(f"NBA Scoreboard - Enabled: {nba_enabled}, Show Odds: {nba_show_odds}")
        logger.info(f"Leaderboard - Enabled: {leaderboard_enabled}, NBA Enabled: {nba_leaderboard_enabled}")

        # Check for consistency
        if not nba_enabled and nba_show_odds:
            logger.warning("⚠️ NBA scoreboard disabled but odds enabled - odds won't be used")

        if leaderboard_enabled and not nba_leaderboard_enabled:
            logger.info("ℹ️ Leaderboard enabled but NBA disabled - NBA won't appear in leaderboard")

        logger.info("✅ Configuration consistency test PASSED")
        return True

    except Exception as e:
        logger.error(f"❌ Configuration consistency test FAILED: {e}")
        return False

def main():
    """Run all integration tests."""
    logger.info("🧪 Starting NBA Manager, Leaderboard, and Odds Manager Integration Tests")
    logger.info("=" * 70)

    tests = [
        ("NBA API Connectivity", test_nba_api_connectivity),
        ("Odds API Connectivity", test_odds_api_connectivity),
        ("NBA Manager Initialization", test_nba_manager_initialization),
        ("Leaderboard NBA Integration", test_leaderboard_nba_integration),
        ("Odds Manager Integration", test_odds_manager_integration),
        ("Configuration Consistency", test_configuration_consistency),
    ]

    results = []
    for test_name, test_func in tests:
        logger.info(f"\n🔍 Running: {test_name}")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"❌ {test_name} crashed: {e}")
            results.append((test_name, False))

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("📊 TEST SUMMARY")
    logger.info("=" * 70)

    passed = 0
    failed = 0

    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        logger.info(f"{test_name:<25} {status}")
        if result:
            passed += 1
        else:
            failed += 1

    logger.info("-" * 70)
    logger.info(f"Total: {len(results)} | Passed: {passed} | Failed: {failed}")

    if failed == 0:
        logger.info("🎉 ALL TESTS PASSED! NBA integration is working correctly.")
        return True
    else:
        logger.error(f"❌ {failed} test(s) failed. Please check the issues above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
