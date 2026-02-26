"""
Of The Day Plugin for LEDMatrix

Display daily featured content like Word of the Day, Bible verses, or custom items.
Supports multiple categories with automatic rotation and configurable data sources.

Features:
- Multiple category support (Word of the Day, Bible verses, etc.)
- Automatic daily updates
- Rotating display of title, definition, examples
- Configurable data sources via JSON files
- Multi-line text wrapping for long content

API Version: 1.0.0
"""

import os
import json
import time
from datetime import date
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

from src.plugin_system.base_plugin import BasePlugin
from src.logging_config import get_logger

logger = get_logger(__name__)


class OfTheDayPlugin(BasePlugin):
    """
    Of The Day plugin for displaying daily featured content.
    
    Supports multiple categories with rotation between title, subtitle, and content.
    
    Configuration options:
        categories (dict): Dictionary of category configurations
        category_order (list): Order to display categories
        display_rotate_interval (float): Seconds between display rotations
        subtitle_rotate_interval (float): Seconds between subtitle rotations
        update_interval (float): Seconds between checking for new day
    """
    
    def __init__(self, plugin_id: str, config: Dict[str, Any],
                 display_manager, cache_manager, plugin_manager):
        """Initialize the of-the-day plugin."""
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        
        # Configuration
        self.update_interval = self._get_positive_float(config, 'update_interval', 3600)
        self.display_rotate_interval = self._get_positive_float(config, 'display_rotate_interval', 20)
        self.subtitle_rotate_interval = self._get_positive_float(config, 'subtitle_rotate_interval', 10)
        
        # Categories
        self.categories = config.get('categories', {})
        self.category_order = config.get('category_order', [])
        
        # State
        self.current_day = None
        self.current_items = {}
        self.current_category_index = 0
        self.rotation_state = 0  # 0 = title, 1 = content
        self.last_update = 0
        self.last_rotation_time = time.time()
        self.last_category_rotation_time = time.time()
        
        # Display state tracking (to avoid unnecessary redraws)
        self.last_displayed_category = None
        self.last_displayed_rotation_state = None
        self.display_needs_update = True  # Force initial display
        
        # Data files
        self.data_files = {}
        
        # Colors
        self.title_color = (255, 255, 255)
        self.subtitle_color = (220, 220, 220)
        self.content_color = (255, 220, 0)     # yellow — crisp & distinct from white title
        self.background_color = (0, 0, 0)

        # Cached fonts (loaded once to avoid per-frame disk I/O on Pi)
        self._title_font: Optional[ImageFont.ImageFont] = None
        self._body_font: Optional[ImageFont.ImageFont] = None
        
        # Load data files
        self._load_data_files()
        
        # Load today's items
        self._load_todays_items()
        
        # Register fonts
        self._register_fonts()
        
        self.logger.info(f"Of The Day plugin initialized with {len(self.current_items)} categories")
    
    def _get_positive_float(self, config: Dict[str, Any], key: str, default: float) -> float:
        """Coerce a config value to a positive float, falling back to default on invalid input."""
        value = config.get(key, default)
        try:
            value_f = float(value)
        except (TypeError, ValueError):
            self.logger.warning(f"Invalid {key}='{value}', using default {default}")
            return default
        if value_f <= 0:
            self.logger.warning(f"{key} must be > 0, using default {default}")
            return default
        return value_f

    def _get_fonts(self) -> Tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
        """Return cached title and body fonts, loading them on first call."""
        if self._title_font is None:
            try:
                self._title_font = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 8)
            except OSError as e:
                self.logger.warning(f"Failed to load PressStart2P font: {e}, using fallback")
                self._title_font = (
                    self.display_manager.small_font
                    if hasattr(self.display_manager, 'small_font')
                    else ImageFont.load_default()
                )
        if self._body_font is None:
            # 4x6 BDF: 6px tall → fits 3 lines in body area (vs 2 for 5x7).
            # Crisp 1-bit rendering; draw_text()/get_text_width() handle freetype.Face.
            self._body_font = getattr(self.display_manager, 'bdf_4x6_font', None)
            if self._body_font is None:
                self.logger.warning("bdf_4x6_font unavailable, falling back")
                self._body_font = getattr(self.display_manager, 'bdf_5x7_font', None)
                if self._body_font is None:
                    self._body_font = getattr(self.display_manager, 'extra_small_font',
                                              ImageFont.load_default())
        return self._title_font, self._body_font

    def _register_fonts(self) -> None:
        """Register fonts with the font manager."""
        try:
            if not hasattr(self.plugin_manager, 'font_manager'):
                return
            
            font_manager = self.plugin_manager.font_manager
            
            font_manager.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.title",
                family="press_start",
                size_px=8,
                color=self.title_color
            )
            
            font_manager.register_manager_font(
                manager_id=self.plugin_id,
                element_key=f"{self.plugin_id}.content",
                family="four_by_six",
                size_px=6,
                color=self.content_color
            )
            
            self.logger.info("Of The Day fonts registered")
        except Exception as e:
            self.logger.warning(f"Error registering fonts: {e}")
    
    def _load_data_files(self):
        """Load all data files for enabled categories.

        Merges two sources:
        1. Explicitly configured categories from self.categories (has data_file path).
        2. Categories listed in self.category_order that are missing from self.categories
           — these are auto-discovered using the conventional path
           ``of_the_day/<category_name>.json`` so that files uploaded before a
           config entry was created are still loaded.
        """
        # Build a unified view: explicit config takes priority; missing entries
        # fall back to the auto-discovered convention.
        categories_to_load = {}
        for category_name in self.category_order:
            if category_name in self.categories:
                categories_to_load[category_name] = self.categories[category_name]
            else:
                # Category is in the display order but has no config entry yet.
                # Derive the data_file path from the category name.
                self.logger.debug(
                    f"Category '{category_name}' in category_order but missing from "
                    f"categories config — auto-discovering data file."
                )
                categories_to_load[category_name] = {
                    'enabled': True,
                    'data_file': f'of_the_day/{category_name}.json',
                    'display_name': category_name.replace('_', ' ').title(),
                }
        # Also include any categories that are in self.categories but not in
        # category_order (they won't be displayed but we load them for safety).
        for category_name, category_config in self.categories.items():
            if category_name not in categories_to_load:
                categories_to_load[category_name] = category_config

        for category_name, category_config in categories_to_load.items():
            if not category_config.get('enabled', True):
                self.logger.debug(f"Skipping disabled category: {category_name}")
                continue

            data_file = category_config.get('data_file')
            if not data_file:
                self.logger.warning(f"No data file specified for category: {category_name}")
                continue

            try:
                # Try to locate the data file
                file_path = self._find_data_file(data_file)
                if not file_path:
                    self.logger.warning(f"Could not find data file: {data_file}")
                    continue

                # Load and parse JSON
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                self.data_files[category_name] = data
                self.logger.info(f"Loaded data for category '{category_name}': {len(data)} entries")

            except Exception as e:
                self.logger.error(f"Error loading data file for {category_name}: {e}")
    
    def _find_data_file(self, data_file: str) -> Optional[str]:
        """Find the data file in possible locations."""
        # Get plugin directory
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Possible paths to check (prioritize plugin directory)
        possible_paths = [
            os.path.join(plugin_dir, data_file),  # In plugin directory (preferred)
            data_file,  # Direct path (if absolute)
            os.path.join(os.getcwd(), data_file),  # Relative to cwd (fallback)
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                self.logger.info(f"Found data file at: {path}")
                return path
        
        self.logger.warning(f"Data file not found: {data_file}")
        return None
    
    def _load_todays_items(self):
        """Load items for today's date from all enabled categories."""
        today = date.today()
        
        if self.current_day == today and self.current_items:
            return  # Already loaded for today
        
        self.current_day = today
        self.current_items = {}
        self.display_needs_update = True  # Force redraw when day changes
        
        # Calculate day of year (1-365, or 1-366 for leap years)
        day_of_year = today.timetuple().tm_yday
        
        for category_name, data in self.data_files.items():
            try:
                # Find today's entry using day of year
                day_key = str(day_of_year)
                
                if day_key in data:
                    self.current_items[category_name] = data[day_key]
                    item_title = data[day_key].get('word', data[day_key].get('title', 'N/A'))
                    self.logger.info(f"Loaded item for {category_name} (day {day_of_year}): {item_title}")
                else:
                    self.logger.warning(f"No entry found for day {day_of_year} in category {category_name}")
            
            except Exception as e:
                self.logger.error(f"Error loading today's item for {category_name}: {e}")
    
    def update(self) -> None:
        """Update items if it's a new day."""
        current_time = time.time()
        
        # Check if we need to update
        if current_time - self.last_update < self.update_interval:
            return
        
        self.last_update = current_time
        
        # Check if it's a new day
        today = date.today()
        if self.current_day != today:
            self.logger.info(f"New day detected, loading items for {today}")
            self._load_todays_items()
    
    def display(self, force_clear: bool = False) -> None:
        """
        Display of-the-day content.
        
        Args:
            force_clear: If True, clear display before rendering
        """
        if not self.current_items:
            if self.last_displayed_category != "NO_DATA":
                self.last_displayed_category = "NO_DATA"
                self._display_no_data()
            return
        
        try:
            # Get enabled categories in order
            enabled_categories = [cat for cat in self.category_order 
                                if cat in self.current_items and 
                                self.categories.get(cat, {}).get('enabled', True)]
            
            if not enabled_categories:
                if self.last_displayed_category != "NO_DATA":
                    self.last_displayed_category = "NO_DATA"
                    self._display_no_data()
                return
            
            # Rotate categories
            current_time = time.time()
            category_changed = False
            if current_time - self.last_category_rotation_time >= self.display_rotate_interval:
                self.current_category_index = (self.current_category_index + 1) % len(enabled_categories)
                self.last_category_rotation_time = current_time
                self.rotation_state = 0  # Reset rotation when changing categories
                self.last_rotation_time = current_time
                category_changed = True
                self.display_needs_update = True
            
            # Get current category
            category_name = enabled_categories[self.current_category_index]
            category_config = self.categories.get(category_name, {})
            item_data = self.current_items.get(category_name, {})
            
            # Rotate display content
            rotation_changed = False
            if current_time - self.last_rotation_time >= self.subtitle_rotate_interval:
                self.rotation_state = (self.rotation_state + 1) % 2
                self.last_rotation_time = current_time
                rotation_changed = True
                self.display_needs_update = True
            
            # Check if we need to update the display
            # Only redraw if category changed, rotation state changed, or force_clear
            if (self.display_needs_update or 
                force_clear or 
                category_changed or 
                rotation_changed or 
                self.last_displayed_category != category_name or 
                self.last_displayed_rotation_state != self.rotation_state):
                
                # Update tracking state
                self.last_displayed_category = category_name
                self.last_displayed_rotation_state = self.rotation_state
                self.display_needs_update = False
                
                # Display based on rotation state
                if self.rotation_state == 0:
                    self._display_title(category_config, item_data)
                else:
                    self._display_content(category_config, item_data)
        
        except Exception as e:
            self.logger.error(f"Error displaying of-the-day: {e}")
            if self.last_displayed_category != "ERROR":
                self.last_displayed_category = "ERROR"
                self._display_error()
    
    def _wrap_text(self, text: str, max_width: int, font, max_lines: int = 10) -> List[str]:
        """Wrap text to fit within max_width, similar to old manager."""
        if not text:
            return [""]
        lines = []
        current_line = []
        words = text.split()
        for word in words:
            test_line = ' '.join(current_line + [word]) if current_line else word
            try:
                text_width = self.display_manager.get_text_width(test_line, font)
            except Exception:
                # Fallback calculation
                if isinstance(font, ImageFont.ImageFont):
                    bbox = font.getbbox(test_line)
                    text_width = bbox[2] - bbox[0]
                else:
                    text_width = len(test_line) * 6
            if text_width <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                    current_line = [word]
                else:
                    # Word is too long - truncate it
                    truncated = word
                    while len(truncated) > 0:
                        try:
                            test_width = self.display_manager.get_text_width(truncated + "...", font)
                        except Exception:
                            if isinstance(font, ImageFont.ImageFont):
                                bbox = font.getbbox(truncated + "...")
                                test_width = bbox[2] - bbox[0]
                            else:
                                test_width = len(truncated + "...") * 6
                        if test_width <= max_width:
                            lines.append(truncated + "...")
                            break
                        truncated = truncated[:-1]
                    if not truncated:
                        lines.append(word[:10] + "...")
            if len(lines) >= max_lines:
                break
        if current_line and len(lines) < max_lines:
            lines.append(' '.join(current_line))
        return lines[:max_lines]
    
    def _draw_bdf_text(self, draw, font, text: str, x: int, y: int, color: tuple = (255, 255, 255)):
        """Draw text supporting both BDF (FreeType Face) and PIL TTF fonts, similar to old manager."""
        self.logger.debug(f"_draw_bdf_text: text='{text}', x={x}, y={y}, font={type(font).__name__}, color={color}")
        try:
            # If we have a PIL font, use native text rendering
            if isinstance(font, ImageFont.ImageFont):
                draw.text((x, y), text, fill=color, font=font)
                self.logger.debug(f"PIL text drawn: '{text}'")
                return
            
            # Try to import freetype
            try:
                import freetype
            except ImportError:
                # If freetype not available, fallback to PIL
                draw.text((x, y), text, fill=color, font=ImageFont.load_default())
                return
            
            # For BDF fonts (FreeType Face)
            if isinstance(font, freetype.Face):
                # Compute baseline from font ascender so caller can pass top-left y
                try:
                    ascender_px = font.size.ascender >> 6
                except Exception:
                    ascender_px = 0
                baseline_y = y + ascender_px
                
                # Render BDF glyphs manually
                current_x = x
                for char in text:
                    font.load_char(char)
                    bitmap = font.glyph.bitmap
                    
                    # Get glyph metrics
                    glyph_left = font.glyph.bitmap_left
                    glyph_top = font.glyph.bitmap_top
                    
                    for i in range(bitmap.rows):
                        for j in range(bitmap.width):
                            try:
                                byte_index = i * bitmap.pitch + (j // 8)
                                if byte_index < len(bitmap.buffer):
                                    byte = bitmap.buffer[byte_index]
                                    if byte & (1 << (7 - (j % 8))):
                                        # Calculate actual pixel position
                                        pixel_x = current_x + glyph_left + j
                                        pixel_y = baseline_y - glyph_top + i
                                        # Only draw if within bounds
                                        if (0 <= pixel_x < self.display_manager.width and 
                                            0 <= pixel_y < self.display_manager.height):
                                            draw.point((pixel_x, pixel_y), fill=color)
                            except IndexError:
                                continue
                    current_x += font.glyph.advance.x >> 6
        except Exception as e:
            self.logger.error(f"Error in _draw_bdf_text for text '{text}' at ({x}, {y}): {e}", exc_info=True)
            # Fallback to simple text drawing
            try:
                draw.text((x, y), text, fill=color, font=ImageFont.load_default())
            except Exception as fallback_e:
                self.logger.error(f"Fallback text drawing also failed: {fallback_e}", exc_info=True)
    
    def _display_title(self, category_config: Dict, item_data: Dict):
        """Display the title/word with subtitle, matching old manager layout."""
        # Clear display first
        self.display_manager.clear()
        
        # Use display_manager's image and draw directly
        draw = self.display_manager.draw
        
        # Load fonts - match old manager font usage
        try:
            title_font = ImageFont.truetype('assets/fonts/PressStart2P-Regular.ttf', 8)
        except Exception as e:
            self.logger.warning(f"Failed to load PressStart2P font: {e}, using fallback")
            title_font = self.display_manager.small_font if hasattr(self.display_manager, 'small_font') else ImageFont.load_default()
        
        body_font = getattr(self.display_manager, 'bdf_4x6_font', None)
        if body_font is None:
            body_font = getattr(self.display_manager, 'bdf_5x7_font', None)
            if body_font is None:
                body_font = getattr(self.display_manager, 'extra_small_font',
                                    ImageFont.load_default())
        
        # Get font heights
        try:
            title_height = self.display_manager.get_font_height(title_font)
        except Exception as e:
            self.logger.warning(f"Error getting title font height: {e}, using default 8")
            title_height = 8
        try:
            body_height = self.display_manager.get_font_height(body_font)
        except Exception as e:
            self.logger.warning(f"Error getting body font height: {e}, using default 8")
            body_height = 8
        
        # Per-category color overrides (fall back to plugin-wide defaults)
        title_color    = tuple(category_config.get('title_color',    list(self.title_color)))
        subtitle_color = tuple(category_config.get('subtitle_color', list(self.subtitle_color)))

        # Layout matching old manager: raise 5px so bottom text isn't clipped
        margin_top = 3
        margin_bottom = 1
        underline_space = 1

        # Get title/word (JSON uses "title" not "word")
        title = item_data.get('title', item_data.get('word', 'N/A'))
        
        # Get subtitle (JSON uses "subtitle")
        subtitle = item_data.get('subtitle', item_data.get('pronunciation', item_data.get('type', '')))
        
        # Calculate title width for centering
        try:
            title_width = self.display_manager.get_text_width(title, title_font)
        except Exception as e:
            self.logger.warning(f"Error calculating title width using display_manager: {e}, trying fallback")
            if isinstance(title_font, ImageFont.ImageFont):
                bbox = title_font.getbbox(title)
                title_width = bbox[2] - bbox[0]
            else:
                title_width = len(title) * 6
        
        # Center the title horizontally
        title_x = (self.display_manager.width - title_width) // 2
        title_y = margin_top
        
        # Draw title using display_manager.draw_text (proper method)
        self.logger.info(f"Drawing title '{title}' at ({title_x}, {title_y}) with font type {type(title_font).__name__}")
        try:
            self.display_manager.draw_text(
                title,
                x=title_x,
                y=title_y,
                color=title_color,
                font=title_font
            )
            self.logger.debug(f"Title '{title}' drawn using display_manager.draw_text")
        except Exception as e:
            self.logger.error(f"Error drawing title '{title}': {e}", exc_info=True)

        # Draw underline below title (like old manager)
        underline_y = title_y + title_height + 1
        underline_x_start = title_x
        underline_x_end = title_x + title_width
        draw.line([(underline_x_start, underline_y), (underline_x_end, underline_y)],
                 fill=title_color, width=1)

        # Draw subtitle below underline (centered, like old manager)
        if subtitle:
            # Wrap subtitle text if needed
            available_width = self.display_manager.width - 4
            wrapped_subtitle_lines = self._wrap_text(subtitle, available_width, body_font, max_lines=3)
            actual_subtitle_lines = [line for line in wrapped_subtitle_lines if line.strip()]

            if actual_subtitle_lines:
                # Calculate spacing - similar to old manager's dynamic spacing
                total_subtitle_height = len(actual_subtitle_lines) * body_height
                available_space = self.display_manager.height - underline_y - margin_bottom
                space_after_underline = max(2, (available_space - total_subtitle_height) // 2)

                subtitle_start_y = underline_y + space_after_underline + underline_space
                current_y = subtitle_start_y

                for line in actual_subtitle_lines:
                    if line.strip():
                        # Center each line of subtitle
                        try:
                            line_width = self.display_manager.get_text_width(line, body_font)
                        except Exception:
                            if isinstance(body_font, ImageFont.ImageFont):
                                bbox = body_font.getbbox(line)
                                line_width = bbox[2] - bbox[0]
                            else:
                                line_width = len(line) * 6
                        line_x = (self.display_manager.width - line_width) // 2

                        # Use display_manager.draw_text for subtitle
                        self.display_manager.draw_text(
                            line,
                            x=line_x,
                            y=current_y,
                            color=subtitle_color,
                            font=body_font
                        )
                        current_y += body_height + 1
        
        self.display_manager.update_display()
    
    def _display_content(self, category_config: Dict, item_data: Dict):
        """Display the definition/content, matching old manager layout."""
        # Clear display first
        self.display_manager.clear()
        
        # Use display_manager's image and draw directly
        draw = self.display_manager.draw
        
        # Load fonts (cached after first call to avoid per-frame disk I/O)
        title_font, body_font = self._get_fonts()
        
        # Get font heights
        try:
            title_height = self.display_manager.get_font_height(title_font)
        except Exception:
            title_height = 8
        try:
            body_height = self.display_manager.get_font_height(body_font)
        except Exception:
            body_height = 8
        
        # Per-category color overrides (fall back to plugin-wide defaults)
        title_color   = tuple(category_config.get('title_color',   list(self.title_color)))
        content_color = tuple(category_config.get('content_color', list(self.content_color)))

        # Layout matching old manager: raise 5px so bottom text isn't clipped
        margin_top = 3
        margin_bottom = 1
        underline_space = 1

        # Get title/word (JSON uses "title")
        title = item_data.get('title', item_data.get('word', 'N/A'))
        self.logger.debug(f"Displaying content for title: {title}")
        
        # Get description (JSON uses "description")
        description = item_data.get('description', item_data.get('definition', item_data.get('content', item_data.get('text', 'No content'))))
        
        # Calculate title width for centering (for underline placement)
        try:
            title_width = self.display_manager.get_text_width(title, title_font)
        except Exception:
            if isinstance(title_font, ImageFont.ImageFont):
                bbox = title_font.getbbox(title)
                title_width = bbox[2] - bbox[0]
            else:
                title_width = len(title) * 6
        
        # Center the title horizontally (same position as in _display_title)
        title_x = (self.display_manager.width - title_width) // 2
        title_y = margin_top
        
        # Draw title using display_manager.draw_text (same as title screen)
        self.display_manager.draw_text(
            title,
            x=title_x,
            y=title_y,
            color=title_color,
            font=title_font
        )

        # Draw underline below title (same as title screen)
        underline_y = title_y + title_height + 1
        underline_x_start = title_x
        underline_x_end = title_x + title_width
        draw.line([(underline_x_start, underline_y), (underline_x_end, underline_y)],
                 fill=title_color, width=1)
        
        # Wrap description text
        available_width = self.display_manager.width - 4
        max_lines = 10
        wrapped_lines = self._wrap_text(description, available_width, body_font, max_lines=max_lines)
        actual_body_lines = [line for line in wrapped_lines if line.strip()]
        
        if actual_body_lines:
            # Calculate dynamic spacing - similar to old manager
            num_body_lines = len(actual_body_lines)
            body_content_height = num_body_lines * body_height
            available_space = self.display_manager.height - underline_y - margin_bottom
            
            if body_content_height < available_space:
                # Distribute extra space: some after underline, rest between lines
                extra_space = available_space - body_content_height
                space_after_underline = max(1, int(extra_space * 0.15))
                space_between_lines = max(1, int(extra_space * 0.7 / max(1, num_body_lines - 1))) if num_body_lines > 1 else 0
            else:
                # Tight spacing — minimize gap to fit max lines with 4x6 font
                space_after_underline = 1
                space_between_lines = 1
            
            # Draw body text with dynamic spacing
            body_start_y = underline_y + space_after_underline + underline_space - 1  # -1 shifts body 2px up vs old manager
            current_y = body_start_y
            
            for i, line in enumerate(actual_body_lines):
                if line.strip():
                    # Center each line of body text (like old manager)
                    try:
                        line_width = self.display_manager.get_text_width(line, body_font)
                    except Exception:
                        if isinstance(body_font, ImageFont.ImageFont):
                            bbox = body_font.getbbox(line)
                            line_width = bbox[2] - bbox[0]
                        else:
                            line_width = len(line) * 6
                    line_x = (self.display_manager.width - line_width) // 2
                    
                    # Use display_manager.draw_text for description
                    self.display_manager.draw_text(
                        line,
                        x=line_x,
                        y=current_y,
                        color=content_color,
                        font=body_font
                    )
                    
                    # Move to next line position
                    if i < len(actual_body_lines) - 1:  # Not the last line
                        current_y += body_height + space_between_lines
        
        self.display_manager.update_display()
    
    def _display_no_data(self):
        """Display message when no data is available."""
        self.display_manager.clear()
        img = Image.new('RGB', (self.display_manager.width,
                               self.display_manager.height),
                       self.background_color)
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype('assets/fonts/4x6-font.ttf', 8)
        except OSError as e:
            self.logger.warning(f"Failed to load 4x6 font: {e}, using fallback")
            font = ImageFont.load_default()

        draw.text((5, 12), "No Data", font=font, fill=(200, 200, 200))

        self.display_manager.image = img.copy()
        self.display_manager.update_display()

    def _display_error(self):
        """Display error message."""
        self.display_manager.clear()
        img = Image.new('RGB', (self.display_manager.width,
                               self.display_manager.height),
                       self.background_color)
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype('assets/fonts/4x6-font.ttf', 8)
        except OSError as e:
            self.logger.warning(f"Failed to load 4x6 font: {e}, using fallback")
            font = ImageFont.load_default()

        draw.text((5, 12), "Error", font=font, fill=(255, 0, 0))

        self.display_manager.image = img.copy()
        self.display_manager.update_display()
    
    def get_display_duration(self) -> float:
        """Get display duration from config."""
        return self.config.get('display_duration', 40.0)
    
    def get_info(self) -> Dict[str, Any]:
        """Return plugin info for web UI."""
        info = super().get_info()
        info.update({
            'current_day': str(self.current_day) if self.current_day else None,
            'categories_loaded': len(self.current_items),
            'enabled_categories': [cat for cat in self.category_order 
                                  if self.categories.get(cat, {}).get('enabled', True)]
        })
        return info
    
    def on_config_change(self, config: Dict[str, Any]) -> None:
        """Handle configuration changes (called when user updates config via web UI)."""
        self.logger.info("Config changed, reloading categories")

        # Update configuration
        self.config = config
        self.update_interval = self._get_positive_float(config, 'update_interval', 3600)
        self.display_rotate_interval = self._get_positive_float(config, 'display_rotate_interval', 20)
        self.subtitle_rotate_interval = self._get_positive_float(config, 'subtitle_rotate_interval', 10)
        self.categories = config.get('categories', {})
        self.category_order = config.get('category_order', [])

        # Reset state
        self.current_category_index = 0
        self.rotation_state = 0
        self.display_needs_update = True

        # Reload data files (respects enabled status)
        self.data_files = {}
        self._load_data_files()

        # Reload today's items
        self.current_day = None  # Force reload
        self._load_todays_items()

        self.logger.info(f"Config reloaded: {len(self.data_files)} categories enabled")

    def cleanup(self) -> None:
        """Cleanup resources."""
        self.current_items = {}
        self.data_files = {}
        self.logger.info("Of The Day plugin cleaned up")

