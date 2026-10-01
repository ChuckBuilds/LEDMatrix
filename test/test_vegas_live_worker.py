"""The live-element worker's choices and hand-overs (src/vegas_mode/live_worker.py).

The worker is driven here one decision at a time -- _pick() then _run() --
against a fake pipeline, so every rule can be pinned without threads or
timing: what runs first, what is skipped, what reaches the render thread.
"""
import collections
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.vegas_mode import live_worker  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.elements import (  # noqa: E402
    ElementRecord, LiveEpochs, LiveView, RenderedElement, pixel_digest,
)
from src.vegas_mode.live_worker import VegasWorker  # noqa: E402

W, H = 128, 32
NOW = 1000.0


def _pixels(width, value):
    array = np.full((H, width, 3), value, dtype=np.uint8)
    array.setflags(write=False)
    return array


def _element(key, width, value, epoch=1):
    pixels = _pixels(width, value)
    return RenderedElement(key=key, epoch=epoch, version=value, pixels=pixels,
                           digest=pixel_digest(pixels), width=width)


def _record(seq, key, abs_x, width=40, pid="p", epoch=0, value=0, hz=0.0):
    return ElementRecord(seq=seq, plugin_id=pid, key=key, abs_x=abs_x, width=width,
                         epoch=epoch, digest=pixel_digest(_pixels(width, value)),
                         refresh_hz=hz)


class _Adapter:
    def __init__(self):
        self.live_epochs = LiveEpochs()
        self.batches = {}          # pid -> {key: RenderedElement}
        self.redraws = {}          # key -> RenderedElement | None
        self.busy = set()
        self.calls = []

    def render_live_elements(self, plugin, pid, lock_timeout):
        self.calls.append(("render", pid, lock_timeout))
        if pid in self.busy:
            return None
        return self.live_epochs.get(pid), self.batches.get(pid, {})

    def redraw_live_element(self, plugin, pid, key, width, height, at):
        self.calls.append(("redraw", pid, key, width, at))
        return self.redraws.get(key)

    has_redraw = True

    def has_lock_free_redraw(self, plugin):
        return self.has_redraw


class _Stream:
    def __init__(self, adapter):
        self.plugin_adapter = adapter
        self.plugin_manager = SimpleNamespace(plugins={"p": object(), "q": object()})
        self.plans = []
        self.fetched = []

    def plan_next_group(self, count=None):
        self.plans.append(count)
        return ["a", "b", "c"]

    def fetch_group_member(self, pid, offscreen_only=False):
        self.fetched.append((pid, offscreen_only))
        return (pid, [f"img-{pid}"])


def _pipeline(gate=True, **cfg):
    adapter = _Adapter()
    p = SimpleNamespace(
        _strip_gen=1, _view=None, _elements=(), _prepared_group=None,
        _prefetch_lock=threading.Lock(), _prefetch_generation=7, _applied={},
        _live_slots={}, _live_ready=collections.deque(),
        display_width=W, display_height=H, frame_interval=0.01,
        config=VegasModeConfig(**cfg),
        display_manager=SimpleNamespace(render_gate=_Gate() if gate else None),
        stream_manager=_Stream(adapter), _prefetch_thread=None, prepared=[])
    p.prepare_group_member = p.prepared.append
    return p, adapter


class _Gate:
    def __init__(self):
        self.entered = 0

    def yielding(self):
        gate = self

        class _Ctx:
            def __enter__(self):
                gate.entered += 1

            def __exit__(self, *exc):
                return False
        return _Ctx()


def _view(left=1000, end=5000, t=NOW):
    return LiveView(abs_left=left, abs_right=left + W, abs_end=end, t_mono=t)


def _worker(p, records=(), view=None, clock=lambda: NOW):
    p._elements = tuple(records)
    for r in records:
        p._applied[r.seq] = (r.epoch, r.digest)
    p._view = view if view is not None else _view()
    return VegasWorker(p, clock=clock)


# -- choosing -----------------------------------------------------------------


def test_an_urgent_group_goes_before_anything():
    p, adapter = _pipeline()
    visible = _record(1, "k", 1010)
    worker = _worker(p, [visible], _view(left=1000, end=1000 + W + 100))
    adapter.live_epochs.bump("p")
    worker.request_group()
    assert worker._pick(NOW) == ("group", None)


def test_visible_data_then_ticks_then_a_normal_group_then_data_ahead():
    p, adapter = _pipeline()
    visible = _record(1, "vis", 1010)
    animated = _record(2, "map", 1060, hz=4)
    ahead = _record(3, "far", 3000, pid="q")
    worker = _worker(p, [visible, animated, ahead])
    adapter.live_epochs.bump("p")
    adapter.live_epochs.bump("q")
    worker.request_group()
    assert worker._pick(NOW) == ("data", "p")
    worker._handled_epoch[1] = worker._handled_epoch[2] = adapter.live_epochs.get("p")
    assert worker._pick(NOW) == ("tick", animated)
    worker._next_tick[2] = NOW + 10
    assert worker._pick(NOW) == ("group", None)
    worker._group_wanted = False
    assert worker._pick(NOW) == ("data", "q")


def test_nothing_behind_the_viewport_is_redrawn():
    p, adapter = _pipeline()
    behind = _record(1, "gone", 900, width=40)
    worker = _worker(p, [behind])
    adapter.live_epochs.bump("p")
    assert worker._pick(NOW) is None


def test_only_group_work_while_frames_have_stopped():
    p, adapter = _pipeline()
    worker = _worker(p, [_record(1, "k", 1010, hz=4)], _view(t=NOW - 5))
    adapter.live_epochs.bump("p")
    assert worker._pick(NOW) is None
    worker.request_group()
    assert worker._pick(NOW) == ("group", None)


def test_an_element_placed_from_older_data_is_caught_up():
    # A group drawn before an update() and placed after it: its records carry
    # the old epoch, so they are due at once.
    p, adapter = _pipeline()
    adapter.live_epochs.bump("p")
    adapter.live_epochs.bump("p")
    worker = _worker(p, [_record(1, "k", 1010, epoch=1)])
    assert worker._pick(NOW) == ("data", "p")


def test_the_data_floor_defers_but_does_not_drop():
    p, adapter = _pipeline(live_min_interval=2.0)
    worker = _worker(p, [_record(1, "k", 1010)])
    adapter.live_epochs.bump("p")
    worker._last_data_job["p"] = NOW - 1.0
    assert worker._pick(NOW) is None
    p._view = _view(t=NOW + 1.5)
    assert worker._pick(NOW + 1.5) == ("data", "p")


# -- data refreshes ------------------------------------------------------------


def test_a_refresh_hands_over_only_what_changed():
    p, adapter = _pipeline()
    same = _record(1, "same", 1010, value=0)
    changed = _record(2, "changed", 1060, value=0)
    worker = _worker(p, [same, changed])
    epoch = adapter.live_epochs.bump("p")
    adapter.batches["p"] = {"same": _element("same", 40, 0, epoch),
                            "changed": _element("changed", 40, 9, epoch)}
    worker._run(("data", "p"))
    assert list(p._live_ready) == [2]
    patch = p._live_slots[2]
    assert patch.epoch == epoch and patch.strip_gen == 1
    assert worker._handled_epoch == {1: epoch, 2: epoch}
    assert worker._pick(NOW + 100) is None     # nothing left due
    assert adapter.calls[0] == ("render", "p", live_worker.DATA_LOCK_TIMEOUT)


def test_a_redraw_of_another_width_is_refused_and_said_once(caplog):
    p, adapter = _pipeline()
    worker = _worker(p, [_record(1, "k", 1010, width=40)])
    for n in range(3):
        epoch = adapter.live_epochs.bump("p")
        adapter.batches["p"] = {"k": _element("k", 44, n + 1, epoch)}
        with caplog.at_level("INFO"):
            worker._run(("data", "p"))
    assert not p._live_ready
    assert worker.stats["refused"] == 3
    assert sum("must not change" in r.message for r in caplog.records) == 1


def test_a_busy_lock_backs_off_instead_of_waiting():
    p, adapter = _pipeline()
    worker = _worker(p, [_record(1, "k", 1010)])
    adapter.live_epochs.bump("p")
    adapter.busy.add("p")
    worker._run(("data", "p"))
    assert worker.stats["lock_busy"] == 1
    assert worker._backoff_until["p"] == NOW + live_worker.LOCK_BACKOFF_S
    p._view = _view(t=NOW + 2.5)
    assert worker._pick(NOW + 0.5) is None           # backing off (and floored)
    adapter.busy.clear()
    assert worker._pick(NOW + 2.5) == ("data", "p")  # tried again later


def test_a_key_the_plugin_dropped_keeps_its_pixels():
    p, adapter = _pipeline()
    worker = _worker(p, [_record(1, "gone", 1010)])
    epoch = adapter.live_epochs.bump("p")
    adapter.batches["p"] = {}
    worker._run(("data", "p"))
    assert not p._live_ready and worker._handled_epoch[1] == epoch


def test_the_latest_hand_over_wins_the_slot():
    p, adapter = _pipeline()
    worker = _worker(p, [_record(1, "k", 1010)])
    for value in (5, 6):
        epoch = adapter.live_epochs.bump("p")
        adapter.batches["p"] = {"k": _element("k", 40, value, epoch)}
        worker._last_data_job.clear()
        worker._run(("data", "p"))
    assert list(p._live_ready) == [1, 1]
    assert p._live_slots[1].pixels[0, 0, 0] == 6


# -- ticks ----------------------------------------------------------------------


def test_a_tick_uses_the_lock_free_redraw_and_reschedules():
    p, adapter = _pipeline(live_max_hz=5)
    record = _record(1, "map", 1010, width=40, hz=4)
    worker = _worker(p, [record])
    adapter.redraws["map"] = _element("map", 40, 3)
    worker._run(("tick", record))
    assert adapter.calls[0][0] == "redraw"
    assert list(p._live_ready) == [1]
    assert worker._next_tick[1] > 0


def test_a_tick_without_a_redraw_never_waits_for_the_lock():
    p, adapter = _pipeline()
    record = _record(1, "map", 1010, hz=4)
    worker = _worker(p, [record])
    adapter.has_redraw = False
    worker._run(("tick", record))
    assert adapter.calls == [("render", "p", 0.0)]


def test_a_redraw_that_returns_none_is_a_skip_not_a_full_redraw():
    # None is the plugin saying "nothing new"; answering it with a locked
    # get_vegas_elements() at the tick rate is exactly what the hook avoids.
    p, adapter = _pipeline()
    record = _record(1, "map", 1010, hz=4)
    worker = _worker(p, [record])
    adapter.redraws["map"] = None
    worker._run(("tick", record))
    assert [c[0] for c in adapter.calls] == ["redraw"]
    assert not p._live_ready


def test_animation_is_capped_without_the_gate_and_when_slow():
    p, _ = _pipeline(gate=False, live_max_hz=5)
    record = _record(1, "map", 1010, hz=4)
    worker = _worker(p, [record])
    assert worker._tick_hz(record) == live_worker.UNGATED_MAX_HZ
    p2, _ = _pipeline(live_max_hz=5)
    worker2 = _worker(p2, [record])
    assert worker2._tick_hz(record) == 4
    worker2._render_ewma[("p", "map")] = live_worker.SLOW_RENDER_S * 2
    assert worker2._tick_hz(record) == 2
    p3, _ = _pipeline(live_max_hz=0)
    assert _worker(p3, [record])._tick_hz(record) == 0


def test_throttling_never_raises_a_slow_elements_rate():
    p, _ = _pipeline(live_max_hz=5)
    slow = _record(1, "map", 1010, hz=live_worker.MIN_THROTTLED_HZ / 2)
    worker = _worker(p, [slow])
    worker._render_ewma[("p", "map")] = live_worker.SLOW_RENDER_S * 2
    assert worker._tick_hz(slow) == live_worker.MIN_THROTTLED_HZ / 2


def test_a_tick_left_by_an_element_trimmed_away_does_not_spin_the_worker():
    p, _ = _pipeline()
    worker = _worker(p, [_record(1, "map", 1010, hz=4)])
    worker._next_tick[1] = NOW - 5          # past due
    p._elements = ()                        # ...and trimmed off the strip
    assert worker._next_wait(NOW) == live_worker.IDLE_WAIT_S


def test_a_tick_for_an_element_no_longer_animated_is_dropped():
    p, _ = _pipeline(live_max_hz=0)
    record = _record(1, "map", 1010, hz=4)
    worker = _worker(p, [record])
    worker._next_tick[1] = NOW - 5
    assert worker._next_wait(NOW) == live_worker.IDLE_WAIT_S
    assert worker._pick(NOW) is None
    assert 1 not in worker._next_tick


def test_the_wait_is_until_the_next_tick_on_screen():
    p, _ = _pipeline(live_max_hz=5)
    record = _record(1, "map", 1010, hz=4)
    worker = _worker(p, [record])
    worker._next_tick[1] = NOW + 0.2
    assert worker._next_wait(NOW) == pytest.approx(0.2)


def test_ticks_stop_for_an_element_far_ahead():
    p, _ = _pipeline(live_lead_screens=1.0)
    far = _record(1, "map", 1000 + 3 * W, hz=4)
    worker = _worker(p, [far])
    worker._next_tick[1] = NOW
    assert worker._pick(NOW) is None
    assert 1 not in worker._next_tick


# -- groups ---------------------------------------------------------------------


def test_a_group_is_fetched_a_member_at_a_time_and_published():
    p, _ = _pipeline()
    worker = _worker(p)
    worker.request_group()
    worker.request_group()                # coalesced
    for _ in range(3):
        assert p._prepared_group is None
        worker._run(worker._pick(NOW))
    assert p._prepared_group == [("a", ["img-a"]), ("b", ["img-b"]), ("c", ["img-c"])]
    # Each member was laid out for the strip here, as it arrived, not by the
    # render thread at the extension.
    assert p.prepared == p._prepared_group
    assert p.stream_manager.plans == [None]
    assert all(offscreen for _pid, offscreen in p.stream_manager.fetched)
    assert worker._pick(NOW) is None      # the slot is full


def test_a_reset_mid_group_drops_it():
    p, _ = _pipeline()
    worker = _worker(p)
    worker.request_group()
    worker._run(worker._pick(NOW))
    p._prefetch_generation += 1
    worker._run(("group", None))
    worker._run(("group", None))
    assert p._prepared_group is None


def test_a_stopped_worker_hands_over_what_it_has_of_a_group():
    p, _ = _pipeline()
    worker = _worker(p)
    worker.request_group()
    worker._run(worker._pick(NOW))          # one member of three
    worker.stop()
    worker._hand_over_partial_group()
    assert p._prepared_group == [("a", ["img-a"])]


@pytest.mark.parametrize("why", ["reset", "a group already waiting"])
def test_a_partial_group_is_not_handed_over(why):
    p, _ = _pipeline()
    worker = _worker(p)
    worker.request_group()
    worker._run(worker._pick(NOW))
    if why == "reset":
        p._prefetch_generation += 1
    else:
        p._prepared_group = ["waiting"]
    worker._hand_over_partial_group()
    assert p._prepared_group == (None if why == "reset" else ["waiting"])


def test_a_new_worker_waits_for_the_one_it_replaces():
    import time
    p, _ = _pipeline()
    retired = threading.Thread(target=time.sleep, args=(0.05,))
    retired.start()
    p._retired_worker = retired
    _worker(p)._join_legacy_prefetch()
    assert not retired.is_alive()


def test_every_job_runs_inside_the_gate():
    p, adapter = _pipeline()
    worker = _worker(p, [_record(1, "k", 1010)])
    adapter.live_epochs.bump("p")
    worker._run(("data", "p"))
    assert p.display_manager.render_gate.entered == 1


# -- robustness -------------------------------------------------------------------


def test_a_job_that_raises_is_counted_and_the_worker_goes_on():
    p, adapter = _pipeline()
    worker = _worker(p, [_record(1, "k", 1010)])
    adapter.live_epochs.bump("p")

    def explode(*a, **k):
        raise KeyError("plugin bug")

    adapter.render_live_elements = explode
    worker._run(("data", "p"))
    assert worker.stats["errors"] == 1
    worker.request_group()
    worker._run(worker._pick(NOW))
    assert p.stream_manager.plans


def test_a_new_strip_forgets_the_old_ones_bookkeeping():
    p, _ = _pipeline()
    worker = _worker(p, [_record(1, "k", 1010)])
    worker._handled_epoch[1] = 5
    worker._next_tick[1] = NOW
    p._strip_gen += 1
    p._elements = ()
    worker._pick(NOW)
    assert worker._handled_epoch == {} and worker._next_tick == {}


def test_the_thread_starts_and_stops():
    import time
    p, _ = _pipeline()
    worker = _worker(p, clock=time.monotonic)
    worker.start()
    worker.request_group()
    for _ in range(200):
        if p._prepared_group is not None:
            break
        time.sleep(0.01)
    worker.stop()
    worker.join(2)
    assert not worker.is_alive()
    assert p._prepared_group is not None


@pytest.mark.parametrize("view", [None])
def test_no_view_yet_still_fetches_groups(view):
    p, _ = _pipeline()
    worker = VegasWorker(p)
    worker.request_group()
    assert worker._pick(NOW) == ("group", None)


def test_the_summary_names_the_slowest_redraw(caplog):
    clock = [NOW]
    p, adapter = _pipeline()
    record = _record(1, "map", 1010, hz=4)
    worker = _worker(p, [record], clock=lambda: clock[0])
    adapter.redraws["map"] = _element("map", 40, 3)
    worker._run(("tick", record))
    clock[0] += live_worker.SUMMARY_INTERVAL_S + 1
    with caplog.at_level("INFO"):
        worker._maybe_summarise()
    line = next(r.getMessage() for r in caplog.records if "Vegas live:" in r.getMessage())
    assert "slowest redraw" in line and "p 'map'" in line
    assert worker._slowest_redraw is None           # per summary interval
