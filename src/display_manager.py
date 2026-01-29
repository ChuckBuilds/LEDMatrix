import os
if os.getenv("EMULATOR", "false") == "true":
    from RGBMatrixEmulator import RGBMatrix, RGBMatrixOptions
else:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
from PIL import Image, ImageDraw, ImageFont
import time
from typing import Dict, Any, List, Tuple
import logging
import math
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

    def __init__(self, config: Dict[str, Any] = None, force_fallback: bool = False, suppress_test_pattern: bool = False):
        start_time = time.time()
        self.config = config or {}
        self._force_fallback = force_fallback
        self._suppress_test_pattern = suppress_test_pattern
        # Snapshot settings for web preview integration (service writes, web reads)
        self._snapshot_path = "/tmp/led_matrix_preview.png"
        self._snapshot_min_interval_sec = 0.2  # max ~5 fps
        self._last_snapshot_ts = 0.0
        
        # Scrolling state tracking for graceful updates
        self._scrolling_state = {
            'is_scrolling': False,
            'last_scroll_activity': 0,
            'scroll_inactivity_threshold': 2.0,  # seconds of inactivity before considering "not scrolling"
            'deferred_updates': [],
            'max_deferred_updates': 50,  # Limit queue size to prevent memory issues
            'deferred_update_ttl': 300.0  # 5 minutes TTL for deferred updates
        }
        
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
            # Allow callers (e.g., web UI) to force non-hardware fallback mode
            if getattr(self, '_force_fallback', False):
                raise RuntimeError('Forced fallback mode requested')
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
            
            # Disable internal privilege dropping - we manage this via systemd or remain root
            # This prevents the library from dropping to 'daemon' user which breaks file permissions
            options.drop_privileges = False
            
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
            
            # Draw a test pattern unless caller suppressed it (e.g., web on-demand)
            if not getattr(self, '_suppress_test_pattern', False):
                self._draw_test_pattern()
            
        except Exception as e:
            logger.error(f"Failed to initialize RGB Matrix: {e}", exc_info=True)
            # Create a fallback image for web preview using configured dimensions when available
            self.matrix = None
            try:
                hardware_config = self.config.get('display', {}).get('hardware', {}) if self.config else {}
                rows = int(hardware_config.get('rows', 32))
                cols = int(hardware_config.get('cols', 64))
                chain_length = int(hardware_config.get('chain_length', 2))
                fallback_width = max(1, cols * chain_length)
                fallback_height = max(1, rows)
            except Exception:
                fallback_width, fallback_height = 128, 32

            self.image = Image.new('RGB', (fallback_width, fallback_height))
            self.draw = ImageDraw.Draw(self.image)
            # Simple fallback visualization so web UI shows a realistic canvas
            try:
                self.draw.rectangle([0, 0, fallback_width - 1, fallback_height - 1], outline=(255, 0, 0))
                self.draw.line([0, 0, fallback_width - 1, fallback_height - 1], fill=(0, 255, 0))
                self.draw.text((2, max(0, (fallback_height // 2) - 4)), "Simulation", fill=(0, 128, 255))
            except Exception:
                # Best-effort; ignore drawing errors in fallback
                pass
            logger.error(f"Matrix initialization failed, using fallback mode with size {fallback_width}x{fallback_height}. Error: {e}")
            # Do not raise here; allow fallback mode so web preview and non-hardware environments work

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

    def set_brightness(self, brightness: int) -> bool:
        """
        Set display brightness at runtime.

        Args:
            brightness: Brightness level (0-100)

        Returns:
            True if brightness was set successfully, False otherwise
        """
        # Fail fast: validate input type
        if not isinstance(brightness, (int, float)):
            logger.error(f"[BRIGHTNESS] Invalid brightness type: {type(brightness).__name__}, expected int")
            return False

        if self.matrix is None:
            logger.warning("[BRIGHTNESS] Cannot set brightness in fallback mode")
            return False

        # Clamp to valid range
        brightness = max(0, min(100, int(brightness)))

        try:
            # RGBMatrix accepts brightness as a property
            self.matrix.brightness = brightness
            logger.info(f"[BRIGHTNESS] Display brightness set to {brightness}%")
            return True
        except AttributeError as e:
            logger.error(f"[BRIGHTNESS] Matrix does not support brightness property: {e}", exc_info=True)
            return False
        except (TypeError, ValueError) as e:
            logger.error(f"[BRIGHTNESS] Invalid brightness value rejected by hardware: {e}", exc_info=True)
            return False

    def get_brightness(self) -> int:
        """
        Get current display brightness.

        Returns:
            Current brightness level (0-100), or -1 if unavailable
        """
        if self.matrix is None:
            logger.debug("[BRIGHTNESS] Cannot get brightness in fallback mode")
            return -1

        try:
            return self.matrix.brightness
        except AttributeError as e:
            logger.warning(f"[BRIGHTNESS] Matrix does not support brightness property: {e}", exc_info=True)
            return -1

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
                # Still write a snapshot so the web UI can preview
                self._write_snapshot_if_due()
                return
                
            # Copy the current image to the offscreen canvas   
            self.offscreen_canvas.SetImage(self.image)
            
            # Swap buffers immediately
            self.matrix.SwapOnVSync(self.offscreen_canvas)
            
            # Swap our canvas references
            self.offscreen_canvas, self.current_canvas = self.current_canvas, self.offscreen_canvas

            # Write a snapshot for the web preview (throttled)
            self._write_snapshot_if_due()
        except Exception as e:
            logger.error(f"Error updating display: {e}")

    def clear(self):
        """Clear the display completely."""
        try:
            if self.matrix is None:
                # Fallback mode - just clear the image
                # Explicitly clear old image reference to help garbage collection
                old_image = getattr(self, 'image', None)
                width = old_image.width if old_image else 64
                height = old_image.height if old_image else 64
                if old_image is not None:
                    del old_image
                
                self.image = Image.new('RGB', (width, height))
                self.draw = ImageDraw.Draw(self.image)
                logger.debug("Cleared display in fallback mode")
                return
                
            # Explicitly clear old image reference to help garbage collection
            old_image = getattr(self, 'image', None)
            if old_image is not None:
                del old_image
                
            # Create a new black image
            self.image = Image.new('RGB', (self.matrix.width, self.matrix.height))
            self.draw = ImageDraw.Draw(self.image)
            
            # Clear both canvases and the underlying matrix to ensure no artifacts
            try:
                self.offscreen_canvas.Clear()
            except Exception:
                pass
            try:
                self.current_canvas.Clear()
            except Exception:
                pass
            try:
                # Extra safety: clear the matrix front buffer as well
                self.matrix.Clear()
            except Exception:
                pass
            
            # Note: We do NOT call update_display() here to avoid black flashes.
            # The caller should call update_display() after drawing new content.
            # If an immediate clear is needed, the caller can explicitly call
            # clear() followed by update_display().
        except Exception as e:
            logger.error(f"Error clearing display: {e}")

    def _draw_bdf_text(self, text, x, y, color=(255, 255, 255), font=None):
        """Draw text using BDF font with proper bitmap handling."""
        try:
            # Use the passed font or fall back to calendar_font
            face = font if font else self.calendar_font
            
            # Compute baseline from font ascender so caller can pass top-left y
            try:
                ascender_px = face.size.ascender >> 6
            except Exception:
                ascender_px = 0
            baseline_y = y + ascender_px
            
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
                                pixel_y = baseline_y - glyph_top + i
                                # Only draw if within bounds
                                if (0 <= pixel_x < self.width and 0 <= pixel_y < self.height):
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
            
            # Use the same font for small text (currently same size; adjust size here if needed)
            self.small_font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
            logger.info("Press Start 2P small font loaded successfully")

            # Load 5x7 BDF font for calendar events
            try:
                self.calendar_font_path = "assets/fonts/5x7.bdf"
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
                font_path = "assets/fonts/4x6-font.ttf"
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
                 small_font: bool = False, font: ImageFont = None, centered: bool = False):
        """Draw text on the canvas with optional font selection.
        
        Args:
            text: Text to display
            x: X position (None to auto-center, or used as center point if centered=True)
            y: Y position (None defaults to 0)
            color: RGB color tuple
            small_font: Use small font if True
            font: Custom font object (overrides small_font)
            centered: If True, x is treated as center point; if False, x is left edge
        """
        try:
            # Select font based on parameters
            if font:
                current_font = font
            else:
                current_font = self.small_font if small_font else self.regular_font
            
            # Calculate x position
            if x is None:
                # No x provided - center text
                text_width = self.get_text_width(text, current_font)
                x = (self.width - text_width) // 2
            elif centered:
                # x is provided as center point - adjust to left edge
                text_width = self.get_text_width(text, current_font)
                x = x - (text_width // 2)
            
            # Set default y position if not provided
            if y is None:
                y = 0  # Default to top of display
            
            # Draw the text
            if isinstance(current_font, freetype.Face):
                # For BDF fonts, _draw_bdf_text will compute the baseline from the
                # provided top-left y using the font ascender. Do not adjust here.
                self._draw_bdf_text(text, x, y, color, current_font)
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
        if hasattr(self, 'matrix') and self.matrix is not None:
            try:
                self.matrix.Clear()
            except Exception as e:
                logger.warning(f"Error clearing matrix during cleanup: {e}")
        # Ensure image/draw are reset to a blank state
        if hasattr(self, 'image') and hasattr(self, 'draw'):
            try:
                self.image = Image.new('RGB', (self.width, self.height))
                self.draw = ImageDraw.Draw(self.image)
            except Exception:
                pass
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

    def set_scrolling_state(self, is_scrolling: bool):
        """Set the current scrolling state. Call this when a display starts/stops scrolling."""
        current_time = time.time()
        self._scrolling_state['is_scrolling'] = is_scrolling
        if is_scrolling:
            self._scrolling_state['last_scroll_activity'] = current_time
        logger.debug(f"Scrolling state set to: {is_scrolling}")

    def is_currently_scrolling(self) -> bool:
        """Check if the display is currently in a scrolling state."""
        current_time = time.time()
        
        # If explicitly not scrolling, return False
        if not self._scrolling_state['is_scrolling']:
            return False
            
        # If we've been inactive for the threshold period, consider it not scrolling
        if current_time - self._scrolling_state['last_scroll_activity'] > self._scrolling_state['scroll_inactivity_threshold']:
            self._scrolling_state['is_scrolling'] = False
            return False
            
        return True

    def defer_update(self, update_func, priority: int = 0):
        """Defer an update function to be called when not scrolling.
        
        Args:
            update_func: Function to call when not scrolling
            priority: Priority level (lower numbers = higher priority)
        """
        current_time = time.time()
        
        # Clean up expired updates before adding new ones
        self._cleanup_expired_deferred_updates(current_time)
        
        # Limit queue size to prevent memory issues
        if len(self._scrolling_state['deferred_updates']) >= self._scrolling_state['max_deferred_updates']:
            # Remove oldest update to make room
            self._scrolling_state['deferred_updates'].pop(0)
            logger.debug("Removed oldest deferred update due to queue size limit")
        
        self._scrolling_state['deferred_updates'].append({
            'func': update_func,
            'priority': priority,
            'timestamp': current_time
        })
        
        # Only sort if we have a reasonable number of updates to avoid excessive sorting
        if len(self._scrolling_state['deferred_updates']) <= 20:
            self._scrolling_state['deferred_updates'].sort(key=lambda x: x['priority'])
        
        logger.debug(f"Deferred update added. Total deferred: {len(self._scrolling_state['deferred_updates'])}")

    def process_deferred_updates(self):
        """Process any deferred updates if not currently scrolling."""
        current_time = time.time()
        
        # Always clean up expired updates, even if scrolling
        # This prevents memory leaks from accumulated expired updates
        self._cleanup_expired_deferred_updates(current_time)
        
        if self.is_currently_scrolling():
            return
            
        if not self._scrolling_state['deferred_updates']:
            return
            
        if not self._scrolling_state['deferred_updates']:
            return
            
        # Process only a limited number of updates per call to avoid blocking
        max_updates_per_call = min(5, len(self._scrolling_state['deferred_updates']))
        updates_to_process = self._scrolling_state['deferred_updates'][:max_updates_per_call]
        self._scrolling_state['deferred_updates'] = self._scrolling_state['deferred_updates'][max_updates_per_call:]
        
        logger.debug(f"Processing {len(updates_to_process)} deferred updates (queue size: {len(self._scrolling_state['deferred_updates'])})")
        
        failed_updates = []
        for update_info in updates_to_process:
            try:
                # Check if update is still valid (not too old)
                if current_time - update_info['timestamp'] > self._scrolling_state['deferred_update_ttl']:
                    logger.debug("Skipping expired deferred update")
                    continue
                    
                update_info['func']()
                logger.debug("Deferred update executed successfully")
            except Exception as e:
                logger.error(f"Error executing deferred update: {e}")
                # Only retry recent failures, and limit retries
                if current_time - update_info['timestamp'] < 60.0:  # Only retry for 1 minute
                    failed_updates.append(update_info)
        
        # Re-add failed updates to the end of the queue (not the beginning)
        if failed_updates:
            self._scrolling_state['deferred_updates'].extend(failed_updates)

    def _cleanup_expired_deferred_updates(self, current_time: float):
        """Remove expired deferred updates to prevent memory leaks."""
        ttl = self._scrolling_state['deferred_update_ttl']
        initial_count = len(self._scrolling_state['deferred_updates'])
        
        # Filter out expired updates
        self._scrolling_state['deferred_updates'] = [
            update for update in self._scrolling_state['deferred_updates']
            if current_time - update['timestamp'] <= ttl
        ]
        
        removed_count = initial_count - len(self._scrolling_state['deferred_updates'])
        if removed_count > 0:
            logger.debug(f"Cleaned up {removed_count} expired deferred updates")

    def get_scrolling_stats(self) -> dict:
        """Get current scrolling statistics for debugging."""
        return {
            'is_scrolling': self._scrolling_state['is_scrolling'],
            'last_activity': self._scrolling_state['last_scroll_activity'],
            'deferred_count': len(self._scrolling_state['deferred_updates']),
            'inactivity_threshold': self._scrolling_state['scroll_inactivity_threshold'],
            'max_deferred_updates': self._scrolling_state['max_deferred_updates'],
            'deferred_update_ttl': self._scrolling_state['deferred_update_ttl']
        }

    def _write_snapshot_if_due(self) -> None:
        """Write the current image to a PNG snapshot file at a limited frequency."""
        try:
            now = time.time()
            if (now - self._last_snapshot_ts) < self._snapshot_min_interval_sec:
                return
            # Ensure directory exists with proper permissions
            from pathlib import Path
            from src.common.permission_utils import (
                ensure_directory_permissions,
                ensure_file_permissions,
                get_assets_dir_mode,
                get_assets_file_mode
            )
            snapshot_path_obj = Path(self._snapshot_path)
            # Only ensure permissions on non-system directories
            # Never modify /tmp permissions - it has special system permissions (1777)
            # that must not be changed or it breaks apt and other system tools
            parent_dir = snapshot_path_obj.parent
            if parent_dir and str(parent_dir) != '/tmp':
                ensure_directory_permissions(parent_dir, get_assets_dir_mode())
            # Write atomically: temp then replace
            tmp_path = f"{self._snapshot_path}.tmp"
            self.image.save(tmp_path, format='PNG')
            try:
                os.replace(tmp_path, self._snapshot_path)
            except Exception:
                # Fallback to direct save if replace not supported
                self.image.save(self._snapshot_path, format='PNG')
            # Set proper file permissions after saving
            try:
                ensure_file_permissions(snapshot_path_obj, get_assets_file_mode())
            except Exception:
                pass
            self._last_snapshot_ts = now
        except Exception as e:
            # Snapshot failures should never break display; log at debug to avoid noise
            logger.debug(f"Snapshot write skipped: {e}")