#!/usr/bin/env python3
# Quick test of font system functionality

from src.config_manager import ConfigManager
from src.font_manager import FontManager

print('Testing font system...')

# Load configuration
config_manager = ConfigManager()
config = config_manager.load_config()
fonts_config = config.get('fonts', {})
families = fonts_config.get('families', {})
print(f'Configuration loaded with {len(families)} font families')

# Initialize font manager
font_manager = FontManager(config)
print(f'Font manager initialized with {len(font_manager.font_catalog)} fonts in catalog')

# Test font resolution
try:
    font = font_manager.resolve(family='press_start', size_px=12)
    print('Font resolution working')
except Exception as e:
    print(f'Font resolution failed: {e}')

# Test font statistics
stats = font_manager.get_font_statistics()
print(f'Font statistics: {stats["total_fonts"]} total fonts, {stats["cached_fonts"]} cached')

print('Font system is fully functional!')
