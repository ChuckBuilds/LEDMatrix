"""
Display Helper

Handles common display operations and layouts for LED matrix displays.
Extracted from LEDMatrix core to provide reusable functionality for plugins.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image, ImageDraw, ImageFont


class DisplayHelper:
    """
    Helper class for common display operations and layouts.
    
    Provides functionality for:
    - Creating base images and overlays
    - Common layout patterns (scorebug, ticker, etc.)
    - Image compositing and manipulation
    - Display dimension utilities
    """
    
    def __init__(self, display_width: int, display_height: int,
                 logger: Optional[logging.Logger] = None):
        """
        Initialize the DisplayHelper.
        
        Args:
            display_width: Width of the LED matrix display
            display_height: Height of the LED matrix display
            logger: Optional logger instance
        """
        self.display_width = display_width
        self.display_height = display_height
        self.logger = logger or logging.getLogger(__name__)
    
    def create_base_image(self, background_color: Tuple[int, int, int] = (0, 0, 0),
                         mode: str = 'RGB') -> Image.Image:
        """
        Create a base image for the display.
        
        Args:
            background_color: Background color (R, G, B)
            mode: Image mode ('RGB', 'RGBA', etc.)
            
        Returns:
            PIL Image object
        """
        return Image.new(mode, (self.display_width, self.display_height), background_color)
    
    def create_overlay(self, background_color: Tuple[int, int, int, int] = (0, 0, 0, 0)) -> Image.Image:
        """
        Create an overlay image for compositing.
        
        Args:
            background_color: Background color with alpha (R, G, B, A)
            
        Returns:
            PIL Image object with alpha channel
        """
        return Image.new('RGBA', (self.display_width, self.display_height), background_color)
    
    def composite_images(self, base_image: Image.Image, overlay_image: Image.Image) -> Image.Image:
        """
        Composite overlay onto base image.
        
        Args:
            base_image: Base image (RGB or RGBA)
            overlay_image: Overlay image (should be RGBA)
            
        Returns:
            Composited image
        """
        if base_image.mode != 'RGBA':
            base_image = base_image.convert('RGBA')
        
        if overlay_image.mode != 'RGBA':
            overlay_image = overlay_image.convert('RGBA')
        
        return Image.alpha_composite(base_image, overlay_image)
    
    def draw_scorebug_layout(self, game_data: Dict[str, Any], 
                            fonts: Dict[str, ImageFont.ImageFont],
                            home_logo: Optional[Image.Image] = None,
                            away_logo: Optional[Image.Image] = None) -> Image.Image:
        """
        Draw a standard scorebug layout for sports games.
        
        Args:
            game_data: Dictionary containing game information
            fonts: Dictionary of loaded fonts
            home_logo: Home team logo (optional)
            away_logo: Away team logo (optional)
            
        Returns:
            PIL Image with scorebug layout
        """
        # Create base image and overlay
        main_img = self.create_base_image()
        overlay = self.create_overlay()
        draw = ImageDraw.Draw(overlay)
        
        # Extract game data
        home_score = str(game_data.get('home_score', '0'))
        away_score = str(game_data.get('away_score', '0'))
        home_abbr = game_data.get('home_abbr', 'HOME')
        away_abbr = game_data.get('away_abbr', 'AWAY')
        status_text = game_data.get('status_text', '')
        period_text = game_data.get('period_text', '')
        clock = game_data.get('clock', '')
        
        # Draw logos if provided
        if home_logo and away_logo:
            self._draw_logos(main_img, home_logo, away_logo)
        
        # Draw status/period text (top center)
        if status_text or period_text:
            status_display = f"{period_text} {status_text}".strip()
            if status_display:
                self._draw_centered_text(draw, status_display, 
                                       fonts.get('time', fonts.get('status')), 
                                       y_position=1)
        
        # Draw clock if available
        if clock:
            self._draw_centered_text(draw, clock, fonts.get('time'), y_position=1)
        
        # Draw scores (center)
        score_text = f"{away_score}-{home_score}"
        self._draw_centered_text(draw, score_text, fonts.get('score'), 
                               y_position=self.display_height // 2 - 3)
        
        # Draw team abbreviations (bottom)
        if away_abbr:
            self._draw_text_with_outline(draw, away_abbr, (0, self.display_height - 12), 
                                       fonts.get('team'))
        if home_abbr:
            text_width = draw.textlength(home_abbr, font=fonts.get('team'))
            self._draw_text_with_outline(draw, home_abbr, 
                                       (self.display_width - text_width, self.display_height - 12), 
                                       fonts.get('team'))
        
        # Composite and return
        final_img = self.composite_images(main_img, overlay)
        return final_img.convert('RGB')
    
    def draw_ticker_layout(self, text: str, font: ImageFont.ImageFont,
                          background_color: Tuple[int, int, int] = (0, 0, 0),
                          text_color: Tuple[int, int, int] = (255, 255, 255),
                          scroll_speed: int = 1) -> Image.Image:
        """
        Draw a ticker/scrolling text layout.
        
        Args:
            text: Text to display
            font: Font to use
            background_color: Background color
            text_color: Text color
            scroll_speed: Pixels to scroll per frame
            
        Returns:
            PIL Image with ticker layout
        """
        img = self.create_base_image(background_color)
        draw = ImageDraw.Draw(img)
        
        # Calculate text position (start off-screen to the right)
        text_width = draw.textlength(text, font=font)
        x_position = self.display_width
        
        # Draw text
        self._draw_text_with_outline(draw, text, (x_position, self.display_height // 2 - 6), 
                                   font, fill=text_color)
        
        return img
    
    def draw_centered_text(self, text: str, font: ImageFont.ImageFont,
                          background_color: Tuple[int, int, int] = (0, 0, 0),
                          text_color: Tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
        """
        Draw centered text on the display.
        
        Args:
            text: Text to display
            font: Font to use
            background_color: Background color
            text_color: Text color
            
        Returns:
            PIL Image with centered text
        """
        img = self.create_base_image(background_color)
        draw = ImageDraw.Draw(img)
        
        # Calculate center position
        text_width = draw.textlength(text, font=font)
        text_height = 12  # Approximate height
        x = (self.display_width - text_width) // 2
        y = (self.display_height - text_height) // 2
        
        # Draw text
        self._draw_text_with_outline(draw, text, (x, y), font, fill=text_color)
        
        return img
    
    def draw_error_message(self, message: str = "Error") -> Image.Image:
        """
        Draw a simple error message.
        
        Args:
            message: Error message to display
            
        Returns:
            PIL Image with error message
        """
        img = self.create_base_image((50, 0, 0))  # Dark red background
        draw = ImageDraw.Draw(img)
        
        # Use default font
        font = ImageFont.load_default()
        
        # Draw centered error message
        self._draw_centered_text(message, font, (50, 0, 0), (255, 255, 255))
        
        return img
    
    def draw_no_data_message(self, message: str = "No Data") -> Image.Image:
        """
        Draw a no data message.
        
        Args:
            message: Message to display
            
        Returns:
            PIL Image with no data message
        """
        img = self.create_base_image((0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        font = ImageFont.load_default()
        self._draw_centered_text(message, font, (0, 0, 0), (150, 150, 150))
        
        return img
    
    def get_display_dimensions(self) -> Tuple[int, int]:
        """
        Get display dimensions.
        
        Returns:
            (width, height) tuple
        """
        return (self.display_width, self.display_height)
    
    def is_portrait(self) -> bool:
        """
        Check if display is in portrait orientation.
        
        Returns:
            True if height > width
        """
        return self.display_height > self.display_width
    
    def is_landscape(self) -> bool:
        """
        Check if display is in landscape orientation.
        
        Returns:
            True if width > height
        """
        return self.display_width > self.display_height
    
    def get_center_position(self) -> Tuple[int, int]:
        """
        Get center position of the display.
        
        Returns:
            (x, y) center position
        """
        return (self.display_width // 2, self.display_height // 2)
    
    def _draw_logos(self, img: Image.Image, home_logo: Image.Image, away_logo: Image.Image) -> None:
        """Draw team logos on the image."""
        center_y = self.display_height // 2
        
        # Home logo (right side)
        if home_logo:
            home_x = self.display_width - home_logo.width + 10
            home_y = center_y - (home_logo.height // 2)
            img.paste(home_logo, (home_x, home_y), home_logo)
        
        # Away logo (left side)
        if away_logo:
            away_x = -10
            away_y = center_y - (away_logo.height // 2)
            img.paste(away_logo, (away_x, away_y), away_logo)
    
    def _draw_centered_text(self, draw: ImageDraw.ImageDraw, text: str, 
                          font: ImageFont.ImageFont, y_position: int) -> None:
        """Draw centered text at specified y position."""
        text_width = draw.textlength(text, font=font)
        x = (self.display_width - text_width) // 2
        self._draw_text_with_outline(draw, text, (x, y_position), font)
    
    def _draw_text_with_outline(self, draw: ImageDraw.ImageDraw, text: str,
                               position: Tuple[int, int], font: ImageFont.ImageFont,
                               fill: Tuple[int, int, int] = (255, 255, 255),
                               outline_color: Tuple[int, int, int] = (0, 0, 0)) -> None:
        """Draw text with outline for better readability."""
        x, y = position
        
        # Draw outline
        for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
        
        # Draw main text
        draw.text((x, y), text, font=font, fill=fill)
