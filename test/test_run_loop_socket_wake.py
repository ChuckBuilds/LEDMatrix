"""A control socket command wakes the run loop (control socket stage 2).

Stage 1's socket was no faster than the file mailbox: a queued command
waited for the same polls the mailbox does -- the 1 s frame sleep of a static
screen, the 0.25 s dwell tick, and Vegas's interrupt check every 10 frames
(about 0.4 s at the 24 fps a Pi 4 manages). Now the render thread waits on
the socket's queue instead of sleeping, and Vegas checks the queue every
frame, so a command is applied:

* at once on a static screen and in a dwell (here: at the instant it is
  queued, on the fake clock);
* at the next frame in Vegas (8 ms here, at 125 fps).

Each test runs the real run() loop on the fake clock of
test/_run_loop_harness.py and compares the socket with the mailbox for the
same request. The latencies are the fake clock's, so they are exact.
"""

import os

import pytest

os.environ.setdefault("EMULATOR", "true")

from src.ipc.contract import Command, ErrorCode  # noqa: E402
from test._run_loop_harness import FakePlugin, RunLoopHarness  # noqa: E402

POSTED = 10.3


def _event_time(trace, kind):
    return next(e[0] for e in trace["events"] if e[1] == kind)


def _on_demand_latency(tmp_path, build, via, horizon=40):
    h = RunLoopHarness(tmp_path, horizon=horizon)
    build(h)
    if via == "socket":
        h.control_socket().post(POSTED, Command.ON_DEMAND_START, {"plugin_id": "weather"})
    else:
        h.on_demand_request(POSTED, "mb1", plugin_id="weather")
    trace = h.run()
    return round(_event_time(trace, "on-demand-start") - POSTED, 3), trace


def _static(h):
    h.add_plugin(FakePlugin("clock", ["clock"], duration=30))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=30))


def _dwell(h):
    # Content on the first frame only: the 1 s loop ends at t=1 and the rest
    # of the 30 s is made up in the dwell sleep (_sleep_with_plugin_updates).
    h.add_plugin(FakePlugin("clock", ["clock"], duration=30, first_frame_only=True))
    h.add_plugin(FakePlugin("weather", ["weather"], duration=30))


def _vegas(h):
    _static(h)
    h.enable_vegas(cycle=30)


@pytest.mark.parametrize("build, mailbox_latency", [
    (_static, 0.7),    # the next 1 s frame, at t=11
    (_dwell, 0.2),     # the next 0.25 s tick, at t=10.5
    (_vegas, 0.02),    # the next 10-frame check: 80 ms at 125 fps, ~0.4 s on a Pi 4
], ids=["static-screen", "dwell", "vegas"])
def test_a_socket_command_lands_at_once(tmp_path, build, mailbox_latency):
    (tmp_path / "s").mkdir()
    (tmp_path / "m").mkdir()
    socket_latency, trace = _on_demand_latency(tmp_path / "s", build, "socket")
    via_mailbox, _ = _on_demand_latency(tmp_path / "m", build, "mailbox")

    assert via_mailbox == pytest.approx(mailbox_latency, abs=0.002)
    if build is _vegas:
        # One 8 ms frame: Vegas checks the queue every frame now.
        assert socket_latency <= 0.008
    else:
        assert socket_latency == 0.0
    # And the requested plugin is what the panel shows next, from then on.
    row = next(r for r in trace["screens"] if r[1] == "weather")
    assert row[0] == pytest.approx(POSTED + socket_latency, abs=0.001)


def test_a_brightness_lands_at_once_without_ending_the_screen(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=40)
    _static(h)
    command = h.control_socket().post(POSTED, Command.BRIGHTNESS_SET, {"brightness": 40})
    trace = h.run()

    assert [e for e in trace["events"] if e[1] == "brightness"] == [[POSTED, "brightness", 40]]
    # The clock screen runs its full 30 s and the next starts on time.
    assert trace["screens"][0][:4] == [0.0, "clock", 30.0, "duration"]
    assert trace["screens"][1][:2] == [30.0, "weather"]
    assert command.outcome.done and command.outcome.result["panel_brightness"] == 40


def test_the_static_frame_cadence_is_unchanged_by_a_command(tmp_path):
    """The wait resumes after a command that does not end the screen, so the
    plugin is still drawn once a second, not once more at the command."""
    h = RunLoopHarness(tmp_path, horizon=31)
    _static(h)
    quiet = h.run()

    (tmp_path / "b").mkdir()
    h2 = RunLoopHarness(tmp_path / "b", horizon=31)
    _static(h2)
    h2.control_socket().post(POSTED, Command.BRIGHTNESS_SET, {"brightness": 40})
    busy = h2.run()
    assert busy["screens"][0] == quiet["screens"][0]


def _with_reloadable(h, version="2.0.0", loads=True):
    new = FakePlugin("clock", ["clock"], duration=30)

    def reload_plugin(plugin_id):
        h.log("reload", plugin_id)
        if not loads:
            return False
        new._h = h
        h.pm.plugins[plugin_id] = new
        h.pm.plugin_manifests[plugin_id] = {"version": version, "display_modes": ["clock"]}
        return True

    h.pm.reload_plugin = reload_plugin
    return new


def test_a_plugin_reload_ends_the_screen_and_reloads_before_the_next(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=70)
    _static(h)
    new = _with_reloadable(h)
    command = h.control_socket().post(POSTED, Command.PLUGIN_RELOAD, {"plugin_id": "clock"})
    trace = h.run()

    # Reloaded the moment it was asked for: the clock screen ends then, and
    # the rotation moves on.
    assert _event_time(trace, "reload") == POSTED
    assert trace["screens"][0][:3] == [0.0, "clock", POSTED]
    assert trace["screens"][1][:2] == [POSTED, "weather"]
    assert command.outcome.result == {"plugin_id": "clock", "reloaded": True,
                                      "version": "2.0.0", "modes": ["clock"]}
    # The next clock screen is drawn by the reloaded instance.
    assert h.controller.plugin_modes["clock"] is new
    assert trace["screens"][2][1] == "clock"


def test_a_plugin_reload_during_vegas_yields_at_the_next_frame(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=70)
    _vegas(h)
    _with_reloadable(h)
    command = h.control_socket().post(POSTED, Command.PLUGIN_RELOAD, {"plugin_id": "clock"})
    trace = h.run()

    assert 0 <= _event_time(trace, "reload") - POSTED <= 0.008
    assert command.outcome.result["reloaded"] is True
    # The ticker resumes after the reload; no rotation screen in between.
    after = [r for r in trace["screens"] if r[0] > POSTED]
    assert after and after[0][1] == "<vegas>"


def test_a_plugin_that_does_not_load_again_is_reported(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=40)
    _static(h)
    _with_reloadable(h, loads=False)
    command = h.control_socket().post(POSTED, Command.PLUGIN_RELOAD, {"plugin_id": "clock"})
    h.run()
    assert command.outcome.error_code == ErrorCode.FAILED
    assert "clock" not in h.controller.available_modes


def test_reloading_a_plugin_that_is_not_running(tmp_path):
    h = RunLoopHarness(tmp_path, horizon=40)
    _static(h)
    command = h.control_socket().post(POSTED, Command.PLUGIN_RELOAD, {"plugin_id": "nope"})
    trace = h.run()
    assert command.outcome.error_code == ErrorCode.NOT_LOADED
    # It still cost the clock screen nothing but its end: rotation goes on.
    assert [r[1] for r in trace["screens"][:2]] == ["clock", "weather"]
