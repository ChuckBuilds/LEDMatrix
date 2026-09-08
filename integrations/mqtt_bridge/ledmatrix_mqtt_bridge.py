#!/usr/bin/env python3
"""Control a LEDMatrix display from Home Assistant over MQTT.

The bridge owns no display logic. It subscribes to one command topic and
turns each message into a call against the same api_v3 routes the web UI
uses, so behaviour stays in one place and this stays a translation layer.

On connect it publishes Home Assistant MQTT Discovery config, so a matrix
appears in HA as real entities rather than something you drive with
`mqtt.publish` by hand:

    select.ledmatrix_display_mode   every mode across enabled plugins;
                                    choosing one force-displays it
    button.ledmatrix_stop_display   back to normal rotation
    switch.ledmatrix_power          the display service, on or off
    number.ledmatrix_brightness     0-100

Anything the entities do not cover is still reachable by publishing JSON
to the command topic:

    {"action": "display", "mode": "nfl_live"}
    {"action": "display", "plugin_id": "starlark-apps", "mode": "aquarium",
     "duration": 300, "pinned": true}
    {"action": "stop_display"}
    {"action": "power", "state": "on" | "off"}
    {"action": "brightness", "value": 75}
    {"action": "refresh"}          re-publish discovery after installing a plugin

Every command publishes its result to <command_topic>/status.

Run it with `python3 ledmatrix_mqtt_bridge.py [--config PATH]`, or install
ledmatrix-mqtt-bridge.service.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger("ledmatrix-mqtt-bridge")

DISCOVERY_PREFIX = "homeassistant"
DEVICE_ID = "ledmatrix"
DEVICE_INFO = {
    "identifiers": [DEVICE_ID],
    "name": "LEDMatrix",
    "manufacturer": "ChuckBuilds",
    "model": "LEDMatrix Display",
}

DEFAULTS = {
    "mqtt_host": "localhost",
    "mqtt_port": 1883,
    "mqtt_username": None,
    "mqtt_password": None,  # nosec B105 - "no password configured", not a credential
    "mqtt_client_id": "ledmatrix-mqtt-bridge",
    "mqtt_topic": "ledmatrix/command",
    "mqtt_tls": False,
    "mqtt_tls_insecure": False,
    "ledmatrix_api_base": "http://localhost:5000",
    "request_timeout": 15,
    "on_demand_duration": None,
    "log_level": "INFO",
}


class ConfigError(Exception):
    """The bridge cannot start with the configuration it was given."""


def load_config(path: str) -> Dict[str, Any]:
    """Read bridge_config.json, overlaid on DEFAULTS.

    Every value may also come from the environment as LEDMATRIX_MQTT_<KEY>,
    which is how a password stays out of a file that has to be world-readable
    for the service user.
    """
    config = dict(DEFAULTS)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            try:
                loaded = json.load(handle)
            except json.JSONDecodeError as err:
                raise ConfigError(f"{path} is not valid JSON: {err}") from err
        if not isinstance(loaded, dict):
            raise ConfigError(f"{path} must contain a JSON object")
        config.update(loaded)
    else:
        logger.warning("No config file at %s - using defaults and environment", path)

    for key in DEFAULTS:
        env_value = os.environ.get(f"LEDMATRIX_MQTT_{key.upper()}")
        if env_value is not None:
            config[key] = env_value

    for key in ("mqtt_port", "request_timeout"):
        try:
            config[key] = int(config[key])
        except (TypeError, ValueError) as err:
            raise ConfigError(f"{key} must be a whole number, got {config[key]!r}") from err
    for key in ("mqtt_tls", "mqtt_tls_insecure"):
        config[key] = str(config[key]).lower() in ("1", "true", "yes", "on")

    if config.get("mqtt_password") == "REPLACE_WITH_YOUR_ACTUAL_MQTT_PASSWORD":
        raise ConfigError(
            "mqtt_password is still the example placeholder - set a real password, "
            "or remove the key if your broker allows anonymous connections")
    return config


class LEDMatrixClient:
    """The api_v3 calls the bridge needs, and nothing else.

    Everything goes through the HTTP API rather than the filesystem, so the
    bridge does not have to live on the Pi, does not need read access to
    config.json, and cannot drift from the web UI's own behaviour.
    """

    def __init__(self, api_base: str, timeout: int = 15,
                 session: Optional[requests.Session] = None):
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _call(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        url = f"{self.api_base}/api/v3{path}"
        response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or body.get("status") == "error":
            message = body.get("message") or f"HTTP {response.status_code}"
            raise RuntimeError(f"{method} {path} failed: {message}")
        return body.get("data", body)

    def list_modes(self) -> List[Dict[str, Any]]:
        """Every display mode that can be force-displayed, newest discovery.

        /display/modes triggers plugin discovery itself, which matters because
        discovery is lazy: a bridge that never opens the dashboard would
        otherwise see nothing at all.
        """
        return self._call("GET", "/display/modes").get("modes", [])

    def display_status(self) -> Dict[str, Any]:
        return self._call("GET", "/display/on-demand/status")

    def start_on_demand(self, mode: str, plugin_id: Optional[str] = None,
                        duration: Optional[int] = None, pinned: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"mode": mode, "pinned": pinned}
        if plugin_id:
            # find_plugin_for_mode only sees modes declared in a static
            # manifest, so a plugin whose modes are generated -- each installed
            # Starlark app is one -- 404s when plugin_id is omitted. Sending it
            # skips that lookup. /display/modes reports it for every mode.
            payload["plugin_id"] = plugin_id
        if duration:
            payload["duration"] = int(duration)
        return self._call("POST", "/display/on-demand/start", json=payload)

    def stop_on_demand(self) -> Dict[str, Any]:
        return self._call("POST", "/display/on-demand/stop", json={})

    def set_power(self, on: bool) -> Dict[str, Any]:
        action = "start_display" if on else "stop_display"
        return self._call("POST", "/system/action", json={"action": action})

    def get_brightness(self) -> Optional[int]:
        config = self._call("GET", "/config/main")
        value = config.get("display", {}).get("hardware", {}).get("brightness")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def set_brightness(self, value: int) -> Dict[str, Any]:
        return self._call("POST", "/config/main", json={"brightness": int(value)})


class CommandHandler:
    """Turns one decoded MQTT payload into one API call.

    Kept free of MQTT so it can be tested against a fake client: the failure
    modes worth pinning are all in here (an unknown mode, an out-of-range
    brightness, a mode name that needs its plugin_id attached).
    """

    def __init__(self, client: LEDMatrixClient, default_duration: Optional[int] = None):
        self.client = client
        self.default_duration = default_duration
        self._modes_by_name: Dict[str, Dict[str, Any]] = {}

    def refresh_modes(self) -> List[Dict[str, Any]]:
        modes = self.client.list_modes()
        self._modes_by_name = {m["mode"]: m for m in modes}
        # Home Assistant's select shows labels, so accept them back as well --
        # otherwise picking "Simple Clock" in a dashboard is not a mode name.
        for entry in modes:
            self._modes_by_name.setdefault(entry.get("name") or entry["mode"], entry)
        return modes

    @property
    def known_modes(self) -> List[Dict[str, Any]]:
        return list({id(v): v for v in self._modes_by_name.values()}.values())

    def handle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        action = payload.get("action")
        handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
            "display": self._display,
            "stop_display": lambda _p: self._ok(self.client.stop_on_demand()),
            "power": self._power,
            "brightness": self._brightness,
            "refresh": lambda _p: self._ok({"modes": len(self.refresh_modes())}),
        }
        handler = handlers.get(action)
        if handler is None:
            return self._error(f"Unknown action {action!r}; expected one of "
                               f"{', '.join(sorted(handlers))}")
        try:
            return handler(payload)
        except (requests.RequestException, RuntimeError) as err:
            logger.error("Command %s failed: %s", action, err)
            return self._error(str(err))

    def _display(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        mode = payload.get("mode")
        plugin_id = payload.get("plugin_id")
        if not mode and not plugin_id:
            return self._error("display requires 'mode' or 'plugin_id'")

        known = self._modes_by_name.get(mode) if mode else None
        if known is None and mode and not plugin_id:
            # One retry against a fresh listing: a plugin installed since the
            # last refresh is the common reason a valid mode looks unknown.
            self.refresh_modes()
            known = self._modes_by_name.get(mode)
        if known is not None:
            mode = known["mode"]
            plugin_id = plugin_id or known.get("plugin_id")

        duration = payload.get("duration", self.default_duration)
        pinned = bool(payload.get("pinned", False))
        result = self.client.start_on_demand(
            mode=mode, plugin_id=plugin_id, duration=duration, pinned=pinned)
        return self._ok(result, mode=mode, plugin_id=plugin_id)

    def _power(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        state = str(payload.get("state", "")).strip().lower()
        if state not in ("on", "off"):
            return self._error("power requires 'state' of 'on' or 'off'")
        return self._ok(self.client.set_power(state == "on"), state=state)

    def _brightness(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw = payload.get("value")
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            return self._error(f"brightness requires a number, got {raw!r}")
        if not 0 <= value <= 100:
            return self._error(f"brightness must be between 0 and 100, got {value}")
        return self._ok(self.client.set_brightness(value), value=value)

    @staticmethod
    def _ok(result: Any, **extra) -> Dict[str, Any]:
        return {"status": "success", "result": result, **extra}

    @staticmethod
    def _error(message: str) -> Dict[str, Any]:
        return {"status": "error", "message": message}


def discovery_messages(command_topic: str, state_topic: str, availability_topic: str,
                       mode_labels: List[str]) -> List[Dict[str, Any]]:
    """The retained MQTT Discovery configs, as {topic, payload} pairs.

    Pure, so the entity shapes can be asserted without a broker. Every entity
    shares one availability topic, which is also the bridge's last will -- HA
    then shows the matrix as unavailable when the bridge dies, instead of
    leaving stale controls that silently do nothing.
    """
    common = {
        "device": DEVICE_INFO,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
    }
    return [
        {
            "topic": f"{DISCOVERY_PREFIX}/select/{DEVICE_ID}/display_mode/config",
            "payload": {
                **common,
                "name": "Display Mode",
                "unique_id": f"{DEVICE_ID}_display_mode",
                "command_topic": command_topic,
                "command_template": '{"action": "display", "mode": "{{ value }}"}',
                "state_topic": state_topic,
                "value_template": "{{ value_json.mode }}",
                "options": mode_labels,
                "icon": "mdi:view-dashboard",
            },
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/button/{DEVICE_ID}/stop_display/config",
            "payload": {
                **common,
                "name": "Stop Display",
                "unique_id": f"{DEVICE_ID}_stop_display",
                "command_topic": command_topic,
                "payload_press": '{"action": "stop_display"}',
                "icon": "mdi:stop",
            },
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/switch/{DEVICE_ID}/power/config",
            "payload": {
                **common,
                "name": "Power",
                "unique_id": f"{DEVICE_ID}_power",
                "command_topic": command_topic,
                "payload_on": '{"action": "power", "state": "on"}',
                "payload_off": '{"action": "power", "state": "off"}',
                "state_topic": state_topic,
                "value_template": "{{ 'ON' if value_json.power else 'OFF' }}",
                "state_on": "ON",
                "state_off": "OFF",
                "icon": "mdi:power",
            },
        },
        {
            "topic": f"{DISCOVERY_PREFIX}/number/{DEVICE_ID}/brightness/config",
            "payload": {
                **common,
                "name": "Brightness",
                "unique_id": f"{DEVICE_ID}_brightness",
                "command_topic": command_topic,
                "command_template": '{"action": "brightness", "value": {{ value }}}',
                "state_topic": state_topic,
                "value_template": "{{ value_json.brightness }}",
                "min": 0,
                "max": 100,
                "step": 1,
                "icon": "mdi:brightness-6",
            },
        },
    ]


def warn_if_cleartext(config: Dict[str, Any]) -> bool:
    """Say so, once, when a broker password is going over an unencrypted link.

    The shipped example has TLS on, so reaching here means somebody turned it
    off deliberately -- which is legitimate (the Mosquitto add-on is plaintext
    on 1883) but should not be silent when there is a password to lose. Returns
    whether it warned, so the decision is testable without a broker.
    """
    if config.get("mqtt_tls") or not config.get("mqtt_password"):
        return False
    logger.warning(
        'mqtt_tls is off and a password is set: the broker password and every '
        'command are sent unencrypted. Set "mqtt_tls": true (port 8883 on most '
        'brokers) unless this is a trusted, isolated network.')
    return True


def read_state(client: LEDMatrixClient) -> Dict[str, Any]:
    """The state every entity reads, so HA opens on real values.

    Each field is fetched independently: a matrix with its display service
    stopped still has a brightness worth showing, and one unreachable field
    should not blank the rest.
    """
    state: Dict[str, Any] = {"power": False, "mode": None, "brightness": None}
    try:
        status = client.display_status()
        state["power"] = bool(status.get("service", {}).get("active"))
        on_demand = status.get("state", {})
        if on_demand.get("active"):
            state["mode"] = on_demand.get("mode")
    except (requests.RequestException, RuntimeError) as err:
        logger.debug("Could not read display status: %s", err)
    try:
        state["brightness"] = client.get_brightness()
    except (requests.RequestException, RuntimeError) as err:
        logger.debug("Could not read brightness: %s", err)
    return state


class Bridge:
    """MQTT wiring around CommandHandler."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.command_topic = config["mqtt_topic"]
        self.status_topic = f"{self.command_topic}/status"
        self.state_topic = f"{self.command_topic}/state"
        self.availability_topic = f"{self.command_topic}/availability"
        self.client = LEDMatrixClient(config["ledmatrix_api_base"], config["request_timeout"])
        self.handler = CommandHandler(self.client, config.get("on_demand_duration"))
        self._stop = threading.Event()
        self._mqtt = None

    # -- MQTT callbacks (paho-mqtt 2.x VERSION2 signatures) ------------------

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None):
        if getattr(reason_code, "is_failure", reason_code != 0):
            logger.error("MQTT connection refused: %s", reason_code)
            return
        logger.info("Connected to MQTT broker; subscribing to %s", self.command_topic)
        client.subscribe(self.command_topic, qos=1)
        client.publish(self.availability_topic, "online", qos=1, retain=True)
        # Re-publish on every reconnect, not just the first connect: a broker
        # restart drops retained discovery configs, and HA would otherwise be
        # left with entities it can no longer describe.
        self.publish_discovery()
        self.publish_state()

    def _on_message(self, _client, _userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            logger.warning("Ignoring unparseable message on %s: %s", message.topic, err)
            self._publish(self.status_topic, {"status": "error", "message": f"bad payload: {err}"})
            return
        if not isinstance(payload, dict):
            self._publish(self.status_topic,
                          {"status": "error", "message": "payload must be a JSON object"})
            return

        logger.info("Command: %s", payload)
        result = self.handler.handle(payload)
        self._publish(self.status_topic, result)
        # The API applies changes asynchronously (the controller polls its
        # mailbox), so read state back rather than assuming the command took.
        self.publish_state()

    # -- publishing ---------------------------------------------------------

    def _publish(self, topic: str, payload: Any, retain: bool = False) -> None:
        if self._mqtt is None:
            return
        body = payload if isinstance(payload, str) else json.dumps(payload)
        self._mqtt.publish(topic, body, qos=1, retain=retain)

    def publish_discovery(self) -> None:
        try:
            modes = self.handler.refresh_modes()
        except (requests.RequestException, RuntimeError) as err:
            logger.error("Could not list display modes: %s", err)
            modes = self.handler.known_modes
        labels = sorted({m.get("name") or m["mode"] for m in modes})
        for message in discovery_messages(self.command_topic, self.state_topic,
                                          self.availability_topic, labels):
            self._publish(message["topic"], message["payload"], retain=True)
        logger.info("Published discovery for %d display mode(s)", len(labels))

    def publish_state(self) -> None:
        self._publish(self.state_topic, read_state(self.client), retain=True)

    # -- lifecycle ----------------------------------------------------------

    def run(self) -> int:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt is not installed: pip install -r requirements.txt")
            return 1

        # VERSION2 is the current callback API. The compatibility note in
        # CLAUDE.md is about code written against the v1 signatures; this file
        # is written against v2 and requires paho-mqtt >= 2.0.
        self._mqtt = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.config["mqtt_client_id"])
        if self.config.get("mqtt_username"):
            self._mqtt.username_pw_set(self.config["mqtt_username"],
                                       self.config.get("mqtt_password"))
        if self.config.get("mqtt_tls"):
            self._mqtt.tls_set()
            if self.config.get("mqtt_tls_insecure"):
                logger.warning("TLS certificate verification is disabled (mqtt_tls_insecure)")
                self._mqtt.tls_insecure_set(True)
        else:
            warn_if_cleartext(self.config)

        self._mqtt.will_set(self.availability_topic, "offline", qos=1, retain=True)
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_message = self._on_message

        logger.info("Connecting to %s:%s", self.config["mqtt_host"], self.config["mqtt_port"])
        try:
            self._mqtt.connect(self.config["mqtt_host"], self.config["mqtt_port"], keepalive=60)
        except OSError as err:
            logger.error("Could not reach the MQTT broker: %s", err)
            return 1

        self._mqtt.loop_start()
        try:
            while not self._stop.wait(30):
                # HA is told the truth about state that changed outside the
                # bridge -- somebody using the web UI, or an on-demand window
                # expiring on its own.
                self.publish_state()
        finally:
            self._publish(self.availability_topic, "offline", retain=True)
            self._mqtt.loop_stop()
            self._mqtt.disconnect()
        return 0

    def stop(self, *_args) -> None:
        logger.info("Shutting down")
        self._stop.set()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_config.json"),
        help="Path to bridge_config.json (default: alongside this script)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    try:
        config = load_config(args.config)
    except ConfigError as err:
        logger.error("%s", err)
        return 1
    logging.getLogger().setLevel(str(config.get("log_level", "INFO")).upper())

    bridge = Bridge(config)
    signal.signal(signal.SIGTERM, bridge.stop)
    signal.signal(signal.SIGINT, bridge.stop)
    return bridge.run()


if __name__ == "__main__":
    sys.exit(main())
