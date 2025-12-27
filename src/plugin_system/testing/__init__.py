"""
Plugin Testing Framework

Provides base classes and utilities for testing LEDMatrix plugins.
"""

from .plugin_test_base import PluginTestCase
from .mocks import MockDisplayManager, MockCacheManager, MockConfigManager, MockPluginManager

__all__ = [
    'PluginTestCase',
    'MockDisplayManager',
    'MockCacheManager',
    'MockConfigManager',
    'MockPluginManager'
]

