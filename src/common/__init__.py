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

# Adaptive layout & images (canonical homes: src.adaptive_layout /
# src.adaptive_images — re-exported here so plugin authors find them in the
# blessed-helpers package). See docs/ADAPTIVE_LAYOUT.md.
from src.adaptive_layout import (
    Region,
    LayoutContext,
    FontStep,
    FontLadder,
    LADDER_GRID,
    LADDER_ARCADE,
    FitResult,
    draw_fitted_text,
    ScoreboardRegions,
    scoreboard_regions,
    MediaRow,
    media_row,
)
from src.adaptive_images import (
    ImageFitResult,
    fit_image,
    draw_fitted_image,
    RESAMPLE_LANCZOS,
    RESAMPLE_NEAREST,
)

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
    # adaptive layout & images
    'Region',
    'LayoutContext',
    'FontStep',
    'FontLadder',
    'LADDER_GRID',
    'LADDER_ARCADE',
    'FitResult',
    'draw_fitted_text',
    'ScoreboardRegions',
    'scoreboard_regions',
    'MediaRow',
    'media_row',
    'ImageFitResult',
    'fit_image',
    'draw_fitted_image',
    'RESAMPLE_LANCZOS',
    'RESAMPLE_NEAREST',
]
