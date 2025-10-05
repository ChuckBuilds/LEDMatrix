#!/usr/bin/env python3
"""
Test script for the static image manager.
This script tests the static image manager functionality without requiring the full LED matrix hardware.
"""

import sys
import os
import logging
from PIL import Image

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.static_image_manager import StaticImageManager
from src.display_manager import DisplayManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MockDisplayManager:
    """Mock display manager for testing without hardware."""
    
    def __init__(self):
        self.matrix = type('Matrix', (), {'width': 64, 'height': 32})()
        self.image = Image.new("RGB", (self.matrix.width, self.matrix.height))
        self.draw = None
    
    def clear(self):
        """Clear the display."""
        self.image = Image.new("RGB", (self.matrix.width, self.matrix.height))
        logger.info("Display cleared")
    
    def update_display(self):
        """Update the display (mock)."""
        logger.info("Display updated")

def test_static_image_manager():
    """Test the static image manager functionality."""
    logger.info("Starting static image manager test...")
    
    # Create mock display manager
    display_manager = MockDisplayManager()
    
    # Test configuration
    config = {
        'static_image': {
            'enabled': True,
            'image_path': 'assets/static_images/default.png',
            'display_duration': 10,
            'zoom_scale': 1.0,
            'preserve_aspect_ratio': True,
            'background_color': [0, 0, 0]
        }
    }
    
    try:
        # Initialize the static image manager
        logger.info("Initializing static image manager...")
        manager = StaticImageManager(display_manager, config)
        
        # Test basic functionality
        logger.info(f"Manager enabled: {manager.is_enabled()}")
        logger.info(f"Display duration: {manager.get_display_duration()}")
        
        # Test image loading
        if manager.image_loaded:
            logger.info("Image loaded successfully")
            image_info = manager.get_image_info()
            logger.info(f"Image info: {image_info}")
        else:
            logger.warning("Image not loaded")
        
        # Test display
        logger.info("Testing display...")
        manager.display()
        
        # Test configuration changes
        logger.info("Testing configuration changes...")
        manager.set_zoom_scale(1.5)
        manager.set_display_duration(15)
        manager.set_background_color((255, 0, 0))
        
        # Test with a different image path (if it exists)
        test_image_path = 'assets/static_images/test.png'
        if os.path.exists(test_image_path):
            logger.info(f"Testing with image: {test_image_path}")
            manager.set_image_path(test_image_path)
        
        logger.info("Static image manager test completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        return False

if __name__ == '__main__':
    success = test_static_image_manager()
    sys.exit(0 if success else 1)
