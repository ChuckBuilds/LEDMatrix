"""
Common utilities and helpers for LEDMatrix.

This package provides reusable functionality for plugins and core modules:
- API helpers
- Logo helpers
- Text/scroll helpers
- Adaptive layout and image helpers

The names below are imported on first use (PEP 562), not when the package is
imported. ``from src.common import ScrollHelper`` and
``src.common.ScrollHelper`` work as before and return the same objects, but
``import src.common`` -- or importing any submodule, such as
``src.common.path_safety`` -- no longer loads numpy, requests and freetype
along with every helper. The web interface imports src.common only for a few
small modules and never needs those.
"""

import importlib
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    # What mypy and editors see: the real names and their types.
    from src.common.api_helper import APIHelper
    from src.common.scroll_helper import ScrollHelper
    from src.common import scroll_config
    from src.common.scroll_config import (
        ScrollSettings,
        configure as configure_scroll,
        resolve as resolve_scroll_settings,
        refresh_hz_from_config,
    )
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

#: Exported name -> (module it lives in, attribute name there). An attribute
#: of None means the name is the module itself. Keep in step with the
#: TYPE_CHECKING imports above and with __all__.
_LAZY: Dict[str, Tuple[str, Optional[str]]] = {
    'APIHelper': ('src.common.api_helper', 'APIHelper'),
    'ScrollHelper': ('src.common.scroll_helper', 'ScrollHelper'),
    'scroll_config': ('src.common.scroll_config', None),
    'ScrollSettings': ('src.common.scroll_config', 'ScrollSettings'),
    'configure_scroll': ('src.common.scroll_config', 'configure'),
    'resolve_scroll_settings': ('src.common.scroll_config', 'resolve'),
    'refresh_hz_from_config': ('src.common.scroll_config', 'refresh_hz_from_config'),
    'LogoHelper': ('src.common.logo_helper', 'LogoHelper'),
    'TextHelper': ('src.common.text_helper', 'TextHelper'),
    # adaptive layout & images
    'Region': ('src.adaptive_layout', 'Region'),
    'LayoutContext': ('src.adaptive_layout', 'LayoutContext'),
    'FontStep': ('src.adaptive_layout', 'FontStep'),
    'FontLadder': ('src.adaptive_layout', 'FontLadder'),
    'LADDER_GRID': ('src.adaptive_layout', 'LADDER_GRID'),
    'LADDER_ARCADE': ('src.adaptive_layout', 'LADDER_ARCADE'),
    'FitResult': ('src.adaptive_layout', 'FitResult'),
    'draw_fitted_text': ('src.adaptive_layout', 'draw_fitted_text'),
    'ScoreboardRegions': ('src.adaptive_layout', 'ScoreboardRegions'),
    'scoreboard_regions': ('src.adaptive_layout', 'scoreboard_regions'),
    'MediaRow': ('src.adaptive_layout', 'MediaRow'),
    'media_row': ('src.adaptive_layout', 'media_row'),
    'ImageFitResult': ('src.adaptive_images', 'ImageFitResult'),
    'fit_image': ('src.adaptive_images', 'fit_image'),
    'draw_fitted_image': ('src.adaptive_images', 'draw_fitted_image'),
    'RESAMPLE_LANCZOS': ('src.adaptive_images', 'RESAMPLE_LANCZOS'),
    'RESAMPLE_NEAREST': ('src.adaptive_images', 'RESAMPLE_NEAREST'),
}

__all__ = [
    'APIHelper',
    'ScrollHelper',
    'scroll_config',
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


def __getattr__(name: str) -> Any:
    """Import an exported name on first access (PEP 562).

    Only called for names not already in the module namespace, so after the
    first access the cached value below is returned directly. Unknown names
    raise AttributeError, which ``from src.common import <submodule>`` relies
    on to fall through to importing the submodule.
    """
    try:
        module_name, attr = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(module_name)
    value = module if attr is None else getattr(module, attr)
    globals()[name] = value
    return value


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(__all__))
