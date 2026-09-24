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
