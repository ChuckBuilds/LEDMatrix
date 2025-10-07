#!/usr/bin/env python3
"""
Debug script to test FlightAware API connectivity and response format.
This will help diagnose why flight plan data is showing as "unknown".
"""

import requests
import json
import sys

def test_flightaware_api():
    """Test FlightAware API with the configured API key."""
    
    # API key from your config
    api_key = "hgnokHdUle3IVzt9CzeLXIippsR4qHDj"
    
    # Test with a common flight callsign
    test_callsigns = [
        "UAL123",  # United Airlines
        "AAL456",  # American Airlines  
        "DAL789",  # Delta Airlines
        "SWA321"   # Southwest Airlines
    ]
    
    print("Testing FlightAware API connectivity...")
    print(f"API Key: {api_key[:8]}...{api_key[-8:]}")
    print("-" * 50)
    
    for callsign in test_callsigns:
        print(f"\nTesting callsign: {callsign}")
        
        try:
            url = f"https://aeroapi.flightaware.com/aeroapi/flights/{callsign}"
            headers = {"x-apikey": api_key}
            
            print(f"URL: {url}")
            response = requests.get(url, headers=headers, timeout=10)
            
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print("✅ Success! Response data:")
                print(json.dumps(data, indent=2))
                
                # Extract origin/destination like the flight manager does
                origin = data.get('origin', {}).get('code', 'Unknown')
                destination = data.get('destination', {}).get('code', 'Unknown')
                print(f"Extracted Origin: {origin}")
                print(f"Extracted Destination: {destination}")
                
            elif response.status_code == 401:
                print("❌ Authentication failed - API key may be invalid")
                print(f"Response: {response.text}")
                
            elif response.status_code == 404:
                print("⚠️  Flight not found - this is normal for test callsigns")
                print(f"Response: {response.text}")
                
            else:
                print(f"❌ Unexpected status code: {response.status_code}")
                print(f"Response: {response.text}")
                
        except requests.exceptions.RequestException as e:
            print(f"❌ Request failed: {e}")
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
    
    print("\n" + "=" * 50)
    print("API Test Complete")
    print("If you see 404 errors, that's normal - the test callsigns may not be active.")
    print("The important thing is that you get a 200 response for any real flight.")

if __name__ == "__main__":
    test_flightaware_api()
