"""
Starlark app location fields default to the device's own location.

The bug this pins: a user set Charlotte, North Carolina under General settings
(and in ledmatrix-weather), but a Tidbyt weather/radar app kept showing San
Francisco -- the ``DEFAULT_LOCATION`` its author hard-coded -- because the
app's own Location field was blank and nothing filled it. There was no San
Francisco anywhere in config.json to explain it.
"""

import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.device_location import (
    FAILURE_RETRY_SECONDS,
    DeviceLocationResolver,
    apply_device_location,
    location_field_ids,
    parse_location,
    pick_geocode_result,
)

CHARLOTTE = {"lat": 35.22709, "lng": -80.84313, "timezone": "America/New_York"}
DEVICE_CONFIG = {
    "timezone": "America/Chicago",
    "location": {"city": "Charlotte", "state": "North Carolina", "country": "US"},
}
SCHEMA = {"version": "1", "schema": [
    {"typeOf": "location", "id": "location", "name": "Location"},
    {"typeOf": "text", "id": "api_key", "name": "API key"},
]}
SAVED_BROOKLYN = json.dumps({"lat": "40.6782", "lng": "-73.9442",
                             "timezone": "America/New_York"})


class FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key, max_age=300, memory_ttl=None):
        return self.store.get(key)

    def set(self, key, data, ttl=None):
        self.store[key] = data


class CountingGeocoder:
    def __init__(self, result=CHARLOTTE, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, city, state, country):
        self.calls.append((city, state, country))
        if self.error:
            raise self.error
        return self.result


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def resolver(geocoder=None, cache=None, clock=None):
    return DeviceLocationResolver(cache if cache is not None else FakeCache(),
                                  MagicMock(), geocoder or CountingGeocoder(),
                                  clock or Clock())


class TestApplyDeviceLocation:
    def test_an_unset_location_renders_at_the_device_location(self):
        out = apply_device_location({"api_key": "k"}, SCHEMA, resolver(), DEVICE_CONFIG)
        loc = json.loads(out["location"])
        assert (loc["lat"], loc["lng"]) == ("35.2271", "-80.8431")
        assert loc["locality"] == "Charlotte"
        assert out["api_key"] == "k"

    @pytest.mark.parametrize("blank", ["", "   ", None, "{}", "not json",
                                       json.dumps({"timezone": "America/Denver"})])
    def test_a_blank_or_lat_lng_less_value_counts_as_unset(self, blank):
        out = apply_device_location({"location": blank}, SCHEMA, resolver(), DEVICE_CONFIG)
        assert json.loads(out["location"])["lat"] == "35.2271"

    def test_a_saved_location_wins(self):
        geocoder = CountingGeocoder()
        out = apply_device_location({"location": SAVED_BROOKLYN}, SCHEMA,
                                    resolver(geocoder), DEVICE_CONFIG)
        assert out["location"] == SAVED_BROOKLYN
        assert geocoder.calls == [], "nothing to resolve, so no network"

    def test_the_input_is_not_mutated(self):
        config = {"location": ""}
        apply_device_location(config, SCHEMA, resolver(), DEVICE_CONFIG)
        assert config == {"location": ""}

    def test_a_timezone_typed_without_coordinates_is_kept(self):
        partial = json.dumps({"timezone": "America/Denver"})
        out = apply_device_location({"location": partial}, SCHEMA, resolver(), DEVICE_CONFIG)
        assert json.loads(out["location"])["timezone"] == "America/Denver"

    def test_the_timezone_is_the_citys_own(self):
        """The device timezone (Chicago here) is only a fallback."""
        out = apply_device_location({}, SCHEMA, resolver(), DEVICE_CONFIG)
        assert json.loads(out["location"])["timezone"] == "America/New_York"

    def test_the_device_timezone_fills_in_when_the_geocoder_has_none(self):
        geocoder = CountingGeocoder({"lat": 35.2, "lng": -80.8, "timezone": None})
        out = apply_device_location({}, SCHEMA, resolver(geocoder), DEVICE_CONFIG)
        assert json.loads(out["location"])["timezone"] == "America/Chicago"

    def test_geocode_failure_falls_back_to_the_apps_own_default(self):
        """Dropped rather than passed blank: an app decoding "" would crash."""
        failing = resolver(CountingGeocoder(error=OSError("network down")))
        out = apply_device_location({"location": "", "api_key": "k"}, SCHEMA,
                                    failing, DEVICE_CONFIG)
        assert "location" not in out
        assert out["api_key"] == "k"

    def test_no_match_falls_back_too(self):
        out = apply_device_location({}, SCHEMA, resolver(CountingGeocoder(result=None)),
                                    DEVICE_CONFIG)
        assert "location" not in out

    @pytest.mark.parametrize("device", [{}, None, {"location": {}},
                                        {"location": {"city": "  "}},
                                        {"location": "Charlotte"}])
    def test_no_device_location_falls_back(self, device):
        geocoder = CountingGeocoder()
        out = apply_device_location({}, SCHEMA, resolver(geocoder), device)
        assert "location" not in out
        assert geocoder.calls == []

    def test_an_app_without_a_location_field_is_untouched(self):
        schema = {"schema": [{"typeOf": "text", "id": "location"}]}
        out = apply_device_location({"location": ""}, schema, resolver(), DEVICE_CONFIG)
        assert out == {"location": ""}

    def test_an_app_without_a_schema_is_untouched(self):
        assert apply_device_location({"a": 1}, None, resolver(), DEVICE_CONFIG) == {"a": 1}


class TestGeocodingIsCached:
    def test_the_city_is_geocoded_once(self):
        geocoder = CountingGeocoder()
        r = resolver(geocoder)
        for _ in range(3):
            apply_device_location({}, SCHEMA, r, DEVICE_CONFIG)
        assert len(geocoder.calls) == 1

    def test_the_cache_survives_a_restart(self):
        cache = FakeCache()
        resolver(CountingGeocoder(), cache).coordinates(DEVICE_CONFIG["location"])
        geocoder = CountingGeocoder()
        coords = resolver(geocoder, cache).coordinates(DEVICE_CONFIG["location"])
        assert coords["lat"] == CHARLOTTE["lat"]
        assert geocoder.calls == []

    def test_a_new_device_city_is_looked_up(self):
        geocoder = CountingGeocoder()
        r = resolver(geocoder)
        r.coordinates({"city": "Charlotte", "state": "NC", "country": "US"})
        r.coordinates({"city": "Tampa", "state": "Florida", "country": "US"})
        assert [c[0] for c in geocoder.calls] == ["Charlotte", "Tampa"]

    def test_state_spellings_share_one_cache_entry(self):
        """"North_Carolina", "north carolina" and "NC" are the same place."""
        geocoder = CountingGeocoder()
        r = resolver(geocoder)
        for state in ("North Carolina", "North_Carolina", "NC"):
            r.coordinates({"city": "Charlotte", "state": state, "country": "US"})
        assert len(geocoder.calls) == 1

    def test_a_failure_is_not_retried_on_every_render(self):
        clock = Clock()
        geocoder = CountingGeocoder(error=OSError("down"))
        r = resolver(geocoder, clock=clock)
        for _ in range(5):
            assert r.coordinates(DEVICE_CONFIG["location"]) is None
        assert len(geocoder.calls) == 1

        clock.now += FAILURE_RETRY_SECONDS + 1
        geocoder.error = None
        assert r.coordinates(DEVICE_CONFIG["location"])["lat"] == CHARLOTTE["lat"]
        assert len(geocoder.calls) == 2

    def test_a_broken_cache_does_not_stop_the_lookup(self):
        cache = MagicMock()
        cache.get.side_effect = OSError("disk")
        cache.set.side_effect = OSError("disk")
        assert resolver(cache=cache).coordinates(DEVICE_CONFIG["location"]) is not None


class TestPickGeocodeResult:
    RESULTS = [
        {"latitude": 42.56, "longitude": -84.84, "country_code": "US", "admin1": "Michigan"},
        {"latitude": 35.23, "longitude": -80.84, "country_code": "US", "admin1": "North Carolina"},
        {"latitude": 18.34, "longitude": -64.93, "country_code": "VI", "admin1": "St Thomas"},
    ]

    @pytest.mark.parametrize("state", ["North Carolina", "north_carolina", "NC", "nc"])
    def test_the_configured_state_wins_over_the_first_hit(self, state):
        assert pick_geocode_result(self.RESULTS, state, "US")["admin1"] == "North Carolina"

    @pytest.mark.parametrize("country", ["US", "us", "USA", "United States"])
    def test_country_spellings_match(self, country):
        best = pick_geocode_result(self.RESULTS, "", country)
        assert best["country_code"] == "US"

    def test_an_unknown_state_falls_back_to_the_country(self):
        assert pick_geocode_result(self.RESULTS, "Ontario", "US")["admin1"] == "Michigan"

    def test_with_nothing_to_match_the_first_hit_is_used(self):
        assert pick_geocode_result(self.RESULTS, "", "")["admin1"] == "Michigan"

    def test_no_results(self):
        assert pick_geocode_result([], "NC", "US") is None


def test_location_field_ids_reads_both_schema_shapes():
    assert location_field_ids({"fields": [{"typeOf": "location", "id": "a"}]}) == ["a"]
    assert location_field_ids({"schema": [{"type": "Location", "id": "b"},
                                          {"typeOf": "location_based", "id": "c"}]}) == ["b"]


def test_parse_location_accepts_a_dict():
    assert parse_location({"lat": 1, "lng": 2}) == {"lat": 1, "lng": 2}
    assert parse_location({"lat": "x", "lng": 2}) is None


# ---------------------------------------------------------------------------
# The display plugin's render path
# ---------------------------------------------------------------------------

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin-repos" / "starlark-apps"


@pytest.fixture(scope="module")
def manager_module():
    if not PLUGIN_DIR.exists():
        pytest.skip("starlark-apps plugin is not checked out")
    sys.path.insert(0, str(PLUGIN_DIR))
    # See test_starlark_display_contract.py: fcntl is POSIX-only and unused here.
    injected_fcntl = "fcntl" not in sys.modules
    if injected_fcntl:
        stub = types.ModuleType("fcntl")
        stub.LOCK_EX, stub.LOCK_UN = 2, 8
        stub.flock = lambda *a, **kw: None
        sys.modules["fcntl"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "starlark_manager_location_test", PLUGIN_DIR / "manager.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:  # noqa: BLE001 - optional deps may be absent
        pytest.skip(f"starlark-apps manager is not importable here: {e}")
    finally:
        sys.path.remove(str(PLUGIN_DIR))
        if injected_fcntl:
            sys.modules.pop("fcntl", None)


def _render(manager_module, tmp_path, app_config, geocoder):
    """Run _render_app and return the config Pixlet was handed."""
    plugin_cls = manager_module.StarlarkAppsPlugin
    plugin = plugin_cls.__new__(plugin_cls)
    plugin.logger = MagicMock()
    plugin.config = {}
    plugin.calculated_magnify = 1
    plugin.pixlet = MagicMock()
    plugin.pixlet.render.return_value = (True, None)
    plugin._load_frames_from_cache = MagicMock(return_value=True)
    plugin.device_location = resolver(geocoder)
    plugin.global_config = DEVICE_CONFIG

    app_cls = manager_module.StarlarkApp
    app = app_cls.__new__(app_cls)
    app.app_id = "weather"
    app.config = dict(app_config)
    app.schema = SCHEMA
    app.star_file = tmp_path / "weather.star"
    app.cache_file = tmp_path / "cached_render.webp"
    app.last_render_time = 0

    assert plugin._render_app(app, force=True) is True
    return plugin.pixlet.render.call_args.kwargs["config"], app


class TestTheDisplayPluginRender:
    def test_an_unset_location_is_rendered_at_the_device_location(self, manager_module, tmp_path):
        config, app = _render(manager_module, tmp_path, {"render_interval": 300},
                              CountingGeocoder())
        assert json.loads(config["location"])["lat"] == "35.2271"
        assert "render_interval" not in config
        assert "location" not in app.config, "never written back to the app's config"

    def test_a_saved_location_wins(self, manager_module, tmp_path):
        config, _ = _render(manager_module, tmp_path, {"location": SAVED_BROOKLYN},
                            CountingGeocoder())
        assert config["location"] == SAVED_BROOKLYN

    def test_geocode_failure_still_renders_with_the_apps_default(self, manager_module, tmp_path):
        config, _ = _render(manager_module, tmp_path, {"location": ""},
                            CountingGeocoder(error=OSError("down")))
        assert "location" not in config
