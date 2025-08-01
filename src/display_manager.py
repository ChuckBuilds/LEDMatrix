from rgbmatrix import RGBMatrix, RGBMatrixOptions
from PIL import Image, ImageDraw, ImageFont
import time
from typing import Dict, Any, List, Tuple
import logging
import math
from .weather_icons import WeatherIcons
import os
import freetype

# Get logger without configuring
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Set to INFO level

class DisplayManager:
    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(DisplayManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, config: Dict[str, Any] = None):
        start_time = time.time()
        self.config = config or {}
        self._setup_matrix()
        logger.info("Matrix setup completed in %.3f seconds", time.time() - start_time)
        
        font_time = time.time()
        self._load_fonts()
        logger.info("Font loading completed in %.3f seconds", time.time() - font_time)
        
        # Initialize managers
        # Calendar manager is now initialized by DisplayController
        
    def _setup_matrix(self):
        """Initialize the RGB matrix with configuration settings."""
        setup_start = time.time()
        
        try:
            options = RGBMatrixOptions()
            
            # Hardware configuration
            hardware_config = self.config.get('display', {}).get('hardware', {})
            runtime_config = self.config.get('display', {}).get('runtime', {})
            
            # Basic hardware settings
            options.rows = hardware_config.get('rows', 32)
            options.cols = hardware_config.get('cols', 64)
            options.chain_length = hardware_config.get('chain_length', 2)
            options.parallel = hardware_config.get('parallel', 1)
            options.hardware_mapping = hardware_config.get('hardware_mapping', 'adafruit-hat-pwm')
            
            # Performance and stability settings
            options.brightness = hardware_config.get('brightness', 90)
            options.pwm_bits = hardware_config.get('pwm_bits', 10)
            options.pwm_lsb_nanoseconds = hardware_config.get('pwm_lsb_nanoseconds', 150)
            options.led_rgb_sequence = hardware_config.get('led_rgb_sequence', 'RGB')
            options.pixel_mapper_config = hardware_config.get('pixel_mapper_config', '')
            options.row_address_type = hardware_config.get('row_address_type', 0)
            options.multiplexing = hardware_config.get('multiplexing', 0)
            options.disable_hardware_pulsing = hardware_config.get('disable_hardware_pulsing', False)
            options.show_refresh_rate = hardware_config.get('show_refresh_rate', False)
            options.limit_refresh_rate_hz = hardware_config.get('limit_refresh_rate_hz', 90)
            options.gpio_slowdown = runtime_config.get('gpio_slowdown', 2)
            
            # Additional settings from config
            if 'scan_mode' in hardware_config:
                options.scan_mode = hardware_config.get('scan_mode')
            if 'pwm_dither_bits' in hardware_config:
                options.pwm_dither_bits = hardware_config.get('pwm_dither_bits')
            if 'inverse_colors' in hardware_config:
                options.inverse_colors = hardware_config.get('inverse_colors')
            
            logger.info(f"Initializing RGB Matrix with settings: rows={options.rows}, cols={options.cols}, chain_length={options.chain_length}, parallel={options.parallel}, hardware_mapping={options.hardware_mapping}")
            
            # Initialize the matrix
            self.matrix = RGBMatrix(options=options)
            logger.info("RGB Matrix initialized successfully")
            
            # Create double buffer for smooth updates
            self.offscreen_canvas = self.matrix.CreateFrameCanvas()
            self.current_canvas = self.matrix.CreateFrameCanvas()
            logger.info("Frame canvases created successfully")
            
            # Create image with full chain width
            self.image = Image.new('RGB', (self.matrix.width, self.matrix.height))
            self.draw = ImageDraw.Draw(self.image)
            logger.info(f"Image canvas created with dimensions: {self.matrix.width}x{self.matrix.height}")
            
            # Initialize font with Press Start 2P
            try:
                self.font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
                logger.info("Initial Press Start 2P font loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load initial font: {e}")
                self.font = ImageFont.load_default()
            
            # Draw a test pattern
            self._draw_test_pattern()
            
        except Exception as e:
            logger.error(f"Failed to initialize RGB Matrix: {e}", exc_info=True)
            # Create a fallback image for web preview
            self.matrix = None
            self.image = Image.new('RGB', (128, 32))  # Default size
            self.draw = ImageDraw.Draw(self.image)
            self.draw.text((10, 10), "Matrix Error", fill=(255, 0, 0))
            logger.error(f"Matrix initialization failed, using fallback mode. Error: {e}")
            raise

    @property
    def width(self):
        """Get the display width."""
        if hasattr(self, 'matrix') and self.matrix is not None:
            return self.matrix.width
        elif hasattr(self, 'image'):
            return self.image.width
        else:
            return 128  # Default fallback width

    @property
    def height(self):
        """Get the display height."""
        if hasattr(self, 'matrix') and self.matrix is not None:
            return self.matrix.height
        elif hasattr(self, 'image'):
            return self.image.height
        else:
            return 32  # Default fallback height

    def _draw_test_pattern(self):
        """Draw a test pattern to verify the display is working."""
        try:
            self.clear()
            
            if self.matrix is None:
                # Fallback mode - just draw on the image
                self.draw.rectangle([0, 0, self.image.width-1, self.image.height-1], outline=(255, 0, 0))
                self.draw.line([0, 0, self.image.width-1, self.image.height-1], fill=(0, 255, 0))
                self.draw.text((10, 10), "Simulation", font=self.font, fill=(0, 0, 255))
                logger.info("Drew test pattern in fallback mode")
                return
            
            # Draw a red rectangle border
            self.draw.rectangle([0, 0, self.matrix.width-1, self.matrix.height-1], outline=(255, 0, 0))
            
            # Draw a diagonal line
            self.draw.line([0, 0, self.matrix.width-1, self.matrix.height-1], fill=(0, 255, 0))
            
            # Draw some text - changed from "TEST" to "Initializing" with smaller font
            self.draw.text((10, 10), "Initializing", font=self.font, fill=(0, 0, 255))
            
            # Update the display once after everything is drawn
            self.update_display()
            time.sleep(0.5)  # Reduced from 1 second to 0.5 seconds for faster animation
            
        except Exception as e:
            logger.error(f"Error drawing test pattern: {e}", exc_info=True)

    def update_display(self):
        """Update the display using double buffering with proper sync."""
        try:
            if self.matrix is None:
                # Fallback mode - no actual hardware to update
                logger.debug("Update display called in fallback mode (no hardware)")
                return
                
            # Copy the current image to the offscreen canvas   
            self.offscreen_canvas.SetImage(self.image)
            
            # Swap buffers immediately
            self.matrix.SwapOnVSync(self.offscreen_canvas, False)
            
            # Swap our canvas references
            self.offscreen_canvas, self.current_canvas = self.current_canvas, self.offscreen_canvas
        except Exception as e:
            logger.error(f"Error updating display: {e}")

    def clear(self):
        """Clear the display completely."""
        try:
            if self.matrix is None:
                # Fallback mode - just clear the image
                self.image = Image.new('RGB', (self.image.width, self.image.height))
                self.draw = ImageDraw.Draw(self.image)
                logger.debug("Cleared display in fallback mode")
                return
                
            # Create a new black image
            self.image = Image.new('RGB', (self.matrix.width, self.matrix.height))
            self.draw = ImageDraw.Draw(self.image)
            
            # Clear both canvases
            self.offscreen_canvas.Clear()
            self.current_canvas.Clear()
            
            # Update the display to show the clear
            self.update_display()
        except Exception as e:
            logger.error(f"Error clearing display: {e}")

    def _draw_bdf_text(self, text, x, y, color=(255, 255, 255), font=None):
        """Draw text using BDF font with proper bitmap handling."""
        try:
            # Use the passed font or fall back to calendar_font
            face = font if font else self.calendar_font
            
            # Add 6-pixel adjustment for vertical alignment
            y += 6
            
            for char in text:
                face.load_char(char)
                bitmap = face.glyph.bitmap
                
                # Get glyph metrics
                glyph_left = face.glyph.bitmap_left
                glyph_top = face.glyph.bitmap_top
                
                # Draw the character
                for i in range(bitmap.rows):
                    for j in range(bitmap.width):
                        byte_index = i * bitmap.pitch + (j // 8)
                        if byte_index < len(bitmap.buffer):
                            byte = bitmap.buffer[byte_index]
                            if byte & (1 << (7 - (j % 8))):
                                # Calculate actual pixel position
                                pixel_x = x + glyph_left + j
                                pixel_y = y - glyph_top + i
                                # Only draw if within bounds
                                if (0 <= pixel_x < self.matrix.width and 
                                    0 <= pixel_y < self.matrix.height):
                                    self.draw.point((pixel_x, pixel_y), fill=color)
                
                # Move to next character
                x += face.glyph.advance.x >> 6
                
        except Exception as e:
            logger.error(f"Error drawing BDF text: {e}", exc_info=True)

    def _load_fonts(self):
        """Load fonts with proper error handling."""
        try:
            # Load Press Start 2P font
            self.regular_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            logger.info("Press Start 2P font loaded successfully")
            
            # Use the same font for small text, just at a smaller size
            self.small_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            logger.info("Press Start 2P small font loaded successfully")

            # Load 5x7 BDF font for calendar events
            try:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                relative_font_path = os.path.join(script_dir, "../assets/fonts/5x7.bdf")
                self.calendar_font_path = os.path.abspath(relative_font_path)
                logger.info(f"Attempting to load 5x7 font from: {self.calendar_font_path}")
                
                if not os.path.exists(self.calendar_font_path):
                    raise FileNotFoundError(f"Font file not found at {self.calendar_font_path}")
                
                # Load with freetype for proper BDF handling
                face = freetype.Face(self.calendar_font_path)
                logger.info(f"5x7 calendar font loaded successfully from {self.calendar_font_path}")
                logger.info(f"Calendar font size: {face.size.height >> 6} pixels")
                
                # Store the face for later use
                self.calendar_font = face
                    
            except Exception as font_err:
                logger.error(f"Failed to load 5x7 font: {str(font_err)}", exc_info=True)
                logger.error("Falling back to small font")
                self.calendar_font = self.small_font

            # Assign the loaded calendar_font (which should be 5x7 BDF or its fallback) 
            # to a new attribute for specific use, e.g., in MusicManager.
            self.bdf_5x7_font = self.calendar_font 
            logger.info(f"Assigned calendar_font (type: {type(self.bdf_5x7_font).__name__}) to bdf_5x7_font.")

            # Load 4x6 font as extra_small_font
            try:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                relative_font_path = os.path.join(script_dir, "../assets/fonts/4x6-font.ttf")
                font_path = os.path.abspath(relative_font_path)
                logger.info(f"Attempting to load 4x6 TTF font from: {font_path} at size 6")
                self.extra_small_font = ImageFont.truetype(font_path, 6)
                logger.info(f"4x6 TTF extra small font loaded successfully from {font_path}")
            except Exception as font_err:
                logger.error(f"Failed to load 4x6 TTF font: {font_err}. Falling back.")
                self.extra_small_font = self.small_font



        except Exception as e:
            logger.error(f"Error in font loading: {e}", exc_info=True)
            # Fallback to default font
            self.regular_font = ImageFont.load_default()
            self.small_font = self.regular_font
            self.calendar_font = self.regular_font
            if not hasattr(self, 'extra_small_font'): 
                self.extra_small_font = self.regular_font
            if not hasattr(self, 'bdf_5x7_font'): # Ensure bdf_5x7_font also gets a fallback
                self.bdf_5x7_font = self.regular_font


    def get_text_width(self, text, font):
        """Get the width of text when rendered with the given font."""
        try:
            if isinstance(font, freetype.Face):
                # For FreeType faces, calculate width using freetype
                width = 0
                for char in text:
                    font.load_char(char)
                    width += font.glyph.advance.x >> 6
                return width
            else:
                # For PIL fonts, use textbbox
                bbox = self.draw.textbbox((0, 0), text, font=font)
                return bbox[2] - bbox[0]
        except Exception as e:
            logger.error(f"Error getting text width: {e}")
            return 0  # Return 0 as fallback

    def get_font_height(self, font):
        """Get the height of the given font for line spacing purposes."""
        try:
            if isinstance(font, freetype.Face):
                # For FreeType faces (BDF), the 'height' metric gives the recommended line spacing.
                return font.size.height >> 6
            else:
                # For PIL TTF fonts, getmetrics() provides ascent and descent.
                # The line height is the sum of ascent and descent.
                ascent, descent = font.getmetrics()
                return ascent + descent
        except Exception as e:
            logger.error(f"Error getting font height for font type {type(font).__name__}: {e}")
            # Fallback for TTF font if getmetrics() fails, or for other font types.
            if hasattr(font, 'size'):
                return font.size
            return 8 # A reasonable default for an 8px font.

    def draw_text(self, text: str, x: int = None, y: int = None, color: tuple = (255, 255, 255), 
                 small_font: bool = False, font: ImageFont = None):
        """Draw text on the canvas with optional font selection."""
        try:
            # Select font based on parameters
            if font:
                current_font = font
            else:
                current_font = self.small_font if small_font else self.regular_font
            
            # Calculate x position if not provided (center text)
            if x is None:
                text_width = self.get_text_width(text, current_font)
                x = (self.matrix.width - text_width) // 2
            
            # Set default y position if not provided
            if y is None:
                y = 0  # Default to top of display
            
            # Draw the text
            if isinstance(current_font, freetype.Face):
                # For BDF fonts, we need to adjust the y-coordinate.
                # The passed 'y' is the top of the text, but our drawing function
                # expects the baseline. The 'ascender' is the distance from the
                # baseline to the top of the glyphs.
                baseline_y = y + (current_font.size.ascender >> 6)
                self._draw_bdf_text(text, x, baseline_y, color, current_font)
            else:
                # For TTF fonts, use PIL's text drawing which expects top-left.
                self.draw.text((x, y), text, font=current_font, fill=color)
            
        except Exception as e:
            logger.error(f"Error drawing text: {e}", exc_info=True)

    def draw_sun(self, x: int, y: int, size: int = 16):
        """Draw a sun icon using yellow circles and lines."""
        center = (x + size//2, y + size//2)
        radius = size//3
        
        # Draw the center circle
        self.draw.ellipse([center[0]-radius, center[1]-radius, 
                          center[0]+radius, center[1]+radius], 
                         fill=(255, 255, 0))  # Yellow
        
        # Draw the rays
        ray_length = size//4
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            start_x = center[0] + (radius * math.cos(rad))
            start_y = center[1] + (radius * math.sin(rad))
            end_x = center[0] + ((radius + ray_length) * math.cos(rad))
            end_y = center[1] + ((radius + ray_length) * math.sin(rad))
            self.draw.line([start_x, start_y, end_x, end_y], fill=(255, 255, 0), width=2)

    def draw_cloud(self, x: int, y: int, size: int = 16, color=(200, 200, 200)):
        """Draw a cloud icon."""
        # Draw multiple circles to form a cloud shape
        self.draw.ellipse([x+size//4, y+size//3, x+size//4+size//2, y+size//3+size//2], fill=color)
        self.draw.ellipse([x+size//2, y+size//3, x+size//2+size//2, y+size//3+size//2], fill=color)
        self.draw.ellipse([x+size//3, y+size//6, x+size//3+size//2, y+size//6+size//2], fill=color)

    def draw_rain(self, x: int, y: int, size: int = 16):
        """Draw rain icon with cloud and droplets."""
        # Draw cloud
        self.draw_cloud(x, y, size)
        
        # Draw rain drops
        drop_color = (0, 0, 255)  # Blue
        drop_size = size//6
        for i in range(3):
            drop_x = x + size//4 + (i * size//3)
            drop_y = y + size//2
            self.draw.line([drop_x, drop_y, drop_x, drop_y+drop_size], 
                          fill=drop_color, width=2)

    def draw_snow(self, x: int, y: int, size: int = 16):
        """Draw snow icon with cloud and snowflakes."""
        # Draw cloud
        self.draw_cloud(x, y, size)
        
        # Draw snowflakes
        snow_color = (200, 200, 255)  # Light blue
        for i in range(3):
            center_x = x + size//4 + (i * size//3)
            center_y = y + size//2 + size//4
            # Draw a small star shape
            for angle in range(0, 360, 60):
                rad = math.radians(angle)
                end_x = center_x + (size//8 * math.cos(rad))
                end_y = center_y + (size//8 * math.sin(rad))
                self.draw.line([center_x, center_y, end_x, end_y], 
                             fill=snow_color, width=1)

    # Weather icon color constants
    WEATHER_COLORS = {
        'sun': (255, 200, 0),    # Bright yellow
        'cloud': (200, 200, 200), # Light gray
        'rain': (0, 100, 255),    # Light blue
        'snow': (220, 220, 255),  # Ice blue
        'storm': (255, 255, 0)    # Lightning yellow
    }

    def _draw_sun(self, x: int, y: int, size: int) -> None:
        """Draw a sun icon with rays."""
        center_x, center_y = x + size//2, y + size//2
        radius = size//4
        ray_length = size//3
        
        # Draw the main sun circle
        self.draw.ellipse([center_x - radius, center_y - radius, 
                          center_x + radius, center_y + radius], 
                         fill=self.WEATHER_COLORS['sun'])
        
        # Draw sun rays
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            start_x = center_x + int((radius + 2) * math.cos(rad))
            start_y = center_y + int((radius + 2) * math.sin(rad))
            end_x = center_x + int((radius + ray_length) * math.cos(rad))
            end_y = center_y + int((radius + ray_length) * math.sin(rad))
            self.draw.line([start_x, start_y, end_x, end_y], 
                         fill=self.WEATHER_COLORS['sun'], width=2)

    def _draw_cloud(self, x: int, y: int, size: int) -> None:
        """Draw a cloud using multiple circles."""
        cloud_color = self.WEATHER_COLORS['cloud']
        base_y = y + size//2
        
        # Draw main cloud body (3 overlapping circles)
        circle_radius = size//4
        positions = [
            (x + size//3, base_y),           # Left circle
            (x + size//2, base_y - size//6), # Top circle
            (x + 2*size//3, base_y)          # Right circle
        ]
        
        for cx, cy in positions:
            self.draw.ellipse([cx - circle_radius, cy - circle_radius,
                             cx + circle_radius, cy + circle_radius],
                            fill=cloud_color)

    def _draw_rain(self, x: int, y: int, size: int) -> None:
        """Draw rain drops falling from a cloud."""
        self._draw_cloud(x, y, size)
        rain_color = self.WEATHER_COLORS['rain']
        
        # Draw rain drops at an angle
        drop_size = size//8
        drops = [
            (x + size//4, y + 2*size//3),
            (x + size//2, y + 3*size//4),
            (x + 3*size//4, y + 2*size//3)
        ]
        
        for dx, dy in drops:
            # Draw angled rain drops
            self.draw.line([dx, dy, dx - drop_size//2, dy + drop_size],
                         fill=rain_color, width=2)

    def _draw_snow(self, x: int, y: int, size: int) -> None:
        """Draw snowflakes falling from a cloud."""
        self._draw_cloud(x, y, size)
        snow_color = self.WEATHER_COLORS['snow']
        
        # Draw snowflakes
        flake_size = size//6
        flakes = [
            (x + size//4, y + 2*size//3),
            (x + size//2, y + 3*size//4),
            (x + 3*size//4, y + 2*size//3)
        ]
        
        for fx, fy in flakes:
            # Draw a snowflake (six-pointed star)
            for angle in range(0, 360, 60):
                rad = math.radians(angle)
                end_x = fx + int(flake_size * math.cos(rad))
                end_y = fy + int(flake_size * math.sin(rad))
                self.draw.line([fx, fy, end_x, end_y],
                             fill=snow_color, width=1)

    def _draw_storm(self, x: int, y: int, size: int) -> None:
        """Draw a storm cloud with lightning bolt."""
        self._draw_cloud(x, y, size)
        
        # Draw lightning bolt
        bolt_color = self.WEATHER_COLORS['storm']
        bolt_points = [
            (x + size//2, y + size//2),          # Top
            (x + 3*size//5, y + 2*size//3),      # Middle right
            (x + 2*size//5, y + 2*size//3),      # Middle left
            (x + size//2, y + 5*size//6)         # Bottom
        ]
        self.draw.polygon(bolt_points, fill=bolt_color)

    def draw_weather_icon(self, condition: str, x: int, y: int, size: int = 16) -> None:
        """Draw a weather icon based on the condition."""
        if condition.lower() in ['clear', 'sunny']:
            self._draw_sun(x, y, size)
        elif condition.lower() in ['clouds', 'cloudy', 'partly cloudy']:
            self._draw_cloud(x, y, size)
        elif condition.lower() in ['rain', 'drizzle', 'shower']:
            self._draw_rain(x, y, size)
        elif condition.lower() in ['snow', 'sleet', 'hail']:
            self._draw_snow(x, y, size)
        elif condition.lower() in ['thunderstorm', 'storm']:
            self._draw_storm(x, y, size)
        else:
            self._draw_sun(x, y, size)
        # Note: No update_display() here - let the caller handle the update

    def draw_text_with_icons(self, text: str, icons: List[tuple] = None, x: int = None, y: int = None, 
                            color: tuple = (255, 255, 255)):
        """Draw text with weather icons at specified positions."""
        # Draw the text
        self.draw_text(text, x, y, color)
        
        # Draw any icons
        if icons:
            for icon_type, icon_x, icon_y in icons:
                self.draw_weather_icon(icon_type, icon_x, icon_y)
        
        # Update the display once after everything is drawn
        self.update_display()

    def cleanup(self):
        """Clean up resources."""
        self.matrix.Clear()
        # Reset the singleton state when cleaning up
        DisplayManager._instance = None
        DisplayManager._initialized = False

    def format_date_with_ordinal(self, dt):
        """Formats a datetime object into 'Mon Aug 30th' style."""
        day = dt.day
        if 11 <= day <= 13:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        
        return dt.strftime(f"%b %-d{suffix}") 