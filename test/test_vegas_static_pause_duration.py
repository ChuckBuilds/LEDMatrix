"""A Vegas static pause lasts as long as the rotation shows the plugin.

The pause asked the plugin for get_display_duration() and compared the
answer with the clock. Several plugins (clock-simple, calendar, countdown)
return their display_duration setting as it is in config.json, so one saved
as "20" or null -- the raw config editor, a hand edit -- reached that
comparison as a string or None. The TypeError went to the pause's broad
except, which ended the pause: the plugin flashed up and the scroll went on,
at every one of its turns. inf paused until something interrupted it, and
NaN, False, 0 or a negative number ended the pause at once.

The pause now reads the answer the way the rotation does since #739, with
the same helper (base_plugin.finite_seconds): a numeric string counts;
anything else that is not a finite number, or a raise, gets the rotation's
30 s; a number at or below zero gets its 15 s.
"""

import logging
import os
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("EMULATOR", "true")

import pytest

from src.vegas_mode import coordinator

NOT_NUMBERS = [None, '', 'twenty', True, False, float('nan'), float('inf'),
               'inf', '1e400', [20], {'seconds': 20}]
NOT_ABOVE_ZERO = [0, -5, '-5', '0']
NUMBERS = [('20', 20.0), (' 7.5 ', 7.5), (12, 12.0), (12.5, 12.5)]


class FakeClock:
    """time.monotonic/time.sleep for the pause loop: sleeping moves the clock."""

    #: A pause still going after this long never ends (inf did that).
    LIMIT = 3600.0

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.now > self.LIMIT:
            raise RuntimeError("the static pause never ended")


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(coordinator, 'time', fake)
    return fake


def _plugin(duration, plugin_id='clock-simple'):
    plugin = MagicMock()
    plugin.plugin_id = plugin_id
    plugin.get_display_duration.return_value = duration
    return plugin


def _coord(*plugins):
    coord = coordinator.VegasModeCoordinator.__new__(coordinator.VegasModeCoordinator)
    coord.render_pipeline = MagicMock()
    coord.render_pipeline.get_scroll_position.return_value = 0
    coord.display_manager = MagicMock()
    locks = {plugin.plugin_id: threading.Lock() for plugin in plugins}
    coord.plugin_manager = SimpleNamespace(get_plugin_lock=locks.__getitem__)
    coord._state_lock = threading.Lock()
    coord._static_pause_active = False
    coord._saved_scroll_position = None
    coord._should_stop = False
    coord._live_priority_active = False
    coord._live_priority_check = None
    coord._interrupt_check = None
    coord.stats = {'static_pauses': 0}
    return coord


def _pause(coord, plugin, clock):
    """One static pause: (whether it completed, how long it lasted)."""
    start = clock.now
    completed = coord._handle_static_pause(plugin)
    return completed, clock.now - start


class TestPauseLength:
    @pytest.mark.parametrize('value, seconds', NUMBERS)
    def test_numbers_and_numeric_strings_are_used(self, clock, value, seconds):
        plugin = _plugin(value)
        completed, lasted = _pause(_coord(plugin), plugin, clock)
        assert completed is True
        assert lasted == pytest.approx(seconds, abs=0.15)

    @pytest.mark.parametrize('value', NOT_NUMBERS, ids=repr)
    def test_anything_but_a_finite_number_pauses_for_30s(self, clock, value):
        plugin = _plugin(value)
        completed, lasted = _pause(_coord(plugin), plugin, clock)
        assert completed is True
        assert lasted == pytest.approx(30.0, abs=0.15)
        plugin.display.assert_called_once_with(force_clear=True)

    @pytest.mark.parametrize('value', NOT_ABOVE_ZERO, ids=repr)
    def test_a_number_not_above_zero_pauses_for_15s(self, clock, value):
        plugin = _plugin(value)
        completed, lasted = _pause(_coord(plugin), plugin, clock)
        assert completed is True
        assert lasted == pytest.approx(15.0, abs=0.15)

    def test_a_raising_get_display_duration_pauses_for_30s(self, clock):
        plugin = _plugin(None)
        plugin.get_display_duration.side_effect = KeyError('display_duration')
        completed, lasted = _pause(_coord(plugin), plugin, clock)
        assert completed is True
        assert lasted == pytest.approx(30.0, abs=0.15)

    def test_a_good_value_after_a_bad_one_is_used(self, clock):
        plugin = _plugin(None)
        coord = _coord(plugin)
        assert _pause(coord, plugin, clock)[1] == pytest.approx(30.0, abs=0.15)
        plugin.get_display_duration.return_value = 45
        assert _pause(coord, plugin, clock)[1] == pytest.approx(45.0, abs=0.15)

    def test_the_pause_can_still_be_interrupted(self, clock):
        plugin = _plugin('twenty')
        coord = _coord(plugin)
        coord._interrupt_check = lambda: clock.now >= 5
        completed, lasted = _pause(coord, plugin, clock)
        assert completed is False
        assert lasted == pytest.approx(5.0, abs=0.15)


class TestWarning:
    def test_logged_once_per_plugin(self, clock, caplog):
        clock_plugin = _plugin('twenty')
        calendar = _plugin(None, plugin_id='calendar')
        coord = _coord(clock_plugin, calendar)
        with caplog.at_level(logging.WARNING, logger='src.vegas_mode.coordinator'):
            for _ in range(3):
                for plugin in (clock_plugin, calendar):
                    coord._handle_static_pause(plugin)
        warnings = [r.getMessage() for r in caplog.records
                    if 'display duration' in r.getMessage()]
        assert len(warnings) == 2
        assert any('clock-simple' in m and "'twenty'" in m for m in warnings)
        assert any('calendar' in m and 'None' in m for m in warnings)


class TestFiniteSeconds:
    """The shared rule: what counts as a number of seconds."""

    @pytest.mark.parametrize('value, seconds', NUMBERS + [(0, 0.0), ('-5', -5.0)])
    def test_numbers_and_numeric_strings(self, value, seconds):
        from src.plugin_system.base_plugin import finite_seconds
        result = finite_seconds(value)
        assert result == seconds and isinstance(result, float)

    @pytest.mark.parametrize('value', NOT_NUMBERS + [pytest.param(10 ** 400, id='10**400')],
                             ids=repr)
    def test_anything_else_is_none(self, value):
        from src.plugin_system.base_plugin import finite_seconds
        assert finite_seconds(value) is None


def _rotation_seconds(plugin):
    """How long the rotation shows ``plugin`` (no dynamic duration, no
    Rotation & Durations override): the two calls run() makes for a screen.
    """
    from src.display_controller import DisplayController
    dc = object.__new__(DisplayController)
    dc.config = {}
    dc.plugin_modes = {'mode': plugin}
    return dc._resolve_durations(plugin, 'mode', dc._get_display_duration('mode'), False)[1]


class TestSameAsTheRotation:
    """The pause and the rotation share finite_seconds; this pins their
    fallbacks (30 s, 15 s) to each other too."""

    @pytest.mark.parametrize('value', [value for value, _ in NUMBERS]
                             + NOT_NUMBERS + NOT_ABOVE_ZERO, ids=repr)
    def test_the_pause_lasts_as_long_as_the_rotation_shows_it(self, clock, value):
        plugin = _plugin(value)
        expected = _rotation_seconds(plugin)
        assert _pause(_coord(plugin), plugin, clock)[1] == pytest.approx(expected, abs=0.15)

    def test_a_raise_too(self, clock):
        plugin = _plugin(None)
        plugin.get_display_duration.side_effect = KeyError('display_duration')
        expected = _rotation_seconds(plugin)
        assert _pause(_coord(plugin), plugin, clock)[1] == pytest.approx(expected, abs=0.15)
