"""
Example Scroll Manager - Demonstration of HorizontalScrollManager usage

This example shows how to create a simple scrolling manager using the
high-performance HorizontalScrollManager base class.

⚠️  NOTE: This uses a MOCK display manager for testing WITHOUT hardware.
    To test on actual LED matrix hardware, use:
        sudo python examples/test_scroll_hardware.py

Usage:
    python examples/example_scroll_manager.py  (no hardware needed)
"""

import sys
import os
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from PIL import Image, ImageDraw, ImageFont

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.horizontal_scroll_manager import HorizontalScrollManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ExampleScrollManager(HorizontalScrollManager):
    """
    Simple example scrolling manager that displays text messages.
    
    This demonstrates the minimal implementation needed to use the
    HorizontalScrollManager base class.
    """
    
    def __init__(self, config: Dict[str, Any], display_manager):
        """
        Initialize the example scroll manager.
        
        Args:
            config: Configuration dictionary
            display_manager: Display manager instance
        """
        # Initialize base class with config key
        super().__init__(config, display_manager, config_key='example_scroll')
        
        # Get manager-specific config
        self.example_config = config.get('example_scroll', {})
        self.is_enabled = self.example_config.get('enabled', True)
        
        # Content data
        self.messages: List[str] = self.example_config.get('messages', [
            "Welcome to LED Matrix!",
            "High Performance Scrolling",
            "100-200+ FPS",
            "Smooth and Flicker-Free"
        ])
        
        # Visual settings
        self.text_color = self._parse_color(
            self.example_config.get('text_color', '255,255,255')
        )
        self.background_color = self._parse_color(
            self.example_config.get('background_color', '0,0,0')
        )
        self.separator = self.example_config.get('separator', ' • ')
        self.separator_color = self._parse_color(
            self.example_config.get('separator_color', '100,100,100')
        )
        
        # Load font
        try:
            font_path = self.example_config.get('font_path', 'assets/fonts/PressStart2P-Regular.ttf')
            font_size = self.example_config.get('font_size', 10)
            self.font = ImageFont.truetype(font_path, font_size)
            logger.info(f"Loaded font: {font_path} size {font_size}")
        except Exception as e:
            logger.warning(f"Could not load custom font: {e}. Using default.")
            self.font = ImageFont.load_default()
        
        logger.info(f"ExampleScrollManager initialized with {len(self.messages)} messages")
    
    def _parse_color(self, color_str: str) -> tuple:
        """
        Parse color string to RGB tuple.
        
        Args:
            color_str: Color as "R,G,B" string
            
        Returns:
            (R, G, B) tuple
        """
        try:
            parts = [int(x.strip()) for x in color_str.split(',')]
            if len(parts) == 3:
                return tuple(parts)
        except Exception:
            pass
        return (255, 255, 255)  # Default to white
    
    def _get_content_data(self) -> Optional[List[str]]:
        """
        Get the content to display.
        
        Returns:
            List of messages, or None if disabled or no messages
        """
        if not self.is_enabled:
            return None
        
        if not self.messages:
            return None
        
        return self.messages
    
    def _create_composite_image(self) -> Optional[Image.Image]:
        """
        Create the wide scrolling image with all messages.
        
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
    
    def add_message(self, message: str) -> None:
        """
        Add a new message to the scroll.
        
        Args:
            message: Message text to add
        """
        self.messages.append(message)
        self.invalidate_image_cache()  # Force image recreation
        logger.info(f"Added message: {message}")
    
    def clear_messages(self) -> None:
        """Clear all messages."""
        self.messages.clear()
        self.invalidate_image_cache()
        logger.info("Cleared all messages")
    
    def set_messages(self, messages: List[str]) -> None:
        """
        Replace all messages.
        
        Args:
            messages: New list of messages
        """
        self.messages = messages
        self.invalidate_image_cache()
        logger.info(f"Set {len(messages)} new messages")


# ===== Example Usage =====

def main():
    """
    Demonstrate the ExampleScrollManager.
    
    Note: This is a demonstration. On actual hardware, you would use
    the real DisplayManager from your LED matrix setup.
    """
    print("=" * 60)
    print("ExampleScrollManager Demonstration")
    print("=" * 60)
    print()
    print("This example shows how to use the HorizontalScrollManager")
    print("base class. On real hardware, this would display smooth")
    print("scrolling text at 100-200+ fps.")
    print()
    print("Note: This demo uses a mock display manager for testing.")
    print("=" * 60)
    print()
    
    # Create mock display manager for testing
    class MockDisplayManager:
        """Mock display manager for testing without hardware."""
        
        class MockMatrix:
            width = 128
            height = 32
        
        def __init__(self):
            self.matrix = self.MockMatrix()
            self.image = None
            self.draw = None
            self._is_scrolling = False
        
        def set_scrolling_state(self, is_scrolling: bool):
            self._is_scrolling = is_scrolling
        
        def update_display(self):
            # In real implementation, this would update the LED matrix
            pass
    
    # Create configuration
    config = {
        'example_scroll': {
            'enabled': True,
            'messages': [
                'High Performance Scrolling',
                'Time-Based Animation',
                '100-200+ FPS',
                'Smooth & Flicker-Free',
                'Independent Speed Control'
            ],
            'scroll_speed': 50.0,  # pixels per second
            'max_fps': 100.0,
            'loop_mode': 'continuous',
            'enable_wrap_around': True,
            'dynamic_duration': True,
            'enable_fps_logging': True,
            'fps_log_interval': 5.0,
            'text_color': '0,255,255',  # Cyan
            'separator': ' ⚡ ',
            'separator_color': '255,255,0'  # Yellow
        }
    }
    
    # Create display manager and scroll manager
    display_manager = MockDisplayManager()
    scroll_manager = ExampleScrollManager(config, display_manager)
    
    print("Configuration:")
    print(f"  - Scroll speed: {scroll_manager.scroll_speed_pixels_per_second} px/s")
    print(f"  - Target FPS: {scroll_manager.target_fps}")
    print(f"  - Loop mode: {scroll_manager.loop_mode}")
    print(f"  - Messages: {len(scroll_manager.messages)}")
    print()
    
    # Simulate display loop
    print("Starting display simulation (10 seconds)...")
    print("Press Ctrl+C to stop")
    print()
    
    start_time = time.time()
    frame_count = 0
    
    try:
        while time.time() - start_time < 10.0:
            # Call display() method (this handles all scrolling)
            completed = scroll_manager.display()
            
            frame_count += 1
            
            # Show progress every second
            elapsed = time.time() - start_time
            if frame_count % 100 == 0:
                current_fps = scroll_manager.get_current_fps()
                progress = scroll_manager.get_progress()
                print(f"Time: {elapsed:.1f}s | "
                      f"FPS: {current_fps:.1f} | "
                      f"Position: {progress[0]:.0f}/{progress[1]}px | "
                      f"Frames: {frame_count}")
            
            if completed:
                print("  → Scroll cycle completed!")
        
    except KeyboardInterrupt:
        print("\nStopped by user")
    
    # Final statistics
    elapsed = time.time() - start_time
    avg_fps = frame_count / elapsed
    
    print()
    print("=" * 60)
    print("Simulation Complete")
    print("=" * 60)
    print(f"  - Total time: {elapsed:.2f}s")
    print(f"  - Total frames: {frame_count}")
    print(f"  - Average FPS: {avg_fps:.1f}")
    print(f"  - Content width: {scroll_manager.total_content_width}px")
    print(f"  - Duration: {scroll_manager.get_duration():.1f}s")
    print()
    print("On real hardware with LED matrix, this would display")
    print("smooth, flicker-free scrolling text at 100-200+ fps!")
    print()


if __name__ == '__main__':
    main()

