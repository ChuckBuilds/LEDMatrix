#!/usr/bin/env python3
"""
Diagnostic script to help determine the correct map scale settings.
This script analyzes your ADSB data to find the actual coverage area.
"""

import requests
import json
import math
from typing import Dict, List, Tuple

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two lat/lon points in miles."""
    R = 3959  # Earth's radius in miles
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c

def analyze_coverage(skyaware_url: str, center_lat: float, center_lon: float) -> None:
    """Analyze ADSB coverage from SkyAware data."""
    print(f"\n{'='*60}")
    print("ADSB Coverage Analysis")
    print(f"{'='*60}\n")
    print(f"Center: ({center_lat:.4f}, {center_lon:.4f})")
    print(f"SkyAware URL: {skyaware_url}\n")
    
    try:
        # Fetch current aircraft data
        response = requests.get(skyaware_url, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        aircraft_list = data.get('aircraft', [])
        print(f"Total aircraft in data: {len(aircraft_list)}")
        
        # Filter aircraft with valid positions
        aircraft_with_position = []
        for aircraft in aircraft_list:
            lat = aircraft.get('lat')
            lon = aircraft.get('lon')
            if lat is not None and lon is not None:
                distance = calculate_distance(lat, lon, center_lat, center_lon)
                aircraft_with_position.append({
                    'hex': aircraft.get('hex', 'unknown'),
                    'callsign': aircraft.get('flight', 'N/A').strip(),
                    'lat': lat,
                    'lon': lon,
                    'altitude': aircraft.get('alt_baro', aircraft.get('alt_geom', 0)),
                    'distance': distance
                })
        
        if not aircraft_with_position:
            print("\n❌ No aircraft with position data found!")
            print("   Make sure your ADSB receiver is running and has data.")
            return
        
        print(f"Aircraft with valid position: {len(aircraft_with_position)}\n")
        
        # Calculate statistics
        distances = [a['distance'] for a in aircraft_with_position]
        max_distance = max(distances)
        avg_distance = sum(distances) / len(distances)
        
        # Find 90th percentile (most planes are within this range)
        sorted_distances = sorted(distances)
        percentile_90_idx = int(len(sorted_distances) * 0.9)
        percentile_90 = sorted_distances[percentile_90_idx] if percentile_90_idx < len(sorted_distances) else sorted_distances[-1]
        
        print(f"Distance Statistics:")
        print(f"  • Maximum: {max_distance:.1f} miles")
        print(f"  • Average: {avg_distance:.1f} miles")
        print(f"  • 90th percentile: {percentile_90:.1f} miles")
        
        # Show closest and furthest
        closest = min(aircraft_with_position, key=lambda a: a['distance'])
        furthest = max(aircraft_with_position, key=lambda a: a['distance'])
        
        print(f"\nClosest aircraft:")
        print(f"  • Callsign: {closest['callsign']}")
        print(f"  • Distance: {closest['distance']:.2f} miles")
        print(f"  • Position: ({closest['lat']:.4f}, {closest['lon']:.4f})")
        
        print(f"\nFurthest aircraft:")
        print(f"  • Callsign: {furthest['callsign']}")
        print(f"  • Distance: {furthest['distance']:.2f} miles")
        print(f"  • Position: ({furthest['lat']:.4f}, {furthest['lon']:.4f})")
        
        # Provide recommendations
        print(f"\n{'='*60}")
        print("RECOMMENDATIONS")
        print(f"{'='*60}\n")
        
        # Recommend map_radius_miles based on 90th percentile
        recommended_radius = math.ceil(percentile_90 * 1.2)  # Add 20% buffer
        print(f"1. map_radius_miles: {recommended_radius}")
        print(f"   (Covers 90% of your planes with 20% buffer)")
        
        # Recommend zoom_factor based on display size
        print(f"\n2. zoom_factor:")
        print(f"   • For 64x32 display: try 1.5 - 2.0")
        print(f"   • For 128x32 display: try 1.0 - 1.5")
        print(f"   • For 128x64 display: try 0.8 - 1.2")
        print(f"   (Higher = more zoomed in)")
        
        # Show aircraft distribution
        print(f"\nDistance Distribution:")
        bins = [0, 2, 5, 10, 20, 50, float('inf')]
        bin_labels = ['0-2mi', '2-5mi', '5-10mi', '10-20mi', '20-50mi', '50+mi']
        
        for i in range(len(bins) - 1):
            count = sum(1 for d in distances if bins[i] <= d < bins[i+1])
            percentage = (count / len(distances)) * 100
            bar = '█' * int(percentage / 2)
            print(f"  {bin_labels[i]:>8}: {count:3d} ({percentage:5.1f}%) {bar}")
        
        print(f"\n{'='*60}")
        print("To apply these settings, update your config.json:")
        print(f"{'='*60}\n")
        print(f'"flight_tracker": {{')
        print(f'    "map_radius_miles": {recommended_radius},')
        print(f'    "zoom_factor": 1.5,  // Adjust based on your display size')
        print(f'    ...')
        print(f'}}')
        print()
        
    except requests.exceptions.RequestException as e:
        print(f"\n❌ Error fetching data: {e}")
        print("   Make sure your SkyAware server is accessible.")
    except Exception as e:
        print(f"\n❌ Error analyzing data: {e}")

if __name__ == "__main__":
    # Load config to get settings
    try:
        with open('config/config.json', 'r') as f:
            config = json.load(f)
        
        flight_config = config.get('flight_tracker', {})
        center_lat = flight_config.get('center_latitude', 27.9506)
        center_lon = flight_config.get('center_longitude', -82.4572)
        skyaware_url = flight_config.get('skyaware_url', 'http://192.168.86.30/skyaware/data/aircraft.json')
        
        analyze_coverage(skyaware_url, center_lat, center_lon)
        
    except FileNotFoundError:
        print("❌ Could not find config/config.json")
        print("   Run this script from the LEDMatrix project root directory.")
    except Exception as e:
        print(f"❌ Error loading config: {e}")

