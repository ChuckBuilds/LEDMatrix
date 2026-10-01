"""Live priority takes the panel promptly, through the real run() loop.

Two behaviours the golden traces recorded (docs/RUN_LOOP_REDESIGN.md):

* A game that went live mid-screen waited for that screen to end. Now the
  frame loops and the dwell sleep check, at most once a second, and switch.
* When Vegas yielded to live content, one rotation screen showed before the
  game. Now the game is what shows next.

These run the real DisplayController.run() on the fake clock from
test/_run_loop_harness.py. Each trace row is
[start, mode, duration, exit_reason, frames, force_clear].
"""

import os

os.environ.setdefault("EMULATOR", "true")

from test._run_loop_harness import FakePlugin, RunLoopHarness  # noqa: E402


def _run(tmp_path, horizon, build):
    harness = RunLoopHarness(tmp_path, horizon=horizon)
    build(harness)
    return harness, harness.run()["screens"]


def _first(rows, mode):
    return next(row for row in rows if row[1] == mode)


def _counting(plugin):
    """Count has_live_content() calls, with the fake-clock time of each."""
    calls = []
    real = plugin.has_live_content

    def has_live_content():
        calls.append(plugin._h.clock.rel())
        return real()
    plugin.has_live_content = has_live_content
    return calls


class TestMidScreenTakeover:
    def test_live_game_cuts_a_one_hz_screen_short(self, tmp_path):
        def build(h):
            h.add_plugin(FakePlugin("clock", ["clock"], duration=30))
            h.add_plugin(FakePlugin("sports", ["sports_live"], duration=20,
                                    live=(12.5, 100), live_priority=True))
        _, rows = _run(tmp_path, 60, build)
        clock = rows[0]
        assert clock[1] == "clock" and clock[3] == "live"
        live = _first(rows, "sports_live")
        # Taken over at the first check after 12.5 s, not at 30 s.
        assert 12.5 <= live[0] <= 13.5

    def test_live_game_cuts_a_scrolling_screen_short(self, tmp_path):
        def build(h):
            h.add_plugin(FakePlugin("ticker", ["ticker"], duration=30, needs_high_fps=True))
            h.add_plugin(FakePlugin("sports", ["sports_live"], duration=20,
                                    live=(7.2, 100), live_priority=True))
        _, rows = _run(tmp_path, 40, build)
        assert rows[0][1] == "ticker" and rows[0][3] == "live"
        assert 7.2 <= _first(rows, "sports_live")[0] <= 8.3

    def test_live_game_cuts_a_make_up_dwell_short(self, tmp_path):
        # display() returns False after the first frame, so the 1 Hz loop
        # breaks and the rest of the 30 s is a dwell sleep.
        def build(h):
            h.add_plugin(FakePlugin("flaky", ["flaky"], duration=30, first_frame_only=True))
            h.add_plugin(FakePlugin("sports", ["sports_live"], duration=20,
                                    live=(10, 100), live_priority=True))
        _, rows = _run(tmp_path, 50, build)
        assert rows[0][1] == "flaky"
        assert 10 <= _first(rows, "sports_live")[0] <= 11

    def test_on_demand_is_never_preempted(self, tmp_path):
        def build(h):
            h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
            h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
            h.add_plugin(FakePlugin("sports", ["sports_live"], duration=20,
                                    live=(10, 200), live_priority=True))
            h.on_demand_request(2, "od", plugin_id="weather", duration=40)
        _, rows = _run(tmp_path, 60, build)
        on_demand = [row for row in rows if 2 <= row[0] < 42]
        assert on_demand and all(row[1] == "weather" for row in on_demand)
        # Not even interrupted and restarted: each on-demand screen runs out.
        assert all(row[3] != "live" for row in on_demand)
        assert on_demand[0][2] == 20.0
        # Once the session expires, the live game takes over.
        after = [row for row in rows if row[0] >= 42]
        assert after[0][1] == "sports_live"

    def test_simultaneous_games_still_take_turns(self, tmp_path):
        # Both go live during the clock screen. The takeover shows the first
        # one; the next pass must not advance the round-robin past it.
        def build(h):
            h.add_plugin(FakePlugin("clock", ["clock"], duration=30))
            h.add_plugin(FakePlugin("nfl", ["nfl_live"], duration=15,
                                    live=(10, 200), live_priority=True))
            h.add_plugin(FakePlugin("nhl", ["nhl_live"], duration=15,
                                    live=(10, 200), live_priority=True))
        _, rows = _run(tmp_path, 75, build)
        modes = [row[1] for row in rows]
        assert modes[:5] == ["clock", "nfl_live", "nhl_live", "nfl_live", "nhl_live"]
        assert 10 <= rows[1][0] <= 11


class TestTakeoverCheck:
    """_check_live_takeover() on its own, on a controller built by the harness."""

    def _controller(self, tmp_path, current="clock"):
        h = RunLoopHarness(tmp_path, horizon=10)
        h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
        sports = h.add_plugin(FakePlugin("sports", ["sports_recent", "sports_live"],
                                         duration=20, live=(0, 100), live_priority=True))
        dc = h.controller
        dc.current_display_mode = current
        dc.current_mode_index = dc.available_modes.index(current)
        return h, dc, _counting(sports)

    def test_switches_to_the_live_mode(self, tmp_path):
        _, dc, calls = self._controller(tmp_path)
        dc._check_live_takeover()
        assert dc.current_display_mode == "sports_live"
        assert dc.force_change is True
        assert dc._live_takeover_unshown is True
        # The rotation resumes from the screen that was cut short.
        assert dc._live_resume_index == 0
        assert len(calls) == 1  # once per plugin, not per mode key

    def test_on_demand_session_is_left_alone(self, tmp_path):
        _, dc, calls = self._controller(tmp_path)
        dc.on_demand_active = True
        dc._check_live_takeover()
        assert dc.current_display_mode == "clock"
        assert calls == []

    def test_scheduled_off_is_left_alone(self, tmp_path):
        _, dc, calls = self._controller(tmp_path)
        dc.is_display_active = False
        dc._check_live_takeover()
        assert dc.current_display_mode == "clock"
        assert calls == []

    def test_vegas_keeping_live_in_the_ticker_is_left_alone(self, tmp_path):
        h, dc, calls = self._controller(tmp_path)
        h.enable_vegas(live_in_ticker=True)
        dc._check_live_takeover()
        assert dc.current_display_mode == "clock"
        assert calls == []

    def test_live_screen_already_showing_is_not_rescanned(self, tmp_path):
        _, dc, calls = self._controller(tmp_path, current="sports_live")
        dc._collect_live_modes()  # the scan that put the live mode up
        dc._last_live_scan = None  # throttle out of the way
        dc._check_live_takeover()
        assert dc.current_display_mode == "sports_live"
        assert dc._live_takeover_unshown is False
        assert len(calls) == 1


class TestLiveContentPollingCost:
    def test_at_most_once_a_second_during_a_rotation_screen(self, tmp_path):
        holder = {}

        def build(h):
            h.add_plugin(FakePlugin("clock", ["clock"], duration=30, needs_high_fps=True))
            # Two mode keys on one plugin: still asked once per scan.
            sports = h.add_plugin(FakePlugin("sports", ["sports_recent", "sports_live"],
                                             duration=20, live_priority=True,
                                             content=lambda t, m: m != "sports_live"))
            holder["calls"] = _counting(sports)
        _run(tmp_path, 29, build)
        calls = holder["calls"]
        # A 125 Hz screen, 29 s long: about one scan a second, never two
        # within a second of each other.
        assert len(calls) <= 30
        assert all(b - a >= 0.99 for a, b in zip(calls, calls[1:]))

    def test_not_rescanned_while_a_live_game_is_showing(self, tmp_path):
        holder = {}

        def build(h):
            h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
            sports = h.add_plugin(FakePlugin("sports", ["sports_live"], duration=30,
                                             live=(0, 200), live_priority=True))
            holder["calls"] = _counting(sports)
        _, rows = _run(tmp_path, 90, build)
        assert all(row[1] == "sports_live" for row in rows)
        # Per 30 s live screen: the scan before it and the hold check after
        # it, as before -- nothing from inside the screen.
        assert len(holder["calls"]) <= 2 * len(rows)


class TestVegasYieldsToLive:
    def test_live_game_shows_next_without_a_rotation_screen(self, tmp_path):
        def build(h):
            h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
            h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
            h.add_plugin(FakePlugin("sports", ["sports_live"], duration=20,
                                    live=(40, 200), live_priority=True))
            h.enable_vegas(cycle=30)
        _, rows = _run(tmp_path, 80, build)
        assert rows[0][1] == "<vegas>"
        yielded = next(i for i, row in enumerate(rows) if row[3] == "vegas-live")
        nxt = rows[yielded + 1]
        assert nxt[1] == "sports_live"
        assert nxt[0] == rows[yielded][0] + rows[yielded][2]

    def test_live_in_ticker_keeps_the_ticker(self, tmp_path):
        def build(h):
            h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
            h.add_plugin(FakePlugin("sports", ["sports_live"], duration=20,
                                    live=(10, 200), live_priority=True))
            h.enable_vegas(cycle=30, live_in_ticker=True)
        _, rows = _run(tmp_path, 70, build)
        assert all(row[1] == "<vegas>" for row in rows)
