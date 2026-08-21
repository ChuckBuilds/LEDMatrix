"""
Tests for the device-location default: a plugin that ships a location field in
its schema must default to the device's configured City/State/Country, not to
whatever place the plugin author hard-coded.

The bug this pins: ledmatrix-weather ships ``"location_city": "Dallas"`` as a
schema default, so a user who set Kansas City under General settings but never
opened the weather plugin's own config form got Dallas weather — and a radar
centred on Dallas — with nothing in config.json to explain it.
"""

import json

import pytest

from src.plugin_system.schema_manager import SchemaManager


class FakeConfigManager:
    """Minimal stand-in exposing the load_config() SchemaManager relies on."""

    def __init__(self, config):
        self.config = config
        self.load_count = 0

    def load_config(self):
        self.load_count += 1
        return self.config


class ExplodingConfigManager:
    def load_config(self):
        raise OSError("config.json is unreadable")


WEATHER_SCHEMA = {
    "type": "object",
    "properties": {
        "location_city": {"type": "string", "default": "Dallas"},
        "location_state": {"type": "string", "default": "Texas"},
        "location_country": {"type": "string", "default": "US"},
        "units": {"type": "string", "default": "imperial"},
    },
}


def write_plugin(plugins_dir, plugin_id, schema):
    plugin_dir = plugins_dir / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "config_schema.json").write_text(json.dumps(schema))
    return plugin_dir


@pytest.fixture
def plugins_dir(tmp_path):
    d = tmp_path / "plugin-repos"
    d.mkdir()
    return d


def make_sm(plugins_dir, tmp_path, location):
    config = {} if location is None else {"location": location}
    cm = FakeConfigManager(config)
    sm = SchemaManager(plugins_dir=plugins_dir, project_root=tmp_path,
                       config_manager=cm)
    return sm, cm


class TestDeviceLocationDefaults:
    def test_device_location_replaces_plugin_default(self, plugins_dir, tmp_path):
        write_plugin(plugins_dir, "ledmatrix-weather", WEATHER_SCHEMA)
        sm, _ = make_sm(plugins_dir, tmp_path,
                        {"city": "Kansas City", "state": "Missouri", "country": "US"})

        defaults = sm.generate_default_config("ledmatrix-weather")

        assert defaults["location_city"] == "Kansas City"
        assert defaults["location_state"] == "Missouri"
        assert defaults["location_country"] == "US"
        # Non-location defaults are untouched.
        assert defaults["units"] == "imperial"

    def test_user_set_plugin_value_still_wins(self, plugins_dir, tmp_path):
        write_plugin(plugins_dir, "ledmatrix-weather", WEATHER_SCHEMA)
        sm, _ = make_sm(plugins_dir, tmp_path,
                        {"city": "Kansas City", "state": "Missouri", "country": "US"})

        defaults = sm.generate_default_config("ledmatrix-weather")
        merged = sm.merge_with_defaults({"location_city": "Denver"}, defaults)

        assert merged["location_city"] == "Denver"
        # Fields the user did not override still follow the device.
        assert merged["location_state"] == "Missouri"

    def test_blank_and_missing_device_fields_leave_schema_default(self, plugins_dir, tmp_path):
        write_plugin(plugins_dir, "ledmatrix-weather", WEATHER_SCHEMA)
        sm, _ = make_sm(plugins_dir, tmp_path, {"city": "Kansas City", "state": "   "})

        defaults = sm.generate_default_config("ledmatrix-weather")

        assert defaults["location_city"] == "Kansas City"
        assert defaults["location_state"] == "Texas"    # blank -> not configured
        assert defaults["location_country"] == "US"     # absent -> schema default

    def test_no_device_location_configured_is_a_no_op(self, plugins_dir, tmp_path):
        write_plugin(plugins_dir, "ledmatrix-weather", WEATHER_SCHEMA)
        sm, _ = make_sm(plugins_dir, tmp_path, None)

        defaults = sm.generate_default_config("ledmatrix-weather")

        assert defaults["location_city"] == "Dallas"

    def test_no_config_manager_is_a_no_op(self, plugins_dir, tmp_path):
        write_plugin(plugins_dir, "ledmatrix-weather", WEATHER_SCHEMA)
        sm = SchemaManager(plugins_dir=plugins_dir, project_root=tmp_path)

        assert sm.generate_default_config("ledmatrix-weather")["location_city"] == "Dallas"

    def test_unreadable_config_falls_back_to_schema_defaults(self, plugins_dir, tmp_path):
        write_plugin(plugins_dir, "ledmatrix-weather", WEATHER_SCHEMA)
        sm = SchemaManager(plugins_dir=plugins_dir, project_root=tmp_path,
                           config_manager=ExplodingConfigManager())

        assert sm.generate_default_config("ledmatrix-weather")["location_city"] == "Dallas"


class TestScopedToNamespacedKeys:
    def test_bare_state_key_is_not_rewritten(self, plugins_dir, tmp_path):
        """ledmatrix-elections' ``state`` is a two-letter code, not a place name."""
        write_plugin(plugins_dir, "ledmatrix-elections", {
            "type": "object",
            "properties": {
                "state": {"type": "string", "default": "CA"},
                "city": {"type": "string", "default": "Springfield"},
            },
        })
        sm, _ = make_sm(plugins_dir, tmp_path,
                        {"city": "Kansas City", "state": "Missouri", "country": "US"})

        defaults = sm.generate_default_config("ledmatrix-elections")

        assert defaults["state"] == "CA"
        assert defaults["city"] == "Springfield"

    def test_plugin_without_location_fields_never_reads_config(self, plugins_dir, tmp_path):
        write_plugin(plugins_dir, "clock-simple", {
            "type": "object",
            "properties": {"format": {"type": "string", "default": "12h"}},
        })
        sm, cm = make_sm(plugins_dir, tmp_path, {"city": "Kansas City"})

        defaults = sm.generate_default_config("clock-simple")

        assert defaults["format"] == "12h"
        assert cm.load_count == 0


class TestCachingStaysFresh:
    def test_location_change_is_picked_up_through_the_defaults_cache(self, plugins_dir, tmp_path):
        write_plugin(plugins_dir, "ledmatrix-weather", WEATHER_SCHEMA)
        sm, cm = make_sm(plugins_dir, tmp_path, {"city": "Kansas City"})

        assert sm.generate_default_config("ledmatrix-weather")["location_city"] == "Kansas City"

        cm.config["location"]["city"] = "Omaha"

        # Second call is served from the defaults cache, but must not serve a
        # stale location.
        assert sm.generate_default_config("ledmatrix-weather")["location_city"] == "Omaha"

    def test_cached_defaults_are_not_mutated_by_the_overlay(self, plugins_dir, tmp_path):
        write_plugin(plugins_dir, "ledmatrix-weather", WEATHER_SCHEMA)
        sm, cm = make_sm(plugins_dir, tmp_path, {"city": "Kansas City"})

        sm.generate_default_config("ledmatrix-weather")
        assert sm._defaults_cache["ledmatrix-weather"]["location_city"] == "Dallas"

        cm.config.pop("location")
        assert sm.generate_default_config("ledmatrix-weather")["location_city"] == "Dallas"
