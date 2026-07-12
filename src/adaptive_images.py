"""
Adaptive image fitting for plugins — the image counterpart to
src/adaptive_layout.py's text fitting.

Promotes the proven in-field image patterns into one shared helper so
plugins stop hand-copying resize/cache code:

- "crop transparent padding, then fill the row height" (football/hockey
  logo pattern) -> ``crop_to_ink=True, mode="fill_height"``
- "crop-to-fill with a top anchor for faces" (masters-tournament headshot
  pattern) -> ``mode="cover", anchor="top"``
- "letterbox to fit, centered on a background" (static-image pattern)
  -> ``mode="contain"``
- NEAREST for pixel art/flags vs LANCZOS for photos (masters flag pattern)
  -> ``resample=RESAMPLE_NEAREST``

Unlike PIL's ``thumbnail()`` (downscale-only — the reason plugin imagery
stays tiny on big panels), ``fit_image`` upscales by default so content
genuinely adapts to larger displays; pass ``upscale=False`` for the old
behavior.

Use via ``LayoutContext.fit_image(...)`` (cached per panel size) or
``BasePlugin.draw_image(...)``; the module-level functions are the
uncached primitives.
"""

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from PIL import Image

# The one Pillow >= 9.1 compat shim (replaces the per-plugin copies).
try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
    RESAMPLE_NEAREST = Image.Resampling.NEAREST
except AttributeError:  # Pillow < 9.1
    RESAMPLE_LANCZOS = Image.LANCZOS
    RESAMPLE_NEAREST = Image.NEAREST

FIT_MODES = ("contain", "cover", "fill_height", "stretch")


@dataclass(frozen=True)
class ImageFitResult:
    """A processed RGBA copy of a source image, sized for a target box."""
    image: Image.Image
    width: int
    height: int
    scale: float          # scale applied vs the (possibly ink-cropped) source
    mode: str
    source_size: Tuple[int, int]

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0


_EMPTY_IMAGE = Image.new("RGBA", (1, 1), (0, 0, 0, 0))


def _empty_result(mode: str, source_size: Tuple[int, int]) -> ImageFitResult:
    return ImageFitResult(_EMPTY_IMAGE, 0, 0, 0.0, mode, source_size)


def _box_dims(box: Any) -> Tuple[int, int]:
    """Accept a Region (duck-typed .w/.h) or a (w, h) tuple."""
    if hasattr(box, "w") and hasattr(box, "h"):
        return (int(box.w), int(box.h))
    w, h = box
    return (int(w), int(h))


def fit_image(img: Image.Image, box: Any, *, mode: str = "contain",
              crop_to_ink: bool = False, anchor: str = "center",
              resample: Any = None, upscale: bool = True) -> ImageFitResult:
    """Fit an image into a box, preserving crispness policy per content type.

    Args:
        img: Source PIL image (any mode; output is always RGBA).
        box: Region or (w, h) target box.
        mode: "contain" (letterbox), "cover" (crop-to-fill),
              "fill_height" (height == box height, contain-capped by width),
              "stretch" (exact resize).
        crop_to_ink: Trim fully-transparent padding (getbbox) before fitting —
              logos shipped with generous padding otherwise render small.
        anchor: For "cover" crops: "center" or "top" (keeps faces/tops).
        resample: PIL resampling filter; defaults to RESAMPLE_LANCZOS.
              Use RESAMPLE_NEAREST for pixel art, flags, and sprite icons.
        upscale: Allow scaling above source size (default True — the adaptive
              point). False mimics the legacy thumbnail() behavior.
    """
    if mode not in FIT_MODES:
        raise ValueError(f"Unknown fit mode '{mode}' (expected one of {FIT_MODES})")
    box_w, box_h = _box_dims(box)
    if box_w <= 0 or box_h <= 0 or img.width <= 0 or img.height <= 0:
        return _empty_result(mode, img.size)

    resample = RESAMPLE_LANCZOS if resample is None else resample

    work = img if img.mode == "RGBA" else img.convert("RGBA")
    if crop_to_ink:
        bbox = work.getbbox()
        if bbox is None:  # fully transparent
            return _empty_result(mode, img.size)
        work = work.crop(bbox)

    src_w, src_h = work.size

    if mode == "stretch":
        out = work.resize((box_w, box_h), resample)
        return ImageFitResult(out, box_w, box_h, box_w / src_w, mode, (src_w, src_h))

    if mode == "cover":
        scale = max(box_w / src_w, box_h / src_h)
        if not upscale:
            scale = min(scale, 1.0)
        scaled_w = max(1, round(src_w * scale))
        scaled_h = max(1, round(src_h * scale))
        out = work.resize((scaled_w, scaled_h), resample)
        # Crop the overhang down to the box (only when the scaled image is
        # larger; with upscale=False it may be smaller and is left as-is).
        crop_w, crop_h = min(box_w, scaled_w), min(box_h, scaled_h)
        left = (scaled_w - crop_w) // 2
        top = 0 if anchor == "top" else (scaled_h - crop_h) // 2
        out = out.crop((left, top, left + crop_w, top + crop_h))
        return ImageFitResult(out, out.width, out.height, scale, mode, (src_w, src_h))

    # contain / fill_height share the "preserve aspect, no crop" path
    if mode == "fill_height":
        scale = box_h / src_h
        # contain-cap: never exceed the box width (football's logo_slot rule)
        scale = min(scale, box_w / src_w)
    else:  # contain
        scale = min(box_w / src_w, box_h / src_h)
    if not upscale:
        scale = min(scale, 1.0)
    out_w = max(1, round(src_w * scale))
    out_h = max(1, round(src_h * scale))
    if (out_w, out_h) == (src_w, src_h):
        # No resize needed — but `work` may still BE the caller's original
        # image (RGBA source, no ink crop). The result must always be an
        # independent copy: LayoutContext caches ImageFitResults, and an
        # aliased image would let later mutations of the source corrupt
        # cached fits (or vice versa).
        out = work.copy() if work is img else work
    else:
        out = work.resize((out_w, out_h), resample)
    return ImageFitResult(out, out_w, out_h, scale, mode, (src_w, src_h))


def draw_fitted_image(display_manager: Any, ifit: ImageFitResult, box: Any, *,
                      align: str = "center", valign: str = "center",
                      offset: Tuple[int, int] = (0, 0)) -> Optional[Tuple[int, int]]:
    """Paste a fitted image aligned within a Region onto the display canvas.

    Pastes with the image's own alpha mask. Returns the (x, y) actually used
    so callers can position adjacent decorations, or None when nothing was
    drawn (empty fit / no canvas).
    """
    if ifit is None or ifit.is_empty:
        return None
    image = getattr(display_manager, "image", None)
    if image is None:
        return None
    if hasattr(box, "align_xy"):
        x, y = box.align_xy(ifit.width, ifit.height, align, valign)
    else:
        box_w, box_h = _box_dims(box)
        x = (box_w - ifit.width) // 2
        y = (box_h - ifit.height) // 2
    x += int(offset[0])
    y += int(offset[1])
    image.paste(ifit.image, (x, y), ifit.image)
    return (x, y)
