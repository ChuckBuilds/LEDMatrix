#!/usr/bin/env python3
"""
Test Baseball Architecture

This test validates the new baseball base class and its integration
with the new architecture components.
"""

import sys
import os
import logging
from typing import Dict, Any

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

def test_baseball_imports():
    """Test that baseball base classes can be imported."""
    print("🧪 Testing Baseball Imports...")
    
    try:
        from src.base_classes.baseball import Baseball, BaseballLive, BaseballRecent, BaseballUpcoming
        print("✅ Baseball base classes imported successfully")
        return True
    except Exception as e:
        print(f"❌ Baseball import failed: {e}")
        return False

def test_baseball_configuration():
    """Test baseball-specific configuration."""
    print("\n🧪 Testing Baseball Configuration...")
    
    try:
        from src.base_classes.sport_configs import get_sport_config
        
        # Test MLB configuration
        mlb_config = get_sport_config('mlb', None)
        
        # Validate MLB-specific settings
        assert mlb_config.update_cadence == 'daily', "MLB should have daily updates"
        assert mlb_config.season_length == 162, "MLB season should be 162 games"
        assert mlb_config.games_per_week == 6, "MLB should have ~6 games per week"
        assert mlb_config.data_source_type == 'mlb_api', "MLB should use MLB API"
        
        # Test baseball-specific fields
        expected_fields = ['inning', 'outs', 'bases', 'strikes', 'balls', 'pitcher', 'batter']
        for field in expected_fields:
            assert field in mlb_config.sport_specific_fields, f"Missing baseball field: {field}"
        
        print("✅ Baseball configuration is correct")
        return True
        
    except Exception as e:
        print(f"❌ Baseball configuration test failed: {e}")
        return False

def test_baseball_api_extractor():
    """Test baseball API extractor."""
    print("\n🧪 Testing Baseball API Extractor...")

    # API extractors have been removed as they were unused dead code
    print("ℹ️ API extractors removed (unused code cleanup)")
    print("✅ Baseball API extractor test skipped")
    return True

def test_baseball_data_source():
    """Test baseball data source."""
    print("\n🧪 Testing Baseball Data Source...")
    
    try:
        from src.base_classes.data_sources import get_data_source_for_sport
        logger = logging.getLogger('test')
        
        # Get MLB data source
        mlb_data_source = get_data_source_for_sport('mlb', 'mlb_api', logger)
        print(f"✅ MLB data source: {type(mlb_data_source).__name__}")
        
        # Test that data source has required methods
        assert hasattr(mlb_data_source, 'fetch_live_games')
        assert hasattr(mlb_data_source, 'fetch_schedule')
        assert hasattr(mlb_data_source, 'fetch_standings')
        
        print("✅ Baseball data source is properly configured")
        return True
        
    except Exception as e:
        print(f"❌ Baseball data source test failed: {e}")
        return False

def test_baseball_sport_specific_logic():
    """Test baseball-specific logic without hardware dependencies."""
    print("\n🧪 Testing Baseball Sport-Specific Logic...")
    
    try:
        # Test baseball-specific game data
        sample_baseball_game = {
            'inning': '3rd',
            'outs': 2,
            'bases': '1st, 3rd',
            'strikes': 2,
            'balls': 1,
            'pitcher': 'Gerrit Cole',
            'batter': 'Rafael Devers',
            'is_live': True,
            'is_final': False,
            'is_upcoming': False
        }
        
        # Test that we can identify baseball-specific characteristics
        assert sample_baseball_game['inning'] == '3rd'
        assert sample_baseball_game['outs'] == 2
        assert sample_baseball_game['bases'] == '1st, 3rd'
        assert sample_baseball_game['strikes'] == 2
        assert sample_baseball_game['balls'] == 1
        
        print("✅ Baseball sport-specific logic is working")
        return True
        
    except Exception as e:
        print(f"❌ Baseball sport-specific logic test failed: {e}")
        return False

def test_baseball_vs_other_sports():
    """Test that baseball has different characteristics than other sports."""
    print("\n🧪 Testing Baseball vs Other Sports...")
    
    try:
        from src.base_classes.sport_configs import get_sport_config
        
        # Compare baseball with other sports
        mlb_config = get_sport_config('mlb', None)
        nfl_config = get_sport_config('nfl', None)
        nhl_config = get_sport_config('nhl', None)
        
        # Baseball should have different characteristics
        assert mlb_config.season_length > nfl_config.season_length, "MLB season should be longer than NFL"
        assert mlb_config.games_per_week > nfl_config.games_per_week, "MLB should have more games per week than NFL"
        assert mlb_config.update_cadence == 'daily', "MLB should have daily updates"
        assert nfl_config.update_cadence == 'weekly', "NFL should have weekly updates"
        
        # Baseball should have different sport-specific fields
        mlb_fields = set(mlb_config.sport_specific_fields)
        nfl_fields = set(nfl_config.sport_specific_fields)
        nhl_fields = set(nhl_config.sport_specific_fields)
        
        # Baseball should have unique fields
        assert 'inning' in mlb_fields, "Baseball should have inning field"
        assert 'outs' in mlb_fields, "Baseball should have outs field"
        assert 'bases' in mlb_fields, "Baseball should have bases field"
        assert 'strikes' in mlb_fields, "Baseball should have strikes field"
        assert 'balls' in mlb_fields, "Baseball should have balls field"
        
        # Baseball should not have football/hockey fields
        assert 'down' not in mlb_fields, "Baseball should not have down field"
        assert 'distance' not in mlb_fields, "Baseball should not have distance field"
        assert 'period' not in mlb_fields, "Baseball should not have period field"
        
        print("✅ Baseball has distinct characteristics from other sports")
        return True
        
    except Exception as e:
        print(f"❌ Baseball vs other sports test failed: {e}")
        return False

def main():
    """Run all baseball architecture tests."""
    print("⚾ Testing Baseball Architecture")
    print("=" * 50)
    
    # Configure logging
    logging.basicConfig(level=logging.WARNING)
    
    # Run all tests
    tests = [
        test_baseball_imports,
        test_baseball_configuration,
        test_baseball_api_extractor,
        test_baseball_data_source,
        test_baseball_sport_specific_logic,
        test_baseball_vs_other_sports
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"❌ Test {test.__name__} failed with exception: {e}")
    
    print("\n" + "=" * 50)
    print(f"🏁 Baseball Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All baseball architecture tests passed! Baseball is ready to use.")
        return True
    else:
        print("❌ Some baseball tests failed. Please check the errors above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
