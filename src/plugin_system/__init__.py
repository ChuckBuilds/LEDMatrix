"""
LEDMatrix Plugin System

This module provides the core plugin infrastructure for the LEDMatrix project.
It enables dynamic loading, management, and discovery of display plugins.

API Version: 1.0.0
"""

__version__ = "1.0.0"
__api_version__ = "1.0.0"

from .base_plugin import BasePlugin
from .plugin_manager import PluginManager

# Import store_manager only when needed to avoid dependency issues
def get_store_manager():
    """Get PluginStoreManager, importing only when needed."""
    try:
        from .store_manager import PluginStoreManager
        return PluginStoreManager
    except ImportError as e:
        raise ImportError("PluginStoreManager requires additional dependencies. Install requests: pip install requests") from e

__all__ = [
    'BasePlugin',
    'PluginManager',
    'get_store_manager',
]

