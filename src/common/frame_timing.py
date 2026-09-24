"""System-wide frame timing: one set of numbers for every presented frame.

Each scroller already logs its own stats line (ScrollHelper.log_frame_rate,
the Vegas coordinator's "Vegas FPS"), but in different formats, per source,
and Vegas only logs a healthy window at DEBUG. None of that answers the
question a release has to answer on each rig: *over a long run, how often did
a moving frame reach the panel late?*

Every frame reaches the panel through ``DisplayManager.update_display``, so it
is recorded there, once, whoever drew it. The render thread only appends a
tuple; a worker thread aggregates, and every ``flush_interval`` seconds writes
cumulative counters and histograms to a small JSON file -- in ``/dev/shm`` where
it exists, so a stats file refreshed all day costs no SD-card writes.
``scripts/frame_soak.py`` reads it twice and reports the difference.

What is counted
---------------
Only intervals between two consecutive *scrolling* frames count: a static
screen that changes once a second has no timing to get wrong, and the first
frame of a scroll has no predecessor worth measuring against.

A frame held for ``hold`` refreshes should arrive ``hold`` refresh periods
after the one before it. One that arrives a whole refresh or more after that is
**late**: the panel showed the previous frame again, which on a moving strip is
a visible hitch. ``missed_refreshes`` sums how many refreshes late.

An interval of ``FREEZE_SECONDS`` or more is a **freeze** instead -- a
recompose, a plugin handover, a blocking call on the render thread. Those are
counted separately, both because they are a different fault and because
folding a single 400ms handover into the late count as "40 missed refreshes"
would drown the jitter the late count exists to measure. ``freeze_by`` splits
them by length. Intervals of ``GAP_SECONDS`` or more are ignored as not being
frames of one scroll at all.

A frame that arrives a whole refresh or more *early* means the swap did not
wait for the panel: the emulator, the fallback display, or a hold that was not
the one in effect. Those are counted as **early**, and a run with more than a
trace of them was not locked to the panel, so its late count means nothing.

The refresh period is estimated from the frames themselves: swaps that block
on vsync can only land on refresh boundaries, so the low end of
interval / hold is the period. It is the smallest per-window 10th percentile
seen so far, over windows with enough frames to trust -- except that a window
cutting it by more than ``MAX_REFRESH_DROP`` is ignored. A panel's refresh does
not jump like that; swaps that stopped blocking do, and adopting their period
would make every early frame look on time.

A caller that has measured the panel independently -- ``scripts/render_bench.py``
times bare swaps first with :func:`measure_refresh_hz` -- passes that rate in
as ``refresh_hz``. The estimate then starts from it instead of from the frames,
which is what catches a loop that never locked at all: one that free-runs
faster than the panel (every frame early) or sits at half its rate (every
frame late), both of which look self-consistent to an estimate taken from
their own intervals.

Stall watchdog
--------------
Counting a freeze says that it happened, not why. ``StallWatchdog`` watches the
same frames from its own thread and, when a scroll's last frame is more than
``STALL_SECONDS`` old, logs the stack of the thread that presented it and the
top of every other thread's, so the log names what the render thread was
waiting on. It also measures how late its own wake-up was: if the watchdog was
held up as long as the render thread, the whole interpreter was blocked (C
code holding the GIL, or the process not scheduled), not one thread on a lock.
Set ``LEDMATRIX_STALL_WATCHDOG=0`` to turn it off, or
``LEDMATRIX_STALL_WATCHDOG_MS`` to dump at a lower threshold -- 30 catches
frames three refreshes late, which is where GIL contention shows. It polls
three times per threshold, so keep it to diagnostic runs, not soaks.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import tempfile
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Bumped when a field changes meaning, so a reader can refuse stale files.
SCHEMA_VERSION = 1

#: Histogram resolution. 64ms of range covers any frame worth drawing a
#: distribution of; everything beyond lands in the last bucket.
BUCKET_MS = 0.25
BUCKET_COUNT = 256

#: See the module docstring.
FREEZE_SECONDS = 0.25

#: Two frames that are both "scrolling" can be at most DisplayManager's
#: scroll_inactivity_threshold (2s) apart: after that the second is recorded
#: as static. This used to be 1s, which silently dropped every 1-2s stall
#: inside a scroll. It is now only a sanity bound.
GAP_SECONDS = 5.0

#: Buckets for freeze length, as cumulative counters a soak can difference.
FREEZE_BUCKETS = ((0.5, "<0.5s"), (1.0, "0.5-1s"), (2.0, "1-2s"),
                  (float("inf"), "2s+"))

#: A window may lower the refresh-period estimate by at most this fraction.
MAX_REFRESH_DROP = 0.2

#: A window needs this many scrolling frames before its refresh estimate is
#: trusted -- about a second of scrolling.
MIN_FRAMES_FOR_REFRESH = 90

FLUSH_INTERVAL = 10.0

#: A scroll's last frame older than this is a stall worth a stack dump.
STALL_SECONDS = 0.25
#: How often the watchdog looks. Also the resolution of its starvation check.
WATCHDOG_POLL_SECONDS = 0.05
#: At most one stack dump per this many seconds: a stall that repeats every
#: extension would otherwise write the same stacks to the SD card all day.
STALL_LOG_INTERVAL = 30.0

#: Written by the display service, read by scripts/frame_soak.py and anything
#: else that wants the numbers. The web UI's viewer marker lives in /tmp; this
#: goes to RAM where there is some, since it is rewritten all day.
STATS_FILENAME = "ledmatrix_frame_stats.json"


def default_stats_path() -> str:
    base = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
    return os.path.join(base, STATS_FILENAME)


def _bucket(seconds: float) -> int:
    index = int(seconds * 1000.0 / BUCKET_MS)
    return min(max(index, 0), BUCKET_COUNT - 1)


def binding_releases_gil() -> Optional[bool]:
    """Whether the loaded rgbmatrix binding releases the GIL, or None.

    The stock binding blocks in SwapOnVSync holding the GIL, which starves
    every other thread for most of each frame (docs/SCROLL_PERFORMANCE.md).
    scripts/build_rgbmatrix_nogil.sh rebuilds it, and the rebuilt module links
    PyEval_SaveThread where the stock one never does -- a crude test, but the
    only one that needs neither a probe on the panel nor the source tree the
    module was built from. None when no hardware binding is loaded.
    """
    module = sys.modules.get("rgbmatrix.core")
    path = getattr(module, "__file__", None)
    if not path:
        return None
    try:
        with open(path, "rb") as handle:
            return b"PyEval_SaveThread" in handle.read()
    except OSError:
        return None


def _pi_model() -> Optional[str]:
    try:
        with open("/proc/device-tree/model", "rb") as handle:
            return handle.read().rstrip(b"\0").decode("ascii", "replace").strip()
    except OSError:
        return None


def measure_refresh_hz(matrix: Any, seconds: float = 4.0) -> float:
    """The panel's refresh rate with nothing else running, by timing bare swaps.

    ``SwapOnVSync`` blocks until the panel's next refresh, so a loop that does
    nothing else runs at exactly the panel's rate. ``limit_refresh_rate_hz`` is
    a *cap*, and a long chain, a high ``pwm_bits`` or an older Pi will sit well
    under it. Solving scroll speeds against a cap the panel cannot reach is
    what produces "3px every 4 refreshes" and the judder that comes with it.

    This is the idle rate. The panel refreshes a few percent slower while the
    Pi is also pushing frames into it (100.4Hz idle against 96.3Hz scrolling on
    a Pi 4 driving 512x64), which is why the recorder reads the rendering rate
    back from the frames rather than trusting this.

    Pass the matrix the display is already running on rather than opening a
    second one: the GPIO has a single owner, and the options in force change
    the answer.

    :returns: measured Hz, or 0.0 if the matrix cannot be swapped (no
        hardware, a stub, a mock).
    """
    try:
        canvas = matrix.CreateFrameCanvas()
        # Discard the first swap: it carries construction and first-touch costs
        # that have nothing to do with the steady-state refresh.
        canvas = matrix.SwapOnVSync(canvas)
    except Exception:  # pylint: disable=broad-except
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

class FrameTimingRecorder:
    """Collects per-frame timings on the render thread; aggregates elsewhere.

    ``record`` is the only method the render thread calls, and it does no more
    than compare two floats and append a tuple.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        flush_interval: float = FLUSH_INTERVAL,
        info: Optional[Dict[str, Any]] = None,
        refresh_hz: Optional[float] = None,
    ):
        """
        :param refresh_hz: the panel's rate, measured independently (see the
            module docstring). Omit it to estimate from the frames alone, as
            the display service does.
        """
        self.path = path or default_stats_path()
        self.flush_interval = flush_interval
        self.info = dict(info or {})

        # Render-thread state.
        self._pending: List[Tuple[float, float, float, int]] = []
        self._static_frames = 0
        self._previous: Optional[Tuple[float, bool, int]] = None
        self._last_flush: Optional[float] = None
        self._queue: "queue.SimpleQueue" = queue.SimpleQueue()
        self._worker: Optional[threading.Thread] = None

        # Worker-thread state. Nothing on the render thread reads these.
        self.started = time.time()
        self.refresh_period: Optional[float] = (
            1.0 / refresh_hz if refresh_hz and refresh_hz > 0 else None)
        self.totals: Dict[str, Any] = {
            "static_frames": 0,
            "scroll_frames": 0,
            "late_frames": 0,
            "missed_refreshes": 0,
            "late_by": {"1": 0, "2": 0, "3-5": 0, "6+": 0},
            "early_frames": 0,
            "freezes": 0,
            "freeze_seconds": 0.0,
            "freeze_by": {label: 0 for _, label in FREEZE_BUCKETS},
            "worst_interval_ms": 0.0,
        }
        self.histograms: Dict[str, Dict[int, int]] = {
            "blit": {}, "wait": {}, "work": {}, "interval_per_hold": {},
        }
        self._binding_gil: Optional[bool] = None
        self._binding_checked = False

        # Read by the stall watchdog from its own thread: one tuple assignment,
        # so it always sees a consistent (time, scrolling, thread) triple.
        self.last_frame: Optional[Tuple[float, bool, int]] = None
        #: Whether a scroll is running *now*, supplied by the display manager.
        #: The last frame's flag alone would call the end of every scroll a
        #: stall.
        self.scrolling_now: Optional[Callable[[], bool]] = None
        self.watchdog: Optional["StallWatchdog"] = None

    # -- render thread ------------------------------------------------------

    def record(self, blit: float, wait: float, hold: int, scrolling: bool,
               presented_at: float) -> None:
        """One frame reached the panel.

        :param blit: seconds spent copying the frame into the canvas.
        :param wait: seconds SwapOnVSync blocked.
        :param hold: the refreshes this frame was held for.
        :param scrolling: whether a scroll was running when it was presented.
        :param presented_at: ``time.perf_counter()`` when the swap returned.
        """
        previous = self._previous
        self._previous = (presented_at, scrolling, hold)
        self.last_frame = (presented_at, scrolling, threading.get_ident())
        if not scrolling:
            self._static_frames += 1
        elif self.watchdog is None and self.scrolling_now is not None \
                and os.environ.get("LEDMATRIX_STALL_WATCHDOG", "1") != "0":
            self.watchdog = StallWatchdog(self, **watchdog_settings())
            self.watchdog.start()
        elif previous is not None and previous[1]:
            interval = presented_at - previous[0]
            if interval < GAP_SECONDS:
                self._pending.append((interval, blit, wait, hold))

        if self._last_flush is None:
            self._last_flush = presented_at
        elif presented_at - self._last_flush >= self.flush_interval:
            self._hand_off()
            self._last_flush = presented_at

    def _hand_off(self) -> None:
        batch, self._pending = self._pending, []
        static, self._static_frames = self._static_frames, 0
        self._queue.put((batch, static))
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._run, daemon=True, name="frame-timing")
            self._worker.start()

    def drain(self) -> None:
        """Aggregate everything recorded so far, on the calling thread.

        For a caller that owns the recorder outright and wants exact numbers at
        a moment of its choosing -- the benchmark, between warm-up and run and
        at the end. Construct it with ``flush_interval=float('inf')`` so the
        worker never runs; the two must not aggregate at once.
        """
        batch, self._pending = self._pending, []
        static, self._static_frames = self._static_frames, 0
        self.aggregate(batch, static)

    # -- worker thread ------------------------------------------------------

    def _run(self) -> None:
        while True:
            batch, static = self._queue.get()
            try:
                self.aggregate(batch, static)
                self.write()
            except Exception:  # never let telemetry take anything down
                logger.debug("Frame timing flush failed", exc_info=True)

    def aggregate(self, batch: List[Tuple[float, float, float, int]],
                  static: int) -> None:
        """Fold one window of frames into the running totals."""
        totals = self.totals
        totals["static_frames"] += static

        per_hold = sorted(interval / max(1, hold)
                          for interval, _, _, hold in batch
                          if interval < FREEZE_SECONDS)
        if len(per_hold) >= MIN_FRAMES_FOR_REFRESH:
            estimate = per_hold[len(per_hold) // 10]
            current = self.refresh_period
            if estimate > 0 and (
                    current is None
                    or current * (1.0 - MAX_REFRESH_DROP) <= estimate < current):
                self.refresh_period = estimate
        period = self.refresh_period

        histograms = self.histograms
        for interval, blit, wait, hold in batch:
            totals["worst_interval_ms"] = max(totals["worst_interval_ms"],
                                              interval * 1000.0)
            if interval >= FREEZE_SECONDS:
                totals["freezes"] += 1
                totals["freeze_seconds"] += interval
                label = next(name for limit, name in FREEZE_BUCKETS
                             if interval < limit)
                totals["freeze_by"][label] += 1
                continue
            totals["scroll_frames"] += 1
            for name, value in (("blit", blit), ("wait", wait),
                                ("work", max(0.0, interval - blit - wait)),
                                ("interval_per_hold", interval / max(1, hold))):
                bucket = _bucket(value)
                histogram = histograms[name]
                histogram[bucket] = histogram.get(bucket, 0) + 1
            if period:
                missed = round(interval / period) - hold
                if missed >= 1:
                    totals["late_frames"] += 1
                    totals["missed_refreshes"] += missed
                    key = ("1" if missed == 1 else "2" if missed == 2
                           else "3-5" if missed <= 5 else "6+")
                    totals["late_by"][key] += 1
                elif missed <= -1:
                    totals["early_frames"] += 1

    def snapshot(self) -> Dict[str, Any]:
        """The JSON document: cumulative since this process started."""
        if not self._binding_checked:
            self._binding_gil = binding_releases_gil()
            self._binding_checked = True
        info = dict(self.info)
        info.setdefault("pi_model", _pi_model())
        period = self.refresh_period
        return {
            "version": SCHEMA_VERSION,
            "pid": os.getpid(),
            "started": self.started,
            "updated": time.time(),
            "bucket_ms": BUCKET_MS,
            "freeze_seconds": FREEZE_SECONDS,
            "measured_refresh_hz": round(1.0 / period, 2) if period else None,
            "binding_releases_gil": self._binding_gil,
            "info": info,
            "totals": self.totals,
            # JSON keys are strings; readers convert back.
            "histograms": {name: {str(k): v for k, v in sorted(h.items())}
                           for name, h in self.histograms.items()},
        }

    def write(self) -> None:
        """Replace the stats file atomically with the current snapshot."""
        directory = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".frame_stats.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.snapshot(), handle)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def watchdog_settings() -> Dict[str, float]:
    """StallWatchdog arguments from ``LEDMATRIX_STALL_WATCHDOG_MS``, if set.

    The poll comes down with the threshold, or a stall shorter than one poll
    would go unseen.
    """
    try:
        ms = float(os.environ.get("LEDMATRIX_STALL_WATCHDOG_MS") or 0)
    except ValueError:
        ms = 0.0
    if ms <= 0:
        return {}
    threshold = ms / 1000.0
    return {"threshold": threshold,
            "poll": min(WATCHDOG_POLL_SECONDS, threshold / 3)}


class StallWatchdog:
    """Log what the render thread is doing when a scroll stops presenting.

    See the module docstring. Polls; never touches the render thread.
    """

    def __init__(
        self,
        recorder: FrameTimingRecorder,
        threshold: float = STALL_SECONDS,
        poll: float = WATCHDOG_POLL_SECONDS,
        log_interval: float = STALL_LOG_INTERVAL,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self.recorder = recorder
        self.threshold = threshold
        self.poll = poll
        self.log_interval = log_interval
        self.clock = clock
        self.stalls = 0
        self._last_dump: Optional[float] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="stall-watchdog")
        self._thread.start()

    def _run(self) -> None:
        last_wake = self.clock()
        stall_from: Optional[float] = None   # presented_at of the stalled frame
        dumped = False
        while True:
            time.sleep(self.poll)
            now = self.clock()
            late = max(0.0, now - last_wake - self.poll)
            last_wake = now
            try:
                stall_from, dumped = self.check(now, late, stall_from, dumped)
            except Exception:  # never let a diagnostic take anything down
                logger.debug("Stall watchdog check failed", exc_info=True)

    def check(self, now: float, late: float, stall_from: Optional[float],
              dumped: bool) -> Tuple[Optional[float], bool]:
        """One look. Returns the updated (stall_from, dumped) state."""
        frame = self.recorder.last_frame
        if frame is None:
            return None, False
        presented_at, scrolling, ident = frame

        if stall_from is not None and presented_at != stall_from:
            # A frame arrived: the stall is over.
            if dumped:
                logger.warning(
                    "Render stall over: no frame for %.0fms",
                    (presented_at - stall_from) * 1000.0)
            return None, False

        scrolling_now = self.recorder.scrolling_now
        if stall_from is not None and (scrolling_now is None or not scrolling_now()):
            # The scroll ended without another frame: nothing more to time.
            return None, False
        age = now - presented_at
        if (stall_from is None and scrolling and age >= self.threshold
                and scrolling_now is not None and scrolling_now()):
            self.stalls += 1
            if self._last_dump is None or now - self._last_dump >= self.log_interval:
                self._last_dump = now
                logger.warning(self.describe(ident, age, late))
                return presented_at, True
            return presented_at, False
        return stall_from, dumped

    def describe(self, ident: int, age: float, late: float) -> str:
        """The stack dump: the stalled thread in full, the rest in brief."""
        names = {t.ident: t.name for t in threading.enumerate()}
        frames = sys._current_frames()
        lines = [
            f"Render stall: no frame for {age * 1000.0:.0f}ms mid-scroll "
            f"(watchdog woke {late * 1000.0:.0f}ms late"
            + ("; the interpreter itself was blocked" if late >= age / 2 else "")
            + ")",
            f"-- {names.get(ident, ident)} (presents frames):",
        ]
        stalled = frames.get(ident)
        if stalled is not None:
            lines.extend(line.rstrip() for line in
                         traceback.format_stack(stalled, limit=12))
        for other, frame in frames.items():
            if other in (ident, threading.get_ident()):
                continue
            top = traceback.extract_stack(frame, limit=3)
            where = " <- ".join(
                f"{os.path.basename(f.filename)}:{f.lineno} {f.name}"
                for f in reversed(top))
            lines.append(f"-- {names.get(other, other)}: {where}")
        return "\n".join(lines)
