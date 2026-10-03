"""
Behavioral tests for DisplayController._check_schedule and
_check_dim_schedule — the on/off window and night-dimming logic.

test_display_controller_optimizations.py::TestScheduleMinuteGate already
covers the once-per-minute gating; this file covers what it doesn't:
midnight-crossing windows, mode selection (global / per-day / legacy
inference), per-day disabled days, invalid time strings, unknown
timezones, the half-open [start, end) boundaries, on-demand ending during
scheduled-off, and the transition-tracking flags.

Both methods read only self.config and a handful of instance attributes,
so a bare stub via object.__new__ (the test_display_controller_vegas_tick
pattern) is enough — no managers needed.
"""

import os
from datetime import datetime

from unittest.mock import patch

import pytest

os.environ.setdefault("EMULATOR", "true")

from src.display_controller import DisplayController  # noqa: E402


def make_controller(config=None, *, normal_brightness=90):
    dc = object.__new__(DisplayController)
    dc.config = config or {}
    dc._tz = None
    dc._schedule_checked_minute = None
    dc.is_display_active = True
    dc._was_display_active = True
    dc._normal_brightness = normal_brightness
    dc._dim_checked_minute = None
    dc._cached_target_brightness = None
    dc.is_dimmed = False
    dc._was_dimmed = False
    return dc


def at(time_str, day="monday"):
    """Patch the controller module's clock to ``HH:MM`` or ``HH:MM:SS``."""
    patcher = patch("src.display_controller.datetime")
    mock_dt = patcher.start()
    mock_dt.strptime = datetime.strptime
    fmt = "%H:%M:%S" if time_str.count(":") == 2 else "%H:%M"
    mock_dt.now.return_value.time.return_value = (
        datetime.strptime(time_str, fmt).time())
    mock_dt.now.return_value.strftime.return_value.lower.return_value = day
    mock_dt.now.return_value.hour = int(time_str.split(":")[0])
    mock_dt.now.return_value.minute = int(time_str.split(":")[1])
    return patcher


@pytest.fixture
def clock():
    patchers = []

    def _at(time_str, day="monday"):
        patchers.append(p := at(time_str, day))
        return p

    yield _at
    for p in patchers:
        p.stop()


def check_at(dc, time_str, day="monday", clock=None):
    """Run _check_schedule at a mocked wall time, resetting the minute gate."""
    dc._schedule_checked_minute = None
    p = at(time_str, day)
    try:
        dc._check_schedule()
    finally:
        p.stop()
    return dc.is_display_active


def dim_at(dc, time_str, day="monday"):
    dc._dim_checked_minute = None
    p = at(time_str, day)
    try:
        return dc._check_dim_schedule()
    finally:
        p.stop()


class TestScheduleWindows:
    def _config(self, start, end, **extra):
        return {"schedule": {"enabled": True, "start_time": start,
                             "end_time": end, **extra},
                "timezone": "UTC"}

    def test_same_day_window(self):
        dc = make_controller(self._config("09:00", "17:00"))
        assert check_at(dc, "12:00") is True
        assert check_at(dc, "20:00") is False
        assert check_at(dc, "08:59") is False

    def test_window_is_half_open(self):
        # [start, end): on from the start minute, off at the end minute.
        dc = make_controller(self._config("09:00", "17:00"))
        assert check_at(dc, "08:59:59") is False
        assert check_at(dc, "09:00") is True    # now == start
        assert check_at(dc, "16:59:59") is True
        assert check_at(dc, "17:00") is False   # now == end

    def test_midnight_crossing_window(self):
        # 21:00 -> 07:00: active late evening AND early morning, inactive
        # mid-day.
        dc = make_controller(self._config("21:00", "07:00"))
        assert check_at(dc, "23:00") is True
        assert check_at(dc, "03:00") is True
        assert check_at(dc, "12:00") is False
        assert check_at(dc, "20:59:59") is False
        assert check_at(dc, "21:00") is True    # start
        assert check_at(dc, "00:00") is True    # midnight itself
        assert check_at(dc, "06:59:59") is True
        assert check_at(dc, "07:00") is False   # end

    def test_no_schedule_config_is_always_active(self):
        dc = make_controller({"timezone": "UTC"})
        dc.is_display_active = False
        dc._check_schedule()
        assert dc.is_display_active is True

    def test_invalid_time_string_falls_back_to_active(self):
        dc = make_controller(self._config("9 o'clock", "17:00"))
        dc.is_display_active = False
        assert check_at(dc, "03:00") is True  # ValueError -> stay on

    def test_unknown_timezone_falls_back_to_utc(self):
        dc = make_controller({"schedule": {"enabled": True,
                                           "start_time": "09:00",
                                           "end_time": "17:00"},
                              "timezone": "Mars/Olympus_Mons"})
        assert check_at(dc, "12:00") is True
        import pytz
        assert dc._tz is pytz.UTC


class TestScheduleModes:
    DAYS = {
        "monday": {"enabled": True, "start_time": "10:00",
                   "end_time": "18:00"},
        "tuesday": {"enabled": False},
    }

    def test_global_mode_ignores_days(self):
        dc = make_controller({"schedule": {
            "enabled": True, "mode": "global",
            "start_time": "09:00", "end_time": "17:00",
            "days": self.DAYS}, "timezone": "UTC"})
        # 09:30 is inside the global window but outside monday's per-day one.
        assert check_at(dc, "09:30", day="monday") is True

    def test_per_day_mode_uses_day_window(self):
        dc = make_controller({"schedule": {
            "enabled": True, "mode": "per-day",
            "start_time": "09:00", "end_time": "17:00",
            "days": self.DAYS}, "timezone": "UTC"})
        assert check_at(dc, "09:30", day="monday") is False  # before 10:00
        assert check_at(dc, "12:00", day="monday") is True

    def test_per_day_underscore_spelling_accepted(self):
        dc = make_controller({"schedule": {
            "enabled": True, "mode": "per_day",
            "days": self.DAYS}, "timezone": "UTC"})
        assert check_at(dc, "12:00", day="monday") is True

    def test_legacy_no_mode_infers_per_day_from_days_config(self):
        dc = make_controller({"schedule": {
            "enabled": True,
            "start_time": "09:00", "end_time": "17:00",
            "days": self.DAYS}, "timezone": "UTC"})
        assert check_at(dc, "09:30", day="monday") is False  # per-day won

    def test_per_day_disabled_day_turns_display_off(self):
        dc = make_controller({"schedule": {
            "enabled": True, "mode": "per-day",
            "days": self.DAYS}, "timezone": "UTC"})
        assert check_at(dc, "12:00", day="tuesday") is False

    def test_per_day_missing_day_falls_back_to_global(self):
        dc = make_controller({"schedule": {
            "enabled": True, "mode": "per-day",
            "start_time": "09:00", "end_time": "17:00",
            "days": self.DAYS}, "timezone": "UTC"})
        # Wednesday has no per-day entry -> global window applies.
        assert check_at(dc, "09:30", day="wednesday") is True

    def test_missing_enabled_key_means_enabled(self):
        # Backward compat: schedules written before the enabled flag.
        dc = make_controller({"schedule": {
            "start_time": "09:00", "end_time": "17:00"}, "timezone": "UTC"})
        assert check_at(dc, "20:00") is False


class TestScheduleTransitions:
    def test_was_display_active_tracks_state(self):
        dc = make_controller({"schedule": {"enabled": True,
                                           "start_time": "09:00",
                                           "end_time": "17:00"},
                              "timezone": "UTC"})
        check_at(dc, "12:00")
        assert dc._was_display_active is True
        check_at(dc, "20:00")
        assert dc._was_display_active is False
        check_at(dc, "12:05")
        assert dc._was_display_active is True


class TestDimSchedule:
    def _config(self, start="20:00", end="07:00", **extra):
        return {"dim_schedule": {"enabled": True, "start_time": start,
                                 "end_time": end, "dim_brightness": 25,
                                 **extra},
                "timezone": "UTC"}

    def test_disabled_by_default(self):
        dc = make_controller({"dim_schedule": {"start_time": "20:00",
                                               "end_time": "07:00"},
                              "timezone": "UTC"})
        # Unlike the on/off schedule, dimming defaults to DISABLED when the
        # enabled key is missing.
        assert dim_at(dc, "23:00") == 90
        assert dc.is_dimmed is False

    def test_overnight_dim_window(self):
        dc = make_controller(self._config())
        assert dim_at(dc, "23:00") == 25
        assert dc.is_dimmed is True
        assert dim_at(dc, "03:00") == 25
        assert dim_at(dc, "12:00") == 90
        assert dc.is_dimmed is False

    def test_dim_brightness_defaults_to_30(self):
        dc = make_controller({"dim_schedule": {"enabled": True,
                                               "start_time": "20:00",
                                               "end_time": "07:00"},
                              "timezone": "UTC"})
        assert dim_at(dc, "23:00") == 30

    def test_inactive_display_short_circuits_undimmed(self):
        dc = make_controller(self._config())
        dc.is_display_active = False
        dc.is_dimmed = True
        assert dim_at(dc, "23:00") == 90
        assert dc.is_dimmed is False

    def test_per_day_mode(self):
        dc = make_controller(self._config(mode="per-day", days={
            "monday": {"enabled": True, "start_time": "22:00",
                       "end_time": "06:00"},
            "tuesday": {"enabled": False},
        }))
        assert dim_at(dc, "23:00", day="monday") == 25
        assert dim_at(dc, "21:00", day="monday") == 90  # before per-day start
        assert dim_at(dc, "23:00", day="tuesday") == 90  # day disabled
        assert dc.is_dimmed is False

    def test_disabled_day_stays_normal_within_the_minute(self):
        # Dim late Monday, then Tuesday (dimming disabled) begins. The second
        # call in the same minute is served from the minute-gate cache, which
        # the disabled-day branch used to leave holding Monday's dim value --
        # so brightness flipped back to dim for the rest of every minute.
        dc = make_controller(self._config(mode="per-day", days={
            "monday": {"enabled": True, "start_time": "22:00",
                       "end_time": "06:00"},
            "tuesday": {"enabled": False},
        }))
        assert dim_at(dc, "23:59", day="monday") == 25
        dc._dim_checked_minute = None
        p = at("00:00", day="tuesday")
        try:
            assert dc._check_dim_schedule() == 90
            assert dc._check_dim_schedule() == 90  # cached, same minute
        finally:
            p.stop()
        assert dc.is_dimmed is False
        assert dc._was_dimmed is False

    def test_no_legacy_inference_for_dim(self):
        # Unlike _check_schedule, dim mode defaults to GLOBAL even when a
        # days config exists — no legacy inference.
        dc = make_controller(self._config(days={
            "monday": {"enabled": True, "start_time": "22:00",
                       "end_time": "06:00"},
        }))
        # 21:00 is inside the global 20:00-07:00 window but outside monday's
        # per-day 22:00 start; global mode wins.
        assert dim_at(dc, "21:00", day="monday") == 25

    def test_invalid_time_string_returns_normal(self):
        dc = make_controller(self._config(start="late"))
        assert dim_at(dc, "23:00") == 90

    def test_was_dimmed_tracks_transitions(self):
        dc = make_controller(self._config())
        dim_at(dc, "23:00")
        assert dc._was_dimmed is True
        dim_at(dc, "12:00")
        assert dc._was_dimmed is False


def check_in_same_minute(dc, time_str, day="monday"):
    """Run _check_schedule WITHOUT resetting the minute gate, as the loop does."""
    p = at(time_str, day)
    try:
        dc._check_schedule()
    finally:
        p.stop()
    return dc.is_display_active


class TestEndMinuteBoundary:
    """The panel goes off at the end minute whichever second the check runs.

    The loop evaluates the schedule once per clock minute, on the first check
    in it. With a closed [start, end] window only a check at hh:mm:00.000 saw
    the end minute as inside, so the panel went off at the start or the end
    of that minute depending on timing.
    """

    WINDOWS = {
        "same_day": ({"start_time": "09:00", "end_time": "17:00"},
                     "monday", "16:59", "17:00"),
        "midnight_crossing": ({"start_time": "22:00", "end_time": "07:00"},
                              "monday", "06:59", "07:00"),
        "per_day_midnight_crossing": (
            {"mode": "per-day", "start_time": "09:00", "end_time": "17:00",
             "days": {"wednesday": {"enabled": True, "start_time": "22:00",
                                    "end_time": "07:00"}}},
            "wednesday", "06:59", "07:00"),
    }

    def _controller(self, window):
        return make_controller({"schedule": {"enabled": True, **window},
                                "timezone": "UTC"})

    @pytest.mark.parametrize("name", sorted(WINDOWS))
    @pytest.mark.parametrize("second", ["00", "59"])
    def test_off_for_the_whole_end_minute(self, name, second):
        window, day, last_on, end = self.WINDOWS[name]
        dc = self._controller(window)
        assert check_at(dc, f"{last_on}:59", day) is True
        # First check of the end minute, at :00 or at :59.
        assert check_in_same_minute(dc, f"{end}:{second}", day) is False

    @pytest.mark.parametrize("name", sorted(WINDOWS))
    def test_gated_minute_keeps_the_off_answer(self, name):
        window, day, last_on, end = self.WINDOWS[name]
        dc = self._controller(window)
        assert check_at(dc, f"{last_on}:30", day) is True
        assert check_in_same_minute(dc, f"{end}:00", day) is False
        assert check_in_same_minute(dc, f"{end}:59", day) is False

    @pytest.mark.parametrize("second", ["00", "59"])
    def test_on_for_the_whole_start_minute(self, second):
        dc = self._controller({"start_time": "22:00", "end_time": "07:00"})
        assert check_at(dc, "21:59:59") is False
        assert check_in_same_minute(dc, f"22:00:{second}") is True


class TestOnDemandEndsDuringScheduledOff:
    """An on-demand session ending in off hours blanks the panel at once,
    not when the once-a-minute schedule check next runs."""

    def _controller(self):
        dc = make_controller({"schedule": {"enabled": True,
                                           "start_time": "07:00",
                                           "end_time": "23:00"},
                              "timezone": "UTC"})
        dc.on_demand_active = False
        dc.on_demand_schedule_override = False
        return dc

    def _evaluate(self, dc, time_str):
        p = at(time_str)
        try:
            dc._evaluate_schedule()
        finally:
            p.stop()
        return dc.is_display_active

    def test_session_end_in_off_hours_blanks_within_the_minute(self):
        dc = self._controller()
        assert self._evaluate(dc, "23:30:05") is False
        dc.on_demand_active = True
        assert self._evaluate(dc, "23:30:10") is True    # override
        assert dc.on_demand_schedule_override is True
        dc._reset_on_demand_fields()                      # expired or stopped
        assert self._evaluate(dc, "23:30:40") is False   # same minute
        assert dc.on_demand_schedule_override is False

    def test_session_end_in_on_hours_stays_on(self):
        dc = self._controller()
        assert self._evaluate(dc, "12:00:05") is True
        dc.on_demand_active = True
        assert self._evaluate(dc, "12:00:10") is True
        dc._reset_on_demand_fields()
        assert self._evaluate(dc, "12:00:40") is True


class TestOnDemandEndsDuringScheduledOffRunLoop:
    """The same through the real run() loop (test/_run_loop_harness.py).

    The harness clock starts at 22:59:30; the schedule below is off from
    23:01 (t=90) until 23:05 (t=330). The sessions end mid-minute, so the
    old behaviour (on until the next minute) would show as a gap."""

    def _harness(self, tmp_path):
        from test._run_loop_harness import FakePlugin, RunLoopHarness
        h = RunLoopHarness(tmp_path, horizon=260)
        h.config["schedule"] = {"enabled": True, "start_time": "23:05",
                                "end_time": "23:01"}
        h.add_plugin(FakePlugin("clock", ["clock"], duration=20))
        h.add_plugin(FakePlugin("weather", ["weather"], duration=20))
        return h

    @staticmethod
    def _first_off_after(trace, t):
        return [row for row in trace["screens"]
                if row[1] == "<off>" and row[0] >= t][0]

    def test_expiry_blanks_at_once(self, tmp_path):
        h = self._harness(tmp_path)
        # 15 s from t=170 ends at t=185, 23:02:35.
        h.on_demand_request(170, "x1", plugin_id="weather", duration=15)
        trace = h.run()
        session = [r for r in trace["screens"] if r[0] == 170.0][0]
        assert session[1:4] == ["weather", 15.0, "on-demand-expired"]
        assert self._first_off_after(trace, 170)[0] == 185.0

    def test_stop_blanks_at_once(self, tmp_path):
        h = self._harness(tmp_path)
        h.on_demand_request(170, "x1", plugin_id="weather")
        h.on_demand_request(181, "x2", action="stop")  # 23:02:31
        trace = h.run()
        assert 181.0 <= self._first_off_after(trace, 170)[0] <= 182.0


class TestDimBoundaries:
    """The dim schedule shares _in_window, so it is half-open too."""

    def _config(self, start, end, **extra):
        return {"dim_schedule": {"enabled": True, "start_time": start,
                                 "end_time": end, "dim_brightness": 25,
                                 **extra},
                "timezone": "UTC"}

    def test_same_day_dim_window_is_half_open(self):
        dc = make_controller(self._config("13:00", "14:00"))
        assert dim_at(dc, "12:59:59") == 90
        assert dim_at(dc, "13:00") == 25
        assert dim_at(dc, "13:59:59") == 25
        assert dim_at(dc, "14:00:00") == 90
        assert dim_at(dc, "14:00:59") == 90

    def test_midnight_crossing_dim_window(self):
        dc = make_controller(self._config("20:00", "07:00"))
        assert dim_at(dc, "19:59:59") == 90
        assert dim_at(dc, "20:00") == 25
        assert dim_at(dc, "00:00") == 25
        assert dim_at(dc, "06:59:59") == 25
        assert dim_at(dc, "07:00:00") == 90
        assert dim_at(dc, "07:00:59") == 90

    def test_per_day_dim_end_minute(self):
        dc = make_controller(self._config("20:00", "07:00", mode="per-day", days={
            "friday": {"enabled": True, "start_time": "23:00",
                       "end_time": "06:00"},
        }))
        assert dim_at(dc, "05:59:59", day="friday") == 25
        assert dim_at(dc, "06:00:00", day="friday") == 90
        assert dim_at(dc, "06:00:59", day="friday") == 90

    def test_dim_end_minute_checked_late_in_the_minute(self):
        dc = make_controller(self._config("20:00", "07:00"))
        assert dim_at(dc, "06:59:30") == 25
        p = at("07:00:59")  # first check of the end minute; gate not reset
        try:
            assert dc._check_dim_schedule() == 90
        finally:
            p.stop()
