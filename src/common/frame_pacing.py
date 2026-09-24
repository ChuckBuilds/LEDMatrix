"""Judge a run of presented frames against the panel's real refresh.

The rule the display obeys is in docs/SCROLL_PERFORMANCE.md: motion is smooth
when the strip advances a whole number of pixels per panel refresh, with each
frame held for a whole number of refreshes. That makes "is this scroll smooth?"
a question with an exact answer rather than a matter of taste --

    every presented frame should last ``frame_hold / refresh_hz`` seconds

-- and it makes a *missed* frame exactly one thing: an interval long enough to
round up to at least one more refresh than the hold asked for. That is a
dropped vsync, and it is what the eye reads as a hitch.

This module is only the arithmetic. It takes a list of intervals between
successive panel pushes (seconds, as ``time.perf_counter`` deltas) and reports
how many of them slipped. Nothing here touches hardware, so the thresholds a
soak is graded against are testable on any machine; ``scripts/render_bench.py``
is the driver that collects the intervals on a real panel.

Two failure modes are counted separately, because they mean opposite things:

* **missed** -- the interval is at least one refresh longer than it should be.
  Something (a slow ``SetImage``, a plugin fetch, the preview encoder, the GIL)
  held the render loop past the panel's deadline.
* **early** -- the interval is at least one refresh *shorter* than it should be.
  The swap returned without waiting, so the frame was never presented as a
  distinct image. A run with early frames is not measuring a vsync-locked loop
  at all, and its missed-frame percentage means nothing; the driver says so
  rather than reporting a flattering number.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

#: The ship gate from the rendering goal: under a tenth of a percent of frames
#: may miss a refresh over a soak.
DEFAULT_MAX_MISSED_PERCENT = 0.1

#: How far an interval may sit from its target before it counts as a different
#: number of refreshes. Half a refresh period is the rounding boundary, so this
#: is not a tunable fudge factor -- it is where "held for N refreshes" stops
#: being the nearest whole answer and "N+1" starts.
_ROUNDING = 0.5

#: How far the typical frame may fall short of its target period before the run
#: is judged not to have been paced by the panel at all. A vsync-locked loop
#: physically cannot present faster than ``refresh_hz / frame_hold``, so a
#: median below that means the swaps were not blocking -- the emulator, the
#: fallback display, or hardware that returned early. The margin only covers
#: error in the measured refresh rate itself.
_LOCK_TOLERANCE = 0.05


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile, as ``scroll_helper.frame_stats`` does for p95."""
    if not sorted_values:
        return 0.0
    rank = max(0, math.ceil(fraction * len(sorted_values)) - 1)
    return sorted_values[min(rank, len(sorted_values) - 1)]


@dataclass(frozen=True)
class PacingReport:
    """What a run of frame intervals says about the loop that produced it."""

    #: Intervals measured. One fewer than the frames pushed: the first push has
    #: no predecessor to time against.
    frames: int
    seconds: float
    refresh_hz: float
    frame_hold: int
    presented_fps: float
    expected_fps: float
    median: float
    p95: float
    p99: float
    maximum: float
    minimum: float
    missed: int
    early: int
    #: How many refreshes each frame actually lasted, rounded, as
    #: ``{refreshes: count}``. A vsync-locked loop puts nearly everything on
    #: ``frame_hold``; a spread across several buckets is judder even when the
    #: average fps looks right.
    histogram: Dict[int, int] = field(default_factory=dict)

    @property
    def expected_period(self) -> float:
        """Seconds a correctly paced frame lasts."""
        return self.frame_hold / self.refresh_hz if self.refresh_hz > 0 else 0.0

    @property
    def missed_percent(self) -> float:
        return 100.0 * self.missed / self.frames if self.frames else 0.0

    @property
    def early_percent(self) -> float:
        return 100.0 * self.early / self.frames if self.frames else 0.0

    @property
    def locked(self) -> bool:
        """True when the loop really was paced by the panel.

        Every frame landing on some whole number of refreshes is not enough --
        a loop that free-runs at half the refresh also does that. Two things
        have to hold: the hold asked for is the hold observed, and the typical
        frame is not *shorter* than the panel could possibly present. The
        second is what catches a swap that returned without blocking, which a
        whole-refresh bucket count cannot see -- 8ms frames on a 100Hz panel
        all land in the 1-refresh bucket while running 25% too fast.
        """
        if not self.frames:
            return False
        modal = max(self.histogram, key=lambda k: self.histogram[k])
        if modal != self.frame_hold or self.early:
            return False
        return self.median >= self.expected_period * (1.0 - _LOCK_TOLERANCE)

    def passed(self, max_missed_percent: float = DEFAULT_MAX_MISSED_PERCENT) -> bool:
        """Whether this run clears the gate. An unlocked run never does."""
        return self.locked and self.missed_percent <= max_missed_percent

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe form, for a soak that writes its result to a file."""
        return {
            "frames": self.frames,
            "seconds": self.seconds,
            "refresh_hz": self.refresh_hz,
            "frame_hold": self.frame_hold,
            "expected_period_ms": self.expected_period * 1000.0,
            "presented_fps": self.presented_fps,
            "expected_fps": self.expected_fps,
            "median_ms": self.median * 1000.0,
            "p95_ms": self.p95 * 1000.0,
            "p99_ms": self.p99 * 1000.0,
            "max_ms": self.maximum * 1000.0,
            "min_ms": self.minimum * 1000.0,
            "missed": self.missed,
            "missed_percent": self.missed_percent,
            "early": self.early,
            "early_percent": self.early_percent,
            "locked": self.locked,
            "histogram": {str(k): v for k, v in sorted(self.histogram.items())},
        }

    def describe(self, max_missed_percent: float = DEFAULT_MAX_MISSED_PERCENT) -> str:
        """The human report: several lines, no trailing newline."""
        if not self.frames:
            return "no frames measured"
        lines = [
            f"{self.presented_fps:6.2f} fps presented over {self.frames} frames "
            f"in {self.seconds:.1f}s "
            f"(expected {self.expected_fps:.2f} fps = "
            f"{self.frame_hold} refresh{'es' if self.frame_hold != 1 else ''} "
            f"of {self.refresh_hz:.1f}Hz)",
            f"  frame time  median {self.median * 1000:6.2f}ms  "
            f"p95 {self.p95 * 1000:6.2f}ms  p99 {self.p99 * 1000:6.2f}ms  "
            f"max {self.maximum * 1000:6.2f}ms  min {self.minimum * 1000:6.2f}ms "
            f"(target {self.expected_period * 1000:.2f}ms)",
            f"  missed      {self.missed} ({self.missed_percent:.3f}%)  "
            f"gate {max_missed_percent:.3f}%",
        ]
        if self.early:
            lines.append(
                f"  early       {self.early} ({self.early_percent:.3f}%) "
                "- swaps returned a whole refresh early"
            )
        buckets = " ".join(
            f"{refreshes}x:{count}" for refreshes, count in sorted(self.histogram.items())
        )
        lines.append(f"  refreshes   {buckets}")
        if not self.locked:
            lines.append(
                "  NOT LOCKED  - the loop was not paced by the panel, so the "
                "missed count above means nothing. Either the swap did not "
                "block (emulator or fallback display) or the frame hold in "
                "effect was not the one this run was graded against."
            )
        verdict = "PASS" if self.passed(max_missed_percent) else "FAIL"
        lines.append(f"  {verdict}")
        return "\n".join(lines)


def analyze(
    intervals: Sequence[float],
    refresh_hz: float,
    frame_hold: int = 1,
    seconds: Optional[float] = None,
) -> PacingReport:
    """Grade a list of frame intervals against a panel refresh.

    :param intervals: seconds between successive panel pushes.
    :param refresh_hz: the panel's *measured* refresh, not its configured cap.
        Grading against a cap the panel cannot reach reports misses that are
        really just the panel being slower than asked -- which is why
        ``render_bench`` measures first and passes the result in here.
    :param frame_hold: refreshes each frame was held for (``SwapOnVSync``'s
        ``framerate_fraction``), so the target period is ``hold / refresh_hz``.
    :param seconds: wall time the run covered. Defaults to the sum of the
        intervals, which is the same thing for a contiguous run.
    """
    samples: List[float] = [float(i) for i in intervals if i is not None and i > 0]
    hold = max(1, int(frame_hold))
    hz = float(refresh_hz)
    if not samples or hz <= 0:
        return PacingReport(
            frames=0, seconds=float(seconds or 0.0), refresh_hz=max(0.0, hz),
            frame_hold=hold, presented_fps=0.0, expected_fps=0.0,
            median=0.0, p95=0.0, p99=0.0, maximum=0.0, minimum=0.0,
            missed=0, early=0, histogram={},
        )

    refresh_period = 1.0 / hz
    ordered = sorted(samples)
    total = float(seconds) if seconds is not None else sum(samples)
    mean = sum(samples) / len(samples)

    histogram: Dict[int, int] = {}
    missed = 0
    early = 0
    for interval in samples:
        # How many refreshes this frame actually occupied. Rounding at the
        # halfway point is what makes a "miss" a whole dropped vsync rather
        # than any interval that ran a little long -- a frame 1ms late on a
        # 10ms refresh still presented on the refresh it was meant to.
        refreshes = max(1, int(math.floor(interval / refresh_period + _ROUNDING)))
        histogram[refreshes] = histogram.get(refreshes, 0) + 1
        if refreshes > hold:
            missed += 1
        elif refreshes < hold:
            early += 1

    return PacingReport(
        frames=len(samples),
        seconds=total,
        refresh_hz=hz,
        frame_hold=hold,
        presented_fps=(1.0 / mean) if mean > 0 else 0.0,
        expected_fps=hz / hold,
        median=_percentile(ordered, 0.5),
        p95=_percentile(ordered, 0.95),
        p99=_percentile(ordered, 0.99),
        maximum=ordered[-1],
        minimum=ordered[0],
        missed=missed,
        early=early,
        histogram=histogram,
    )


def measure_refresh_hz(matrix: Any, seconds: float = 4.0) -> float:
    """The panel's real refresh rate, by timing unthrottled swaps.

    ``SwapOnVSync`` blocks until the panel's next refresh, so a loop that does
    nothing else runs at exactly the panel's rate. This is the number every
    pacing decision has to be made against: ``limit_refresh_rate_hz`` is a
    *cap*, and a long chain, a high ``pwm_bits`` or an older Pi will sit well
    under it. Solving scroll speeds against a cap the panel cannot reach is
    what produces "3px every 4 refreshes" and the judder that comes with it.

    Pass the matrix the display is already running on rather than opening a
    second one -- the GPIO has a single owner, and the options in force change
    the answer.

    :returns: measured Hz, or 0.0 if the matrix cannot be swapped (no
        hardware, a stub, a mock).
    """
    try:
        canvas = matrix.CreateFrameCanvas()
        # Discard the first swap: it carries construction and first-touch costs
        # that have nothing to do with the steady-state refresh.
        canvas = matrix.SwapOnVSync(canvas)
    except Exception:
        return 0.0

    frames = 0
    started = time.perf_counter()
    while time.perf_counter() - started < seconds:
        canvas = matrix.SwapOnVSync(canvas)
        frames += 1
    elapsed = time.perf_counter() - started
    if elapsed <= 0 or frames <= 0:
        return 0.0
    return frames / elapsed


#: Samples needed before an interval list can be asked what the refresh was.
_MIN_SAMPLES_FOR_ESTIMATE = 30

#: Where in the sorted intervals the refresh period is read from. Not the
#: minimum: one anomalously short sample (a skipped swap, a clock wobble) would
#: set the period for the whole run and turn every honest frame into a miss.
_REFRESH_QUANTILE = 0.1


def refresh_from_intervals(
    intervals: Sequence[float],
    frame_hold: int = 1,
) -> float:
    """The refresh the panel actually ran at *while rendering*, from the frames.

    A panel does not refresh at one fixed rate regardless of what the Pi is
    doing. Driving an LED matrix is bit-banging on the same machine, so the
    work of pushing a frame -- ``SetImage`` over a 512x64 chain at 8 PWM bits
    is milliseconds -- contends with the refresh itself and slows it. Measured
    on a Pi 4 with a 512x64 chain: 100.4Hz idle, 96.3Hz while scrolling.

    That makes the idle measurement the wrong thing to grade a soak against.
    Graded against 100.4Hz, a loop perfectly locked to the panel's real 96.3Hz
    reports 96.3 fps against an expected 100.4 and looks broken; once the drop
    passes half a refresh period every frame is counted as a miss outright.
    The give-away that nothing is actually being missed is that the intervals
    cluster tightly around 10.46ms rather than splitting between 9.96ms and
    19.92ms, which is what missing every twenty-fifth vsync would look like.

    So the period is read back from the frames themselves. Swaps that block on
    vsync can only return on a refresh boundary, so the low end of
    ``interval / frame_hold`` is the period -- the frames that waited out one
    whole refresh and no more.

    :returns: Hz, or 0.0 when there are too few samples to say.
    """
    samples = sorted(float(i) for i in intervals if i is not None and i > 0)
    if len(samples) < _MIN_SAMPLES_FOR_ESTIMATE:
        return 0.0
    period = _percentile(samples, _REFRESH_QUANTILE) / max(1, int(frame_hold))
    return (1.0 / period) if period > 0 else 0.0
