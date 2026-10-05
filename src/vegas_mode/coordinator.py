"""
Vegas Mode Coordinator

Main orchestrator for Vegas-style continuous scroll mode. Coordinates between
StreamManager, RenderPipeline, and the display system to provide smooth
continuous scrolling of all enabled plugin content.

Each plugin takes part in one of three ways (its Vegas participation, see
BasePlugin.get_vegas_participation):
- 'scroll': its content scrolls by within the stream
- 'pause': the scroll pauses, the plugin displays for its duration, then
  the scroll resumes
- 'exclude': left out
"""

import logging
import math
import sys
import time
import threading
from typing import Optional, Dict, Any, FrozenSet, List, Callable, TYPE_CHECKING

from src import display_watchdog
from src.common import render_gate
from src.plugin_system.base_plugin import finite_seconds
from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.elements import LiveEpochs
from src.vegas_mode.plugin_adapter import PluginAdapter
from src.vegas_mode.stream_manager import StreamManager
from src.vegas_mode.render_pipeline import RenderPipeline

if TYPE_CHECKING:
    from src.plugin_system.plugin_manager import PluginManager
    from src.plugin_system.base_plugin import BasePlugin
    from src.display_manager import DisplayManager

logger = logging.getLogger(__name__)


#: Degradation threshold, as a fraction of target_fps. A marquee jitters a
#: little all the time, so "anything under target" would report constantly and
#: mean nothing; 90% of target is the point where a shortfall is real. At a
#: 60fps target that is 54fps -- 55fps is a normal wobble and stays at DEBUG,
#: which is deliberate, not an off-by-one.
_FPS_HEALTHY_FRACTION = 0.9

#: A healthy marquee still reports this often, so silence means stopped
#: rather than fine.
_FPS_HEARTBEAT_INTERVAL = 300.0

#: Seconds between live-priority scans while scrolling. The scan asks every
#: plugin mode has_live_priority() / has_live_content(): 139us per call on a
#: Pi 4 with two scoreboards (9 modes), 1.7% of a 125fps frame, growing with
#: every plugin. Game state doesn't change within a quarter second.
_LIVE_PRIORITY_CHECK_INTERVAL = 0.25

#: Seconds a static pause shows a plugin whose display duration can't be
#: used, as long as the rotation shows it: 30 when get_display_duration()
#: raises or answers something that is not a number
#: (DisplayController._get_display_duration), 15 when it answers a number at
#: or below zero (DisplayController._resolve_durations).
_UNREADABLE_DURATION = 30.0
_NOT_POSITIVE_DURATION = 15.0


def _percentile(ordered: List[float], fraction: float) -> float:
    """Nearest-rank percentile of an already-sorted list.

    Index ceil(n * fraction) - 1, so 100 samples at 0.99 give the 99th-ranked
    value. The obvious int(n * fraction) is off by one and, at exactly 100
    samples, lands on the maximum -- which is the number already reported
    alongside this one as the worst frame, so the two columns would agree
    precisely when the sample was smallest.
    """
    if not ordered:
        return 0.0
    index = math.ceil(len(ordered) * fraction) - 1
    return ordered[min(len(ordered) - 1, max(0, index))]


class VegasModeCoordinator:
    """
    Orchestrates Vegas scroll mode operation.

    Responsibilities:
    - Initialize and coordinate all Vegas mode components
    - Manage the high-FPS render loop
    - Handle live priority interruptions
    - Process config updates
    - Provide status and control interface
    """

    #: How long a STATIC pause waits for the plugin's lock (held while its
    #: update() runs) before skipping that turn.
    STATIC_LOCK_TIMEOUT = 1.0

    # Class-level so coordinators built without __init__ (tests) have it.
    _last_live_check: float = float('-inf')
    #: Whether live elements are on for this run (see _apply_live_state).
    live_active: bool = False
    _live_reason: Optional[str] = None
    # Set only while Vegas has changed the GIL switch interval; read with getattr.
    _saved_switch_interval: Optional[float]
    #: Plugins already warned about a display duration the pause can't use,
    #: so a bad setting logs once, not at every turn. Replaced, not mutated.
    _duration_warned: FrozenSet[str] = frozenset()

    def __init__(
        self,
        config: Dict[str, Any],
        display_manager: 'DisplayManager',
        plugin_manager: 'PluginManager'
    ):
        """
        Initialize the Vegas mode coordinator.

        Args:
            config: Main configuration dictionary
            display_manager: DisplayManager instance
            plugin_manager: PluginManager instance
        """
        # Parse configuration
        self.vegas_config = VegasModeConfig.from_config(config)

        # Store references
        self.display_manager = display_manager
        self.plugin_manager = plugin_manager

        # Initialize components
        self.plugin_adapter = PluginAdapter(
            display_manager, self.vegas_config, plugin_manager=plugin_manager)
        self.stream_manager = StreamManager(
            self.vegas_config,
            plugin_manager,
            self.plugin_adapter
        )
        self.render_pipeline = RenderPipeline(
            self.vegas_config,
            display_manager,
            self.stream_manager
        )

        # Live elements: one data epoch per plugin, shared with the adapter,
        # which stamps every element it draws with it. Moved on by the plugin
        # manager's update listener while Vegas runs. See _apply_live_state.
        self.live_epochs = LiveEpochs()
        self.plugin_adapter.live_epochs = self.live_epochs

        # State management
        self._is_active = False
        self._is_paused = False
        self._should_stop = False
        # Frame-rate health, tracked across run_iteration() calls so the
        # heartbeat is one-per-interval rather than one-per-cycle, and so a
        # recovery spanning two cycles is still reported. Reset on start().
        self._fps_last_health_log = 0.0
        self._fps_was_degraded = False
        self._state_lock = threading.Lock()

        # Live priority tracking
        self._live_priority_active = False
        self._live_priority_check: Optional[Callable[[], Optional[str]]] = None

        # Interrupt checker for yielding control back to display controller
        self._interrupt_check: Optional[Callable[[], bool]] = None
        self._interrupt_check_interval: int = 10  # Check every N frames
        # Checked every frame; True runs the interrupt check at once.
        self._interrupt_urgent: Optional[Callable[[], bool]] = None

        # Plugin update callback — fired from a background thread inside the loop
        # so the main loop's _tick_plugin_updates() finds nothing due when Vegas
        # returns, eliminating the inter-iteration frozen-frame gap.
        self._update_callback: Optional[Callable[[], None]] = None
        self._update_tick_running: bool = False

        # Config update tracking
        self._config_version = 0
        self._pending_config_update = False
        self._pending_config: Optional[Dict[str, Any]] = None

        # Static pause handling
        self._static_pause_active = False
        self._saved_scroll_position: Optional[int] = None

        # Statistics
        self.stats = {
            'total_runtime_seconds': 0.0,
            'cycles_completed': 0,
            'interruptions': 0,
            'config_updates': 0,
            'static_pauses': 0,
        }
        self._start_time: Optional[float] = None

        logger.info(
            "VegasModeCoordinator initialized: enabled=%s, fps=%d, buffer_ahead=%d",
            self.vegas_config.enabled,
            self.vegas_config.target_fps,
            self.vegas_config.buffer_ahead
        )

    @property
    def is_enabled(self) -> bool:
        """Check if Vegas mode is enabled in configuration."""
        return self.vegas_config.enabled

    @property
    def is_active(self) -> bool:
        """Check if Vegas mode is currently running."""
        return self._is_active

    def set_sync_manager(self, sync_manager, follower_position: str = "left") -> None:
        """
        Attach a DisplaySyncManager so Vegas mode sends the follower's portion
        of the ticker to the second display on every rendered frame.

        Args:
            sync_manager:       DisplaySyncManager instance, or None to disable sync
            follower_position:  "left" (default) or "right" — physical position of
                                the follower display relative to the leader
        """
        if self.render_pipeline:
            # Don't expose a standalone (no-op) manager to the pipeline — treat it as None
            if sync_manager is not None and hasattr(sync_manager, 'role'):
                from src.common.sync_manager import SyncRole
                if sync_manager.role == SyncRole.STANDALONE:
                    sync_manager = None
            self.render_pipeline.sync_manager = sync_manager
            self.render_pipeline.sync_follower_left = (follower_position == "left")

    def set_live_priority_checker(self, checker: Callable[[], Optional[str]]) -> None:
        """
        Set the callback for checking live priority content.

        Args:
            checker: Callable that returns live priority mode name or None
        """
        self._live_priority_check = checker

    def set_interrupt_checker(
        self,
        checker: Callable[[], bool],
        check_interval: int = 10,
        urgent: Optional[Callable[[], bool]] = None,
    ) -> None:
        """
        Set the callback for checking if Vegas should yield control.

        This allows the display controller to interrupt Vegas mode
        when on-demand, wifi status, or other priority events occur.

        Args:
            checker: Callable that returns True if Vegas should yield
            check_interval: Check every N frames (default 10)
            urgent: A cheap per-frame test; when it is True the checker
                runs at this frame instead of waiting for the interval (the
                display controller passes "a control socket command is
                queued", so a command waits one frame, not ten)
        """
        self._interrupt_check = checker
        self._interrupt_check_interval = max(1, check_interval)
        self._interrupt_urgent = urgent

    def _interrupt_is_urgent(self) -> bool:
        """The per-frame test set with ``urgent``; never raises."""
        urgent = getattr(self, '_interrupt_urgent', None)
        if urgent is None:
            return False
        try:
            return bool(urgent())  # pylint: disable=not-callable
        except Exception:  # pylint: disable=broad-except
            logger.debug("Urgent interrupt test failed", exc_info=True)
            return False

    def set_update_callback(self, callback: Callable[[], None]) -> None:
        """
        Set a callback for running plugin updates from inside the Vegas loop.

        Fired in a daemon background thread every ~4 s so plugin data stays
        fresh without blocking the render loop.  The main loop's
        _tick_plugin_updates() then finds all intervals already satisfied and
        returns immediately, collapsing the inter-iteration gap to <1 ms.

        Args:
            callback: Callable with no arguments. The display controller
                passes _tick_plugin_updates_for_vegas, which also reports the
                plugins that got fresh data through mark_plugin_updated().
        """
        self._update_callback = callback

    def start(self) -> bool:
        """
        Start Vegas mode operation.

        Returns:
            True if started successfully
        """
        if not self.vegas_config.enabled:
            logger.warning("Cannot start Vegas mode - not enabled in config")
            return False

        with self._state_lock:
            if self._is_active:
                logger.warning("Vegas mode already active")
                return True

            # Validate configuration
            errors = self.vegas_config.validate()
            if errors:
                logger.error("Vegas config validation failed: %s", errors)
                return False

            # Initialize stream manager
            if not self.stream_manager.initialize():
                logger.error("Failed to initialize stream manager")
                return False

            # Compose initial content
            if not self.render_pipeline.compose_scroll_content():
                logger.error("Failed to compose initial scroll content")
                return False

            self._is_active = True
            self._should_stop = False
            # A pause belongs to the run it happened in; carrying it into a
            # new run would have run_frame() refuse every frame.
            self._is_paused = False
            self._live_priority_active = False
            self._start_time = time.time()
            # A fresh run starts with a clean health slate: no stale
            # "was degraded" from the previous run, and a heartbeat that is
            # due immediately so the first sample confirms the marquee is up.
            self._fps_last_health_log = 0.0
            self._fps_was_degraded = False
            self._apply_switch_interval()
            self._install_render_gate()
            # Before the first background fetch below, which is the first
            # that may ask a plugin for live elements.
            self._apply_live_state()

        # Line up the next group immediately, so the first extension is already
        # warm rather than stalling the scroll to fetch it.
        if self.vegas_config.continuous_scroll:
            self.render_pipeline.start_prefetch()

        logger.info("Vegas mode started")
        return True

    def stop(self) -> None:
        """Stop Vegas mode operation."""
        with self._state_lock:
            if not self._is_active:
                return

            self._should_stop = True
            self._is_active = False
            self._is_paused = False
            self._live_priority_active = False

            if self._start_time:
                self.stats['total_runtime_seconds'] += time.time() - self._start_time
                self._start_time = None

        self._restore_switch_interval()
        self._remove_render_gate()
        self._set_live(False, None)

        # Cleanup components
        self.render_pipeline.reset()
        self.stream_manager.reset()
        self.display_manager.set_scrolling_state(False)

        logger.info("Vegas mode stopped")

    def _apply_switch_interval(self) -> None:
        """Shorten the GIL switch interval for the run; see VegasModeConfig."""
        ms = self.vegas_config.switch_interval_ms
        if not ms or ms <= 0:
            return
        if getattr(self, '_saved_switch_interval', None) is None:
            self._saved_switch_interval = sys.getswitchinterval()
        sys.setswitchinterval(ms / 1000.0)
        logger.info("Vegas: GIL switch interval %.1fms (was %.1fms)",
                    ms, self._saved_switch_interval * 1000.0)  # type: ignore[operator]  # set just above; getattr hides it

    def _restore_switch_interval(self) -> None:
        saved = getattr(self, '_saved_switch_interval', None)
        if saved is not None:
            sys.setswitchinterval(saved)
            self._saved_switch_interval = None

    # -- live elements ------------------------------------------------------

    def _live_blocker(self) -> Optional[str]:
        """Why live elements must stay off for this run, or None if they may run."""
        cfg = self.vegas_config
        if not getattr(cfg, 'live_refresh', False):
            return "switched off (vegas_scroll.live_refresh)"
        if getattr(self.render_pipeline, 'sync_manager', None) is not None:
            # The follower mirrors whole strips only; a patch would not reach it.
            return "multi-display sync is configured"
        if not cfg.continuous_scroll:
            return "swap mode (vegas_scroll.continuous_scroll is off)"
        if not cfg.offscreen_prefetch:
            return "vegas_scroll.offscreen_prefetch is off"
        if not hasattr(self.display_manager, 'offscreen'):
            return "the display manager has no off-screen canvas"
        return None

    def _apply_live_state(self) -> None:
        """Switch live elements on or off for this run, as the config allows."""
        blocker = self._live_blocker()
        self._set_live(blocker is None, blocker)

    def _set_live(self, active: bool, reason: Optional[str]) -> None:
        was, self.live_active = self.live_active, active
        # getattr: tests build coordinators without every component.
        adapter = getattr(self, 'plugin_adapter', None)
        if adapter is not None:
            adapter.live_elements_enabled = active
        pipeline = getattr(self, 'render_pipeline', None)
        if pipeline is not None and hasattr(pipeline, 'set_live'):
            pipeline.set_live(active)
        plugin_manager = getattr(self, 'plugin_manager', None)
        add = getattr(plugin_manager, 'add_update_listener', None)
        remove = getattr(plugin_manager, 'remove_update_listener', None)
        if active and callable(add):
            add(self._on_plugin_data_changed)
        elif not active and callable(remove):
            remove(self._on_plugin_data_changed)
        if active != was or (reason is not None and reason != self._live_reason):
            if active:
                logger.info("Vegas live elements on")
            elif reason is not None:
                logger.info("Vegas live elements off: %s", reason)
        self._live_reason = reason

    def _on_plugin_data_changed(self, plugin_id: str) -> None:
        """Update listener: a plugin's data may have changed.

        Runs on the update worker with the plugin's lock held, so it only
        moves the plugin's epoch on and wakes the live-element worker, which
        redraws once the lock is free.
        """
        self.live_epochs.bump(plugin_id)
        self.render_pipeline.notify_live_data(plugin_id)

    def _install_render_gate(self) -> None:
        """Gate the prefetch thread on the render thread's swaps; see VegasModeConfig."""
        if not self.vegas_config.prefetch_gate:
            return
        if getattr(self.display_manager, 'render_gate', None) is not None:
            return
        releases = render_gate.swap_releases_gil()
        if releases is None:
            logger.debug("Vegas: no prefetch gate -- no hardware binding loaded")
            return
        if not releases:
            # On by default, so this is every stock install: say so once per
            # run, not as a warning.
            logger.info("Vegas: no prefetch gate -- this rgbmatrix binding keeps "
                        "the GIL in SwapOnVSync (scripts/build_rgbmatrix_nogil.sh)")
            return
        gate = render_gate.RenderGate()
        # Locks the render thread takes too: never park the prefetch holding one.
        gate.guard(self._state_lock,
                   getattr(self.stream_manager, '_buffer_lock', None),
                   getattr(self.render_pipeline, '_buffer_lock', None),
                   getattr(self.render_pipeline, '_prefetch_lock', None),
                   getattr(self.plugin_adapter, '_cache_lock', None))
        self.display_manager.render_gate = gate
        logger.info("Vegas: prefetch gated on vsync")

    def _remove_render_gate(self) -> None:
        gate = getattr(self.display_manager, 'render_gate', None)
        if gate is None:
            return
        self.display_manager.render_gate = None
        logger.info("Vegas: prefetch gate parked the prefetch %d times, %.1fs in all",
                    gate.parks, gate.parked_seconds)

    def pause(self) -> None:
        """Pause Vegas mode (for live priority interruption)."""
        with self._state_lock:
            if not self._is_active:
                return
            self._is_paused = True
            self.stats['interruptions'] += 1

        self.display_manager.set_scrolling_state(False)
        logger.info("Vegas mode paused")

    def resume(self) -> None:
        """Resume Vegas mode after pause."""
        with self._state_lock:
            if not self._is_active:
                return
            self._is_paused = False

        self.display_manager.set_scrolling_state(True)
        logger.info("Vegas mode resumed")

    def run_frame(self) -> bool:
        """
        Run a single frame of Vegas mode.

        Should be called at target FPS (e.g., 125 FPS = every 8ms).

        Returns:
            True if frame was rendered, False if Vegas mode is not active
        """
        # Check if we should be running
        with self._state_lock:
            if not self._is_active or self._is_paused or self._should_stop:
                return False
            # Check for config updates (synchronized access)
            has_pending_update = self._pending_config_update

        # Check for live priority (throttled; see _LIVE_PRIORITY_CHECK_INTERVAL).
        # Only a negative result is ever reused: a positive one pauses Vegas,
        # and run_frame() returns early while paused.
        now = time.monotonic()
        if now - self._last_live_check >= _LIVE_PRIORITY_CHECK_INTERVAL:
            self._last_live_check = now
            if self._check_live_priority():
                return False

        # Apply pending config update outside lock
        if has_pending_update:
            self._apply_pending_config()

        if self.vegas_config.continuous_scroll:
            # Drop cached content for plugins whose data just changed, so the
            # next time each comes round it is composed from current data. The
            # swap path's hot_swap_content() does this via process_updates(),
            # but it also rebuilds and repositions the whole strip, which is
            # the freeze-and-jump this mode exists to avoid. Without this the
            # pending-update flags are never consumed and a segment keeps
            # rendering whatever it was first built from — last night's live
            # game still shown as live the next morning.
            self.render_pipeline.refresh_updated_plugins()

            # Copy any live-element redraws the worker has finished into the
            # strip, between this frame and the last. A deque check when there
            # are none.
            if self.live_active:
                self.render_pipeline.apply_live_patches()

            # Extend the strip before the scroll can reach its end, so the next
            # group arrives from the right and motion never stops. No cycle
            # boundary, so no freeze, no substitution and no restart with the
            # viewport already full.
            # Trickle in the plugins that can only be fetched here, one per
            # frame, before considering a further extension.
            if self.render_pipeline.has_deferred():
                self.render_pipeline.drain_deferred()
            elif self.render_pipeline.needs_extension():
                if self.render_pipeline.extend_scroll_content():
                    self.stats['cycles_completed'] += 1
                elif self.render_pipeline.is_cycle_complete():
                    # Extension failed and the strip has run out: fall back to
                    # the swap rather than sitting on a dead frame.
                    self.render_pipeline.start_new_cycle()
        else:
            # Check if we need to start a new cycle
            if self.render_pipeline.is_cycle_complete():
                if not self.render_pipeline.start_new_cycle():
                    logger.warning("Failed to start new Vegas cycle")
                    return False
                self.stats['cycles_completed'] += 1

            # Check for hot-swap opportunities
            if self.render_pipeline.should_recompose():
                self.render_pipeline.hot_swap_content()

        # Render frame
        return self.render_pipeline.render_frame()

    def run_iteration(self) -> bool:
        """
        Run a complete Vegas mode iteration (display duration).

        This is called by DisplayController to run Vegas mode for one
        "display duration" period before checking for mode changes.

        Handles three display modes:
        - SCROLL/FIXED_SEGMENT: Continue normal scroll rendering
        - STATIC: Pause scroll, display plugin, resume on completion

        Returns:
            True if iteration completed normally, False if interrupted
        """
        if not self.is_active:
            if not self.start():
                return False

        # A live-priority pause is only ever lifted by _check_live_priority(),
        # and run_frame() returns before reaching it while paused -- so once
        # paused, every later iteration returned False at its first frame and
        # the ticker never came back until a restart. The display controller
        # only calls run_iteration() when nothing preempts Vegas (no live mode,
        # or live content is kept in the ticker), so being called at all means
        # the live content that paused us has ended.
        with self._state_lock:
            paused_for_live = self._is_paused and self._live_priority_active
        if paused_for_live:
            self._live_priority_active = False
            self.resume()
            logger.info("Live priority ended - resuming Vegas")

        if self.vegas_config.continuous_scroll:
            # The strip is continuously extended and trimmed, so its width says
            # nothing about how long to run. This is only how often control
            # returns to the display controller; interrupts are still checked
            # every few frames, so it costs nothing to make it a fixed period.
            duration = float(self.vegas_config.max_cycle_duration)
        else:
            duration = self.render_pipeline.get_dynamic_duration()
        # Monotonic for the same reason as the per-frame clock below: this
        # bounds how long the iteration runs, and an NTP step on an RTC-less
        # Pi would otherwise end it at once (forward) or stretch it by the
        # size of the correction (backward).
        start_time = time.monotonic()
        frame_count = 0
        fps_log_interval = 5.0  # Sample FPS every 5 seconds
        # Health state lives on the coordinator, not here: run_iteration() is
        # called once per cycle, so locals reset every few seconds. That made
        # `last_fps_health_log = 0.0` fire the "heartbeat" on the first sample
        # of every iteration rather than once per interval, and a recovery
        # that crossed an iteration boundary was never reported at all --
        # was_degraded had already gone back to False.
        # Monotonic. Never mix it with a wall-clock value: every delta would
        # be hugely negative and silence the frame-rate reporting altogether.
        last_fps_log_time = time.monotonic()
        fps_frame_count = 0
        # A mean hides stutter completely. At 120fps a five-second window is
        # ~600 frames, so a 200ms freeze -- plainly visible on a marquee --
        # moves the average from 120.0 to 115.4 and reads as healthy. What a
        # viewer actually notices is the worst frame, so track that too.
        frame_worst = 0.0
        frame_times: List[float] = []

        logger.info("Starting Vegas iteration for %.1fs", duration)

        while True:
            # Monotonic, like the FPS window below. These devices have no RTC,
            # so the wall clock jumps by however wrong boot time was the moment
            # NTP first syncs. A backward jump makes frame_elapsed negative,
            # and `frame_interval - frame_elapsed` then sleeps for longer than
            # the whole budget -- the render loop stalls for the size of the
            # correction. A forward jump inflates p99 and worst-frame instead.
            frame_started = time.monotonic()
            # An iteration runs for minutes (max_cycle_duration) without
            # returning to the display controller's loop.
            display_watchdog.beat()

            # Check for STATIC mode plugin that should pause scroll
            static_plugin = self._check_static_plugin_trigger()
            if static_plugin:
                if not self._handle_static_pause(static_plugin):
                    # Static pause was interrupted
                    return False
                # The trigger consumed the plugin's marker; carry on scrolling.
                continue

            # Run frame
            if not self.run_frame():
                # Check why we stopped
                with self._state_lock:
                    if self._should_stop:
                        return False
                    if self._is_paused:
                        # Paused for live priority - let caller handle
                        return False

            # Sleep only the remainder of the frame budget. This used to sleep
            # the whole interval on top of however long the frame took, so at a
            # measured 31.6ms per frame a fixed 8ms of that was pure idle — a
            # quarter of the budget spent not rendering. Subtracting the work
            # already done keeps the pacing target while reclaiming that time,
            # and yields the GIL either way so other threads still run.
            # Read every frame: a config change applied mid-iteration can
            # switch between crisp and blended pacing.
            frame_elapsed = time.monotonic() - frame_started
            frame_interval = self.render_pipeline.frame_interval
            time.sleep(max(0.0, frame_interval - frame_elapsed))

            # Measured before the sleep: time spent working, not pacing.
            if frame_elapsed > frame_worst:
                frame_worst = frame_elapsed
            frame_times.append(frame_elapsed)

            # Increment frame count and check for interrupt periodically
            frame_count += 1
            fps_frame_count += 1

            # Periodic FPS logging. Reported at INFO only when the frame rate
            # is actually worth an operator's attention -- a shortfall against
            # target, or the recovery from one -- with a slow heartbeat so a
            # healthy marquee still shows a pulse.
            #
            # Measured over two hours on a running rig: 1410 samples, 98.5%
            # of them within 10% of target. The 1.5% that were not included a
            # reading of 8.6fps against a target of 60 -- a real stall, and
            # completely invisible inside 1389 lines reading "59.6".
            # Monotonic: every use of this value in the block below is a
            # duration, and these devices have no RTC, so the wall clock jumps
            # by however wrong boot time was the moment NTP first syncs. That
            # would not only mis-fire the heartbeat, it would corrupt the
            # frame rate itself, since fps is frames divided by this delta.
            current_time = time.monotonic()
            if current_time - last_fps_log_time >= fps_log_interval:
                fps = fps_frame_count / (current_time - last_fps_log_time)
                p99 = _percentile(sorted(frame_times), 0.99)
                target = self.render_pipeline.target_fps
                degraded = target > 0 and fps < target * _FPS_HEALTHY_FRACTION
                due = (current_time - self._fps_last_health_log
                       >= _FPS_HEARTBEAT_INTERVAL)
                if degraded or self._fps_was_degraded or due:
                    logger.info(
                        "Vegas FPS: %.1f (target: %.0f, frames: %d) p99 %.1fms worst %.1fms",
                        fps, target, fps_frame_count,
                        p99 * 1000.0, frame_worst * 1000.0
                    )
                    gate = getattr(self.display_manager, 'render_gate', None)
                    if gate is not None:
                        logger.info("Vegas: prefetch parked %d times, %.1fs in all",
                                    gate.parks, gate.parked_seconds)
                    self._fps_last_health_log = current_time
                else:
                    logger.debug(
                        "Vegas FPS: %.1f (target: %.0f, frames: %d) p99 %.1fms worst %.1fms",
                        fps, target, fps_frame_count,
                        p99 * 1000.0, frame_worst * 1000.0
                    )
                self._fps_was_degraded = degraded
                last_fps_log_time = current_time
                fps_frame_count = 0
                frame_worst = 0.0
                frame_times.clear()

            if (self._interrupt_check and
                    (frame_count % self._interrupt_check_interval == 0
                     or self._interrupt_is_urgent())):
                try:
                    if self._interrupt_check():
                        logger.debug(
                            "Vegas interrupted by callback after %d frames",
                            frame_count
                        )
                        return False
                except Exception:
                    # Log but don't let interrupt check errors stop Vegas
                    logger.exception("Interrupt check failed")

            # Fire plugin update tick in a background thread every ~4 s.
            # Running it here (rather than only between iterations) means the
            # main loop's _tick_plugin_updates() finds all intervals already
            # satisfied on return, so the inter-iteration gap is <1 ms and the
            # display never shows a frozen frame between iterations.
            # Every 4 s, or every 1 s while the strip holds live elements:
            # plugins are only scheduled on this tick, so its period is added
            # to how late a live update can be.
            tick_seconds = (1.0 if self.live_active
                            and self.render_pipeline.has_live_records() else 4.0)
            _UPDATE_TICK_FRAMES = max(1, int(self.render_pipeline.target_fps * tick_seconds))
            if (self._update_callback and
                    frame_count % _UPDATE_TICK_FRAMES == 0 and
                    not self._update_tick_running):
                self._update_tick_running = True
                def _run_tick(cb=self._update_callback):
                    try:
                        cb()
                    finally:
                        self._update_tick_running = False
                threading.Thread(
                    target=_run_tick, daemon=True, name="vegas-plugin-tick"
                ).start()

            # Check elapsed time
            elapsed = time.monotonic() - start_time
            if elapsed >= duration:
                break

            # NOTE: do NOT break on is_cycle_complete() here.
            # When multi-display sync is active, breaking exits run_iteration()
            # which causes a 2-3s delay before start_new_cycle() is called on
            # the next run_iteration(). During that gap the scroll advances into
            # the pre-roll zone, then start_new_cycle() resets it — producing a
            # second visible jump on the follower display ~2.5s after the first.
            #
            # Instead, run_frame() handles cycle completion directly (it calls
            # start_new_cycle() in the very next frame, 8ms later), collapsing
            # the two events into a single clean transition.
            #
            # Without sync, the iteration now runs to its full duration and may
            # cycle content multiple times within one iteration — acceptable for
            # a continuous ticker.

        logger.info("Vegas iteration completed after %.1fs", time.monotonic() - start_time)
        return True

    def _check_live_priority(self) -> bool:
        """
        Check if live priority content should interrupt Vegas mode.

        Returns:
            True if Vegas mode should be paused for live priority
        """
        if not self._live_priority_check:
            return False

        if self.vegas_config.live_in_ticker:
            # The ticker keeps live content rather than yielding to it; the
            # extra turns are arranged in the rotation itself, so there is
            # nothing to pause for.
            return False

        try:
            live_mode = self._live_priority_check()
            if live_mode:
                if not self._live_priority_active:
                    self._live_priority_active = True
                    self.pause()
                    logger.info("Live priority detected: %s - pausing Vegas", live_mode)
                return True
            else:
                if self._live_priority_active:
                    self._live_priority_active = False
                    self.resume()
                    logger.info("Live priority ended - resuming Vegas")
                return False
        except Exception:
            logger.exception("Error checking live priority")
            return False

    def update_config(self, new_config: Dict[str, Any]) -> None:
        """
        Update Vegas mode configuration.

        Config changes are applied at next safe point to avoid disruption.

        Args:
            new_config: New configuration dictionary
        """
        with self._state_lock:
            self._pending_config_update = True
            self._pending_config = new_config
            self._config_version += 1
            self.stats['config_updates'] += 1

        logger.debug("Config update queued (version %d)", self._config_version)

    def apply_pending_config_if_idle(self) -> None:
        """Apply a queued config update while Vegas isn't running.

        run_frame() applies updates between frames but returns early once
        Vegas has stopped, so without this a disable followed by a re-enable
        would never take effect. Call from the display thread only.
        """
        with self._state_lock:
            if self._is_active or not self._pending_config_update:
                return
        self._apply_pending_config()

    def _apply_pending_config(self) -> None:
        """Apply pending configuration update."""
        # Atomically grab pending config and clear it to avoid losing concurrent updates
        with self._state_lock:
            if self._pending_config is None:
                self._pending_config_update = False
                return
            pending_config = self._pending_config
            self._pending_config = None  # Clear while holding lock

        try:
            new_vegas_config = VegasModeConfig.from_config(pending_config)

            # Check if enabled state changed
            was_enabled = self.vegas_config.enabled
            self.vegas_config = new_vegas_config

            # Update components
            self.render_pipeline.update_config(new_vegas_config)
            self.stream_manager.config = new_vegas_config
            self.plugin_adapter.config = new_vegas_config
            # Cached segments were trimmed under the old settings, so drop them
            # or a changed trim/padding value would not visibly take effect.
            self.plugin_adapter.invalidate_cache()
            if self._is_active:
                self._apply_live_state()

            # Force refresh of stream manager to pick up plugin_order/buffer changes
            self.stream_manager._last_refresh = 0
            self.stream_manager.refresh()

            # Handle enable/disable
            if was_enabled and not new_vegas_config.enabled:
                self.stop()
            elif not was_enabled and new_vegas_config.enabled:
                self.start()

            logger.info("Config update applied (version %d)", self._config_version)

        except Exception:
            logger.exception("Error applying config update")

        finally:
            # Only clear update flag if no new config arrived during processing
            with self._state_lock:
                if self._pending_config is None:
                    self._pending_config_update = False

    def mark_plugin_updated(self, plugin_id: str) -> None:
        """
        Notify that a plugin's data has been updated.

        Args:
            plugin_id: ID of plugin that was updated
        """
        if self._is_active:
            self.stream_manager.mark_plugin_updated(plugin_id)
            self.plugin_adapter.invalidate_cache(plugin_id)

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive Vegas mode status."""
        status = {
            'enabled': self.vegas_config.enabled,
            'active': self._is_active,
            'paused': self._is_paused,
            'live_priority_active': self._live_priority_active,
            'config': self.vegas_config.to_dict(),
            'stats': self.stats.copy(),
        }

        if self._is_active:
            status['render_info'] = self.render_pipeline.get_current_scroll_info()
            status['stream_status'] = self.stream_manager.get_buffer_status()

        return status

    # -------------------------------------------------------------------------
    # Static pause handling (for STATIC display mode)
    # -------------------------------------------------------------------------

    def _check_static_plugin_trigger(self) -> Optional['BasePlugin']:
        """
        Check if a STATIC mode plugin should take over display.

        Called every frame. The render pipeline marks where each STATIC
        plugin's turn falls in the strip, and this reports the one the scroll
        has just reached.

        This used to peek at the front of the stream manager's segment
        buffer, which continuous scrolling (the default) never advances: it
        extends the strip with take_next_group() instead. The same first
        segment was examined on every frame, so a STATIC plugin paused the
        scroll only if it happened to be first, once, at startup -- and
        otherwise just scrolled past as ordinary content. Swap mode fared no
        better: nothing advanced the buffer mid-cycle either.

        Returns:
            Plugin instance if static pause should begin, None otherwise
        """
        plugin_id = self.render_pipeline.next_static_trigger()
        if not plugin_id:
            return None
        plugin: Optional['BasePlugin'] = self.plugin_manager.get_plugin(plugin_id)
        if not plugin:
            logger.debug("[%s] STATIC turn reached, but the plugin is no longer loaded",
                         plugin_id)
            return None
        return plugin

    def _handle_static_pause(self, plugin: 'BasePlugin') -> bool:
        """
        Handle a static pause - scroll pauses while plugin displays.

        Args:
            plugin: The STATIC mode plugin to display

        Returns:
            True if completed normally, False if interrupted
        """
        plugin_id = plugin.plugin_id

        with self._state_lock:
            if self._static_pause_active:
                logger.warning("Static pause already active")
                return True

            # Save current scroll position for smooth resume
            self._saved_scroll_position = self.render_pipeline.get_scroll_position()
            self._static_pause_active = True
            self.stats['static_pauses'] += 1

        logger.info("Static pause started for plugin: %s", plugin_id)

        # Stop scrolling indicator
        self.display_manager.set_scrolling_state(False)

        try:
            # Display the plugin using its standard display() method, under
            # its plugin lock like every other display() call: without it this
            # could draw while the update worker is inside the plugin's
            # update(). If update() holds the lock past the wait, skip this
            # turn rather than stall the marquee.
            get_lock = getattr(self.plugin_manager, 'get_plugin_lock', None)
            plugin_lock = get_lock(plugin_id) if get_lock else None
            if plugin_lock is not None and not plugin_lock.acquire(
                    timeout=self.STATIC_LOCK_TIMEOUT):
                logger.info("Static pause skipped for %s: its update() is still running",
                            plugin_id)
                return True
            try:
                plugin.display(force_clear=True)
            finally:
                if plugin_lock is not None:
                    plugin_lock.release()
            self.display_manager.update_display()

            # Wait for the plugin's display duration. Monotonic, like the
            # iteration clock: an NTP step on an RTC-less Pi would otherwise
            # end the pause at once or stretch it by the correction.
            duration = self._static_pause_duration(plugin)
            start = time.monotonic()

            while time.monotonic() - start < duration:
                # Check for interruptions
                if self._should_stop:
                    logger.info("Static pause interrupted by stop request")
                    return False

                if self._check_live_priority():
                    logger.info("Static pause interrupted by live priority")
                    return False

                # On-demand, a WiFi message, the schedule, follower mode...
                if self._interrupt_check and self._interrupt_check():
                    logger.info("Static pause interrupted by the display controller")
                    return False

                # Sleep in small increments to remain responsive
                time.sleep(0.1)
                display_watchdog.beat()

            logger.info(
                "Static pause completed for %s after %.1fs",
                plugin_id, time.monotonic() - start
            )

        except Exception:
            logger.exception("Error during static pause for %s", plugin_id)
            return False

        finally:
            self._end_static_pause()

        return True

    def _static_pause_duration(self, plugin: 'BasePlugin') -> float:
        """Seconds a static pause shows ``plugin``: its display duration,
        read the way the rotation reads it.

        Several plugins return their display_duration setting straight from
        config.json, so one saved as "20" or null came back as a string or
        None; comparing it with the clock raised, and the pause's broad
        except ended the pause at every one of the plugin's turns. inf
        paused until something interrupted it, and NaN, False, 0 or a
        negative number ended the pause at once. A numeric string counts
        (finite_seconds); anything else, or a raise, gets
        _UNREADABLE_DURATION, and a number at or below zero
        _NOT_POSITIVE_DURATION, logged once per plugin.
        """
        try:
            value = plugin.get_display_duration()
        except Exception as err:  # pylint: disable=broad-except
            problem = f"get_display_duration() raised {type(err).__name__}: {err}"
            fallback = _UNREADABLE_DURATION
        else:
            seconds = finite_seconds(value)
            if seconds is not None and seconds > 0:
                return seconds
            if seconds is None:
                problem = f"display duration {value!r} is not a number"
                fallback = _UNREADABLE_DURATION
            else:
                problem = f"display duration {value!r} is not above zero"
                fallback = _NOT_POSITIVE_DURATION
        plugin_id = plugin.plugin_id
        if plugin_id not in self._duration_warned:
            self._duration_warned = self._duration_warned | {plugin_id}
            logger.warning("[%s] %s; its static pause lasts %.0fs (logged once)",
                           plugin_id, problem, fallback)
        return fallback

    def _end_static_pause(self) -> None:
        """End static pause and restore scroll state."""
        should_resume_scrolling = False

        with self._state_lock:
            # Only resume scrolling if we weren't interrupted
            was_active = self._static_pause_active
            should_resume_scrolling = (
                was_active and
                not self._should_stop and
                not self._live_priority_active
            )

            # Clear pause state
            self._static_pause_active = False

            # Restore scroll position if we're resuming
            if should_resume_scrolling and self._saved_scroll_position is not None:
                self.render_pipeline.set_scroll_position(self._saved_scroll_position)
            self._saved_scroll_position = None

        # Only resume scrolling state if not interrupted
        if should_resume_scrolling:
            self.display_manager.set_scrolling_state(True)
            logger.debug("Static pause ended, scroll resumed")
        else:
            logger.debug("Static pause ended (interrupted, not resuming scroll)")

    def cleanup(self) -> None:
        """Clean up all resources."""
        self.stop()
        self.render_pipeline.cleanup()
        self.stream_manager.cleanup()
        self.plugin_adapter.cleanup()
        logger.info("VegasModeCoordinator cleanup complete")
