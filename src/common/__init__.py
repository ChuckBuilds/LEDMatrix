"""
Common utilities and helpers for LEDMatrix.

This package provides reusable functionality for plugins and core modules:
- API helpers
- Logo helpers
- Text/scroll helpers
- Adaptive layout and image helpers
"""

# Export commonly used utilities
from src.common.api_helper import APIHelper
from src.common.scroll_helper import ScrollHelper
from src.common import frame_pacing, scroll_config
from src.common.scroll_config import (
    ScrollSettings,
    configure as configure_scroll,
    resolve as resolve_scroll_settings,
    refresh_hz_from_config,
)
from src.common.frame_pacing import PacingReport, analyze as analyze_frame_pacing
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
    'APIHelper',
    'ScrollHelper',
    'scroll_config',
    'frame_pacing',
    'PacingReport',
    'analyze_frame_pacing',
    'ScrollSettings',
    'configure_scroll',
    'resolve_scroll_settings',
    'refresh_hz_from_config',
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
