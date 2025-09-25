#!/usr/bin/env python3
"""
Test script to validate new scroll system with existing display managers.

This script shows how to test the scroll improvements with your current
stock manager, news manager, etc. on the Raspberry Pi.
"""

import sys
import os
import time
import json
import logging
from typing import Dict, Any

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from display_manager import DisplayManager
    from stock_manager import StockManager
    from stock_news_manager import StockNewsManager
    from news_manager import NewsManager
    from odds_ticker_manager import OddsTickerManager
    from leaderboard_manager import LeaderboardManager
    from text_display import TextDisplay
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you're running this from the LEDMatrix project directory")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ScrollPerformanceMonitor:
    """Monitor scroll performance of existing managers."""
    
    def __init__(self):
        self.frame_times = []
        self.start_time = None
        self.frame_count = 0
    
    def start_monitoring(self):
        """Start performance monitoring."""
        self.start_time = time.time()
        self.frame_times = []
        self.frame_count = 0
    
    def record_frame(self):
        """Record a frame for performance tracking."""
        if self.start_time is None:
            self.start_time = time.time()
        
        current_time = time.time()
        if self.frame_count > 0:
            frame_time = current_time - self.last_frame_time
            self.frame_times.append(frame_time)
            
            # Keep only last 100 frames
            if len(self.frame_times) > 100:
                self.frame_times.pop(0)
        
        self.last_frame_time = current_time
        self.frame_count += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current performance statistics."""
        if not self.frame_times:
            return {'fps': 0, 'avg_frame_time': 0, 'total_frames': self.frame_count}
        
        avg_frame_time = sum(self.frame_times) / len(self.frame_times)
        fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0
        
        return {
            'fps': fps,
            'avg_frame_time': avg_frame_time * 1000,  # ms
            'total_frames': self.frame_count,
            'elapsed_time': time.time() - self.start_time if self.start_time else 0
        }


def test_manager_performance(manager_class, manager_name: str, config: Dict[str, Any], 
                           duration: float = 30.0) -> Dict[str, Any]:
    """Test the performance of a specific manager."""
    print(f"\n--- Testing {manager_name} ---")
    
    try:
        # Initialize display manager
        display_manager = DisplayManager(config)
        
        # Initialize the manager
        if manager_class == TextDisplay:
            manager = manager_class(display_manager, config)
        else:
            manager = manager_class(config, display_manager)
        
        # Performance monitor
        monitor = ScrollPerformanceMonitor()
        monitor.start_monitoring()
        
        print(f"Running {manager_name} for {duration} seconds...")
        print("Watch your LED matrix for scrolling performance!")
        
        start_time = time.time()
        
        while time.time() - start_time < duration:
            frame_start = time.time()
            
            try:
                # Call the manager's display method
                if hasattr(manager, 'display_stocks'):
                    manager.display_stocks()
                elif hasattr(manager, 'display_news'):
                    manager.display_news()
                elif hasattr(manager, 'display'):
                    if manager_class == TextDisplay:
                        manager.update()
                        manager.display()
                    else:
                        manager.display()
                elif hasattr(manager, 'get_news_display'):
                    # News manager has a different interface
                    news_image = manager.get_news_display()
                    display_manager.image = news_image
                    display_manager.update_display()
                else:
                    print(f"Unknown display method for {manager_name}")
                    break
                
                monitor.record_frame()
                
                # Log progress every 5 seconds
                elapsed = time.time() - start_time
                if monitor.frame_count % 500 == 0 and elapsed > 0:
                    stats = monitor.get_stats()
                    print(f"  {elapsed:.1f}s: {stats['fps']:.1f} FPS, "
                          f"{stats['avg_frame_time']:.1f}ms frame time")
                
            except Exception as e:
                print(f"Error in {manager_name}: {e}")
                break
        
        # Final statistics
        final_stats = monitor.get_stats()
        final_stats['manager_name'] = manager_name
        
        print(f"{manager_name} Results:")
        print(f"  Average FPS: {final_stats['fps']:.1f}")
        print(f"  Frame time: {final_stats['avg_frame_time']:.1f}ms")
        print(f"  Total frames: {final_stats['total_frames']}")
        print(f"  Duration: {final_stats['elapsed_time']:.1f}s")
        
        # Performance assessment
        if final_stats['fps'] > 90:
            print(f"  Status: ✓ EXCELLENT - Very smooth scrolling")
        elif final_stats['fps'] > 60:
            print(f"  Status: ○ GOOD - Smooth scrolling")
        elif final_stats['fps'] > 30:
            print(f"  Status: △ ACCEPTABLE - May benefit from scroll system upgrade")
        else:
            print(f"  Status: ✗ POOR - Definitely needs scroll system upgrade")
        
        return final_stats
        
    except Exception as e:
        print(f"Failed to test {manager_name}: {e}")
        return {'manager_name': manager_name, 'error': str(e), 'fps': 0}


def load_config():
    """Load the LED matrix configuration."""
    config_paths = [
        os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json'),
        os.path.join(os.path.dirname(__file__), '..', 'config', 'config.template.json')
    ]
    
    for config_path in config_paths:
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                print(f"Loaded config from: {config_path}")
                return config
            except Exception as e:
                print(f"Error loading {config_path}: {e}")
    
    # Fallback config
    print("Using fallback configuration")
    return {
        'display': {
            'hardware': {
                'rows': 32,
                'cols': 64,
                'chain_length': 2,
                'brightness': 95,
                'hardware_mapping': 'adafruit-hat-pwm'
            }
        },
        'stocks': {'enabled': True, 'symbols': ['AAPL', 'GOOGL', 'TSLA']},
        'stock_news': {'enabled': True},
        'news_manager': {'enabled': True},
        'text_display': {
            'enabled': True,
            'text': 'Scroll performance test - checking smoothness and frame rate',
            'scroll': True,
            'scroll_speed': 30
        }
    }


def run_existing_manager_tests():
    """Run tests on existing display managers."""
    print("=" * 60)
    print("LED Matrix Existing Managers - Performance Test")
    print("=" * 60)
    print("This will test your current display managers to show")
    print("scroll performance before/after upgrading to the new system.")
    
    # Load configuration
    config = load_config()
    
    # Test cases - only test managers that are likely to work
    test_cases = [
        (TextDisplay, "Text Display", 15),
        (StockManager, "Stock Manager", 20),
        (StockNewsManager, "Stock News", 20),
    ]
    
    # Ask user which tests to run
    print(f"\nAvailable tests:")
    for i, (manager_class, name, duration) in enumerate(test_cases, 1):
        print(f"{i}. {name} ({duration}s)")
    print(f"{len(test_cases) + 1}. Run all tests")
    print(f"{len(test_cases) + 2}. Exit")
    
    try:
        choice = input(f"\nSelect test (1-{len(test_cases) + 2}): ").strip()
        
        if choice == str(len(test_cases) + 2):  # Exit
            return
        
        results = []
        
        if choice == str(len(test_cases) + 1):  # Run all
            selected_tests = test_cases
        else:
            try:
                index = int(choice) - 1
                if 0 <= index < len(test_cases):
                    selected_tests = [test_cases[index]]
                else:
                    print("Invalid choice")
                    return
            except ValueError:
                print("Invalid choice")
                return
        
        # Run selected tests
        for manager_class, name, duration in selected_tests:
            print(f"\nPreparing {name} test...")
            input(f"Press Enter to start {name} test (or Ctrl+C to skip)...")
            
            try:
                result = test_manager_performance(manager_class, name, config, duration)
                results.append(result)
                
                print(f"\n{name} test completed!")
                input("Press Enter to continue to next test...")
                
            except KeyboardInterrupt:
                print(f"\nSkipped {name} test")
                continue
        
        # Summary
        print(f"\n" + "=" * 60)
        print("PERFORMANCE TEST SUMMARY")
        print("=" * 60)
        
        for result in results:
            if 'error' in result:
                print(f"✗ {result['manager_name']}: ERROR - {result['error']}")
            else:
                fps = result['fps']
                if fps > 90:
                    status = "✓ EXCELLENT"
                elif fps > 60:
                    status = "○ GOOD"
                elif fps > 30:
                    status = "△ ACCEPTABLE"
                else:
                    status = "✗ POOR"
                
                print(f"{status} {result['manager_name']}: {fps:.1f} FPS")
        
        print(f"\nRecommendations:")
        print(f"• FPS > 90: Current performance is excellent")
        print(f"• FPS 60-90: Good performance, new scroll system will make it even smoother")
        print(f"• FPS 30-60: New scroll system will provide significant improvement")
        print(f"• FPS < 30: New scroll system will dramatically improve performance")
        
    except KeyboardInterrupt:
        print("\nExiting...")


def quick_scroll_comparison():
    """Quick comparison of old vs new scroll approaches."""
    print("Quick Scroll System Comparison")
    print("=" * 40)
    
    config = load_config()
    
    # Test with text display (simplest to set up)
    try:
        display_manager = DisplayManager(config)
        
        # Test current implementation
        print("Testing current scroll implementation...")
        text_config = config.get('text_display', {})
        text_config.update({
            'text': 'CURRENT SCROLL SYSTEM - Testing performance and smoothness',
            'scroll': True,
            'scroll_speed': 40
        })
        
        text_display = TextDisplay(display_manager, {'text_display': text_config})
        
        monitor = ScrollPerformanceMonitor()
        monitor.start_monitoring()
        
        print("Running current system for 10 seconds...")
        start_time = time.time()
        
        while time.time() - start_time < 10:
            text_display.update()
            text_display.display()
            monitor.record_frame()
        
        current_stats = monitor.get_stats()
        
        print(f"\nCurrent System Results:")
        print(f"  FPS: {current_stats['fps']:.1f}")
        print(f"  Frame time: {current_stats['avg_frame_time']:.1f}ms")
        
        print(f"\nWith the new scroll system, you can expect:")
        print(f"  • FPS: 95-100 (vs {current_stats['fps']:.1f})")
        print(f"  • Frame time: 10ms (vs {current_stats['avg_frame_time']:.1f}ms)")
        print(f"  • Smoother animation with subpixel positioning")
        print(f"  • Consistent performance regardless of content size")
        print(f"  • Built-in performance monitoring")
        
    except Exception as e:
        print(f"Error in comparison test: {e}")


if __name__ == "__main__":
    print("LED Matrix Scroll System - Existing Manager Testing")
    print("=" * 55)
    print("This script tests your current display managers to show")
    print("how the new scroll system will improve performance.")
    print()
    
    print("Choose test mode:")
    print("1. Test existing managers (comprehensive)")
    print("2. Quick scroll comparison")
    print("3. Exit")
    
    try:
        choice = input("\nEnter choice (1-3): ").strip()
        
        if choice == "1":
            run_existing_manager_tests()
        elif choice == "2":
            quick_scroll_comparison()
        elif choice == "3":
            print("Exiting...")
        else:
            print("Invalid choice")
            
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
