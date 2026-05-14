import os
import freetype
from PIL import ImageDraw, ImageFont
import logging
from typing import Dict, Any
from src.display_manager import DisplayManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FontTestManager:
    """Manager for testing fonts with easy BDF/TTF switching."""
    
    def __init__(self, config: Dict[str, Any], display_manager: DisplayManager):
        self.display_manager = display_manager
        self.config = config
        self.logger = logging.getLogger('FontTest')
        
        # FONT CONFIGURATION - EASY SWITCHING
        # Set to 'bdf' or 'ttf' to switch font types
        self.font_type = 'bdf'  # Change this to 'ttf' to use TTF font
        
        # Font configurations
        self.font_configs = {
            'bdf': {
                'path': "assets/fonts/cozette.bdf",
                'display_name': "Cozette BTF",
                'description': "BTF font Test"
            },
            'ttf': {
                'path': "assets/fonts/5by7.regular.ttf",
                'display_name': "5by7 TTF",
                'description': "TTF font test"
            }
        }
        
        # Get current font configuration
        self.current_config = self.font_configs[self.font_type]
        self.font_path = self.current_config['path']
        
        # Verify font exists
        if not os.path.exists(self.font_path):
            self.logger.error(f"Font file not found: {self.font_path}")
            raise FileNotFoundError(f"Font file not found: {self.font_path}")
        
        # Load the font based on type
        if self.font_type == 'bdf':
            self._load_bdf_font()
        else:
            self._load_ttf_font()
        
        self.logger.info(f"Initialized FontTestManager with {self.current_config['description']}")
    
    def _load_bdf_font(self):
        """Load BDF font using freetype."""
        try:
            self.face = freetype.Face(self.font_path)
            self.logger.info(f"Successfully loaded BDF font from {self.font_path}")
        except Exception as e:
            self.logger.error(f"Failed to load BDF font: {e}")
            raise
    
    def _load_ttf_font(self):
        """Load TTF font using PIL."""
        try:
            self.font = ImageFont.truetype(self.font_path, 8)  # Size 8 for 5x7 font
            self.logger.info(f"Successfully loaded TTF font from {self.font_path}")
        except Exception as e:
            self.logger.error(f"Failed to load TTF font: {e}")
            raise
    
    def update(self):
        """No update needed for static display."""
    
    def display(self, force_clear: bool = False):
        """Display the font with sample text."""
        try:
            # Clear the display
            self.display_manager.clear()
            
            # Get display dimensions
            width = self.display_manager.matrix.width
            height = self.display_manager.matrix.height
            
            # Draw font name at the top
            self.display_manager.draw_text(self.current_config['display_name'], y=2, color=(255, 255, 255))
            
            # Draw sample text
            draw = ImageDraw.Draw(self.display_manager.image)
            sample_text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            
            # Calculate starting position
            x = 10  # Start 10 pixels from the left
            y = 10  # Start 10 pixels from the top
            
            # Draw text based on font type
            if self.font_type == 'bdf':
                self._draw_bdf_text(draw, sample_text, x, y)
            else:
                self._draw_ttf_text(draw, sample_text, x, y)
            
            # Update the display once
            self.display_manager.update_display()
            
            # Log that display is complete
            self.logger.info("Font test display complete.")
            
        except Exception as e:
            self.logger.error(f"Error displaying font test: {e}", exc_info=True)
    
    def _draw_bdf_text(self, draw, text, x, y):
        """Draw text using BDF font."""
        for char in text:
            # Load the glyph
            self.face.load_char(char)
            bitmap = self.face.glyph.bitmap
            
            # Draw the glyph
            for i in range(bitmap.rows):
                for j in range(bitmap.width):
                    try:
                        # Get the byte containing the pixel
                        byte_index = i * bitmap.pitch + (j // 8)
                        if byte_index < len(bitmap.buffer):
                            byte = bitmap.buffer[byte_index]
                            # Check if the specific bit is set
                            if byte & (1 << (7 - (j % 8))):
                                draw.point((x + j, y + i), fill=(255, 255, 255))
                    except IndexError:
                        self.logger.warning(f"Index out of range for char '{char}' at position ({i}, {j})")
                        continue
            
            # Move to next character position
            x += self.face.glyph.advance.x >> 6
    
    def _draw_ttf_text(self, draw, text, x, y):
        """Draw text using TTF font."""
        draw.text((x, y), text, font=self.font, fill=(255, 255, 255)) 