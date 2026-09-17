"""Display size from config: the one computation the size readers share.

``DisplayManager`` sizes its canvas from ``display.hardware`` plus
``display.double_sided``. The web preview endpoints (``/display/current``, the
SSE fallback), the Starlark magnify default and ``scripts/dev/vegas_audit.py``
used to re-derive that size themselves, each with its own defaults
(``chain_length`` fell back to 2 in one place and 1 in others) and none of
them applying double-sided mode. They now call this module. (The multi-display
sync handshake doesn't compute a size; it shares only
``DEFAULT_CHAIN_LENGTH``.)

The size includes what the rgbmatrix library's pixel mappers do to it --
``orientation`` and ``pixel_mapper_config`` (``Rotate:90`` swaps the axes,
``U-mapper`` folds the chain) -- because ``RGBMatrix.width``/``height``, which
``DisplayManager`` reports on hardware, are measured after them.

Kept free of hardware imports on purpose: the web interface imports it, and
``display_manager`` pulls in ``rgbmatrix``.
"""

import logging
import re
from typing import Any, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# Match config/config.template.json's display.hardware block.
DEFAULT_ROWS = 32
DEFAULT_COLS = 64
DEFAULT_CHAIN_LENGTH = 2
DEFAULT_PARALLEL = 1

#: ``display.hardware.orientation`` -> degrees of the ``Rotate`` mapper
#: DisplayManager appends to ``pixel_mapper_config`` (None: no mapper).
ORIENTATION_ROTATE_DEGREES = {'normal': None, '90': 90, '180': 180, '270': 270}


def _display(config: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    # A hand-edited config.json can hold anything here; treat a non-mapping
    # like a missing block so callers get the defaults, not AttributeError.
    display = (config or {}).get('display')
    return display if isinstance(display, Mapping) else {}


def _hardware(config: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    hw = _display(config).get('hardware')
    return hw if isinstance(hw, Mapping) else {}


def compose_pixel_mapper_config(hardware: Mapping[str, Any]) -> str:
    """The ``pixel_mapper_config`` DisplayManager hands the library.

    ``pixel_mapper_config`` stays a free-form advanced field (e.g. "U-mapper"
    for chain layouts); ``orientation`` is the user-facing mounting rotation,
    appended as a trailing ``Rotate:<deg>`` mapper rather than overwriting it.
    """
    base = hardware.get('pixel_mapper_config') or ''
    base = base.strip() if isinstance(base, str) else ''
    degrees = ORIENTATION_ROTATE_DEGREES.get(hardware.get('orientation', 'normal'))
    if degrees is None:
        return base
    rotate = f'Rotate:{degrees}'
    return f'{base};{rotate}' if base else rotate


def _c_div(a: int, b: int) -> int:
    """C integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _c_strtol(text: str) -> Tuple[int, str]:
    """strtol(text, &end, 10): the parsed value (0 if none) and the rest."""
    match = re.match(r'\s*([+-]?\d+)', text)
    if not match:
        return 0, text
    return int(match.group(1)), text[match.end():]


def _remap_size(param: Optional[str], width: int, height: int,
                chain: int, parallel: int) -> Optional[Tuple[int, int]]:
    """RemapMapper::SetParameters and GetSizeMapping (lib/pixel-mapper.cc)."""
    if not param:
        return None
    new_w, rest = _c_strtol(param)
    if not rest.startswith(','):
        return None
    new_h, rest = _c_strtol(rest[1:])
    if not rest.startswith('|'):
        return None
    rest = rest[1:]
    tiles = []
    while rest:
        x, rest = _c_strtol(rest)
        if not rest.startswith(','):
            return None
        y, rest = _c_strtol(rest[1:])
        if not rest or rest[0].lower() not in 'neswx':
            return None
        tiles.append((x, y, rest[0].lower()))
        rest = rest[1:]
        if rest.startswith('|'):
            rest = rest[1:]
        elif rest:
            return None
    if len(tiles) != chain * parallel:
        return None
    panel_w, panel_h = _c_div(width, chain), _c_div(height, parallel)
    for x, y, kind in tiles:
        if kind == 'x':
            continue
        # MapTile::MapToVisible of the panel's (0, 0) and far corner.
        x0, y0, x1, y1 = {
            'n': (x, y, x + panel_w - 1, y + panel_h - 1),
            'w': (x, y + panel_w - 1, x + panel_h - 1, y),
            's': (x + panel_w - 1, y + panel_h - 1, x, y),
            'e': (x + panel_h - 1, y, x, y + panel_w - 1),
        }[kind]
        if x1 < 0 or x0 >= new_w or y1 < 0 or y0 >= new_h:
            return None
    return new_w, new_h


def apply_pixel_mappers(width: int, height: int, mapper_config: str,
                        chain: int, parallel: int) -> Tuple[int, int]:
    """The canvas size after the library applies ``mapper_config``.

    Mirrors ``RGBMatrix::Impl::ApplyNamedPixelMappers`` and each built-in
    mapper's ``SetParameters``/``GetSizeMapping`` in the pinned
    ``lib/pixel-mapper.cc``: mappers apply left to right, and one the library
    doesn't know or can't configure is skipped and leaves the size alone.
    ``multiplexing`` isn't modelled: its mappers give back the configured
    size for the panel sizes they are made for.
    """
    for entry in (mapper_config or '').split(';'):
        name, colon, param = entry.partition(':')
        name = name.lower()
        param = param if colon else None
        if name == 'rotate':
            if not param:
                continue
            angle, rest = _c_strtol(param)
            if rest or angle % 90:
                continue
            if angle % 180:
                width, height = height, width
        elif name == 'u-mapper':
            if chain < 2 or chain % 2 or height % parallel:
                continue
            width, height = _c_div(width, 64) * 32, 2 * height
        elif name == 'v-mapper':
            width, height = (_c_div(width * parallel, chain),
                             _c_div(height * chain, parallel))
        elif name == 'stacktorow':
            if param and any(c not in 'ZzFf, ' for c in param):
                continue
            width, height = width * parallel, _c_div(height, parallel)
        elif name == 'remap':
            size = _remap_size(param, width, height, chain, parallel)
            if size is not None:
                width, height = size
        # "mirror" keeps the size; the library skips names it doesn't know.
    return width, height


def physical_size(config: Optional[Mapping[str, Any]]) -> Tuple[int, int]:
    """Width and height of the whole panel chain, in pixels.

    ``cols * chain_length`` by ``rows * parallel``, then through the pixel
    mappers ``orientation`` and ``pixel_mapper_config`` set up -- what
    ``RGBMatrix.width``/``height`` report. Raises ``ValueError`` or
    ``TypeError`` on a non-numeric value, as ``DisplayManager`` does; callers
    decide their own fallback.

    A non-finite value (``Infinity``, which Python's JSON parser accepts in a
    hand-edited config.json) raises ``ValueError`` too, not ``OverflowError``,
    so every caller's existing fallback catches it.
    """
    hw = _hardware(config)
    try:
        rows = int(hw.get('rows', DEFAULT_ROWS))
        cols = int(hw.get('cols', DEFAULT_COLS))
        chain_length = int(hw.get('chain_length', DEFAULT_CHAIN_LENGTH))
        parallel = int(hw.get('parallel', DEFAULT_PARALLEL))
    except OverflowError as e:
        raise ValueError(f"display.hardware size is not finite: {e}") from e
    width, height = max(1, cols * chain_length), max(1, rows * parallel)
    if chain_length >= 1 and parallel >= 1:
        width, height = apply_pixel_mappers(
            width, height, compose_pixel_mapper_config(hw), chain_length, parallel)
    return max(1, width), max(1, height)


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
