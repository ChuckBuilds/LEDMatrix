"""GET /config/main must not hand out credentials.

The endpoint returned the raw config to anyone who could reach the port, and
this web interface has no authentication of any kind. Measured against a live
rig, an unauthenticated request returned:

    github.api_token                40 chars
    incoming-packages.ha_token     183 chars
    jellyfin-now-playing.api_key    32 chars
    ledmatrix-weather.api_key       32 chars
    on-air.mqtt_password             8 chars
    youtube.api_key                 20 chars
    youtube-stats.api_key           39 chars

A GitHub token and a Home Assistant long-lived token among them.

The x-secret masking the plugin config endpoints use does not apply here: this
endpoint never consults a schema, and core keys such as github.api_token have
no schema to carry the marker. Several of those fields *are* tagged x-secret in
their plugin's schema and were still returned in full, which is what makes the
schema route the wrong one to rely on for this endpoint.

Matching on field name is blunt. For a whole-config dump it is the right
default: anything named like a credential should not leave the process, and a
new plugin that adds a differently-shaped secret is covered without anyone
remembering to tag it.
"""
import pytest

from web_interface.blueprints.api_v3 import (
    _looks_like_a_credential,
    _redact_credentials,
)


@pytest.mark.parametrize("name", [
    "password", "mqtt_password", "opensky_password", "passwd",
    "api_key", "apikey", "API_KEY", "flightaware_api_key",
    "token", "ha_token", "api_token", "access_token",
    "secret", "client_secret", "spotify_client_secret",
    "access_key", "private_key",
])
def test_credential_names_are_recognised(name):
    assert _looks_like_a_credential(name)


@pytest.mark.parametrize("name", [
    "timezone", "city", "brightness", "enabled", "update_interval",
    "favorite_teams", "display_duration", "keyword",
])
def test_ordinary_names_are_left_alone(name):
    assert not _looks_like_a_credential(name)


def test_the_measured_leak_is_closed():
    """The exact shape taken off the rig."""
    config = {
        "github": {"api_token": "ghp_" + "x" * 36},
        "incoming-packages": {"ha_token": "y" * 183, "enabled": True},
        "jellyfin-now-playing": {"api_key": "z" * 32},
        "on-air": {"mqtt_password": "hunter22"},
        "youtube": {"api_key": "k" * 20},
        "timezone": "America/New_York",
    }
    out = _redact_credentials(config)
    assert out["github"]["api_token"] == ""
    assert out["incoming-packages"]["ha_token"] == ""
    assert out["jellyfin-now-playing"]["api_key"] == ""
    assert out["on-air"]["mqtt_password"] == ""
    assert out["youtube"]["api_key"] == ""
    # Everything else survives, or the config editor breaks.
    assert out["timezone"] == "America/New_York"
    assert out["incoming-packages"]["enabled"] is True


def test_nested_and_listed_credentials_are_reached():
    config = {"a": {"b": {"c": {"password": "p"}}},
              "feeds": [{"name": "x", "api_key": "k"}, {"name": "y"}]}
    out = _redact_credentials(config)
    assert out["a"]["b"]["c"]["password"] == ""
    assert out["feeds"][0]["api_key"] == ""
    assert out["feeds"][0]["name"] == "x"


def test_the_original_is_not_mutated():
    """The caller holds the live config; redaction must not edit it in place."""
    config = {"github": {"api_token": "keepme"}}
    _redact_credentials(config)
    assert config["github"]["api_token"] == "keepme"


def test_a_credential_shaped_container_is_still_walked():
    """`secrets: {...}` is a section name, not a value to blank."""
    config = {"secrets": {"api_key": "k", "note": "keep"}}
    out = _redact_credentials(config)
    assert out["secrets"]["api_key"] == ""
    assert out["secrets"]["note"] == "keep"


def test_non_dict_input_passes_through():
    assert _redact_credentials("plain") == "plain"
    assert _redact_credentials(7) == 7
    assert _redact_credentials(None) is None


def test_the_endpoint_itself_redacts():
    """Through the view function, not the helper.

    The helper tests above all passed with the route still returning
    `config` -- reverting the one line that calls the redactor changed
    nothing, because nothing exercised the route. A property asserted on a
    helper is not a property asserted on the endpoint, and it is the endpoint
    that is exposed to the network.
    """
    import json as _json
    from unittest.mock import MagicMock

    import flask

    from web_interface.blueprints import api_v3 as mod

    raw = {"github": {"api_token": "ghp_secret_value"},
           "timezone": "America/New_York"}

    manager = MagicMock()
    manager.load_config.return_value = raw
    previous = getattr(mod.api_v3, "config_manager", None)
    mod.api_v3.config_manager = manager

    app = flask.Flask(__name__)
    try:
        with app.test_request_context("/config/main"):
            response = mod.get_main_config()
        payload = response.get_json() if hasattr(response, "get_json") else _json.loads(response[0].data)
    finally:
        mod.api_v3.config_manager = previous

    data = payload["data"]
    assert data["github"]["api_token"] == "", (
        "the endpoint returned the token; the redactor is not wired in")
    assert data["timezone"] == "America/New_York"
    # And the config the manager handed over is untouched.
    assert raw["github"]["api_token"] == "ghp_secret_value"
