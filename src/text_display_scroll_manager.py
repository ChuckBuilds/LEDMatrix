import logging
import time
from PIL import ImageFont, Image, ImageDraw
import freetype
import os

from .display_manager import DisplayManager
from .horizontal_scroll_manager import HorizontalScrollManager

logger = logging.getLogger(__name__)

class TextDisplayScrollManager(HorizontalScrollManager):
    """
    Text display manager using the HorizontalScrollManager base class.
    
    This provides high-performance scrolling with sub-pixel accuracy,
    time-based animation, and all the optimizations from the base class.
    """
    
    def __init__(self, display_manager: DisplayManager, config: dict):
        # Initialize the base class with 'text_display' config key
        super().__init__(config, display_manager, 'text_display')
        
        # Text-specific configuration
        self.text = self.config.get('text', "Hello, World!")
        self.font_path = self.config.get('font_path', "assets/fonts/PressStart2P-Regular.ttf")
        self.font_size = self.config.get('font_size', 8)
        self.text_color = tuple(self.config.get('text_color', [255, 255, 255]))
        self.bg_color = tuple(self.config.get('background_color', [0, 0, 0]))
        
        # Load font
        self.font = self._load_font()
        
        # Enable the manager
        self.is_enabled = True
        
        logger.info(f"[TextDisplayScrollManager] Initialized with text: '{self.text[:30]}...'")
    
    def _get_content_data(self):
        """Return the text content to display."""
        return self.text if self.text else None
    
    def _create_composite_image(self):
        """Create a wide image with the text content for scrolling."""
        if not self.text or not self.font:
            # Create empty image
            self.total_content_width = 0
            return Image.new('RGB', (1, self.display_manager.matrix.height), self.bg_color)
        
        try:
            # Calculate text width
            text_width = self.display_manager.get_text_width(self.text, self.font)
            
            # Add gap width for scrolling (default to display width)
            gap_width = self.config.get('scroll_gap_width', self.display_manager.matrix.width)
            total_width = text_width + gap_width
            
            # Create the composite image
            image = Image.new('RGB', (total_width, self.display_manager.matrix.height), self.bg_color)
            draw = ImageDraw.Draw(image)
            
            # Calculate vertical position for centering
            matrix_height = self.display_manager.matrix.height
            try:
                if isinstance(self.font, freetype.Face):
                    # BDF font
                    text_render_height = self.font.size.height >> 6
                    y_pos = (matrix_height - text_render_height) // 2
                else:
                    # TTF font
                    bbox = draw.textbbox((0, 0), self.text, font=self.font)
                    text_render_height = bbox[3] - bbox[1]
                    y_pos = (matrix_height - text_render_height) // 2 - bbox[1]
            except Exception as e:
                logger.warning(f"[TextDisplayScrollManager] Could not calculate text height: {e}. Using y=0.")
                y_pos = 0
            
            # Draw the text at the beginning of the image
            if isinstance(self.font, freetype.Face):
                # BDF font - use display manager's draw_text method
                # Create a temporary image for BDF drawing
                temp_img = Image.new('RGB', (text_width, matrix_height), self.bg_color)
                temp_draw = ImageDraw.Draw(temp_img)
                self.display_manager.draw_text(
                    text=self.text, x=0, y=y_pos,
                    color=self.text_color, font=self.font,
                    draw=temp_draw, image=temp_img
                )
                # Paste the BDF-rendered text onto our composite image
                image.paste(temp_img, (0, 0))
            else:
                # TTF font - direct drawing
                draw.text((0, y_pos), self.text, font=self.font, fill=self.text_color)
            
            # Set the total content width for the base class
            self.total_content_width = total_width
            
            logger.info(f"[TextDisplayScrollManager] Created composite image: {total_width}x{matrix_height} (text: {text_width}px + gap: {gap_width}px)")
            return image
            
        except Exception as e:
            logger.error(f"[TextDisplayScrollManager] Failed to create composite image: {e}", exc_info=True)
            # Return empty image on error
            self.total_content_width = 0
            return Image.new('RGB', (1, self.display_manager.matrix.height), self.bg_color)
    
    def _display_fallback_message(self):
        """Display a fallback message when no text is available."""
        dm = self.display_manager
        matrix_width = dm.matrix.width
        matrix_height = dm.matrix.height
        
        dm.image = Image.new('RGB', (matrix_width, matrix_height), self.bg_color)
        dm.draw = ImageDraw.Draw(dm.image)
        
        # Draw "No Text" message
        fallback_text = "No Text"
        try:
            if isinstance(self.font, freetype.Face):
                text_height = self.font.size.height >> 6
                y_pos = (matrix_height - text_height) // 2
            else:
                bbox = dm.draw.textbbox((0, 0), fallback_text, font=self.font)
                text_height = bbox[3] - bbox[1]
                y_pos = (matrix_height - text_height) // 2 - bbox[1]
            
            x_pos = (matrix_width - self.display_manager.get_text_width(fallback_text, self.font)) // 2
            
            dm.draw_text(
                text=fallback_text, x=x_pos, y=y_pos,
                color=self.text_color, font=self.font
            )
        except Exception as e:
            logger.warning(f"[TextDisplayScrollManager] Could not draw fallback message: {e}")
            # Draw simple centered text as last resort
            dm.draw.text((10, 10), "No Text", fill=self.text_color)
        
        dm.update_display()
    
    def _load_font(self):
        """Load the specified font file (TTF or BDF)."""
        font_path = self.font_path
        # Resolve relative paths against project root
        if not os.path.isabs(font_path):
            base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            font_path = os.path.join(base_path, font_path)

        logger.info(f"[TextDisplayScrollManager] Loading font: {font_path} at size {self.font_size}")
        
        if not os.path.exists(font_path):
            logger.error(f"[TextDisplayScrollManager] Font file not found: {font_path}. Using default.")
            return self.display_manager.regular_font

        try:
            if font_path.lower().endswith('.ttf'):
                font = ImageFont.truetype(font_path, self.font_size)
                logger.info(f"[TextDisplayScrollManager] Loaded TTF font: {self.font_path}")
                return font
            elif font_path.lower().endswith('.bdf'):
                face = freetype.Face(font_path)
                face.set_pixel_sizes(0, self.font_size) 
                logger.info(f"[TextDisplayScrollManager] Loaded BDF font: {self.font_path}")
                return face 
            else:
                logger.warning(f"[TextDisplayScrollManager] Unsupported font type: {font_path}. Using default.")
                return self.display_manager.regular_font
        except Exception as e:
            logger.error(f"[TextDisplayScrollManager] Failed to load font {font_path}: {e}", exc_info=True)
            return self.display_manager.regular_font
    
    # Public methods for external control
    def set_text(self, new_text: str):
        """Update the text content and invalidate cache."""
        self.text = new_text
        self.invalidate_image_cache()
        logger.info(f"[TextDisplayScrollManager] Text updated to: '{new_text[:30]}...'")
    
    def set_font(self, font_path: str, font_size: int):
        """Update font and invalidate cache."""
        self.font_path = font_path
        self.font_size = font_size
        self.font = self._load_font()
        self.invalidate_image_cache()
        logger.info(f"[TextDisplayScrollManager] Font updated: {font_path} size {font_size}")
    
    def set_color(self, text_color: tuple, bg_color: tuple):
        """Update colors and invalidate cache."""
        self.text_color = text_color
        self.bg_color = bg_color
        self.invalidate_image_cache()
        logger.info(f"[TextDisplayScrollManager] Colors updated: text={text_color}, bg={bg_color}")
    
    def set_scroll_speed(self, speed: float):
        """Update scroll speed (pixels per second)."""
        self.set_scroll_speed(speed)  # Use base class method
        logger.info(f"[TextDisplayScrollManager] Scroll speed updated to {speed} px/s")
    
    def set_scroll_gap_width(self, gap_width: int):
        """Update scroll gap width and invalidate cache."""
        self.config['scroll_gap_width'] = gap_width
        self.invalidate_image_cache()
        logger.info(f"[TextDisplayScrollManager] Scroll gap width updated to {gap_width}px")
    
    def display(self, force_clear=False):
        """
        Display the text with scrolling.
        
        This method is inherited from HorizontalScrollManager and provides:
        - High-performance scrolling with sub-pixel accuracy
        - Time-based animation (independent of frame rate)
        - Anti-stutter optimizations
        - FPS monitoring and logging
        - Automatic loop handling
        """
        return super().display(force_clear)
