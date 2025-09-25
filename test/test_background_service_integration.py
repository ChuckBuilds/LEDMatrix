#!/usr/bin/env python3
"""
Test Background Service Integration with API Extractor System

This test validates that the background data service properly integrates
with the new API extractor system and removes configuration duplication.
"""

import sys
import os
import logging
from typing import Dict, Any

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

def test_background_service_initialization():
    """Test that background service initializes with data sources and extractors."""
    print("🧪 Testing Background Service Initialization...")
    
    try:
        from src.cache_manager import CacheManager
        from src.background_data_service import BackgroundDataService
        
        # Create cache manager
        cache_manager = CacheManager()
        
        # Initialize background service
        background_service = BackgroundDataService(cache_manager, max_workers=2)
        
        # Test that data sources are initialized
        assert hasattr(background_service, 'data_sources'), "Background service should have data_sources"
        assert 'espn' in background_service.data_sources, "ESPN data source should be available"
        assert 'mlb_api' in background_service.data_sources, "MLB API data source should be available"
        assert 'soccer_api' in background_service.data_sources, "Soccer API data source should be available"
        
        # Test that API extractors are initialized
        assert hasattr(background_service, 'api_extractors'), "Background service should have api_extractors"
        assert 'football' in background_service.api_extractors, "Football extractor should be available"
        assert 'baseball' in background_service.api_extractors, "Baseball extractor should be available"
        assert 'hockey' in background_service.api_extractors, "Hockey extractor should be available"
        assert 'soccer' in background_service.api_extractors, "Soccer extractor should be available"
        
        print("✅ Background service initialization test passed")
        return True
        
    except Exception as e:
        print(f"❌ Background service initialization test failed: {e}")
        return False

def test_sport_classes_no_duplicate_config():
    """Test that sport classes no longer have duplicate SPORT_CONFIG."""
    print("\n🧪 Testing Sport Classes Configuration...")
    
    try:
        import inspect
        from src.base_classes.baseball import Baseball
        from src.base_classes.football import Football
        from src.base_classes.hockey import Hockey
        
        # Test that SPORT_CONFIG is removed from classes
        assert not hasattr(Baseball, 'SPORT_CONFIG'), "Baseball class should not have SPORT_CONFIG attribute"
        assert not hasattr(Football, 'SPORT_CONFIG'), "Football class should not have SPORT_CONFIG attribute"
        assert not hasattr(Hockey, 'SPORT_CONFIG'), "Hockey class should not have SPORT_CONFIG attribute"
        
        # Test that get_sport_config method exists and returns proper structure
        # We can't instantiate due to rgbmatrix dependency, but we can check method signature
        baseball_method = getattr(Baseball, 'get_sport_config')
        assert callable(baseball_method), "Baseball should have get_sport_config method"
        
        football_method = getattr(Football, 'get_sport_config')
        assert callable(football_method), "Football should have get_sport_config method"
        
        hockey_method = getattr(Hockey, 'get_sport_config')
        assert callable(hockey_method), "Hockey should have get_sport_config method"
        
        # Check method signatures
        baseball_sig = inspect.signature(baseball_method)
        assert len(baseball_sig.parameters) == 1, "get_sport_config should only take self parameter"
        
        print("✅ Sport classes configuration test passed")
        return True
        
    except Exception as e:
        print(f"❌ Sport classes configuration test failed: {e}")
        return False

def test_background_cache_mixin_integration():
    """Test that background cache mixin can use the new extractor system."""
    print("\n🧪 Testing Background Cache Mixin Integration...")
    
    try:
        from src.background_cache_mixin import BackgroundCacheMixin
        from src.cache_manager import CacheManager
        from src.background_data_service import BackgroundDataService
        
        # Create a test class that uses the mixin
        class TestManager(BackgroundCacheMixin):
            def __init__(self):
                self.cache_manager = CacheManager()
                self.background_service = BackgroundDataService(self.cache_manager, max_workers=1)
                self.logger = logging.getLogger('test')
        
        # Test that the mixin has the new method
        manager = TestManager()
        assert hasattr(manager, '_fetch_with_new_extractor_system'), "Mixin should have extractor system method"
        
        # Test sport mapping
        sport_mapping = {
            'nfl': ('football', 'nfl'),
            'ncaa_fb': ('football', 'college-football'),
            'mlb': ('baseball', 'mlb'),
            'nhl': ('hockey', 'nhl'),
            'soccer': ('soccer', 'soccer')
        }
        
        # Verify mapping is correct
        for sport_key, (sport_type, league) in sport_mapping.items():
            assert sport_type in manager.background_service.api_extractors, f"{sport_type} extractor should be available"
        
        print("✅ Background cache mixin integration test passed")
        return True
        
    except Exception as e:
        print(f"❌ Background cache mixin integration test failed: {e}")
        return False

def main():
    """Run all integration tests."""
    print("🚀 Starting Background Service Integration Tests...")
    
    tests = [
        test_background_service_initialization,
        test_sport_classes_no_duplicate_config,
        test_background_cache_mixin_integration
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ Test {test.__name__} crashed: {e}")
            failed += 1
    
    print(f"\n📊 Test Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("🎉 All tests passed! Background service integration is working correctly.")
        return True
    else:
        print("⚠️  Some tests failed. Please check the implementation.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
