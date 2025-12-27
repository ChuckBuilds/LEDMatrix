"""
Tests for LayoutManager.

Tests layout creation, management, rendering, and element positioning.
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, Mock
from datetime import datetime
from src.layout_manager import LayoutManager


class TestLayoutManager:
    """Test LayoutManager functionality."""
    
    @pytest.fixture
    def tmp_layout_file(self, tmp_path):
        """Create a temporary layout file."""
        layout_file = tmp_path / "custom_layouts.json"
        return str(layout_file)
    
    @pytest.fixture
    def mock_display_manager(self):
        """Create a mock display manager."""
        dm = MagicMock()
        dm.clear = MagicMock()
        dm.update_display = MagicMock()
        dm.draw_text = MagicMock()
        dm.draw_weather_icon = MagicMock()
        dm.small_font = MagicMock()
        dm.regular_font = MagicMock()
        return dm
    
    @pytest.fixture
    def layout_manager(self, tmp_layout_file, mock_display_manager):
        """Create a LayoutManager instance."""
        return LayoutManager(
            display_manager=mock_display_manager,
            config_path=tmp_layout_file
        )
    
    def test_init(self, tmp_layout_file, mock_display_manager):
        """Test LayoutManager initialization."""
        lm = LayoutManager(
            display_manager=mock_display_manager,
            config_path=tmp_layout_file
        )
        
        assert lm.display_manager == mock_display_manager
        assert lm.config_path == tmp_layout_file
        assert lm.layouts == {}
        assert lm.current_layout is None
    
    def test_load_layouts_file_exists(self, tmp_path, mock_display_manager):
        """Test loading layouts from existing file."""
        layout_file = tmp_path / "custom_layouts.json"
        layout_data = {
            "test_layout": {
                "elements": [{"type": "text", "x": 0, "y": 0}],
                "description": "Test layout"
            }
        }
        with open(layout_file, 'w') as f:
            json.dump(layout_data, f)
        
        lm = LayoutManager(
            display_manager=mock_display_manager,
            config_path=str(layout_file)
        )
        
        assert "test_layout" in lm.layouts
        assert lm.layouts["test_layout"]["description"] == "Test layout"
    
    def test_load_layouts_file_not_exists(self, tmp_layout_file, mock_display_manager):
        """Test loading layouts when file doesn't exist."""
        lm = LayoutManager(
            display_manager=mock_display_manager,
            config_path=tmp_layout_file
        )
        
        assert lm.layouts == {}
    
    def test_create_layout(self, layout_manager):
        """Test creating a new layout."""
        elements = [{"type": "text", "x": 10, "y": 20, "properties": {"text": "Hello"}}]
        
        result = layout_manager.create_layout("test_layout", elements, "Test description")
        
        assert result is True
        assert "test_layout" in layout_manager.layouts
        assert layout_manager.layouts["test_layout"]["elements"] == elements
        assert layout_manager.layouts["test_layout"]["description"] == "Test description"
        assert "created" in layout_manager.layouts["test_layout"]
        assert "modified" in layout_manager.layouts["test_layout"]
    
    def test_update_layout(self, layout_manager):
        """Test updating an existing layout."""
        # Create a layout first
        elements1 = [{"type": "text", "x": 0, "y": 0}]
        layout_manager.create_layout("test_layout", elements1, "Original")
        
        # Update it
        elements2 = [{"type": "text", "x": 10, "y": 20}]
        result = layout_manager.update_layout("test_layout", elements2, "Updated")
        
        assert result is True
        assert layout_manager.layouts["test_layout"]["elements"] == elements2
        assert layout_manager.layouts["test_layout"]["description"] == "Updated"
        assert "modified" in layout_manager.layouts["test_layout"]
    
    def test_update_layout_not_exists(self, layout_manager):
        """Test updating a non-existent layout."""
        elements = [{"type": "text", "x": 0, "y": 0}]
        result = layout_manager.update_layout("nonexistent", elements)
        
        assert result is False
    
    def test_delete_layout(self, layout_manager):
        """Test deleting a layout."""
        elements = [{"type": "text", "x": 0, "y": 0}]
        layout_manager.create_layout("test_layout", elements)
        
        result = layout_manager.delete_layout("test_layout")
        
        assert result is True
        assert "test_layout" not in layout_manager.layouts
    
    def test_delete_layout_not_exists(self, layout_manager):
        """Test deleting a non-existent layout."""
        result = layout_manager.delete_layout("nonexistent")
        
        assert result is False
    
    def test_get_layout(self, layout_manager):
        """Test getting a specific layout."""
        elements = [{"type": "text", "x": 0, "y": 0}]
        layout_manager.create_layout("test_layout", elements)
        
        layout = layout_manager.get_layout("test_layout")
        
        assert layout is not None
        assert layout["elements"] == elements
    
    def test_get_layout_not_exists(self, layout_manager):
        """Test getting a non-existent layout."""
        layout = layout_manager.get_layout("nonexistent")
        
        assert layout == {}
    
    def test_list_layouts(self, layout_manager):
        """Test listing all layouts."""
        layout_manager.create_layout("layout1", [])
        layout_manager.create_layout("layout2", [])
        layout_manager.create_layout("layout3", [])
        
        layouts = layout_manager.list_layouts()
        
        assert len(layouts) == 3
        assert "layout1" in layouts
        assert "layout2" in layouts
        assert "layout3" in layouts
    
    def test_set_current_layout(self, layout_manager):
        """Test setting the current layout."""
        layout_manager.create_layout("test_layout", [])
        
        result = layout_manager.set_current_layout("test_layout")
        
        assert result is True
        assert layout_manager.current_layout == "test_layout"
    
    def test_set_current_layout_not_exists(self, layout_manager):
        """Test setting a non-existent layout as current."""
        result = layout_manager.set_current_layout("nonexistent")
        
        assert result is False
        assert layout_manager.current_layout is None
    
    def test_render_layout(self, layout_manager, mock_display_manager):
        """Test rendering a layout."""
        elements = [
            {"type": "text", "x": 0, "y": 0, "properties": {"text": "Hello"}},
            {"type": "text", "x": 10, "y": 10, "properties": {"text": "World"}}
        ]
        layout_manager.create_layout("test_layout", elements)
        
        result = layout_manager.render_layout("test_layout")
        
        assert result is True
        mock_display_manager.clear.assert_called_once()
        mock_display_manager.update_display.assert_called_once()
        assert mock_display_manager.draw_text.call_count == 2
    
    def test_render_layout_no_display_manager(self, tmp_layout_file):
        """Test rendering without display manager."""
        lm = LayoutManager(display_manager=None, config_path=tmp_layout_file)
        lm.create_layout("test_layout", [])
        
        result = lm.render_layout("test_layout")
        
        assert result is False
    
    def test_render_layout_not_exists(self, layout_manager):
        """Test rendering a non-existent layout."""
        result = layout_manager.render_layout("nonexistent")
        
        assert result is False
    
    def test_render_element_text(self, layout_manager, mock_display_manager):
        """Test rendering a text element."""
        element = {
            "type": "text",
            "x": 10,
            "y": 20,
            "properties": {
                "text": "Hello",
                "color": [255, 0, 0],
                "font_size": "small"
            }
        }
        
        layout_manager.render_element(element, {})
        
        mock_display_manager.draw_text.assert_called_once()
        call_args = mock_display_manager.draw_text.call_args
        assert call_args[0][0] == "Hello"  # text
        assert call_args[0][1] == 10  # x
        assert call_args[0][2] == 20  # y
    
    def test_render_element_weather_icon(self, layout_manager, mock_display_manager):
        """Test rendering a weather icon element."""
        element = {
            "type": "weather_icon",
            "x": 10,
            "y": 20,
            "properties": {
                "condition": "sunny",
                "size": 16
            }
        }
        
        layout_manager.render_element(element, {})
        
        mock_display_manager.draw_weather_icon.assert_called_once_with("sunny", 10, 20, 16)
    
    def test_render_element_weather_icon_from_context(self, layout_manager, mock_display_manager):
        """Test rendering weather icon with data from context."""
        element = {
            "type": "weather_icon",
            "x": 10,
            "y": 20,
            "properties": {"size": 16}
        }
        data_context = {
            "weather": {
                "condition": "cloudy"
            }
        }
        
        layout_manager.render_element(element, data_context)
        
        mock_display_manager.draw_weather_icon.assert_called_once_with("cloudy", 10, 20, 16)
    
    def test_render_element_rectangle(self, layout_manager, mock_display_manager):
        """Test rendering a rectangle element."""
        element = {
            "type": "rectangle",
            "x": 10,
            "y": 20,
            "properties": {
                "width": 50,
                "height": 30,
                "color": [255, 0, 0],
                "filled": True
            }
        }
        
        # Mock the draw object and rectangle method
        mock_draw = MagicMock()
        mock_display_manager.draw = mock_draw
        
        layout_manager.render_element(element, {})
        
        # Verify rectangle was drawn
        mock_draw.rectangle.assert_called_once()
    
    def test_render_element_unknown_type(self, layout_manager):
        """Test rendering an unknown element type."""
        element = {
            "type": "unknown_type",
            "x": 0,
            "y": 0,
            "properties": {}
        }
        
        # Should not raise an exception
        layout_manager.render_element(element, {})
    
    def test_process_template_text(self, layout_manager):
        """Test template text processing."""
        text = "Hello {name}, temperature is {temp}°F"
        data_context = {
            "name": "World",
            "temp": 72
        }
        
        result = layout_manager._process_template_text(text, data_context)
        
        assert result == "Hello World, temperature is 72°F"
    
    def test_process_template_text_no_context(self, layout_manager):
        """Test template text with missing context."""
        text = "Hello {name}"
        data_context = {}
        
        result = layout_manager._process_template_text(text, data_context)
        
        # Should leave template as-is or handle gracefully
        assert "{name}" in result or result == "Hello "
    
    def test_save_layouts_error_handling(self, layout_manager):
        """Test error handling when saving layouts."""
        # Create a layout
        layout_manager.create_layout("test", [])
        
        # Make save fail by using invalid path
        layout_manager.config_path = "/nonexistent/directory/layouts.json"
        
        result = layout_manager.save_layouts()
        
        # Should handle error gracefully
        assert result is False
    
    def test_render_element_line(self, layout_manager, mock_display_manager):
        """Test rendering a line element."""
        element = {
            "type": "line",
            "x": 10,
            "y": 20,
            "properties": {
                "x2": 50,
                "y2": 30,
                "color": [255, 0, 0],
                "width": 2
            }
        }
        
        mock_draw = MagicMock()
        mock_display_manager.draw = mock_draw
        
        layout_manager.render_element(element, {})
        
        mock_draw.line.assert_called_once()
    
    def test_render_element_clock(self, layout_manager, mock_display_manager):
        """Test rendering a clock element."""
        element = {
            "type": "clock",
            "x": 10,
            "y": 20,
            "properties": {
                "format": "%H:%M",
                "color": [255, 255, 255]
            }
        }
        
        layout_manager.render_element(element, {})
        
        mock_display_manager.draw_text.assert_called_once()
    
    def test_render_element_data_text(self, layout_manager, mock_display_manager):
        """Test rendering a data text element."""
        element = {
            "type": "data_text",
            "x": 10,
            "y": 20,
            "properties": {
                "data_key": "weather.temperature",
                "format": "Temp: {value}°F",
                "color": [255, 255, 255],
                "default": "N/A"
            }
        }
        data_context = {
            "weather": {
                "temperature": 72
            }
        }
        
        layout_manager.render_element(element, data_context)
        
        mock_display_manager.draw_text.assert_called_once()
