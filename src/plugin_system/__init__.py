"""
LEDMatrix Plugin System

This module provides the core plugin infrastructure for the LEDMatrix project.
It enables dynamic loading, management, and discovery of display plugins.
"""

__version__ = "1.0.0"

from .base_plugin import BasePlugin
from .plugin_manager import PluginManager

__all__ = [
    'BasePlugin',
    'PluginManager',
]

