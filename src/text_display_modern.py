"""
Modern text display implementation using the new BaseScrollController.

This serves as an example of how to migrate existing display managers to use
the standardized scroll base class.
"""

import logging
import time
from PIL import ImageFont, Image, ImageDraw
import freetype
import os
from .display_manager import DisplayManager
from .base_classes.scroll_mixin import ScrollMixin, LegacyScrollAdapter

logger = logging.getLogger(__name__)


class ModernTextDisplay(ScrollMixin):
    """
    Modern text display implementation using standardized scrolling.
    
    This demonstrates how to use the ScrollMixin to add advanced scrolling
    capabilities to any display manager with minimal code changes.
    """
    
    def __init__(self, display_manager: DisplayManager, config: dict):
        self.display_manager = display_manager
        self.config = config.get('text_display', {})
        
        # Text configuration
        self.text = self.config.get('text', "Hello, World!")
        self.font_path = self.config.get('font_path', "assets/fonts/PressStart2P-Regular.ttf")
        self.font_size = self.config.get('font_size', 8)
        self.text_color = tuple(self.config.get('text_color', [255, 255, 255]))
        self.bg_color = tuple(self.config.get('background_color', [0, 0, 0]))
        
        # Load font
        self.font = self._load_font()
        
        # Calculate initial text dimensions
        self.text_content_width = 0
        self.text_content_height = 0
        self._calculate_text_dimensions()
        
        # Initialize scroll controller using mixin
        # Convert legacy config if needed
        scroll_config = LegacyScrollAdapter.convert_legacy_config(self.config)
        self.config.update(scroll_config)
        
        self.init_scroll_controller(
            debug_name="ModernTextDisplay",
            config_section='text_display',
            content_width=self.text_content_width,
            content_height=self.text_content_height
        )
        
        # Create cached text image
        self.text_image_cache = None
        self._create_text_image_cache()
        
        logger.info(f"ModernTextDisplay initialized: '{self.text[:30]}...' "
                   f"({self.text_content_width}x{self.text_content_height}px)")
    
    def _load_font(self):
        """Load the specified font file (TTF or BDF)."""
        font_path = self.font_path
        
        # Resolve relative paths
        if not os.path.isabs(font_path):
            base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            font_path = os.path.join(base_path, font_path)
        
        logger.debug(f"Loading font: {font_path} at size {self.font_size}")
        
        if not os.path.exists(font_path):
            logger.error(f"Font file not found: {font_path}. Using default.")
            return self.display_manager.regular_font
        
        try:
            if font_path.lower().endswith('.ttf'):
                font = ImageFont.truetype(font_path, self.font_size)
                logger.debug(f"Loaded TTF font: {self.font_path}")
                return font
            elif font_path.lower().endswith('.bdf'):
                face = freetype.Face(font_path)
                face.set_pixel_sizes(0, self.font_size)
                logger.debug(f"Loaded BDF font: {self.font_path}")
                return face
            else:
                logger.warning(f"Unsupported font type: {font_path}. Using default.")
                return self.display_manager.regular_font
        except Exception as e:
            logger.error(f"Failed to load font {font_path}: {e}")
            return self.display_manager.regular_font
    
    def _calculate_text_dimensions(self):
        """Calculate the pixel dimensions of the text."""
        if not self.text or not self.font:
            self.text_content_width = 0
            self.text_content_height = 0
            return
        
        try:
            self.text_content_width = self.display_manager.get_text_width(self.text, self.font)
            
            # Calculate height based on font type
            if isinstance(self.font, freetype.Face):
                self.text_content_height = self.font.size.height >> 6
            else:
                # For PIL fonts, use textbbox
                dummy_img = Image.new('RGB', (1, 1))
                dummy_draw = ImageDraw.Draw(dummy_img)
                bbox = dummy_draw.textbbox((0, 0), self.text, font=self.font)
                self.text_content_height = bbox[3] - bbox[1]
                
        except Exception as e:
            logger.error(f"Error calculating text dimensions: {e}")
            self.text_content_width = 0
            self.text_content_height = 0
    
    def _create_text_image_cache(self):
        """Pre-render the text with gap for smooth scrolling."""
        self.text_image_cache = None
        
        if not self.text or not self.font or self.text_content_width == 0:
            return
        
        # For BDF fonts, we'll use direct drawing instead of caching
        if isinstance(self.font, freetype.Face):
            logger.debug("Using direct drawing for BDF font - no cache created")
            return
        
        try:
            # Create cache with text + gap for smooth looping
            gap_width = self.config.get('scroll_loop_gap_pixels', 
                                      self.display_manager.matrix.width // 2)
            total_cache_width = self.text_content_width + gap_width
            cache_height = self.display_manager.matrix.height
            
            # Create the cache image
            self.text_image_cache = Image.new('RGB', (total_cache_width, cache_height), self.bg_color)
            draw_cache = ImageDraw.Draw(self.text_image_cache)
            
            # Calculate vertical centering
            dummy_img = Image.new('RGB', (1, 1))
            dummy_draw = ImageDraw.Draw(dummy_img)
            bbox = dummy_draw.textbbox((0, 0), self.text, font=self.font)
            text_height = bbox[3] - bbox[1]
            y_pos = (cache_height - text_height) // 2 - bbox[1]
            
            # Draw the text
            draw_cache.text((0, y_pos), self.text, font=self.font, fill=self.text_color)
            
            # Update scroll controller with new cache dimensions
            self.update_scroll_content_size(total_cache_width, cache_height)
            
            logger.debug(f"Created text cache: {total_cache_width}x{cache_height}px "
                        f"(text: {self.text_content_width}px, gap: {gap_width}px)")
            
        except Exception as e:
            logger.error(f"Failed to create text image cache: {e}")
            self.text_image_cache = None
    
    def set_text(self, new_text: str):
        """Update the displayed text."""
        if new_text != self.text:
            self.text = new_text
            self._calculate_text_dimensions()
            self._create_text_image_cache()
            self.reset_scroll_position()
            logger.debug(f"Text updated to: '{new_text[:30]}...'")
    
    def set_font(self, font_path: str, font_size: int):
        """Update the font."""
        self.font_path = font_path
        self.font_size = font_size
        self.font = self._load_font()
        self._calculate_text_dimensions()
        self._create_text_image_cache()
        self.reset_scroll_position()
    
    def set_colors(self, text_color: tuple, bg_color: tuple):
        """Update text and background colors."""
        self.text_color = text_color
        self.bg_color = bg_color
        self._create_text_image_cache()  # Recreate cache with new colors
    
    def update(self):
        """Update scroll position - called by the display loop."""
        return self.update_scroll()
    
    def display(self, force_clear: bool = False):
        """Render the text to the display."""
        # Clear display if requested
        if force_clear:
            self.display_manager.clear()
            self.reset_scroll_position()
        
        # Update scroll position
        scroll_state = self.update_scroll()
        
        # Create display image
        matrix_width = self.display_manager.matrix.width
        matrix_height = self.display_manager.matrix.height
        self.display_manager.image = Image.new('RGB', (matrix_width, matrix_height), self.bg_color)
        self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
        
        if not self.text or self.text_content_width == 0:
            self.display_manager.update_display()
            return
        
        # Render based on scrolling state and available cache
        if scroll_state['is_scrolling'] and self.text_image_cache:
            # Use cached image with scrolling
            cropped_image = self.crop_scrolled_image(self.text_image_cache, wrap_around=True)
            self.display_manager.image.paste(cropped_image, (0, 0))
            
        elif scroll_state['is_scrolling'] and not self.text_image_cache:
            # Direct drawing with scrolling (for BDF fonts)
            self._draw_scrolled_text_direct(scroll_state['scroll_position'])
            
        else:
            # Static text (centered)
            self._draw_static_text()
        
        # Update the display
        self.display_manager.update_display()
        
        # Log performance metrics if enabled
        if scroll_state.get('fps', 0) > 0:
            self.log_scroll_performance()
        
        # Calculate and apply frame delay for target FPS
        frame_delay = self.calculate_scroll_frame_delay()
        if frame_delay > 0:
            time.sleep(frame_delay)
    
    def _draw_scrolled_text_direct(self, scroll_position: float):
        """Draw scrolling text directly (used for BDF fonts)."""
        matrix_width = self.display_manager.matrix.width
        matrix_height = self.display_manager.matrix.height
        
        # Calculate text position
        x_pos = matrix_width - int(scroll_position)
        
        # Calculate vertical centering
        if isinstance(self.font, freetype.Face):
            text_height = self.font.size.height >> 6
            y_pos = (matrix_height - text_height) // 2
        else:
            dummy_img = Image.new('RGB', (1, 1))
            dummy_draw = ImageDraw.Draw(dummy_img)
            bbox = dummy_draw.textbbox((0, 0), self.text, font=self.font)
            text_height = bbox[3] - bbox[1]
            y_pos = (matrix_height - text_height) // 2 - bbox[1]
        
        # Draw the text
        self.display_manager.draw_text(
            text=self.text,
            x=x_pos,
            y=y_pos,
            color=self.text_color,
            font=self.font
        )
    
    def _draw_static_text(self):
        """Draw centered static text."""
        matrix_width = self.display_manager.matrix.width
        matrix_height = self.display_manager.matrix.height
        
        # Center horizontally
        x_pos = (matrix_width - self.text_content_width) // 2
        
        # Center vertically
        if isinstance(self.font, freetype.Face):
            text_height = self.font.size.height >> 6
            y_pos = (matrix_height - text_height) // 2
        else:
            dummy_img = Image.new('RGB', (1, 1))
            dummy_draw = ImageDraw.Draw(dummy_img)
            bbox = dummy_draw.textbbox((0, 0), self.text, font=self.font)
            text_height = bbox[3] - bbox[1]
            y_pos = (matrix_height - text_height) // 2 - bbox[1]
        
        # Draw the text
        self.display_manager.draw_text(
            text=self.text,
            x=x_pos,
            y=y_pos,
            color=self.text_color,
            font=self.font
        )
    
    def get_debug_info(self) -> dict:
        """Get comprehensive debug information."""
        base_info = self.get_scroll_debug_info()
        base_info.update({
            'text': self.text[:50] + ('...' if len(self.text) > 50 else ''),
            'font_path': self.font_path,
            'font_size': self.font_size,
            'text_color': self.text_color,
            'bg_color': self.bg_color,
            'has_cache': self.text_image_cache is not None,
            'cache_size': f"{self.text_image_cache.size[0]}x{self.text_image_cache.size[1]}" if self.text_image_cache else "None"
        })
        return base_info
