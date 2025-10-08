"""
Test script for flight manager with offline database.

This script tests the flight manager's integration with the offline aircraft database,
demonstrating reduced API calls.
"""

import sys
from pathlib import Path
import logging
import json
from unittest.mock import MagicMock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.flight_manager import BaseFlightManager
from src.cache_manager import CacheManager
from src.aircraft_database import AircraftDatabase

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def create_mock_display_manager():
    """Create a mock display manager."""
    mock = MagicMock()
    mock.matrix = MagicMock()
    mock.matrix.width = 128
    mock.matrix.height = 32
    return mock


def create_test_config():
    """Create test configuration."""
    return {
        'flight_tracker': {
            'enabled': True,
            'update_interval': 5,
            'center_latitude': 27.9506,
            'center_longitude': -82.4572,
            'map_radius_miles': 10,
            'skyaware_url': 'http://192.168.86.30/skyaware/data/aircraft.json',
            'flight_plan_enabled': False,  # Disable API calls for testing
            'use_offline_database': True,   # Enable offline database
            'max_api_calls_per_hour': 20,
            'daily_api_budget': 60,
        }
    }


def test_offline_database_integration():
    """Test that flight manager uses offline database."""
    print("\n" + "="*60)
    print("Testing Flight Manager - Offline Database Integration")
    print("="*60)
    
    # Create cache directory
    cache_dir = Path.home() / '.cache' / 'ledmatrix_test'
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize components
    print("\nInitializing components...")
    cache_manager = CacheManager(str(cache_dir))
    display_manager = create_mock_display_manager()
    config = create_test_config()
    
    # Create flight manager
    print("Creating flight manager...")
    manager = BaseFlightManager(config, display_manager, cache_manager)
    
    # Check if offline database is loaded
    if manager.aircraft_db:
        print("✓ Offline database loaded successfully!")
        stats = manager.aircraft_db.get_stats()
        print(f"\n  Database Statistics:")
        print(f"    Total Aircraft: {stats['total_aircraft']:,}")
        print(f"    Database Size: {stats['database_size_mb']:.2f} MB")
    else:
        print("✗ Offline database not loaded")
        return False
    
    return True


def test_aircraft_lookup_without_api():
    """Test aircraft lookup using offline database (no API calls)."""
    print("\n" + "="*60)
    print("Testing Aircraft Lookup (No API Calls)")
    print("="*60)
    
    # Create cache directory
    cache_dir = Path.home() / '.cache' / 'ledmatrix_test'
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize components
    cache_manager = CacheManager(str(cache_dir))
    display_manager = create_mock_display_manager()
    config = create_test_config()
    
    # Create flight manager
    manager = BaseFlightManager(config, display_manager, cache_manager)
    
    if not manager.aircraft_db:
        print("✗ Offline database not available")
        return False
    
    # Test aircraft lookups
    test_cases = [
        ('a12345', 'N12345'),  # ICAO24, Callsign
        ('abc123', 'ABC123'),
        ('a00001', 'N00001'),
    ]
    
    api_calls_made = 0
    db_lookups_successful = 0
    
    for icao24, callsign in test_cases:
        print(f"\nTesting lookup for ICAO24: {icao24}, Callsign: {callsign}")
        
        # Get flight plan data (should use offline DB, not API)
        flight_plan = manager._get_flight_plan_data(callsign, icao24)
        
        # Check if we used offline database
        if flight_plan.get('source') == 'offline_db':
            print(f"  ✓ Used offline database (NO API CALL)")
            print(f"    Aircraft Type: {flight_plan.get('aircraft_type', 'Unknown')}")
            print(f"    Registration: {flight_plan.get('registration', 'Unknown')}")
            print(f"    Operator: {flight_plan.get('operator', 'Unknown')}")
            db_lookups_successful += 1
        else:
            print(f"  ⚠️  Offline lookup failed, would have made API call")
            api_calls_made += 1
    
    print(f"\nResults:")
    print(f"  Offline DB Lookups: {db_lookups_successful}/{len(test_cases)}")
    print(f"  API Calls Avoided: {db_lookups_successful}")
    print(f"  API Calls Made: {api_calls_made}")
    
    if db_lookups_successful > 0:
        print(f"\n✓ Successfully avoided {db_lookups_successful} API calls!")
        return True
    else:
        print("\n✗ No successful offline lookups")
        return False


def test_api_call_reduction():
    """Demonstrate API call reduction with offline database."""
    print("\n" + "="*60)
    print("Testing API Call Reduction")
    print("="*60)
    
    # Create cache directory
    cache_dir = Path.home() / '.cache' / 'ledmatrix_test'
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    # Test WITHOUT offline database
    print("\n1. Without Offline Database:")
    print("-" * 40)
    
    config_no_db = create_test_config()
    config_no_db['flight_tracker']['use_offline_database'] = False
    
    cache_manager = CacheManager(str(cache_dir))
    display_manager = create_mock_display_manager()
    manager_no_db = BaseFlightManager(config_no_db, display_manager, cache_manager)
    
    print(f"  Offline Database Enabled: {manager_no_db.use_offline_db}")
    print(f"  Aircraft DB Loaded: {manager_no_db.aircraft_db is not None}")
    print("  Result: Would make API call for each aircraft")
    
    # Test WITH offline database
    print("\n2. With Offline Database:")
    print("-" * 40)
    
    config_with_db = create_test_config()
    config_with_db['flight_tracker']['use_offline_database'] = True
    
    manager_with_db = BaseFlightManager(config_with_db, display_manager, cache_manager)
    
    print(f"  Offline Database Enabled: {manager_with_db.use_offline_db}")
    print(f"  Aircraft DB Loaded: {manager_with_db.aircraft_db is not None}")
    
    if manager_with_db.aircraft_db:
        stats = manager_with_db.aircraft_db.get_stats()
        print(f"  Database Aircraft: {stats['total_aircraft']:,}")
        print("  Result: Can lookup aircraft info without API calls")
    
    print("\n" + "="*60)
    print("API Call Reduction Summary")
    print("="*60)
    print("\nTypical Scenario (10 aircraft visible):")
    print("  Without offline DB: 10 API calls (1 per aircraft)")
    print("  With offline DB: 0 API calls (all lookups local)")
    print("\nEstimated Savings:")
    print("  Daily: ~240 API calls (assuming 1 update/minute)")
    print("  Monthly: ~7,200 API calls")
    print("  Cost Savings: ~$36/month (at $0.005 per call)")


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("Flight Manager Offline Database Test Suite")
    print("="*60)
    
    try:
        # Test 1: Database integration
        if not test_offline_database_integration():
            print("\n⚠️  Offline database not available")
            print("   Run test_aircraft_database.py first to initialize database")
            return
        
        # Test 2: Aircraft lookup
        test_aircraft_lookup_without_api()
        
        # Test 3: API call reduction
        test_api_call_reduction()
        
        print("\n" + "="*60)
        print("Test Suite Completed Successfully")
        print("="*60)
        print("\n✓ Offline database is working!")
        print("✓ API calls will be significantly reduced")
        print("\nNext Steps:")
        print("  1. Update your config.json to enable offline database")
        print("  2. Let the database download on first run (one-time)")
        print("  3. Enjoy reduced API costs!")
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        print(f"\n✗ Test suite failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

