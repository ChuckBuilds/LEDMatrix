"""
Layout Manager for LED Matrix Display
Handles custom layouts, element positioning, and display composition.
"""

import json
import os
import logging
from typing import Dict, List, Any, Tuple
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

class LayoutManager:
    def __init__(self, display_manager=None, config_path="config/custom_layouts.json"):
        self.display_manager = display_manager
        self.config_path = config_path
        self.layouts = self.load_layouts()
        self.current_layout = None
        
    def load_layouts(self) -> Dict[str, Any]:
        """Load saved layouts from file."""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading layouts: {e}")
            return {}
    
    def save_layouts(self) -> bool:
        """Save layouts to file."""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(self.layouts, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Error saving layouts: {e}")
            return False
    
    def create_layout(self, name: str, elements: List[Dict], description: str = "") -> bool:
        """Create a new layout."""
        try:
            self.layouts[name] = {
                'elements': elements,
                'description': description,
                'created': datetime.now().isoformat(),
                'modified': datetime.now().isoformat()
            }
            return self.save_layouts()
        except Exception as e:
            logger.error(f"Error creating layout '{name}': {e}")
            return False
    
    def update_layout(self, name: str, elements: List[Dict], description: str = None) -> bool:
        """Update an existing layout."""
        try:
            if name not in self.layouts:
                return False
            
            self.layouts[name]['elements'] = elements
            self.layouts[name]['modified'] = datetime.now().isoformat()
            
            if description is not None:
                self.layouts[name]['description'] = description
                
            return self.save_layouts()
        except Exception as e:
            logger.error(f"Error updating layout '{name}': {e}")
            return False
    
    def delete_layout(self, name: str) -> bool:
        """Delete a layout."""
        try:
            if name in self.layouts:
                del self.layouts[name]
                return self.save_layouts()
            return False
        except Exception as e:
            logger.error(f"Error deleting layout '{name}': {e}")
            return False
    
    def get_layout(self, name: str) -> Dict[str, Any]:
        """Get a specific layout."""
        return self.layouts.get(name, {})
    
    def list_layouts(self) -> List[str]:
        """Get list of all layout names."""
        return list(self.layouts.keys())
    
    def set_current_layout(self, name: str) -> bool:
        """Set the current active layout."""
        if name in self.layouts:
            self.current_layout = name
            return True
        return False
    
    def render_layout(self, layout_name: str = None, data_context: Dict = None) -> bool:
        """Render a layout to the display."""
        if not self.display_manager:
            logger.error("No display manager available")
            return False
        
        layout_name = layout_name or self.current_layout
        if not layout_name or layout_name not in self.layouts:
            logger.error(f"Layout '{layout_name}' not found")
            return False
        
        try:
            # Clear the display
            self.display_manager.clear()
            
            # Get layout elements
            elements = self.layouts[layout_name]['elements']
            
            # Render each element
            for element in elements:
                self.render_element(element, data_context or {})
            
            # Update the display
            self.display_manager.update_display()
            return True
            
        except Exception as e:
            logger.error(f"Error rendering layout '{layout_name}': {e}")
            return False
    
    def render_element(self, element: Dict, data_context: Dict) -> None:
        """Render a single element."""
        element_type = element.get('type')
        x = element.get('x', 0)
        y = element.get('y', 0)
        properties = element.get('properties', {})
        
        try:
            if element_type == 'text':
                self._render_text_element(x, y, properties, data_context)
            elif element_type == 'weather_icon':
                self._render_weather_icon_element(x, y, properties, data_context)
            elif element_type == 'rectangle':
                self._render_rectangle_element(x, y, properties)
            elif element_type == 'line':
                self._render_line_element(x, y, properties)
            elif element_type == 'clock':
                self._render_clock_element(x, y, properties)
            elif element_type == 'data_text':
                self._render_data_text_element(x, y, properties, data_context)
            else:
                logger.warning(f"Unknown element type: {element_type}")
                
        except Exception as e:
            logger.error(f"Error rendering element {element_type}: {e}")
    
    def _render_text_element(self, x: int, y: int, properties: Dict, data_context: Dict) -> None:
        """Render a text element."""
        text = properties.get('text', 'Sample Text')
        color = tuple(properties.get('color', [255, 255, 255]))
        font_size = properties.get('font_size', 'normal')
        
        # Support template variables in text
        text = self._process_template_text(text, data_context)
        
        # Select font
        if font_size == 'small':
            font = self.display_manager.small_font
        elif font_size == 'large':
            font = self.display_manager.regular_font
        else:
            font = self.display_manager.regular_font
        
        self.display_manager.draw_text(text, x, y, color, font=font)
    
    def _render_weather_icon_element(self, x: int, y: int, properties: Dict, data_context: Dict) -> None:
        """Render a weather icon element."""
        condition = properties.get('condition', 'sunny')
        size = properties.get('size', 16)
        
        # Use weather data from context if available
        if 'weather' in data_context and 'condition' in data_context['weather']:
            condition = data_context['weather']['condition'].lower()
        
        self.display_manager.draw_weather_icon(condition, x, y, size)
    
    def _render_rectangle_element(self, x: int, y: int, properties: Dict) -> None:
        """Render a rectangle element."""
        width = properties.get('width', 10)
        height = properties.get('height', 10)
        color = tuple(properties.get('color', [255, 255, 255]))
        filled = properties.get('filled', False)
        
        if filled:
            self.display_manager.draw.rectangle(
                [x, y, x + width, y + height], 
                fill=color
            )
        else:
            self.display_manager.draw.rectangle(
                [x, y, x + width, y + height], 
                outline=color
            )
    
    def _render_line_element(self, x: int, y: int, properties: Dict) -> None:
        """Render a line element."""
        x2 = properties.get('x2', x + 10)
        y2 = properties.get('y2', y)
        color = tuple(properties.get('color', [255, 255, 255]))
        width = properties.get('width', 1)
        
        self.display_manager.draw.line([x, y, x2, y2], fill=color, width=width)
    
    def _render_clock_element(self, x: int, y: int, properties: Dict) -> None:
        """Render a clock element."""
        format_str = properties.get('format', '%H:%M')
        color = tuple(properties.get('color', [255, 255, 255]))
        
        current_time = datetime.now().strftime(format_str)
        self.display_manager.draw_text(current_time, x, y, color)
    
    def _render_data_text_element(self, x: int, y: int, properties: Dict, data_context: Dict) -> None:
        """Render a data-driven text element."""
        data_key = properties.get('data_key', '')
        format_str = properties.get('format', '{value}')
        color = tuple(properties.get('color', [255, 255, 255]))
        default_value = properties.get('default', 'N/A')
        
        # Extract data from context
        value = self._get_nested_value(data_context, data_key, default_value)
        
        # Format the text
        try:
            text = format_str.format(value=value)
        except:
            text = str(value)
        
        self.display_manager.draw_text(text, x, y, color)
    
    def _process_template_text(self, text: str, data_context: Dict) -> str:
        """Process template variables in text."""
        try:
            # Simple template processing - replace {key} with values from context
            for key, value in data_context.items():
                placeholder = f"{{{key}}}"
                if placeholder in text:
                    text = text.replace(placeholder, str(value))
            return text
        except Exception as e:
            logger.error(f"Error processing template text: {e}")
            return text
    
    def _get_nested_value(self, data: Dict, key: str, default=None):
        """Get a nested value from a dictionary using dot notation."""
        try:
            keys = key.split('.')
            value = data
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def create_preset_layouts(self) -> None:
        """Create some preset layouts for common use cases."""
        # Basic clock layout
        clock_layout = [
            {
                'type': 'clock',
                'x': 10,
                'y': 10,
                'properties': {
                    'format': '%H:%M',
                    'color': [255, 255, 255]
                }
            },
            {
                'type': 'clock',
                'x': 10,
                'y': 20,
                'properties': {
                    'format': '%m/%d',
                    'color': [100, 100, 255]
                }
            }
        ]
        self.create_layout('basic_clock', clock_layout, 'Simple clock with date')
        
        # Weather layout
        weather_layout = [
            {
                'type': 'weather_icon',
                'x': 5,
                'y': 5,
                'properties': {
                    'condition': 'sunny',
                    'size': 20
                }
            },
            {
                'type': 'data_text',
                'x': 30,
                'y': 8,
                'properties': {
                    'data_key': 'weather.temperature',
                    'format': '{value}°',
                    'color': [255, 200, 0],
                    'default': '--°'
                }
            },
            {
                'type': 'data_text',
                'x': 30,
                'y': 18,
                'properties': {
                    'data_key': 'weather.condition',
                    'format': '{value}',
                    'color': [200, 200, 200],
                    'default': 'Unknown'
                }
            }
        ]
        self.create_layout('weather_display', weather_layout, 'Weather icon with temperature and condition')
        
        # Mixed dashboard layout
        dashboard_layout = [
            {
                'type': 'clock',
                'x': 2,
                'y': 2,
                'properties': {
                    'format': '%H:%M',
                    'color': [255, 255, 255]
                }
            },
            {
                'type': 'weather_icon',
                'x': 50,
                'y': 2,
                'properties': {
                    'size': 16
                }
            },
            {
                'type': 'data_text',
                'x': 70,
                'y': 5,
                'properties': {
                    'data_key': 'weather.temperature',
                    'format': '{value}°',
                    'color': [255, 200, 0],
                    'default': '--°'
                }
            },
            {
                'type': 'line',
                'x': 0,
                'y': 15,
                'properties': {
                    'x2': 128,
                    'y2': 15,
                    'color': [100, 100, 100]
                }
            },
            {
                'type': 'data_text',
                'x': 2,
                'y': 18,
                'properties': {
                    'data_key': 'stocks.AAPL.price',
                    'format': 'AAPL: ${value}',
                    'color': [0, 255, 0],
                    'default': 'AAPL: N/A'
                }
            }
        ]
        self.create_layout('dashboard', dashboard_layout, 'Mixed dashboard with clock, weather, and stocks')
        
        logger.info("Created preset layouts")

    def get_layout_preview(self, layout_name: str) -> Dict[str, Any]:
        """Get a preview representation of a layout."""
        if layout_name not in self.layouts:
            return {}
        
        layout = self.layouts[layout_name]
        elements = layout['elements']
        
        # Create a simple preview representation
        preview = {
            'name': layout_name,
            'description': layout.get('description', ''),
            'element_count': len(elements),
            'elements': []
        }
        
        for element in elements:
            preview['elements'].append({
                'type': element.get('type'),
                'position': f"({element.get('x', 0)}, {element.get('y', 0)})",
                'properties': list(element.get('properties', {}).keys())
            })
        
        return preview