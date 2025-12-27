"""
Text Helper

Handles text rendering with outlines, fonts, and positioning for LED matrix displays.
Extracted from LEDMatrix core to provide reusable functionality for plugins.
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from PIL import Image, ImageDraw, ImageFont


class TextHelper:
    """
    Helper class for text rendering with outlines and font management.
    
    Provides functionality for:
    - Loading and managing fonts
    - Drawing text with outlines for better readability
    - Calculating text dimensions and positioning
    - Managing font resources
    """
    
    def __init__(self, font_dir: Optional[Union[str, Path]] = None, 
                 logger: Optional[logging.Logger] = None):
        """
        Initialize the TextHelper.
        
        Args:
            font_dir: Directory containing font files (defaults to assets/fonts)
            logger: Optional logger instance
        """
        self.logger = logger or logging.getLogger(__name__)
        self.font_dir = Path(font_dir) if font_dir else Path("assets/fonts")
        self._font_cache: Dict[str, ImageFont.ImageFont] = {}
    
    def load_fonts(self, font_config: Optional[Dict[str, Dict]] = None) -> Dict[str, ImageFont.ImageFont]:
        """
        Load fonts for different text elements.
        
        Args:
            font_config: Custom font configuration dictionary
            
        Returns:
            Dictionary mapping font names to PIL ImageFont objects
        """
        if font_config is None:
            font_config = self._get_default_font_config()
        
        fonts = {}
        
        for font_name, config in font_config.items():
            try:
                font_path = self.font_dir / config['file']
                size = config['size']
                
                if font_path.exists():
                    font = ImageFont.truetype(str(font_path), size)
                    fonts[font_name] = font
                    self.logger.debug(f"Loaded font: {font_name} ({font_path}, size {size})")
                else:
                    # Fallback to default font
                    font = ImageFont.load_default()
                    fonts[font_name] = font
                    self.logger.warning(f"Font file not found: {font_path}, using default")
                    
            except Exception as e:
                self.logger.error(f"Error loading font {font_name}: {e}")
                fonts[font_name] = ImageFont.load_default()
        
        return fonts
    
    def draw_text_with_outline(self, draw: ImageDraw.ImageDraw, text: str, 
                              position: Tuple[int, int], font: ImageFont.ImageFont,
                              fill: Tuple[int, int, int] = (255, 255, 255),
                              outline_color: Tuple[int, int, int] = (0, 0, 0),
                              outline_width: int = 1) -> None:
        """
        Draw text with an outline for better readability on LED displays.
        
        Args:
            draw: PIL ImageDraw object
            text: Text to draw
            position: (x, y) position tuple
            font: PIL ImageFont object
            fill: Text color (R, G, B)
            outline_color: Outline color (R, G, B)
            outline_width: Width of outline in pixels
        """
        x, y = position
        
        # Draw outline by drawing text in outline color at offset positions
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx != 0 or dy != 0:  # Skip center position
                    draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        
        # Draw main text
        draw.text((x, y), text, font=font, fill=fill)
    
    def get_text_width(self, text: str, font: ImageFont.ImageFont) -> int:
        """
        Get the width of text when rendered with the given font.
        
        Args:
            text: Text to measure
            font: PIL ImageFont object
            
        Returns:
            Width in pixels
        """
        try:
            return draw.textlength(text, font=font)
        except AttributeError:
            # Fallback for older PIL versions
            bbox = draw.textbbox((0, 0), text, font=font)
            return bbox[2] - bbox[0]
    
    def get_text_height(self, text: str, font: ImageFont.ImageFont) -> int:
        """
        Get the height of text when rendered with the given font.
        
        Args:
            text: Text to measure
            font: PIL ImageFont object
            
        Returns:
            Height in pixels
        """
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            return bbox[3] - bbox[1]
        except AttributeError:
            # Fallback for older PIL versions
            bbox = draw.textbbox((0, 0), text, font=font)
            return bbox[3] - bbox[1]
    
    def get_text_dimensions(self, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
        """
        Get both width and height of text.
        
        Args:
            text: Text to measure
            font: PIL ImageFont object
            
        Returns:
            (width, height) tuple
        """
        return (self.get_text_width(text, font), self.get_text_height(text, font))
    
    def center_text(self, text: str, font: ImageFont.ImageFont, 
                   container_width: int, container_height: int) -> Tuple[int, int]:
        """
        Calculate position to center text within a container.
        
        Args:
            text: Text to center
            font: PIL ImageFont object
            container_width: Width of container
            container_height: Height of container
            
        Returns:
            (x, y) position tuple for centered text
        """
        text_width, text_height = self.get_text_dimensions(text, font)
        x = (container_width - text_width) // 2
        y = (container_height - text_height) // 2
        return (x, y)
    
    def wrap_text(self, text: str, font: ImageFont.ImageFont, 
                  max_width: int, max_lines: Optional[int] = None) -> List[str]:
        """
        Wrap text to fit within specified width.
        
        Args:
            text: Text to wrap
            font: PIL ImageFont object
            max_width: Maximum width in pixels
            max_lines: Maximum number of lines (None for unlimited)
            
        Returns:
            List of text lines
        """
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            # Test if adding this word would exceed width
            test_line = ' '.join(current_line + [word])
            if self.get_text_width(test_line, font) <= max_width:
                current_line.append(word)
            else:
                # Start new line
                if current_line:
                    lines.append(' '.join(current_line))
                    current_line = [word]
                else:
                    # Single word is too long, add it anyway
                    lines.append(word)
        
        # Add remaining words
        if current_line:
            lines.append(' '.join(current_line))
        
        # Limit lines if specified
        if max_lines is not None:
            lines = lines[:max_lines]
        
        return lines
    
    def draw_multiline_text(self, draw: ImageDraw.ImageDraw, text: str,
                           position: Tuple[int, int], font: ImageFont.ImageFont,
                           line_spacing: int = 2, **kwargs) -> None:
        """
        Draw multiline text with proper spacing.
        
        Args:
            draw: PIL ImageDraw object
            text: Text to draw (can contain newlines)
            position: Starting (x, y) position
            font: PIL ImageFont object
            line_spacing: Pixels between lines
            **kwargs: Additional arguments for draw_text_with_outline
        """
        x, y = position
        lines = text.split('\n')
        
        for line in lines:
            if line.strip():  # Skip empty lines
                self.draw_text_with_outline(draw, line, (x, y), font, **kwargs)
            y += self.get_text_height(line, font) + line_spacing
    
    def create_text_image(self, text: str, font: ImageFont.ImageFont,
                         background_color: Tuple[int, int, int] = (0, 0, 0),
                         text_color: Tuple[int, int, int] = (255, 255, 255),
                         padding: int = 5) -> Image.Image:
        """
        Create an image containing only the specified text.
        
        Args:
            text: Text to render
            font: PIL ImageFont object
            background_color: Background color (R, G, B)
            text_color: Text color (R, G, B)
            padding: Padding around text in pixels
            
        Returns:
            PIL Image containing the text
        """
        # Calculate dimensions
        text_width, text_height = self.get_text_dimensions(text, font)
        img_width = text_width + (padding * 2)
        img_height = text_height + (padding * 2)
        
        # Create image
        img = Image.new('RGB', (img_width, img_height), background_color)
        draw = ImageDraw.Draw(img)
        
        # Draw text
        self.draw_text_with_outline(draw, text, (padding, padding), font, 
                                   fill=text_color)
        
        return img
    
    def _get_default_font_config(self) -> Dict[str, Dict]:
        """Get default font configuration."""
        return {
            'score': {
                'file': 'PressStart2P-Regular.ttf',
                'size': 10
            },
            'time': {
                'file': 'PressStart2P-Regular.ttf', 
                'size': 8
            },
            'team': {
                'file': 'PressStart2P-Regular.ttf',
                'size': 8
            },
            'status': {
                'file': '4x6-font.ttf',
                'size': 6
            },
            'detail': {
                'file': '4x6-font.ttf',
                'size': 6
            },
            'rank': {
                'file': 'PressStart2P-Regular.ttf',
                'size': 10
            }
        }
    
    def clear_font_cache(self) -> None:
        """Clear the font cache."""
        self._font_cache.clear()
        self.logger.debug("Font cache cleared")
    
    def get_font_cache_stats(self) -> Dict[str, int]:
        """
        Get font cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        return {
            'cached_fonts': len(self._font_cache)
        }
