import time
import logging
import requests
import json
import random
from typing import Dict, Any, List, Tuple
from datetime import datetime
import os
import urllib.parse
import re
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import hashlib
from .cache_manager import CacheManager
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Import the API counter function from web interface
try:
    from web_interface_v2 import increment_api_counter
except ImportError:
    # Fallback if web interface is not available
    def increment_api_counter(kind: str, count: int = 1):
        pass

# Get logger without configuring
logger = logging.getLogger(__name__)

class StockManager:
    def __init__(self, config: Dict[str, Any], display_manager):
        self.config = config
        self.display_manager = display_manager
        self.stocks_config = config.get('stocks', {})
        self.crypto_config = config.get('crypto', {})
        self.last_update = 0
        self.stock_data = {}
        self.current_stock_index = 0
        self.scroll_position = 0
        self.cached_text_image = None
        self.cached_text = None
        self.cache_manager = CacheManager()
        
        # Initialize logo cache to prevent repeated file I/O
        self._logo_cache = {}
        
        # Get scroll settings from config with faster defaults
        self.scroll_speed = self.stocks_config.get('scroll_speed', 1)
        self.scroll_delay = self.stocks_config.get('scroll_delay', 0.01)
        
        # Get chart toggle setting from config
        self.toggle_chart = self.stocks_config.get('toggle_chart', False)
        
        # Dynamic duration settings
        self.dynamic_duration_enabled = self.stocks_config.get('dynamic_duration', True)
        self.min_duration = self.stocks_config.get('min_duration', 30)
        self.max_duration = self.stocks_config.get('max_duration', 300)
        self.duration_buffer = self.stocks_config.get('duration_buffer', 0.1)
        self.dynamic_duration = 60  # Default duration in seconds
        self.total_scroll_width = 0  # Track total width for dynamic duration calculation
        
        # Initialize frame rate tracking
        self.frame_count = 0
        self.last_frame_time = time.time()
        self.last_fps_log_time = time.time()
        self.frame_times = []
        
        # Set up the ticker icons directory
        self.ticker_icons_dir = os.path.join('assets', 'stocks', 'ticker_icons')
        if not os.path.exists(self.ticker_icons_dir):
            logger.warning(f"Ticker icons directory not found: {self.ticker_icons_dir}")
            
        # Set up the crypto icons directory
        self.crypto_icons_dir = os.path.join('assets', 'stocks', 'crypto_icons')
        if not os.path.exists(self.crypto_icons_dir):
            logger.warning(f"Crypto icons directory not found: {self.crypto_icons_dir}")
            
        # Set up the logo directory for external logos
        self.logo_dir = os.path.join('assets', 'stocks')
        if not os.path.exists(self.logo_dir):
            try:
                os.makedirs(self.logo_dir, mode=0o755, exist_ok=True)
                logger.info(f"Created logo directory: {self.logo_dir}")
            except (PermissionError, OSError) as e:
                logger.error(f"Cannot create logo directory '{self.logo_dir}': {str(e)}")
                self.logo_dir = None
        
        self.headers = {
            'User-Agent': 'LEDMatrix/1.0 (https://github.com/yourusername/LEDMatrix; contact@example.com)',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }
        
        # Set up session with retry logic
        self.session = requests.Session()
        retry_strategy = Retry(
            total=5,  # increased number of retries
            backoff_factor=1,  # increased backoff factor
            status_forcelist=[429, 500, 502, 503, 504],  # added 429 to retry list
            allowed_methods=["GET", "HEAD", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        # Initialize with first update
        self.update_stock_data()
        
    def _get_stock_color(self, symbol: str) -> Tuple[int, int, int]:
        """Get color based on stock performance."""
        if symbol not in self.stock_data:
            return (255, 255, 255)  # White for unknown
        
        change = self.stock_data[symbol].get('change', 0)
        if change > 0:
            return (0, 255, 0)  # Green for positive
        elif change < 0:
            return (255, 0, 0)  # Red for negative
        return (255, 255, 0)  # Yellow for no change
        
    def _extract_json_from_html(self, html: str) -> Dict:
        """Extract the JSON data from Yahoo Finance HTML."""
        try:
            # Look for the finance data in the HTML
            patterns = [
                r'root\.App\.main = (.*?);\s*</script>',
                r'"QuotePageStore":\s*({.*?}),\s*"',
                r'{"regularMarketPrice":.*?"regularMarketChangePercent".*?}'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    json_str = match.group(1)
                    try:
                        data = json.loads(json_str)
                        if isinstance(data, dict):
                            if 'context' in data:
                                # First pattern matched
                                context = data.get('context', {})
                                dispatcher = context.get('dispatcher', {})
                                stores = dispatcher.get('stores', {})
                                quote_data = stores.get('QuoteSummaryStore', {})
                                if quote_data:
                                    return quote_data
                            else:
                                # Direct quote data
                                return data
                    except json.JSONDecodeError:
                        continue
                        
            # If we get here, try one last attempt to find the price data directly
            price_match = re.search(r'"regularMarketPrice":{"raw":([\d.]+)', html)
            change_match = re.search(r'"regularMarketChangePercent":{"raw":([-\d.]+)', html)
            prev_close_match = re.search(r'"regularMarketPreviousClose":{"raw":([\d.]+)', html)
            name_match = re.search(r'"shortName":"([^"]+)"', html)
            
            if price_match:
                return {
                    "price": {
                        "regularMarketPrice": {"raw": float(price_match.group(1))},
                        "regularMarketChangePercent": {"raw": float(change_match.group(1)) if change_match else 0},
                        "regularMarketPreviousClose": {"raw": float(prev_close_match.group(1)) if prev_close_match else 0},
                        "shortName": name_match.group(1) if name_match else None
                    }
                }
            
            return {}
        except Exception as e:
            logger.error(f"Error extracting JSON data: {e}")
            return {}
        
    def _fetch_stock_data(self, symbol: str, is_crypto: bool = False) -> Dict[str, Any]:
        """Fetch stock or crypto data from Yahoo Finance public API."""
        # Try to get cached data first
        cache_key = 'crypto' if is_crypto else 'stocks'
        cached_data = self.cache_manager.get(cache_key)
        if cached_data and symbol in cached_data:
            logger.info(f"Using cached data for {symbol}")
            return cached_data[symbol]

        try:
            # For crypto, we need to append -USD if not already present
            if is_crypto and not symbol.endswith('-USD'):
                encoded_symbol = urllib.parse.quote(f"{symbol}-USD")
            else:
                encoded_symbol = urllib.parse.quote(symbol)

            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}"
            params = {
                'interval': '5m',  # 5-minute intervals
                'range': '1d'      # 1 day of data
            }
            
            # Use session with retry logic
            response = self.session.get(
                url,
                headers=self.headers,
                params=params,
                timeout=10,  # Increased timeout
                verify=True  # Enable SSL verification
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch data for {symbol}: HTTP {response.status_code}")
                return None
                
            data = response.json()
            
            # Increment API counter for stock/crypto data call
            increment_api_counter('stocks', 1)
            
            # Extract the relevant data from the response
            chart_data = data.get('chart', {}).get('result', [{}])[0]
            meta = chart_data.get('meta', {})
            
            if not meta:
                logger.error(f"No meta data found for {symbol}")
                return None
                
            current_price = meta.get('regularMarketPrice', 0)
            prev_close = meta.get('previousClose', current_price)
            
            # Get price history
            timestamps = chart_data.get('timestamp', [])
            indicators = chart_data.get('indicators', {}).get('quote', [{}])[0]
            close_prices = indicators.get('close', [])
            
            # Build price history
            price_history = []
            for i, ts in enumerate(timestamps):
                if i < len(close_prices) and close_prices[i] is not None:
                    price_history.append({
                        'timestamp': datetime.fromtimestamp(ts),
                        'price': close_prices[i]
                    })
            
            # Calculate change percentage
            change_pct = ((current_price - prev_close) / prev_close) * 100 if prev_close > 0 else 0
            
            # Get company name (symbol will be used if name not available)
            name = meta.get('symbol', symbol)
            
            logger.debug(f"Processed data for {symbol}: price={current_price}, change={change_pct}%")
            
            # Remove -USD suffix from crypto symbols for display
            display_symbol = symbol.replace('-USD', '') if is_crypto else symbol
            
            stock_data = {
                "symbol": display_symbol,  # Use the display symbol without -USD
                "name": name,
                "price": current_price,
                "change": change_pct,
                "open": prev_close,
                "price_history": price_history,
                "is_crypto": is_crypto
            }
            
            # Cache the new data
            if cached_data is None:
                cached_data = {}
            cached_data[symbol] = stock_data
            self.cache_manager.update_cache(cache_key, cached_data)
            
            # Add a longer delay between requests to avoid rate limiting
            time.sleep(random.uniform(1.0, 2.0))  # increased delay between requests
            return stock_data
            
        except requests.exceptions.SSLError as e:
            logger.error(f"SSL error fetching data for {symbol}: {e}")
            # Try to use cached data as fallback
            if cached_data and symbol in cached_data:
                logger.info(f"Using cached data as fallback for {symbol} after SSL error")
                return cached_data[symbol]
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error fetching data for {symbol}: {e}")
            # Try to use cached data as fallback
            if cached_data and symbol in cached_data:
                logger.info(f"Using cached data as fallback for {symbol}")
                return cached_data[symbol]
            return None
        except (ValueError, IndexError, KeyError) as e:
            logger.error(f"Error parsing data for {symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching data for {symbol}: {e}")
            return None

    def _draw_chart(self, symbol: str, data: Dict[str, Any]):
        """Draw a price chart for the stock."""
        if not data.get('price_history'):
            return
            
        # Clear the display
        self.display_manager.clear()
        
        # Draw the symbol at the top with small font
        self.display_manager.draw_text(
            symbol,
            y=1,  # Moved up slightly
            color=(255, 255, 255),
            small_font=True  # Use small font
        )
        
        # Calculate chart dimensions
        chart_height = 16  # Reduced from 22 to make chart smaller
        chart_y = 8  # Slightly adjusted starting position
        width = self.display_manager.matrix.width
        
        # Get min and max prices for scaling
        prices = [p['price'] for p in data['price_history']]
        if not prices:
            return
        min_price = min(prices)
        max_price = max(prices)
        price_range = max_price - min_price
        
        if price_range == 0:
            return
            
        # Draw chart points
        points = []
        color = self._get_stock_color(symbol)
        
        for i, point in enumerate(data['price_history']):
            x = int((i / len(data['price_history'])) * width)
            y = chart_y + chart_height - int(((point['price'] - min_price) / price_range) * chart_height)
            points.append((x, y))
            
        # Draw lines between points
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            self.display_manager.draw.line([x1, y1, x2, y2], fill=color, width=1)
            
        # Draw current price at the bottom with small font
        price_text = f"${data['price']:.2f} ({data['change']:+.1f}%)"
        self.display_manager.draw_text(
            price_text,
            y=28,  # Moved down slightly from 30 to give more space
            color=color,
            small_font=True  # Use small font
        )
        
        # Update the display
        self.display_manager.update_display()

    def _reload_config(self):
        """Reload configuration from file."""
        # Reset stock data if symbols have changed
        new_symbols = set(self.stocks_config.get('symbols', []))
        current_symbols = set(self.stock_data.keys())
        if new_symbols != current_symbols:
            self.stock_data = {}
            self.current_stock_index = 0
            self.last_update = 0  # Force immediate update
            logger.info(f"Stock symbols changed. New symbols: {new_symbols}")
            
        # Update scroll and chart settings
        self.scroll_speed = self.stocks_config.get('scroll_speed', 1)
        self.scroll_delay = self.stocks_config.get('scroll_delay', 0.01)
        self.toggle_chart = self.stocks_config.get('toggle_chart', False)
        
        # Clear cached image if settings changed
        if self.cached_text_image is not None:
            self.cached_text_image = None
            logger.info("Stock display settings changed, clearing cache")

    def update_stock_data(self):
        """Update stock data from API."""
        current_time = time.time()
        update_interval = self.stocks_config.get('update_interval', 600)
        
        # Check if we're currently scrolling and defer the update if so
        if self.display_manager.is_currently_scrolling():
            logger.debug("Stock display is currently scrolling, deferring update")
            self.display_manager.defer_update(self._perform_stock_update, priority=2)
            return
            
        self._perform_stock_update()

    def _perform_stock_update(self):
        """Internal method to perform the actual stock update."""
        current_time = time.time()
        update_interval = self.stocks_config.get('update_interval', 600)
        
        if current_time - self.last_update < update_interval:
            return
            
        try:
            logger.debug("Updating stock data")
            symbols = self.stocks_config.get('symbols', [])
            
            if not symbols:
                logger.warning("No stock symbols configured")
                return
                
            # Fetch stock data
            for symbol in symbols:
                try:
                    data = self._fetch_stock_data(symbol)
                    if data:
                        self.stock_data[symbol] = data
                        logger.debug(f"Updated data for {symbol}: {data}")
                except Exception as e:
                    logger.error(f"Error fetching data for {symbol}: {e}")
                    
            self.last_update = current_time
            
            # Clear cached text to force regeneration
            self.cached_text = None
            self.cached_text_image = None
            
        except Exception as e:
            logger.error(f"Error updating stock data: {e}")

    def _get_stock_logo(self, symbol: str, is_crypto: bool = False) -> Image.Image:
        """Get stock or crypto logo image from local directory with caching."""
        # Check cache first to avoid repeated file I/O
        cache_key = f"{symbol}_{'crypto' if is_crypto else 'stock'}"
        if cache_key in self._logo_cache:
            return self._logo_cache[cache_key]
        
        try:
            # Try crypto icons first if it's a crypto symbol
            if is_crypto:
                # Remove -USD suffix for crypto symbols
                base_symbol = symbol.replace('-USD', '')
                icon_path = os.path.join(self.crypto_icons_dir, f"{base_symbol}.png")
                if os.path.exists(icon_path):
                    with Image.open(icon_path) as img:
                        if img.mode != 'RGBA':
                            img = img.convert('RGBA')
                        max_size = min(int(self.display_manager.matrix.width / 1.2), 
                                    int(self.display_manager.matrix.height / 1.2))
                        img = img.resize((max_size, max_size), Image.Resampling.LANCZOS)
                        cached_img = img.copy()
                        self._logo_cache[cache_key] = cached_img
                        return cached_img
            
            # Fall back to stock icons if not crypto or crypto icon not found
            icon_path = os.path.join(self.ticker_icons_dir, f"{symbol}.png")
            if os.path.exists(icon_path):
                with Image.open(icon_path) as img:
                    if img.mode != 'RGBA':
                        img = img.convert('RGBA')
                    max_size = min(int(self.display_manager.matrix.width / 1.2), 
                                int(self.display_manager.matrix.height / 1.2))
                    img = img.resize((max_size, max_size), Image.Resampling.LANCZOS)
                    cached_img = img.copy()
                    self._logo_cache[cache_key] = cached_img
                    return cached_img
        except Exception as e:
            logger.warning(f"Error loading local icon for {symbol}: {e}")

        # If local icon not found or failed to load, create text-based fallback
        logger.warning(f"No local icon found for {symbol}. Using text fallback.")
        fallback = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(fallback)
        try:
            # Try to load OpenSans font first, fall back to PS2P if missing
            try:
                font = ImageFont.truetype("assets/fonts/OpenSans-Regular.ttf", 16)
            except Exception:
                font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 8)
        except:
            font = ImageFont.load_default()
        
        # Draw the symbol text
        text = symbol[:3]  # Limit to first 3 characters
        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        x = (32 - text_width) // 2
        y = (32 - text_height) // 2
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
        
        # Cache the fallback logo as well
        self._logo_cache[cache_key] = fallback
        return fallback

    def _create_stock_display(self, symbol: str, price: float, change: float, change_percent: float, is_crypto: bool = False) -> Image.Image:
        """Create a display image for a stock or crypto with logo, symbol, price, and change."""
        # Create a wider image for scrolling - adjust width based on chart toggle
        width = int(self.display_manager.matrix.width * (2 if self.toggle_chart else 1.5))  # Reduced width when no chart
        height = self.display_manager.matrix.height
        image = Image.new('RGB', (width, height), color=(0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Draw large stock/crypto logo on the left
        logo = self._get_stock_logo(symbol, is_crypto)
        if logo:
            # Position logo on the left side with minimal spacing
            logo_x = 2  # Small margin from left edge
            logo_y = (height - logo.height) // 2
            image.paste(logo, (logo_x, logo_y), logo)
        
        # Draw symbol, price, and change in a centered column
        # Use the fonts from display_manager
        regular_font = self.display_manager.regular_font
        small_font = self.display_manager.small_font
        
        # Create smaller versions of the fonts for symbol and price
        symbol_font = ImageFont.truetype(self.display_manager.regular_font.path, 
                                       int(self.display_manager.regular_font.size))
        price_font = ImageFont.truetype(self.display_manager.regular_font.path, 
                                      int(self.display_manager.regular_font.size))
        
        # Calculate text dimensions for proper spacing
        display_symbol = symbol.replace('-USD', '') if is_crypto else symbol
        symbol_text = display_symbol
        price_text = f"${price:.2f}"
        change_text = f"{change:+.2f} ({change_percent:+.1f}%)"
        
        # Get the height of each text element
        symbol_bbox = draw.textbbox((0, 0), symbol_text, font=symbol_font)
        price_bbox = draw.textbbox((0, 0), price_text, font=price_font)
        change_bbox = draw.textbbox((0, 0), change_text, font=small_font)
        
        # Calculate total height needed - adjust gaps based on chart toggle
        text_gap = 2 if self.toggle_chart else 1  # Reduced gap when no chart
        total_text_height = (symbol_bbox[3] - symbol_bbox[1]) + \
                           (price_bbox[3] - price_bbox[1]) + \
                           (change_bbox[3] - change_bbox[1]) + \
                           (text_gap * 2)  # Account for gaps between elements
        
        # Calculate starting y position to center all text
        start_y = (height - total_text_height) // 2
        
        # Calculate center x position for the column - adjust based on chart toggle
        if self.toggle_chart:
            # When chart is enabled, center text more to the left
            column_x = width // 2.85
        else:
            # When chart is disabled, position text with more space from logo
            column_x = width // 2.2
        
        # Draw symbol
        symbol_width = symbol_bbox[2] - symbol_bbox[0]
        symbol_x = column_x - (symbol_width // 2)
        draw.text((symbol_x, start_y), symbol_text, font=symbol_font, fill=(255, 255, 255))
        
        # Draw price
        price_width = price_bbox[2] - price_bbox[0]
        price_x = column_x - (price_width // 2)
        price_y = start_y + (symbol_bbox[3] - symbol_bbox[1]) + text_gap  # Adjusted gap
        draw.text((price_x, price_y), price_text, font=price_font, fill=(255, 255, 255))
        
        # Draw change with color based on value
        change_width = change_bbox[2] - change_bbox[0]
        change_x = column_x - (change_width // 2)
        change_y = price_y + (price_bbox[3] - price_bbox[1]) + text_gap  # Adjusted gap
        change_color = (0, 255, 0) if change >= 0 else (255, 0, 0)
        draw.text((change_x, change_y), change_text, font=small_font, fill=change_color)
        
        # Draw mini chart on the right only if toggle_chart is enabled
        if self.toggle_chart and symbol in self.stock_data and 'price_history' in self.stock_data[symbol]:
            price_history = self.stock_data[symbol]['price_history']
            if len(price_history) >= 2:
                # Extract prices from price history
                chart_data = [p['price'] for p in price_history]
                
                # Calculate chart dimensions
                chart_width = int(width // 2.5)  # Reduced from width//2.5 to prevent overlap
                chart_height = height // 1.5
                chart_x = width - chart_width - 4  # 4px margin from right edge
                chart_y = (height - chart_height) // 2
                
                # Find min and max prices for scaling
                min_price = min(chart_data)
                max_price = max(chart_data)
                
                # Add padding to avoid flat lines when prices are very close
                price_range = max_price - min_price
                if price_range < 0.01:
                    min_price -= 0.01
                    max_price += 0.01
                    price_range = 0.02
                
                # Calculate points for the line
                points = []
                for i, price in enumerate(chart_data):
                    x = chart_x + (i * chart_width) // (len(chart_data) - 1)
                    y = chart_y + chart_height - int(((price - min_price) / price_range) * chart_height)
                    points.append((x, y))
                
                # Draw lines between points
                color = self._get_stock_color(symbol)
                for i in range(len(points) - 1):
                    draw.line([points[i], points[i + 1]], fill=color, width=1)
        
        return image

    def _update_stock_display(self, symbol: str, data: Dict[str, Any], width: int, height: int) -> None:
        """Update the stock display with smooth scrolling animation."""
        try:
            # Create the full scrolling image
            full_image = self._create_stock_display(symbol, data['price'], data['change'], data['change'] / data['open'] * 100)
            scroll_width = width * 2  # Double width for smooth scrolling
            
            # Scroll the image smoothly
            for scroll_pos in range(0, scroll_width - width, 15):  # Increased scroll speed to match news ticker
                # Create visible portion
                visible_image = full_image.crop((scroll_pos, 0, scroll_pos + width, height))
                
                # Convert to RGB and create numpy array
                rgb_image = visible_image.convert('RGB')
                image_array = np.array(rgb_image)
                
                # Update display
                self.display_manager.update_display(image_array)
                
                # Small delay for smooth animation
                time.sleep(0.005)  # Reduced delay to 5ms for smoother scrolling
            
            # Show final position briefly
            final_image = full_image.crop((scroll_width - width, 0, scroll_width, height))
            rgb_image = final_image.convert('RGB')
            image_array = np.array(rgb_image)
            self.display_manager.update_display(image_array)
            time.sleep(0.2)  # Reduced pause at the end for better performance
            
        except Exception as e:
            logger.error(f"Error updating stock display for {symbol}: {str(e)}")
            # Show error state
            self._show_error_state(width, height)

    def _log_frame_rate(self):
        """Log frame rate statistics."""
        current_time = time.time()
        
        # Calculate instantaneous frame time
        frame_time = current_time - self.last_frame_time
        self.frame_times.append(frame_time)
        
        # Keep only last 100 frames for average
        if len(self.frame_times) > 100:
            self.frame_times.pop(0)
        
        # Log FPS every second
        if current_time - self.last_fps_log_time >= 1.0:
            avg_frame_time = sum(self.frame_times) / len(self.frame_times)
            avg_fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0
            instant_fps = 1.0 / frame_time if frame_time > 0 else 0
            
            logger.info(f"Frame stats - Avg FPS: {avg_fps:.1f}, Current FPS: {instant_fps:.1f}, Frame time: {frame_time*1000:.2f}ms")
            self.last_fps_log_time = current_time
            self.frame_count = 0
        
        self.last_frame_time = current_time
        self.frame_count += 1

    def display_stocks(self, force_clear: bool = False):
        """Display stock and crypto information with continuous scrolling animation."""
        if not self.stocks_config.get('enabled', False) and not self.crypto_config.get('enabled', False):
            return
            
        # Start update in background if needed
        if time.time() - self.last_update >= self.stocks_config.get('update_interval', 60):
            self.update_stock_data()
        
        if not self.stock_data:
            logger.warning("No stock or crypto data available to display")
            return
            
        # Get all symbols
        symbols = list(self.stock_data.keys())
        if not symbols:
            return
            
        # Create a continuous scrolling image if needed
        # Check if stock data has changed by comparing with cached symbols
        current_symbols = sorted(symbols)
        cached_symbols = getattr(self, '_cached_symbols', None)
        data_changed = cached_symbols != current_symbols
        
        if self.cached_text_image is None or force_clear or data_changed:
            # Create a very wide image that contains all stocks in sequence
            width = self.display_manager.matrix.width
            height = self.display_manager.matrix.height
            
            # Calculate total width needed for all stocks
            # Each stock needs width*2 for scrolling, plus consistent gaps between elements
            stock_gap = width // 6  # Reduced gap between stocks
            element_gap = width // 8  # Reduced gap between elements within a stock
            total_width = sum(width * 2 for _ in symbols) + stock_gap * (len(symbols) - 1) + element_gap * (len(symbols) * 2 - 1)
            
            # Create the full image
            full_image = Image.new('RGB', (total_width, height), (0, 0, 0))
            draw = ImageDraw.Draw(full_image)
            
            # Add initial gap before the first stock
            current_x = width
            
            # Draw each stock in sequence with consistent spacing
            for symbol in symbols:
                data = self.stock_data[symbol]
                is_crypto = data.get('is_crypto', False)
                
                # Create stock display for this symbol
                stock_image = self._create_stock_display(
                    symbol, 
                    data['price'], 
                    data['change'], 
                    data['change'] / data['open'] * 100,
                    is_crypto
                )
                
                # Paste this stock image into the full image
                full_image.paste(stock_image, (current_x, 0))
                
                # Move to next position with consistent spacing
                current_x += width * 2 + element_gap
                
                # Add extra gap between stocks
                if symbol != symbols[-1]:  # Don't add gap after the last stock
                    current_x += stock_gap
            
            # Cache the full image and symbols
            self.cached_text_image = full_image
            self._cached_symbols = current_symbols
            self.scroll_position = 0
            self.last_update = time.time()
            
            # Calculate total scroll width for dynamic duration
            self.total_scroll_width = total_width
            self.calculate_dynamic_duration()
        
        # Clear the display if requested
        if force_clear:
            self.display_manager.clear()
            self.scroll_position = 0
        
        # Calculate the visible portion of the image
        width = self.display_manager.matrix.width
        total_width = self.cached_text_image.width
        
        # Check if we should be scrolling
        should_scroll = True  # Stock display always scrolls continuously
        
        # Signal scrolling state to display manager
        self.display_manager.set_scrolling_state(True)
        
        # Process any deferred updates (though stocks are always scrolling)
        self.display_manager.process_deferred_updates()
        
        # Update scroll position with small increments
        self.scroll_position = (self.scroll_position + self.scroll_speed) % total_width
        
        # Calculate the visible portion
        visible_portion = self.cached_text_image.crop((
            self.scroll_position, 0,
            self.scroll_position + width, self.display_manager.matrix.height
        ))
        
        # Copy the visible portion to the display
        self.display_manager.image.paste(visible_portion, (0, 0))
        self.display_manager.update_display()
        
        # Log frame rate
        self._log_frame_rate()
        
        # Add a small delay between frames
        time.sleep(self.scroll_delay)
        
        # If we've scrolled through the entire image, reset
        if self.scroll_position == 0:
            return True
            
        return False

    def calculate_dynamic_duration(self):
        """Calculate the exact time needed to display all stocks"""
        # If dynamic duration is disabled, use fixed duration from config
        if not self.dynamic_duration_enabled:
            self.dynamic_duration = self.stocks_config.get('fixed_duration', 60)
            logger.debug(f"Dynamic duration disabled, using fixed duration: {self.dynamic_duration}s")
            return
            
        if not self.total_scroll_width:
            self.dynamic_duration = self.min_duration  # Use configured minimum
            return
            
        try:
            # Get display width (assume full width of display)
            display_width = getattr(self.display_manager, 'width', 128)  # Default to 128 if not available
            
            # Calculate total scroll distance needed
            # Text needs to scroll from right edge to completely off left edge
            total_scroll_distance = display_width + self.total_scroll_width
            
            # Calculate time based on scroll speed and delay
            # scroll_speed = pixels per frame, scroll_delay = seconds per frame
            frames_needed = total_scroll_distance / self.scroll_speed
            total_time = frames_needed * self.scroll_delay
            
            # Add buffer time for smooth cycling (configurable %)
            buffer_time = total_time * self.duration_buffer
            calculated_duration = int(total_time + buffer_time)
            
            # Apply configured min/max limits
            if calculated_duration < self.min_duration:
                self.dynamic_duration = self.min_duration
                logger.debug(f"Duration capped to minimum: {self.min_duration}s")
            elif calculated_duration > self.max_duration:
                self.dynamic_duration = self.max_duration
                logger.debug(f"Duration capped to maximum: {self.max_duration}s")
            else:
                self.dynamic_duration = calculated_duration
                
            logger.debug(f"Stock dynamic duration calculation:")
            logger.debug(f"  Display width: {display_width}px")
            logger.debug(f"  Text width: {self.total_scroll_width}px")
            logger.debug(f"  Total scroll distance: {total_scroll_distance}px")
            logger.debug(f"  Frames needed: {frames_needed:.1f}")
            logger.debug(f"  Base time: {total_time:.2f}s")
            logger.debug(f"  Buffer time: {buffer_time:.2f}s ({self.duration_buffer*100}%)")
            logger.debug(f"  Calculated duration: {calculated_duration}s")
            logger.debug(f"  Final duration: {self.dynamic_duration}s")
            
        except Exception as e:
            logger.error(f"Error calculating dynamic duration: {e}")
            self.dynamic_duration = self.min_duration  # Use configured minimum as fallback

    def get_dynamic_duration(self) -> int:
        """Get the calculated dynamic duration for display"""
        return self.dynamic_duration
        
    def set_toggle_chart(self, enabled: bool):
        """Enable or disable chart display in the scrolling ticker."""
        self.toggle_chart = enabled
        self.cached_text_image = None  # Clear cache when switching modes
        logger.info(f"Chart toggle set to: {enabled}")
        
    def set_scroll_speed(self, speed: int):
        """Set the scroll speed for the ticker."""
        self.scroll_speed = speed
        logger.info(f"Scroll speed set to: {speed}")
        
    def set_scroll_delay(self, delay: float):
        """Set the scroll delay for the ticker."""
        self.scroll_delay = delay
        logger.info(f"Scroll delay set to: {delay}") 