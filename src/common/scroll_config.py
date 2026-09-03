"""One place that turns plugin config into a configured ScrollHelper.

Five ticker plugins each hand-rolled this resolution (odds-ticker, news and
ledmatrix-leaderboard reference the deprecated ``scroll_pixels_per_second``
key 16-18 times apiece), and they disagreed in ways that were invisible until
someone watched the panel:

* odds-ticker read ``scroll_pixels_per_second`` on the *recommended* config
  path and let it override ``scroll_speed``/``scroll_delay``. Because that key
  carries a schema default, the documented settings were dead for every user
  -- see ChuckBuilds/ledmatrix-plugins#408.
* ledmatrix-leaderboard read the same key only as a fallback, so identical
  config produced different speeds in the two plugins.
* stock-news derived px/frame from it via its own arithmetic.

What matters on the hardware
----------------------------
Motion is smooth when the strip advances a **whole number of pixels per panel
refresh**. On a 100Hz panel that means 100 px/s, 200 px/s, and so on. Anything
else has to either blend adjacent columns (which on pixel-font text reads as
shimmer) or repeat frames (which reads as judder). :func:`resolve` warns when
the requested speed will not divide evenly, because that is a real display
artefact and not a rounding detail.

Speed is always expressed to the helper as pixels per second and applied in
time-based mode. Frame-based stepping gates motion on a wall clock at
``1/scroll_delay`` steps per second; plugins set ``scroll_delay`` to the frame
period, which puts that comparison exactly on its own threshold and makes the
step count flip on sub-millisecond jitter. Accumulating elapsed time keeps
position proportional to real time instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Speed used when a plugin supplies nothing usable. One pixel per refresh on a
#: 100Hz panel, which is the slowest crisp scroll that hardware can show.
DEFAULT_PIXELS_PER_SECOND = 100.0

#: Bounds accepted from config. Below the floor a marquee appears frozen;
#: above the ceiling it outruns any panel's refresh and tears.
MIN_PIXELS_PER_SECOND = 1.0
MAX_PIXELS_PER_SECOND = 500.0

#: Assumed refresh when the caller does not say. Matches the usual
#: ``display.hardware.limit_refresh_rate_hz``.
DEFAULT_REFRESH_HZ = 100.0

#: How far px/s may sit from a whole number of pixels per refresh before it is
#: worth warning about. 0.05px per frame is invisible; a third of a pixel is not.
_WHOLE_PIXEL_TOLERANCE = 0.05


@dataclass(frozen=True)
class ScrollSettings:
    """The resolved outcome, and which config key produced it."""

    pixels_per_second: float
    source: str
    target_fps: Optional[float] = None
    pixels_per_frame: Optional[float] = None
    warning: Optional[str] = None

    def describe(self) -> str:
        text = f"{self.pixels_per_second:.1f} px/s (from {self.source})"
        if self.pixels_per_frame is not None:
            text += f" = {self.pixels_per_frame:.2f} px/frame"
        if self.target_fps:
            text += f" at {self.target_fps:.0f} fps"
        return text


def _coerce(value: Any) -> Optional[float]:
    """A positive float, or None. Config reaches us with nulls and strings."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _from_speed_and_delay(block: Any) -> Optional[float]:
    """px/s from a ``scroll_speed`` (px/frame) + ``scroll_delay`` (s) pair."""
    if not isinstance(block, dict):
        return None
    speed = _coerce(block.get("scroll_speed"))
    delay = _coerce(block.get("scroll_delay"))
    if speed is None or delay is None:
        return None
    return speed / delay


def resolve(
    plugin_config: Optional[Dict[str, Any]] = None,
    global_config: Optional[Dict[str, Any]] = None,
    default_pixels_per_second: float = DEFAULT_PIXELS_PER_SECOND,
    refresh_hz: Optional[float] = None,
) -> ScrollSettings:
    """Resolve one scroll speed from the several shapes plugins accept.

    Precedence, highest first. The deprecated flat key sits *below* the
    explicit pairs deliberately: it carries schema defaults in some plugins, so
    ranking it above them silently disables the documented settings.

    1. ``display_options.scroll_speed`` + ``scroll_delay``  (current)
    2. ``display.scroll_speed`` + ``scroll_delay``          (deprecated shape)
    3. ``scroll_speed`` + ``scroll_delay`` at the root      (legacy flat)
    4. ``scroll_pixels_per_second``, nested or flat         (deprecated)
    5. the global ``display`` block
    6. ``default_pixels_per_second``

    :param refresh_hz: panel refresh, used only to check whether the resolved
        speed lands on whole pixels per frame and to fill in ``target_fps``.
    """
    plugin_config = plugin_config or {}
    global_config = global_config or {}
    refresh = _coerce(refresh_hz) or DEFAULT_REFRESH_HZ

    display_options = plugin_config.get("display_options")
    display_block = plugin_config.get("display")

    candidates = [
        (_from_speed_and_delay(display_options), "display_options.scroll_speed/delay"),
        (_from_speed_and_delay(display_block), "display.scroll_speed/delay"),
        (_from_speed_and_delay(plugin_config), "scroll_speed/delay (root)"),
    ]
    for block, label in (
        (display_options, "display_options.scroll_pixels_per_second"),
        (display_block, "display.scroll_pixels_per_second"),
        (plugin_config, "scroll_pixels_per_second"),
    ):
        if isinstance(block, dict):
            candidates.append((_coerce(block.get("scroll_pixels_per_second")), label))

    global_display = global_config.get("display")
    candidates.append((_from_speed_and_delay(global_display), "global display.scroll_speed/delay"))

    pixels_per_second = None
    source = "default"
    for value, label in candidates:
        if value is not None:
            pixels_per_second, source = value, label
            break
    if pixels_per_second is None:
        pixels_per_second = default_pixels_per_second

    clamped = max(MIN_PIXELS_PER_SECOND, min(MAX_PIXELS_PER_SECOND, pixels_per_second))
    warning = None
    if clamped != pixels_per_second:
        warning = (
            f"scroll speed {pixels_per_second:.1f} px/s out of range, "
            f"clamped to {clamped:.1f}"
        )
        pixels_per_second = clamped

    pixels_per_frame = pixels_per_second / refresh if refresh > 0 else None
    if warning is None and pixels_per_frame is not None:
        offset = abs(pixels_per_frame - round(pixels_per_frame))
        if pixels_per_frame < 1.0 - _WHOLE_PIXEL_TOLERANCE or offset > _WHOLE_PIXEL_TOLERANCE:
            suggestion = max(1.0, round(pixels_per_frame)) * refresh
            warning = (
                f"{pixels_per_second:.1f} px/s is {pixels_per_frame:.2f} px per "
                f"refresh at {refresh:.0f}Hz, so some frames repeat and the "
                f"scroll will judder; {suggestion:.0f} px/s divides evenly"
            )

    return ScrollSettings(
        pixels_per_second=pixels_per_second,
        source=source,
        target_fps=refresh,
        pixels_per_frame=pixels_per_frame,
        warning=warning,
    )


def configure(
    scroll_helper: Any,
    plugin_config: Optional[Dict[str, Any]] = None,
    global_config: Optional[Dict[str, Any]] = None,
    default_pixels_per_second: float = DEFAULT_PIXELS_PER_SECOND,
    refresh_hz: Optional[float] = None,
    plugin_logger: Optional[logging.Logger] = None,
) -> ScrollSettings:
    """Resolve the config and apply it to ``scroll_helper``.

    Applied in time-based mode: see the module docstring for why frame-based
    stepping is not used. ``hasattr`` guards keep this usable against older
    ScrollHelper builds that a plugin may be running on.

    :returns: the settings applied, so the caller can log or assert on them.
    """
    log = plugin_logger or logger
    settings = resolve(
        plugin_config,
        global_config,
        default_pixels_per_second=default_pixels_per_second,
        refresh_hz=refresh_hz,
    )

    if hasattr(scroll_helper, "set_frame_based_scrolling"):
        scroll_helper.set_frame_based_scrolling(False)
    scroll_helper.set_scroll_speed(settings.pixels_per_second)
    if settings.target_fps and hasattr(scroll_helper, "set_target_fps"):
        scroll_helper.set_target_fps(settings.target_fps)

    log.info("Scroll configured: %s", settings.describe())
    if settings.warning:
        log.warning("Scroll speed: %s", settings.warning)
    return settings


def refresh_hz_from_config(global_config: Optional[Dict[str, Any]]) -> float:
    """The panel's refresh cap from the global config, or the default."""
    if not isinstance(global_config, dict):
        return DEFAULT_REFRESH_HZ
    hardware = (global_config.get("display") or {}).get("hardware") or {}
    return _coerce(hardware.get("limit_refresh_rate_hz")) or DEFAULT_REFRESH_HZ
