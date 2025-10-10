#!/usr/bin/env python3
"""
Test script to verify that the NBA leaderboard fix works correctly.
This script simulates the leaderboard manager's data fetching process.
"""
import sys
import os
import logging
from typing import Dict, Any

# Add the src directory to Python path so we can import the leaderboard manager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_nba_standings_data():
    """Test that NBA standings data includes team ID fields."""
    try:
        from leaderboard_manager import LeaderboardManager
        from display_manager import DisplayManager
        from cache_manager import CacheManager
        import json

        # Load config
        with open('config/config.json', 'r') as f:
            config = json.load(f)

        # Create mock display and cache managers
        display_manager = DisplayManager(config)
        cache_manager = CacheManager()

        # Create leaderboard manager
        leaderboard_manager = LeaderboardManager(config, display_manager)

        # Test NBA standings fetching
        logger.info("Testing NBA standings data fetching...")
        nba_config = leaderboard_manager.league_configs['nba']
        nba_config['enabled'] = True  # Enable NBA for testing

        standings = leaderboard_manager._fetch_standings(nba_config)

        if not standings:
            logger.error("No NBA standings data returned!")
            return False

        logger.info(f"Successfully fetched {len(standings)} NBA teams")

        # Check if team ID fields are present
        missing_id_count = 0
        for i, team in enumerate(standings[:5]):  # Check first 5 teams
            team_id = team.get('id')
            team_abbr = team.get('abbreviation', 'Unknown')
            team_name = team.get('name', 'Unknown')

            logger.info(f"Team {i+1}: ID={team_id}, ABBR={team_abbr}, NAME={team_name}")

            if team_id is None:
                logger.error(f"Team {team_abbr} is missing ID field!")
                missing_id_count += 1

        if missing_id_count > 0:
            logger.error(f"{missing_id_count} teams are missing ID fields!")
            return False
        else:
            logger.info("All tested teams have ID fields!")

        # Test that we can create a leaderboard image (without actually displaying)
        logger.info("Testing leaderboard image creation...")
        leaderboard_manager.leaderboard_data = [{
            'league': 'nba',
            'league_config': nba_config,
            'teams': standings[:3]  # Test with first 3 teams
        }]

        try:
            leaderboard_manager._create_leaderboard_image()
            if leaderboard_manager.leaderboard_image:
                logger.info(f"Successfully created leaderboard image: {leaderboard_manager.leaderboard_image.width}x{leaderboard_manager.leaderboard_image.height}")
                return True
            else:
                logger.error("Failed to create leaderboard image!")
                return False
        except Exception as e:
            logger.error(f"Error creating leaderboard image: {e}")
            return False

    except ImportError as e:
        logger.error(f"Import error: {e}")
        logger.info("This script needs to be run from the LEDMatrix project directory")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return False

def main():
    """Main test function."""
    logger.info("Testing NBA leaderboard fix...")

    success = test_nba_standings_data()

    if success:
        logger.info("✅ NBA leaderboard fix test PASSED!")
        logger.info("The NBA leaderboard should now work correctly with team logos")
    else:
        logger.error("❌ NBA leaderboard fix test FAILED!")
        logger.info("The issue may not be fully resolved")

    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
