"""
Display Renderer for Stock Ticker Plugin

Handles all display creation, layout, and rendering logic for both
scrolling and static display modes.
"""

import os
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

# Import common utilities
from src.common import ScrollHelper, LogoHelper, TextHelper


class StockDisplayRenderer:
    """Handles rendering of stock and cryptocurrency displays."""
    
    def __init__(self, config: Dict[str, Any], display_width: int, display_height: int, logger):
        """Initialize the display renderer."""
        self.config = config
        self.display_width = display_width
        self.display_height = display_height
        self.logger = logger
        
        # Display configuration
        self.toggle_chart = config.get('display', {}).get('toggle_chart', True)
        
        # Load colors from customization structure (organized by element: symbol, price, price_delta)
        # Support both new format (customization.stocks.*) and old format (top-level) for backwards compatibility
        customization = config.get('customization', {})
        stocks_custom = customization.get('stocks', {})
        crypto_custom = customization.get('crypto', {})
        
        # Stock colors - new format: customization.stocks.symbol/price/price_delta
        # Old format fallback: top-level text_color, positive_color, negative_color
        # Ensure all color values are integers (RGB values from config might be floats)
        if stocks_custom.get('symbol') and 'text_color' in stocks_custom['symbol']:
            # New format: separate colors for symbol and price
            symbol_color_list = stocks_custom['symbol'].get('text_color', [255, 255, 255])
            price_color_list = stocks_custom.get('price', {}).get('text_color', [255, 255, 255])
            self.symbol_text_color = tuple(int(c) for c in symbol_color_list)
            self.price_text_color = tuple(int(c) for c in price_color_list)
        else:
            # Old format: shared text_color for symbol and price
            old_text_color_list = config.get('text_color', [255, 255, 255])
            old_text_color = tuple(int(c) for c in old_text_color_list)
            self.symbol_text_color = old_text_color
            self.price_text_color = old_text_color
        
        price_delta_custom = stocks_custom.get('price_delta', {})
        if price_delta_custom:
            positive_color_list = price_delta_custom.get('positive_color', [0, 255, 0])
            negative_color_list = price_delta_custom.get('negative_color', [255, 0, 0])
            self.positive_color = tuple(int(c) for c in positive_color_list)
            self.negative_color = tuple(int(c) for c in negative_color_list)
        else:
            # Old format fallback
            positive_color_list = config.get('positive_color', [0, 255, 0])
            negative_color_list = config.get('negative_color', [255, 0, 0])
            self.positive_color = tuple(int(c) for c in positive_color_list)
            self.negative_color = tuple(int(c) for c in negative_color_list)
        
        # Crypto colors - new format: customization.crypto.symbol/price/price_delta
        # Old format fallback: customization.crypto.text_color, etc.
        if crypto_custom.get('symbol') and 'text_color' in crypto_custom['symbol']:
            # New format: separate colors for symbol and price
            crypto_symbol_color_list = crypto_custom['symbol'].get('text_color', [255, 215, 0])
            crypto_price_color_list = crypto_custom.get('price', {}).get('text_color', [255, 215, 0])
            self.crypto_symbol_text_color = tuple(int(c) for c in crypto_symbol_color_list)
            self.crypto_price_text_color = tuple(int(c) for c in crypto_price_color_list)
        else:
            # Old format: shared text_color for symbol and price
            old_crypto_text_color_list = crypto_custom.get('text_color', [255, 215, 0])
            old_crypto_text_color = tuple(int(c) for c in old_crypto_text_color_list)
            self.crypto_symbol_text_color = old_crypto_text_color
            self.crypto_price_text_color = old_crypto_text_color
        
        crypto_price_delta_custom = crypto_custom.get('price_delta', {})
        if crypto_price_delta_custom:
            crypto_positive_color_list = crypto_price_delta_custom.get('positive_color', [0, 255, 0])
            crypto_negative_color_list = crypto_price_delta_custom.get('negative_color', [255, 0, 0])
            self.crypto_positive_color = tuple(int(c) for c in crypto_positive_color_list)
            self.crypto_negative_color = tuple(int(c) for c in crypto_negative_color_list)
        else:
            # Old format fallback
            crypto_positive_color_list = crypto_custom.get('positive_color', [0, 255, 0])
            crypto_negative_color_list = crypto_custom.get('negative_color', [255, 0, 0])
            self.crypto_positive_color = tuple(int(c) for c in crypto_positive_color_list)
            self.crypto_negative_color = tuple(int(c) for c in crypto_negative_color_list)
        
        # Initialize helpers
        self.logo_helper = LogoHelper(display_width, display_height, logger=logger)
        self.text_helper = TextHelper(logger=self.logger)
        
        # Initialize scroll helper
        self.scroll_helper = ScrollHelper(display_width, display_height, logger)
        
        # Load custom fonts from config
        # Fonts are under customization.stocks/crypto.symbol/price/price_delta
        # For backwards compatibility, try to load from customization.fonts first
        fonts_config = customization.get('fonts', {})
        if fonts_config:
            # Old format: fonts at customization.fonts level (shared for stocks and crypto)
            self.symbol_font = self._load_custom_font_from_element_config(fonts_config.get('symbol', {}))
            self.price_font = self._load_custom_font_from_element_config(fonts_config.get('price', {}))
            self.price_delta_font = self._load_custom_font_from_element_config(fonts_config.get('price_delta', {}))
        else:
            # New format: fonts at customization.stocks/crypto.symbol/price/price_delta
            # Use stocks font config (crypto can override later if needed, but currently shares fonts)
            stocks_custom = customization.get('stocks', {})
            self.symbol_font = self._load_custom_font_from_element_config(stocks_custom.get('symbol', {}))
            self.price_font = self._load_custom_font_from_element_config(stocks_custom.get('price', {}))
            self.price_delta_font = self._load_custom_font_from_element_config(stocks_custom.get('price_delta', {}))
    
    def _load_custom_font_from_element_config(self, element_config: Dict[str, Any]) -> ImageFont.FreeTypeFont:
        """
        Load a custom font from an element configuration dictionary.
        
        Args:
            element_config: Configuration dict for a single element (symbol, price, or price_delta)
                           containing 'font' and 'font_size' keys
            
        Returns:
            PIL ImageFont object
        """
        # Get font name and size, with defaults
        font_name = element_config.get('font', 'PressStart2P-Regular.ttf')
        font_size = int(element_config.get('font_size', 8))  # Ensure integer for PIL
        
        # Build font path
        font_path = os.path.join('assets', 'fonts', font_name)
        
        # Try to load the font
        try:
            if os.path.exists(font_path):
                # Try loading as TTF first (works for both TTF and some BDF files with PIL)
                if font_path.lower().endswith('.ttf'):
                    font = ImageFont.truetype(font_path, font_size)
                    self.logger.debug(f"Loaded font: {font_name} at size {font_size}")
                    return font
                elif font_path.lower().endswith('.bdf'):
                    # PIL's ImageFont.truetype() can sometimes handle BDF files
                    # If it fails, we'll fall through to the default font
                    try:
                        font = ImageFont.truetype(font_path, font_size)
                        self.logger.debug(f"Loaded BDF font: {font_name} at size {font_size}")
                        return font
                    except Exception:
                        self.logger.warning(f"Could not load BDF font {font_name} with PIL, using default")
                        # Fall through to default
                else:
                    self.logger.warning(f"Unknown font file type: {font_name}, using default")
            else:
                self.logger.warning(f"Font file not found: {font_path}, using default")
        except Exception as e:
            self.logger.error(f"Error loading font {font_name}: {e}, using default")
        
        # Fall back to default font
        default_font_path = os.path.join('assets', 'fonts', 'PressStart2P-Regular.ttf')
        try:
            if os.path.exists(default_font_path):
                return ImageFont.truetype(default_font_path, font_size)
            else:
                self.logger.warning("Default font not found, using PIL default")
                return ImageFont.load_default()
        except Exception as e:
            self.logger.error(f"Error loading default font: {e}")
            return ImageFont.load_default()
    
    def create_stock_display(self, symbol: str, data: Dict[str, Any]) -> Image.Image:
        """Create a display image for a single stock or crypto - matching old stock manager layout exactly."""
        # Create a wider image for scrolling - adjust width based on chart toggle
        # Match old stock_manager: width = int(self.display_manager.matrix.width * (2 if self.toggle_chart else 1.5))
        # Ensure dimensions are integers
        width = int(self.display_width * (2 if self.toggle_chart else 1.5))
        height = int(self.display_height)
        image = Image.new('RGB', (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        is_crypto = data.get('is_crypto', False)
        
        # Draw large stock/crypto logo on the left
        logo = self._get_stock_logo(symbol, is_crypto)
        if logo:
            # Position logo on the left side with minimal spacing - matching old stock_manager
            # Ensure positions are integers
            logo_x = 2  # Small margin from left edge
            logo_y = int((height - logo.height) // 2)
            image.paste(logo, (int(logo_x), int(logo_y)), logo)
        
        # Use custom fonts loaded from config
        symbol_font = self.symbol_font
        price_font = self.price_font
        change_font = self.price_delta_font
        
        # Create text elements
        display_symbol = symbol.replace('-USD', '') if is_crypto else symbol
        symbol_text = display_symbol
        price_text = f"${data['price']:.2f}"
        
        # Build change text based on show_change and show_percentage flags
        # Get flags from config (stock-specific or crypto-specific)
        if is_crypto:
            show_change = self.config.get('crypto', {}).get('show_change', True)
            show_percentage = self.config.get('crypto', {}).get('show_percentage', True)
        else:
            show_change = self.config.get('show_change', True)
            show_percentage = self.config.get('show_percentage', True)
        
        # Build change text components
        change_parts = []
        if show_change:
            change_parts.append(f"{data['change']:+.2f}")
        if show_percentage:
            # Use change_percent if available, otherwise calculate from change and open
            if 'change_percent' in data:
                change_parts.append(f"({data['change_percent']:+.1f}%)")
            elif 'open' in data and data['open'] > 0:
                change_percent = (data['change'] / data['open']) * 100
                change_parts.append(f"({change_percent:+.1f}%)")
        
        change_text = " ".join(change_parts) if change_parts else ""
        
        # Get colors based on change
        if data['change'] >= 0:
            change_color = self.positive_color if not is_crypto else self.crypto_positive_color
        else:
            change_color = self.negative_color if not is_crypto else self.crypto_negative_color
        
        # Use symbol color for symbol, price color for price
        symbol_color = self.symbol_text_color if not is_crypto else self.crypto_symbol_text_color
        price_color = self.price_text_color if not is_crypto else self.crypto_price_text_color
        
        # Calculate text dimensions for proper spacing (matching old stock manager)
        symbol_bbox = draw.textbbox((0, 0), symbol_text, font=symbol_font)
        price_bbox = draw.textbbox((0, 0), price_text, font=price_font)
        
        # Only calculate change_bbox if change_text is not empty
        if change_text:
            change_bbox = draw.textbbox((0, 0), change_text, font=change_font)
            change_height = int(change_bbox[3] - change_bbox[1])
        else:
            change_bbox = (0, 0, 0, 0)
            change_height = 0
        
        # Calculate total height needed - adjust gaps based on chart toggle
        # Match old stock_manager: text_gap = 2 if self.toggle_chart else 1
        text_gap = 2 if self.toggle_chart else 1
        # Only add change height and gap if change is shown
        change_gap = text_gap if change_text else 0
        symbol_height = int(symbol_bbox[3] - symbol_bbox[1])
        price_height = int(price_bbox[3] - price_bbox[1])
        total_text_height = symbol_height + price_height + change_height + (text_gap + change_gap)  # Account for gaps between elements
        
        # Calculate starting y position to center all text
        start_y = int((height - total_text_height) // 2)
        
        # Calculate center x position for the column - adjust based on chart toggle
        # Match old stock_manager exactly
        if self.toggle_chart:
            # When chart is enabled, center text more to the left
            column_x = int(width / 2.85)
        else:
            # When chart is disabled, position text with more space from logo
            column_x = int(width / 2.2)
        
        # Draw symbol
        symbol_width = int(symbol_bbox[2] - symbol_bbox[0])
        symbol_x = int(column_x - (symbol_width / 2))
        draw.text((symbol_x, start_y), symbol_text, font=symbol_font, fill=symbol_color)
        
        # Draw price
        price_width = int(price_bbox[2] - price_bbox[0])
        price_x = int(column_x - (price_width / 2))
        symbol_height = int(symbol_bbox[3] - symbol_bbox[1])
        price_y = int(start_y + symbol_height + text_gap)  # Adjusted gap
        draw.text((price_x, price_y), price_text, font=price_font, fill=price_color)
        
        # Draw change with color based on value (only if change_text is not empty)
        if change_text:
            change_width = int(change_bbox[2] - change_bbox[0])
            change_x = int(column_x - (change_width / 2))
            price_height = int(price_bbox[3] - price_bbox[1])
            change_y = int(price_y + price_height + text_gap)  # Adjusted gap
            draw.text((change_x, change_y), change_text, font=change_font, fill=change_color)
        
        # Draw mini chart on the right only if toggle_chart is enabled
        if self.toggle_chart and 'price_history' in data and len(data['price_history']) >= 2:
            self._draw_mini_chart(draw, data['price_history'], width, height, change_color)
        
        return image
    
    def create_static_display(self, symbol: str, data: Dict[str, Any]) -> Image.Image:
        """Create a static display for one stock/crypto (no scrolling)."""
        # Ensure dimensions are integers
        image = Image.new('RGB', (int(self.display_width), int(self.display_height)), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        is_crypto = data.get('is_crypto', False)
        
        # Draw logo
        logo = self._get_stock_logo(symbol, is_crypto)
        if logo:
            # Ensure positions are integers
            logo_x = 5
            logo_y = int((int(self.display_height) - logo.height) // 2)
            image.paste(logo, (int(logo_x), int(logo_y)), logo)
        
        # Use custom fonts loaded from config
        symbol_font = self.symbol_font
        price_font = self.price_font
        change_font = self.price_delta_font
        
        # Create text
        display_symbol = symbol.replace('-USD', '') if is_crypto else symbol
        symbol_text = display_symbol
        price_text = f"${data['price']:.2f}"
        
        # Build change text based on show_change and show_percentage flags
        if is_crypto:
            show_change = self.config.get('crypto', {}).get('show_change', True)
            show_percentage = self.config.get('crypto', {}).get('show_percentage', True)
        else:
            show_change = self.config.get('show_change', True)
            show_percentage = self.config.get('show_percentage', True)
        
        # Build change text components
        change_parts = []
        if show_change:
            change_parts.append(f"{data['change']:+.2f}")
        if show_percentage:
            if 'change_percent' in data:
                change_parts.append(f"({data['change_percent']:+.1f}%)")
            elif 'open' in data and data['open'] > 0:
                change_percent = (data['change'] / data['open']) * 100
                change_parts.append(f"({change_percent:+.1f}%)")
        
        change_text = " ".join(change_parts) if change_parts else ""
        
        # Get colors
        if data['change'] >= 0:
            change_color = self.positive_color if not is_crypto else self.crypto_positive_color
        else:
            change_color = self.negative_color if not is_crypto else self.crypto_negative_color
        
        # Use symbol color for symbol, price color for price
        symbol_color = self.symbol_text_color if not is_crypto else self.crypto_symbol_text_color
        price_color = self.price_text_color if not is_crypto else self.crypto_price_text_color
        
        # Calculate positions
        symbol_bbox = draw.textbbox((0, 0), symbol_text, font=symbol_font)
        price_bbox = draw.textbbox((0, 0), price_text, font=price_font)
        
        # Only calculate change_bbox if change_text is not empty
        if change_text:
            change_bbox = draw.textbbox((0, 0), change_text, font=change_font)
        else:
            change_bbox = (0, 0, 0, 0)
        
        # Center everything - ensure integer
        center_x = int(self.display_width) // 2
        
        # Draw symbol
        symbol_width = int(symbol_bbox[2] - symbol_bbox[0])
        symbol_x = int(center_x - (symbol_width / 2))
        draw.text((symbol_x, 5), symbol_text, font=symbol_font, fill=symbol_color)
        
        # Draw price
        price_width = int(price_bbox[2] - price_bbox[0])
        price_x = int(center_x - (price_width / 2))
        draw.text((price_x, 15), price_text, font=price_font, fill=price_color)
        
        # Draw change (only if change_text is not empty)
        if change_text:
            change_width = int(change_bbox[2] - change_bbox[0])
            change_x = int(center_x - (change_width / 2))
            draw.text((change_x, 25), change_text, font=change_font, fill=change_color)
        
        return image
    
    def create_scrolling_display(self, all_data: Dict[str, Any]) -> Image.Image:
        """Create a wide scrolling image with all stocks/crypto - matching old stock_manager spacing."""
        if not all_data:
            return self._create_error_display()
        
        # Calculate total width needed - match old stock_manager spacing logic
        # Ensure dimensions are integers
        width = int(self.display_width)
        height = int(self.display_height)
        
        # Create individual stock displays
        stock_displays = []
        for symbol, data in all_data.items():
            display = self.create_stock_display(symbol, data)
            stock_displays.append(display)
        
        # Calculate spacing - match old stock_manager exactly
        # Old code: stock_gap = width // 6, element_gap = width // 8
        stock_gap = int(width // 6)  # Reduced gap between stocks
        element_gap = int(width // 8)  # Reduced gap between elements within a stock
        
        # Calculate total width - match old stock_manager calculation
        # Old code: total_width = sum(width * 2 for _ in symbols) + stock_gap * (len(symbols) - 1) + element_gap * (len(symbols) * 2 - 1)
        # But each display already has its own width (width * 2 or width * 1.5), so we sum display widths
        # Ensure all values are integers
        total_width = sum(int(display.width) for display in stock_displays)
        total_width += int(stock_gap) * (len(stock_displays) - 1)
        total_width += int(element_gap) * (len(stock_displays) * 2 - 1)
        
        # Create scrolling image - ensure dimensions are integers
        scrolling_image = Image.new('RGB', (int(total_width), int(height)), (0, 0, 0))
        
        # Paste all stock displays with spacing - match old stock_manager logic
        # Old code: current_x = width (starts with display width gap)
        current_x = int(width)  # Add initial gap before the first stock
        
        for i, display in enumerate(stock_displays):
            # Paste this stock image into the full image - ensure position is integer tuple
            scrolling_image.paste(display, (int(current_x), 0))
            
            # Move to next position with consistent spacing
            # Old code: current_x += width * 2 + element_gap
            current_x += int(display.width) + int(element_gap)
            
            # Add extra gap between stocks (except after the last stock)
            if i < len(stock_displays) - 1:
                current_x += int(stock_gap)
        
        return scrolling_image
    
    def _get_stock_logo(self, symbol: str, is_crypto: bool = False) -> Optional[Image.Image]:
        """Get stock or crypto logo image - matching old stock manager sizing."""
        try:
            if is_crypto:
                # Try crypto icons first
                logo_path = f"assets/stocks/crypto_icons/{symbol}.png"
            else:
                # Try stock icons
                logo_path = f"assets/stocks/ticker_icons/{symbol}.png"
            
            # Use same sizing as old stock manager (display_width/1.2, display_height/1.2)
            max_size = min(int(self.display_width / 1.2), int(self.display_height / 1.2))
            return self.logo_helper.load_logo(symbol, logo_path, max_size, max_size)
            
        except (OSError, IOError) as e:
            self.logger.warning("Error loading logo for %s: %s", symbol, e)
            return None
    
    def _get_stock_color(self, change: float) -> Tuple[int, int, int]:
        """Get color based on stock performance - matching old stock manager."""
        if change > 0:
            return (0, 255, 0)  # Green for positive
        elif change < 0:
            return (255, 0, 0)  # Red for negative
        return (255, 255, 0)  # Yellow for no change
    
    def _draw_mini_chart(self, draw: ImageDraw.Draw, price_history: List[Dict], 
                        width: int, height: int, color: Tuple[int, int, int]) -> None:
        """Draw a mini price chart on the right side of the display - matching old stock_manager exactly."""
        if len(price_history) < 2:
            return
        
        # Chart dimensions - match old stock_manager exactly
        # Old code: chart_width = int(width // 2.5), chart_height = height // 1.5
        # Ensure all dimensions are integers
        chart_width = int(width / 2.5)  # Reduced from width//2.5 to prevent overlap
        chart_height = int(height / 1.5)
        chart_x = int(width - chart_width - 4)  # 4px margin from right edge
        chart_y = int((height - chart_height) / 2)
        
        # Extract prices - match old stock_manager exactly
        prices = [point['price'] for point in price_history if 'price' in point]
        if len(prices) < 2:
            return
        
        # Find min and max prices for scaling - match old stock_manager
        min_price = min(prices)
        max_price = max(prices)
        
        # Add padding to avoid flat lines when prices are very close - match old stock_manager
        price_range = max_price - min_price
        if price_range < 0.01:
            min_price -= 0.01
            max_price += 0.01
            price_range = 0.02
        
        if price_range == 0:
            # All prices are the same, draw a horizontal line
            y = int(chart_y + chart_height / 2)
            draw.line([(chart_x, y), (chart_x + chart_width, y)], fill=color, width=1)
            return
        
        # Calculate points for the line - match old stock_manager exactly
        # Ensure all coordinates are integers
        points = []
        for i, price in enumerate(prices):
            x = int(chart_x + (i * chart_width) / (len(prices) - 1))
            y = int(chart_y + chart_height - int(((price - min_price) / price_range) * chart_height))
            points.append((x, y))
        
        # Draw lines between points - match old stock_manager
        if len(points) > 1:
            for i in range(len(points) - 1):
                draw.line([points[i], points[i + 1]], fill=color, width=1)
    
    def _create_error_display(self) -> Image.Image:
        """Create an error display when no data is available."""
        # Ensure dimensions are integers
        image = Image.new('RGB', (int(self.display_width), int(self.display_height)), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Use symbol font for error display
        error_font = self.symbol_font
        
        # Draw error message
        error_text = "No Data Available"
        bbox = draw.textbbox((0, 0), error_text, font=error_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # Ensure dimensions are integers
        x = (int(self.display_width) - text_width) // 2
        y = (int(self.display_height) - text_height) // 2
        
        draw.text((x, y), error_text, font=error_font, fill=(255, 0, 0))
        
        return image
    
    def set_toggle_chart(self, enabled: bool) -> None:
        """Set whether to show mini charts."""
        self.toggle_chart = enabled
        self.logger.debug("Chart toggle set to: %s", enabled)
    
    def get_scroll_helper(self) -> ScrollHelper:
        """Get the scroll helper instance."""
        return self.scroll_helper
