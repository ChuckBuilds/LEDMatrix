# AP Mode Manual Enable - Implementation Summary

## Changes Made

### 1. Configuration Option Added

Added `auto_enable_ap_mode` configuration option to `config/wifi_config.json`:
- **Default value**: `false` (manual enable only)
- **Purpose**: Controls whether AP mode automatically enables when WiFi/Ethernet disconnect
- **Migration**: Existing configs automatically get this field set to `false` if missing

### 2. WiFi Manager Updates (`src/wifi_manager.py`)

#### Added Configuration Field
- Default config now includes `"auto_enable_ap_mode": False`
- Existing configs are automatically migrated to include this field

#### Updated `check_and_manage_ap_mode()` Method
- Now checks `auto_enable_ap_mode` setting before auto-enabling AP mode
- AP mode only auto-enables if:
  - `auto_enable_ap_mode` is `true` AND
  - WiFi is NOT connected AND
  - Ethernet is NOT connected
- AP mode still auto-disables when WiFi or Ethernet connects (regardless of setting)
- Manual AP mode (via web UI) works regardless of this setting

### 3. Web Interface API Updates (`web_interface/blueprints/api_v3.py`)

#### Updated `/wifi/status` Endpoint
- Now returns `auto_enable_ap_mode` setting in response

#### Added `/wifi/ap/auto-enable` GET Endpoint
- Returns current `auto_enable_ap_mode` setting

#### Added `/wifi/ap/auto-enable` POST Endpoint
- Allows setting `auto_enable_ap_mode` via API
- Accepts JSON: `{"auto_enable_ap_mode": true/false}`

### 4. Documentation Updates

- Updated `docs/WIFI_SETUP.md` with new configuration option
- Created `docs/AP_MODE_MANUAL_ENABLE.md` with comprehensive guide
- Created `docs/AP_MODE_MANUAL_ENABLE_CHANGES.md` (this file)

## Behavior Changes

### Before
- AP mode automatically enabled when WiFi disconnected (if Ethernet also disconnected)
- Could cause SSH to become unavailable after installation
- No way to disable auto-enable behavior

### After
- AP mode **does not** automatically enable by default
- Must be manually enabled through web UI or API
- Can optionally enable auto-enable via configuration
- Prevents unexpected AP mode activation

## Migration

### Existing Installations

1. **Automatic Migration**:
   - When WiFi manager loads config, it automatically adds `auto_enable_ap_mode: false` if missing
   - No manual intervention required

2. **To Enable Auto-Enable** (if desired):
   ```bash
   # Edit config file
   nano config/wifi_config.json
   # Set "auto_enable_ap_mode": true
   
   # Restart WiFi monitor service
   sudo systemctl restart ledmatrix-wifi-monitor
   ```

### New Installations

- Default behavior is manual enable only
- No changes needed

## Testing

### Verify Default Behavior

```bash
# Check config
python3 -c "
from src.wifi_manager import WiFiManager
wm = WiFiManager()
print('Auto-enable:', wm.config.get('auto_enable_ap_mode', False))
"
# Should output: Auto-enable: False
```

### Test Manual Enable

1. Disconnect WiFi and Ethernet
2. AP mode should **not** automatically enable
3. Enable via web UI: WiFi tab → Enable AP Mode
4. AP mode should activate
5. Connect WiFi or Ethernet
6. AP mode should automatically disable

### Test Auto-Enable (if enabled)

1. Set `auto_enable_ap_mode: true` in config
2. Restart WiFi monitor service
3. Disconnect WiFi and Ethernet
4. AP mode should automatically enable within 30 seconds
5. Connect WiFi or Ethernet
6. AP mode should automatically disable

## API Usage Examples

### Get Auto-Enable Setting
```bash
curl http://localhost:5001/api/v3/wifi/ap/auto-enable
```

### Set Auto-Enable to True
```bash
curl -X POST http://localhost:5001/api/v3/wifi/ap/auto-enable \
  -H "Content-Type: application/json" \
  -d '{"auto_enable_ap_mode": true}'
```

### Set Auto-Enable to False
```bash
curl -X POST http://localhost:5001/api/v3/wifi/ap/auto-enable \
  -H "Content-Type: application/json" \
  -d '{"auto_enable_ap_mode": false}'
```

### Get WiFi Status (includes auto-enable)
```bash
curl http://localhost:5001/api/v3/wifi/status
```

## Files Modified

1. `src/wifi_manager.py`
   - Added `auto_enable_ap_mode` to default config
   - Added migration logic for existing configs
   - Updated `check_and_manage_ap_mode()` to respect setting

2. `web_interface/blueprints/api_v3.py`
   - Updated `/wifi/status` to include auto-enable setting
   - Added `/wifi/ap/auto-enable` GET endpoint
   - Added `/wifi/ap/auto-enable` POST endpoint

3. `docs/WIFI_SETUP.md`
   - Updated documentation with new configuration option
   - Updated WiFi monitor daemon description

4. `docs/AP_MODE_MANUAL_ENABLE.md` (new)
   - Comprehensive guide for manual enable feature

## Benefits

1. **Prevents SSH Loss**: AP mode won't activate automatically after installation
2. **User Control**: Users can choose whether to enable auto-enable
3. **Ethernet-Friendly**: Works well with hardwired connections
4. **Backward Compatible**: Existing installations automatically migrate
5. **Flexible**: Can still enable auto-enable if desired

## Deployment

### On Existing Installations

1. **No action required** - automatic migration on next WiFi manager initialization
2. **Restart WiFi monitor** (optional, to apply immediately):
   ```bash
   sudo systemctl restart ledmatrix-wifi-monitor
   ```

### On New Installations

- Default behavior is already manual enable
- No additional configuration needed

## Related Issues Fixed

- SSH becoming unavailable after installation
- AP mode activating when Ethernet is connected
- Unexpected AP mode activation on stable network connections

