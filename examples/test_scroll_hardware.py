#!/usr/bin/env python3
"""
Hardware Test for HorizontalScrollManager

This script tests the high-performance scrolling on actual LED matrix hardware.
Unlike example_scroll_manager.py (which uses a mock), this connects to real hardware.

Usage:
    sudo python examples/test_scroll_hardware.py

Note: Requires sudo on Raspberry Pi for hardware access
"""

import sys
import os
import time
import logging
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config_manager import ConfigManager
from src.display_manager import DisplayManager
from src.horizontal_scroll_manager import HorizontalScrollManager
from PIL import Image, ImageDraw, ImageFont
from typing import Dict, Any, Optional, List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TestScrollManager(HorizontalScrollManager):
    """
    Simple test scrolling manager for hardware testing.
    
    Displays a series of test messages to verify smooth scrolling
    at high frame rates on the LED matrix.
    """
    
    def __init__(self, config: Dict[str, Any], display_manager):
        """
        Initialize the test scroll manager.
        
        Args:
            config: Configuration dictionary
            display_manager: Real DisplayManager instance
        """
        # Initialize base class - use test_scroll config or create defaults
        super().__init__(config, display_manager, config_key='test_scroll')
        
        # Get test-specific config
        test_config = config.get('test_scroll', {})
        self.is_enabled = True  # Always enabled for testing
        
        # Test messages
        self.messages: List[str] = test_config.get('messages', [
            "HIGH-PERFORMANCE SCROLLING TEST",
            "100-200+ FPS",
            "Time-Based Animation",
            "Sub-Pixel Accuracy",
            "Smooth & Flicker-Free",
            "LED Matrix Capable",
            "Independent Speed Control"
        ])
        
        # Visual settings
        self.text_color = (0, 255, 255)  # Cyan
        self.background_color = (0, 0, 0)  # Black
        self.separator = " ⚡ "
        self.separator_color = (255, 255, 0)  # Yellow
        
        # Load font
        try:
            font_path = 'assets/fonts/PressStart2P-Regular.ttf'
            font_size = 8
            self.font = ImageFont.truetype(font_path, font_size)
            logger.info(f"Loaded font: {font_path} size {font_size}")
        except Exception as e:
            logger.warning(f"Could not load custom font: {e}. Using default.")
            self.font = ImageFont.load_default()
        
        logger.info(f"TestScrollManager initialized with {len(self.messages)} messages")
        logger.info(f"Display size: {display_manager.matrix.width}x{display_manager.matrix.height}")
    
    def _get_content_data(self) -> Optional[List[str]]:
        """Get the content to display."""
        if not self.is_enabled:
            return None
        
        if not self.messages:
            return None
        
        return self.messages
    
    def _create_composite_image(self) -> Optional[Image.Image]:
        """
        Create the wide scrolling image with all test messages.
        
        Returns:
            PIL Image containing all messages, or None on error
        """
        if not self.messages:
            logger.warning("No messages to display")
            return None
        
        try:
            # Get display dimensions
            display_height = self.display_manager.matrix.height
            
            # Create temporary draw context to measure text
            temp_image = Image.new('RGB', (1, 1))
            temp_draw = ImageDraw.Draw(temp_image)
            
            # Calculate width needed for each message + separator
            message_widths = []
            separator_width = temp_draw.textlength(self.separator, font=self.font)
            
            for message in self.messages:
                text_width = temp_draw.textlength(message, font=self.font)
                message_widths.append(text_width)
            
            # Calculate total width
            # Add padding at start and end for smooth looping
            padding = display_height * 2
            total_width = padding
            for width in message_widths:
                total_width += width + separator_width
            total_width += padding
            
            # Create the composite image
            image = Image.new('RGB', (int(total_width), display_height), 
                            self.background_color)
            draw = ImageDraw.Draw(image)
            
            # Draw messages with separators
            x = padding
            for i, message in enumerate(self.messages):
                # Calculate vertical centering
                text_bbox = draw.textbbox((0, 0), message, font=self.font)
                text_height = text_bbox[3] - text_bbox[1]
                y = (display_height - text_height) // 2
                
                # Draw message
                draw.text((x, y), message, fill=self.text_color, font=self.font)
                x += message_widths[i]
                
                # Draw separator (except after last message)
                if i < len(self.messages) - 1:
                    sep_y = (display_height - text_height) // 2
                    draw.text((x, sep_y), self.separator, 
                            fill=self.separator_color, font=self.font)
                    x += separator_width
            
            # Store the content width
            self.total_content_width = int(total_width)
            
            logger.info(f"Created composite image: {self.total_content_width}x{display_height}px")
            logger.info(f"  - Messages: {len(self.messages)}")
            logger.info(f"  - Avg message width: {sum(message_widths) / len(message_widths):.1f}px")
            
            return image
            
        except Exception as e:
            logger.error(f"Error creating composite image: {e}", exc_info=True)
            return None
    
    def _display_fallback_message(self) -> None:
        """Display a fallback message when no content is available."""
        try:
            width = self.display_manager.matrix.width
            height = self.display_manager.matrix.height
            
            # Create simple fallback image
            image = Image.new('RGB', (width, height), (0, 0, 0))
            draw = ImageDraw.Draw(image)
            
            # Draw "No messages" text
            message = "No messages"
            text_bbox = draw.textbbox((0, 0), message, font=self.font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            x = (width - text_width) // 2
            y = (height - text_height) // 2
            
            draw.text((x, y), message, fill=(255, 0, 0), font=self.font)
            
            # Update display
            self.display_manager.image = image
            self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
            self.display_manager.update_display()
            
            logger.debug("Displayed fallback message")
            
        except Exception as e:
            logger.error(f"Error displaying fallback message: {e}", exc_info=True)


def main():
    """
    Main function to test horizontal scrolling on actual hardware.
    """
    print("=" * 70)
    print("High-Performance Horizontal Scrolling - Hardware Test")
    print("=" * 70)
    print()
    print("This test demonstrates smooth scrolling at 100-200+ fps on your")
    print("LED matrix. You should see text scrolling smoothly with no flicker.")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 70)
    print()
    
    try:
        # Load configuration
        logger.info("Loading configuration...")
        config_manager = ConfigManager()
        config = config_manager.load_config()
        
        # Add test scroll configuration if not present
        if 'test_scroll' not in config:
            config['test_scroll'] = {
                'enabled': True,
                'scroll_speed': 50.0,  # pixels per second
                'max_fps': 150.0,  # target 150 fps for smooth motion
                'target_fps': 100.0,
                'enable_throttling': True,
                'loop_mode': 'continuous',
                'enable_wrap_around': True,
                'dynamic_duration': True,
                'min_duration': 30,
                'max_duration': 300,
                'duration_buffer': 0.1,
                'enable_fps_logging': True,
                'fps_log_interval': 10.0
            }
        
        # Initialize DisplayManager with real hardware
        logger.info("Initializing display manager (this may take a moment)...")
        display_manager = DisplayManager(config)
        
        logger.info(f"Display initialized: {display_manager.matrix.width}x{display_manager.matrix.height}")
        print(f"✓ Display ready: {display_manager.matrix.width}x{display_manager.matrix.height} LED matrix")
        print()
        
        # Create scroll manager
        logger.info("Creating scroll manager...")
        scroll_manager = TestScrollManager(config, display_manager)
        
        print("Configuration:")
        print(f"  - Scroll speed: {scroll_manager.scroll_speed_pixels_per_second} px/s")
        print(f"  - Target FPS: {scroll_manager.target_fps}")
        print(f"  - Max FPS: {scroll_manager.max_fps}")
        print(f"  - Loop mode: {scroll_manager.loop_mode}")
        print(f"  - Messages: {len(scroll_manager.messages)}")
        print()
        
        print("Starting scrolling test...")
        print("Watch your LED matrix for smooth, flicker-free text scrolling.")
        print()
        
        # Run test for 60 seconds or until interrupted
        start_time = time.time()
        frame_count = 0
        last_status_time = start_time
        
        while True:
            # Call display() method - this handles all scrolling
            completed = scroll_manager.display()
            frame_count += 1
            
            # Print status every 5 seconds
            current_time = time.time()
            if current_time - last_status_time >= 5.0:
                elapsed = current_time - start_time
                avg_fps = frame_count / elapsed
                current_fps = scroll_manager.get_current_fps()
                progress = scroll_manager.get_progress()
                
                print(f"[{elapsed:6.1f}s] "
                      f"FPS: {current_fps:5.1f} (avg: {avg_fps:5.1f}) | "
                      f"Position: {progress[0]:5.0f}/{progress[1]}px | "
                      f"Frames: {frame_count}")
                
                last_status_time = current_time
            
            if completed:
                logger.debug("Scroll cycle completed")
        
    except KeyboardInterrupt:
        print("\n")
        print("=" * 70)
        print("Test stopped by user")
        print("=" * 70)
        
        elapsed = time.time() - start_time
        avg_fps = frame_count / elapsed if elapsed > 0 else 0
        
        print()
        print("Statistics:")
        print(f"  - Total time: {elapsed:.2f}s")
        print(f"  - Total frames: {frame_count}")
        print(f"  - Average FPS: {avg_fps:.1f}")
        print(f"  - Content width: {scroll_manager.total_content_width}px")
        print(f"  - Duration: {scroll_manager.get_duration():.1f}s")
        print()
        print("Test complete!")
        
    except Exception as e:
        logger.error(f"Error during test: {e}", exc_info=True)
        print()
        print(f"ERROR: {e}")
        print()
        print("Make sure you're running with sudo on Raspberry Pi:")
        print("  sudo python examples/test_scroll_hardware.py")
        sys.exit(1)


if __name__ == '__main__':
    main()

