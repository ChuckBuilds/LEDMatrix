"""
Behavioral tests for DisplayController._check_schedule and
_check_dim_schedule — the on/off window and night-dimming logic.

test_display_controller_optimizations.py::TestScheduleMinuteGate already
covers the once-per-minute gating; this file covers what it doesn't:
midnight-crossing windows, mode selection (global / per-day / legacy
inference), per-day disabled days, invalid time strings, unknown
timezones, boundary equality, and the transition-tracking flags.

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
    """Context manager patching the controller module's clock."""
    patcher = patch("src.display_controller.datetime")
    mock_dt = patcher.start()
    mock_dt.strptime = datetime.strptime
    mock_dt.now.return_value.time.return_value = (
        datetime.strptime(time_str, "%H:%M").time())
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

    def test_boundaries_are_inclusive(self):
        dc = make_controller(self._config("09:00", "17:00"))
        assert check_at(dc, "09:00") is True   # now == start
        assert check_at(dc, "17:00") is True   # now == end

    def test_midnight_crossing_window(self):
        # 21:00 -> 07:00: active late evening AND early morning, inactive
        # mid-day.
        dc = make_controller(self._config("21:00", "07:00"))
        assert check_at(dc, "23:00") is True
        assert check_at(dc, "03:00") is True
        assert check_at(dc, "12:00") is False
        assert check_at(dc, "21:00") is True   # boundary
        assert check_at(dc, "07:00") is True   # boundary

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
