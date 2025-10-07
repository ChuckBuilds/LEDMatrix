#!/usr/bin/env python3
"""
Test Tile Providers Script
Tests different tile providers to find the most reliable one for your location.
"""

import requests
import time
import logging
from typing import Dict, List, Tuple

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TileProviderTester:
    """Test different tile providers for reliability and speed."""
    
    def __init__(self):
        self.providers = {
            'osm': [
                "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
                "https://b.tile.openstreetmap.org/{z}/{x}/{y}.png",
                "https://c.tile.openstreetmap.org/{z}/{x}/{y}.png"
            ],
            'carto': [
                "https://cartodb-basemaps-a.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png",
                "https://cartodb-basemaps-b.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png",
                "https://cartodb-basemaps-c.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png"
            ],
            'carto_dark': [
                "https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png",
                "https://cartodb-basemaps-b.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png",
                "https://cartodb-basemaps-c.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png"
            ],
            'stamen': [
                "https://stamen-tiles.a.ssl.fastly.net/terrain/{z}/{x}/{y}.png",
                "https://stamen-tiles.b.ssl.fastly.net/terrain/{z}/{x}/{y}.png",
                "https://stamen-tiles-c.a.ssl.fastly.net/terrain/{z}/{x}/{y}.png"
            ],
            'esri': [
                "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
                "https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}"
            ]
        }
        
        # Test coordinates (Tampa, FL area)
        self.test_coords = [
            (27.9506, -82.4572, 11),  # Tampa, FL
            (40.7128, -74.0060, 11),  # New York, NY
            (34.0522, -118.2437, 11), # Los Angeles, CA
        ]
    
    def test_provider(self, provider_name: str, url_template: str, x: int, y: int, z: int) -> Dict:
        """Test a single tile URL."""
        url = url_template.format(x=x, y=y, z=z)
        
        start_time = time.time()
        try:
            response = requests.get(url, timeout=10, headers={
                'User-Agent': 'TileProviderTester/1.0',
                'Accept': 'image/png,image/*,*/*;q=0.8'
            })
            
            end_time = time.time()
            response_time = end_time - start_time
            
            # Check response
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '').lower()
                if 'image' in content_type and len(response.content) > 2000:
                    return {
                        'success': True,
                        'response_time': response_time,
                        'size': len(response.content),
                        'content_type': content_type,
                        'status_code': response.status_code
                    }
                else:
                    return {
                        'success': False,
                        'error': f'Invalid content type: {content_type} or size: {len(response.content)}',
                        'response_time': response_time,
                        'status_code': response.status_code
                    }
            elif response.status_code == 403:
                return {
                    'success': False,
                    'error': 'Access blocked (403)',
                    'response_time': response_time,
                    'status_code': response.status_code
                }
            elif response.status_code == 429:
                return {
                    'success': False,
                    'error': 'Rate limited (429)',
                    'response_time': response_time,
                    'status_code': response.status_code
                }
            else:
                return {
                    'success': False,
                    'error': f'HTTP {response.status_code}',
                    'response_time': response_time,
                    'status_code': response.status_code
                }
                
        except requests.exceptions.Timeout:
            return {
                'success': False,
                'error': 'Timeout',
                'response_time': 10.0,
                'status_code': None
            }
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': f'Request error: {str(e)}',
                'response_time': time.time() - start_time,
                'status_code': None
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Unexpected error: {str(e)}',
                'response_time': time.time() - start_time,
                'status_code': None
            }
    
    def test_all_providers(self) -> Dict[str, List[Dict]]:
        """Test all providers with multiple coordinates."""
        results = {}
        
        for provider_name, urls in self.providers.items():
            logger.info(f"Testing provider: {provider_name}")
            results[provider_name] = []
            
            for lat, lon, zoom in self.test_coords:
                # Convert lat/lon to tile coordinates
                x, y = self._latlon_to_tile_coords(lat, lon, zoom)
                
                logger.info(f"  Testing coordinates: ({lat}, {lon}) -> tile ({x}, {y}, {zoom})")
                
                for i, url_template in enumerate(urls):
                    logger.info(f"    Trying URL {i+1}/{len(urls)}")
                    
                    result = self.test_provider(provider_name, url_template, x, y, zoom)
                    result['url_template'] = url_template
                    result['coordinates'] = (lat, lon, zoom)
                    result['tile_coords'] = (x, y, zoom)
                    
                    results[provider_name].append(result)
                    
                    if result['success']:
                        logger.info(f"    ✓ Success: {result['response_time']:.2f}s, {result['size']} bytes")
                        break  # Found working URL, no need to try others
                    else:
                        logger.warning(f"    ✗ Failed: {result['error']}")
                    
                    # Add delay between requests to be respectful
                    time.sleep(1)
                
                # Add delay between coordinate tests
                time.sleep(2)
        
        return results
    
    def _latlon_to_tile_coords(self, lat: float, lon: float, zoom: int) -> Tuple[int, int]:
        """Convert lat/lon to tile coordinates."""
        import math
        n = 2.0 ** zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
        return (x, y)
    
    def print_results(self, results: Dict[str, List[Dict]]):
        """Print test results in a readable format."""
        print("\n" + "="*80)
        print("TILE PROVIDER TEST RESULTS")
        print("="*80)
        
        for provider_name, provider_results in results.items():
            print(f"\n{provider_name.upper()}:")
            print("-" * 40)
            
            successful_tests = [r for r in provider_results if r['success']]
            failed_tests = [r for r in provider_results if not r['success']]
            
            if successful_tests:
                avg_time = sum(r['response_time'] for r in successful_tests) / len(successful_tests)
                avg_size = sum(r['size'] for r in successful_tests) / len(successful_tests)
                print(f"  ✓ SUCCESS: {len(successful_tests)}/{len(provider_results)} tests passed")
                print(f"  ✓ Average response time: {avg_time:.2f}s")
                print(f"  ✓ Average tile size: {avg_size/1024:.1f} KB")
                
                # Show working URLs
                working_urls = set(r['url_template'] for r in successful_tests)
                for url in working_urls:
                    print(f"  ✓ Working URL: {url}")
            else:
                print(f"  ✗ FAILED: 0/{len(provider_results)} tests passed")
            
            if failed_tests:
                print(f"  ✗ Failed tests: {len(failed_tests)}")
                error_counts = {}
                for result in failed_tests:
                    error = result['error']
                    error_counts[error] = error_counts.get(error, 0) + 1
                
                for error, count in error_counts.items():
                    print(f"    - {error}: {count} times")
        
        # Summary recommendations
        print("\n" + "="*80)
        print("RECOMMENDATIONS")
        print("="*80)
        
        # Find best providers
        provider_scores = {}
        for provider_name, provider_results in results.items():
            successful_tests = [r for r in provider_results if r['success']]
            if successful_tests:
                avg_time = sum(r['response_time'] for r in successful_tests) / len(successful_tests)
                success_rate = len(successful_tests) / len(provider_results)
                # Score based on success rate and speed (lower time = higher score)
                score = success_rate * (1.0 / (avg_time + 0.1))  # Add small value to avoid division by zero
                provider_scores[provider_name] = score
        
        if provider_scores:
            sorted_providers = sorted(provider_scores.items(), key=lambda x: x[1], reverse=True)
            
            print("\nBest providers (in order of recommendation):")
            for i, (provider, score) in enumerate(sorted_providers[:3], 1):
                print(f"  {i}. {provider} (score: {score:.2f})")
            
            print(f"\nRecommended provider: {sorted_providers[0][0]}")
            print(f"Use this in your flight_tracker_dev_viewer.py by setting tile_provider to '{sorted_providers[0][0]}'")
        else:
            print("\n⚠️  WARNING: No providers are working reliably!")
            print("This might be due to:")
            print("  - Network connectivity issues")
            print("  - All tile servers being blocked")
            print("  - Firewall restrictions")
            print("\nTry running the test again later, or check your network connection.")

def main():
    """Main test function."""
    print("Testing tile providers for reliability and speed...")
    print("This will test multiple providers with different coordinates.")
    print("Please be patient as this may take a few minutes.\n")
    
    tester = TileProviderTester()
    results = tester.test_all_providers()
    tester.print_results(results)

if __name__ == "__main__":
    main()
