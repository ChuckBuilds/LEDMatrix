"""System-wide frame timing (src/common/frame_timing.py) and its soak report.

The recorder's job is to separate what a viewer sees as a hitch -- a moving
frame one or more refreshes late -- from things that are not jitter: static
screens, the first frame of a scroll, gaps between scrolls, and freezes.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common import frame_timing  # noqa: E402
from src.common.frame_timing import FrameTimingRecorder  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import frame_soak  # noqa: E402

PERIOD = 0.010  # a 100Hz panel


def _feed(recorder, intervals, hold=1, scrolling=True, start=100.0,
          blit=0.002, wait=0.004):
    """Present one frame, then one more per interval."""
    t = start
    recorder.record(blit, wait, hold, scrolling, t)
    for interval in intervals:
        t += interval
        recorder.record(blit, wait, hold, scrolling, t)
    return t


def _aggregate(recorder):
    batch, static = recorder._pending, recorder._static_frames
    recorder._pending, recorder._static_frames = [], 0
    recorder.aggregate(batch, static)
    return recorder.totals


def _recorder(tmp_path):
    # A flush interval nothing in these tests reaches, so aggregation is
    # driven explicitly and no worker thread starts.
    return FrameTimingRecorder(path=str(tmp_path / "stats.json"),
                               flush_interval=1e9)


def test_steady_frames_are_on_time_and_give_the_refresh(tmp_path):
    r = _recorder(tmp_path)
    _feed(r, [PERIOD] * 200)
    totals = _aggregate(r)
    assert totals["scroll_frames"] == 200
    assert totals["late_frames"] == 0
    assert abs(1.0 / r.refresh_period - 100.0) < 0.5


def test_a_frame_a_refresh_late_is_counted(tmp_path):
    r = _recorder(tmp_path)
    intervals = [PERIOD] * 200
    intervals[50] = 2 * PERIOD      # one refresh late
    intervals[120] = 4 * PERIOD     # three refreshes late
    _feed(r, intervals)
    totals = _aggregate(r)
    assert totals["late_frames"] == 2
    assert totals["missed_refreshes"] == 1 + 3
    assert totals["late_by"] == {"1": 1, "2": 0, "3-5": 1, "6+": 0}


def test_a_held_frame_is_not_late(tmp_path):
    # 50px/s on a 100Hz panel is 1px every 2 refreshes: 20ms is on time.
    r = _recorder(tmp_path)
    _feed(r, [2 * PERIOD] * 200, hold=2)
    totals = _aggregate(r)
    assert totals["late_frames"] == 0
    assert abs(1.0 / r.refresh_period - 100.0) < 0.5


def test_small_jitter_is_not_late(tmp_path):
    r = _recorder(tmp_path)
    _feed(r, [PERIOD * (1 + 0.03 * ((i % 5) - 2)) for i in range(300)])
    assert _aggregate(r)["late_frames"] == 0


def test_static_frames_and_the_start_of_a_scroll_are_not_timed(tmp_path):
    r = _recorder(tmp_path)
    t = _feed(r, [1.0, 1.0, 1.0], scrolling=False)
    # The first scrolling frame follows a static one 300ms later: that is a
    # scroll starting, not a 30-refresh stall.
    _feed(r, [PERIOD] * 100, start=t + 0.3)
    totals = _aggregate(r)
    assert totals["static_frames"] == 4
    assert totals["scroll_frames"] == 100
    assert totals["late_frames"] == totals["freezes"] == 0


def test_freezes_are_separate_from_late_frames_and_gaps_are_ignored(tmp_path):
    r = _recorder(tmp_path)
    intervals = [PERIOD] * 200
    intervals[80] = 0.400   # a render-thread plugin fetch: freeze
    intervals[120] = 1.5    # a longer stall, still inside the scroll: freeze
    intervals[150] = 6.0    # past any scroll's inactivity window: ignored
    _feed(r, intervals)
    totals = _aggregate(r)
    assert totals["freezes"] == 2
    assert abs(totals["freeze_seconds"] - 1.9) < 1e-9
    assert totals["freeze_by"] == {"<0.5s": 1, "0.5-1s": 0, "1-2s": 1, "2s+": 0}
    assert totals["late_frames"] == 0
    assert totals["scroll_frames"] == 197


def test_a_stall_between_one_and_two_seconds_is_not_lost(tmp_path):
    # Two "scrolling" frames can be up to DisplayManager's 2s inactivity
    # threshold apart. The first version ignored everything past 1s, so a
    # 1.4s render-thread stall vanished from the report.
    r = _recorder(tmp_path)
    intervals = [PERIOD] * 100
    intervals[40] = 1.4
    _feed(r, intervals)
    assert _aggregate(r)["freezes"] == 1


def test_early_frames_are_counted(tmp_path):
    # Hold 2 on a 100Hz panel: frames are due every 20ms. A swap that returns
    # after 10ms did not wait out the hold.
    r = _recorder(tmp_path)
    _feed(r, [2 * PERIOD] * 200, hold=2)
    _aggregate(r)
    intervals = [2 * PERIOD] * 200
    for i in range(0, 200, 20):
        intervals[i] = PERIOD
    _feed(r, intervals, hold=2, start=1000.0)
    totals = _aggregate(r)
    assert totals["early_frames"] == 10
    assert totals["late_frames"] == 0


def test_refresh_estimate_survives_a_window_full_of_misses(tmp_path):
    r = _recorder(tmp_path)
    _feed(r, [PERIOD] * 200)
    _aggregate(r)
    # A bad window where every frame is late must not redefine the refresh.
    _feed(r, [2 * PERIOD] * 200, start=1000.0)
    totals = _aggregate(r)
    assert abs(1.0 / r.refresh_period - 100.0) < 0.5
    assert totals["late_frames"] == 200


def test_record_hands_off_and_the_worker_writes_the_file(tmp_path):
    path = tmp_path / "stats.json"
    r = FrameTimingRecorder(path=str(path), flush_interval=0.5,
                            info={"cols": 128, "rows": 32})
    _feed(r, [PERIOD] * 120)     # 1.2s of frames: at least one flush
    deadline = time.time() + 5
    while not path.exists() and time.time() < deadline:
        time.sleep(0.02)
    stats = json.loads(path.read_text(encoding="utf-8"))
    assert stats["version"] == frame_timing.SCHEMA_VERSION
    assert stats["info"]["cols"] == 128
    assert stats["totals"]["scroll_frames"] > 0


def test_soak_report_is_the_difference_between_snapshots(tmp_path):
    r = _recorder(tmp_path)
    _feed(r, [PERIOD] * 200)
    _aggregate(r)
    before = json.loads(json.dumps(r.snapshot()))
    intervals = [PERIOD] * 1000
    intervals[500] = 0.0201  # a refresh late; off a bucket boundary
    _feed(r, intervals, start=500.0)
    _aggregate(r)
    after = json.loads(json.dumps(r.snapshot()))
    after["updated"] = before["updated"] + 10.0

    report = frame_soak.build_report(before, after, preview=True)
    assert report["scroll_frames"] == 1000
    assert report["late_frames"] == 1
    assert report["late_pct"] == 0.1
    assert report["timing_ms"]["blit"]["p50"] == 2.25   # 2ms lands in [2, 2.25)
    assert report["timing_ms"]["interval_per_hold"]["max"] == 20.25


def test_soak_fails_a_run_that_was_not_locked(tmp_path):
    r = _recorder(tmp_path)
    _feed(r, [2 * PERIOD] * 200, hold=2)
    _aggregate(r)
    before = json.loads(json.dumps(r.snapshot()))
    _feed(r, [PERIOD] * 1000, hold=2, start=500.0)   # never waited out the hold
    _aggregate(r)
    after = json.loads(json.dumps(r.snapshot()))
    after["updated"] = before["updated"] + 10.0
    report = frame_soak.build_report(before, after, preview=False)
    assert report["late_pct"] == 0.0
    assert report["early_pct"] == 100.0
    assert not frame_soak.passed(report, 0.1)


def test_soak_percentiles_mark_the_overflow_bucket():
    top = frame_timing.BUCKET_COUNT - 1
    result = frame_soak.percentiles({0: 98, top: 2}, 0.25)
    assert result["p50"] == 0.25
    assert str(result["max"]).startswith(">=")


def test_display_manager_records_every_presented_frame():
    """The hook sits in update_display, so every source is covered."""
    import os
    os.environ["EMULATOR"] = "true"
    from src.display_manager import DisplayManager
    DisplayManager._instance = None
    DisplayManager._initialized = False
    dm = DisplayManager({"display": {
        "hardware": {"rows": 32, "cols": 64, "chain_length": 1, "parallel": 1},
        "runtime": {"gpio_slowdown": 0}}}, suppress_test_pattern=True)
    try:
        assert dm.frame_timing.info["cols"] == 64
        dm.set_scrolling_state(True)
        for shade in (10, 20, 30):
            dm.draw.rectangle([0, 0, 4, 4], fill=(shade, 0, 0))
            dm.update_display()
        assert len(dm.frame_timing._pending) == 2  # 3 frames, 2 intervals
    finally:
        dm.set_scrolling_state(False)
        DisplayManager._instance = None
        DisplayManager._initialized = False


# --- stall watchdog ----------------------------------------------------------

class _FakeRecorder:
    def __init__(self):
        self.last_frame = None
        self.scrolling = True
        self.scrolling_now = lambda: self.scrolling


def test_watchdog_reports_a_stall_once_and_its_end(caplog):
    rec = _FakeRecorder()
    dog = frame_timing.StallWatchdog(rec, threshold=0.25, log_interval=0.0)
    ident = __import__("threading").get_ident()
    rec.last_frame = (10.0, True, ident)
    with caplog.at_level("WARNING", logger="src.common.frame_timing"):
        state = dog.check(10.1, 0.0, None, False)          # 100ms: fine
        assert state == (None, False)
        state = dog.check(10.4, 0.0, *state)               # 400ms: stall
        assert state == (10.0, True)
        state = dog.check(10.9, 0.0, *state)               # still stalled: no repeat
        rec.last_frame = (11.2, True, ident)
        state = dog.check(11.25, 0.0, *state)              # a frame arrived
    assert state == (None, False)
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2
    assert messages[0].startswith("Render stall: no frame for 400ms")
    assert "test_watchdog_reports_a_stall_once_and_its_end" in messages[0]
    assert messages[1] == "Render stall over: no frame for 1200ms"
    assert dog.stalls == 1


def test_watchdog_ignores_a_scroll_that_ended(caplog):
    rec = _FakeRecorder()
    dog = frame_timing.StallWatchdog(rec, threshold=0.25, log_interval=0.0)
    rec.last_frame = (10.0, True, 1)
    rec.scrolling = False                  # the scroll handed over
    with caplog.at_level("WARNING", logger="src.common.frame_timing"):
        assert dog.check(12.0, 0.0, None, False) == (None, False)
    assert not caplog.records


def test_watchdog_names_what_the_stalled_thread_is_waiting_on():
    import threading
    rec = _FakeRecorder()
    dog = frame_timing.StallWatchdog(rec)
    blocked, release = threading.Event(), threading.Event()

    def render_loop_waiting_on_a_lock():
        blocked.set()
        release.wait(5)

    t = threading.Thread(target=render_loop_waiting_on_a_lock, name="render")
    t.start()
    try:
        assert blocked.wait(5)
        text = dog.describe(t.ident, 1.5, 1.4)
    finally:
        release.set()
        t.join(5)
    assert "no frame for 1500ms" in text
    assert "the interpreter itself was blocked" in text
    assert "-- render (presents frames):" in text
    assert "render_loop_waiting_on_a_lock" in text


def test_watchdog_rate_limits_its_dumps(caplog):
    rec = _FakeRecorder()
    dog = frame_timing.StallWatchdog(rec, threshold=0.25, log_interval=30.0)
    with caplog.at_level("WARNING", logger="src.common.frame_timing"):
        for start in (10.0, 20.0):          # two stalls 10s apart
            rec.last_frame = (start, True, 1)
            state = dog.check(start + 0.5, 0.0, None, False)
            rec.last_frame = (start + 0.6, True, 1)
            dog.check(start + 0.65, 0.0, *state)
    dumps = [r for r in caplog.records if r.getMessage().startswith("Render stall:")]
    assert len(dumps) == 1
    assert dog.stalls == 2


# --- measuring the panel, and runs that never locked -------------------------
# The refresh measurement and the "not locked" cases came from the first
# version of scripts/render_bench.py, which graded runs with its own module.

class FakePanel:
    """A matrix whose swaps block for a fixed period, like real vsync."""

    def __init__(self, period, fail=False):
        self.period = period
        self.fail = fail
        self.swaps = 0

    def CreateFrameCanvas(self):  # noqa: N802 - mirrors rgbmatrix
        if self.fail:
            raise RuntimeError("no hardware here")
        return object()

    def SwapOnVSync(self, canvas, framerate_fraction=1):  # noqa: N802
        self.swaps += 1
        time.sleep(self.period)
        return canvas


def test_measure_refresh_times_the_swaps_not_the_loop():
    # A loop that spun without waiting for each swap would report far more
    # than the 200Hz a 5ms swap allows; the lower bound is loose because
    # sleep() on a loaded runner overshoots.
    measured = frame_timing.measure_refresh_hz(FakePanel(0.005), seconds=0.2)
    assert 0 < measured <= 210.0


def test_measure_refresh_discards_the_first_swap():
    panel = FakePanel(0.005)
    frame_timing.measure_refresh_hz(panel, seconds=0.05)
    assert panel.swaps >= 2


def test_measure_refresh_without_hardware_reports_nothing():
    assert frame_timing.measure_refresh_hz(FakePanel(0.0, fail=True), seconds=0.1) == 0.0
    assert frame_timing.measure_refresh_hz(object(), seconds=0.1) == 0.0


def _seeded(tmp_path, hz=100.0):
    return FrameTimingRecorder(path=str(tmp_path / "s.json"),
                               flush_interval=float("inf"), refresh_hz=hz)


def test_a_seeded_recorder_catches_a_loop_that_never_waited(tmp_path):
    # The first bench build free-ran at 827fps once the dirty-tracking skip
    # fired mid-scroll. Estimated from its own frames that looks fine; against
    # the measured panel rate every frame is early.
    r = _seeded(tmp_path)
    _feed(r, [0.0012] * 500)
    r.drain()
    assert r.totals["early_frames"] == 500
    assert abs(1.0 / r.refresh_period - 100.0) < 0.5


def test_a_seeded_recorder_catches_a_loop_stuck_at_half_rate(tmp_path):
    # Hold 1, but every frame takes two refreshes: self-consistent at 50Hz,
    # late on every frame against the panel's 100Hz.
    r = _seeded(tmp_path)
    _feed(r, [2 * PERIOD] * 500)
    r.drain()
    assert r.totals["late_frames"] == 500


def test_a_seeded_recorder_still_passes_a_panel_a_little_slower_than_idle(tmp_path):
    # 100.4Hz idle, 96.3Hz while rendering: not a single frame is late.
    r = _seeded(tmp_path, hz=100.4)
    _feed(r, [1 / 96.3] * 500)
    r.drain()
    assert r.totals["late_frames"] == r.totals["early_frames"] == 0


def test_soak_calls_a_rate_faster_than_the_panel_not_locked(tmp_path):
    r = _recorder(tmp_path)
    r.info = {"limit_refresh_rate_hz": 100}
    before = json.loads(json.dumps(r.snapshot()))
    _feed(r, [0.0012] * 500)           # unseeded: nothing looks early...
    r.drain()
    after = json.loads(json.dumps(r.snapshot()))
    after["updated"] = before["updated"] + 10.0
    report = frame_soak.build_report(before, after, preview=False)
    assert report["early_pct"] == 0.0
    assert report["measured_refresh_hz"] > 800
    assert not frame_soak.locked(report, 0.1)   # ...but 833Hz beats a 100Hz cap
    assert not frame_soak.passed(report, 0.1)


def test_soak_reports_the_rate_held_while_rendering(tmp_path):
    r = _recorder(tmp_path)
    before = json.loads(json.dumps(r.snapshot()))
    _feed(r, [1 / 96.3] * 500)
    r.drain()
    after = json.loads(json.dumps(r.snapshot()))
    after["updated"] = before["updated"] + 10.0
    report = frame_soak.build_report(before, after, preview=False)
    assert 95.0 <= report["held_refresh_hz"] <= 97.0


def test_render_bench_strip_lights_a_real_share_of_pixels():
    # How long SetImage takes depends on how many subpixels are lit; a mostly
    # dark strip would flatter the panel.
    import render_bench
    strip = render_bench.build_strip(128, 32, "test")
    assert strip.width >= 128 * 4
    lit = sum(1 for px in strip.getdata() if px != (0, 0, 0))
    assert lit / (strip.width * strip.height) > 0.05
