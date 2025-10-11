#!/usr/bin/env python3
"""
Test New Architecture Components

This test validates the new sports architecture including:
- API extractors
- Sport configurations  
- Data sources
- Baseball base classes
"""

import sys
import os
import logging
from typing import Dict, Any

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

def test_sport_configurations():
    """Test sport-specific configurations."""
    print("🧪 Testing Sport Configurations...")
    
    try:
        from src.base_classes.sport_configs import get_sport_configs, get_sport_config
        
        # Test getting all configurations
        configs = get_sport_configs()
        print(f"✅ Loaded {len(configs)} sport configurations")
        
        # Test each sport
        sports_to_test = ['nfl', 'ncaa_fb', 'mlb', 'nhl', 'ncaam_hockey', 'soccer', 'nba']
        
        for sport_key in sports_to_test:
            config = get_sport_config(sport_key, None)
            print(f"✅ {sport_key}: {config.update_cadence}, {config.season_length} games, {config.data_source_type}")
            
            # Validate configuration
            assert config.update_cadence in ['daily', 'weekly', 'hourly', 'live_only']
            assert config.season_length > 0
            assert config.data_source_type in ['espn', 'mlb_api', 'soccer_api']
            assert len(config.sport_specific_fields) > 0
        
        print("✅ All sport configurations valid")
        return True
        
    except Exception as e:
        print(f"❌ Sport configuration test failed: {e}")
        return False

def test_api_extractors():
    """Test API extractors for different sports."""
    print("\n🧪 Testing API Extractors...")

    # API extractors have been removed as they were unused dead code
    print("ℹ️ API extractors removed (unused code cleanup)")
    print("✅ API extractor test skipped")
    return True

def test_data_sources():
    """Test data sources for different sports."""
    print("\n🧪 Testing Data Sources...")
    
    try:
        from src.base_classes.data_sources import get_data_source_for_sport
        logger = logging.getLogger('test')
        
        # Test different data source types
        data_source_tests = [
            ('nfl', 'espn'),
            ('mlb', 'mlb_api'),
            ('soccer', 'soccer_api')
        ]
        
        for sport_key, source_type in data_source_tests:
            data_source = get_data_source_for_sport(sport_key, source_type, logger)
            print(f"✅ {sport_key} data source: {type(data_source).__name__}")
            
            # Test that data source has required methods
            assert hasattr(data_source, 'fetch_live_games')
            assert hasattr(data_source, 'fetch_schedule')
            assert hasattr(data_source, 'fetch_standings')
            assert callable(data_source.fetch_live_games)
            assert callable(data_source.fetch_schedule)
            assert callable(data_source.fetch_standings)
        
        print("✅ All data sources valid")
        return True
        
    except Exception as e:
        print(f"❌ Data source test failed: {e}")
        return False

def test_baseball_base_class():
    """Test baseball base class without hardware dependencies."""
    print("\n🧪 Testing Baseball Base Class...")
    
    try:
        # Test that we can import the baseball base class
        from src.base_classes.baseball import Baseball, BaseballLive, BaseballRecent, BaseballUpcoming
        print("✅ Baseball base classes imported successfully")
        
        # Test that classes are properly defined
        assert Baseball is not None
        assert BaseballLive is not None
        assert BaseballRecent is not None
        assert BaseballUpcoming is not None
        
        print("✅ Baseball base classes properly defined")
        return True
        
    except Exception as e:
        print(f"❌ Baseball base class test failed: {e}")
        return False

def test_sport_specific_fields():
    """Test that each sport has appropriate sport-specific fields."""
    print("\n🧪 Testing Sport-Specific Fields...")
    
    try:
        from src.base_classes.sport_configs import get_sport_config
        
        # Test sport-specific fields for each sport
        sport_fields_tests = {
            'nfl': ['down', 'distance', 'possession', 'timeouts', 'is_redzone'],
            'mlb': ['inning', 'outs', 'bases', 'strikes', 'balls', 'pitcher', 'batter'],
            'nhl': ['period', 'power_play', 'penalties', 'shots_on_goal'],
            'soccer': ['half', 'stoppage_time', 'cards', 'possession']
        }
        
        for sport_key, expected_fields in sport_fields_tests.items():
            config = get_sport_config(sport_key, None)
            actual_fields = config.sport_specific_fields
            
            print(f"✅ {sport_key} fields: {actual_fields}")
            
            # Check that we have the expected fields
            for field in expected_fields:
                assert field in actual_fields, f"Missing field {field} for {sport_key}"
        
        print("✅ All sport-specific fields valid")
        return True
        
    except Exception as e:
        print(f"❌ Sport-specific fields test failed: {e}")
        return False

def test_configuration_consistency():
    """Test that configurations are consistent and logical."""
    print("\n🧪 Testing Configuration Consistency...")
    
    try:
        from src.base_classes.sport_configs import get_sport_config
        
        # Test that each sport has logical configuration
        sports_to_test = ['nfl', 'ncaa_fb', 'mlb', 'nhl', 'ncaam_hockey', 'soccer', 'nba']
        
        for sport_key in sports_to_test:
            config = get_sport_config(sport_key, None)
            
            # Test update cadence makes sense
            if config.season_length > 100:  # Long season
                assert config.update_cadence in ['daily', 'hourly'], f"{sport_key} should have frequent updates for long season"
            elif config.season_length < 20:  # Short season
                assert config.update_cadence in ['weekly', 'daily'], f"{sport_key} should have less frequent updates for short season"
            
            # Test that games per week makes sense
            assert config.games_per_week > 0, f"{sport_key} should have at least 1 game per week"
            assert config.games_per_week <= 7, f"{sport_key} should not have more than 7 games per week"
            
            # Test that season length is reasonable
            assert config.season_length > 0, f"{sport_key} should have positive season length"
            assert config.season_length < 200, f"{sport_key} season length seems too long"
            
            print(f"✅ {sport_key} configuration is consistent")
        
        print("✅ All configurations are consistent")
        return True
        
    except Exception as e:
        print(f"❌ Configuration consistency test failed: {e}")
        return False

def main():
    """Run all architecture tests."""
    print("🚀 Testing New Sports Architecture")
    print("=" * 50)
    
    # Configure logging
    logging.basicConfig(level=logging.WARNING)
    
    # Run all tests
    tests = [
        test_sport_configurations,
        test_api_extractors,
        test_data_sources,
        test_baseball_base_class,
        test_sport_specific_fields,
        test_configuration_consistency
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
    print(f"🏁 Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All architecture tests passed! The new system is ready to use.")
        return True
    else:
        print("❌ Some tests failed. Please check the errors above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
