"""Tests that live content can take extra turns inside the Vegas ticker.

Vegas was a strict round robin -- every plugin exactly once per cycle -- and
live content did not appear in it at all, because the display controller
refused to run the ticker while anything was live. With a dozen plugins
enabled that left a live score either absent or minutes stale.

Two things change, both off by default. `live_in_ticker` keeps the marquee
running instead of yielding to a full-screen takeover, and the rotation is
expanded by Smooth Weighted Round-Robin so a weighted plugin gets several
slots per cycle, spaced through it rather than clumped.

Weights are per plugin, not per game: a scoreboard showing four live games
still occupies one slot at a time and rotates its own games within it.
"""

from unittest.mock import Mock

import pytest

from src.vegas_mode.config import VegasModeConfig
from src.vegas_mode.stream_manager import StreamManager


class FakePlugin:
    def __init__(self, live=False, declared=None, raises=False):
        self._live = live
        self._declared = declared
        self._raises = raises
        self.enabled = True

    def has_live_priority(self):
        if self._raises:
            raise RuntimeError("plugin blew up")
        return self._live

    def has_live_content(self):
        return self._live

    def get_vegas_priority_weight(self):
        if self._raises:
            raise RuntimeError("plugin blew up")
        return self._declared


def _manager(plugins, **cfg):
    config = VegasModeConfig(live_in_ticker=cfg.pop('live_in_ticker', True), **cfg)
    pm = Mock()
    pm.plugins = plugins
    sm = StreamManager.__new__(StreamManager)
    sm.config = config
    sm.plugin_manager = pm
    return sm


def _counts(schedule):
    return {p: schedule.count(p) for p in set(schedule)}


def _max_gap(schedule, plugin_id):
    """Largest gap between consecutive appearances, wrapping around."""
    at = [i for i, p in enumerate(schedule) if p == plugin_id]
    if len(at) < 2:
        return len(schedule)
    gaps = [b - a for a, b in zip(at, at[1:])]
    gaps.append(len(schedule) - at[-1] + at[0])
    return max(gaps)


class TestWeightsComeFromTheRightPlace:
    def test_a_quiet_plugin_gets_one_slot(self):
        sm = _manager({'clock': FakePlugin()})
        assert sm._plugin_weight('clock') == 1

    def test_live_content_earns_the_configured_weight(self):
        sm = _manager({'mlb': FakePlugin(live=True)}, live_weight=4)
        assert sm._plugin_weight('mlb') == 4

    def test_a_plugin_may_answer_for_itself(self):
        # The only route for favorite-team awareness: the core can see that a
        # game is live, not whose.
        sm = _manager({'mlb': FakePlugin(live=True, declared=7)}, live_weight=3)
        assert sm._plugin_weight('mlb') == 7

    def test_declaring_none_defers_to_the_core(self):
        sm = _manager({'mlb': FakePlugin(live=True, declared=None)}, live_weight=3)
        assert sm._plugin_weight('mlb') == 3

    def test_a_declared_weight_is_clamped(self):
        sm = _manager({'a': FakePlugin(declared=99), 'b': FakePlugin(declared=0)})
        assert sm._plugin_weight('a') == 10
        assert sm._plugin_weight('b') == 1

    def test_a_plugin_that_raises_does_not_break_the_rotation(self):
        sm = _manager({'bad': FakePlugin(raises=True)})
        assert sm._plugin_weight('bad') == 1

    def test_an_unknown_plugin_weighs_one(self):
        assert _manager({})._plugin_weight('ghost') == 1


class TestTheSchedule:
    def test_nothing_weighted_leaves_the_order_untouched(self):
        order = ['weather', 'clock', 'news']
        sm = _manager({p: FakePlugin() for p in order})
        assert sm._apply_priority_weights(order) == order

    def test_off_by_default_the_order_is_untouched(self):
        order = ['weather', 'mlb', 'news']
        sm = _manager({'weather': FakePlugin(), 'mlb': FakePlugin(live=True),
                       'news': FakePlugin()}, live_in_ticker=False, live_weight=3)
        assert sm._apply_priority_weights(order) == order

    def test_a_live_plugin_takes_its_share_of_slots(self):
        order = ['weather', 'mlb', 'news', 'clock']
        sm = _manager({'weather': FakePlugin(), 'mlb': FakePlugin(live=True),
                       'news': FakePlugin(), 'clock': FakePlugin()},
                      live_weight=3)
        schedule = sm._apply_priority_weights(order)
        counts = _counts(schedule)
        assert counts['mlb'] == 3, counts
        assert counts['weather'] == counts['news'] == counts['clock'] == 1, counts
        assert len(schedule) == 6

    def test_every_plugin_still_appears(self):
        # A boost must not starve anything out of the cycle.
        order = ['a', 'b', 'c', 'd', 'e', 'f']
        plugins = {p: FakePlugin() for p in order}
        plugins['a'] = FakePlugin(live=True, declared=10)
        sm = _manager(plugins)
        schedule = sm._apply_priority_weights(order)
        assert set(schedule) == set(order), set(order) - set(schedule)

    def test_repeats_are_spread_not_clumped(self):
        # The point of Smooth Weighted Round-Robin. Three-in-a-row followed by
        # a long silence would be worse than not boosting at all.
        order = ['weather', 'mlb', 'news', 'clock', 'stocks', 'f1']
        plugins = {p: FakePlugin() for p in order}
        plugins['mlb'] = FakePlugin(live=True)
        sm = _manager(plugins, live_weight=3)
        schedule = sm._apply_priority_weights(order)

        assert _counts(schedule)['mlb'] == 3
        # Evenly spread over 8 slots means a gap of about 3, never 6.
        assert _max_gap(schedule, 'mlb') <= 4, schedule
        # And never twice running.
        assert not any(a == b == 'mlb' for a, b in zip(schedule, schedule[1:])), schedule

    def test_a_favorite_outranks_another_live_game(self):
        order = ['weather', 'mlb', 'nhl']
        sm = _manager({'weather': FakePlugin(),
                       'mlb': FakePlugin(live=True, declared=5),
                       'nhl': FakePlugin(live=True)}, live_weight=2)
        counts = _counts(sm._apply_priority_weights(order))
        assert counts['mlb'] == 5 and counts['nhl'] == 2 and counts['weather'] == 1, counts

    def test_an_empty_rotation_is_harmless(self):
        assert _manager({})._apply_priority_weights([]) == []


class TestConfigParsing:
    def test_defaults_preserve_todays_behaviour(self):
        cfg = VegasModeConfig.from_config({})
        assert cfg.live_in_ticker is False
        assert cfg.live_weight == 3 and cfg.favorite_live_weight == 5

    @pytest.mark.parametrize("given,expected", [(0, 1), (-4, 1), (99, 10), (4, 4)])
    def test_weights_are_clamped(self, given, expected):
        cfg = VegasModeConfig.from_config(
            {'display': {'vegas_scroll': {'live_weight': given}}})
        assert cfg.live_weight == expected
