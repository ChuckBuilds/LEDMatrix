#!/usr/bin/env python3
"""
Test script for the new scroll system on Raspberry Pi.

This script provides comprehensive testing of the scroll system with real LED matrix hardware.
Run this on your Pi to verify smooth, high-performance scrolling.
"""

import sys
import os
import time
import logging
import json
from typing import Dict, Any
from PIL import Image, ImageDraw, ImageFont

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    from display_manager import DisplayManager
    from base_classes.scroll_base import BaseScrollController, ScrollMode, ScrollDirection
    from base_classes.scroll_mixin import ScrollMixin, LegacyScrollAdapter
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you're running this from the LEDMatrix project directory")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ScrollTestManager(ScrollMixin):
    """Test manager for validating scroll functionality on Pi."""
    
    def __init__(self, display_manager, test_config):
        self.display_manager = display_manager
        self.config = test_config
        
        # Initialize scroll controller
        self.init_scroll_controller(
            debug_name="ScrollTest",
            config_section='test_scroll'
        )
        
        # Test content
        self.test_images = {}
        self.current_test = None
        
    def create_test_image(self, test_name: str, width: int, text: str) -> Image.Image:
        """Create a test image of specified width."""
        height = self.display_manager.matrix.height
        image = Image.new('RGB', (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Create colorful test pattern
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255), (0, 255, 255)]
        
        # Background pattern
        for x in range(0, width, 20):
            color = colors[(x // 20) % len(colors)]
            draw.rectangle([x, 0, x + 10, height], fill=color)
        
        # Text overlay
        font = self.display_manager.regular_font
        text_width = self.display_manager.get_text_width(text, font)
        
        # Draw text multiple times across the image
        text_spacing = max(200, text_width + 50)
        for x in range(0, width, text_spacing):
            draw.text((x + 10, height // 2 - 5), text, fill=(255, 255, 255), font=font)
        
        # Add position markers every 100 pixels
        for x in range(0, width, 100):
            draw.line([(x, 0), (x, height)], fill=(255, 255, 255), width=1)
            draw.text((x + 2, 2), f"{x}", fill=(255, 255, 255), font=font)
        
        logger.info(f"Created {test_name} test image: {width}x{height}px")
        return image
    
    def run_test(self, test_name: str, duration: float = 30.0):
        """Run a specific scroll test."""
        if test_name not in self.test_images:
            logger.error(f"Test '{test_name}' not found")
            return False
        
        logger.info(f"Starting {test_name} test for {duration}s")
        
        test_image = self.test_images[test_name]
        self.update_scroll_content_size(test_image.width, test_image.height)
        self.reset_scroll_position()
        
        start_time = time.time()
        frame_count = 0
        fps_samples = []
        
        try:
            while time.time() - start_time < duration:
                frame_start = time.time()
                
                # Update scroll
                scroll_state = self.update_scroll()
                
                # Get visible portion
                visible = self.crop_scrolled_image(test_image, wrap_around=True)
                
                # Display
                self.display_manager.image = visible
                self.display_manager.update_display()
                
                frame_count += 1
                frame_time = time.time() - frame_start
                fps = 1.0 / frame_time if frame_time > 0 else 0
                fps_samples.append(fps)
                
                # Log progress every 5 seconds
                elapsed = time.time() - start_time
                if frame_count % 500 == 0:  # Approximately every 5 seconds at 100fps
                    avg_fps = sum(fps_samples[-100:]) / min(len(fps_samples), 100)
                    logger.info(f"{test_name}: {elapsed:.1f}s elapsed, "
                               f"FPS: {avg_fps:.1f}, "
                               f"Position: {scroll_state['scroll_position']:.1f}")
                
                # Frame rate limiting
                frame_delay = self.calculate_scroll_frame_delay()
                if frame_delay > 0:
                    time.sleep(frame_delay)
        
        except KeyboardInterrupt:
            logger.info(f"{test_name} test interrupted by user")
            return False
        
        # Calculate final statistics
        total_time = time.time() - start_time
        avg_fps = frame_count / total_time
        max_fps = max(fps_samples) if fps_samples else 0
        min_fps = min(fps_samples) if fps_samples else 0
        
        logger.info(f"{test_name} Test Results:")
        logger.info(f"  Duration: {total_time:.2f}s")
        logger.info(f"  Total frames: {frame_count}")
        logger.info(f"  Average FPS: {avg_fps:.1f}")
        logger.info(f"  FPS range: {min_fps:.1f} - {max_fps:.1f}")
        logger.info(f"  Image size: {test_image.width}x{test_image.height}px")
        
        return True


def create_test_config():
    """Create test configuration with various scroll settings."""
    return {
        'test_scroll': {
            'scroll_pixels_per_second': 20.0,
            'scroll_target_fps': 100.0,
            'scroll_mode': 'continuous_loop',
            'scroll_direction': 'left',
            'enable_scroll_metrics': True,
            'scroll_subpixel_positioning': True,
            'scroll_frame_skip_threshold': 0.001
        }
    }


def run_comprehensive_tests():
    """Run comprehensive scroll system tests on Pi."""
    print("=" * 60)
    print("LED Matrix Scroll System - Raspberry Pi Test Suite")
    print("=" * 60)
    
    # Load display configuration
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')
    if not os.path.exists(config_path):
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.template.json')
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        print("Using default configuration")
        config = {
            'display': {
                'hardware': {
                    'rows': 32,
                    'cols': 64,
                    'chain_length': 2,
                    'brightness': 95
                }
            }
        }
    
    # Add test configuration
    config.update(create_test_config())
    
    print(f"Initializing display manager...")
    try:
        display_manager = DisplayManager(config)
        print(f"✓ Display initialized: {display_manager.matrix.width}x{display_manager.matrix.height}")
    except Exception as e:
        print(f"✗ Failed to initialize display: {e}")
        print("Make sure you're running on a Raspberry Pi with LED matrix connected")
        return False
    
    # Create test manager
    test_manager = ScrollTestManager(display_manager, config)
    
    # Define test cases
    test_cases = [
        ("Small Text", 400, "Small scroll test - should be very smooth"),
        ("Medium Content", 1000, "Medium length content test"),
        ("Large Ticker", 2500, "Large ticker simulation - like stock data"),
        ("Huge Leaderboard", 5000, "Huge leaderboard simulation - multiple sports"),
        ("Extreme Long", 8000, "Extreme long content - stress test")
    ]
    
    print(f"\nCreating test images...")
    for test_name, width, text in test_cases:
        test_manager.test_images[test_name] = test_manager.create_test_image(test_name, width, text)
        memory_mb = (width * display_manager.matrix.height * 3) / (1024 * 1024)
        print(f"✓ {test_name}: {width}px wide ({memory_mb:.1f}MB)")
    
    # Run tests
    print(f"\nRunning scroll tests...")
    print(f"Each test runs for 15 seconds. Press Ctrl+C to skip to next test.")
    print(f"Watch your LED matrix for smooth scrolling!")
    
    results = {}
    
    for test_name, _, _ in test_cases:
        print(f"\n--- Testing: {test_name} ---")
        input(f"Press Enter to start {test_name} test (or Ctrl+C to skip all tests)...")
        
        try:
            success = test_manager.run_test(test_name, duration=15.0)
            results[test_name] = "PASSED" if success else "SKIPPED"
            
            if success:
                # Get performance info
                perf_info = test_manager.get_scroll_debug_info()
                print(f"Performance: {perf_info['current_fps']:.1f} FPS, "
                      f"{perf_info['pixels_per_second']:.1f} px/s")
        
        except KeyboardInterrupt:
            print(f"\nSkipping {test_name}")
            results[test_name] = "SKIPPED"
            
            # Ask if user wants to continue
            try:
                response = input("Continue with next test? (y/n): ")
                if response.lower() != 'y':
                    break
            except KeyboardInterrupt:
                break
    
    # Final results
    print(f"\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)
    
    for test_name, result in results.items():
        status = "✓" if result == "PASSED" else "○"
        print(f"{status} {test_name}: {result}")
    
    print(f"\nIf all tests showed smooth scrolling, the new scroll system is working perfectly!")
    print(f"You should have seen:")
    print(f"  • Smooth, flicker-free scrolling")
    print(f"  • Consistent speed regardless of image size")
    print(f"  • High frame rate (90-100 FPS)")
    print(f"  • No stuttering or jerky movement")
    
    return True


def quick_performance_test():
    """Quick performance test with live metrics."""
    print("Quick Performance Test")
    print("=" * 30)
    
    # Simple config for quick test
    config = {
        'display': {
            'hardware': {
                'rows': 32, 'cols': 64, 'chain_length': 2, 'brightness': 95
            }
        },
        'test_scroll': {
            'scroll_pixels_per_second': 25.0,
            'scroll_target_fps': 100.0,
            'enable_scroll_metrics': True
        }
    }
    
    try:
        display_manager = DisplayManager(config)
        test_manager = ScrollTestManager(display_manager, config)
        
        # Create a medium-sized test image
        test_image = test_manager.create_test_image("Quick Test", 2000, "PERFORMANCE TEST")
        test_manager.update_scroll_content_size(test_image.width, test_image.height)
        
        print("Running 10-second performance test...")
        print("Watch for smooth scrolling on your LED matrix!")
        
        start_time = time.time()
        frame_count = 0
        
        while time.time() - start_time < 10.0:
            scroll_state = test_manager.update_scroll()
            visible = test_manager.crop_scrolled_image(test_image)
            display_manager.image = visible
            display_manager.update_display()
            
            frame_count += 1
            
            # Show live stats every second
            if frame_count % 100 == 0:
                elapsed = time.time() - start_time
                fps = frame_count / elapsed
                print(f"  {elapsed:.1f}s: {fps:.1f} FPS, pos={scroll_state['scroll_position']:.1f}")
        
        total_time = time.time() - start_time
        final_fps = frame_count / total_time
        
        print(f"\nQuick Test Results:")
        print(f"  Average FPS: {final_fps:.1f}")
        print(f"  Total frames: {frame_count}")
        print(f"  Image size: 2000x32px")
        print(f"  Status: {'✓ EXCELLENT' if final_fps > 90 else '○ GOOD' if final_fps > 60 else '✗ NEEDS OPTIMIZATION'}")
        
    except Exception as e:
        print(f"Error in quick test: {e}")
        return False
    
    return True


if __name__ == "__main__":
    print("LED Matrix Scroll System - Pi Testing")
    print("Make sure your LED matrix is connected and working!")
    print()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--quick":
        quick_performance_test()
    else:
        print("Choose test mode:")
        print("1. Comprehensive tests (recommended)")
        print("2. Quick performance test")
        print("3. Exit")
        
        try:
            choice = input("\nEnter choice (1-3): ").strip()
            
            if choice == "1":
                run_comprehensive_tests()
            elif choice == "2":
                quick_performance_test()
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
