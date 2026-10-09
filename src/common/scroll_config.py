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

How the speed is applied
------------------------
:func:`configure` snaps the requested speed to a :class:`CrispSpeed` -- a whole
number of pixels per presented frame, with each frame held for ``frame_hold``
panel refreshes -- and puts the helper in fixed-step mode
(``ScrollHelper.set_pixels_per_frame``). In that mode the helper consults no
clock: every ``update_scroll_position()`` call moves exactly that many pixels.
``SwapOnVSync`` blocks until the panel has taken each frame, so the frame count
is the clock, and the speed on the panel is::

    px/s = refresh_hz / frame_hold * pixels_per_frame

That makes the hold part of the speed. The caller must pass
``settings.frame_hold`` to ``display_manager.set_scrolling_state(True, ...)``;
a caller that omits it is presented every refresh and scrolls ``frame_hold``
times too fast. The refresh is the panel's (``display_manager.refresh_hz``,
i.e. ``display.hardware.limit_refresh_rate_hz``) -- never the global
``target_fps``, which no longer paces anything.

With ``snap_to_crisp=False`` there is no fixed step: the helper is set to
px/s and advances by elapsed time, and the hold is 1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, cast

from src.matrix_support import DEFAULT_REFRESH_LIMIT_HZ

logger = logging.getLogger(__name__)

#: Speed used when a plugin supplies nothing usable. One pixel per refresh on a
#: 100Hz panel, which is the slowest crisp scroll that hardware can show.
DEFAULT_PIXELS_PER_SECOND = 100.0

#: Bounds accepted from config. Below the floor a marquee appears frozen;
#: above the ceiling it outruns any panel's refresh and tears.
MIN_PIXELS_PER_SECOND = 1.0
MAX_PIXELS_PER_SECOND = 500.0

#: Assumed refresh when the caller does not say. Matches the usual
#: ``display.hardware.limit_refresh_rate_hz``, and is the cap DisplayManager
#: applies when that key is missing.
DEFAULT_REFRESH_HZ = float(DEFAULT_REFRESH_LIMIT_HZ)

#: How far px/s may sit from a whole number of pixels per refresh before it is
#: worth warning about. 0.05px per frame is invisible; a third of a pixel is not.
_WHOLE_PIXEL_TOLERANCE = 0.05


#: Longest a frame may be held before motion reads as a slideshow rather than
#: a scroll. 6 refreshes at 100Hz is ~17px/s, already visibly stepped.
MAX_FRAME_HOLD = 8

#: Largest whole-pixel jump per presented frame before motion looks like it is
#: teleporting rather than sliding.
MAX_PIXELS_PER_FRAME = 6


@dataclass(frozen=True)
class CrispSpeed:
    """A speed the panel can show with whole-pixel motion.

    ``pixels_per_second`` is always ``refresh_hz / frame_hold * pixels_per_frame``
    exactly -- no rounding, no fractional pixel positions, so nothing has to be
    blended or repeated unevenly.

    :param frame_hold: refreshes each frame is held for. This is rgbmatrix's
        ``SwapOnVSync(canvas, framerate_fraction)``. The panel keeps refreshing
        at full rate either way, so holding a frame costs nothing in flicker.
    :param pixels_per_frame: whole pixels advanced per presented frame.
    """

    pixels_per_second: float
    frame_hold: int
    pixels_per_frame: int
    refresh_hz: float

    @property
    def frames_per_second(self) -> float:
        """Distinct frames per second: the refresh divided by the hold."""
        return self.refresh_hz / self.frame_hold

    @property
    def steppiness(self) -> str:
        """Rough readability hint for this combination."""
        if self.pixels_per_frame > 2:
            return "jumpy"
        if self.frames_per_second < 20:
            return "stepped"
        if self.frames_per_second < 30:
            return "slightly stepped"
        return "smooth"

    def describe(self) -> str:
        """This speed as a line for the speed ladder, aligned for a column."""
        return (
            f"{self.pixels_per_second:6.1f} px/s  "
            f"({self.pixels_per_frame}px every {self.frame_hold} refresh"
            f"{'es' if self.frame_hold != 1 else ' '} = "
            f"{self.frames_per_second:5.1f} fps, {self.steppiness})"
        )


def crisp_ladder(
    refresh_hz: float = DEFAULT_REFRESH_HZ,
    max_frame_hold: int = MAX_FRAME_HOLD,
    max_pixels_per_frame: int = MAX_PIXELS_PER_FRAME,
) -> List[CrispSpeed]:
    """Every whole-pixel speed this panel can show, slowest first.

    Duplicates are collapsed keeping the gentlest option: 100 px/s is reachable
    as 1px every refresh or 2px every 2nd refresh, and the former moves in
    smaller increments, so that is the one worth offering.
    """
    best: Dict[float, CrispSpeed] = {}
    for hold in range(1, max_frame_hold + 1):
        for ppf in range(1, max_pixels_per_frame + 1):
            pps = refresh_hz / hold * ppf
            key = round(pps, 3)
            candidate = CrispSpeed(pps, hold, ppf, refresh_hz)
            incumbent = best.get(key)
            if incumbent is None or ppf < incumbent.pixels_per_frame:
                best[key] = candidate
    return [best[k] for k in sorted(best)]


#: How much a bigger pixel step costs, as a fraction of the target speed.
#: Tuned so 66.7px/s (2px at 33fps) beats 50px/s (1px at 50fps) when 60 was
#: asked for, but 33.3px/s (1px, smooth) still beats 28.6px/s (2px at 14fps)
#: when 30 was asked for -- being 11% slow is worth far less than looking bad.
_STEP_PENALTY = 0.05
_SLOW_FPS_PENALTY = 0.25   # below 20fps
_LOWISH_FPS_PENALTY = 0.16  # below 30fps, i.e. "slightly stepped"
# Up to 30fps, matching CrispSpeed.steppiness: a measured 125.7Hz panel makes
# 50.3px/s (2px every 5 refreshes) 25.1fps, which a 25fps cutoff let through.
# 0.16, not less: asked for 50px/s on a 120Hz panel, 48px/s (2px every 5
# refreshes, 24fps) costs 0.04 + 0.05 + this, and has to lose to both 60px/s
# and 40px/s (1px, smooth, 20% off = 0.20). At 0.10 it won and shipped a
# visibly stepped scroll to anyone asking for the default.


def _quality_cost(candidate: "CrispSpeed", target: float) -> float:
    """Lower is better. Numeric closeness alone picks bad-looking speeds.

    Nearest-by-value would answer "30 px/s" with 28.6 px/s -- which is 2px
    jumps at 14fps -- over 33.3 px/s, which is single-pixel motion at 33fps and
    obviously better on the panel. Proximity has to be traded against how the
    motion actually reads.
    """
    error = abs(candidate.pixels_per_second - target) / max(target, 1e-6)
    cost = error + _STEP_PENALTY * (candidate.pixels_per_frame - 1)
    fps = candidate.frames_per_second
    if fps < 20:
        cost += _SLOW_FPS_PENALTY
    elif fps < 30:
        cost += _LOWISH_FPS_PENALTY
    return cost


def solve_crisp(
    target_pixels_per_second: float,
    refresh_hz: float = DEFAULT_REFRESH_HZ,
    max_frame_hold: int = MAX_FRAME_HOLD,
    max_pixels_per_frame: int = MAX_PIXELS_PER_FRAME,
) -> CrispSpeed:
    """The whole-pixel speed that will look best for what was asked for.

    Not simply the nearest -- see :func:`_quality_cost`. Ties break toward the
    smaller pixel step and the shorter hold.
    """
    ladder = crisp_ladder(refresh_hz, max_frame_hold, max_pixels_per_frame)
    # Clamp into the ladder's range first. Relative error saturates near 1.0
    # for a target far outside it, so the quality penalty would dominate and
    # answer "10000 px/s" with the *slowest* entry -- smooth, and useless.
    target = min(max(target_pixels_per_second, ladder[0].pixels_per_second),
                 ladder[-1].pixels_per_second)
    return min(
        ladder,
        key=lambda c: (round(_quality_cost(c, target), 6),
                       c.pixels_per_frame, c.frame_hold),
    )


@dataclass(frozen=True)
class ScrollSettings:
    """The resolved outcome, and which config key produced it."""

    pixels_per_second: float
    source: str
    target_fps: Optional[float] = None
    pixels_per_frame: Optional[float] = None
    warning: Optional[str] = None
    #: The whole-pixel speed actually applied, when snapping was enabled.
    crisp: Optional[CrispSpeed] = None
    #: What the config asked for, before snapping.
    requested_pixels_per_second: Optional[float] = None

    @property
    def frame_hold(self) -> int:
        """Refreshes to hold each frame for; pass to set_scrolling_state()."""
        return self.crisp.frame_hold if self.crisp else 1

    def describe(self) -> str:
        """One log line: the speed applied, and which config key produced it."""
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
    display_manager: Any = None,
    snap_to_crisp: bool = True,
) -> ScrollSettings:
    """Resolve the config and apply it to ``scroll_helper``.

    Frame-based mode is switched off, the (snapped) speed is set, and with
    ``snap_to_crisp`` the helper steps a fixed whole-pixel amount per presented
    frame -- see the module docstring. ``hasattr`` guards keep this usable
    against older ScrollHelper builds that a plugin may be running on.

    :param display_manager: consulted for the panel's refresh rate only (it can
        see display.hardware; a plugin cannot). The frame hold is NOT applied
        here -- see the note in the body. The caller must pass
        ``settings.frame_hold`` to ``display_manager.set_scrolling_state(True,
        ...)`` when it scrolls. The helper moves its fixed step on every call,
        so without the hold each step is presented every refresh and the
        scroll runs ``frame_hold`` times faster than the resolved speed.
    :param snap_to_crisp: move the requested speed to the nearest speed the
        panel can show in whole pixels. On by default because a speed that does
        not divide evenly has no good rendering, only a choice of artefacts.

    :returns: the settings applied, so the caller can log or assert on them.
    """
    log = plugin_logger or logger

    # Refresh rate, most authoritative first: what the caller passed, then the
    # display manager (which can see display.hardware; a plugin cannot), then
    # the global config, then the default.
    #
    # This has to be settled BEFORE resolve(), not after. resolve() uses the
    # refresh to fill in target_fps, pixels_per_frame and the judder warning,
    # so deriving it afterwards described a 100Hz panel to everyone running at
    # 60 -- and with snap_to_crisp=False nothing downstream corrected it, so
    # the settings and the helper's (informational) target_fps said 100 FPS on
    # a 60Hz panel.
    hz = _coerce(refresh_hz)
    if hz is None and display_manager is not None:
        hz = _coerce(getattr(display_manager, "refresh_hz", None))
    if hz is None:
        hz = refresh_hz_from_config(global_config)

    settings = resolve(
        plugin_config,
        global_config,
        default_pixels_per_second=default_pixels_per_second,
        refresh_hz=hz,
    )
    applied = settings.pixels_per_second
    choice = None

    if snap_to_crisp:
        choice = solve_crisp(settings.pixels_per_second, hz)
        applied = choice.pixels_per_second
        settings = replace(
            settings,
            pixels_per_second=applied,
            requested_pixels_per_second=settings.pixels_per_second,
            crisp=choice,
            pixels_per_frame=float(choice.pixels_per_frame),
            # Snapping resolves the whole-pixel problem the warning describes.
            warning=None if settings.warning and "judder" in settings.warning
            else settings.warning,
        )

    if hasattr(scroll_helper, "set_frame_based_scrolling"):
        scroll_helper.set_frame_based_scrolling(False)
    scroll_helper.set_scroll_speed(applied)

    # A crisp speed is a whole number of pixels per presented frame, so step by
    # that number rather than by speed * elapsed time. Snapping alone only
    # fixes the average: the wall clock puts the accumulator back on an integer
    # boundary every frame, where jitter of a fraction of a millisecond decides
    # whether the pixel moves. That is what the ladder was bought to prevent.
    if hasattr(scroll_helper, "set_pixels_per_frame"):
        scroll_helper.set_pixels_per_frame(
            choice.pixels_per_frame if choice else None)
    # Informational only: nothing in the helper paces off target_fps. It is
    # still recorded because plugins read it back (ledmatrix-elections'
    # test_scroll_pacing.py asserts it equals the crisp presentation rate).
    if choice and hasattr(scroll_helper, "set_target_fps"):
        scroll_helper.set_target_fps(choice.frames_per_second)
    elif settings.target_fps and hasattr(scroll_helper, "set_target_fps"):
        scroll_helper.set_target_fps(settings.target_fps)

    # Deliberately NOT applied here. The hold belongs to a scroll, not to a
    # plugin's lifetime: plugins share one display manager, and one left set at
    # construction is reset the moment any other plugin finishes scrolling.
    # Callers pass settings.frame_hold to set_scrolling_state(True, ...) when
    # they start scrolling. configure() only reports what is needed.

    if choice:
        # Set whenever there is a crisp choice (see the replace() above).
        requested = cast(float, settings.requested_pixels_per_second)
        if abs(requested - applied) > 0.05:
            log.info(
                "Scroll configured: %s (asked for %.1f px/s from %s; "
                "nearest whole-pixel speed on a %.0fHz panel)",
                choice.describe(), requested, settings.source, hz,
            )
        else:
            log.info("Scroll configured: %s (from %s)",
                     choice.describe(), settings.source)
        if choice.frame_hold > 1:
            log.debug(
                "Scroll needs a frame hold of %d - pass settings.frame_hold to "
                "display_manager.set_scrolling_state(True, ...) each scroll",
                choice.frame_hold,
            )
    else:
        log.info("Scroll configured: %s", settings.describe())

    if settings.warning:
        log.warning("Scroll speed: %s", settings.warning)
    return settings


def refresh_hz_from_config(global_config: Optional[Dict[str, Any]]) -> float:
    """The panel's refresh cap from the global config, or the default."""
    if not isinstance(global_config, dict):
        return DEFAULT_REFRESH_HZ
    # Each level is checked for being a mapping rather than merely truthy: a
    # malformed config where display or display.hardware is a string or a list
    # raised AttributeError out of what is meant to be a total function with a
    # default, taking down every caller that asked for the refresh rate.
    display = global_config.get("display")
    if not isinstance(display, dict):
        return DEFAULT_REFRESH_HZ
    hardware = display.get("hardware")
    if not isinstance(hardware, dict):
        return DEFAULT_REFRESH_HZ
    return _coerce(hardware.get("limit_refresh_rate_hz")) or DEFAULT_REFRESH_HZ


#: Smooth options offered next to a speed that is not one itself.
_ADVICE_ALTERNATIVES = 2


def speed_advice(
    requested_pixels_per_second: float,
    refresh_hz: float,
    min_pixels_per_second: float = MIN_PIXELS_PER_SECOND,
    max_pixels_per_second: float = MAX_PIXELS_PER_SECOND,
) -> Dict[str, Any]:
    """What the panel will do with a requested speed, for showing in a UI.

    ``applied`` is what :func:`solve_crisp` picks, i.e. what really runs.
    ``smooth`` is true when that is single-pixel-ish, 30fps-or-better motion.
    ``alternatives`` are the smooth ladder entries nearest the request inside
    the given range, for a click-to-apply suggestion; empty when the request
    already is one.
    """
    hz = _coerce(refresh_hz) or DEFAULT_REFRESH_HZ
    requested = max(MIN_PIXELS_PER_SECOND,
                    min(MAX_PIXELS_PER_SECOND, _coerce(requested_pixels_per_second) or 0.0))
    applied = solve_crisp(requested, hz)

    def as_dict(c: CrispSpeed) -> Dict[str, Any]:
        return {
            "pixels_per_second": round(c.pixels_per_second, 1),
            "pixels_per_frame": c.pixels_per_frame,
            "frame_hold": c.frame_hold,
            "frames_per_second": round(c.frames_per_second, 1),
            "steppiness": c.steppiness,
        }

    smooth_ladder = [
        c for c in crisp_ladder(hz)
        if c.steppiness == "smooth"
        and min_pixels_per_second <= c.pixels_per_second <= max_pixels_per_second
    ]
    # 2%: a UI hands over whole numbers, and 63 asked of a 62.9 px/s panel is
    # as good as exact.
    exact = abs(applied.pixels_per_second - requested) <= max(0.05, 0.02 * requested)
    smooth = applied.steppiness == "smooth"
    alternatives: List[CrispSpeed] = []
    if not (exact and smooth):
        alternatives = sorted(
            smooth_ladder,
            key=lambda c: abs(c.pixels_per_second - requested),
        )[:_ADVICE_ALTERNATIVES]
        alternatives.sort(key=lambda c: c.pixels_per_second)
    return {
        "requested": round(requested, 1),
        "refresh_hz": round(hz, 1),
        "applied": as_dict(applied),
        "exact": exact,
        "smooth": smooth,
        "alternatives": [as_dict(c) for c in alternatives],
    }


#: A measured refresh this far below the rate speeds are planned for means
#: the panel cannot reach its cap. Smaller gaps are the cap's own slack and
#: the estimate's: one rig measured 99.95 Hz under a 100 Hz cap.
REFRESH_SHORTFALL = 0.03

#: How far under the measured rate a suggested cap sits. The measurement is
#: the fast end of the panel's refreshes (frame_timing takes the 10th
#: percentile of intervals), and an uncapped panel drifts: one read
#: 107.6-113.1 Hz over 15 seconds. A cap inside that band would not hold.
CAP_HEADROOM = 0.05


def holdable_cap(measured_hz: Any) -> Optional[int]:
    """A refresh cap the panel can hold: a multiple of 10, 5% under what it measured.

    A multiple of 10 because its whole-pixel speeds are round numbers (a
    100 Hz cap gives 50 and 100 px/s). None without a usable measurement, or
    when the panel is too slow for any cap of 10 Hz or more.
    """
    hz = _coerce(measured_hz)
    if hz is None:
        return None
    cap = int(hz * (1.0 - CAP_HEADROOM) // 10) * 10
    return cap if cap >= 10 else None


def refresh_shortfall(measured_hz: Any, planned_hz: Any) -> Optional[Dict[str, Any]]:
    """When the panel refreshes measurably slower than speeds are planned for.

    ``planned_hz`` is what :func:`configure` solves against -- the
    ``limit_refresh_rate_hz`` cap, or :data:`DEFAULT_REFRESH_HZ` when it is 0.
    A panel that cannot reach it still moves whole pixels per frame, but every
    speed runs slow by the shortfall and the ladder of smooth speeds is the
    cap's, not the panel's. None when there is no measurement, or the panel
    reaches the cap (or beats it, as some do by a few Hz).
    """
    measured, planned = _coerce(measured_hz), _coerce(planned_hz)
    if measured is None or planned is None:
        return None
    if measured >= planned * (1.0 - REFRESH_SHORTFALL):
        return None
    return {
        "measured_hz": round(measured, 1),
        "planned_hz": round(planned, 1),
        "suggested_cap_hz": holdable_cap(measured),
        "slow_percent": round((1.0 - measured / planned) * 100),
    }


def describe_refresh_shortfall(shortfall: Dict[str, Any]) -> str:
    """One log line for :func:`refresh_shortfall`'s answer."""
    text = (
        f"The panel refreshes at about {shortfall['measured_hz']:.0f} Hz, below "
        f"the {shortfall['planned_hz']:.0f} Hz that scroll speeds are planned "
        f"for (display.hardware.limit_refresh_rate_hz), so every scroll runs "
        f"about {shortfall['slow_percent']}% slower than configured and the "
        f"smooth speeds are worked out for a rate this panel never reaches.")
    if shortfall.get("suggested_cap_hz"):
        text += (f" Set Limit Refresh Rate to {shortfall['suggested_cap_hz']} Hz "
                 f"(web UI, Display tab), which this panel can hold, and restart.")
    return text
