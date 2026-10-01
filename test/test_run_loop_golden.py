"""Golden traces of DisplayController.run(): what is shown, for how long, and why.

Each scenario runs the real run() loop against fake plugins on a fake clock
(see test/_run_loop_harness.py) and compares the screens it produced with
test/fixtures/run_loop_golden/<scenario>.json. A trace row is

    [start_s, mode, duration_s, exit_reason, frames, force_clear_on_first_frame]

and ``events`` lists what else happened (requests, live changes, schedule,
brightness) with its time.

These pin down today's behaviour so run() can be restructured into an
Arbiter / ScreenRunner / Sources (docs/RUN_LOOP_REDESIGN.md) without changing
it. A diff here is a behaviour change: if it is intended, regenerate with
LEDMATRIX_REGEN_GOLDEN=1 and explain the change in the commit message.
"""

import os

import pytest

os.environ.setdefault("EMULATOR", "true")

from test._run_loop_harness import (  # noqa: E402
    FakePlugin,
    LegacyFakePlugin,
    RunLoopHarness,
    check_golden,
)


def scenario_plain_rotation(h: RunLoopHarness):
    # clock: duration from display_durations, which beats the plugin's own.
    # weather: the plugin's own duration. ticker: scrolls, so high-FPS.
    # legacy: display() without display_mode.
    h.config["display"]["display_durations"] = {"clock": 15}
    h.add_plugin(FakePlugin("clock", ["clock"], duration=99))
    h.add_plugin(FakePlugin("weather", ["weather_now", "weather_forecast"], duration=20))
    h.add_plugin(FakePlugin("ticker", ["ticker"], duration=10, enable_scrolling=True))
    h.add_plugin(LegacyFakePlugin("legacy", ["legacy"], duration=5))


def scenario_empty_modes(h: RunLoopHarness):
    # empty: never has content, skipped at once. ghost: a mode with no
    # plugin behind it. flaky: content on the first frame only, so the
    # 1 s loop breaks early and the dwell is made up by sleeping.
    h.add_plugin(FakePlugin("clock", ["clock"], duration=10))
    h.add_plugin(FakePlugin("empty", ["empty"], duration=10, content=lambda t, m: False))
    h.add_mode_without_plugin("ghost")
    h.add_plugin(FakePlugin("flaky", ["flaky"], duration=12, first_frame_only=True))


def scenario_all_empty(h: RunLoopHarness):
    # Nothing to show anywhere: one rotation of empty passes, then a 1 s
    # pause per pass instead of a spin.
    h.add_plugin(FakePlugin("a", ["a"], content=lambda t, m: False))
    h.add_plugin(FakePlugin("b", ["b"], content=lambda t, m: False))
    h.add_plugin(FakePlugin("c", ["c"], content=lambda t, m: t >= 6))


def scenario_plugin_error(h: RunLoopHarness):
    # broken's dispatch raises (no display lock: loading failed part-way),
    # so all its modes are skipped together; two failures open the breaker.
    # crashy's display() raises inside the executor: an empty pass
    # ("raised") that also counts as a breaker failure, so after two raises
    # it is skipped by the breaker. Its modes are not skipped together.
    h.add_plugin(FakePlugin("clock", ["clock"], duration=10))
    h.add_plugin(FakePlugin("broken", ["broken_a", "broken_b"], duration=10), lock=False)
    h.add_plugin(FakePlugin("weather", ["weather"], duration=10))
    h.add_plugin(FakePlugin("crashy", ["crashy"], duration=10, raises=True))


def scenario_dynamic_duration(h: RunLoopHarness):
    # Read once at startup, so set where __init__ left it.
    h.controller.global_dynamic_config = {"max_duration_seconds": 50}
    # scroller: high-FPS, completes its cycle 20 s after each reset.
    h.add_plugin(FakePlugin("scroller", ["scroller"], duration=10, needs_high_fps=True,
                            dynamic={"cap": None, "complete_after": 20}))
    # news: 1 s loop, asks for 45 s but its own cap is 40; never completes.
    h.add_plugin(FakePlugin("news", ["news"], duration=10,
                            dynamic={"cap": 40, "cycle": 45, "complete_after": None}))
    # board: no cap of its own, so the global 50 s applies; done after 5 s,
    # but the 10 s minimum (+0.5 s grace) holds it.
    h.add_plugin(FakePlugin("board", ["board"], duration=10,
                            dynamic={"cap": None, "complete_after": 5}))
    h.add_plugin(FakePlugin("clock", ["clock"], duration=10))


def scenario_live_priority(h: RunLoopHarness):
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
    h.add_plugin(FakePlugin(
        "sports", ["sports_recent", "sports_live"], duration=20,
        live=(50, 110), live_priority=True,
        content=lambda t, mode: mode != "sports_live" or 50 <= t < 110))


def scenario_live_round_robin(h: RunLoopHarness):
    h.add_plugin(FakePlugin("clock", ["clock"], duration=15))
    h.add_plugin(FakePlugin("nfl", ["nfl_live"], duration=15, live=(0, 70), live_priority=True))
    h.add_plugin(FakePlugin("nhl", ["nhl_live"], duration=15, live=(20, 100), live_priority=True))


def scenario_on_demand(h: RunLoopHarness):
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
    h.add_plugin(FakePlugin("sports", ["sports_recent", "sports_upcoming"], duration=15))
    # Mid-way through clock's first screen; then stopped by request.
    h.on_demand_request(25, "r1", plugin_id="sports")
    h.on_demand_request(95, "r2", action="stop")
    # A timed request that expires on its own.
    h.on_demand_request(150, "r3", plugin_id="weather", duration=30)


def scenario_on_demand_pinned(h: RunLoopHarness):
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin("sports", ["sports_recent", "sports_upcoming"], duration=15))
    h.on_demand_request(12, "p1", plugin_id="sports", mode="sports_upcoming", pinned=True)
    # An on-demand mode with nothing to show is skipped like any other.
    h.add_plugin(FakePlugin("starlark", ["app_a", "app_b"], duration=10,
                            content=lambda t, mode: mode != "app_a"))
    h.on_demand_request(80, "p2", plugin_id="starlark")
    h.on_demand_request(120, "p3", action="stop")


def scenario_on_demand_restored(h: RunLoopHarness):
    # A restart during an on-demand session resumes it: the first screen is
    # the saved mode (with a full clear), not the rotation's first mode, and
    # the rotation starts from the top once it expires.
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
    h.add_plugin(FakePlugin("sports", ["sports_recent", "sports_upcoming"], duration=15))
    h.restore_on_demand("sports", mode="sports_upcoming", duration=40)


def scenario_schedule(h: RunLoopHarness):
    # The clock starts at 22:59:30. Off from 23:01 until 23:05 (the window
    # spans midnight); dimmed from 23:00 until 23:01.
    h.config["schedule"] = {"enabled": True, "start_time": "23:05", "end_time": "23:01"}
    h.config["dim_schedule"] = {"enabled": True, "start_time": "23:00",
                                "end_time": "23:01", "dim_brightness": 30}
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
    # An on-demand request during scheduled downtime overrides it.
    h.on_demand_request(170, "s1", plugin_id="weather", duration=20)


def scenario_wifi_notice(h: RunLoopHarness):
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
    # Posted mid-screen: it preempts the screen at its next frame, stays up
    # until it expires, and the interrupted mode then comes back in full.
    h.wifi_message(25, "Connected to HomeNet", duration=5)
    # While on-demand is active the notice waits.
    h.on_demand_request(60, "w1", plugin_id="clock", duration=20)
    h.wifi_message(65, "AP mode on", duration=30)


def scenario_follower(h: RunLoopHarness):
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
    # Only checked at the top of a pass, so it takes over when the screen
    # running at t=35 ends, and hands back the pass after it ends.
    h.sync.follower_windows = [(35, 50)]


def scenario_vegas(h: RunLoopHarness):
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin(
        "sports", ["sports_live"], duration=20, live=(70, 100), live_priority=True,
        content=lambda t, mode: 70 <= t < 100))
    h.enable_vegas(cycle=30)
    # On-demand takes the panel from Vegas mid-iteration, then hands back.
    h.on_demand_request(150, "v1", plugin_id="clock", duration=25)
    h.wifi_message(200, "Connected to HomeNet", duration=3)


def scenario_vegas_live_in_ticker(h: RunLoopHarness):
    h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
    h.add_plugin(FakePlugin("sports", ["sports_live"], duration=20, live=(10, 50),
                            live_priority=True))
    h.enable_vegas(cycle=30, live_in_ticker=True)


SCENARIOS = {
    "plain_rotation": (scenario_plain_rotation, 160),
    "empty_modes": (scenario_empty_modes, 90),
    "all_empty": (scenario_all_empty, 12),
    "plugin_error": (scenario_plugin_error, 90),
    "dynamic_duration": (scenario_dynamic_duration, 220),
    "live_priority": (scenario_live_priority, 200),
    "live_round_robin": (scenario_live_round_robin, 150),
    "on_demand": (scenario_on_demand, 240),
    "on_demand_pinned": (scenario_on_demand_pinned, 160),
    "on_demand_restored": (scenario_on_demand_restored, 100),
    "schedule": (scenario_schedule, 400),
    "wifi_notice": (scenario_wifi_notice, 150),
    "follower": (scenario_follower, 80),
    "vegas": (scenario_vegas, 260),
    "vegas_live_in_ticker": (scenario_vegas_live_in_ticker, 100),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_run_loop_golden_trace(name, tmp_path):
    build, horizon = SCENARIOS[name]
    harness = RunLoopHarness(tmp_path, horizon=horizon)
    build(harness)
    trace = harness.run()
    check_golden(name, trace)


def test_traces_are_repeatable(tmp_path):
    """Two runs of the busiest scenario give the identical trace."""
    traces = []
    for i in range(2):
        (tmp_path / str(i)).mkdir()
        harness = RunLoopHarness(tmp_path / str(i), horizon=240)
        scenario_on_demand(harness)
        traces.append(harness.run())
    assert traces[0] == traces[1]
