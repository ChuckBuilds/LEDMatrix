"""Display size from config: the one computation every caller shares.

``DisplayManager`` sizes its canvas from ``display.hardware`` plus
``display.double_sided``. The web preview, the Starlark magnify default and
the multi-display sync handshake used to re-derive that size themselves,
each with its own defaults (``chain_length`` fell back to 2 in one place and
1 in three others) and none of them applying double-sided mode. They now all
call this module.

Kept free of hardware imports on purpose: the web interface imports it, and
``display_manager`` pulls in ``rgbmatrix``.
"""

import logging
from typing import Any, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# Match config/config.template.json's display.hardware block.
DEFAULT_ROWS = 32
DEFAULT_COLS = 64
DEFAULT_CHAIN_LENGTH = 2
DEFAULT_PARALLEL = 1


def _display(config: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    # A hand-edited config.json can hold anything here; treat a non-mapping
    # like a missing block so callers get the defaults, not AttributeError.
    display = (config or {}).get('display')
    return display if isinstance(display, Mapping) else {}


def _hardware(config: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    hw = _display(config).get('hardware')
    return hw if isinstance(hw, Mapping) else {}


def physical_size(config: Optional[Mapping[str, Any]]) -> Tuple[int, int]:
    """Width and height of the whole panel chain, in pixels.

    ``cols * chain_length`` by ``rows * parallel``. Raises ``ValueError`` or
    ``TypeError`` on a non-numeric value, as ``DisplayManager`` does; callers
    decide their own fallback.
    """
    hw = _hardware(config)
    rows = int(hw.get('rows', DEFAULT_ROWS))
    cols = int(hw.get('cols', DEFAULT_COLS))
    chain_length = int(hw.get('chain_length', DEFAULT_CHAIN_LENGTH))
    parallel = int(hw.get('parallel', DEFAULT_PARALLEL))
    return max(1, cols * chain_length), max(1, rows * parallel)


def resolve_double_sided(physical_width: int, physical_height: int,
                         ds_config: Dict[str, Any],
                         quiet: bool = False) -> Optional[Dict[str, Any]]:
    """Validate the ``display.double_sided`` config against the physical size.

    Returns a dict ``{copies, axis, logical_width, logical_height}`` when the
    feature is enabled and the physical panel divides evenly into ``copies``
    along the chosen axis, otherwise ``None`` (single-screen behaviour). Bad
    config is logged and disabled rather than raised — a misconfigured panel
    should still light up.

    Only pixels are checked, not whole panels: ``chain_length`` and
    ``parallel`` don't say which axis a panel lies on once an orientation
    ``Rotate:`` or U-mapper ``pixel_mapper_config`` rearranges the chain.

    ``quiet`` suppresses the log lines, for callers that run on every web
    request and would otherwise repeat them on each poll.
    """
    def _log(level, *args):
        if not quiet:
            logger.log(level, *args)

    if not isinstance(ds_config, dict) or not ds_config.get('enabled', False):
        return None

    copies = ds_config.get('copies', 2)
    if not isinstance(copies, int) or copies < 2:
        _log(logging.WARNING,
             "double_sided: 'copies' must be an integer >= 2 (got %r); "
             "disabling double-sided mode", copies)
        return None

    axis = ds_config.get('axis', 'horizontal')
    if axis not in ('horizontal', 'vertical'):
        _log(logging.WARNING,
             "double_sided: 'axis' must be 'horizontal' or 'vertical' "
             "(got %r); defaulting to 'horizontal'", axis)
        axis = 'horizontal'

    # Horizontal splits the chain (panels side by side); vertical splits the
    # parallel outputs (panels stacked). The split axis must divide evenly.
    if axis == 'horizontal':
        if physical_width % copies != 0:
            _log(logging.WARNING,
                 "double_sided: physical width %d is not divisible by copies "
                 "%d; disabling double-sided mode", physical_width, copies)
            return None
        logical_width = physical_width // copies
        logical_height = physical_height
    else:
        if physical_height % copies != 0:
            _log(logging.WARNING,
                 "double_sided: physical height %d is not divisible by copies "
                 "%d; disabling double-sided mode", physical_height, copies)
            return None
        logical_width = physical_width
        logical_height = physical_height // copies

    _log(logging.INFO,
         "double_sided enabled: %d copies on %s axis — logical screen %dx%d "
         "tiled across physical %dx%d", copies, axis, logical_width,
         logical_height, physical_width, physical_height)
    return {
        'copies': copies,
        'axis': axis,
        'logical_width': logical_width,
        'logical_height': logical_height,
    }


def logical_size(config: Optional[Mapping[str, Any]],
                 quiet: bool = True) -> Tuple[int, int]:
    """The size plugins draw at and the web preview shows.

    The physical size, divided by ``double_sided.copies`` along its axis when
    double-sided mode is enabled and valid — the same answer
    ``DisplayManager.width``/``height`` give.
    """
    width, height = physical_size(config)
    ds = resolve_double_sided(width, height,
                              _display(config).get('double_sided') or {},
                              quiet=quiet)
    if ds is not None:
        return ds['logical_width'], ds['logical_height']
    return width, height
