#!/usr/bin/env python3
"""
Test script to compare old TextDisplay vs new TextDisplayScrollManager.

This script demonstrates the performance improvements and features
of the new HorizontalScrollManager-based implementation.
"""

import sys
import os
import time
import logging

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.display_manager import DisplayManager
from src.text_display import TextDisplay
from src.text_display_scroll_manager import TextDisplayScrollManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def create_test_config():
    """Create test configuration for both implementations."""
    return {
        'text_display': {
            'enabled': True,
            'text': 'This is a test of the new HorizontalScrollManager base class! It provides high-performance scrolling with sub-pixel accuracy and time-based animation.',
            'font_path': 'assets/fonts/PressStart2P-Regular.ttf',
            'font_size': 8,
            'text_color': [255, 255, 0],  # Yellow text
            'background_color': [0, 0, 0],  # Black background
            'scroll': True,
            'scroll_speed': 50.0,  # pixels per second
            'scroll_gap_width': 64,  # Gap between loops
            
            # NEW: HorizontalScrollManager settings
            'max_fps': 100.0,  # Soft FPS limit
            'target_fps': 100.0,
            'enable_throttling': False,  # DISABLED - causes jitter!
            'loop_mode': 'continuous',
            'enable_wrap_around': True,
            'dynamic_duration': True,
            'min_duration': 10,
            'enable_fps_logging': True,
            'fps_log_interval': 5.0,
            
            # Anti-stutter settings
            'enable_delta_smoothing': True,
            'delta_smoothing_window': 5,
            'max_delta_time': 0.020,
            
            # Performance optimization
            'display_update_interval': 2,  # Update display every 2nd frame
            'gc_optimization': True
        }
    }

def test_old_implementation():
    """Test the original TextDisplay implementation."""
    logger.info("=" * 60)
    logger.info("TESTING OLD TextDisplay IMPLEMENTATION")
    logger.info("=" * 60)
    
    try:
        # Initialize display manager
        display_manager = DisplayManager()
        
        # Create config
        config = create_test_config()
        
        # Create old TextDisplay
        old_display = TextDisplay(display_manager, config)
        
        logger.info("Old TextDisplay initialized successfully")
        logger.info(f"Text: '{old_display.text[:50]}...'")
        logger.info(f"Scroll enabled: {old_display.scroll_enabled}")
        logger.info(f"Scroll speed: {old_display.scroll_speed} px/s")
        logger.info(f"Text width: {old_display.text_content_width}px")
        
        # Test display for a few seconds
        start_time = time.time()
        frame_count = 0
        
        logger.info("Starting old implementation test (10 seconds)...")
        
        while time.time() - start_time < 10:
            old_display.update()  # Update scroll position
            old_display.display()  # Display current frame
            frame_count += 1
            
            # Log progress every 2 seconds
            elapsed = time.time() - start_time
            if int(elapsed) % 2 == 0 and elapsed - int(elapsed) < 0.1:
                logger.info(f"Old implementation: {elapsed:.1f}s, {frame_count} frames")
        
        total_time = time.time() - start_time
        avg_fps = frame_count / total_time
        
        logger.info(f"Old implementation results:")
        logger.info(f"  - Total time: {total_time:.2f}s")
        logger.info(f"  - Total frames: {frame_count}")
        logger.info(f"  - Average FPS: {avg_fps:.1f}")
        logger.info(f"  - Text width: {old_display.text_content_width}px")
        
        return True
        
    except Exception as e:
        logger.error(f"Old implementation failed: {e}", exc_info=True)
        return False

def test_new_implementation():
    """Test the new TextDisplayScrollManager implementation."""
    logger.info("=" * 60)
    logger.info("TESTING NEW TextDisplayScrollManager IMPLEMENTATION")
    logger.info("=" * 60)
    
    try:
        # Initialize display manager
        display_manager = DisplayManager()
        
        # Create config
        config = create_test_config()
        
        # Create new TextDisplayScrollManager
        new_display = TextDisplayScrollManager(display_manager, config)
        
        logger.info("New TextDisplayScrollManager initialized successfully")
        logger.info(f"Text: '{new_display.text[:50]}...'")
        logger.info(f"Scroll speed: {new_display.scroll_speed_pixels_per_second} px/s")
        logger.info(f"Max FPS: {new_display.max_fps}")
        logger.info(f"Loop mode: {new_display.loop_mode}")
        
        # Test display for a few seconds
        start_time = time.time()
        frame_count = 0
        
        logger.info("Starting new implementation test (10 seconds)...")
        
        while time.time() - start_time < 10:
            completed = new_display.display()  # Single method call handles everything!
            frame_count += 1
            
            # Log progress every 2 seconds
            elapsed = time.time() - start_time
            if int(elapsed) % 2 == 0 and elapsed - int(elapsed) < 0.1:
                current_fps = new_display.get_current_fps()
                pos, total = new_display.get_progress()
                logger.info(f"New implementation: {elapsed:.1f}s, FPS: {current_fps:.1f}, Position: {pos:.0f}/{total}px")
        
        total_time = time.time() - start_time
        avg_fps = frame_count / total_time
        
        logger.info(f"New implementation results:")
        logger.info(f"  - Total time: {total_time:.2f}s")
        logger.info(f"  - Total frames: {frame_count}")
        logger.info(f"  - Average FPS: {avg_fps:.1f}")
        logger.info(f"  - Content width: {new_display.total_content_width}px")
        logger.info(f"  - Duration: {new_display.get_duration():.1f}s")
        
        return True
        
    except Exception as e:
        logger.error(f"New implementation failed: {e}", exc_info=True)
        return False

def main():
    """Run comparison tests."""
    logger.info("TextDisplay Comparison Test")
    logger.info("Comparing old TextDisplay vs new TextDisplayScrollManager")
    logger.info("")
    
    # Test old implementation
    old_success = test_old_implementation()
    
    # Wait a moment between tests
    logger.info("")
    logger.info("Waiting 3 seconds before next test...")
    time.sleep(3)
    
    # Test new implementation
    new_success = test_new_implementation()
    
    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("COMPARISON SUMMARY")
    logger.info("=" * 60)
    
    if old_success and new_success:
        logger.info("✅ Both implementations completed successfully!")
        logger.info("")
        logger.info("Key differences:")
        logger.info("  OLD: Manual update() + display() calls")
        logger.info("  NEW: Single display() call handles everything")
        logger.info("")
        logger.info("  OLD: Basic scroll position tracking")
        logger.info("  NEW: Sub-pixel accuracy + time-based animation")
        logger.info("")
        logger.info("  OLD: No FPS monitoring")
        logger.info("  NEW: Built-in FPS monitoring and optimization")
        logger.info("")
        logger.info("  OLD: Simple loop handling")
        logger.info("  NEW: Advanced loop modes + wrap-around")
        logger.info("")
        logger.info("  OLD: No anti-stutter features")
        logger.info("  NEW: Delta time smoothing + clamping")
        logger.info("")
        logger.info("  OLD: No performance optimization")
        logger.info("  NEW: Display update intervals + GC tuning")
    else:
        logger.error("❌ One or both implementations failed!")
        if not old_success:
            logger.error("  - Old TextDisplay failed")
        if not new_success:
            logger.error("  - New TextDisplayScrollManager failed")
    
    logger.info("")
    logger.info("Test complete!")

if __name__ == "__main__":
    main()
