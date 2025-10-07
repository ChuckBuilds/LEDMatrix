#!/usr/bin/env python3
"""
Test script to debug FlightAware API connectivity on the Pi.
Run this on your Pi to check if the API is working.
"""

import json
import requests
import sys
from pathlib import Path

def test_flight_api():
    """Test FlightAware API connectivity and configuration."""
    
    # Load config
    config_path = Path("config/config.json")
    secrets_path = Path("config/config_secrets.json")
    
    if not config_path.exists():
        print("❌ config/config.json not found")
        return False
    
    if not secrets_path.exists():
        print("❌ config/config_secrets.json not found")
        return False
    
    # Load configuration
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    with open(secrets_path, 'r') as f:
        secrets = json.load(f)
    
    # Check flight tracker config
    flight_config = config.get('flight_tracker', {})
    if not flight_config.get('enabled', False):
        print("❌ Flight tracker is disabled in config")
        return False
    
    flight_plan_enabled = flight_config.get('flight_plan_enabled', False)
    if not flight_plan_enabled:
        print("❌ Flight plan data is disabled in config")
        return False
    
    # Check API key
    flight_secrets = secrets.get('flight_tracker', {})
    api_key = flight_secrets.get('flightaware_api_key', '')
    
    if not api_key or api_key == 'YOUR_FLIGHTAWARE_API_KEY':
        print("❌ FlightAware API key is not set or still has placeholder value")
        print(f"   Current key: {api_key[:10]}..." if len(api_key) > 10 else f"   Current key: {api_key}")
        return False
    
    print(f"✅ API key found: {api_key[:10]}...")
    
    # Test API connectivity with a simple call
    test_callsign = "AAL123"  # American Airlines test flight
    url = f"https://aeroapi.flightaware.com/aeroapi/flights/{test_callsign}"
    headers = {"x-apikey": api_key}
    
    print(f"🔄 Testing API connectivity with callsign: {test_callsign}")
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"   Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("✅ API call successful!")
            if 'flights' in data and data['flights']:
                flight = data['flights'][0]
                print(f"   Flight found: {flight.get('ident', 'N/A')}")
                print(f"   Origin: {flight.get('origin', {}).get('code', 'N/A')}")
                print(f"   Destination: {flight.get('destination', {}).get('code', 'N/A')}")
                print(f"   Aircraft Type: {flight.get('aircraft_type', 'N/A')}")
            else:
                print("   No flight data returned (flight might not exist)")
            return True
            
        elif response.status_code == 401:
            print("❌ API authentication failed - check your API key")
            print(f"   Response: {response.text[:200]}")
            return False
            
        elif response.status_code == 403:
            print("❌ API access forbidden - check your API key permissions")
            print(f"   Response: {response.text[:200]}")
            return False
            
        elif response.status_code == 429:
            print("❌ API rate limit exceeded")
            print(f"   Response: {response.text[:200]}")
            return False
            
        else:
            print(f"❌ API call failed with status {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
            
    except requests.exceptions.Timeout:
        print("❌ API call timed out - check network connectivity")
        return False
        
    except requests.exceptions.ConnectionError:
        print("❌ Connection error - check network connectivity to FlightAware")
        return False
        
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def test_skyaware_connectivity():
    """Test SkyAware connectivity."""
    print("\n🔄 Testing SkyAware connectivity...")
    
    # Load config to get SkyAware URL
    config_path = Path("config/config.json")
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    flight_config = config.get('flight_tracker', {})
    skyaware_url = flight_config.get('skyaware_url', 'http://192.168.86.30/skyaware/data/aircraft.json')
    
    print(f"   SkyAware URL: {skyaware_url}")
    
    try:
        response = requests.get(skyaware_url, timeout=5)
        print(f"   Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            aircraft_count = len(data.get('aircraft', []))
            print(f"✅ SkyAware connected! Found {aircraft_count} aircraft")
            return True
        else:
            print(f"❌ SkyAware failed with status {response.status_code}")
            return False
            
    except requests.exceptions.Timeout:
        print("❌ SkyAware connection timed out")
        return False
        
    except requests.exceptions.ConnectionError:
        print("❌ SkyAware connection error - check if SkyAware is running")
        return False
        
    except Exception as e:
        print(f"❌ SkyAware error: {e}")
        return False

if __name__ == "__main__":
    print("🔍 Flight Tracker API Diagnostic Tool")
    print("=" * 50)
    
    # Test SkyAware first (easier test)
    skyaware_ok = test_skyaware_connectivity()
    
    # Test FlightAware API
    api_ok = test_flight_api()
    
    print("\n" + "=" * 50)
    print("📊 Summary:")
    print(f"   SkyAware: {'✅ Working' if skyaware_ok else '❌ Failed'}")
    print(f"   FlightAware API: {'✅ Working' if api_ok else '❌ Failed'}")
    
    if skyaware_ok and api_ok:
        print("\n🎉 All systems working! The 'Unknown' entries might be due to:")
        print("   - Rate limiting (API calls per hour/day)")
        print("   - Aircraft not in FlightAware database")
        print("   - Callsigns that don't match airline patterns")
    elif skyaware_ok and not api_ok:
        print("\n⚠️  SkyAware working but FlightAware API failing.")
        print("   You'll see aircraft on the map but no origin/destination data.")
    else:
        print("\n❌ Basic connectivity issues detected.")
