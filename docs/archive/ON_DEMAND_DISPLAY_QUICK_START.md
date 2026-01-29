# On-Demand Display - Quick Start Guide

## 🎯 What Is It?

On-Demand Display lets users **manually trigger** specific plugins to show on the LED matrix - perfect for "Show Now" buttons in your web interface!

> **2025 update:** The LEDMatrix web interface now ships with first-class on-demand controls. You can trigger plugins directly from the Plugin Management page or by calling the new `/api/v3/display/on-demand/*` endpoints described below. The legacy quick-start steps are still documented for bespoke integrations.

## ✅ Built-In Controls

### Web Interface (no-code)

- Navigate to **Settings → Plugin Management**.
- Each installed plugin now exposes a **Run On-Demand** button:
  - Choose the display mode (when a plugin exposes multiple views).
  - Optionally set a fixed duration (leave blank to use the plugin default or `0` to run until you stop it).
  - Pin the plugin so rotation stays paused.
  - The dashboard shows real-time status and lets you stop the session. **Shift+click** the stop button to stop the display service after clearing the plugin.
- The status card refreshes automatically and indicates whether the display service is running.

### REST Endpoints

All endpoints live under `/api/v3/display/on-demand`.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | Returns the current on-demand state plus display service health. |
| `/start`  | POST | Requests a plugin/mode to run. Automatically starts the display service (unless `start_service: false`). |
| `/stop`   | POST | Clears on-demand mode. Include `{"stop_service": true}` to stop the systemd service. |

Example `curl` calls:

```bash
# Start the default mode for football-scoreboard for 45 seconds
curl -X POST http://localhost:5000/api/v3/display/on-demand/start \
     -H "Content-Type: application/json" \
     -d '{
            "plugin_id": "football-scoreboard",
            "duration": 45,
            "pinned": true
         }'

# Start by mode name (plugin id inferred automatically)
curl -X POST http://localhost:5000/api/v3/display/on-demand/start \
     -H "Content-Type: application/json" \
     -d '{ "mode": "football_live" }'

# Stop on-demand and shut down the display service
curl -X POST http://localhost:5000/api/v3/display/on-demand/stop \
     -H "Content-Type: application/json" \
     -d '{ "stop_service": true }'

# Check current status
curl http://localhost:5000/api/v3/display/on-demand/status | jq
```

**Notes**

- The display controller will honour the plugin’s configured `display_duration` when no duration is provided.
- When you pass `duration: 0` (or omit it) and `pinned: true`, the plugin stays active until you issue `/stop`.
- The service automatically resumes normal rotation after the on-demand session expires or is cleared.

## 🚀 Quick Implementation (3 Steps)

> The steps below describe a lightweight custom implementation that predates the built-in API. You generally no longer need this unless you are integrating with a separate control surface.

### Step 1: Add API Endpoint

```python
# In web_interface/blueprints/api_v3.py

@api_v3.route('/display/show', methods=['POST'])
def show_on_demand():
    data = request.json
    mode = data.get('mode')
    duration = data.get('duration', 30)  # Default 30 seconds
    
    # Get display controller (implementation depends on your setup)
    controller = get_display_controller()
    
    success = controller.show_on_demand(mode, duration=duration)
    
    return jsonify({'success': success})

@api_v3.route('/display/clear', methods=['POST'])
def clear_on_demand():
    controller = get_display_controller()
    controller.clear_on_demand()
    return jsonify({'success': True})
```

### Step 2: Add UI Button

```html
<!-- Show weather button -->
<button onclick="showWeather()">Show Weather Now</button>

<script>
async function showWeather() {
    await fetch('/api/v3/display/show', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            mode: 'weather',
            duration: 30  // Show for 30 seconds
        })
    });
}
</script>
```

### Step 3: Done! 🎉

Users can now click the button to show weather immediately!

## 📋 Complete Web UI Example

```html
<!DOCTYPE html>
<html>
<head>
    <title>Display Control</title>
    <style>
        .plugin-card {
            border: 1px solid #ccc;
            padding: 15px;
            margin: 10px;
            border-radius: 5px;
        }
        .show-now-btn {
            background: #4CAF50;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
        }
        .pin-btn {
            background: #2196F3;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
        }
        .clear-btn {
            background: #f44336;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
        }
        #status-bar {
            background: #ff9800;
            color: white;
            padding: 15px;
            text-align: center;
            display: none;
        }
    </style>
</head>
<body>
    <!-- Status bar (shown when on-demand is active) -->
    <div id="status-bar">
        <span id="status-text"></span>
        <button class="clear-btn" onclick="clearOnDemand()">
            Return to Rotation
        </button>
    </div>

    <!-- Plugin controls -->
    <div class="plugin-grid">
        <div class="plugin-card">
            <h3>⛅ Weather</h3>
            <button class="show-now-btn" onclick="showPlugin('weather', 30)">
                Show for 30s
            </button>
            <button class="pin-btn" onclick="pinPlugin('weather')">
                Pin Weather
            </button>
        </div>

        <div class="plugin-card">
            <h3>🏒 Hockey</h3>
            <button class="show-now-btn" onclick="showPlugin('hockey_live', 45)">
                Show Live Game
            </button>
            <button class="pin-btn" onclick="pinPlugin('hockey_live')">
                Pin Game
            </button>
        </div>

        <div class="plugin-card">
            <h3>🎵 Music</h3>
            <button class="show-now-btn" onclick="showPlugin('music', 20)">
                Show Now Playing
            </button>
        </div>
    </div>

    <script>
    // Show plugin for specific duration
    async function showPlugin(mode, duration) {
        const response = await fetch('/api/v3/display/show', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode, duration })
        });
        
        const data = await response.json();
        if (data.success) {
            updateStatus();
        } else {
            alert('Failed to show plugin');
        }
    }

    // Pin plugin (stays until cleared)
    async function pinPlugin(mode) {
        const response = await fetch('/api/v3/display/show', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                mode, 
                pinned: true 
            })
        });
        
        const data = await response.json();
        if (data.success) {
            updateStatus();
        }
    }

    // Clear on-demand and return to rotation
    async function clearOnDemand() {
        await fetch('/api/v3/display/clear', { method: 'POST' });
        updateStatus();
    }

    // Update status display
    async function updateStatus() {
        const response = await fetch('/api/v3/display/on-demand-info');
        const info = await response.json();
        
        const statusBar = document.getElementById('status-bar');
        const statusText = document.getElementById('status-text');
        
        if (info.active) {
            let text = `Showing: ${info.mode}`;
            if (info.remaining) {
                text += ` (${Math.ceil(info.remaining)}s remaining)`;
            } else if (info.pinned) {
                text += ' (pinned)';
            }
            statusText.textContent = text;
            statusBar.style.display = 'block';
        } else {
            statusBar.style.display = 'none';
        }
    }

    // Poll for status updates every second
    setInterval(updateStatus, 1000);
    
    // Initial status check
    updateStatus();
    </script>
</body>
</html>
```

## ⚡ Usage Patterns

### Pattern 1: Timed Preview
```javascript
// Show for 30 seconds then return to rotation
showPlugin('weather', 30);
```

### Pattern 2: Pinned Display
```javascript
// Stay on this plugin until manually cleared
pinPlugin('hockey_live');
```

### Pattern 3: Quick Check
```javascript
// Show for 10 seconds
showPlugin('clock', 10);
```

### Pattern 4: Indefinite Display
```javascript
// Show until cleared (duration=0)
fetch('/api/v3/display/show', {
    method: 'POST',
    body: JSON.stringify({ mode: 'weather', duration: 0 })
});
```

## 📊 Priority Order

```
User clicks "Show Weather" button
         ↓
1. On-Demand (Highest) ← Shows immediately
2. Live Priority        ← Overridden
3. Normal Rotation      ← Paused
```

On-demand has **highest priority** - it overrides everything!

## 🎮 Common Use Cases

### Quick Weather Check
```html
<button onclick="showPlugin('weather', 20)">
    Check Weather
</button>
```

### Monitor Live Game
```html
<button onclick="pinPlugin('hockey_live')">
    Watch Game
</button>
```

### Test Plugin Configuration
```html
<button onclick="showPlugin('my-plugin', 15)">
    Preview Plugin
</button>
```

### Emergency Message
```html
<button onclick="pinPlugin('text-display')">
    Show Alert
</button>
```

## 🔧 Duration Options

| Value | Behavior | Example |
|-------|----------|---------|
| `30` | Show for 30s then return | Quick preview |
| `0` | Show until cleared | Extended viewing |
| `null` | Use plugin's default | Let plugin decide |
| `pinned: true` | Stay until unpinned | Monitor mode |

## ❓ FAQ

### Q: What happens when duration expires?
**A:** Display automatically returns to normal rotation (or live priority if active).

### Q: Can I show multiple modes at once?
**A:** No, only one mode at a time. Last request wins.

### Q: Does it override live games?
**A:** Yes! On-demand has highest priority, even over live priority.

### Q: How do I go back to normal rotation?
**A:** Either wait for duration to expire, or call `clearOnDemand()`.

### Q: What if the mode doesn't exist?
**A:** API returns `success: false` and logs a warning.

## 🐛 Testing

### Test 1: Show for 30 seconds
```bash
curl -X POST http://pi-ip:5001/api/v3/display/show \
  -H "Content-Type: application/json" \
  -d '{"mode": "weather", "duration": 30}'
```

### Test 2: Pin mode
```bash
curl -X POST http://pi-ip:5001/api/v3/display/show \
  -H "Content-Type: application/json" \
  -d '{"mode": "hockey_live", "pinned": true}'
```

### Test 3: Clear on-demand
```bash
curl -X POST http://pi-ip:5001/api/v3/display/clear
```

### Test 4: Check status
```bash
curl http://pi-ip:5001/api/v3/display/on-demand-info
```

## 📝 Implementation Checklist

- [ ] Add API endpoints to web interface
- [ ] Add "Show Now" buttons to plugin cards
- [ ] Add status bar showing current on-demand mode
- [ ] Add "Clear" button when on-demand active
- [ ] Add authentication to API endpoints
- [ ] Test with multiple plugins
- [ ] Test duration expiration
- [ ] Test pinned mode

## 📚 Full Documentation

See `ON_DEMAND_DISPLAY_API.md` for:
- Complete API reference
- Security best practices
- Troubleshooting guide
- Advanced examples

## 🎯 Key Points

1. **User-triggered** - Manual control from web UI
2. **Highest priority** - Overrides everything
3. **Auto-clear** - Returns to rotation after duration
4. **Pin mode** - Stay on mode until manually cleared
5. **Simple API** - Just 3 endpoints needed

That's it! Your users can now control what shows on the display! 🚀

