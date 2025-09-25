"""
Test script for the new scroll system.

This script demonstrates the scroll system functionality and can be used
for testing and validation.
"""

import sys
import os
import time
import logging

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from base_classes.scroll_base import BaseScrollController, ScrollMode, ScrollDirection
from base_classes.scroll_mixin import LegacyScrollAdapter

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_scroll_controller():
    """Test the BaseScrollController functionality."""
    print("\n=== Testing BaseScrollController ===")
    
    # Create a test configuration
    config = {
        'scroll_pixels_per_second': 25.0,
        'scroll_target_fps': 100.0,
        'scroll_mode': 'continuous_loop',
        'scroll_direction': 'left',
        'enable_scroll_metrics': True,
        'scroll_loop_gap_pixels': 50
    }
    
    # Create controller (simulating 128x32 display with 400px content)
    controller = BaseScrollController(
        config=config,
        display_width=128,
        display_height=32,
        content_width=400,
        content_height=32,
        debug_name="TestController"
    )
    
    print(f"Initial state: {controller.should_scroll()}")
    print(f"Content size: {controller.content_width}x{controller.content_height}")
    print(f"Display size: {controller.display_width}x{controller.display_height}")
    
    # Simulate several frames
    start_time = time.time()
    for i in range(10):
        current_time = start_time + (i * 0.01)  # 10ms per frame (100 FPS)
        state = controller.update(current_time)
        
        if i % 3 == 0:  # Print every 3rd frame
            print(f"Frame {i}: pos={state['scroll_position']:.1f}, "
                  f"scrolling={state['is_scrolling']}, "
                  f"delta={state['scroll_delta']:.2f}")
    
    # Test crop region calculation
    crop_info = controller.get_crop_region()
    print(f"Crop region: {crop_info}")
    
    # Test debug info
    debug_info = controller.get_debug_info()
    print(f"Debug info: {debug_info}")
    
    print("✓ BaseScrollController test completed")


def test_legacy_adapter():
    """Test the legacy configuration adapter."""
    print("\n=== Testing LegacyScrollAdapter ===")
    
    # Test legacy config conversion
    legacy_config = {
        'scroll_speed': 2,
        'scroll_delay': 0.02,
        'scroll_speed_scale': 4,
        'other_setting': 'preserved'
    }
    
    print(f"Legacy config: {legacy_config}")
    
    converted = LegacyScrollAdapter.convert_legacy_config(legacy_config)
    print(f"Converted config: {converted}")
    
    # Verify conversion
    expected_pixels_per_second = (2 / 0.02) * 4  # speed / delay * scale = 400
    actual_pixels_per_second = converted['scroll_pixels_per_second']
    
    print(f"Expected pixels/sec: {expected_pixels_per_second}")
    print(f"Actual pixels/sec: {actual_pixels_per_second}")
    
    assert abs(actual_pixels_per_second - expected_pixels_per_second) < 0.1, \
        f"Conversion failed: expected {expected_pixels_per_second}, got {actual_pixels_per_second}"
    
    print("✓ LegacyScrollAdapter test completed")


def test_scroll_modes():
    """Test different scroll modes."""
    print("\n=== Testing Scroll Modes ===")
    
    base_config = {
        'scroll_pixels_per_second': 50.0,  # Fast for testing
        'scroll_target_fps': 100.0,
        'enable_scroll_metrics': False
    }
    
    modes_to_test = [
        ('continuous_loop', ScrollMode.CONTINUOUS_LOOP),
        ('one_shot', ScrollMode.ONE_SHOT),
        ('bounce', ScrollMode.BOUNCE),
        ('static', ScrollMode.STATIC)
    ]
    
    for mode_name, mode_enum in modes_to_test:
        print(f"\nTesting {mode_name} mode:")
        
        config = base_config.copy()
        config['scroll_mode'] = mode_name
        
        controller = BaseScrollController(
            config=config,
            display_width=64,
            display_height=16,
            content_width=200,
            content_height=16,
            debug_name=f"Test{mode_name.title()}"
        )
        
        # Simulate scrolling for a bit
        start_time = time.time()
        positions = []
        
        for i in range(20):
            current_time = start_time + (i * 0.02)  # 50 FPS
            state = controller.update(current_time)
            positions.append(state['scroll_position'])
            
            if not state['is_scrolling'] and mode_enum == ScrollMode.ONE_SHOT:
                print(f"  One-shot mode stopped at position {state['scroll_position']:.1f}")
                break
        
        print(f"  Position range: {min(positions):.1f} to {max(positions):.1f}")
        print(f"  Should scroll: {controller.should_scroll()}")
        
        if mode_enum == ScrollMode.STATIC:
            assert all(pos == 0.0 for pos in positions), "Static mode should not scroll"
            print("  ✓ Static mode correctly stayed at position 0")
    
    print("✓ Scroll modes test completed")


def test_performance_simulation():
    """Simulate performance under different conditions."""
    print("\n=== Performance Simulation ===")
    
    # Test different FPS targets
    fps_targets = [30, 60, 100, 120]
    
    for target_fps in fps_targets:
        print(f"\nTesting {target_fps} FPS target:")
        
        config = {
            'scroll_pixels_per_second': 20.0,
            'scroll_target_fps': target_fps,
            'scroll_mode': 'continuous_loop',
            'enable_scroll_metrics': True
        }
        
        controller = BaseScrollController(
            config=config,
            display_width=128,
            display_height=32,
            content_width=500,
            content_height=32,
            debug_name=f"Perf{target_fps}FPS"
        )
        
        # Simulate frames at target FPS
        frame_time = 1.0 / target_fps
        start_time = time.time()
        
        for i in range(target_fps):  # Simulate 1 second
            current_time = start_time + (i * frame_time)
            state = controller.update(current_time)
        
        # Check metrics
        if controller.enable_metrics:
            current_fps = controller.metrics.get_current_fps()
            avg_speed = controller.metrics.get_average_scroll_speed()
            print(f"  Measured FPS: {current_fps:.1f}")
            print(f"  Measured speed: {avg_speed:.1f} px/s (target: 20.0)")
            print(f"  Total frames: {controller.metrics.total_frames}")
    
    print("✓ Performance simulation completed")


def main():
    """Run all tests."""
    print("LED Matrix Scroll System Test Suite")
    print("=" * 50)
    
    try:
        test_scroll_controller()
        test_legacy_adapter()
        test_scroll_modes()
        test_performance_simulation()
        
        print("\n" + "=" * 50)
        print("✅ All tests passed successfully!")
        print("\nThe scroll system is ready for use.")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
