"""Bookkeeping for live Vegas elements (see src/plugin_system/vegas_elements.py).

A live element travels through the same plumbing as any other Vegas content --
the adapter's cache, a prefetched group, the pipeline's join -- as a PIL image.
What makes it live rides along in the image's ``info`` dict (:data:`INFO_KEY`),
which Pillow copies through ``copy()``, ``crop()``, ``convert()`` and
``resize()``, so none of that plumbing has to change shape. The pipeline reads
the tag back when it places the image in the strip and keeps an
:class:`ElementRecord` of where it went.

Geometry is pinned: a live element is never trimmed to its ink. It is padded
with ``content_padding`` black columns each side, the margin trimming would
have left, so its width in the strip is its image width plus twice that, for
as long as its key is there. That is what lets a redraw be swapped in place.
"""

from __future__ import annotations

import itertools
import threading
import zlib
from typing import Dict, NamedTuple, Optional, Tuple

import numpy as np
from PIL import Image

#: Where a live element's :class:`ElementMeta` rides in ``Image.info``.
INFO_KEY = "ledmatrix.vegas_element"


class ElementMeta(NamedTuple):
    """What the pipeline needs to know about one live element's pixels."""
    plugin_id: str
    key: str
    #: The plugin's data epoch (LiveEpochs) the pixels were drawn from.
    epoch: int
    #: pixel_digest() of the pinned pixels.
    digest: Tuple[Tuple[int, ...], int]
    #: time.monotonic() when drawn.
    rendered_at: float
    refresh_hz: float
    #: The plugin's own version for the pixels, or None.
    version: object = None


class ElementRecord(NamedTuple):
    """Where one live element sits in the strip.

    ``abs_x`` is in absolute strip columns: the strip's own column plus every
    column trimmed off its front since it was composed (the pipeline's
    ``_strip_origin``). Trimming therefore never moves a record.
    """
    seq: int
    plugin_id: str
    key: str
    abs_x: int
    width: int
    epoch: int
    digest: Tuple[Tuple[int, ...], int]
    refresh_hz: float


class RenderedElement(NamedTuple):
    """One live element freshly redrawn by the worker, ready to compare and swap."""
    key: str
    #: The plugin's data epoch it was drawn from.
    epoch: int
    version: object
    #: Pinned pixels (see pin_element), read-only.
    pixels: np.ndarray
    digest: Tuple[Tuple[int, ...], int]
    #: Pinned width, the width it would occupy in the strip.
    width: int


class LivePatch(NamedTuple):
    """A redraw handed from the worker to the render thread for one record."""
    seq: int
    #: The strip generation it was made against; a patch for an older strip
    #: is dropped.
    strip_gen: int
    epoch: int
    pixels: np.ndarray
    digest: Tuple[Tuple[int, ...], int]
    made_at: float


class LiveView(NamedTuple):
    """Where the viewport is, in absolute strip columns, published every frame."""
    abs_left: int
    abs_right: int
    #: The end of the strip: how far ahead content exists.
    abs_end: int
    #: time.monotonic() when published. An old one means frames have stopped.
    t_mono: float


def tag(image: Image.Image, meta: ElementMeta) -> Image.Image:
    """Mark ``image`` as the live element ``meta`` describes. Returns it."""
    image.info[INFO_KEY] = meta
    return image


def meta_of(image: object) -> Optional[ElementMeta]:
    """The live-element tag on ``image``, or None for plain content."""
    info = getattr(image, 'info', None)
    if not isinstance(info, dict):
        return None
    meta = info.get(INFO_KEY)
    return meta if isinstance(meta, ElementMeta) else None


def untag(image: Image.Image) -> Image.Image:
    """Make ``image`` plain content again (e.g. after cropping it). Returns it."""
    image.info.pop(INFO_KEY, None)
    return image


def pin_element(image: Image.Image, padding: int) -> Tuple[Image.Image, np.ndarray]:
    """An element's pixels as they will sit in the strip, as image and array.

    RGB, with ``padding`` black columns each side. The array is what a live
    patch writes into the strip; it is read-only, so a patch in flight cannot
    be changed under the render thread.
    """
    if image.mode != 'RGB':
        image = image.convert('RGB')
    pad = max(0, int(padding))
    if pad:
        pinned = Image.new('RGB', (image.width + 2 * pad, image.height), (0, 0, 0))
        pinned.paste(image, (pad, 0))
    else:
        pinned = image.copy()
    array = np.ascontiguousarray(np.asarray(pinned))
    array.setflags(write=False)
    return pinned, array


def pixel_digest(array: np.ndarray) -> Tuple[Tuple[int, ...], int]:
    """A cheap fingerprint of an element's pixels: its shape and a CRC.

    Two redraws with the same digest are treated as the same pixels and the
    second is not swapped in. CRC-32 rather than Adler-32: a changed digit is
    a small, local change, which is exactly where Adler-32 is weakest.
    """
    data = np.ascontiguousarray(array)
    return tuple(data.shape), zlib.crc32(memoryview(data).cast('B'))


class LiveEpochs:
    """A counter per plugin that moves on whenever its data may have changed.

    Bumped when a plugin's update() completes (PluginManager's update
    listener) or when it calls notify_vegas_data_changed(). Every live element
    is tagged with the epoch it was drawn from; one drawn from an older epoch
    than the plugin's current one is due a redraw. Epochs are the truth and
    wake-ups only hints, so a missed wake-up delays a redraw but never loses
    one.
    """

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._epochs: Dict[str, int] = {}
        self._lock = threading.Lock()

    def bump(self, plugin_id: str) -> int:
        with self._lock:
            epoch = next(self._counter)
            self._epochs[plugin_id] = epoch
            return epoch

    def get(self, plugin_id: str) -> int:
        return self._epochs.get(plugin_id, 0)
