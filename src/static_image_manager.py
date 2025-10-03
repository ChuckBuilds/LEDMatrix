import logging
import os
import time
from typing import Optional, Tuple
from PIL import Image, ImageOps
import json

from .display_manager import DisplayManager

logger = logging.getLogger(__name__)

class StaticImageManager:
    """
    Manager for displaying static images on the LED matrix.
    Supports image scaling, transparency, and configurable display duration.
    """
    
    def __init__(self, display_manager: DisplayManager, config: dict):
        self.display_manager = display_manager
        self.config = config.get('static_image', {})
        
        # Configuration
        self.enabled = self.config.get('enabled', False)
        self.image_path = self.config.get('image_path', '')
        self.display_duration = self.config.get('display_duration', 10)  # seconds
        self.zoom_scale = self.config.get('zoom_scale', 1.0)  # 1.0 = fit to display
        self.preserve_aspect_ratio = self.config.get('preserve_aspect_ratio', True)
        self.background_color = tuple(self.config.get('background_color', [0, 0, 0]))
        
        # State
        self.current_image = None
        self.image_loaded = False
        self.last_update_time = 0
        
        # Load initial image if enabled
        if self.enabled and self.image_path:
            self._load_image()
    
    def _load_image(self) -> bool:
        """
        Load and process the image for display.
        Returns True if successful, False otherwise.
        """
        if not self.image_path or not os.path.exists(self.image_path):
            logger.warning(f"[Static Image] Image file not found: {self.image_path}")
            return False
        
        try:
            # Load the image
            img = Image.open(self.image_path)
            
            # Convert to RGBA to handle transparency
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            # Get display dimensions
            display_width = self.display_manager.matrix.width
            display_height = self.display_manager.matrix.height
            
            # Calculate target size based on zoom scale
            if self.zoom_scale == 1.0:
                # Fit to display while preserving aspect ratio
                target_size = self._calculate_fit_size(img.size, (display_width, display_height))
            else:
                # Apply zoom scale
                original_size = img.size
                scaled_width = int(original_size[0] * self.zoom_scale)
                scaled_height = int(original_size[1] * self.zoom_scale)
                target_size = (scaled_width, scaled_height)
            
            # Resize image
            if self.preserve_aspect_ratio:
                img = img.resize(target_size, Image.Resampling.LANCZOS)
            else:
                img = img.resize((display_width, display_height), Image.Resampling.LANCZOS)
            
            # Create display-sized canvas with background color
            canvas = Image.new('RGB', (display_width, display_height), self.background_color)
            
            # Calculate position to center the image
            if self.zoom_scale == 1.0 or self.preserve_aspect_ratio:
                # Center the image
                paste_x = (display_width - img.width) // 2
                paste_y = (display_height - img.height) // 2
            else:
                # For non-aspect-ratio preserving zoom, center the scaled image
                paste_x = max(0, (display_width - img.width) // 2)
                paste_y = max(0, (display_height - img.height) // 2)
            
            # Handle transparency by compositing
            if img.mode == 'RGBA':
                # Create a temporary image with the background color
                temp_canvas = Image.new('RGB', (display_width, display_height), self.background_color)
                temp_canvas.paste(img, (paste_x, paste_y), img)
                canvas = temp_canvas
            else:
                canvas.paste(img, (paste_x, paste_y))
            
            self.current_image = canvas
            self.image_loaded = True
            self.last_update_time = time.time()
            
            logger.info(f"[Static Image] Successfully loaded and processed image: {self.image_path}")
            logger.info(f"[Static Image] Original size: {Image.open(self.image_path).size}, "
                       f"Display size: {target_size}, Position: ({paste_x}, {paste_y})")
            
            return True
            
        except Exception as e:
            logger.error(f"[Static Image] Error loading image {self.image_path}: {e}")
            self.image_loaded = False
            return False
    
    def _calculate_fit_size(self, image_size: Tuple[int, int], display_size: Tuple[int, int]) -> Tuple[int, int]:
        """
        Calculate the size to fit an image within display bounds while preserving aspect ratio.
        """
        img_width, img_height = image_size
        display_width, display_height = display_size
        
        # Calculate scaling factor to fit within display
        scale_x = display_width / img_width
        scale_y = display_height / img_height
        scale = min(scale_x, scale_y)
        
        return (int(img_width * scale), int(img_height * scale))
    
    def update(self):
        """
        Update method - no continuous updates needed for static images.
        """
        pass
    
    def display(self, force_clear: bool = False):
        """
        Display the static image on the LED matrix.
        """
        if not self.enabled or not self.image_loaded or not self.current_image:
            if self.enabled:
                logger.warning("[Static Image] Manager enabled but no image loaded")
            return
        
        # Clear display if requested
        if force_clear:
            self.display_manager.clear()
        
        # Set the image on the display manager
        self.display_manager.image = self.current_image.copy()
        
        # Update the display
        self.display_manager.update_display()
        
        logger.debug(f"[Static Image] Displayed image: {self.image_path}")
    
    def set_image_path(self, image_path: str) -> bool:
        """
        Set a new image path and load it.
        Returns True if successful, False otherwise.
        """
        self.image_path = image_path
        return self._load_image()
    
    def set_zoom_scale(self, zoom_scale: float):
        """
        Set the zoom scale and reload the image.
        """
        self.zoom_scale = max(0.1, min(5.0, zoom_scale))  # Clamp between 0.1 and 5.0
        if self.image_path:
            self._load_image()
        logger.info(f"[Static Image] Zoom scale set to: {self.zoom_scale}")
    
    def set_display_duration(self, duration: int):
        """
        Set the display duration in seconds.
        """
        self.display_duration = max(1, duration)  # Minimum 1 second
        logger.info(f"[Static Image] Display duration set to: {self.display_duration} seconds")
    
    def set_background_color(self, color: Tuple[int, int, int]):
        """
        Set the background color and reload the image.
        """
        self.background_color = color
        if self.image_path:
            self._load_image()
        logger.info(f"[Static Image] Background color set to: {self.background_color}")
    
    def get_image_info(self) -> dict:
        """
        Get information about the currently loaded image.
        """
        if not self.image_loaded or not self.current_image:
            return {"loaded": False}
        
        return {
            "loaded": True,
            "path": self.image_path,
            "display_size": self.current_image.size,
            "zoom_scale": self.zoom_scale,
            "display_duration": self.display_duration,
            "background_color": self.background_color
        }
    
    def reload_image(self) -> bool:
        """
        Reload the current image.
        """
        if not self.image_path:
            logger.warning("[Static Image] No image path set for reload")
            return False
        
        return self._load_image()
    
    def is_enabled(self) -> bool:
        """
        Check if the manager is enabled.
        """
        return self.enabled
    
    def get_display_duration(self) -> int:
        """
        Get the display duration in seconds.
        """
        return self.display_duration
