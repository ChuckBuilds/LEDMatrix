"""A plugin duration that is not a number must not stop the display.

Several plugins return their ``display_duration`` setting as it is in
config.json (``return self.config.get('display_duration', 15.0)``), so a
value saved as ``"20"`` or ``null`` -- from the raw config editor, or by
hand -- reached run() as a string or None. _resolve_durations then compared
it with 0, the TypeError went past every handler in the loop, and the
display service exited; systemd restarted it into the same screen and the
same crash.
"""

import logging
import math
import os
from unittest.mock import MagicMock

os.environ.setdefault("EMULATOR", "true")

import pytest

from src.display_controller import DisplayController
from test._run_loop_harness import FakePlugin, RunLoopHarness


def _controller(plugin_modes):
    dc = object.__new__(DisplayController)
    dc.config = {}
    dc.plugin_modes = plugin_modes
    return dc


def _plugin(duration, plugin_id='clock-simple'):
    plugin = MagicMock()
    plugin.plugin_id = plugin_id
    plugin.get_display_duration.return_value = duration
    return plugin


class TestPluginDurationIsCoerced:
    @pytest.mark.parametrize('value, expected', [
        ('20', 20.0), (' 7.5 ', 7.5), (12, 12.0), (12.5, 12.5)])
    def test_numbers_and_numeric_strings_are_used(self, value, expected):
        dc = _controller({'clock': _plugin(value)})
        duration = dc._get_display_duration('clock')
        assert duration == expected and isinstance(duration, float)

    @pytest.mark.parametrize('value', [
        None, '', 'twenty', True, False, float('nan'), float('inf'), 'inf',
        [20], {'seconds': 20}])
    def test_anything_but_a_finite_number_gets_the_default(self, value):
        dc = _controller({'clock': _plugin(value)})
        assert dc._get_display_duration('clock') == 30

    @pytest.mark.parametrize('value', [0, -5, '-5', '0'])
    def test_a_number_not_above_zero_still_gets_the_15s_rule(self, value):
        """Unchanged: _resolve_durations turns it into 15 s, with its warning."""
        plugin = _plugin(value)
        dc = _controller({'clock': plugin})
        base = dc._get_display_duration('clock')
        assert dc._resolve_durations(plugin, 'clock', base, False)[1] == 15.0

    def test_a_raising_get_display_duration_gets_the_default(self):
        plugin = _plugin(None)
        plugin.get_display_duration.side_effect = KeyError('display_duration')
        assert _controller({'clock': plugin})._get_display_duration('clock') == 30

    def test_the_result_feeds_resolve_durations(self):
        """The two calls run() makes back to back, for one screen."""
        plugin = _plugin('bad')
        dc = _controller({'clock': plugin})
        base = dc._get_display_duration('clock')
        assert dc._resolve_durations(plugin, 'clock', base, False) == (30, 30)

    def test_logged_once_per_plugin(self, caplog):
        dc = _controller({'clock': _plugin('twenty'),
                          'clock_big': _plugin('twenty'),
                          'calendar': _plugin(None, plugin_id='calendar')})
        # clock_big is a second mode of the same plugin.
        dc.plugin_modes['clock_big'].plugin_id = 'clock-simple'
        with caplog.at_level(logging.WARNING, logger='src.display_controller'):
            for _ in range(3):
                for mode in ('clock', 'clock_big', 'calendar'):
                    dc._get_display_duration(mode)
        warnings = [r for r in caplog.records if 'display duration' in r.getMessage()]
        assert len(warnings) == 2
        assert {'clock-simple', 'calendar'} == {
            next(p for p in ('clock-simple', 'calendar') if p in r.getMessage())
            for r in warnings}

    def test_a_good_value_after_a_bad_one_is_used(self):
        plugin = _plugin(None)
        dc = _controller({'clock': plugin})
        assert dc._get_display_duration('clock') == 30
        plugin.get_display_duration.return_value = 45
        assert dc._get_display_duration('clock') == 45.0


class TestRunLoopSurvives:
    """Through the real run() on the harness's fake clock."""

    @pytest.mark.parametrize('duration, shown_for', [('20', 20.0), (None, 30.0),
                                                     ('twenty', 30.0)])
    def test_the_screen_runs_and_the_rotation_goes_on(self, tmp_path, duration, shown_for):
        harness = RunLoopHarness(tmp_path, horizon=120)
        harness.add_plugin(FakePlugin("weather", ["weather"], duration=30))
        harness.add_plugin(FakePlugin("clock-simple", ["clock"], duration=duration))
        # Before the fix run() returned at t=30, when the clock came up, and
        # the harness raised "run() returned ... before the horizon".
        rows = harness.run()["screens"]
        clock = next(row for row in rows if row[1] == "clock")
        assert math.isclose(clock[2], shown_for, abs_tol=1.0)
        assert [row[1] for row in rows][:3] == ["weather", "clock", "weather"]
