"""
Render Pipeline for Vegas Mode

Composes plugin content into one wide strip and renders the visible window of
it each frame, using ScrollHelper for the numpy-backed scroll.
"""

import itertools
import logging
import os
import time
import threading
from collections import deque
from contextlib import nullcontext
from typing import Optional, List, Any, Dict, Deque, Tuple
from PIL import Image

from src.common.scroll_config import solve_crisp
from src.common.scroll_helper import ScrollHelper
from src.matrix_support import DEFAULT_REFRESH_LIMIT_HZ
from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.elements import ElementMeta, ElementRecord, LivePatch, LiveView, meta_of
from src.vegas_mode.live_worker import LEGACY_PREFETCH_JOIN_S, VegasWorker
from src.vegas_mode.geometry import separation_gap
from src.vegas_mode.stream_manager import StreamManager

logger = logging.getLogger(__name__)

#: Shortest gap between multi-display sync sends from the leader, for both the
#: Vegas scroll position and the controller's per-frame follower images. The
#: payloads are raw and cheap, and 90/s is above the follower's render rate.
SYNC_SEND_INTERVAL = 1.0 / 90


def join_plugin_rows(
    images: List[Image.Image], config: VegasModeConfig
) -> Tuple[Image.Image, List[Tuple[int, ElementMeta, int]]]:
    """Join one plugin's images into the block the strip will hold.

    Returns ``(block, layout)``, layout being ``(x, meta, width)`` for every
    image tagged as a live element (src/vegas_mode/elements.py), x measured
    from the block's left edge. A single image is returned as it is.

    A module function so tooling (scripts/render_plugin.py --vegas) lays a
    plugin out exactly as the ticker does.
    """
    if len(images) == 1:
        meta = meta_of(images[0])
        layout = [(0, meta, images[0].width)] if meta is not None else []
        return images[0], layout

    floor = max(0, config.intra_plugin_gap)
    target = max(0, config.min_content_separation)
    threshold = config.trim_threshold

    # Space by measured separation, not a flat gap. Rows drawn flush to their
    # own edges (sports score cards) would otherwise end up nearly touching,
    # while rows that already carry wide margins would be pushed needlessly
    # further apart.
    gaps = [
        separation_gap(images[i], images[i + 1], target, floor, threshold)
        for i in range(len(images) - 1)
    ]

    width = sum(img.width for img in images) + sum(gaps)
    height = max(img.height for img in images)

    block = Image.new('RGB', (width, height), (0, 0, 0))
    layout: List[Tuple[int, ElementMeta, int]] = []
    x = 0
    for i, img in enumerate(images):
        block.paste(img, (x, 0))
        meta = meta_of(img)
        if meta is not None:
            layout.append((x, meta, img.width))
        x += img.width + (gaps[i] if i < len(gaps) else 0)
    return block, layout


class RenderPipeline:
    """
    High-performance render pipeline for Vegas scroll mode.

    Key responsibilities:
    - Compose content segments into scrollable image
    - Manage scroll position and velocity
    - Render one frame per call at the target FPS
    - Extend the strip (continuous mode) or recompose and hot-swap it (swap
      mode) as content changes
    - Track scroll cycle completion
    """

    # Minimum gap between fetches of canvas-bound plugins, so their individual
    # stalls land in separate moments rather than one run of hitches.
    DEFERRED_DRAIN_INTERVAL = 2.0

    # Swaps timed before trusting a refresh measurement: about a second.
    REFRESH_SAMPLES = 96
    # Frames skipped first, while the hold from whatever ran before settles.
    REFRESH_WARMUP_FRAMES = 8
    # A panel measured within this fraction of its cap is keeping up with it.
    REFRESH_TOLERANCE = 0.03

    # Bumped by reset(). A prefetch thread records the value it started under
    # and drops its group if a reset happened meanwhile, so a fetch still in
    # flight when Vegas stops cannot land in the next run. Class-level so
    # pipelines built without __init__ (tests) still have it.
    _prefetch_generation = 0

    # Where each STATIC plugin's turn falls in the strip, as (x, plugin_id)
    # in ascending strip columns: x is the end of the content before it. When
    # that column reaches the right edge of the viewport, the coordinator
    # pauses the scroll for the plugin (next_static_trigger). Always replaced,
    # never mutated, so the class-level default is safe for pipelines built
    # without __init__ (tests).
    _static_markers: Tuple[Tuple[int, str], ...] = ()

    # Live elements in the strip (see "live element records" below). Replaced,
    # never mutated, like _static_markers, and class-level for the same reason.
    _elements: Tuple[ElementRecord, ...] = ()
    # Columns trimmed off the strip's front since it was composed.
    _strip_origin: int = 0
    # Bumped whenever a new strip replaces the old one (compose, reset), so
    # anything computed against the old strip can tell.
    _strip_gen: int = 0

    # Live updates (see apply_live_patches and src/vegas_mode/live_worker.py).
    # Where the viewport is, published every frame for the worker.
    _view: Optional[LiveView] = None
    # Set by the coordinator for a run in which live elements are on.
    _live_enabled: bool = False
    _live_worker: Optional[VegasWorker] = None
    # The last worker stopped: it finishes its current job, and hands over
    # what it has of a group, before the one-shot prefetch fetches another.
    _retired_worker: Optional[VegasWorker] = None
    #: Patches applied between two frames at most, and the bytes they may
    #: copy, in screens of pixels (at least one patch is always applied).
    LIVE_PATCHES_PER_FRAME = 4
    LIVE_PATCH_BUDGET_SCREENS = 2
    #: A worker that dies this often in this many seconds is not restarted
    #: again this run; live updates stop and the one-shot prefetch returns.
    LIVE_WORKER_MAX_DEATHS = 3
    LIVE_WORKER_DEATH_WINDOW = 600.0
    #: Frames between checks that the worker is still alive.
    LIVE_SUPERVISE_FRAMES = 256

    def __init__(
        self,
        config: VegasModeConfig,
        display_manager: Any,
        stream_manager: StreamManager
    ):
        """
        Initialize the render pipeline.

        Args:
            config: Vegas mode configuration
            display_manager: DisplayManager for rendering
            stream_manager: StreamManager for content
        """
        self.config = config
        self.display_manager = display_manager
        self.stream_manager = stream_manager
        self.sync_manager = None        # Optional DisplaySyncManager — set by coordinator
        self.sync_follower_left = True  # True = follower is LEFT of leader (default)
        self._last_sync_send = 0.0

        self.display_width = display_manager.width
        self.display_height = display_manager.height

        # ScrollHelper for optimized scrolling
        self.scroll_helper = ScrollHelper(
            self.display_width,
            self.display_height,
            logger
        )

        # The panel's real refresh rate, measured from our own vsync-blocked
        # swaps once scrolling starts. None until then; see _measure_refresh.
        self._measured_hz: Optional[float] = None
        self._swap_times: Deque[float] = deque(maxlen=self.REFRESH_SAMPLES + 1)

        # Configure scroll helper
        self._configure_scroll_helper()

        # Group prepared off the render thread, waiting to be appended.
        self._prepared_group = None
        # Plugins that need the shared canvas, appended one at a time.
        self._deferred_queue: List[str] = []
        self._last_drain_time = 0.0
        self._prefetch_thread: Optional[threading.Thread] = None
        self._prefetch_lock = threading.Lock()

        # Render state
        self._cycle_complete = False
        self._segments_in_scroll: List[str] = []  # Plugin IDs in current scroll
        self._record_by_seq: Dict[int, ElementRecord] = {}
        # Live updates. _applied: per record, the (epoch, digest) of the
        # pixels the strip holds. _live_slots / _live_ready: the worker's
        # hand-over, one slot per record (latest wins) and the order they
        # arrived in. Written by the worker, consumed by the render thread;
        # single-key dict operations and deque append/popleft only.
        self._applied: Dict[int, Tuple[int, Any]] = {}
        self._live_slots: Dict[int, LivePatch] = {}
        self._live_ready: Deque[int] = deque()
        self._worker_deaths: Deque[float] = deque()
        self._live_frames = 0

        # The sub-pixel path's pacing; the crisp path solves its own (frame_interval).
        self._frame_interval = config.get_frame_interval()
        self._cycle_start_time = 0.0

        # Statistics
        self.stats = {
            'frames_rendered': 0,
            'scroll_cycles': 0,
            'composition_count': 0,
            'hot_swaps': 0,
            'avg_frame_time_ms': 0.0,
        }
        self._frame_times: Deque[float] = deque(maxlen=100)  # Efficient fixed-size buffer

        logger.info(
            "RenderPipeline initialized: %dx%d @ %d FPS",
            self.display_width, self.display_height, config.target_fps
        )

    def _configure_scroll_helper(self) -> None:
        """Configure ScrollHelper with current settings."""
        self.scroll_helper.set_scroll_delay(self.config.scroll_delay)
        self.scroll_helper.set_sub_pixel_scrolling(self.config.sub_pixel_blend)

        # With smooth_scroll the strip moves a whole number of pixels per
        # presented frame, each frame held for frame_hold panel refreshes, and
        # SwapOnVSync is the clock -- the same crisp pacing the plugin tickers
        # use (src/common/scroll_config.py). The time-based path below has no
        # fixed relation to the refresh: at 90px/s on a panel refreshing at
        # 95Hz every frame lands 0.95px on, so text is re-blended at a
        # different phase each refresh, and any frame that misses a vsync is
        # followed by a double step. Measured on a 512x64 chain: 73fps against
        # a 95Hz panel, p99 21-28ms -- a visible hitch every few frames.
        self._crisp = None
        self._frame_hold = 1
        # Gaps timed under the old hold would be divided by the new one.
        self._swap_times.clear()
        if self.config.smooth_scroll and not self.config.sub_pixel_blend:
            self._crisp = solve_crisp(self.config.scroll_speed, self._refresh_hz())
            self._frame_hold = self._crisp.frame_hold
            self.scroll_helper.set_frame_based_scrolling(False)
            self.scroll_helper.set_scroll_speed(self._crisp.pixels_per_second)
            self.scroll_helper.set_pixels_per_frame(self._crisp.pixels_per_frame)
            logger.info(
                "Vegas scroll: %s (asked for %d px/s on a %.0fHz panel)",
                self._crisp.describe(), self.config.scroll_speed, self._refresh_hz()
            )
            self._apply_dynamic_duration_settings()
            return

        self.scroll_helper.set_pixels_per_frame(None)
        self.scroll_helper.set_frame_based_scrolling(self.config.frame_based_scrolling)

        # Config scroll_speed is always pixels per second, but ScrollHelper
        # takes it in different units depending on frame_based_scrolling:
        # - Frame-based: pixels per scroll_delay seconds (clamped to 0.1-5)
        # - Time-based: pixels per second (clamped to 1-500)
        # Both modes then advance by elapsed time; frame-based mode does not
        # step. So frame-based with scroll_delay only adds the clamp: the
        # applied speed is clamp(scroll_speed * scroll_delay, 0.1, 5) /
        # scroll_delay px/s.
        if self.config.frame_based_scrolling:
            pixels_per_frame = self.config.scroll_speed * self.config.scroll_delay
            self.scroll_helper.set_scroll_speed(pixels_per_frame)
        else:
            self.scroll_helper.set_scroll_speed(self.config.scroll_speed)
        self._apply_dynamic_duration_settings()

    def _apply_dynamic_duration_settings(self) -> None:
        self.scroll_helper.set_dynamic_duration_settings(
            enabled=self.config.dynamic_duration_enabled,
            min_duration=self.config.min_cycle_duration,
            max_duration=self.config.max_cycle_duration,
            buffer=0.1  # 10% buffer
        )

    def _cap_hz(self) -> float:
        """The panel's refresh cap, as the display manager reports it."""
        try:
            hz = float(getattr(self.display_manager, 'refresh_hz', 0) or 0)
        except (TypeError, ValueError):
            hz = 0.0
        return hz if hz > 0 else float(DEFAULT_REFRESH_LIMIT_HZ)

    def _refresh_hz(self) -> float:
        """The refresh to solve the crisp speed against: measured, else the cap."""
        return self._measured_hz or self._cap_hz()

    def _measure_refresh(self) -> None:
        """Time our swaps to learn the rate the panel really refreshes at.

        limit_refresh_rate_hz is a cap, not a rate. A long single chain cannot
        reach a high one: 4x128x64 at pwm_bits 8 refreshes at ~95Hz under a
        120Hz cap. Solving against the cap then picks a speed built for a
        refresh the panel never delivers -- 90px/s at "120Hz" is 3px every 4
        refreshes, visibly jumpy, where the real 95Hz allows 1px every refresh.

        SwapOnVSync blocks for frame_hold refreshes, and a swap can only come
        back late -- a missed vsync lengthens its gap by whole refreshes, never
        shortens one -- so the low end of the gaps is frame_hold refresh
        periods: the 10th percentile, as src/common/frame_timing.py uses. The
        median would track the render loop instead once most frames in the
        window were late (startup, a prefetch, a recompose), lock in a rate
        too low, and scroll faster than configured until restart. Measured
        once: the refresh only changes with the hardware config, which
        restarts us.
        """
        if self._crisp is None or self._measured_hz is not None:
            return
        if getattr(self.display_manager, 'matrix', None) is None:
            return  # No hardware: nothing blocks, so there is nothing to time.
        if self.stats['frames_rendered'] < self.REFRESH_WARMUP_FRAMES:
            return
        self._swap_times.append(time.monotonic())
        if len(self._swap_times) <= self.REFRESH_SAMPLES:
            return

        times = list(self._swap_times)
        gaps = sorted(b - a for a, b in zip(times, times[1:]))
        period = gaps[len(gaps) // 10]
        self._swap_times.clear()
        if period <= 0:
            return
        measured = self._frame_hold / period
        cap = self._cap_hz()
        if measured >= cap * (1.0 - self.REFRESH_TOLERANCE):
            self._measured_hz = cap
            return
        self._measured_hz = round(measured, 1)
        logger.info(
            "Vegas: panel refreshes at %.1fHz, below its %.0fHz cap; "
            "re-solving the scroll speed for the real rate",
            self._measured_hz, cap
        )
        self._configure_scroll_helper()

    @property
    def target_fps(self) -> float:
        """Frames per second this scroll presents when it keeps up."""
        if self._crisp is not None:
            return self._crisp.frames_per_second
        return float(self.config.target_fps)

    @property
    def frame_interval(self) -> float:
        """Shortest time the render loop should spend on one frame.

        With crisp pacing SwapOnVSync already blocks for frame_hold refreshes,
        so this is only a floor for when the swap does not block (no hardware,
        or the emulator). It must not exceed the real refresh period: a loop
        that sleeps even slightly longer than the panel drifts against it and
        misses a refresh every few frames -- which is what target_fps 90 on a
        95Hz panel did. The configured cap is at least the real refresh, so
        hold / cap never exceeds hold real periods -- the measured rate is
        deliberately not used here.
        """
        if self._crisp is not None:
            # 0.9: the floor must sit strictly below the real period, or the
            # surplus accumulates frame over frame until one misses.
            return 0.9 * self._frame_hold / self._cap_hz()
        return self._frame_interval

    def compose_scroll_content(self) -> bool:
        """
        Compose content from stream manager into scrollable image.

        Returns:
            True if composition successful
        """
        try:
            # Content grouped by plugin, so a separator can be placed at the
            # plugin boundaries only.
            grouped = self.stream_manager.get_grouped_content_for_composition()
            self._static_markers = ()
            # A compose replaces the strip, and every record with it.
            self._reset_records()

            if not grouped:
                logger.warning("No content available for composition")
                return False

            # Collapse each plugin's rows into a single block, joined by
            # intra_plugin_gap. ScrollHelper applies one uniform gap between the
            # items it is given, so handing it one item per plugin is what makes
            # separator_width mean "between plugins" instead of "between every
            # row". Without this, a per-row ticker such as the F1 scoreboard got
            # the full separator between each of its ~116 rows.
            blocks = []
            layouts = []
            total_rows = 0
            for _plugin_id, images in grouped:
                total_rows += len(images)
                block, layout = self._join_plugin_rows_with_layout(images)
                blocks.append(block)
                layouts.append(layout)

            # Create scrolling image via ScrollHelper.
            #
            # lead_gap is explicit because ScrollHelper otherwise prepends a
            # full display width of black — appropriate for a standalone ticker
            # scrolling in from off-screen, but in Vegas mode it is charged
            # once per cycle and reads as the panel switching off.
            self.scroll_helper.create_scrolling_image(
                content_items=blocks,
                item_gap=self.config.separator_width,
                element_gap=0,
                lead_gap=self.config.lead_in_width
            )

            # Verify scroll image was created successfully
            if not self.scroll_helper.has_strip():
                logger.error("ScrollHelper failed to create cached image")
                return False

            self._static_markers = self._markers_for_composition(blocks)
            self._register_elements(
                self._block_starts([b.width for b in blocks], 0, False,
                                   lead=self.config.lead_in_width),
                layouts)
            self._note_op('compose', self._strip_nbytes())

            # Track which plugins are in this scroll (get safely via buffer status)
            self._segments_in_scroll = self.stream_manager.get_active_plugin_ids()

            self.stats['composition_count'] += 1
            self._cycle_start_time = time.time()
            self._cycle_complete = False

            logger.info(
                "Composed scroll image: %dx%d, %d plugin block(s), %d rows, "
                "separator=%dpx between plugins, rows spaced to %dpx of ink "
                "(min added %dpx)",
                self.scroll_helper.total_scroll_width if self.scroll_helper.has_strip() else 0,
                self.display_height,
                len(blocks),
                total_rows,
                self.config.separator_width,
                self.config.min_content_separation,
                self.config.intra_plugin_gap,
            )

            return True

        except (ValueError, TypeError, OSError, RuntimeError):
            # Expected errors from image operations, scroll helper, or bad data
            logger.exception("Error composing scroll content")
            return False

    def _note_op(self, kind: str, nbytes: int = 0) -> None:
        """Tag the next presented frame with render-thread work done for it.

        See "Operations" in src/common/frame_timing.py: a soak can then say
        how often a frame straight after an extension or a patch was late,
        rather than only how often any frame was.
        """
        timing = getattr(self.display_manager, 'frame_timing', None)
        note = getattr(timing, 'note_op', None)
        if note is not None:
            note(kind, nbytes)

    def _strip_nbytes(self) -> int:
        array = self.scroll_helper.cached_array
        return int(array.nbytes) if array is not None else 0

    def _markers_for_composition(self, blocks: List[Image.Image]) -> Tuple[Tuple[int, str], ...]:
        """Static markers for a strip just built by create_scrolling_image.

        Mirrors its layout: lead_in_width, then each block followed by
        separator_width except the last. A STATIC plugin's marker is the end
        of whatever content precedes it (the lead-in, if nothing does).
        """
        layout_fn = getattr(self.stream_manager, 'get_static_layout', None)
        if layout_fn is None:
            return ()
        layout = layout_fn()
        if not any(is_static for _pid, is_static in layout):
            return ()
        markers = []
        end = max(0, int(self.config.lead_in_width))
        x = end
        block_index = 0
        for plugin_id, is_static in layout:
            if is_static:
                markers.append((end, plugin_id))
                continue
            if block_index >= len(blocks):
                break
            end = x + blocks[block_index].width
            x = end + self.config.separator_width
            block_index += 1
        return tuple(markers)

    def _add_static_markers(self, markers: List[Tuple[int, str]]) -> None:
        if markers:
            self._static_markers = tuple(
                sorted(self._static_markers + tuple(markers), key=lambda m: m[0]))

    def next_static_trigger(self) -> Optional[str]:
        """
        The STATIC plugin whose turn the scroll has just reached, if any.

        Called every frame by the coordinator, so it is a comparison against
        the first marker and nothing more. The marker is consumed: a plugin
        pauses the scroll once per place it holds in the strip.
        """
        markers = self._static_markers
        if not markers:
            return None
        x, plugin_id = markers[0]
        if x > self.scroll_helper.scroll_position + self.display_width:
            return None
        self._static_markers = markers[1:]
        return plugin_id

    def needs_extension(self) -> bool:
        """
        Whether the strip should be extended with the next group of plugins.

        Cheap enough to call every frame: it is arithmetic over cached state.
        """
        if not self.config.continuous_scroll or not self.scroll_helper.has_strip():
            return False
        threshold = int(self.display_width * self.config.extend_threshold_screens)
        return self.scroll_helper.remaining_unscrolled() <= threshold

    def start_prefetch(self) -> None:
        """
        Begin preparing the next group in the background, if not already doing so.

        This is what makes the join seamless rather than merely continuous:
        fetching a group costs 0.5-4.8s (rendering leaderboard and baseball cards
        dominates), and doing it on the render thread stalls the scroll for that
        long. Off the render thread there is a whole group's scroll time to work
        in, so by the time the strip needs extending the content is already sat
        waiting.

        Only paths that avoid the shared display canvas run here; anything
        needing it is marked and picked up on the render thread, where it is
        safe. Those are the cheap ones — display capture measured 12-14ms
        against seconds for the native renders.
        """
        if not self.config.continuous_scroll:
            return

        # With live elements in the strip, the live-element worker fetches
        # groups too, one plugin at a time between its redraws, so that only
        # one thread ever draws for the strip.
        worker = self._live_worker
        if worker is not None and worker.is_alive():
            with self._prefetch_lock:
                if self._prepared_group is not None:
                    return
            worker.request_group()
            return

        with self._prefetch_lock:
            if self._prefetch_thread is not None and self._prefetch_thread.is_alive():
                return
            if self._prepared_group is not None:
                return  # already have one waiting
            generation = self._prefetch_generation
            retired = self._retired_worker

            def _work():
                # Deprioritise against the render loop for CPU time (Linux
                # applies nice per thread). Nice does nothing about the GIL,
                # which Pillow's drawing holds (docs/OFFSCREEN_RENDERING.md,
                # risk 5); the render gate below is what keeps this thread off
                # it while the render thread needs it.
                try:
                    os.nice(10)
                except (OSError, AttributeError):
                    pass
                # A live-element worker just stopped finishes its current job
                # and hands over what it has of a group. Wait for it, so only
                # one thread draws and a group it handed over is not replaced.
                if retired is not None and retired is not threading.current_thread()                         and retired.is_alive():
                    retired.join(LEGACY_PREFETCH_JOIN_S)
                with self._prefetch_lock:
                    if generation != self._prefetch_generation                             or self._prepared_group is not None:
                        return
                # With vegas_scroll.prefetch_gate on, run only while the render
                # thread waits on vsync; see src/common/render_gate.py.
                gate = getattr(self.display_manager, 'render_gate', None)
                try:
                    with gate.yielding() if gate is not None else nullcontext():
                        group = self.stream_manager.take_next_group(offscreen_only=True)
                except Exception:
                    logger.exception("Background prefetch failed")
                    group = []
                with self._prefetch_lock:
                    if generation != self._prefetch_generation:
                        return  # Vegas was reset while this was fetching
                    if self._prepared_group is None:
                        self._prepared_group = group

            self._prefetch_thread = threading.Thread(
                target=_work, daemon=True, name="vegas-strip-prefetch")
            self._prefetch_thread.start()

    def drain_deferred(self) -> bool:
        """
        Fetch one queued canvas-bound plugin and append it to the strip.

        Called once per frame. These plugins cannot be prepared off the render
        thread — display capture and scroll-content generation both need the
        shared canvas — so each costs roughly 290ms here. Doing one at a time
        spreads that out instead of stalling for the whole group at once, and the
        strip's lookahead means nothing runs dry while they arrive.

        The cost is that a deferred plugin appears slightly after the group it
        came with, which is a fair trade for a smooth scroll.

        Returns:
            True if a plugin was appended
        """
        if not self._deferred_queue:
            return False

        # Space the drains out. Each costs 40-600ms, and taking them back to
        # back turns one long stall into a train of short ones — barely better.
        # With a healthy lookahead there is no hurry, so wait a beat between
        # them; when the strip is actually running short, fetch immediately.
        threshold = int(self.display_width * self.config.extend_threshold_screens)
        urgent = self.scroll_helper.remaining_unscrolled() <= threshold
        if not urgent:
            now = time.time()
            if now - self._last_drain_time < self.DEFERRED_DRAIN_INTERVAL:
                return False
            self._last_drain_time = now
        else:
            self._last_drain_time = time.time()

        plugin_id = self._deferred_queue.pop(0)
        plugins = getattr(self.stream_manager.plugin_manager, 'plugins', {})
        plugin = plugins.get(plugin_id)
        if plugin is None:
            return False

        try:
            images = self.stream_manager.plugin_adapter.get_content(plugin, plugin_id)
        except Exception:
            logger.exception("[%s] Error fetching deferred content", plugin_id)
            return False

        if not images:
            return False

        appended = self.scroll_helper.append_content(
            content_items=[self._join_plugin_rows(images)],
            item_gap=self.config.separator_width,
            element_gap=0,
        )
        if appended:
            self._note_op('extend', self._strip_nbytes())
            logger.info(
                "[%s] Appended deferred content: strip now %dpx, %dpx ahead",
                plugin_id, self.scroll_helper.total_scroll_width,
                self.scroll_helper.remaining_unscrolled()
            )
        return appended

    def has_deferred(self) -> bool:
        """Whether any canvas-bound plugins are still queued."""
        return bool(self._deferred_queue)

    def _claim_prepared_group(self):
        """Take the prefetched group, if one is ready."""
        with self._prefetch_lock:
            group = self._prepared_group
            self._prepared_group = None
        return group

    def extend_scroll_content(self) -> bool:
        """
        Append the next group of plugins to the strip, without interrupting motion.

        This is what replaces the swap. Scroll position is untouched, so the new
        content simply arrives from the right; there is no substitution to see
        and no restart with the viewport already full.

        Consumed columns behind the viewport are then released, keeping the strip
        bounded however long Vegas runs.

        Returns:
            True if the strip was extended
        """
        try:
            grouped = self._claim_prepared_group()
            if grouped is None:
                # Nothing prepared (first extension, or prefetch still running).
                # Fetch inline; the scroll hitches, but content keeps flowing.
                logger.info("No prepared group ready; fetching inline")
                grouped = self.stream_manager.take_next_group()

            if not grouped:
                logger.warning("No content available to extend the scroll strip")
                return False

            # STATIC plugins pause the scroll instead of joining the strip.
            # Note each one's place -- how many of this group's blocks come
            # before it -- and take it out before anything else sees it.
            is_static = getattr(self.stream_manager, 'is_static_plugin', None)
            statics: List[Tuple[int, str]] = []
            content: List[Tuple[str, Optional[List[Image.Image]]]] = []
            for pid, images in grouped:
                if is_static is not None and is_static(pid):
                    statics.append((sum(1 for _p, imgs in content if imgs), pid))
                else:
                    content.append((pid, images))
            grouped = content
            # From the helper's own bookkeeping, never cached_image: reading
            # that would build the full PIL strip the helper now defers.
            strip_end = (self.scroll_helper.total_scroll_width
                         if self.scroll_helper.has_strip() else 0)

            # Plugins the background thread had to defer need the shared canvas,
            # so they can only be fetched here. Queue them rather than doing all
            # of them now: measured, six in one go held the render thread for
            # 1.75s. They are trickled in one per frame by drain_deferred(),
            # which the strip's lookahead comfortably absorbs.
            deferred = [pid for pid, images in grouped if images is None]
            if deferred:
                self._deferred_queue.extend(deferred)
                logger.info(
                    "Queued %d plugin(s) needing the render thread: %s",
                    len(deferred), ', '.join(deferred)
                )

            grouped = [(pid, imgs) for pid, imgs in grouped if imgs]

            if not grouped:
                # Nothing is appended, so each STATIC turn falls at the end
                # of the strip as it stands.
                self._add_static_markers([(strip_end, pid) for _n, pid in statics])
                if deferred:
                    # Everything in this group is queued; the queue will extend
                    # the strip as it drains, so this is not a failure.
                    logger.info("Whole group deferred; strip will extend as it drains")
                else:
                    logger.info("Nothing to show in this group; fetching the next")
                self.start_prefetch()
                return bool(deferred)

            blocks = []
            layouts = []
            total_rows = 0
            for _plugin_id, images in grouped:
                total_rows += len(images)
                block, layout = self._join_plugin_rows_with_layout(images)
                blocks.append(block)
                layouts.append(layout)

            had_strip = self.scroll_helper.has_strip()
            if not had_strip:
                # append_content is about to build a strip from scratch.
                self._reset_records()
            appended = self.scroll_helper.append_content(
                content_items=blocks,
                item_gap=self.config.separator_width,
                element_gap=0,
            )
            if not appended:
                return False
            moved = self._strip_nbytes()

            # Where each block starts, laid out as append_content does: a
            # separator before every block, or -- when there was no strip to
            # extend -- as create_scrolling_image does with no lead-in.
            starts = self._block_starts([b.width for b in blocks], strip_end, had_strip)
            self._register_elements(starts, layouts)
            if statics:
                ends = [start + block.width for start, block in zip(starts, blocks)]
                self._add_static_markers([
                    (ends[n - 1] if n > 0 else strip_end, pid) for n, pid in statics
                ])

            # Keep a screen's worth behind the viewport as a safety margin.
            cut = self.scroll_helper.drop_scrolled_prefix(keep_before=self.display_width)
            if cut and self._static_markers:
                self._static_markers = tuple(
                    (max(0, x - cut), pid) for x, pid in self._static_markers)
            self._forget_trimmed_records(cut)
            # The append built the whole strip anew, and a trim copies what is
            # left of it again: both land in the frame after this one.
            self._note_op('extend', moved + (self._strip_nbytes() if cut else 0))

            self._segments_in_scroll = [pid for pid, _ in grouped]
            self.stats['composition_count'] += 1
            self.stats['extensions'] = self.stats.get('extensions', 0) + 1

            logger.info(
                "Extended scroll strip with %d plugin block(s), %d rows: "
                "strip now %dpx, %dpx still ahead of the viewport",
                len(blocks), total_rows, self.scroll_helper.total_scroll_width,
                self.scroll_helper.remaining_unscrolled()
            )

            # Line up the group after this one straight away, so it is ready
            # well before the strip runs short again.
            self.start_prefetch()
            return True

        except (ValueError, TypeError, OSError, RuntimeError):
            logger.exception("Error extending scroll content")
            return False

    def _join_plugin_rows(self, images: List[Image.Image]) -> Image.Image:
        """
        Concatenate one plugin's images into a single block.

        Args:
            images: That plugin's content, in order

        Returns:
            A single image with the rows laid out left to right, separated by
            ``intra_plugin_gap``. Returned unchanged when there is only one row,
            which is the common case and avoids a pointless copy.
        """
        return self._join_plugin_rows_with_layout(images)[0]

    def _join_plugin_rows_with_layout(
        self, images: List[Image.Image]
    ) -> Tuple[Image.Image, List[Tuple[int, ElementMeta, int]]]:
        """_join_plugin_rows, plus where each live element landed in the block.

        See join_plugin_rows.
        """
        return join_plugin_rows(images, self.config)

    # -- live element records ---------------------------------------------
    #
    # Where each live element sits in the strip (ElementRecord), kept so a
    # redraw can later be swapped into exactly its columns. Coordinates are
    # absolute: a record's column in the strip is abs_x - _strip_origin, and
    # a trim moves the origin instead of every record. Only the render thread
    # changes any of this, at the points where it builds or trims the strip.

    def _block_starts(self, widths: List[int], strip_end: int, had_strip: bool,
                      lead: int = 0) -> List[int]:
        """Strip columns where each of these blocks starts once placed.

        Mirrors ScrollHelper exactly: append_content puts a separator before
        every block after an existing strip; create_scrolling_image (a compose,
        or an append with nothing to extend) puts ``lead`` columns first and a
        separator between blocks.
        """
        gap = max(0, self.config.separator_width)
        starts = []
        if had_strip:
            x = strip_end
            for width in widths:
                x += gap
                starts.append(x)
                x += width
        else:
            x = max(0, int(lead))
            for width in widths:
                starts.append(x)
                x += width + gap
        return starts

    def _next_record_seq(self) -> int:
        counter = self.__dict__.get('_record_counter')
        if counter is None:
            counter = self._record_counter = itertools.count(1)
        return next(counter)

    def _register_elements(
        self, starts: List[int], layouts: List[List[Tuple[int, ElementMeta, int]]]
    ) -> int:
        """Record every live element in blocks just placed at ``starts``."""
        new = []
        for start, layout in zip(starts, layouts):
            for offset, meta, width in layout:
                new.append(ElementRecord(
                    seq=self._next_record_seq(), plugin_id=meta.plugin_id,
                    key=meta.key, abs_x=self._strip_origin + start + offset,
                    width=width, epoch=meta.epoch, digest=meta.digest,
                    refresh_hz=meta.refresh_hz))
        if new:
            self._elements = self._elements + tuple(new)
            by_seq = self.__dict__.setdefault('_record_by_seq', {})
            applied = self.__dict__.setdefault('_applied', {})
            for record in new:
                by_seq[record.seq] = record
                applied[record.seq] = (record.epoch, record.digest)
            if self._live_enabled:
                self._ensure_live_worker()
        return len(new)

    def _forget_trimmed_records(self, cut: int) -> None:
        """The strip lost ``cut`` columns off its front: move the origin on."""
        if cut <= 0:
            return
        self._strip_origin += cut
        origin = self._strip_origin
        records = self._elements
        if not records:
            return
        kept = tuple(r for r in records if r.abs_x + r.width > origin)
        if len(kept) != len(records):
            by_seq = self.__dict__.setdefault('_record_by_seq', {})
            applied = self.__dict__.setdefault('_applied', {})
            slots = self.__dict__.setdefault('_live_slots', {})
            for record in records:
                if record.abs_x + record.width <= origin:
                    by_seq.pop(record.seq, None)
                    applied.pop(record.seq, None)
                    slots.pop(record.seq, None)
            self._elements = kept

    def _reset_records(self) -> None:
        """A new strip: nothing recorded, coordinates from zero, a new generation."""
        self._strip_gen += 1
        self._strip_origin = 0
        self._elements = ()
        self._record_by_seq = {}
        self._applied = {}
        # Patches still queued belong to the old strip; apply would drop them
        # on their generation anyway, but there is no reason to keep them.
        self._live_slots = {}
        self._live_ready = deque()
        self._view = None

    def has_live_records(self) -> bool:
        """Whether the strip holds any live element."""
        return bool(self._elements)

    # -- live updates ---------------------------------------------------------

    def set_live(self, enabled: bool) -> None:
        """Switch live updates on or off for this run (the coordinator decides)."""
        self._live_enabled = enabled
        if enabled:
            if self._elements:
                self._ensure_live_worker()
        elif self._stop_live_worker():
            # The worker was fetching the strip's groups as well. Hand that
            # back to the one-shot prefetch now: nothing else asks for a group
            # until the next extension, which would find none prepared and
            # fetch inline, stalling the scroll.
            self.start_prefetch()

    def notify_live_data(self, plugin_id: str) -> None:
        """A plugin's data may have changed: wake the worker, if one runs."""
        worker = self._live_worker
        if worker is not None:
            worker.notify_data(plugin_id)

    def _ensure_live_worker(self) -> None:
        """Start the live-element worker, or restart one that died.

        Started lazily, by the first live element placed: an install with no
        plugin that has live elements keeps the one-shot prefetch thread and
        never runs this worker at all. A worker that keeps dying is given up
        on for the run; live updates stop and the one-shot prefetch returns.
        """
        if not self._live_enabled:
            return
        worker = self._live_worker
        if worker is not None and worker.is_alive():
            return
        if worker is None and not self._elements:
            # Nothing live in the strip: the one-shot prefetch does the work
            # until a live element is placed (_register_elements).
            return
        deaths = self.__dict__.setdefault('_worker_deaths', deque())
        now = time.monotonic()
        if worker is not None:
            deaths.append(now)
            while deaths and now - deaths[0] > self.LIVE_WORKER_DEATH_WINDOW:
                deaths.popleft()
            if len(deaths) >= self.LIVE_WORKER_MAX_DEATHS:
                logger.error(
                    "Vegas live worker stopped %d times in %.0fs; live updates "
                    "are off until Vegas restarts", len(deaths),
                    self.LIVE_WORKER_DEATH_WINDOW)
                self._live_enabled = False
                self._live_worker = None
                # Whatever group the dead worker was fetching is lost.
                self.start_prefetch()
                return
            logger.warning("Vegas live worker was not running; restarting it")
        worker = VegasWorker(self)
        self._live_worker = worker
        worker.start()
        # Whatever the one-shot prefetch was asked for, the worker now does.
        with self._prefetch_lock:
            wanted = self._prepared_group is None
        if wanted and self.config.continuous_scroll:
            worker.request_group()

    def _stop_live_worker(self) -> bool:
        """Ask the worker to stop after its current job. Whether one was running."""
        worker, self._live_worker = self._live_worker, None
        if worker is None:
            return False
        self._retired_worker = worker
        worker.stop()
        return True

    def apply_live_patches(self) -> int:
        """Copy the worker's finished redraws into the strip. Render thread only.

        Called between two frames (coordinator.run_frame). The only work here
        is popping prepared patches and a numpy slice copy per patch -- no
        drawing, no locks, no allocation -- bounded to LIVE_PATCHES_PER_FRAME
        patches or LIVE_PATCH_BUDGET_SCREENS screens of bytes, whichever comes
        first (always at least one). A patch is dropped when it no longer
        fits: made for an older strip, for an element trimmed away or already
        behind the screen, or older than what the strip already shows.

        Returns:
            Patches applied.
        """
        if self._live_enabled:
            self._live_frames = self.__dict__.get('_live_frames', 0) + 1
            if self._live_frames % self.LIVE_SUPERVISE_FRAMES == 0:
                self._ensure_live_worker()
        ready = self.__dict__.get('_live_ready')
        if not ready:
            return 0
        slots = self._live_slots
        if getattr(self, 'sync_manager', None) is not None:
            # Defensive: live elements are never on under sync, and the
            # follower would not see a patch.
            ready.clear()
            slots.clear()
            return 0
        budget = (self.LIVE_PATCH_BUDGET_SCREENS * self.display_width
                  * self.display_height * 3)
        helper = self.scroll_helper
        left_edge = int(helper.scroll_position)
        applied = 0
        moved = 0
        while ready and applied < self.LIVE_PATCHES_PER_FRAME \
                and (applied == 0 or moved < budget):
            seq = ready.popleft()
            patch = slots.pop(seq, None)
            if patch is None:
                continue        # a newer patch for this record already went
            record = self._record_by_seq.get(seq)
            if record is None or patch.strip_gen != self._strip_gen:
                continue
            previous = self._applied.get(seq)
            if previous is not None and patch.epoch < previous[0]:
                continue
            x = record.abs_x - self._strip_origin
            if x + record.width <= left_edge:
                continue        # scrolled past; nobody will see it
            moved += helper.patch_columns(x, patch.pixels)
            self._applied[seq] = (patch.epoch, patch.digest)
            applied += 1
        if applied:
            self._note_op('patch', moved)
        return applied

    def live_records(self) -> Tuple[ElementRecord, ...]:
        """The live elements in the strip, in the order they were placed."""
        return self._elements

    def render_frame(self) -> bool:
        """
        Render a single frame to the display.

        Called once per frame by the coordinator, which paces the calls by
        frame_interval (see that property) rather than a fixed rate.

        Returns:
            True if frame was rendered, False if no content
        """
        frame_start = time.time()

        try:
            if not self.scroll_helper.has_strip():
                return False

            # Update scroll position
            self.scroll_helper.update_scroll_position()
            # Where the viewport is now, for the live-element worker: one
            # tuple store, read by the worker without a lock.
            left = self._strip_origin + int(self.scroll_helper.scroll_position)
            self._view = LiveView(
                abs_left=left, abs_right=left + self.display_width,
                abs_end=self._strip_origin + self.scroll_helper.total_scroll_width,
                t_mono=time.monotonic())

            # Determine if the cycle is done.
            #
            # get_visible_portion wraps: once scroll_position + display_width
            # passes the end of the strip it fills the right-hand side of the
            # frame from the *head* of the same strip. So the last
            # display_width of travel shows the cycle's first plugin re-entering
            # on the right while its last plugin exits on the left, and the
            # recompose that follows then replaces both at once. That reads as
            # the ticker "switching mid-scroll".
            #
            # This used to be hidden because the strip began with a full
            # display_width of blank, so the wrapped-in region was black.
            # lead_in_width now defaults to 0 (that blank was 10s of dead panel
            # at 50px/s), which exposed the wrap — so the cycle has to end
            # before it, one display width earlier.
            #
            # A strip no wider than the display never wraps, and subtracting
            # would make the cycle complete instantly, so clamp in that case.
            # In continuous mode there is no cycle to complete: the strip is
            # extended before the scroll can reach its end, so the wrap is never
            # entered and motion never stops. The completion path below stays for
            # the swap behaviour and as a backstop if an extension fails.
            wrap_point = self.scroll_helper.total_scroll_width
            if wrap_point > self.display_width:
                wrap_point -= self.display_width

            at_wrap_point = (
                not self._cycle_complete and
                self.scroll_helper.total_distance_scrolled >= wrap_point
            )

            if at_wrap_point or self.scroll_helper.is_scroll_complete():
                if not self._cycle_complete:
                    self._cycle_complete = True
                    self.stats['scroll_cycles'] += 1
                    logger.info(
                        "Scroll cycle complete after %.1fs",
                        time.time() - self._cycle_start_time
                    )
                    # Deliberately leave the last rendered frame on the panel.
                    #
                    # This used to push a blank frame so no post-wrap content
                    # could be seen while the next cycle was composed. But
                    # recomposing is synchronous and fetches plugin content:
                    # measured 84ms at best and 4.8s at worst on a 512px panel,
                    # and every millisecond of it was black. Holding the last
                    # frame instead turns that into a brief freeze, which reads
                    # as far less broken than the display switching off. The
                    # frame is already past the end of the content, so there is
                    # no second-pass content to leak.
                return True  # Cycle done; coordinator starts new cycle next frame

            # Get visible portion
            visible_frame = self.scroll_helper.get_visible_portion()
            if not visible_frame:
                return False

            # Render to display
            self.display_manager.image = visible_frame
            self.display_manager.update_display()

            # Multi-display sync: send scroll position to follower.
            # The follower renders from its own cached_array (kept identical to the
            # leader's via TCP image transfer at each new_cycle) at scroll_x ± display_width.
            if self.sync_manager:
                now = time.time()
                if now - self._last_sync_send >= SYNC_SEND_INTERVAL:
                    self._last_sync_send = now
                    self.sync_manager.send_scroll_x(self.scroll_helper.scroll_position)

            # Update scrolling state
            self.display_manager.set_scrolling_state(True, self._frame_hold)

            # Track statistics
            self.stats['frames_rendered'] += 1
            self._measure_refresh()
            frame_time = time.time() - frame_start
            self._track_frame_time(frame_time)

            return True

        except (ValueError, TypeError, OSError, RuntimeError):
            # Expected errors from scroll helper or display manager operations
            logger.exception("Error rendering frame")
            return False

    def _track_frame_time(self, frame_time: float) -> None:
        """Track frame timing for statistics."""
        self._frame_times.append(frame_time)  # deque with maxlen auto-removes old entries

        if self._frame_times:
            self.stats['avg_frame_time_ms'] = (
                sum(self._frame_times) / len(self._frame_times) * 1000
            )

    def is_cycle_complete(self) -> bool:
        """Check if current scroll cycle is complete."""
        return self._cycle_complete

    def should_recompose(self) -> bool:
        """
        Check if scroll content should be recomposed.

        Returns True when:
        - Cycle is complete and we should start fresh
        - A plugin currently visible in the scroll has pending updated data
          (e.g. a live score changed) — standalone (non-sync) mode only
        """
        if self._cycle_complete:
            return True

        # When multi-display sync is active, defer mid-cycle hot swaps until the
        # cycle ends naturally. Hot swaps block the render loop for 15-30ms while
        # the image is rebuilt, causing a freeze+jump that the follower perceives
        # as a speed-up. Deferring to cycle boundaries keeps transitions clean;
        # the pending updates are still applied, by the recompose at cycle end.
        if self.sync_manager is not None:
            return False

        # Trigger recompose when pending updates affect visible segments, so
        # live score/status changes reach the display within a few seconds
        # instead of waiting for the next full cycle.
        if self.stream_manager.has_pending_updates_for_visible_segments():
            return True

        return False

    def refresh_updated_plugins(self) -> bool:
        """
        Let changed plugin data reach the strip without interrupting motion.

        Used instead of :meth:`hot_swap_content` when scrolling continuously.
        The swap rebuilds the whole image and repositions the scroll, which is
        visible as a freeze and a jump; the strip is extended here rather than
        replaced, so it is enough to drop the stale caches and let the plugin
        recompose when it next comes round.

        Returns:
            True if any plugin's cached content was dropped.
        """
        try:
            return bool(self.stream_manager.invalidate_pending_updates())
        except Exception:  # pylint: disable=broad-except
            logger.exception("Failed to refresh updated plugins")
            return False

    def hot_swap_content(self) -> bool:
        """
        Refetch the plugins with pending updates and recompose the strip.

        Swap mode only (continuous mode uses :meth:`refresh_updated_plugins`).
        Called when :meth:`should_recompose` finds a visible plugin with
        pending updates. The scroll resumes at the same relative position in
        the rebuilt strip.

        Returns:
            True if swap occurred
        """
        try:
            # Snapshot position before swap so we can reposition after.
            # The new image has completely different content — if scroll_position
            # is left unchanged it lands at an arbitrary mid-content point in the
            # new image, causing a visible jump on both displays.
            old_width = self.scroll_helper.total_scroll_width
            old_pos = self.scroll_helper.scroll_position

            self.stream_manager.process_updates()

            # Recompose with updated content
            if self.compose_scroll_content():
                # Map scroll position proportionally into the new image width so
                # we resume at the same relative progress through the content.
                # This keeps the visual tempo consistent and avoids the jump that
                # occurred when old scroll_position landed arbitrarily in new image.
                new_width = self.scroll_helper.total_scroll_width
                if old_width > 0 and new_width > 0:
                    ratio = (old_pos % old_width) / old_width
                    self.scroll_helper.scroll_position = ratio * new_width
                else:
                    self.scroll_helper.scroll_position = 0.0

                self.stats['hot_swaps'] += 1
                logger.debug(
                    "Hot-swap completed: scroll repositioned %.0f→%.0f (%.1f%% of new %dpx image)",
                    old_pos, self.scroll_helper.scroll_position,
                    (self.scroll_helper.scroll_position / new_width * 100) if new_width else 0,
                    new_width,
                )
                return True

            return False

        except (ValueError, TypeError, OSError, RuntimeError):
            # Expected errors from stream manager or composition operations
            logger.exception("Error during hot-swap")
            return False

    def start_new_cycle(self) -> bool:
        """
        Start a new scroll cycle.

        Fetches fresh content and recomposes.

        Returns:
            True if new cycle started successfully
        """
        # Reset scroll position
        self.scroll_helper.reset_scroll()
        self._cycle_complete = False

        # Clear buffer from previous cycle so new content is fetched
        self.stream_manager.advance_cycle()

        # Refresh stream content (picks up plugin list changes)
        self.stream_manager.refresh()

        # Reinitialize stream (fills buffer with fresh content)
        if not self.stream_manager.initialize():
            logger.warning("Failed to reinitialize stream for new cycle")
            return False

        # Compose new scroll content
        result = self.compose_scroll_content()

        if result and self.sync_manager:
            # Start the leader past the lead-in gap so it immediately shows
            # content, leaving the follower on the blank gap for a clean
            # transition rather than near-end content wrapping around.
            self.scroll_helper.scroll_position = float(self.config.lead_in_width)

            # Signal follower that a new cycle started (triggers its own rebuild)
            self.sync_manager.send_new_cycle()
            # Push the actual scroll image over TCP so follower has identical pixels.
            # Done in a background thread to not block the render loop (~15ms transfer).
            image = self.scroll_helper.cached_image
            if image is not None:
                threading.Thread(
                    target=self.sync_manager.send_scroll_image,
                    args=(image,),
                    daemon=True, name="sync-image-push"
                ).start()

        return result

    def get_current_scroll_info(self) -> Dict[str, Any]:
        """Get current scroll state information."""
        scroll_info = self.scroll_helper.get_scroll_info()
        return {
            **scroll_info,
            'cycle_complete': self._cycle_complete,
            'plugins_in_scroll': self._segments_in_scroll,
            'stats': self.stats.copy(),
        }

    def get_scroll_position(self) -> int:
        """
        Get current scroll position.

        Used by coordinator to save position before static pause.

        Returns:
            Current scroll position in pixels
        """
        return int(self.scroll_helper.scroll_position)

    def set_scroll_position(self, position: int) -> None:
        """
        Set scroll position.

        Used by coordinator to restore position after static pause.

        Args:
            position: Scroll position in pixels
        """
        self.scroll_helper.scroll_position = float(position)

    def update_config(self, new_config: VegasModeConfig) -> None:
        """
        Update render pipeline configuration.

        Args:
            new_config: New configuration to apply
        """
        old_fps = self.config.target_fps
        self.config = new_config
        self._frame_interval = new_config.get_frame_interval()

        # Reconfigure scroll helper
        self._configure_scroll_helper()

        if old_fps != new_config.target_fps:
            logger.info("FPS target updated: %d -> %d", old_fps, new_config.target_fps)

    def reset(self) -> None:
        """Reset the render pipeline state."""
        self.scroll_helper.reset_scroll()
        self.scroll_helper.clear_cache()

        self._cycle_complete = False
        self._segments_in_scroll = []
        self._frame_times = deque(maxlen=100)

        # Content lined up for the old run belongs to it. Left in place, the
        # first extension after Vegas is switched back on appended that stale
        # group -- including plugins disabled in the meantime -- and the
        # deferred queue went on fetching the old run's plugins.
        with self._prefetch_lock:
            self._prefetch_generation += 1
            self._prepared_group = None
            self._deferred_queue = []
        self._static_markers = ()
        self._stop_live_worker()
        self._reset_records()

        self.display_manager.set_scrolling_state(False)

        logger.info("RenderPipeline reset")

    def cleanup(self) -> None:
        """Clean up resources."""
        self.reset()
        self.display_manager.set_scrolling_state(False)
        logger.debug("RenderPipeline cleanup complete")

    def get_dynamic_duration(self) -> float:
        """Get the calculated dynamic duration for current content."""
        return float(self.scroll_helper.get_dynamic_duration())
