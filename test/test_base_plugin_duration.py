"""
Tests for BasePlugin.get_display_duration — ~100 lines of type coercion that
every plugin's rotation slot depends on, previously untested.

The contract: a positive number wins wherever it comes from; everything else
falls through instance attr → config → the 15.0 default, logging on the way.
"""

from unittest.mock import MagicMock

import pytest

from src.plugin_system.base_plugin import BasePlugin


class _MinimalPlugin(BasePlugin):
    def update(self):
        pass

    def display(self, force_clear=False):
        pass


def make_plugin(config=None, instance_duration="__unset__"):
    plugin = _MinimalPlugin(
        plugin_id="duration-test",
        config=config or {},
        display_manager=MagicMock(),
        cache_manager=MagicMock(),
        plugin_manager=MagicMock(),
    )
    if instance_duration != "__unset__":
        plugin.display_duration = instance_duration
    return plugin


class TestInstanceVariable:
    def test_positive_int_wins(self):
        assert make_plugin(instance_duration=30).get_display_duration() == 30.0

    def test_positive_float_wins(self):
        assert make_plugin(instance_duration=12.5).get_display_duration() == 12.5

    def test_returns_float_type(self):
        result = make_plugin(instance_duration=30).get_display_duration()
        assert isinstance(result, float)

    def test_numeric_string_wins(self):
        assert make_plugin(instance_duration="25").get_display_duration() == 25.0

    def test_zero_falls_through_to_config(self):
        plugin = make_plugin(config={"display_duration": 20},
                             instance_duration=0)
        assert plugin.get_display_duration() == 20.0

    def test_negative_falls_through_to_config(self):
        plugin = make_plugin(config={"display_duration": 20},
                             instance_duration=-5)
        assert plugin.get_display_duration() == 20.0

    def test_none_falls_through_to_config(self):
        plugin = make_plugin(config={"display_duration": 20},
                             instance_duration=None)
        assert plugin.get_display_duration() == 20.0

    def test_garbage_string_falls_through(self):
        plugin = make_plugin(config={"display_duration": 20},
                             instance_duration="abc")
        assert plugin.get_display_duration() == 20.0

    def test_non_positive_string_falls_through(self):
        plugin = make_plugin(config={"display_duration": 20},
                             instance_duration="0")
        assert plugin.get_display_duration() == 20.0

    def test_unexpected_type_falls_through(self):
        plugin = make_plugin(config={"display_duration": 20},
                             instance_duration=[30])
        assert plugin.get_display_duration() == 20.0

    def test_bool_true_is_one_second(self):
        # Characterized quirk: bool is an int subclass, so display_duration =
        # True passes the isinstance((int, float)) branch and returns 1.0.
        assert make_plugin(instance_duration=True).get_display_duration() == 1.0


class TestConfigFallback:
    def test_config_number(self):
        assert make_plugin({"display_duration": 20}).get_display_duration() == 20.0

    def test_config_numeric_string(self):
        assert make_plugin({"display_duration": "12.5"}).get_display_duration() == 12.5

    def test_missing_config_uses_default(self):
        assert make_plugin({}).get_display_duration() == 15.0

    def test_config_zero_uses_default(self):
        assert make_plugin({"display_duration": 0}).get_display_duration() == 15.0

    def test_config_negative_uses_default(self):
        assert make_plugin({"display_duration": -10}).get_display_duration() == 15.0

    def test_config_garbage_string_uses_default(self):
        assert make_plugin({"display_duration": "soon"}).get_display_duration() == 15.0

    def test_config_unexpected_type_uses_default(self):
        assert make_plugin({"display_duration": {"s": 5}}).get_display_duration() == 15.0

    def test_config_none_uses_default(self):
        assert make_plugin({"display_duration": None}).get_display_duration() == 15.0
