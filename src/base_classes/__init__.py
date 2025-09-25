"""
Base classes for the LED Matrix project.

This module contains standardized base classes and mixins for common functionality
across different display managers, including scrolling, sports data handling,
and API interactions.
"""

from base_classes.scroll_base import BaseScrollController, ScrollMode, ScrollDirection, ScrollMetrics
from base_classes.scroll_mixin import ScrollMixin, LegacyScrollAdapter

__all__ = [
    'BaseScrollController',
    'ScrollMode', 
    'ScrollDirection',
    'ScrollMetrics',
    'ScrollMixin',
    'LegacyScrollAdapter'
]

__version__ = '1.0.0'
