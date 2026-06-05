"""
Plugin Testing Framework

Provides base classes and utilities for testing LEDMatrix plugins.
"""

from .plugin_test_base import PluginTestCase
from .mocks import MockDisplayManager, MockCacheManager, MockConfigManager, MockPluginManager
from .visual_display_manager import VisualTestDisplayManager
from .bounds_display_manager import BoundsCheckingDisplayManager
from .sizes import (
    DEFAULT_TEST_SIZES, SUPPORTED_SIZES, resolve_test_sizes, size_label,
)

__all__ = [
    'PluginTestCase',
    'VisualTestDisplayManager',
    'BoundsCheckingDisplayManager',
    'MockDisplayManager',
    'MockCacheManager',
    'MockConfigManager',
    'MockPluginManager',
    'DEFAULT_TEST_SIZES',
    'SUPPORTED_SIZES',
    'resolve_test_sizes',
    'size_label',
]

