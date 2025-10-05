import logging
import time
from PIL import ImageFont, Image, ImageDraw
import freetype
import os

from .display_manager import DisplayManager

logger = logging.getLogger(__name__)

class TextDisplay:
    def __init__(self, display_manager: DisplayManager, config: dict):
        self.display_manager = display_manager
        self.config = config.get('text_display', {})
        
        self.text = self.config.get('text', "Hello, World!")
        
        # Support new unified font system
        self.element_key = self.config.get('element_key')
        self.font_family = self.config.get('font_family')
        self.font_size_px = self.config.get('font_size_px')
        self.font_size_token = self.config.get('font_size_token')
        
        # Legacy font support
        self.font_path = self.config.get('font_path', "assets/fonts/PressStart2P-Regular.ttf")
        self.font_size = self.config.get('font_size', 8)
        
        self.scroll_enabled = self.config.get('scroll', False)
        self.text_color = tuple(self.config.get('text_color', [255, 255, 255]))
        self.bg_color = tuple(self.config.get('background_color', [0, 0, 0]))
        # scroll_gap_width defaults to the width of the display matrix
        self.scroll_gap_width = self.config.get('scroll_gap_width', self.display_manager.matrix.width)

        self.font = self._load_font()
        
        self.text_content_width = 0 # Pixel width of the actual text string
        self.text_image_cache = None # For pre-rendered text (PIL.Image)
        self.cached_total_scroll_width = 0 # Total width of the cache: text_content_width + scroll_gap_width

        self._regenerate_renderings() # Initial creation of cache and width calculation
        
        self.scroll_pos = 0.0 # Use float for precision
        self.last_update_time = time.time()
        self.scroll_speed = self.config.get('scroll_speed', 30) # Pixels per second

    def _regenerate_renderings(self):
        """Calculate text width and attempt to create/update the text image cache."""
        if not self.text or not self.font:
            self.text_content_width = 0
            self.text_image_cache = None
            self.cached_total_scroll_width = 0
            return

        try:
            self.text_content_width, _, _ = self.display_manager.measure_text(self.text, self.font)
        except Exception as e:
            logger.error(f"Error calculating text content width: {e}")
            self.text_content_width = 0
            self.text_image_cache = None
            self.cached_total_scroll_width = 0
            return
            
        self._create_text_image_cache()
        self.scroll_pos = 0.0 # Reset scroll position when text/font/colors change

    def _create_text_image_cache(self):
        """Pre-render the text onto an image if using a TTF font. Includes a trailing gap."""
        self.text_image_cache = None # Clear previous cache
        self.cached_total_scroll_width = 0

        if not self.text or not self.font or self.text_content_width == 0:
            return

        if isinstance(self.font, freetype.Face):
            logger.info("TextDisplay: Pre-rendering cache is not used for BDF/freetype fonts. Will use direct drawing.")
            # For BDF, the "scroll width" for reset purposes is handled by the direct drawing logic's conditions
            return

        # --- TTF Caching Path ---
        try:
            dummy_img = Image.new('RGB', (1, 1))
            dummy_draw = ImageDraw.Draw(dummy_img)
            bbox = dummy_draw.textbbox((0, 0), self.text, font=self.font)
            actual_text_render_height = bbox[3] - bbox[1]
            
            # Total width of the cache is the text width plus the configured gap
            self.cached_total_scroll_width = self.text_content_width + self.scroll_gap_width
            cache_height = self.display_manager.matrix.height

            self.text_image_cache = Image.new('RGB', (self.cached_total_scroll_width, cache_height), self.bg_color)
            draw_cache = ImageDraw.Draw(self.text_image_cache)

            desired_top_edge = (cache_height - actual_text_render_height) // 2
            y_draw_on_cache = desired_top_edge - bbox[1]
            
            # Draw the text at the beginning of the cache
            draw_cache.text((0, y_draw_on_cache), self.text, font=self.font, fill=self.text_color)
            # The rest of the image (the gap) is already bg_color
            logger.info(f"TextDisplay: Created text cache for '{self.text[:30]}...' (TTF). Text width: {self.text_content_width}, Gap: {self.scroll_gap_width}, Total cache width: {self.cached_total_scroll_width}x{cache_height}")
        except Exception as e:
            logger.error(f"TextDisplay: Failed to create text image cache: {e}", exc_info=True)
            self.text_image_cache = None
            self.cached_total_scroll_width = 0

    def _load_font(self):
        """Load the specified font using the unified font system or legacy method."""
        # Use new unified font system if configured
        if self.element_key or self.font_family or self.font_size_px or self.font_size_token:
            try:
                font = self.display_manager.font_manager.resolve(
                    element_key=self.element_key,
                    family=self.font_family,
                    size_px=self.font_size_px,
                    size_token=self.font_size_token
                )
                logger.info(f"Loaded font via FontManager: element_key={self.element_key}, family={self.font_family}, size_px={self.font_size_px}, size_token={self.font_size_token}")
                return font
            except Exception as e:
                logger.error(f"Failed to load font via FontManager: {e}", exc_info=True)
                return self.display_manager.regular_font
        
        # Legacy font loading
        font_path = self.font_path
        # Resolve relative paths against project root based on this file location
        if not os.path.isabs(font_path):
            base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            font_path = os.path.join(base_path, font_path)

        logger.info(f"Attempting to load font: {font_path} at size {self.font_size}")
        
        if not os.path.exists(font_path):
            logger.error(f"Font file not found: {font_path}. Falling back to default.")
            return self.display_manager.regular_font

        try:
            if font_path.lower().endswith('.ttf'):
                font = ImageFont.truetype(font_path, self.font_size)
                logger.info(f"Loaded TTF font: {self.font_path}")
                return font
            elif font_path.lower().endswith('.bdf'):
                face = freetype.Face(font_path)
                face.set_pixel_sizes(0, self.font_size) 
                logger.info(f"Loaded BDF font: {self.font_path} with freetype")
                return face 
            else:
                logger.warning(f"Unsupported font type: {font_path}. Falling back.")
                return self.display_manager.regular_font
        except Exception as e:
            logger.error(f"Failed to load font {font_path}: {e}", exc_info=True)
            return self.display_manager.regular_font

    # _calculate_text_width is effectively replaced by logic in _regenerate_renderings
    # but kept for direct calls if ever needed, or as a reference to DisplayManager's method
    def _calculate_text_width(self): 
        """DEPRECATED somewhat: Get text width. Relies on self.text_content_width set by _regenerate_renderings."""
        try:
            return self.display_manager.get_text_width(self.text, self.font)
        except Exception as e:
            logger.error(f"Error calculating text width: {e}")
            return 0

    def update(self):
        """Update scroll position if scrolling is enabled."""
        # Scrolling is only meaningful if the actual text content is wider than the screen,
        # or if a cache is used (which implies scrolling over text + gap).
        # The condition self.text_content_width <= self.display_manager.matrix.width handles non-scrolling for static text.
        if not self.scroll_enabled or (not self.text_image_cache and self.text_content_width <= self.display_manager.matrix.width):
            self.scroll_pos = 0.0 
            return

        current_time = time.time()
        delta_time = current_time - self.last_update_time
        self.last_update_time = current_time

        scroll_delta = delta_time * self.scroll_speed
        self.scroll_pos += scroll_delta

        if self.text_image_cache: 
            # Using cached image: scroll_pos loops over the total cache width (text + gap)
            if self.cached_total_scroll_width > 0 and self.scroll_pos >= self.cached_total_scroll_width:
                self.scroll_pos %= self.cached_total_scroll_width 
        else: 
            # Not using cache (e.g., BDF direct drawing):
            # Reset when text fully scrolled past left edge + matrix width (original behavior creating a conceptual gap)
            # self.text_content_width is used here as it refers to the actual text being drawn directly.
            if self.text_content_width > 0 and self.scroll_pos > self.text_content_width + self.display_manager.matrix.width:
                self.scroll_pos = 0.0
            
    def display(self):
        """Draw the text onto the display manager's canvas."""
        dm = self.display_manager
        matrix_width = dm.matrix.width
        matrix_height = dm.matrix.height

        dm.image = Image.new('RGB', (matrix_width, matrix_height), self.bg_color)
        dm.draw = ImageDraw.Draw(dm.image)
        
        if not self.text or self.text_content_width == 0:
            dm.update_display()
            return

        # Use pre-rendered cache if available and scrolling is active
        # Scrolling via cache is only relevant if the actual text content itself is wider than the matrix,
        # or if we want to scroll a short text with a large gap.
        # The self.cached_total_scroll_width > matrix_width implies the content (text+gap) is scrollable.
        if self.text_image_cache and self.scroll_enabled and self.cached_total_scroll_width > matrix_width :
            current_scroll_int = int(self.scroll_pos)
            
            source_x1 = current_scroll_int
            source_x2 = current_scroll_int + matrix_width

            if source_x2 <= self.cached_total_scroll_width:
                segment = self.text_image_cache.crop((source_x1, 0, source_x2, matrix_height))
                dm.image.paste(segment, (0, 0))
            else:
                # Wrap-around: paste two parts from cache
                width1 = self.cached_total_scroll_width - source_x1
                if width1 > 0:
                    segment1 = self.text_image_cache.crop((source_x1, 0, self.cached_total_scroll_width, matrix_height))
                    dm.image.paste(segment1, (0, 0))
                
                remaining_width_for_screen = matrix_width - width1
                if remaining_width_for_screen > 0:
                    segment2 = self.text_image_cache.crop((0, 0, remaining_width_for_screen, matrix_height))
                    dm.image.paste(segment2, (width1 if width1 > 0 else 0, 0))
        else:
            # Fallback: Direct drawing (BDF, static TTF, or TTF text that fits screen and isn't forced to scroll by gap)
            final_y_for_draw = 0
            try:
                if isinstance(self.font, freetype.Face):
                    text_render_height = self.font.size.height >> 6 
                    final_y_for_draw = (matrix_height - text_render_height) // 2
                else: 
                    pil_bbox = dm.draw.textbbox((0, 0), self.text, font=self.font)
                    text_render_height = pil_bbox[3] - pil_bbox[1] 
                    final_y_for_draw = (matrix_height - text_render_height) // 2 - pil_bbox[1]
            except Exception as e:
                 logger.warning(f"TextDisplay: Could not calculate text height for direct drawing: {e}. Using y=0.", exc_info=True)
                 final_y_for_draw = 0

            if self.scroll_enabled and self.text_content_width > matrix_width:
                # Scrolling text (direct drawing path, e.g., for BDF)
                x_draw_pos = matrix_width - int(self.scroll_pos) # scroll_pos for BDF already considers a type of gap for reset
                dm.draw_text(
                    text=self.text, x=x_draw_pos, y=final_y_for_draw,
                    color=self.text_color, font=self.font
                )
            else:
                # Static text (centered horizontally)
                x_draw_pos = (matrix_width - self.text_content_width) // 2
                dm.draw_text(
                    text=self.text, x=x_draw_pos, y=final_y_for_draw,
                    color=self.text_color, font=self.font
                )
            
        dm.update_display()

    def set_text(self, new_text: str):
        self.text = new_text
        self._regenerate_renderings()

    def set_font(self, font_path: str = None, font_size: int = None, 
                 element_key: str = None, family: str = None, size_px: int = None, size_token: str = None):
        """Set font using legacy or new unified font system."""
        if element_key or family or size_px or size_token:
            # Use new unified font system
            self.element_key = element_key
            self.font_family = family
            self.font_size_px = size_px
            self.font_size_token = size_token
            # Clear legacy parameters
            self.font_path = None
            self.font_size = None
        else:
            # Use legacy font system
            if font_path is not None:
                self.font_path = font_path
            if font_size is not None:
                self.font_size = font_size
            # Clear new parameters
            self.element_key = None
            self.font_family = None
            self.font_size_px = None
            self.font_size_token = None
        
        self.font = self._load_font()
        self._regenerate_renderings()

    def set_color(self, text_color: tuple, bg_color: tuple):
        self.text_color = text_color
        self.bg_color = bg_color
        self._regenerate_renderings()

    def set_scroll_enabled(self, enabled: bool):
        self.scroll_enabled = enabled
        self.scroll_pos = 0.0 
        # Cache regeneration is not strictly needed, display logic handles scroll_enabled.

    def set_scroll_speed(self, speed: float):
        self.scroll_speed = speed
        
    def set_scroll_gap_width(self, gap_width: int):
        self.scroll_gap_width = gap_width
        self._regenerate_renderings() # Gap change requires cache rebuild

