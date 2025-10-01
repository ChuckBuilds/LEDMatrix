#!/usr/bin/env python3
"""
Simple test script for the new TextDisplayScrollManager.

This tests the new HorizontalScrollManager-based text display
with high-performance scrolling features.
"""

import sys
import os
import time
import logging

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.display_manager import DisplayManager
from src.text_display_scroll_manager import TextDisplayScrollManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Test the new TextDisplayScrollManager."""
    logger.info("Testing new TextDisplayScrollManager with HorizontalScrollManager")
    logger.info("=" * 60)
    
    try:
        # Initialize display manager
        logger.info("Initializing DisplayManager...")
        display_manager = DisplayManager()
        
        # Create test configuration
        config = {
            'text_display': {
                'enabled': True,
                'text': 'Hello from the new TextDisplayScrollManager! This uses the HorizontalScrollManager base class for high-performance scrolling with sub-pixel accuracy and time-based animation.',
                'font_path': 'assets/fonts/PressStart2P-Regular.ttf',
                'font_size': 8,
                'text_color': [0, 255, 255],  # Cyan text
                'background_color': [0, 0, 0],  # Black background
                'scroll_gap_width': 64,  # Gap between loops
                
                # HorizontalScrollManager settings
                'scroll_speed': 50.0,  # pixels per second
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
        
        # Create TextDisplayScrollManager
        logger.info("Creating TextDisplayScrollManager...")
        text_display = TextDisplayScrollManager(display_manager, config)
        
        logger.info("TextDisplayScrollManager initialized successfully!")
        logger.info(f"Text: '{text_display.text[:50]}...'")
        logger.info(f"Scroll speed: {text_display.scroll_speed_pixels_per_second} px/s")
        logger.info(f"Max FPS: {text_display.max_fps}")
        logger.info(f"Loop mode: {text_display.loop_mode}")
        logger.info(f"Content width: {text_display.total_content_width}px")
        logger.info(f"Duration: {text_display.get_duration():.1f}s")
        
        # Test scrolling
        logger.info("")
        logger.info("Starting scroll test (30 seconds)...")
        logger.info("Press Ctrl+C to stop early")
        logger.info("")
        
        start_time = time.time()
        frame_count = 0
        
        try:
            while time.time() - start_time < 30:
                # Single method call handles everything!
                completed = text_display.display()
                frame_count += 1
                
                # Log progress every 5 seconds
                elapsed = time.time() - start_time
                if int(elapsed) % 5 == 0 and elapsed - int(elapsed) < 0.1:
                    current_fps = text_display.get_current_fps()
                    pos, total = text_display.get_progress()
                    remaining = text_display.get_remaining_time()
                    logger.info(f"[{elapsed:.0f}s] FPS: {current_fps:.1f} | Position: {pos:.0f}/{total}px ({pos/total*100:.1f}%) | Remaining: {remaining:.1f}s")
                
                # Test dynamic text update (change text after 10 seconds)
                if elapsed > 10 and elapsed < 11:
                    new_text = "Text updated! The HorizontalScrollManager automatically handles content changes with smooth transitions and cache invalidation."
                    text_display.set_text(new_text)
                    logger.info("Text updated dynamically!")
        
        except KeyboardInterrupt:
            logger.info("Test stopped by user")
        
        # Final statistics
        total_time = time.time() - start_time
        avg_fps = frame_count / total_time if total_time > 0 else 0
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("TEST RESULTS")
        logger.info("=" * 60)
        logger.info(f"Total time: {total_time:.2f}s")
        logger.info(f"Total frames: {frame_count}")
        logger.info(f"Average FPS: {avg_fps:.1f}")
        logger.info(f"Content width: {text_display.total_content_width}px")
        logger.info(f"Scroll speed: {text_display.scroll_speed_pixels_per_second} px/s")
        logger.info(f"Duration: {text_display.get_duration():.1f}s")
        logger.info(f"Loop mode: {text_display.loop_mode}")
        
        # Test method calls
        logger.info("")
        logger.info("Testing method calls:")
        logger.info(f"Current FPS: {text_display.get_current_fps():.1f}")
        logger.info(f"Progress: {text_display.get_progress()}")
        logger.info(f"Elapsed time: {text_display.get_elapsed_time():.1f}s")
        logger.info(f"Remaining time: {text_display.get_remaining_time():.1f}s")
        
        logger.info("")
        logger.info("✅ TextDisplayScrollManager test completed successfully!")
        logger.info("The new implementation provides:")
        logger.info("  - High-performance scrolling with sub-pixel accuracy")
        logger.info("  - Time-based animation (independent of frame rate)")
        logger.info("  - Anti-stutter optimizations")
        logger.info("  - FPS monitoring and logging")
        logger.info("  - Automatic loop handling")
        logger.info("  - Dynamic content updates")
        logger.info("  - Performance optimizations for Raspberry Pi")
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        return False
    
    return True

if __name__ == "__main__":
    success = main()
    if success:
        logger.info("Test completed successfully!")
    else:
        logger.error("Test failed!")
        sys.exit(1)
