#!/usr/bin/env python3
"""
Test Sports Integration

This test validates that all sports work together with the new architecture
and that the system can handle multiple sports simultaneously.
"""

import sys
import os
import logging
from typing import Dict, Any, List

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

def test_all_sports_configuration():
    """Test that all sports have valid configurations."""
    print("🧪 Testing All Sports Configuration...")
    
    try:
        from src.base_classes.sport_configs import get_sport_configs, get_sport_config
        
        # Get all sport configurations
        configs = get_sport_configs()
        all_sports = list(configs.keys())
        
        print(f"✅ Found {len(all_sports)} sports: {all_sports}")
        
        # Test each sport
        for sport_key in all_sports:
            config = get_sport_config(sport_key, None)
            
            # Validate basic configuration
            assert config.update_cadence in ['daily', 'weekly', 'hourly', 'live_only']
            assert config.season_length > 0
            assert config.games_per_week > 0
            assert config.data_source_type in ['espn', 'mlb_api', 'soccer_api']
            assert len(config.sport_specific_fields) > 0
            
            print(f"✅ {sport_key}: {config.update_cadence}, {config.season_length} games, {config.data_source_type}")
        
        print("✅ All sports have valid configurations")
        return True
        
    except Exception as e:
        print(f"❌ All sports configuration test failed: {e}")
        return False

def test_sports_api_extractors():
    """Test that all sports have working API extractors."""
    print("\n🧪 Testing All Sports API Extractors...")
    
    try:
        from src.base_classes.api_extractors import get_extractor_for_sport
        logger = logging.getLogger('test')
        
        # Test all sports
        sports_to_test = ['nfl', 'ncaa_fb', 'mlb', 'nhl', 'ncaam_hockey', 'soccer', 'nba']
        
        for sport_key in sports_to_test:
            extractor = get_extractor_for_sport(sport_key, logger)
            print(f"✅ {sport_key} extractor: {type(extractor).__name__}")
            
            # Test that extractor has required methods
            assert hasattr(extractor, 'extract_game_details')
            assert hasattr(extractor, 'get_sport_specific_fields')
            assert callable(extractor.extract_game_details)
            assert callable(extractor.get_sport_specific_fields)
        
        print("✅ All sports have working API extractors")
        return True
        
    except Exception as e:
        print(f"❌ Sports API extractors test failed: {e}")
        return False

def test_sports_data_sources():
    """Test that all sports have working data sources."""
    print("\n🧪 Testing All Sports Data Sources...")
    
    try:
        from src.base_classes.data_sources import get_data_source_for_sport
        from src.base_classes.sport_configs import get_sport_config
        logger = logging.getLogger('test')
        
        # Test all sports
        sports_to_test = ['nfl', 'ncaa_fb', 'mlb', 'nhl', 'ncaam_hockey', 'soccer', 'nba']
        
        for sport_key in sports_to_test:
            # Get sport configuration to determine data source type
            config = get_sport_config(sport_key, None)
            data_source_type = config.data_source_type
            
            # Get data source
            data_source = get_data_source_for_sport(sport_key, data_source_type, logger)
            print(f"✅ {sport_key} data source: {type(data_source).__name__} ({data_source_type})")
            
            # Test that data source has required methods
            assert hasattr(data_source, 'fetch_live_games')
            assert hasattr(data_source, 'fetch_schedule')
            assert hasattr(data_source, 'fetch_standings')
            assert callable(data_source.fetch_live_games)
            assert callable(data_source.fetch_schedule)
            assert callable(data_source.fetch_standings)
        
        print("✅ All sports have working data sources")
        return True
        
    except Exception as e:
        print(f"❌ Sports data sources test failed: {e}")
        return False

def test_sports_consistency():
    """Test that sports configurations are consistent and logical."""
    print("\n🧪 Testing Sports Consistency...")
    
    try:
        from src.base_classes.sport_configs import get_sport_config
        
        # Test that each sport has logical configuration
        sports_to_test = ['nfl', 'ncaa_fb', 'mlb', 'nhl', 'ncaam_hockey', 'soccer', 'nba']
        
        for sport_key in sports_to_test:
            config = get_sport_config(sport_key, None)
            
            # Test update cadence makes sense for season length
            if config.season_length > 100:  # Long season (MLB, NBA, NHL)
                assert config.update_cadence in ['daily', 'hourly'], f"{sport_key} should have frequent updates for long season"
            elif config.season_length < 20:  # Short season (NFL, NCAA)
                assert config.update_cadence in ['weekly', 'daily'], f"{sport_key} should have less frequent updates for short season"
            
            # Test that games per week makes sense
            assert config.games_per_week > 0, f"{sport_key} should have at least 1 game per week"
            assert config.games_per_week <= 7, f"{sport_key} should not have more than 7 games per week"
            
            # Test that season length is reasonable
            assert config.season_length > 0, f"{sport_key} should have positive season length"
            assert config.season_length < 200, f"{sport_key} season length seems too long"
            
            print(f"✅ {sport_key} configuration is consistent")
        
        print("✅ All sports configurations are consistent")
        return True
        
    except Exception as e:
        print(f"❌ Sports consistency test failed: {e}")
        return False

def test_sports_uniqueness():
    """Test that each sport has unique characteristics."""
    print("\n🧪 Testing Sports Uniqueness...")
    
    try:
        from src.base_classes.sport_configs import get_sport_config
        
        # Test that each sport has unique sport-specific fields
        sports_to_test = ['nfl', 'mlb', 'nhl', 'soccer']
        
        sport_fields = {}
        for sport_key in sports_to_test:
            config = get_sport_config(sport_key, None)
            sport_fields[sport_key] = set(config.sport_specific_fields)
        
        # Test that each sport has unique fields
        for sport_key in sports_to_test:
            current_fields = sport_fields[sport_key]
            
            # Check that sport has unique fields
            if sport_key == 'nfl':
                assert 'down' in current_fields, "NFL should have down field"
                assert 'distance' in current_fields, "NFL should have distance field"
                assert 'possession' in current_fields, "NFL should have possession field"
            elif sport_key == 'mlb':
                assert 'inning' in current_fields, "MLB should have inning field"
                assert 'outs' in current_fields, "MLB should have outs field"
                assert 'bases' in current_fields, "MLB should have bases field"
                assert 'strikes' in current_fields, "MLB should have strikes field"
                assert 'balls' in current_fields, "MLB should have balls field"
            elif sport_key == 'nhl':
                assert 'period' in current_fields, "NHL should have period field"
                assert 'power_play' in current_fields, "NHL should have power_play field"
                assert 'penalties' in current_fields, "NHL should have penalties field"
            elif sport_key == 'soccer':
                assert 'half' in current_fields, "Soccer should have half field"
                assert 'stoppage_time' in current_fields, "Soccer should have stoppage_time field"
                assert 'cards' in current_fields, "Soccer should have cards field"
                assert 'possession' in current_fields, "Soccer should have possession field"
            
            print(f"✅ {sport_key} has unique sport-specific fields")
        
        print("✅ All sports have unique characteristics")
        return True
        
    except Exception as e:
        print(f"❌ Sports uniqueness test failed: {e}")
        return False

def test_sports_data_source_mapping():
    """Test that sports are mapped to appropriate data sources."""
    print("\n🧪 Testing Sports Data Source Mapping...")
    
    try:
        from src.base_classes.sport_configs import get_sport_config
        
        # Test that each sport uses an appropriate data source
        sports_to_test = ['nfl', 'ncaa_fb', 'mlb', 'nhl', 'ncaam_hockey', 'soccer', 'nba']
        
        for sport_key in sports_to_test:
            config = get_sport_config(sport_key, None)
            data_source_type = config.data_source_type
            
            # Test that data source type makes sense for the sport
            if sport_key == 'mlb':
                assert data_source_type == 'mlb_api', "MLB should use MLB API"
            elif sport_key == 'soccer':
                assert data_source_type == 'soccer_api', "Soccer should use Soccer API"
            else:
                assert data_source_type == 'espn', f"{sport_key} should use ESPN API"
            
            print(f"✅ {sport_key} uses appropriate data source: {data_source_type}")
        
        print("✅ All sports use appropriate data sources")
        return True
        
    except Exception as e:
        print(f"❌ Sports data source mapping test failed: {e}")
        return False

def main():
    """Run all sports integration tests."""
    print("🏈 Testing Sports Integration")
    print("=" * 50)
    
    # Configure logging
    logging.basicConfig(level=logging.WARNING)
    
    # Run all tests
    tests = [
        test_all_sports_configuration,
        test_sports_api_extractors,
        test_sports_data_sources,
        test_sports_consistency,
        test_sports_uniqueness,
        test_sports_data_source_mapping
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
    print(f"🏁 Sports Integration Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All sports integration tests passed! The system can handle multiple sports.")
        return True
    else:
        print("❌ Some sports integration tests failed. Please check the errors above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
