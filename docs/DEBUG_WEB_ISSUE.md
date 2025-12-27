# Debug: Service Deactivated After Installing Dependencies

## What Happened

The service:
1. ✅ Started successfully
2. ✅ Installed dependencies
3. ❌ Deactivated successfully (exited cleanly)

This means it finished running but didn't actually launch the Flask app.

## Most Likely Cause

**`web_display_autostart` is probably set to `false` in your config.json**

The service is designed to exit gracefully if this is false - it won't even try to start Flask.

## Commands to Run RIGHT NOW

### 1. Check the full logs to see what it said before exiting:
```bash
sudo journalctl -u ledmatrix-web -n 200 --no-pager | grep -A 5 -B 5 "web_display_autostart\|Configuration\|Launching\|will not"
```

This will show you if it said something like:
- "Configuration 'web_display_autostart' is false or not set. Web interface will not be started."

### 2. Check your config.json:
```bash
cat ~/LEDMatrix/config/config.json | grep web_display_autostart
```

### 3. If it's false or missing, set it to true:
```bash
nano ~/LEDMatrix/config/config.json
```

Find the line with `web_display_autostart` and change it to:
```json
"web_display_autostart": true,
```

If the line doesn't exist, add it near the top of the file (after the opening `{`):
```json
{
  "web_display_autostart": true,
  ... rest of config ...
}
```

### 4. After fixing the config, restart the service:
```bash
sudo systemctl restart ledmatrix-web
```

### 5. Watch it start up:
```bash
sudo journalctl -u ledmatrix-web -f
```

You should see:
- "Configuration 'web_display_autostart' is true. Starting web interface..."
- "Dependencies installed successfully"
- "Launching web interface v3: ..."
- Flask starting up

## Alternative: View ALL Recent Logs

To see everything that happened:
```bash
sudo journalctl -u ledmatrix-web --since "5 minutes ago" --no-pager
```

This will show you the complete log including what happened after dependency installation.

