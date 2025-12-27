"""
Web UI Info Plugin for LEDMatrix

A simple plugin that displays the web UI URL for easy access.
Shows "visit web ui at http://[deviceID]:5000"

API Version: 1.0.0
"""

import logging
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont

from src.plugin_system.base_plugin import BasePlugin

logger = logging.getLogger(__name__)


class WebUIInfoPlugin(BasePlugin):
    """
    Web UI Info plugin that displays the web UI URL.
    
    Configuration options:
        display_duration (float): Display duration in seconds (default: 10)
        enabled (bool): Enable/disable plugin (default: true)
    """
    
    def __init__(self, plugin_id: str, config: Dict[str, Any],
                 display_manager, cache_manager, plugin_manager):
        """Initialize the Web UI Info plugin."""
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        
        # Get device hostname
        try:
            self.device_id = socket.gethostname()
        except Exception as e:
            self.logger.warning(f"Could not get hostname: {e}, using 'localhost'")
            self.device_id = "localhost"
        
        # Get device IP address
        self.device_ip = self._get_local_ip()
        
        # IP refresh tracking
        self.last_ip_refresh = time.time()
        self.ip_refresh_interval = 30.0  # Refresh IP every 30 seconds
        
        # Rotation state
        self.current_display_mode = "hostname"  # "hostname" or "ip"
        self.last_rotation_time = time.time()
        self.rotation_interval = 10.0  # Rotate every 10 seconds
        
        self.web_ui_url = f"http://{self.device_id}:5000"
        
        self.logger.info(f"Web UI Info plugin initialized - Hostname: {self.device_id}, IP: {self.device_ip}")
    
    def _is_ap_mode_active(self) -> bool:
        """
        Check if AP mode is currently active.
        
        Returns:
            bool: True if AP mode is active, False otherwise
        """
        try:
            # Check if hostapd service is running
            result = subprocess.run(
                ["systemctl", "is-active", "hostapd"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0 and result.stdout.strip() == "active":
                return True
            
            # Check if wlan0 has AP mode IP (192.168.4.1)
            result = subprocess.run(
                ["ip", "addr", "show", "wlan0"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0 and "192.168.4.1" in result.stdout:
                return True
            
            return False
        except Exception as e:
            self.logger.debug(f"Error checking AP mode status: {e}")
            return False
    
    def _get_local_ip(self) -> str:
        """
        Get the local IP address of the device using network interfaces.
        Handles AP mode, no internet connectivity, and network state changes.
        
        Returns:
            str: Local IP address, or "localhost" if unable to determine
        """
        # First check if AP mode is active
        if self._is_ap_mode_active():
            self.logger.debug("AP mode detected, returning AP IP: 192.168.4.1")
            return "192.168.4.1"
        
        try:
            # Try using 'hostname -I' first (fastest, gets all IPs)
            result = subprocess.run(
                ["hostname", "-I"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                ips = result.stdout.strip().split()
                # Filter out loopback and AP mode IPs
                for ip in ips:
                    ip = ip.strip()
                    if ip and not ip.startswith("127.") and ip != "192.168.4.1":
                        self.logger.debug(f"Found IP via hostname -I: {ip}")
                        return ip
            
            # Fallback: Use 'ip addr show' to get interface IPs
            result = subprocess.run(
                ["ip", "-4", "addr", "show"],
                capture_output=True,
                text=True,
                timeout=3
            )
            if result.returncode == 0:
                current_interface = None
                for line in result.stdout.split('\n'):
                    line = line.strip()
                    # Check for interface name
                    if ':' in line and not line.startswith('inet'):
                        parts = line.split(':')
                        if len(parts) >= 2:
                            current_interface = parts[1].strip().split('@')[0]
                    # Check for inet address
                    elif line.startswith('inet '):
                        parts = line.split()
                        if len(parts) >= 2:
                            ip_with_cidr = parts[1]
                            ip = ip_with_cidr.split('/')[0]
                            # Skip loopback and AP mode IPs
                            if not ip.startswith("127.") and ip != "192.168.4.1":
                                # Prefer eth0/ethernet interfaces, then wlan0, then others
                                if current_interface and (
                                    current_interface.startswith("eth") or 
                                    current_interface.startswith("enp")
                                ):
                                    self.logger.debug(f"Found Ethernet IP: {ip} on {current_interface}")
                                    return ip
                                elif current_interface == "wlan0":
                                    self.logger.debug(f"Found WiFi IP: {ip} on {current_interface}")
                                    return ip
            
            # Fallback: Try socket method (requires internet connectivity)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    # Connect to a public DNS server (doesn't actually connect)
                    s.connect(('8.8.8.8', 80))
                    ip = s.getsockname()[0]
                    if ip and not ip.startswith("127.") and ip != "192.168.4.1":
                        self.logger.debug(f"Found IP via socket method: {ip}")
                        return ip
                finally:
                    s.close()
            except Exception:
                pass
            
            # Last resort: try hostname resolution (often returns 127.0.0.1)
            try:
                ip = socket.gethostbyname(socket.gethostname())
                if ip and not ip.startswith("127.") and ip != "192.168.4.1":
                    self.logger.debug(f"Found IP via hostname resolution: {ip}")
                    return ip
            except Exception:
                pass
            
            self.logger.warning("Could not determine IP address, using 'localhost'")
            return "localhost"
            
        except Exception as e:
            self.logger.warning(f"Error getting IP address: {e}, using 'localhost'")
            return "localhost"
    
    def update(self) -> None:
        """
        Update method - refreshes IP address periodically to handle network state changes.
        
        The hostname is determined at initialization and doesn't change,
        but IP address can change when network state changes (WiFi connect/disconnect, AP mode, etc.)
        """
        current_time = time.time()
        if current_time - self.last_ip_refresh >= self.ip_refresh_interval:
            # Refresh IP address to handle network state changes
            new_ip = self._get_local_ip()
            if new_ip != self.device_ip:
                self.logger.info(f"IP address changed from {self.device_ip} to {new_ip}")
                self.device_ip = new_ip
            self.last_ip_refresh = current_time
    
    def display(self, force_clear: bool = False) -> None:
        """
        Display the web UI URL message.
        Rotates between hostname and IP address every 10 seconds.
        
        Args:
            force_clear: If True, clear display before rendering
        """
        try:
            # Check if we need to rotate between hostname and IP
            current_time = time.time()
            if current_time - self.last_rotation_time >= self.rotation_interval:
                # Switch display mode
                if self.current_display_mode == "hostname":
                    self.current_display_mode = "ip"
                else:
                    self.current_display_mode = "hostname"
                self.last_rotation_time = current_time
                self.logger.debug(f"Rotated to display mode: {self.current_display_mode}")
            
            if force_clear:
                self.display_manager.clear()
            
            # Get display dimensions
            width = self.display_manager.matrix.width
            height = self.display_manager.matrix.height
            
            # Create a new image for the display
            img = Image.new('RGB', (width, height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            # Try to load a small font
            # Try to find project root and use assets/fonts
            font_small = None
            try:
                # Try to find project root (parent of plugins directory)
                current_dir = Path(__file__).resolve().parent
                project_root = current_dir.parent.parent
                font_path = project_root / "assets" / "fonts" / "4x6-font.ttf"
                
                if font_path.exists():
                    font_small = ImageFont.truetype(str(font_path), 6)
                else:
                    # Try relative path from current working directory
                    font_path = "assets/fonts/4x6-font.ttf"
                    if os.path.exists(font_path):
                        font_small = ImageFont.truetype(font_path, 6)
                    else:
                        font_small = ImageFont.load_default()
            except Exception as e:
                self.logger.debug(f"Could not load custom font: {e}, using default")
                font_small = ImageFont.load_default()
            
            # Determine which address to display
            if self.current_display_mode == "ip":
                address = self.device_ip
            else:
                address = self.device_id
            
            # Prepare text to display
            lines = [
                "visit web ui",
                f"at {address}:5000"
            ]
            
            # Calculate text positions (centered)
            y_start = 5
            line_height = 8
            total_height = len(lines) * line_height
            
            # Draw each line
            for i, line in enumerate(lines):
                # Get text size for centering
                bbox = draw.textbbox((0, 0), line, font=font_small)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                
                # Center horizontally
                x = (width - text_width) // 2
                y = y_start + (i * line_height)
                
                # Draw text in white
                draw.text((x, y), line, font=font_small, fill=(255, 255, 255))
            
            # Set the image on the display manager
            self.display_manager.image = img
            
            # Update the display
            self.display_manager.update_display()
            
            self.logger.debug(f"Displayed web UI info: {address}:5000 (mode: {self.current_display_mode})")
            
        except Exception as e:
            self.logger.error(f"Error displaying web UI info: {e}")
            # Fallback: just clear the display
            try:
                self.display_manager.clear()
                self.display_manager.update_display()
            except:
                pass
    
    def get_display_duration(self) -> float:
        """Get display duration from config."""
        return self.config.get('display_duration', 10.0)
    
    def validate_config(self) -> bool:
        """Validate plugin configuration."""
        # Call parent validation first
        if not super().validate_config():
            return False
        
        # No additional validation needed - this is a simple plugin
        return True
    
    def get_info(self) -> Dict[str, Any]:
        """Return plugin info for web UI."""
        info = super().get_info()
        info.update({
            'device_id': self.device_id,
            'device_ip': self.device_ip,
            'web_ui_url': self.web_ui_url,
            'current_display_mode': self.current_display_mode
        })
        return info

