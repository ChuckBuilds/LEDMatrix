# On-Demand Cache Management

## Overview

The on-demand feature uses several cache keys to manage state. Understanding these keys helps with troubleshooting and manual recovery.

## Cache Keys Used

### 1. `display_on_demand_request`
**Purpose**: Stores pending on-demand requests (start/stop actions)  
**TTL**: 1 hour  
**When Set**: When you click "Run On-Demand" or "Stop On-Demand"  
**When Cleared**: Automatically after processing, or manually via cache management

**Structure**:
```json
{
  "request_id": "uuid-string",
  "action": "start" | "stop",
  "plugin_id": "plugin-name",
  "mode": "mode-name",
  "duration": 30.0,
  "pinned": true,
  "timestamp": 1234567890.123
}
```

### 2. `display_on_demand_config`
**Purpose**: Stores the active on-demand configuration (persists across restarts)  
**TTL**: 1 hour  
**When Set**: When on-demand mode is activated  
**When Cleared**: When on-demand mode is stopped, or manually via cache management

**Structure**:
```json
{
  "plugin_id": "plugin-name",
  "mode": "mode-name",
  "duration": 30.0,
  "pinned": true,
  "requested_at": 1234567890.123,
  "expires_at": 1234567920.123
}
```

### 3. `display_on_demand_state`
**Purpose**: Current on-demand state (read-only, published by display controller)  
**TTL**: None (updated continuously)  
**When Set**: Continuously updated by display controller  
**When Cleared**: Automatically when on-demand ends, or manually via cache management

**Structure**:
```json
{
  "active": true,
  "mode": "mode-name",
  "plugin_id": "plugin-name",
  "requested_at": 1234567890.123,
  "expires_at": 1234567920.123,
  "duration": 30.0,
  "pinned": true,
  "status": "active" | "idle" | "restarting" | "error",
  "error": null,
  "last_event": "started",
  "remaining": 25.5,
  "last_updated": 1234567895.123
}
```

### 4. `display_on_demand_processed_id`
**Purpose**: Tracks which request_id has been processed (prevents duplicate processing)  
**TTL**: 1 hour  
**When Set**: When a request is processed  
**When Cleared**: Automatically expires, or manually via cache management

**Structure**: Just a string (the request_id)

## When Manual Clearing is Needed

### Scenario 1: Stuck On-Demand State
**Symptoms**: 
- Display stuck showing only one plugin
- "Stop On-Demand" button doesn't work
- Display controller shows on-demand as active but it shouldn't be

**Solution**: Clear these keys:
- `display_on_demand_config` - Removes the active configuration
- `display_on_demand_state` - Resets the published state
- `display_on_demand_request` - Clears any pending requests

**How to Clear**: Use the Cache Management tab in the web UI:
1. Go to Cache Management tab
2. Find the keys starting with `display_on_demand_`
3. Click "Delete" for each one
4. Restart the display service: `sudo systemctl restart ledmatrix`

### Scenario 2: Infinite Restart Loop
**Symptoms**:
- Display keeps restarting repeatedly
- Logs show "Activating on-demand mode... restarting display controller" in a loop

**Solution**: Clear these keys:
- `display_on_demand_request` - Stops the restart trigger
- `display_on_demand_processed_id` - Allows new requests to be processed

**How to Clear**: Same as Scenario 1, but focus on `display_on_demand_request` first

### Scenario 3: On-Demand Not Activating
**Symptoms**:
- Clicking "Run On-Demand" does nothing
- No errors in logs, but on-demand doesn't start

**Solution**: Clear these keys:
- `display_on_demand_processed_id` - May be blocking new requests
- `display_on_demand_request` - Clear any stale requests

**How to Clear**: Same as Scenario 1

### Scenario 4: After Service Crash or Unexpected Shutdown
**Symptoms**:
- Service was stopped unexpectedly (power loss, crash, etc.)
- On-demand state may be inconsistent

**Solution**: Clear all on-demand keys:
- `display_on_demand_config`
- `display_on_demand_state`
- `display_on_demand_request`
- `display_on_demand_processed_id`

**How to Clear**: Same as Scenario 1, clear all four keys

## Does Clearing from Cache Management Tab Reset It?

**Yes, but with caveats:**

1. **Clearing `display_on_demand_state`**: 
   - ✅ Removes the published state from cache
   - ⚠️ **Does NOT** immediately clear the in-memory state in the running display controller
   - The display controller will continue using its internal state until it polls for updates or restarts

2. **Clearing `display_on_demand_config`**:
   - ✅ Removes the configuration from cache
   - ⚠️ **Does NOT** immediately affect a running display controller
   - The display controller only reads this on startup/restart

3. **Clearing `display_on_demand_request`**:
   - ✅ Prevents new requests from being processed
   - ✅ Stops restart loops if that's the issue
   - ⚠️ **Does NOT** stop an already-active on-demand session

4. **Clearing `display_on_demand_processed_id`**:
   - ✅ Allows previously-processed requests to be processed again
   - Useful if a request got stuck

## Best Practice for Manual Clearing

**To fully reset on-demand state:**

1. **Stop the display service** (if possible):
   ```bash
   sudo systemctl stop ledmatrix
   ```

2. **Clear all on-demand cache keys** via Cache Management tab:
   - `display_on_demand_config`
   - `display_on_demand_state`
   - `display_on_demand_request`
   - `display_on_demand_processed_id`

3. **Clear systemd environment variable** (if set):
   ```bash
   sudo systemctl unset-environment LEDMATRIX_ON_DEMAND_PLUGIN
   ```

4. **Restart the display service**:
   ```bash
   sudo systemctl start ledmatrix
   ```

## Automatic Cleanup

The display controller automatically:
- Clears `display_on_demand_config` when on-demand mode is stopped
- Updates `display_on_demand_state` continuously
- Expires `display_on_demand_request` after processing
- Expires `display_on_demand_processed_id` after 1 hour

## Troubleshooting

If clearing cache keys doesn't resolve the issue:

1. **Check logs**: `sudo journalctl -u ledmatrix -f`
2. **Check service status**: `sudo systemctl status ledmatrix`
3. **Check environment variables**: `sudo systemctl show ledmatrix | grep LEDMATRIX`
4. **Check cache files directly**: `ls -la /var/cache/ledmatrix/display_on_demand_*`

## Related Files

- `src/display_controller.py` - Main on-demand logic
- `web_interface/blueprints/api_v3.py` - API endpoints for on-demand
- `web_interface/templates/v3/partials/cache.html` - Cache management UI
