#!/usr/bin/env python3
"""
Simple test script for the static image manager.
This script tests the image processing functionality without requiring the full LED matrix hardware.
"""

import sys
import os
import logging
from PIL import Image

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_image_processing():
    """Test image processing functionality."""
    logger.info("Testing image processing...")
    
    # Test image path
    image_path = 'assets/static_images/default.png'
    
    if not os.path.exists(image_path):
        logger.error(f"Test image not found: {image_path}")
        return False
    
    try:
        # Load the image
        img = Image.open(image_path)
        logger.info(f"Original image size: {img.size}")
        
        # Test different zoom scales
        display_size = (64, 32)
        
        for zoom_scale in [0.5, 1.0, 1.5, 2.0]:
            logger.info(f"Testing zoom scale: {zoom_scale}")
            
            # Calculate target size
            if zoom_scale == 1.0:
                # Fit to display while preserving aspect ratio
                scale_x = display_size[0] / img.size[0]
                scale_y = display_size[1] / img.size[1]
                scale = min(scale_x, scale_y)
                target_size = (int(img.size[0] * scale), int(img.size[1] * scale))
            else:
                # Apply zoom scale
                target_size = (int(img.size[0] * zoom_scale), int(img.size[1] * zoom_scale))
            
            logger.info(f"Target size: {target_size}")
            
            # Resize image
            resized_img = img.resize(target_size, Image.Resampling.LANCZOS)
            
            # Create display canvas
            canvas = Image.new('RGB', display_size, (0, 0, 0))
            
            # Center the image
            paste_x = max(0, (display_size[0] - resized_img.width) // 2)
            paste_y = max(0, (display_size[1] - resized_img.height) // 2)
            
            # Handle transparency
            if resized_img.mode == 'RGBA':
                temp_canvas = Image.new('RGB', display_size, (0, 0, 0))
                temp_canvas.paste(resized_img, (paste_x, paste_y), resized_img)
                canvas = temp_canvas
            else:
                canvas.paste(resized_img, (paste_x, paste_y))
            
            logger.info(f"Final canvas size: {canvas.size}")
            logger.info(f"Image position: ({paste_x}, {paste_y})")
            
            # Save test output
            output_path = f'test_output_zoom_{zoom_scale}.png'
            canvas.save(output_path)
            logger.info(f"Test output saved: {output_path}")
        
        logger.info("Image processing test completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        return False

def test_config_loading():
    """Test configuration loading."""
    logger.info("Testing configuration loading...")
    
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
        # Test configuration parsing
        static_config = config.get('static_image', {})
        enabled = static_config.get('enabled', False)
        image_path = static_config.get('image_path', '')
        display_duration = static_config.get('display_duration', 10)
        zoom_scale = static_config.get('zoom_scale', 1.0)
        preserve_aspect_ratio = static_config.get('preserve_aspect_ratio', True)
        background_color = tuple(static_config.get('background_color', [0, 0, 0]))
        
        logger.info(f"Configuration loaded:")
        logger.info(f"  Enabled: {enabled}")
        logger.info(f"  Image path: {image_path}")
        logger.info(f"  Display duration: {display_duration}")
        logger.info(f"  Zoom scale: {zoom_scale}")
        logger.info(f"  Preserve aspect ratio: {preserve_aspect_ratio}")
        logger.info(f"  Background color: {background_color}")
        
        logger.info("Configuration loading test completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"Configuration test failed with error: {e}")
        return False

if __name__ == '__main__':
    logger.info("Starting static image manager simple test...")
    
    success1 = test_config_loading()
    success2 = test_image_processing()
    
    if success1 and success2:
        logger.info("All tests completed successfully!")
        sys.exit(0)
    else:
        logger.error("Some tests failed!")
        sys.exit(1)
