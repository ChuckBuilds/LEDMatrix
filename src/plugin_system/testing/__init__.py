"""
Plugin Testing Framework

Provides base classes and utilities for testing LEDMatrix plugins.
"""

from .plugin_test_base import PluginTestCase
from .mocks import MockDisplayManager, MockCacheManager, MockConfigManager, MockPluginManager
from .visual_display_manager import VisualTestDisplayManager

__all__ = [
    'PluginTestCase',
    'VisualTestDisplayManager',
    'MockDisplayManager',
    'MockCacheManager',
    'MockConfigManager',
    'MockPluginManager',
]

