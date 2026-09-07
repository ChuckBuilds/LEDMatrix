# Home Assistant MQTT Bridge

Control the matrix from Home Assistant: force any plugin or mode on demand,
turn the display on and off, and set brightness — as real HA entities, not
hand-written `mqtt.publish` calls.

The bridge owns no display logic. It subscribes to one command topic and
turns each message into a call against the same `api_v3` routes the web UI
uses, so behaviour lives in one place. It talks to the API over HTTP only —
no filesystem access — so it can run on the Pi or anywhere that can reach
the web interface.

## What appears in Home Assistant

On connect the bridge publishes [MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery)
config, so the matrix shows up under **Settings → Devices & Services → MQTT**
with no YAML:

| Entity | Does |
|---|---|
| `select.ledmatrix_display_mode` | Every mode across enabled plugins. Choosing one force-displays it. |
| `button.ledmatrix_stop_display` | Back to normal rotation. |
| `switch.ledmatrix_power` | Starts/stops the display service. |
| `number.ledmatrix_brightness` | 0–100. |

State is read back from the API every 30 seconds, so the entities also track
changes made from the web UI or an on-demand window expiring on its own.
All four share an availability topic that is the bridge's MQTT last will:
if the bridge dies, HA greys the controls out rather than leaving them
looking live but inert.

## Raw commands

For anything the entities do not cover, publish JSON to the command topic
(`ledmatrix/command` by default):

```jsonc
// Force a mode. plugin_id is optional — the bridge fills it in from
// /api/v3/display/modes.
{"action": "display", "mode": "nfl_live"}

// duration is seconds; pinned holds this one mode instead of rotating
// through every mode the plugin owns. Pin Starlark apps, where each mode
// is an unrelated widget; leave a sports plugin unpinned so live/recent/
// upcoming still cycle.
{"action": "display", "plugin_id": "starlark-apps", "mode": "aquarium",
 "duration": 300, "pinned": true}

{"action": "stop_display"}
{"action": "power", "state": "on"}
{"action": "brightness", "value": 75}

// Re-read the mode list and re-publish discovery, after installing a plugin
{"action": "refresh"}
```

Every command publishes its outcome to `<command_topic>/status`, and current
state to `<command_topic>/state`.

## Requirements

- A LEDMatrix install with its web interface reachable (default `http://localhost:5000`)
- An MQTT broker that Home Assistant is also connected to
- Python 3 with `paho-mqtt` 2.x and `requests`

## Install

```bash
sudo ./scripts/install/install_mqtt_bridge.sh
```

That copies `bridge_config.example.json` to `bridge_config.json` on first
run, installs the dependencies, and enables `ledmatrix-mqtt-bridge.service`.
Edit the config with your broker details and re-run it.

```json
{
  "mqtt_host": "192.168.1.10",
  "mqtt_port": 1883,
  "mqtt_username": "ledmatrix",
  "mqtt_password": null,
  "mqtt_topic": "ledmatrix/command",
  "mqtt_tls": false,
  "ledmatrix_api_base": "http://localhost:5000"
}
```

`bridge_config.json` is gitignored. Any key can also be supplied through the
environment as `LEDMATRIX_MQTT_<KEY>` (`LEDMATRIX_MQTT_MQTT_PASSWORD`, say),
which keeps a broker password out of a file on disk — put it in a systemd
drop-in with `Environment=` or `EnvironmentFile=` instead.

Set `mqtt_tls: true` for a broker with TLS. `mqtt_tls_insecure` skips
certificate verification and exists only for a self-signed broker on a
trusted LAN; it logs a warning when used.

To run it in the foreground while setting things up:

```bash
python3 integrations/mqtt_bridge/ledmatrix_mqtt_bridge.py --config integrations/mqtt_bridge/bridge_config.json
```

## Notes

- Only one thing can be on-demand at a time — the same constraint the web UI has.
- Forcing a mode restarts the display service, so the panel blanks for a moment.
- The mode list comes from `/api/v3/display/modes`, which triggers plugin
  discovery itself. Discovery is lazy and normally happens because somebody
  opened the dashboard; without that endpoint a bridge that never does would
  see an empty list.
- Brightness writes `display.hardware.brightness` through `/api/v3/config/main`.
  The display service picks it up on its next restart, not instantly.
