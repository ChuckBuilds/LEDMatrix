# Web UI Info Plugin

A simple built-in plugin for LEDMatrix that displays the web UI URL for easy access.

## Features

- Displays "visit web ui at http://[deviceID]:5000" on the LED matrix
- Automatically detects device hostname
- Easily enabled/disabled via web interface
- Configurable display duration

## Configuration

The plugin can be configured in `config/config.json`:

```json
{
  "web-ui-info": {
    "enabled": true,
    "display_duration": 10,
    "transition": {
      "type": "redraw",
      "speed": 2,
      "enabled": true
    }
  }
}
```

### Configuration Options

- `enabled` (boolean): Enable or disable the plugin (default: `true`)
- `display_duration` (number): How long to display the message in seconds (default: `10`, range: 1-300)
- `transition` (object): Transition settings for switching to/from this display

## Usage

The plugin will automatically be discovered and loaded when the LEDMatrix system starts. It will display the web UI URL during the normal rotation cycle.

To enable/disable the plugin:
1. Open the web UI at `http://[your-device]:5000`
2. Navigate to the Plugins tab
3. Find "Web UI Info" in the plugin list
4. Toggle the enable/disable checkbox

## Device ID

The plugin uses `socket.gethostname()` to determine the device hostname. If the hostname cannot be determined, it falls back to "localhost".

## Display

The plugin displays two lines of text:
- Line 1: "visit web ui"
- Line 2: "at [deviceID]:5000"

Text is centered horizontally on the display and rendered in white.

## Notes

- This is a built-in plugin that comes with the LEDMatrix repository
- No external dependencies required
- The plugin does not fetch any external data - it only displays the local device information

