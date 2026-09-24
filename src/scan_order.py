"""Compensate for the order an LED panel lights its rows.

A HUB75 panel with 1:N multiplexing lights its rows in pairs: row ``d`` of the
top half together with row ``d`` of the bottom half, ``d`` running from 0 to
N-1 across each refresh. So the two rows either side of the middle of a panel
are lit at opposite ends of every refresh: the last row of one half near the
end, the first row of the other at the start. When the picture moves, each
refresh shows it one step further on, and those two neighbouring rows -- lit
almost a whole refresh apart -- show the text one step apart. On a strip
scrolling a whole pixel per refresh that is a crisp 1px step across the middle
of every panel, which the eye and a phone camera both see.

Measured on hdpi (4x128x64 on one chain, rotated 180) on 2026-09-24: switching
the panel to interlaced scanning made the step vanish, and halving the scroll
speed halved it, so it is the scan order and not a torn frame. Showing one half
of the panel a refresh behind the other lines those two rows up again. What is
left is a uniform lean of about one step per half from top to bottom, which
reads as nothing at all where the step read as a tear.

Which half lags follows from the geometry. Walking the logical rows top to
bottom, wherever the next row is lit near the start of a refresh and the one
above it near the end, the section below must show one more refresh of lag to
stay continuous with it (and one less where the order jumps the other way).
Stacked parallel chains are lit simultaneously, so each further half adds one.

Only layouts whose physical row order is known are compensated: plain chains,
parallel chains, and a 0 or 180 degree rotation. Other pixel mappers
(U-mapper, 90/270 rotation, ...), special multiplexing and interlaced scan are
left alone, as is the emulator, which has no scan order to compensate for.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, List, Optional, Sequence, Tuple

from PIL import Image

from src.display_geometry import compose_pixel_mapper_config

#: (first row, last row + 1, refreshes of lag), for bands that lag at all.
Band = Tuple[int, int, int]


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rotation(hardware: Mapping[str, Any]) -> Optional[int]:
    """The rotation applied by the pixel mappers, or None if they do anything else."""
    mappers = [m.strip() for m in compose_pixel_mapper_config(hardware).split(';')
               if m.strip()]
    if not mappers:
        return 0
    if len(mappers) == 1 and mappers[0].replace(' ', '') in ('Rotate:0', 'Rotate:180'):
        return int(mappers[0].split(':')[1])
    return None


def scan_lag_bands(hardware: Mapping[str, Any], height: int,
                   setting: str = "auto") -> Optional[List[Band]]:
    """Which rows of the logical canvas to show how many refreshes behind.

    Returns None when compensation is off or the layout is not one this
    understands; see the module docstring.
    """
    if str(setting or "auto").lower() == "off":
        return None
    rows = _int(hardware.get('rows'), 0)
    parallel = max(1, _int(hardware.get('parallel'), 1))
    if rows < 4 or rows % 2:
        return None
    if _int(hardware.get('multiplexing'), 0) != 0 or _int(hardware.get('scan_mode'), 0) != 0:
        return None
    rotation = _rotation(hardware)
    if rotation is None:
        return None
    physical_height = rows * parallel
    if physical_height != height:
        return None   # something remapped the canvas; its row order is unknown

    half = rows // 2

    def phase(y: int) -> int:
        """When in the refresh logical row y is lit, as a row-pair index."""
        physical = y if rotation == 0 else physical_height - 1 - y
        return (physical % rows) % half

    lags = [0]
    for y in range(1, physical_height):
        step = phase(y) - phase(y - 1)
        if step < -half / 2:      # lit near the start after a row lit near the end
            lags.append(lags[-1] + 1)
        elif step > half / 2:     # the other way round
            lags.append(lags[-1] - 1)
        else:
            lags.append(lags[-1])
    low = min(lags)
    bands: List[Band] = []
    for y, lag in enumerate(lags):
        lag -= low
        if bands and bands[-1][2] == lag and bands[-1][1] == y:
            bands[-1] = (bands[-1][0], y + 1, lag)
        else:
            bands.append((y, y + 1, lag))
    return [band for band in bands if band[2] > 0] or None


def compose(image: Image.Image, history: Sequence[Image.Image],
            bands: Sequence[Band]) -> Image.Image:
    """``image`` with each band taken from the frame ``lag`` refreshes back.

    ``history[0]`` is the previous frame. A band whose frame is not available
    yet (the first frames of a scroll) is left current. Returns ``image`` itself
    when nothing changes, so the caller pays for a copy only when it must.
    """
    out = image
    for top, bottom, lag in bands:
        if lag > len(history):
            continue
        source = history[lag - 1]
        if source.size != image.size:
            continue
        if out is image:
            out = image.copy()
        out.paste(source.crop((0, top, source.width, bottom)), (0, top))
    return out
