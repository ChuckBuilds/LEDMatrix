#!/usr/bin/env python3
"""
Test script to verify font API endpoints are working correctly.
"""
import requests
import json
import time

def test_font_api():
    """Test the font API endpoints."""
    base_url = "http://localhost:5001"
    
    endpoints = [
        "/api/fonts/catalog",
        "/api/fonts/tokens", 
        "/api/fonts/defaults",
        "/api/fonts/overrides"
    ]
    
    print("Testing font API endpoints...")
    
    for endpoint in endpoints:
        try:
            url = base_url + endpoint
            print(f"\nTesting {endpoint}...")
            
            response = requests.get(url, timeout=10)
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"Success: {data.get('status', 'unknown')}")
                if 'catalog' in data:
                    print(f"Catalog entries: {len(data['catalog'])}")
                elif 'tokens' in data:
                    print(f"Tokens: {data['tokens']}")
                elif 'defaults' in data:
                    print(f"Defaults: {data['defaults']}")
                elif 'overrides' in data:
                    print(f"Overrides: {len(data['overrides'])} entries")
            else:
                print(f"Error: {response.text}")
                
        except requests.exceptions.ConnectionError:
            print(f"Connection error: Web interface not running on {base_url}")
            break
        except Exception as e:
            print(f"Error testing {endpoint}: {e}")
    
    print("\nFont API test completed.")

if __name__ == "__main__":
    test_font_api()
