"""
Test script for offline aircraft database functionality.

This script tests the aircraft database lookup without making API calls.
"""

import sys
from pathlib import Path
import logging

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.aircraft_database import AircraftDatabase

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_database_initialization():
    """Test database initialization and download."""
    print("\n" + "="*60)
    print("Testing Aircraft Database Initialization")
    print("="*60)
    
    # Create test cache directory
    cache_dir = Path.home() / '.cache' / 'ledmatrix_test'
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\nUsing cache directory: {cache_dir}")
    
    # Initialize database
    print("\nInitializing aircraft database...")
    db = AircraftDatabase(cache_dir)
    
    # Get stats
    stats = db.get_stats()
    print(f"\nDatabase Statistics:")
    print(f"  Total Aircraft: {stats['total_aircraft']:,}")
    print(f"  FAA Aircraft: {stats['faa_aircraft']:,}")
    print(f"  OpenSky Aircraft: {stats['opensky_aircraft']:,}")
    print(f"  Database Size: {stats['database_size_mb']:.2f} MB")
    print(f"  Last Update: {stats['last_update']}")
    
    if stats['total_aircraft'] == 0:
        print("\n⚠️  Database is empty. This may be the first run.")
        print("    Database will download on next initialization.")
    else:
        print("\n✓ Database initialized successfully!")
    
    return db


def test_icao24_lookup(db: AircraftDatabase):
    """Test ICAO24 lookup."""
    print("\n" + "="*60)
    print("Testing ICAO24 Lookup")
    print("="*60)
    
    # Test ICAO24 codes (common US aircraft)
    test_icao24s = [
        'a12345',  # Example N-number conversion
        'a00001',  # Another example
        'abc123',  # Generic test
    ]
    
    for icao24 in test_icao24s:
        print(f"\nLooking up ICAO24: {icao24}")
        result = db.lookup_by_icao24(icao24)
        
        if result:
            print(f"  ✓ Found!")
            print(f"    Registration: {result.get('registration', 'Unknown')}")
            print(f"    Manufacturer: {result.get('manufacturer', 'Unknown')}")
            print(f"    Model: {result.get('model', 'Unknown')}")
            print(f"    Type: {result.get('type_aircraft', 'Unknown')}")
            print(f"    Operator: {result.get('operator', 'Unknown')}")
            print(f"    Source: {result.get('source', 'Unknown')}")
        else:
            print(f"  ✗ Not found in database")


def test_registration_lookup(db: AircraftDatabase):
    """Test registration lookup."""
    print("\n" + "="*60)
    print("Testing Registration Lookup")
    print("="*60)
    
    # Test common N-numbers (US registrations)
    test_registrations = [
        'N12345',   # Common pattern
        'N1234A',   # With letter
        'N123AB',   # Two letters
    ]
    
    for registration in test_registrations:
        print(f"\nLooking up Registration: {registration}")
        result = db.lookup_by_registration(registration)
        
        if result:
            print(f"  ✓ Found!")
            print(f"    ICAO24: {result.get('icao24', 'Unknown')}")
            print(f"    Manufacturer: {result.get('manufacturer', 'Unknown')}")
            print(f"    Model: {result.get('model', 'Unknown')}")
            print(f"    Type: {result.get('type_aircraft', 'Unknown')}")
            print(f"    Serial Number: {result.get('serial_number', 'Unknown')}")
            print(f"    Owner: {result.get('owner_name', 'Unknown')}")
            print(f"    Source: {result.get('source', 'Unknown')}")
        else:
            print(f"  ✗ Not found in database")


def test_performance(db: AircraftDatabase):
    """Test lookup performance."""
    print("\n" + "="*60)
    print("Testing Lookup Performance")
    print("="*60)
    
    import time
    
    # Test 100 random lookups
    test_count = 100
    start_time = time.time()
    
    for i in range(test_count):
        icao24 = f"a{i:05d}"
        db.lookup_by_icao24(icao24)
    
    elapsed = time.time() - start_time
    avg_time = (elapsed / test_count) * 1000  # milliseconds
    
    print(f"\n{test_count} lookups completed in {elapsed:.3f} seconds")
    print(f"Average lookup time: {avg_time:.2f} ms")
    
    if avg_time < 1.0:
        print("✓ Excellent performance!")
    elif avg_time < 5.0:
        print("✓ Good performance")
    else:
        print("⚠️  Slow performance - consider database optimization")


def test_update_database(db: AircraftDatabase):
    """Test database update (optional - can be skipped)."""
    print("\n" + "="*60)
    print("Testing Database Update")
    print("="*60)
    
    stats_before = db.get_stats()
    
    print("\n⚠️  This will download aircraft database (~50-100 MB)")
    print("    This may take several minutes...")
    
    user_input = input("\nProceed with database update? (yes/no): ").strip().lower()
    
    if user_input == 'yes':
        print("\nUpdating database...")
        success = db.update_database(force=True)
        
        if success:
            print("✓ Database updated successfully!")
            
            stats_after = db.get_stats()
            print(f"\nDatabase Statistics After Update:")
            print(f"  Total Aircraft: {stats_after['total_aircraft']:,} (was {stats_before['total_aircraft']:,})")
            print(f"  FAA Aircraft: {stats_after['faa_aircraft']:,}")
            print(f"  OpenSky Aircraft: {stats_after['opensky_aircraft']:,}")
            print(f"  Database Size: {stats_after['database_size_mb']:.2f} MB")
        else:
            print("✗ Database update failed")
    else:
        print("Skipping database update")


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("Aircraft Database Test Suite")
    print("="*60)
    
    try:
        # Initialize database
        db = test_database_initialization()
        
        # Run tests
        test_icao24_lookup(db)
        test_registration_lookup(db)
        test_performance(db)
        
        # Optional: Test database update
        test_update_database(db)
        
        print("\n" + "="*60)
        print("Test Suite Completed")
        print("="*60)
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        print(f"\n✗ Test suite failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

