"""A WiFi notice preempts the current screen promptly and stays up.

It used to be checked only between screens, so a notice shorter than the
screen it was posted during expired unseen, and Vegas yielding for one went
on to a rotation screen instead of the notice. Runs the real run() loop on
the fake clock of test/_run_loop_harness.py.
"""

import os

os.environ.setdefault("EMULATOR", "true")

from test._run_loop_harness import FakePlugin, RunLoopHarness  # noqa: E402


def _rows(trace):
    return [tuple(row[:4]) for row in trace["screens"]]


def _wifi_rows(trace):
    return [row for row in trace["screens"] if row[1] == "<wifi>"]


def test_notice_preempts_a_static_screen_and_the_mode_resumes(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=70)
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
    h.wifi_message(25.3, "Connected to HomeNet", duration=5)
    trace = h.run()

    (wifi,) = _wifi_rows(trace)
    # Within about a second of being posted (the 1 Hz loop's next frame),
    # and up until it expires at 30.3.
    assert 25.3 <= wifi[0] <= 26.3
    assert wifi[0] + wifi[2] >= 30.3
    rows = _rows(trace)
    i = rows.index(tuple(wifi[:4]))
    assert rows[i - 1][1] == "weather" and rows[i - 1][3] == "wifi"
    # The interrupted screen comes back in full; the rotation is not skipped.
    assert rows[i + 1][1:3] == ("weather", 20.0)
    assert rows[i + 2][1] == "clock"


def test_notice_preempts_a_high_fps_screen(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=60)
    h.add_plugin(FakePlugin("ticker", ["ticker"], duration=30, enable_scrolling=True))
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.wifi_message(10.0, "AP mode on", duration=4)
    trace = h.run()

    (wifi,) = _wifi_rows(trace)
    assert 10.0 <= wifi[0] <= 11.0
    assert wifi[0] + wifi[2] >= 14.0
    assert trace["screens"][0][1:4] == ["ticker", wifi[0], "wifi"]


def test_notice_cuts_the_make_up_dwell_short(tmp_path):
    # flaky returns False after its first frame, so the rest of its screen
    # is a make-up dwell rather than a frame loop.
    h = RunLoopHarness(tmp_path, horizon=40)
    h.add_plugin(FakePlugin("flaky", ["flaky"], duration=20, first_frame_only=True))
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.wifi_message(8.0, "Connected to HomeNet", duration=5)
    trace = h.run()

    (wifi,) = _wifi_rows(trace)
    assert 8.0 <= wifi[0] <= 9.0
    assert wifi[0] + wifi[2] >= 13.0
    rows = _rows(trace)
    i = rows.index(tuple(wifi[:4]))
    # flaky was cut short, so it comes back rather than being rotated past.
    assert rows[i + 1][1] == "flaky"


def test_a_screen_that_ran_its_full_time_is_not_repeated(tmp_path):
    # Posted just before clock's 20 s are up: clock ends on time, the notice
    # shows, and the rotation moves on to weather rather than clock again.
    h = RunLoopHarness(tmp_path, horizon=50)
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
    h.wifi_message(19.5, "Connected to HomeNet", duration=5)
    trace = h.run()

    rows = _rows(trace)
    assert rows[0] == (0.0, "clock", 20.0, "wifi")
    assert rows[1][1] == "<wifi>"
    assert rows[2][1] == "weather"


def test_on_demand_still_outranks_the_notice(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=80)
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
    h.on_demand_request(5, "o1", plugin_id="weather", duration=30)
    h.wifi_message(15, "AP mode on", duration=30)
    trace = h.run()

    (wifi,) = _wifi_rows(trace)
    # Held back until the on-demand session expires at about t=35.
    assert wifi[0] >= 35.0
    on_demand = [r for r in trace["screens"] if r[1] == "weather" and r[0] < 35]
    assert on_demand and all(r[3] != "wifi" for r in on_demand[:-1])


def test_vegas_yields_to_the_notice_then_resumes(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=80)
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.enable_vegas(cycle=30)
    h.wifi_message(40, "Connected to HomeNet", duration=3)
    trace = h.run()

    rows = _rows(trace)
    i = next(n for n, row in enumerate(rows) if row[3] == "vegas-interrupt")
    assert rows[i][1] == "<vegas>" and 40.0 <= rows[i][0] + rows[i][2] <= 41.0
    # The notice is what shows next, for its whole 3 s, and Vegas follows.
    assert rows[i + 1][1] == "<wifi>"
    assert rows[i + 1][0] + rows[i + 1][2] >= 43.0
    assert rows[i + 2][1] == "<vegas>"
    assert "clock" not in [row[1] for row in rows[i:]]


def test_pending_check_ignores_a_cached_notice_that_has_expired(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=10)
    dc = h.controller
    dc._check_wifi_status_message = lambda: {"message": "x", "expires_at": 0.0}
    assert not dc._wifi_notice_pending()
    dc._check_wifi_status_message = lambda: {"message": "x", "expires_at": float("inf")}
    assert dc._wifi_notice_pending()
    dc.on_demand_active = True
    assert not dc._wifi_notice_pending()
