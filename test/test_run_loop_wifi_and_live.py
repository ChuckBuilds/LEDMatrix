"""A WiFi notice and a live game that both want the panel at once.

The two preempt the current screen independently (_wifi_notice_pending and
_check_live_takeover, both polled from the frame loops and the dwell sleep),
so these pin down how they combine. The documented priority is follower,
on-demand, WiFi, live, Vegas, rotation: the notice shows first, then the
game, with no rotation screen in between, and the scheduled-off panel shows
neither. Runs the real run() loop on the fake clock of
test/_run_loop_harness.py. Each trace row is
[start, mode, duration, exit_reason, frames, force_clear].
"""

import os

import pytest

os.environ.setdefault("EMULATOR", "true")

from test._run_loop_harness import FakePlugin, RunLoopHarness  # noqa: E402


def _run(tmp_path, horizon, build):
    harness = RunLoopHarness(tmp_path, horizon=horizon)
    build(harness)
    return harness.run()


def _sports(h, live, **kwargs):
    h.add_plugin(FakePlugin("sports", ["sports_live"], duration=20, live=live,
                            live_priority=True, **kwargs))


def _notice_then_game(rows, after, posted, expires):
    """Check the rows from index `after` on: notice, then game, nothing else.

    The notice is up within about a second of being posted and stays up
    until it expires (it may be redrawn across pass boundaries, so it can
    span several rows). The game follows it directly.
    """
    wifi = []
    i = after
    while rows[i][1] == "<wifi>":
        wifi.append(rows[i])
        i += 1
    assert wifi, rows
    assert posted <= wifi[0][0] <= posted + 1.25
    # Continuous: each notice row starts where the one before it ended.
    for prev, cur in zip(wifi, wifi[1:]):
        assert cur[0] == pytest.approx(prev[0] + prev[2])
    assert wifi[-1][0] + wifi[-1][2] >= expires
    game = rows[i]
    assert game[1] == "sports_live"
    assert game[0] == pytest.approx(wifi[-1][0] + wifi[-1][2])
    return i


@pytest.mark.parametrize("live_at, wifi_at", [(10.2, 10.4), (10.4, 10.2)],
                         ids=["game-first", "notice-first"])
def test_both_during_a_static_screen(tmp_path, live_at, wifi_at):
    def build(h):
        h.add_plugin(FakePlugin("clock", ["clock"], duration=30))
        h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
        _sports(h, (live_at, 60))
        h.wifi_message(wifi_at, "Connected to HomeNet", duration=5)
    trace = _run(tmp_path, 90, build)
    rows = trace["screens"]

    # The 1 Hz loop's next check after both arrive ends clock's screen.
    assert rows[0][1] == "clock" and rows[0][2] <= 11.0
    _notice_then_game(rows, 1, wifi_at, wifi_at + 5)
    # The game is never on the panel before the notice.
    first_game = next(row for row in rows if row[1] == "sports_live")
    first_wifi = next(row for row in rows if row[1] == "<wifi>")
    assert first_wifi[0] < first_game[0]
    # Once the game ends, the rotation resumes at the screen it cut short.
    after_game = next(row for row in rows if row[0] >= 60 and row[1] != "sports_live")
    assert after_game[1] == "clock"


def test_both_at_once_during_a_scrolling_screen(tmp_path):
    def build(h):
        h.add_plugin(FakePlugin("ticker", ["ticker"], duration=30, enable_scrolling=True))
        h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
        _sports(h, (10.0, 60))
        h.wifi_message(10.0, "AP mode on", duration=4)
    rows = _run(tmp_path, 90, build)["screens"]

    assert rows[0][1] == "ticker" and rows[0][0] + rows[0][2] <= 11.0
    _notice_then_game(rows, 1, 10.0, 14.0)
    assert "weather" not in [row[1] for row in rows if row[0] < 60]


def test_vegas_yields_to_both_with_no_rotation_screen(tmp_path):
    def build(h):
        h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
        _sports(h, (40, 70), content=lambda t, mode: 40 <= t < 70)
        h.enable_vegas(cycle=30)
        h.wifi_message(40, "Connected to HomeNet", duration=3)
    trace = _run(tmp_path, 110, build)
    rows = trace["screens"]

    yielded = next(i for i, row in enumerate(rows)
                   if row[1] == "<vegas>" and row[3] in ("vegas-live", "vegas-interrupt"))
    # Vegas's live check (4 Hz) can see the game before the notice file's
    # 1 Hz stat sees the notice; then the game is up for at most a second
    # before the notice preempts it.
    i = yielded + 1
    if rows[i][1] == "sports_live":
        assert rows[i][2] <= 1.0 and rows[i][3] == "wifi"
        i += 1
    i = _notice_then_game(rows, i, 40, 43)
    # Neither the rotation nor the ticker runs while the game is live.
    during = [row[1] for row in rows[yielded + 1:] if row[0] < 70]
    assert set(during) <= {"sports_live", "<wifi>"}
    assert rows[-1][1] == "<vegas>"


def test_vegas_stopped_for_a_game_shows_a_known_notice_first(tmp_path):
    # The notice is already posted when Vegas stops for the game: each Vegas
    # frame runs its live check before its interrupt check, so the game can
    # be what stops it. Without the interrupt check the notice is only
    # learned after the yield, which pins the order the yield path checks
    # them in: the notice first.
    def build(h):
        h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
        _sports(h, (40, 70), content=lambda t, mode: 40 <= t < 70)
        vegas = h.enable_vegas(cycle=30)
        vegas.set_interrupt_checker(lambda: False)
        h.wifi_message(39.5, "Connected to HomeNet", duration=4)
    rows = _run(tmp_path, 110, build)["screens"]

    yielded = next(i for i, row in enumerate(rows) if row[3] == "vegas-live")
    assert 40.0 <= rows[yielded][0] + rows[yielded][2] <= 40.3
    _notice_then_game(rows, yielded + 1, 40.0, 43.5)


def test_scheduled_off_shows_neither(tmp_path):
    # The harness clock starts at 22:59:30: off from 23:00 (t=30) to 23:05
    # (t=330). The notice and the game both arrive at t=120, well inside it
    # (whichever way the end minute is counted).
    def build(h):
        h.config["schedule"] = {"enabled": True, "start_time": "23:05", "end_time": "23:00"}
        h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
        h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
        _sports(h, (120, 400))
        h.wifi_message(120, "AP mode on", duration=30)
    trace = _run(tmp_path, 380, build)
    rows = trace["screens"]

    off = next(i for i, row in enumerate(rows) if row[1] == "<off>")
    assert rows[off][0] <= 90.0
    assert rows[off][0] + rows[off][2] == 330.0 and rows[off][3] == "schedule-on"
    assert not any(row[1] == "<wifi>" for row in rows)
    live_events = [e for e in trace["events"] if e[1] == "live"]
    assert live_events and all(e[0] >= 330.0 for e in live_events)
    # The game, still live when the panel comes back, is what shows.
    assert rows[off + 1][1] == "sports_live"
