"""
MQTT Manager for HomeAssistant Integration

This module provides MQTT connectivity and display management for HomeAssistant
integration with the LEDMatrix project. It handles both rotation displays and
on-demand notifications.
"""

import paho.mqtt.client as mqtt
import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional
from PIL import Image, ImageDraw, ImageFont
import os
from pathlib import Path

logger = logging.getLogger(__name__)

class MQTTManager:
    """MQTT Manager for HomeAssistant integration with LEDMatrix."""
    
    def __init__(self, config: Dict[str, Any], display_manager, cache_manager):
        self.config = config.get('mqtt', {})
        self.display_manager = display_manager
        self.cache_manager = cache_manager
        self.is_enabled = self.config.get('enabled', False)
        
        if not self.is_enabled:
            logger.info("MQTT manager is disabled in configuration")
            return
            
        # MQTT client setup
        self.client = None
        self.is_connected = False
        self.connection_lock = threading.Lock()
        
        # Display management
        self.rotation_displays = []
        self.ondemand_displays = []
        self.current_ondemand = None
        self.ondemand_queue = []
        self.ondemand_lock = threading.Lock()
        
        # Data storage
        self.mqtt_data = {}  # Store latest MQTT values by topic
        self.data_lock = threading.Lock()
        
        # Load display configurations
        self._load_display_configs()
        
        # Initialize MQTT client
        self._setup_mqtt_client()
        
        logger.info("MQTT Manager initialized successfully")
    
    def _load_display_configs(self):
        """Load MQTT display configurations from config."""
        displays_config = self.config.get('displays', {})
        self.rotation_displays = displays_config.get('rotation', [])
        self.ondemand_displays = displays_config.get('ondemand', [])
        
        logger.info(f"Loaded {len(self.rotation_displays)} rotation displays and {len(self.ondemand_displays)} on-demand displays")
    
    def _setup_mqtt_client(self):
        """Initialize MQTT client and connection."""
        broker_config = self.config.get('broker', {})
        
        if not broker_config.get('host'):
            logger.error("MQTT broker host not configured")
            return
            
        self.client = mqtt.Client(client_id=broker_config.get('client_id', 'ledmatrix'))
        
        # Set credentials if provided
        if broker_config.get('username'):
            self.client.username_pw_set(
                broker_config['username'], 
                broker_config.get('password', '')
            )
        
        # Set callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        
        # Connect
        try:
            self.client.connect(
                broker_config['host'],
                broker_config.get('port', 1883),
                keepalive=broker_config.get('keepalive', 60)
            )
            self.client.loop_start()
            logger.info(f"MQTT client connecting to {broker_config['host']}:{broker_config.get('port', 1883)}")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
    
    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback."""
        with self.connection_lock:
            if rc == 0:
                self.is_connected = True
                logger.info("Connected to MQTT broker successfully")
                self._subscribe_to_topics()
            else:
                self.is_connected = False
                logger.error(f"Failed to connect to MQTT broker: {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnection callback."""
        with self.connection_lock:
            self.is_connected = False
            logger.warning("Disconnected from MQTT broker")
    
    def _on_message(self, client, userdata, msg):
        """MQTT message callback."""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            # Store the data
            with self.data_lock:
                self.mqtt_data[topic] = {
                    'value': payload,
                    'timestamp': time.time()
                }
            
            # Check for on-demand triggers
            self._check_ondemand_triggers(topic, payload)
            
            logger.debug(f"MQTT message received: {topic} = {payload}")
            
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")
    
    def _subscribe_to_topics(self):
        """Subscribe to all required MQTT topics."""
        topics = set()
        
        # Collect topics from all displays
        for display in self.rotation_displays + self.ondemand_displays:
            for element in display.get('elements', []):
                if element.get('type') in ['text', 'icon'] and element.get('mqtt_topic'):
                    topics.add(element['mqtt_topic'])
                if element.get('trigger_topic'):  # For on-demand triggers
                    topics.add(element['trigger_topic'])
        
        # Subscribe to all topics
        for topic in topics:
            self.client.subscribe(topic)
            logger.info(f"Subscribed to MQTT topic: {topic}")
    
    def _check_ondemand_triggers(self, topic: str, payload: str):
        """Check if an MQTT message should trigger an on-demand display."""
        with self.ondemand_lock:
            for display in self.ondemand_displays:
                trigger_topic = display.get('trigger_topic')
                trigger_value = display.get('trigger_value')
                
                if topic == trigger_topic and payload == trigger_value:
                    # Add to on-demand queue
                    self.ondemand_queue.append({
                        'display': display,
                        'timestamp': time.time(),
                        'duration': display.get('duration', 10)
                    })
                    
                    # If no current on-demand, start this one
                    if not self.current_ondemand:
                        self._start_next_ondemand()
                    
                    logger.info(f"On-demand display triggered: {display['name']}")
    
    def _start_next_ondemand(self):
        """Start the next on-demand display in the queue."""
        if not self.ondemand_queue:
            self.current_ondemand = None
            return
            
        self.current_ondemand = self.ondemand_queue.pop(0)
        logger.info(f"Starting on-demand display: {self.current_ondemand['display']['name']}")
    
    def get_mqtt_value(self, topic: str, default: str = "N/A") -> str:
        """Get the latest value for an MQTT topic."""
        with self.data_lock:
            data = self.mqtt_data.get(topic)
            if data:
                return data['value']
            return default
    
    def is_ondemand_active(self) -> bool:
        """Check if an on-demand display is currently active."""
        if not self.current_ondemand:
            return False
            
        # Check if on-demand display has expired
        elapsed = time.time() - self.current_ondemand['timestamp']
        if elapsed >= self.current_ondemand['duration']:
            self._start_next_ondemand()
            return self.current_ondemand is not None
            
        return True
    
    def get_current_ondemand_display(self) -> Optional[Dict]:
        """Get the current on-demand display configuration."""
        return self.current_ondemand['display'] if self.current_ondemand else None
    
    def get_rotation_displays(self) -> List[Dict]:
        """Get list of rotation displays."""
        return self.rotation_displays
    
    def update(self):
        """Update MQTT manager (called by DisplayController)."""
        if not self.is_enabled or not self.is_connected:
            return
            
        # Check for expired on-demand displays
        if self.current_ondemand:
            elapsed = time.time() - self.current_ondemand['timestamp']
            if elapsed >= self.current_ondemand['duration']:
                self._start_next_ondemand()
    
    def display(self, display_config: Dict, force_clear: bool = False):
        """Display an MQTT display configuration."""
        if not self.is_enabled:
            return
            
        try:
            # Clear display if requested
            if force_clear:
                self.display_manager.clear_display()
            
            # Render each element
            for element in display_config.get('elements', []):
                self._render_element(element)
                
            # Update display
            self.display_manager.update_display()
            
        except Exception as e:
            logger.error(f"Error displaying MQTT content: {e}")
    
    def _render_element(self, element: Dict):
        """Render a single MQTT display element."""
        element_type = element.get('type')
        
        if element_type == 'text':
            self._render_text_element(element)
        elif element_type == 'icon':
            self._render_icon_element(element)
        elif element_type == 'rectangle':
            self._render_rectangle_element(element)
        elif element_type == 'line':
            self._render_line_element(element)
    
    def _render_text_element(self, element: Dict):
        """Render a text element with MQTT data."""
        try:
            x = element.get('x', 0)
            y = element.get('y', 0)
            font_size = element.get('font_size', 12)
            color = tuple(element.get('color', [255, 255, 255]))
            mqtt_topic = element.get('mqtt_topic')
            format_string = element.get('format', '{value}')
            
            # Get MQTT value
            if mqtt_topic:
                value = self.get_mqtt_value(mqtt_topic)
                text = format_string.format(value=value)
            else:
                text = element.get('text', '')
            
            # Load font and draw text
            font_path = element.get('font_path', 'assets/fonts/PressStart2P-Regular.ttf')
            try:
                font = ImageFont.truetype(font_path, font_size)
            except:
                font = ImageFont.load_default()
            
            self.display_manager.draw.text((x, y), text, font=font, fill=color)
            
        except Exception as e:
            logger.error(f"Error rendering text element: {e}")
    
    def _render_icon_element(self, element: Dict):
        """Render an icon element."""
        try:
            x = element.get('x', 0)
            y = element.get('y', 0)
            width = element.get('width', 32)
            height = element.get('height', 32)
            
            # Get icon path
            icon_path = element.get('path')
            if not icon_path or not os.path.exists(icon_path):
                # Fallback to text if icon not found
                self._render_text_element({
                    'type': 'text',
                    'x': x,
                    'y': y,
                    'text': element.get('fallback_text', '?'),
                    'font_size': element.get('font_size', 12),
                    'color': element.get('color', [255, 255, 255])
                })
                return
            
            # Load and resize icon
            icon = Image.open(icon_path)
            icon = icon.resize((width, height), Image.Resampling.LANCZOS)
            
            # Convert to RGB if needed
            if icon.mode != 'RGB':
                icon = icon.convert('RGB')
            
            # Paste icon onto display
            self.display_manager.canvas.paste(icon, (x, y))
            
        except Exception as e:
            logger.error(f"Error rendering icon element: {e}")
    
    def _render_rectangle_element(self, element: Dict):
        """Render a rectangle element."""
        try:
            x = element.get('x', 0)
            y = element.get('y', 0)
            width = element.get('width', 10)
            height = element.get('height', 10)
            color = tuple(element.get('color', [255, 255, 255]))
            fill = element.get('fill', True)
            
            if fill:
                self.display_manager.draw.rectangle([x, y, x + width, y + height], fill=color)
            else:
                self.display_manager.draw.rectangle([x, y, x + width, y + height], outline=color)
                
        except Exception as e:
            logger.error(f"Error rendering rectangle element: {e}")
    
    def _render_line_element(self, element: Dict):
        """Render a line element."""
        try:
            x1 = element.get('x1', 0)
            y1 = element.get('y1', 0)
            x2 = element.get('x2', 10)
            y2 = element.get('y2', 10)
            color = tuple(element.get('color', [255, 255, 255]))
            
            self.display_manager.draw.line([(x1, y1), (x2, y2)], fill=color)
            
        except Exception as e:
            logger.error(f"Error rendering line element: {e}")
    
    def cleanup(self):
        """Cleanup MQTT connection."""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info("MQTT client disconnected")
