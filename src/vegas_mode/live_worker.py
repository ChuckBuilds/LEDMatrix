"""The one background worker behind live Vegas elements.

Vegas draws everything the strip shows off the render thread. Until live
elements that was one short-lived prefetch thread per group; now, once the
strip holds a live element, it is this worker, which does three kinds of job
one at a time, most urgent first:

- **group steps**: fetching the next group of plugins for the strip, one
  plugin per step (what the prefetch thread did in one go);
- **data refreshes**: when a plugin's data has moved on (its epoch, see
  elements.LiveEpochs) past what its elements in the strip were drawn from,
  redraw them and hand over the ones whose pixels changed;
- **ticks**: redraw an element that animates (``refresh_hz``) while it is on
  or near the screen.

Nothing here touches the strip. A finished redraw becomes a
:class:`~src.vegas_mode.elements.LivePatch` in the pipeline's slot for that
element (one per element, the latest wins) and the render thread copies it
into the strip between two frames (RenderPipeline.apply_live_patches). The
hand-over is lock-free: a dict store and a deque append here, a deque popleft
and a dict pop there, so the render thread never waits on this thread.

Every job runs inside the render gate when there is one (src/common/
render_gate.py), so Python runs here only while the render thread is waiting
for the panel. Without it (the stock rgbmatrix binding) animation is capped at
:data:`UNGATED_MAX_HZ`.
"""

from __future__ import annotations

import collections
import logging
import os
import queue
import threading
import time
from contextlib import nullcontext
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from src.vegas_mode.elements import ElementRecord, LivePatch, LiveView

logger = logging.getLogger(__name__)

#: A view older than this means frames have stopped (a paused scroll, an
#: interrupt): only group work runs, since nothing redrawn would be seen.
VIEW_STALE_S = 0.5
#: How long a data refresh waits for the plugin's lock before trying later.
DATA_LOCK_TIMEOUT = 0.25
#: ...and how much later.
LOCK_BACKOFF_S = 1.0
#: Animation ceiling without the render gate, where every redraw competes
#: with the render thread for the GIL.
UNGATED_MAX_HZ = 1.0
#: An element whose redraws take longer than this on average is animated at
#: half its rate, down to MIN_THROTTLED_HZ.
SLOW_RENDER_S = 0.05
MIN_THROTTLED_HZ = 0.5
#: Longest the worker sleeps with nothing due, so a floor or a backoff that
#: expires is noticed.
IDLE_WAIT_S = 0.5
#: How often the worker logs what it did, when it did anything.
SUMMARY_INTERVAL_S = 300.0
#: Weight of the newest sample in the per-element render time average.
EWMA_ALPHA = 0.2
#: How long the worker waits for a one-shot prefetch thread it takes over from.
LEGACY_PREFETCH_JOIN_S = 15.0


def _visible(record: ElementRecord, view: LiveView) -> bool:
    return record.abs_x < view.abs_right and record.abs_x + record.width > view.abs_left


def _behind(record: ElementRecord, view: LiveView) -> bool:
    return record.abs_x + record.width <= view.abs_left


class _GroupJob:
    """A group fetch in progress, one member per step."""

    def __init__(self, generation: int, plugin_ids: List[str]) -> None:
        self.generation = generation
        self.pending = list(plugin_ids)
        self.group: List[Tuple[str, Any]] = []


class VegasWorker(threading.Thread):
    """See the module docstring. Owned by the RenderPipeline that starts it."""

    def __init__(self, pipeline: Any, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(daemon=True, name="vegas-live-worker")
        self.pipeline = pipeline
        #: time.monotonic, or a fake one in tests; the pipeline's view is
        #: stamped with time.monotonic too.
        self._clock = clock
        self.inbox: "queue.SimpleQueue[Tuple[str, Any]]" = queue.SimpleQueue()
        self._stopping = False
        self._group_wanted = False
        self._group_job: Optional[_GroupJob] = None
        self._strip_gen = pipeline._strip_gen
        # Per element (record seq): the epoch this worker last handed over,
        # or found needed nothing; the digest of its latest hand-over; when
        # its next animation tick is due.
        self._handled_epoch: Dict[int, int] = {}
        self._handed_digest: Dict[int, Any] = {}
        self._next_tick: Dict[int, float] = {}
        # Per plugin: when its last data refresh ran, and a lock backoff.
        self._last_data_job: Dict[str, float] = {}
        self._backoff_until: Dict[str, float] = {}
        # Per (plugin, key): average redraw time, for throttling.
        self._render_ewma: Dict[Tuple[str, str], float] = {}
        self._refused: Set[Tuple[str, str, int]] = set()
        self.stats: collections.Counter = collections.Counter()
        self.busy_seconds = 0.0
        self._began = clock()
        self._last_summary = self._began
        self._jobs_since_prune = 0
        # The slowest redraw since the last summary: (seconds, (plugin, key)).
        self._slowest_redraw: Optional[Tuple[float, Tuple[str, str]]] = None

    # -- control, from other threads ------------------------------------------

    def request_group(self) -> None:
        """Fetch the next group for the strip when nothing more urgent is due."""
        self._group_wanted = True
        self.inbox.put(("group", None))

    def notify_data(self, plugin_id: str) -> None:
        """A plugin's data moved on. Only a wake-up: its epoch is the truth."""
        self.inbox.put(("data", plugin_id))

    def stop(self) -> None:
        """Stop after the current job. Does not wait for it."""
        self._stopping = True
        self.inbox.put(("stop", None))

    # -- the loop -------------------------------------------------------------

    def run(self) -> None:
        try:
            # Linux applies nice per thread: deprioritise against the render
            # loop, as the one-shot prefetch thread always did.
            os.nice(10)
        except (OSError, AttributeError):
            pass
        self._join_legacy_prefetch()
        while not self._should_stop():
            self._wait(self._next_wait(self._clock()))
            if self._should_stop():
                break
            job = self._pick(self._clock())
            if job is not None:
                self._run(job)
            self._maybe_summarise()
        self._hand_over_partial_group()
        logger.debug("Vegas live worker stopped")

    def _should_stop(self) -> bool:
        # A method, not a bare attribute read: stop() sets it from another
        # thread between two reads in run().
        return self._stopping

    def _join_legacy_prefetch(self) -> None:
        """Let a one-shot prefetch thread, or a worker stopped earlier, finish first.

        So that only one thread ever draws for the strip: measured on hdpi,
        each extra thread competing for the GIL made the render thread late
        more often, not less (src/common/render_gate.py).
        """
        for name in ('_prefetch_thread', '_retired_worker'):
            thread = getattr(self.pipeline, name, None)
            if thread is not None and thread is not self \
                    and thread is not threading.current_thread() and thread.is_alive():
                thread.join(LEGACY_PREFETCH_JOIN_S)

    def _wait(self, timeout: float) -> None:
        try:
            message = self.inbox.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return
        while True:
            if message[0] == "stop":
                self._stopping = True
            elif message[0] == "group":
                self._group_wanted = True
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                return

    def _next_wait(self, now: float) -> float:
        """Seconds until something may be due: the next tick, else IDLE_WAIT_S."""
        p = self.pipeline
        if self._group_job is not None or (
                self._group_wanted and p._prepared_group is None):
            return 0.0
        # Ticks only count while frames are flowing: during a pause _pick runs
        # none, and a past-due tick would otherwise make this 0 and spin.
        view = p._view
        if view is None or now - view.t_mono > VIEW_STALE_S:
            return IDLE_WAIT_S
        # Only elements _due_tick would run. A tick left behind by an element
        # trimmed away, or one no longer animated, is never run, and counting
        # it held this at its floor: a spin at 100 wake-ups a second.
        soonest = now + IDLE_WAIT_S
        lead = self._tick_lead()
        for record in p._elements:
            if self._tickable(record, view, lead):
                soonest = min(soonest, self._next_tick.get(record.seq, now))
        return max(0.01, soonest - now)

    # -- choosing ---------------------------------------------------------------

    def _pick(self, now: float) -> Optional[Tuple[str, Any]]:
        """The most urgent job, or None. See the module docstring for the order."""
        p = self.pipeline
        if p._strip_gen != self._strip_gen:
            self._forget_everything(p._strip_gen)
        view = p._view
        fresh = view is not None and now - view.t_mono <= VIEW_STALE_S
        group_ready = self._group_job is not None or (
            self._group_wanted and p._prepared_group is None)
        records = p._elements

        if group_ready and fresh and self._group_urgent(view):
            return ("group", None)
        if fresh and records:
            plugin_id = self._due_data(records, view, now, visible_only=True)
            if plugin_id is not None:
                return ("data", plugin_id)
            record = self._due_tick(records, view, now)
            if record is not None:
                return ("tick", record)
        if group_ready:
            return ("group", None)
        if fresh and records:
            plugin_id = self._due_data(records, view, now, visible_only=False)
            if plugin_id is not None:
                return ("data", plugin_id)
        return None

    def _group_urgent(self, view: LiveView) -> bool:
        width = self.pipeline.display_width
        threshold = (self.pipeline.config.extend_threshold_screens + 1.0) * width
        return bool(view.abs_end - view.abs_right <= threshold)

    def _epoch(self, plugin_id: str) -> int:
        epochs = getattr(self.pipeline.stream_manager.plugin_adapter, 'live_epochs', None)
        return int(epochs.get(plugin_id)) if epochs is not None else 0

    def _done_epoch(self, record: ElementRecord) -> int:
        applied = self.pipeline._applied.get(record.seq)
        return max(applied[0] if applied is not None else record.epoch,
                   self._handled_epoch.get(record.seq, -1))

    def _due_data(self, records: Tuple[ElementRecord, ...], view: LiveView,
                  now: float, visible_only: bool) -> Optional[str]:
        """The plugin whose stale elements are nearest the screen, if any may redraw."""
        floor = self.pipeline.config.live_min_interval
        best: Optional[Tuple[int, str]] = None
        for record in records:
            if _behind(record, view):
                continue
            if visible_only and not _visible(record, view):
                continue
            plugin_id = record.plugin_id
            if self._epoch(plugin_id) <= self._done_epoch(record):
                continue
            if now < self._backoff_until.get(plugin_id, 0.0):
                continue
            if now - self._last_data_job.get(plugin_id, float('-inf')) < floor:
                continue
            distance = max(0, record.abs_x - view.abs_right)
            if best is None or distance < best[0]:
                best = (distance, plugin_id)
        return best[1] if best is not None else None

    def _tick_hz(self, record: ElementRecord) -> float:
        cfg = self.pipeline.config
        hz = min(float(record.refresh_hz), float(cfg.live_max_hz))
        if getattr(self.pipeline.display_manager, 'render_gate', None) is None:
            hz = min(hz, UNGATED_MAX_HZ)
        ewma = self._render_ewma.get((record.plugin_id, record.key), 0.0)
        if hz > 0 and ewma > SLOW_RENDER_S:
            # Halved, but never below the floor -- nor raised to it, for an
            # element already asking for less.
            hz = min(hz, max(MIN_THROTTLED_HZ, hz / 2.0))
        return hz

    def _tick_lead(self) -> float:
        return float(self.pipeline.config.live_lead_screens * self.pipeline.display_width)

    def _tickable(self, record: ElementRecord, view: LiveView, lead: float) -> bool:
        """Whether an element animates now: it has a rate, and is on or near the screen."""
        return (record.refresh_hz > 0 and self._tick_hz(record) > 0
                and not _behind(record, view) and record.abs_x < view.abs_right + lead)

    def _due_tick(self, records: Tuple[ElementRecord, ...], view: LiveView,
                  now: float) -> Optional[ElementRecord]:
        lead = self._tick_lead()
        best: Optional[ElementRecord] = None
        best_due = 0.0
        for record in records:
            if not self._tickable(record, view, lead):
                self._next_tick.pop(record.seq, None)
                continue
            due = self._next_tick.get(record.seq, now)
            if due <= now and (best is None or due < best_due):
                best, best_due = record, due
        return best

    # -- running ----------------------------------------------------------------

    def _run(self, job: Tuple[str, Any]) -> None:
        kind, arg = job
        gate = getattr(self.pipeline.display_manager, 'render_gate', None)
        started = self._clock()
        try:
            with gate.yielding() if gate is not None else nullcontext():
                if kind == "group":
                    self._group_step()
                elif kind == "data":
                    self._data_job(arg, started)
                else:
                    self._tick_job(arg, started)
            self.stats[kind] += 1
        except Exception as exc:  # pylint: disable=broad-except
            # Plugin code runs in here; one bad job must not end the worker,
            # which also fetches every group for the strip.
            self.stats["errors"] += 1
            if self.stats["errors"] <= 3 or self.stats["errors"] % 100 == 0:
                logger.exception("Vegas live worker: %s job failed (%s)", kind, exc)
        finally:
            self.busy_seconds += self._clock() - started
        self._jobs_since_prune += 1
        if self._jobs_since_prune >= 64:
            self._prune()

    def _hand_over_partial_group(self) -> None:
        """On stopping: publish the members of a group already fetched.

        Its plugins were taken from the rotation when it was planned, so a
        group dropped here would skip them until the next cycle. The rest of
        it is not fetched. Nothing is published over a group already waiting,
        or into a Vegas reset since.
        """
        job, self._group_job = self._group_job, None
        if job is None or not job.group:
            return
        p = self.pipeline
        with p._prefetch_lock:
            if job.generation == p._prefetch_generation and p._prepared_group is None:
                p._prepared_group = job.group

    def _group_step(self) -> None:
        p = self.pipeline
        job = self._group_job
        if job is None:
            with p._prefetch_lock:
                if p._prepared_group is not None:
                    self._group_wanted = False
                    return
                generation = p._prefetch_generation
            self._group_wanted = False
            job = self._group_job = _GroupJob(generation, p.stream_manager.plan_next_group())
        if job.generation != p._prefetch_generation:
            self._group_job = None      # Vegas was reset meanwhile
            return
        if job.pending:
            member = p.stream_manager.fetch_group_member(
                job.pending.pop(0), offscreen_only=True)
            if member is not None:
                job.group.append(member)
        if not job.pending:
            self._group_job = None
            with p._prefetch_lock:
                if job.generation == p._prefetch_generation:
                    p._prepared_group = job.group

    def _data_job(self, plugin_id: str, now: float) -> None:
        p = self.pipeline
        self._last_data_job[plugin_id] = now
        plugin = getattr(p.stream_manager.plugin_manager, 'plugins', {}).get(plugin_id)
        if plugin is None:
            return
        gen = p._strip_gen
        batch = p.stream_manager.plugin_adapter.render_live_elements(
            plugin, plugin_id, lock_timeout=DATA_LOCK_TIMEOUT)
        if batch is None:
            self.stats["lock_busy"] += 1
            self._backoff_until[plugin_id] = now + LOCK_BACKOFF_S
            return
        epoch, rendered = batch
        view = p._view
        for record in p._elements:
            if record.plugin_id != plugin_id:
                continue
            if view is not None and _behind(record, view):
                continue
            element = rendered.get(record.key)
            if element is not None:
                self._hand_over(record, element, epoch, gen)
            # A key the plugin no longer has keeps its last pixels until it
            # scrolls off; either way this epoch is dealt with.
            self._handled_epoch[record.seq] = max(
                epoch, self._handled_epoch.get(record.seq, -1))

    def _tick_job(self, record: ElementRecord, now: float) -> None:
        p = self.pipeline
        hz = self._tick_hz(record)
        self._next_tick[record.seq] = now + (1.0 / hz if hz > 0 else IDLE_WAIT_S)
        plugin = getattr(p.stream_manager.plugin_manager, 'plugins', {}).get(record.plugin_id)
        if plugin is None:
            return
        adapter = p.stream_manager.plugin_adapter
        key = (record.plugin_id, record.key)
        at = now + self._render_ewma.get(key, 0.0) + p.frame_interval
        started = time.perf_counter()
        if adapter.has_lock_free_redraw(plugin):
            # None from the plugin means nothing to redraw this time.
            element = adapter.redraw_live_element(
                plugin, record.plugin_id, record.key, record.width, p.display_height, at)
        else:
            # No lock-free redraw: redraw everything, but never wait for the
            # plugin's lock (update() may be doing network I/O under it).
            batch = adapter.render_live_elements(plugin, record.plugin_id, lock_timeout=0.0)
            element = batch[1].get(record.key) if batch is not None else None
        took = time.perf_counter() - started
        if self._slowest_redraw is None or took > self._slowest_redraw[0]:
            self._slowest_redraw = (took, key)
        previous = self._render_ewma.get(key)
        self._render_ewma[key] = took if previous is None else (
            EWMA_ALPHA * took + (1.0 - EWMA_ALPHA) * previous)
        if element is not None:
            self._hand_over(record, element, element.epoch, p._strip_gen)

    def _hand_over(self, record: ElementRecord, element: Any, epoch: int, gen: int) -> None:
        """Queue a redraw for the render thread, unless nothing would change."""
        p = self.pipeline
        if element.width != record.width:
            marker = (record.plugin_id, record.key, element.width)
            if marker not in self._refused:
                self._refused.add(marker)
                logger.info(
                    "[%s] Live element %r redrawn %dpx wide, placed at %dpx; "
                    "kept as it was (a live element's width must not change)",
                    record.plugin_id, record.key, element.width, record.width)
            self.stats["refused"] += 1
            return
        last = self._handed_digest.get(record.seq)
        if last is None:
            applied = p._applied.get(record.seq)
            last = applied[1] if applied is not None else record.digest
        if element.digest == last:
            self.stats["unchanged"] += 1
            return
        p._live_slots[record.seq] = LivePatch(
            seq=record.seq, strip_gen=gen, epoch=epoch, pixels=element.pixels,
            digest=element.digest, made_at=self._clock())
        p._live_ready.append(record.seq)
        self._handed_digest[record.seq] = element.digest
        self.stats["patches"] += 1

    # -- housekeeping -----------------------------------------------------------

    def _forget_everything(self, gen: int) -> None:
        self._strip_gen = gen
        self._handled_epoch.clear()
        self._handed_digest.clear()
        self._next_tick.clear()

    def _prune(self) -> None:
        self._jobs_since_prune = 0
        live = {record.seq for record in self.pipeline._elements}
        for table in (self._handled_epoch, self._handed_digest, self._next_tick):
            for seq in [s for s in table if s not in live]:
                del table[seq]

    def _maybe_summarise(self) -> None:
        now = self._clock()
        if now - self._last_summary < SUMMARY_INTERVAL_S:
            return
        elapsed = now - self._last_summary
        self._last_summary = now
        stats, self.stats = self.stats, collections.Counter()
        busy, self.busy_seconds = self.busy_seconds, 0.0
        slowest, self._slowest_redraw = self._slowest_redraw, None
        if not stats:
            return
        redraws = ""
        if slowest is not None:
            # What a tick costs is the number that decides whether an
            # animated element can keep its rate: say it for the worst one.
            took, (plugin_id, key) = slowest
            average = self._render_ewma.get((plugin_id, key), took)
            redraws = "; slowest redraw %.1fms (%s %r, average %.1fms)" % (
                took * 1000.0, plugin_id, key, average * 1000.0)
        logger.info(
            "Vegas live: %d group step(s), %d data refresh(es), %d tick(s); "
            "%d patch(es) handed over, %d unchanged, %d refused, %d lock-busy, "
            "%d error(s); worker busy %.1f%%%s",
            stats["group"], stats["data"], stats["tick"], stats["patches"],
            stats["unchanged"], stats["refused"], stats["lock_busy"],
            stats["errors"], 100.0 * busy / elapsed if elapsed else 0.0, redraws)
