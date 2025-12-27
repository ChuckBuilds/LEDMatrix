# On-Demand Display API

## Overview

The On-Demand Display API allows **manual control** of what's shown on the LED matrix. Unlike the automatic rotation or live priority system, on-demand display is **user-triggered** - typically from the web interface with a "Show Now" button.

## Use Cases

- 📺 **"Show Weather Now"** button in web UI
- 🏒 **"Show Live Game"** button for specific sports
- 📰 **"Show Breaking News"** button
- 🎵 **"Show Currently Playing"** button for music
- 🎮 **Quick preview** of any plugin without waiting for rotation

## Priority Hierarchy

The display controller processes requests in this order:

```
1. On-Demand Display (HIGHEST) ← User explicitly requested
2. Live Priority (plugins with live content)
3. Normal Rotation (automatic cycling)
```

On-demand overrides everything, including live priority.

## API Reference

### DisplayController Methods

#### `show_on_demand(mode, duration=None, pinned=False) -> bool`

Display a specific mode immediately, interrupting normal rotation.

**Parameters:**
- `mode` (str): The display mode to show (e.g., 'weather', 'hockey_live')
- `duration` (float, optional): How long to show in seconds
  - `None`: Use mode's default `display_duration` from config
  - `0`: Show indefinitely (until cleared)
  - `> 0`: Show for exactly this many seconds
- `pinned` (bool): If True, stays on this mode until manually cleared

**Returns:**
- `True`: Mode was found and activated
- `False`: Mode doesn't exist

**Example:**
```python
# Show weather for 30 seconds then return to rotation
controller.show_on_demand('weather', duration=30)

# Show weather indefinitely
controller.show_on_demand('weather', duration=0)

# Pin to hockey live (stays until unpinned)
controller.show_on_demand('hockey_live', pinned=True)

# Use plugin's default duration
controller.show_on_demand('weather')  # Uses display_duration from config
```

#### `clear_on_demand() -> None`

Clear on-demand display and return to normal rotation.

**Example:**
```python
controller.clear_on_demand()
```

#### `is_on_demand_active() -> bool`

Check if on-demand display is currently active.

**Returns:**
- `True`: On-demand mode is active
- `False`: Normal rotation or live priority

**Example:**
```python
if controller.is_on_demand_active():
    print("User is viewing on-demand content")
```

#### `get_on_demand_info() -> dict`

Get detailed information about current on-demand display.

**Returns:**
```python
{
    'active': True,              # Whether on-demand is active
    'mode': 'weather',           # Current mode being displayed
    'duration': 30.0,            # Total duration (None if indefinite)
    'elapsed': 12.5,             # Seconds elapsed
    'remaining': 17.5,           # Seconds remaining (None if indefinite)
    'pinned': False              # Whether pinned
}

# Or if not active:
{
    'active': False
}
```

**Example:**
```python
info = controller.get_on_demand_info()
if info['active']:
    print(f"Showing {info['mode']}, {info['remaining']}s remaining")
```

## Web Interface Integration

### API Endpoint Example

```python
# In web_interface/blueprints/api_v3.py

from flask import jsonify, request

@api_v3.route('/display/show', methods=['POST'])
def show_on_demand():
    """Show a specific plugin on-demand"""
    data = request.json
    mode = data.get('mode')
    duration = data.get('duration')  # Optional
    pinned = data.get('pinned', False)  # Optional
    
    # Get display controller instance
    controller = get_display_controller()
    
    success = controller.show_on_demand(mode, duration, pinned)
    
    if success:
        return jsonify({
            'success': True,
            'message': f'Showing {mode}',
            'info': controller.get_on_demand_info()
        })
    else:
        return jsonify({
            'success': False,
            'error': f'Mode {mode} not found'
        }), 404

@api_v3.route('/display/clear', methods=['POST'])
def clear_on_demand():
    """Clear on-demand display"""
    controller = get_display_controller()
    controller.clear_on_demand()
    
    return jsonify({
        'success': True,
        'message': 'On-demand display cleared'
    })

@api_v3.route('/display/on-demand-info', methods=['GET'])
def get_on_demand_info():
    """Get on-demand display status"""
    controller = get_display_controller()
    info = controller.get_on_demand_info()
    
    return jsonify(info)
```

### Frontend Example (JavaScript)

```javascript
// Show weather for 30 seconds
async function showWeather() {
    const response = await fetch('/api/v3/display/show', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            mode: 'weather',
            duration: 30
        })
    });
    
    const data = await response.json();
    if (data.success) {
        updateStatus(`Showing weather for ${data.info.duration}s`);
    }
}

// Pin to live hockey game
async function pinHockeyLive() {
    const response = await fetch('/api/v3/display/show', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            mode: 'hockey_live',
            pinned: true
        })
    });
    
    const data = await response.json();
    if (data.success) {
        updateStatus('Pinned to hockey live');
    }
}

// Clear on-demand
async function clearOnDemand() {
    const response = await fetch('/api/v3/display/clear', {
        method: 'POST'
    });
    
    const data = await response.json();
    if (data.success) {
        updateStatus('Returned to normal rotation');
    }
}

// Check status
async function checkOnDemandStatus() {
    const response = await fetch('/api/v3/display/on-demand-info');
    const info = await response.json();
    
    if (info.active) {
        updateStatus(`On-demand: ${info.mode} (${info.remaining}s remaining)`);
    } else {
        updateStatus('Normal rotation');
    }
}
```

### UI Example (HTML)

```html
<!-- Plugin controls -->
<div class="plugin-card">
    <h3>Weather</h3>
    <button onclick="showWeather()">Show Now (30s)</button>
    <button onclick="showWeatherIndefinite()">Show Until Cleared</button>
    <button onclick="pinWeather()">Pin Weather</button>
</div>

<!-- On-demand status display -->
<div id="on-demand-status" class="status-bar">
    <span id="status-text">Normal rotation</span>
    <button id="clear-btn" onclick="clearOnDemand()" style="display: none;">
        Clear On-Demand
    </button>
</div>

<script>
// Poll for status updates
setInterval(async () => {
    const info = await fetch('/api/v3/display/on-demand-info').then(r => r.json());
    
    const statusText = document.getElementById('status-text');
    const clearBtn = document.getElementById('clear-btn');
    
    if (info.active) {
        let text = `On-demand: ${info.mode}`;
        if (info.remaining) {
            text += ` (${Math.ceil(info.remaining)}s)`;
        } else if (info.pinned) {
            text += ' (pinned)';
        }
        statusText.textContent = text;
        clearBtn.style.display = 'inline-block';
    } else {
        statusText.textContent = 'Normal rotation';
        clearBtn.style.display = 'none';
    }
}, 1000);  // Update every second
</script>
```

## Behavior Details

### Duration Modes

| Duration Value | Behavior | Use Case |
|---------------|----------|----------|
| `None` | Use plugin's `display_duration` from config | Default behavior |
| `0` | Show indefinitely until cleared | Quick preview |
| `> 0` | Show for exactly N seconds | Timed preview |
| `pinned=True` | Stay on mode until unpinned | Extended viewing |

### Auto-Clear Behavior

On-demand display automatically clears when:
- Duration expires (if set and > 0)
- User manually clears it
- System restarts

On-demand does NOT clear when:
- `duration=0` (indefinite)
- `pinned=True`
- Live priority content appears (on-demand still has priority)

### Interaction with Live Priority

```python
# Scenario 1: On-demand overrides live priority
controller.show_on_demand('weather', duration=30)
# → Shows weather even if live game is happening

# Scenario 2: After on-demand expires, live priority takes over
controller.show_on_demand('weather', duration=10)
# → Shows weather for 10s
# → If live game exists, switches to live game
# → Otherwise returns to normal rotation
```

## Use Case Examples

### Example 1: Quick Weather Check

```python
# User clicks "Show Weather" button
controller.show_on_demand('weather', duration=30)
# Shows weather for 30 seconds, then returns to rotation
```

### Example 2: Monitor Live Game

```python
# User clicks "Watch Live Game" button
controller.show_on_demand('hockey_live', pinned=True)
# Stays on live game until user clicks "Back to Rotation"
```

### Example 3: Preview Plugin

```python
# User clicks "Preview" in plugin settings
controller.show_on_demand('my-plugin', duration=15)
# Shows plugin for 15 seconds to test configuration
```

### Example 4: Emergency Override

```python
# Admin needs to show important message
controller.show_on_demand('text-display', pinned=True)
# Display stays on message until admin clears it
```

## Testing

### Manual Test from Python

```python
# Access display controller
from src.display_controller import DisplayController
controller = DisplayController()  # Or get existing instance

# Test show on-demand
controller.show_on_demand('weather', duration=20)
print(controller.get_on_demand_info())

# Test clear
time.sleep(5)
controller.clear_on_demand()
print(controller.get_on_demand_info())
```

### Test with Web API

```bash
# Show weather for 30 seconds
curl -X POST http://pi-ip:5001/api/v3/display/show \
  -H "Content-Type: application/json" \
  -d '{"mode": "weather", "duration": 30}'

# Check status
curl http://pi-ip:5001/api/v3/display/on-demand-info

# Clear on-demand
curl -X POST http://pi-ip:5001/api/v3/display/clear
```

### Monitor Logs

```bash
sudo journalctl -u ledmatrix -f | grep -i "on-demand"
```

Expected output:
```
On-demand display activated: weather (duration: 30s, pinned: False)
On-demand display expired after 30.1s
Clearing on-demand display: weather
```

## Best Practices

### 1. Provide Visual Feedback

Always show users when on-demand is active:

```javascript
// Update UI to show on-demand status
function updateOnDemandUI(info) {
    const banner = document.getElementById('on-demand-banner');
    if (info.active) {
        banner.style.display = 'block';
        banner.textContent = `Showing: ${info.mode}`;
        if (info.remaining) {
            banner.textContent += ` (${Math.ceil(info.remaining)}s)`;
        }
    } else {
        banner.style.display = 'none';
    }
}
```

### 2. Default to Timed Display

Unless explicitly requested, use a duration:

```python
# Good: Auto-clears after 30 seconds
controller.show_on_demand('weather', duration=30)

# Risky: Stays indefinitely
controller.show_on_demand('weather', duration=0)
```

### 3. Validate Modes

Check if mode exists before showing:

```python
# Get available modes
available_modes = controller.available_modes + list(controller.plugin_modes.keys())

if mode in available_modes:
    controller.show_on_demand(mode, duration=30)
else:
    return jsonify({'error': 'Mode not found'}), 404
```

### 4. Handle Concurrent Requests

Last request wins:

```python
# Request 1: Show weather
controller.show_on_demand('weather', duration=30)

# Request 2: Show hockey (overrides weather)
controller.show_on_demand('hockey_live', duration=20)
# Hockey now shows for 20s, weather request is forgotten
```

## Troubleshooting

### On-Demand Not Working

**Check 1:** Verify mode exists
```python
info = controller.get_on_demand_info()
print(f"Active: {info['active']}, Mode: {info.get('mode')}")
print(f"Available modes: {controller.available_modes}")
```

**Check 2:** Check logs
```bash
sudo journalctl -u ledmatrix -f | grep "on-demand\|available modes"
```

### On-Demand Not Clearing

**Check if pinned:**
```python
info = controller.get_on_demand_info()
if info['pinned']:
    print("Mode is pinned - must clear manually")
    controller.clear_on_demand()
```

**Check duration:**
```python
if info['duration'] == 0:
    print("Duration is indefinite - must clear manually")
```

### Mode Shows But Looks Wrong

This is a **display** issue, not an on-demand issue. Check:
- Plugin's `update()` method is fetching data
- Plugin's `display()` method is rendering correctly
- Cache is not stale

## Security Considerations

### 1. Authentication Required

Always require authentication for on-demand control:

```python
@api_v3.route('/display/show', methods=['POST'])
@login_required  # Add authentication
def show_on_demand():
    # ... implementation
```

### 2. Rate Limiting

Prevent spam:

```python
from flask_limiter import Limiter

limiter = Limiter(app, key_func=get_remote_address)

@api_v3.route('/display/show', methods=['POST'])
@limiter.limit("10 per minute")  # Max 10 requests per minute
def show_on_demand():
    # ... implementation
```

### 3. Input Validation

Sanitize mode names:

```python
import re

def validate_mode(mode):
    # Only allow alphanumeric, underscore, hyphen
    if not re.match(r'^[a-zA-Z0-9_-]+$', mode):
        raise ValueError("Invalid mode name")
    return mode
```

## Implementation Checklist

- [ ] Add API endpoint to web interface
- [ ] Add "Show Now" buttons to plugin UI
- [ ] Add on-demand status indicator
- [ ] Add "Clear" button when on-demand active
- [ ] Add authentication/authorization
- [ ] Add rate limiting
- [ ] Test with multiple plugins
- [ ] Test duration expiration
- [ ] Test pinned mode
- [ ] Document for end users

## Future Enhancements

Consider adding:
1. **Queue system** - Queue multiple on-demand requests
2. **Scheduled on-demand** - Show mode at specific time
3. **Recurring on-demand** - Show every N minutes
4. **Permission levels** - Different users can show different modes
5. **History tracking** - Log who triggered what and when

