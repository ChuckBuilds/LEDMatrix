"""
Common utilities and helpers for LEDMatrix.

This package provides reusable functionality for plugins and core modules:
- Error handling utilities
- API helpers
- Configuration helpers
- Display helpers
- Game/team helpers
- Logo helpers
- Text/scroll helpers
- General utilities
"""

# Export commonly used utilities
from src.common.error_handler import (
    handle_file_operation,
    handle_json_operation,
    safe_execute,
    retry_on_failure,
    log_and_continue,
    log_and_raise
)
from src.common.api_helper import APIHelper
from src.common.scroll_helper import ScrollHelper
from src.common.logo_helper import LogoHelper
from src.common.text_helper import TextHelper

__all__ = [
    'handle_file_operation',
    'handle_json_operation',
    'safe_execute',
    'retry_on_failure',
    'log_and_continue',
    'log_and_raise',
    'APIHelper',
    'ScrollHelper',
    'LogoHelper',
    'TextHelper',
]
