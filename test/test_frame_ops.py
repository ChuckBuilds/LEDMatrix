"""Attributing late frames to render-thread work (FrameTimingRecorder.note_op).

The recorder already says how often a moving frame was late. These tests pin
the part that says which work it followed: Vegas tags a strip extension or a
live-element patch before the frame it lands in, and the soak report gives
each kind its own late rate. The render bench drives the same work on a
schedule so it can be measured on a panel with nothing else running.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from src.common.frame_timing import FrameTimingRecorder  # noqa: E402
from src.common.scroll_helper import ScrollHelper  # noqa: E402
from src.vegas_mode.config import VegasModeConfig  # noqa: E402
from src.vegas_mode.render_pipeline import RenderPipeline  # noqa: E402

import frame_soak  # noqa: E402
import render_bench  # noqa: E402

PERIOD = 0.010  # a 100Hz panel


def _recorder(tmp_path, **kwargs):
    return FrameTimingRecorder(path=str(tmp_path / "stats.json"),
                               flush_interval=1e9, refresh_hz=100.0, **kwargs)


def _frame(recorder, t, scrolling=True, hold=1):
    recorder.record(0.002, 0.004, hold, scrolling, t)


def _totals(recorder):
    recorder.drain()
    return recorder.totals


# -- the recorder -----------------------------------------------------------


def test_a_noted_op_tags_the_next_frame_only(tmp_path):
    r = _recorder(tmp_path)
    t = 100.0
    _frame(r, t)
    for i in range(10):
        if i == 4:
            r.note_op("patch", 1000)
        t += PERIOD
        _frame(r, t)
    totals = _totals(r)
    assert totals["op_frames"] == {"patch": 1}
    assert totals["late_op_frames"] == {}
    assert totals["op_bytes"] == {"patch": 1000}


def test_a_late_frame_after_an_op_is_counted_against_it(tmp_path):
    r = _recorder(tmp_path)
    t = 100.0
    _frame(r, t)
    for i in range(20):
        late = i in (5, 12)
        if late or i == 15:
            r.note_op("extend", 4_000_000)
        t += 2 * PERIOD if late else PERIOD
        _frame(r, t)
    totals = _totals(r)
    assert totals["late_frames"] == 2
    assert totals["op_frames"] == {"extend": 3}
    assert totals["late_op_frames"] == {"extend": 2}
    assert totals["op_bytes"] == {"extend": 12_000_000}


def test_notes_before_one_frame_accumulate_per_kind(tmp_path):
    r = _recorder(tmp_path)
    _frame(r, 100.0)
    r.note_op("patch", 100)
    r.note_op("patch", 50)
    r.note_op("extend", 7)
    _frame(r, 100.0 + PERIOD)
    totals = _totals(r)
    assert totals["op_frames"] == {"patch": 1, "extend": 1}
    assert totals["op_bytes"] == {"patch": 150, "extend": 7}


def test_an_op_before_a_freeze_is_a_freeze_not_a_late_frame(tmp_path):
    r = _recorder(tmp_path)
    _frame(r, 100.0)
    r.note_op("extend", 10)
    _frame(r, 100.4)
    totals = _totals(r)
    assert totals["freezes"] == 1
    assert totals["op_freezes"] == {"extend": 1}
    assert totals["op_frames"] == {}
    assert totals["op_bytes"] == {"extend": 10}


def test_an_op_survives_the_scroll_state_going_missing_for_a_frame(tmp_path):
    # The frame the op landed in was presented with the scroll state missing;
    # the scroll resumed, so the interval counts, and so does its tag.
    r = _recorder(tmp_path)
    _frame(r, 100.0)
    r.note_op("patch", 5)
    _frame(r, 100.0 + 2 * PERIOD, scrolling=False)
    _frame(r, 100.0 + 3 * PERIOD)
    totals = _totals(r)
    assert totals["late_frames"] == 1
    assert totals["late_op_frames"] == {"patch": 1}


def test_an_op_noted_before_a_static_frame_is_dropped(tmp_path):
    r = _recorder(tmp_path)
    _frame(r, 100.0, scrolling=False)
    r.note_op("patch", 5)
    _frame(r, 101.0, scrolling=False)
    _frame(r, 102.0, scrolling=False)
    totals = _totals(r)
    assert totals["op_frames"] == {} and totals["op_bytes"] == {}


def test_frames_before_the_period_is_known_are_not_op_frames(tmp_path):
    # op_frames is a denominator for late_op_frames, which needs a period.
    r = FrameTimingRecorder(path=str(tmp_path / "s.json"), flush_interval=1e9)
    _frame(r, 100.0)
    r.note_op("patch", 5)
    _frame(r, 100.0 + PERIOD)
    totals = _totals(r)
    assert totals["op_frames"] == {}
    assert totals["op_bytes"] == {"patch": 5}


def test_aggregate_still_takes_frames_without_ops(tmp_path):
    r = _recorder(tmp_path)
    r.aggregate([(PERIOD, 0.002, 0.004, 1)] * 5, 0)
    assert r.totals["scroll_frames"] == 5
    assert r.totals["op_frames"] == {}


# -- screen handovers ---------------------------------------------------------
# The display controller tags a screen's first frame "handover". A freeze that
# frame ends is the next plugin drawing, not a scroll that stalled.


def test_a_handover_freeze_is_not_a_freeze(tmp_path):
    r = _recorder(tmp_path)
    _frame(r, 100.0)
    r.note_op("handover")
    _frame(r, 101.4)                 # 1.4s to draw the next screen
    r.note_op("extend", 10)
    _frame(r, 101.8)                 # a real one, for contrast
    totals = _totals(r)
    assert totals["handover_freezes"] == 1
    assert totals["freezes"] == 1
    assert totals["freeze_by"] == {"<0.5s": 1, "0.5-1s": 0, "1-2s": 0, "2s+": 0}
    assert totals["freeze_seconds"] == pytest.approx(0.4)
    # Still on the "after work" table, with its freezes column.
    assert totals["op_freezes"] == {"handover": 1, "extend": 1}


def test_a_quick_handover_is_an_ordinary_timed_frame(tmp_path):
    r = _recorder(tmp_path)
    _frame(r, 100.0)
    r.note_op("handover")
    _frame(r, 100.0 + 4 * PERIOD)
    totals = _totals(r)
    assert totals["handover_freezes"] == totals["freezes"] == 0
    assert totals["op_frames"] == {"handover": 1}
    assert totals["late_op_frames"] == {"handover": 1}


def test_a_dropped_note_tags_nothing(tmp_path):
    # The first display() drew nothing: the tag must not wait for whatever
    # frame comes next, here a stall a minute later.
    r = _recorder(tmp_path)
    _frame(r, 100.0)
    r.note_op("handover")
    r.drop_op("handover")
    _frame(r, 101.0)
    totals = _totals(r)
    assert totals["handover_freezes"] == 0
    assert totals["freezes"] == 1
    assert totals["op_freezes"] == {}


def test_dropping_a_note_keeps_other_kinds_and_is_harmless_when_none(tmp_path):
    r = _recorder(tmp_path)
    r.drop_op("handover")            # nothing noted: nothing to do
    _frame(r, 100.0)
    r.note_op("handover")
    r.note_op("patch", 5)
    r.drop_op("handover")
    _frame(r, 100.0 + PERIOD)
    r.drop_op("handover")            # already carried by a frame
    totals = _totals(r)
    assert totals["op_frames"] == {"patch": 1}


def test_the_soak_report_prints_handover_gaps(tmp_path, capsys):
    r = _recorder(tmp_path)
    before = json.loads(json.dumps(r.snapshot()))
    _frame(r, 100.0)
    r.note_op("handover")
    _frame(r, 100.5)
    report = _report(r, before)
    assert report["handover_freezes"] == 1
    assert report["freezes"] == 0
    frame_soak.print_report(report, 0.1)
    assert "Handover gaps      1" in capsys.readouterr().out


def test_a_report_from_an_older_recorder_has_no_handover_line(tmp_path, capsys):
    # Stats written before the count existed: diffed and printed without it.
    r = _recorder(tmp_path)
    before = json.loads(json.dumps(r.snapshot()))
    _frame(r, 100.0)
    _frame(r, 100.0 + PERIOD)
    r.drain()
    after = json.loads(json.dumps(r.snapshot()))
    after["updated"] = before["updated"] + 10.0
    for stats in (before, after):
        del stats["totals"]["handover_freezes"]
    report = frame_soak.build_report(before, after, preview=False)
    assert report["handover_freezes"] is None
    frame_soak.print_report(report, 0.1)
    assert "Handover gaps" not in capsys.readouterr().out


# -- the soak report ----------------------------------------------------------


def _report(r, before):
    r.drain()
    after = json.loads(json.dumps(r.snapshot()))
    after["updated"] = before["updated"] + 10.0
    return frame_soak.build_report(before, after, preview=False)


def test_the_soak_report_gives_each_kind_its_late_rate(tmp_path):
    r = _recorder(tmp_path)
    _frame(r, 50.0)
    r.note_op("patch", 10)
    _frame(r, 50.0 + PERIOD)
    r.drain()
    before = json.loads(json.dumps(r.snapshot()))

    t = 100.0
    _frame(r, t)
    for i in range(400):
        late = i % 100 == 0
        if i % 4 == 0:
            r.note_op("patch", 1000)
        t += 2 * PERIOD if late else PERIOD
        _frame(r, t)
    report = _report(r, before)
    row = report["ops"]["patch"]
    assert row["frames"] == 100          # the frame before the baseline is not in it
    assert row["late"] == 4
    assert row["late_pct"] == 4.0
    assert row["bytes"] == 100_000


def test_the_soak_report_has_no_op_rows_when_nothing_was_tagged(tmp_path, capsys):
    r = _recorder(tmp_path)
    before = json.loads(json.dumps(r.snapshot()))
    t = 100.0
    _frame(r, t)
    for _ in range(200):
        t += PERIOD
        _frame(r, t)
    report = _report(r, before)
    assert report["ops"] == {}
    frame_soak.print_report(report, 0.1)
    assert "after work" not in capsys.readouterr().out


def test_the_soak_report_prints_the_op_table(tmp_path, capsys):
    r = _recorder(tmp_path)
    before = json.loads(json.dumps(r.snapshot()))
    t = 100.0
    _frame(r, t)
    for i in range(200):
        if i == 10:
            r.note_op("extend", 3_000_000)
        t += PERIOD
        _frame(r, t)
    frame_soak.print_report(_report(r, before), 0.1)
    out = capsys.readouterr().out
    assert "after work" in out
    assert "extend" in out


def test_a_report_from_an_older_recorder_has_an_empty_op_table():
    totals = {"late_frames": 0}
    assert frame_soak.op_rows(totals) == {}


# -- Vegas tags its own work ---------------------------------------------------

W, H = 128, 32


class _DM:
    width = W
    height = H

    def __init__(self, recorder):
        self.image = Image.new("RGB", (W, H))
        self.frame_timing = recorder

    def set_scrolling_state(self, *a):
        pass

    def update_display(self):
        pass


class _Stream:
    def __init__(self, groups):
        self.groups = groups
        self.plugin_manager = type("PM", (), {"plugins": {}})()
        self.plugin_adapter = None
        self._i = 0

    def get_grouped_content_for_composition(self):
        return self.groups[0]

    def take_next_group(self, count=None, offscreen_only=False):
        if self._i >= len(self.groups):
            return []
        group = self.groups[self._i]
        self._i += 1
        return group


class _NoteSpy:
    def __init__(self):
        self.notes = []

    def note_op(self, kind, nbytes=0):
        self.notes.append((kind, nbytes))


def _block(w):
    return Image.new("RGB", (w, H), (255, 255, 255))


def test_vegas_tags_a_compose_and_every_extension():
    spy = _NoteSpy()
    groups = [[("a", [_block(600)])], [("b", [_block(600)])], [("c", [_block(600)])]]
    p = RenderPipeline(VegasModeConfig(lead_in_width=0, continuous_scroll=True),
                       _DM(spy), _Stream(groups))
    assert p.compose_scroll_content()
    assert [kind for kind, _ in spy.notes] == ["compose"]

    p.scroll_helper.scroll_position = 400.0   # far enough to trim on extension
    assert p.extend_scroll_content()
    kind, moved = spy.notes[-1]
    assert kind == "extend"
    # The append built the whole strip anew and the trim copied it again.
    assert moved > p.scroll_helper.cached_array.nbytes


def test_vegas_without_a_recorder_does_not_fail():
    class DM(_DM):
        def __init__(self):
            super().__init__(None)

    groups = [[("a", [_block(600)])], [("b", [_block(600)])]]
    p = RenderPipeline(VegasModeConfig(lead_in_width=0, continuous_scroll=True),
                       DM(), _Stream(groups))
    assert p.compose_scroll_content()
    assert p.extend_scroll_content()


# -- the render bench's strip work --------------------------------------------


def _helper(screens=6):
    helper = ScrollHelper(W, H)
    helper.set_scrolling_image(render_bench.build_strip(W, H, "t", screens=screens))
    return helper


def test_the_bench_strip_can_be_made_vegas_wide():
    assert render_bench.build_strip(W, H, "t", screens=30).width >= W * 30


def test_a_visible_patch_writes_its_columns_on_screen_and_nothing_else():
    helper = _helper()
    spy = _NoteSpy()
    work = render_bench.StripWork(helper, spy, helper.cached_image,
                                  patch_bytes=40 * H * 3, patch_every=1)
    helper.scroll_position = 200.0
    before = helper.cached_array.copy()
    work.before_frame()
    changed = np.flatnonzero((helper.cached_array != before).any(axis=(0, 2)))
    assert changed.size
    assert changed.min() >= 200 and changed.max() < 200 + W
    assert changed.max() - changed.min() < 40
    assert spy.notes == [("patch", 40 * H * 3)]
    assert work.patches == 1


def test_an_ahead_patch_lands_past_the_viewport():
    helper = _helper()
    work = render_bench.StripWork(helper, _NoteSpy(), helper.cached_image,
                                  patch_bytes=20 * H * 3, patch_every=1,
                                  patch_where="ahead")
    helper.scroll_position = 100.0
    before = helper.cached_array.copy()
    work.before_frame()
    changed = np.flatnonzero((helper.cached_array != before).any(axis=(0, 2)))
    assert changed.min() >= 100 + W


def test_patches_run_every_k_frames():
    helper = _helper()
    spy = _NoteSpy()
    work = render_bench.StripWork(helper, spy, helper.cached_image,
                                  patch_bytes=10 * H * 3, patch_every=5)
    for _ in range(20):
        work.before_frame()
    assert work.patches == 4 and len(spy.notes) == 4


def test_extensions_keep_the_strip_bounded_and_the_scroll_going():
    helper = _helper(screens=8)
    helper.set_pixels_per_frame(4)
    spy = _NoteSpy()
    work = render_bench.StripWork(helper, spy, helper.cached_image,
                                  extend_every_screens=2)
    widths = []
    for _ in range(3000):
        work.before_frame()
        helper.update_scroll_position()
        assert not helper.is_scroll_complete()
        widths.append(helper.cached_array.shape[1])
    assert work.extensions >= 10
    assert {kind for kind, _ in spy.notes} == {"extend"}
    # Appends and trims balance: the strip holds its width (within one
    # extension of it) instead of growing or running out ahead of the viewport.
    assert max(widths) - min(widths) <= 3 * W
    assert widths[-1] >= widths[0] - W
    assert helper.remaining_unscrolled() > 0
