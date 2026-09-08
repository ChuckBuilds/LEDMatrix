"""The Home Assistant MQTT bridge, without a broker or a matrix.

The bridge is a translation layer: one MQTT payload in, one api_v3 call out.
Everything worth pinning is on that path -- attaching the plugin_id a mode
needs, rejecting values HA can produce but the API cannot take, and the
discovery configs HA reads once and caches -- so it is all reachable with a
fake API client and a pure function.
"""

import importlib.util
import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BRIDGE_PATH = (Path(__file__).resolve().parent.parent
               / "integrations" / "mqtt_bridge" / "ledmatrix_mqtt_bridge.py")


@pytest.fixture(scope="module")
def bridge_module():
    if not BRIDGE_PATH.exists():
        pytest.skip("mqtt bridge is not present")
    try:
        spec = importlib.util.spec_from_file_location("ledmatrix_mqtt_bridge", BRIDGE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    except ImportError as e:
        pytest.skip(f"mqtt bridge dependencies are not installed here: {e}")
    finally:
        sys.modules.pop("ledmatrix_mqtt_bridge", None)


MODES = [
    {"mode": "clock-simple", "plugin_id": "clock-simple",
     "plugin_name": "Simple Clock", "name": "Simple Clock", "enabled": True},
    {"mode": "nfl_live", "plugin_id": "football-scoreboard",
     "plugin_name": "Football Scoreboard", "name": "nfl_live", "enabled": True},
    {"mode": "aquarium", "plugin_id": "starlark-apps",
     "plugin_name": "Starlark Apps", "name": "aquarium", "enabled": True},
]


@pytest.fixture
def client():
    """A stand-in for LEDMatrixClient that records what it was asked to do."""
    fake = MagicMock()
    fake.list_modes.return_value = list(MODES)
    fake.start_on_demand.return_value = {"request_id": "r1"}
    fake.stop_on_demand.return_value = {}
    fake.set_power.return_value = {}
    fake.set_brightness.return_value = {}
    fake.get_brightness.return_value = 90
    fake.display_status.return_value = {
        "service": {"active": True},
        "state": {"active": True, "mode": "nfl_live"},
    }
    return fake


@pytest.fixture
def handler(bridge_module, client):
    h = bridge_module.CommandHandler(client)
    h.refresh_modes()
    return h


class TestDisplayCommands:
    def test_a_mode_is_forced_on_demand(self, handler, client):
        result = handler.handle({"action": "display", "mode": "nfl_live"})
        assert result["status"] == "success"
        assert client.start_on_demand.call_args.kwargs["mode"] == "nfl_live"

    def test_the_owning_plugin_is_sent_with_the_mode(self, handler, client):
        """on-demand/start's find_plugin_for_mode fallback cannot resolve a
        generated mode, so an omitted plugin_id 404s for every Starlark app."""
        handler.handle({"action": "display", "mode": "aquarium"})
        assert client.start_on_demand.call_args.kwargs["plugin_id"] == "starlark-apps"

    def test_a_home_assistant_label_resolves_to_its_mode(self, handler, client):
        """The select entity shows labels, so that is what comes back."""
        handler.handle({"action": "display", "mode": "Simple Clock"})
        assert client.start_on_demand.call_args.kwargs["mode"] == "clock-simple"

    def test_an_unknown_mode_refreshes_before_giving_up(self, handler, client):
        """A plugin installed since the last refresh is the usual reason."""
        client.list_modes.return_value = MODES + [
            {"mode": "new_app", "plugin_id": "starlark-apps", "name": "new_app"}]
        handler.handle({"action": "display", "mode": "new_app"})
        assert client.start_on_demand.call_args.kwargs["plugin_id"] == "starlark-apps"

    def test_an_explicit_plugin_id_is_respected(self, handler, client):
        handler.handle({"action": "display", "mode": "x", "plugin_id": "custom"})
        assert client.start_on_demand.call_args.kwargs["plugin_id"] == "custom"

    def test_duration_and_pinned_are_passed_through(self, handler, client):
        handler.handle({"action": "display", "mode": "aquarium",
                        "duration": 300, "pinned": True})
        kwargs = client.start_on_demand.call_args.kwargs
        assert kwargs["duration"] == 300 and kwargs["pinned"] is True

    def test_display_with_nothing_to_show_is_rejected(self, handler, client):
        result = handler.handle({"action": "display"})
        assert result["status"] == "error"
        client.start_on_demand.assert_not_called()

    def test_stop_returns_to_normal_rotation(self, handler, client):
        assert handler.handle({"action": "stop_display"})["status"] == "success"
        client.stop_on_demand.assert_called_once()


class TestPowerAndBrightness:
    @pytest.mark.parametrize("state,expected", [("on", True), ("OFF", False), (" On ", True)])
    def test_power_states_are_accepted(self, handler, client, state, expected):
        assert handler.handle({"action": "power", "state": state})["status"] == "success"
        client.set_power.assert_called_with(expected)

    def test_an_unknown_power_state_is_rejected(self, handler, client):
        assert handler.handle({"action": "power", "state": "maybe"})["status"] == "error"
        client.set_power.assert_not_called()

    def test_brightness_is_applied(self, handler, client):
        assert handler.handle({"action": "brightness", "value": 75})["status"] == "success"
        client.set_brightness.assert_called_with(75)

    def test_a_float_from_home_assistant_is_accepted(self, handler, client):
        """The number entity publishes "75.0"; int() would raise on that."""
        handler.handle({"action": "brightness", "value": "75.0"})
        client.set_brightness.assert_called_with(75)

    @pytest.mark.parametrize("value", [-1, 101, "bright", None])
    def test_out_of_range_brightness_never_reaches_the_api(self, handler, client, value):
        assert handler.handle({"action": "brightness", "value": value})["status"] == "error"
        client.set_brightness.assert_not_called()


class TestFailuresAreReportedNotRaised:
    """A failed command must publish an error, not kill the MQTT loop."""

    def test_an_api_error_becomes_an_error_result(self, handler, client):
        client.start_on_demand.side_effect = RuntimeError("Display service is not running")
        result = handler.handle({"action": "display", "mode": "nfl_live"})
        assert result["status"] == "error"
        assert "not running" in result["message"]

    def test_an_unknown_action_lists_the_known_ones(self, handler):
        result = handler.handle({"action": "explode"})
        assert result["status"] == "error"
        assert "brightness" in result["message"]

    def test_a_missing_action_is_an_error(self, handler):
        assert handler.handle({})["status"] == "error"


class TestDiscoveryPayloads:
    """HA reads these once and caches them; a wrong shape is a dead entity."""

    @pytest.fixture
    def messages(self, bridge_module):
        return bridge_module.discovery_messages(
            "ledmatrix/command", "ledmatrix/command/state",
            "ledmatrix/command/availability", ["Simple Clock", "nfl_live"])

    def test_every_entity_is_published(self, messages):
        components = {m["topic"].split("/")[1] for m in messages}
        assert components == {"select", "button", "switch", "number"}

    def test_each_entity_has_a_stable_unique_id(self, messages):
        """Without one HA cannot let a user rename or reassign the entity."""
        ids = [m["payload"]["unique_id"] for m in messages]
        assert len(ids) == len(set(ids)) and all(ids)

    def test_the_select_offers_the_modes_it_was_given(self, messages):
        select = next(m for m in messages if "/select/" in m["topic"])
        assert select["payload"]["options"] == ["Simple Clock", "nfl_live"]

    def test_every_entity_shares_the_availability_topic(self, messages):
        """It is also the bridge's last will, so HA greys the controls out
        when the bridge dies rather than leaving them silently inert."""
        assert all(m["payload"]["availability_topic"] == "ledmatrix/command/availability"
                   for m in messages)

    def test_every_entity_belongs_to_one_device(self, messages):
        assert all(m["payload"]["device"]["identifiers"] == ["ledmatrix"] for m in messages)

    def test_the_command_templates_are_valid_json_the_handler_accepts(self, messages, handler):
        """A template that renders malformed JSON fails only at runtime, in HA."""
        select = next(m for m in messages if "/select/" in m["topic"])
        rendered = select["payload"]["command_template"].replace("{{ value }}", "nfl_live")
        assert handler.handle(json.loads(rendered))["status"] == "success"

        number = next(m for m in messages if "/number/" in m["topic"])
        rendered = number["payload"]["command_template"].replace("{{ value }}", "40")
        assert handler.handle(json.loads(rendered))["status"] == "success"

    def test_the_switch_payloads_are_valid_json_the_handler_accepts(self, messages, handler):
        switch = next(m for m in messages if "/switch/" in m["topic"])
        for key in ("payload_on", "payload_off"):
            assert handler.handle(json.loads(switch["payload"][key]))["status"] == "success"

    def test_the_button_payload_is_valid_json_the_handler_accepts(self, messages, handler):
        button = next(m for m in messages if "/button/" in m["topic"])
        assert handler.handle(json.loads(button["payload"]["payload_press"]))["status"] == "success"


class TestStateReporting:
    def test_state_reflects_the_running_display(self, bridge_module, client):
        state = bridge_module.read_state(client)
        assert state == {"power": True, "mode": "nfl_live", "brightness": 90}

    def test_normal_rotation_reports_no_forced_mode(self, bridge_module, client):
        client.display_status.return_value = {
            "service": {"active": True}, "state": {"active": False}}
        assert bridge_module.read_state(client)["mode"] is None

    def test_an_unreachable_field_does_not_blank_the_others(self, bridge_module, client):
        """A stopped display service still has a brightness worth showing."""
        client.display_status.side_effect = RuntimeError("connection refused")
        state = bridge_module.read_state(client)
        assert state["brightness"] == 90 and state["power"] is False


class TestConfigLoading:
    def test_defaults_apply_when_no_file_exists(self, bridge_module, tmp_path):
        config = bridge_module.load_config(str(tmp_path / "absent.json"))
        assert config["mqtt_topic"] == "ledmatrix/command"
        assert config["mqtt_port"] == 1883

    def test_the_file_overrides_defaults(self, bridge_module, tmp_path):
        path = tmp_path / "bridge_config.json"
        path.write_text(json.dumps({"mqtt_host": "broker.local", "mqtt_port": "8883"}))
        config = bridge_module.load_config(str(path))
        assert config["mqtt_host"] == "broker.local"
        assert config["mqtt_port"] == 8883, "a port read from JSON must still be an int"

    def test_the_environment_overrides_the_file(self, bridge_module, tmp_path, monkeypatch):
        """So a password need not sit in a file the service user can read."""
        path = tmp_path / "bridge_config.json"
        path.write_text(json.dumps({"mqtt_password": "from-file"}))
        monkeypatch.setenv("LEDMATRIX_MQTT_MQTT_PASSWORD", "from-env")
        assert bridge_module.load_config(str(path))["mqtt_password"] == "from-env"

    def test_the_example_placeholder_password_is_refused(self, bridge_module, tmp_path):
        """Copying the example unedited must fail loudly, not silently fail to
        authenticate against the broker."""
        path = tmp_path / "bridge_config.json"
        path.write_text(json.dumps(
            {"mqtt_password": "REPLACE_WITH_YOUR_ACTUAL_MQTT_PASSWORD"}))
        with pytest.raises(bridge_module.ConfigError):
            bridge_module.load_config(str(path))

    def test_malformed_json_is_a_clear_error(self, bridge_module, tmp_path):
        path = tmp_path / "bridge_config.json"
        path.write_text("{not json")
        with pytest.raises(bridge_module.ConfigError):
            bridge_module.load_config(str(path))

    def test_a_non_numeric_port_is_a_clear_error(self, bridge_module, tmp_path):
        path = tmp_path / "bridge_config.json"
        path.write_text(json.dumps({"mqtt_port": "not-a-port"}))
        with pytest.raises(bridge_module.ConfigError):
            bridge_module.load_config(str(path))


class TestTheExampleConfigIsSecureByDefault:
    """The installer copies bridge_config.example.json verbatim on first run.

    Without TLS the broker password and every display command cross the network
    in cleartext, so the shipped default has to be the safe one -- a plaintext
    broker is a deliberate edit, not something you get by not reading.
    """

    @pytest.fixture
    def example(self):
        path = (Path(__file__).resolve().parent.parent
                / "integrations" / "mqtt_bridge" / "bridge_config.example.json")
        if not path.exists():
            pytest.skip("mqtt bridge example config is not present")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_tls_is_on(self, example):
        assert example["mqtt_tls"] is True

    def test_the_port_is_the_tls_one(self, example):
        """1883 with mqtt_tls on would just fail to connect."""
        assert example["mqtt_port"] == 8883

    def test_certificate_verification_is_not_disabled(self, example):
        assert example.get("mqtt_tls_insecure", False) is False

    def test_no_password_ships_in_the_example(self, example):
        assert example["mqtt_password"] is None

    def test_the_example_loads(self, bridge_module, tmp_path):
        """It is copied verbatim, so it must survive load_config."""
        path = (Path(__file__).resolve().parent.parent
                / "integrations" / "mqtt_bridge" / "bridge_config.example.json")
        config = bridge_module.load_config(str(path))
        assert config["mqtt_tls"] is True and config["mqtt_port"] == 8883


class TestCleartextIsCalledOut:
    """Turning TLS off is allowed -- the Mosquitto add-on is plaintext on 1883 --
    but it should not be silent when a password is going over it."""

    def test_a_password_without_tls_warns(self, bridge_module, caplog):
        with caplog.at_level(logging.WARNING):
            warned = bridge_module.warn_if_cleartext(
                {"mqtt_tls": False, "mqtt_password": "hunter2"})
        assert warned is True
        assert "unencrypted" in caplog.text

    def test_the_warning_does_not_repeat_the_password(self, bridge_module, caplog):
        with caplog.at_level(logging.WARNING):
            bridge_module.warn_if_cleartext({"mqtt_tls": False, "mqtt_password": "hunter2"})
        assert "hunter2" not in caplog.text

    def test_no_password_means_nothing_to_lose(self, bridge_module):
        assert bridge_module.warn_if_cleartext(
            {"mqtt_tls": False, "mqtt_password": None}) is False

    def test_tls_on_does_not_warn(self, bridge_module):
        assert bridge_module.warn_if_cleartext(
            {"mqtt_tls": True, "mqtt_password": "hunter2"}) is False
