"""
Tests for TextHelper class.

Tests text rendering, font loading, and text positioning utilities.
"""

import pytest
from unittest.mock import MagicMock, patch, Mock
from PIL import Image, ImageDraw, ImageFont
from src.common.text_helper import TextHelper


class TestTextHelper:
    """Test TextHelper functionality."""
    
    @pytest.fixture
    def text_helper(self, tmp_path):
        """Create a TextHelper instance."""
        return TextHelper(font_dir=str(tmp_path))
    
    def test_init(self, tmp_path):
        """Test TextHelper initialization."""
        th = TextHelper(font_dir=str(tmp_path))
        assert th.font_dir == tmp_path
        assert th._font_cache == {}
    
    def test_init_default_font_dir(self):
        """Test TextHelper initialization with default font directory."""
        th = TextHelper()
        assert th.font_dir == pytest.importorskip("pathlib").Path("assets/fonts")
    
    @patch('PIL.ImageFont.truetype')
    @patch('PIL.ImageFont.load_default')
    def test_load_fonts_success(self, mock_default, mock_truetype, text_helper, tmp_path):
        """Test loading fonts successfully."""
        font_file = tmp_path / "test_font.ttf"
        font_file.write_text("fake font")
        
        mock_font = MagicMock()
        mock_truetype.return_value = mock_font
        
        font_config = {
            "regular": {
                "file": "test_font.ttf",
                "size": 12
            }
        }
        
        fonts = text_helper.load_fonts(font_config)
        
        assert "regular" in fonts
        assert fonts["regular"] == mock_font
    
    @patch('PIL.ImageFont.load_default')
    def test_load_fonts_file_not_found(self, mock_default, text_helper):
        """Test loading fonts when file doesn't exist."""
        mock_font = MagicMock()
        mock_default.return_value = mock_font
        
        font_config = {
            "regular": {
                "file": "nonexistent.ttf",
                "size": 12
            }
        }
        
        fonts = text_helper.load_fonts(font_config)
        
        assert "regular" in fonts
        assert fonts["regular"] == mock_font  # Should use default
    
    def test_draw_text_with_outline(self, text_helper):
        """Test drawing text with outline."""
        # Create a mock image and draw object
        mock_image = Image.new('RGB', (100, 100))
        mock_draw = ImageDraw.Draw(mock_image)
        mock_font = ImageFont.load_default()
        
        # Should not raise an exception
        text_helper.draw_text_with_outline(
            mock_draw, "Hello", (10, 10), mock_font
        )
    
    def test_get_text_dimensions(self, text_helper):
        """Test getting text dimensions."""
        from PIL import Image, ImageDraw
        mock_image = Image.new('RGB', (100, 100))
        mock_draw = ImageDraw.Draw(mock_image)
        mock_font = ImageFont.load_default()
        
        # Patch the draw object in the method
        with patch.object(text_helper, 'get_text_width', return_value=50), \
             patch.object(text_helper, 'get_text_height', return_value=10):
            width, height = text_helper.get_text_dimensions("Hello", mock_font)
            assert width == 50
            assert height == 10
    
    def test_center_text(self, text_helper):
        """Test centering text position."""
        mock_font = ImageFont.load_default()
        
        with patch.object(text_helper, 'get_text_dimensions', return_value=(50, 10)):
            x, y = text_helper.center_text("Hello", mock_font, 100, 20)
            assert x == 25  # (100 - 50) / 2
            assert y == 5   # (20 - 10) / 2
    
    def test_wrap_text(self, text_helper):
        """Test wrapping text to width."""
        mock_font = ImageFont.load_default()
        text = "This is a long line of text"
        
        with patch.object(text_helper, 'get_text_width') as mock_width:
            # Simulate width calculation
            def width_side_effect(text, font):
                return len(text) * 5  # Simple width calculation
            mock_width.side_effect = width_side_effect
            
            lines = text_helper.wrap_text(text, mock_font, max_width=20)
            
            assert isinstance(lines, list)
            assert len(lines) > 0
    
    def test_get_default_font_config(self, text_helper):
        """Test getting default font configuration."""
        config = text_helper._get_default_font_config()
        
        assert isinstance(config, dict)
        assert len(config) > 0
