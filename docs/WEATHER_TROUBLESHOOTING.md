# Weather Plugin Troubleshooting Guide

## Quick Diagnosis

Run the troubleshooting script on your Pi:

```bash
./troubleshoot_weather.sh
```

This will check:
- Plugin installation
- Configuration files
- API key setup
- Network connectivity
- Cache status

## Common Issues

### 1. "No Weather Data" Message

This appears when the weather plugin cannot fetch or access weather data.

### 2. Missing or Invalid API Key

**Symptoms:**
- Plugin shows "No Weather Data"
- Logs show "No valid OpenWeatherMap API key configured"
- Plugin initialized but no data updates

**Solution:**

1. Get an API key from [OpenWeatherMap](https://openweathermap.org/api)
   - Sign up for a free account
   - Navigate to API Keys section
   - Generate a new API key

2. Add API key to `config/config_secrets.json` (recommended):
   ```json
   {
     "ledmatrix-weather": {
       "api_key": "your_actual_api_key_here"
     }
   }
   ```

   OR add directly to `config/config.json`:
   ```json
   {
     "ledmatrix-weather": {
       "enabled": true,
       "api_key": "your_actual_api_key_here",
       "location_city": "Dallas",
       "location_state": "Texas",
       "location_country": "US"
     }
   }
   ```

3. Restart the LEDMatrix service:
   ```bash
   sudo systemctl restart ledmatrix
   ```

### 3. Plugin Not Enabled

**Symptoms:**
- Plugin doesn't appear in display rotation
- No weather data displayed

**Solution:**

Check `config/config.json` and ensure the plugin is enabled:

```json
{
  "ledmatrix-weather": {
    "enabled": true,
    "display_duration": 30,
    ...
  }
}
```

### 4. Network/API Connectivity Issues

**Symptoms:**
- Plugin shows "No Weather Data"
- Logs show connection errors or timeouts

**Solution:**

1. Check internet connectivity:
   ```bash
   ping -c 4 api.openweathermap.org
   ```

2. Check firewall settings (if applicable)

3. Verify DNS resolution:
   ```bash
   nslookup api.openweathermap.org
   ```

4. Test API directly:
   ```bash
   curl "https://api.openweathermap.org/data/2.5/weather?q=Dallas,TX,US&appid=YOUR_API_KEY&units=imperial"
   ```

### 5. API Rate Limits Exceeded

**Symptoms:**
- Plugin worked before but now shows "No Weather Data"
- Logs show HTTP 429 errors

**Solution:**

OpenWeatherMap free tier limits:
- 1,000 API calls per day
- 60 calls per minute

Default plugin settings use ~48 calls/day (1800s = 30 min intervals).

If exceeded:
- Wait for quota reset (daily)
- Increase `update_interval` in config (minimum 300s = 5 minutes)
- Upgrade OpenWeatherMap plan

### 6. Invalid Location Configuration

**Symptoms:**
- Plugin shows "No Weather Data"
- Logs show geocoding errors

**Solution:**

Ensure location is correctly configured in `config/config.json`:

```json
{
  "ledmatrix-weather": {
    "location_city": "Dallas",
    "location_state": "Texas",
    "location_country": "US"
  }
}
```

- Use proper city names
- Include state for US cities to avoid ambiguity
- Use ISO 3166-1 alpha-2 country codes (US, GB, CA, etc.)

### 7. Stale Cache Data

**Symptoms:**
- Weather data not updating
- Old data displayed

**Solution:**

Clear the cache:

```bash
# Find cache files
find cache/ -name "*weather*" -type f

# Remove cache files (plugin will fetch fresh data)
rm cache/*weather*
```

### 8. Plugin Not Loading

**Symptoms:**
- Weather modes don't appear in available modes
- Logs show plugin loading errors

**Solution:**

1. Check plugin directory exists:
   ```bash
   ls -la plugins/ledmatrix-weather/
   ```

2. Verify manifest.json is valid:
   ```bash
   python3 -m json.tool plugins/ledmatrix-weather/manifest.json
   ```

3. Check logs for specific errors:
   ```bash
   sudo journalctl -u ledmatrix -f | grep -i weather
   ```

4. Verify plugin dependencies are installed:
   ```bash
   pip3 install -r plugins/ledmatrix-weather/requirements.txt
   ```

## Checking Logs

View real-time logs:

```bash
sudo journalctl -u ledmatrix -f
```

Filter for weather-related messages:

```bash
sudo journalctl -u ledmatrix -f | grep -i weather
```

View last 100 lines:

```bash
sudo journalctl -u ledmatrix -n 100 | grep -i weather
```

## Configuration Example

Complete configuration in `config/config.json`:

```json
{
  "ledmatrix-weather": {
    "enabled": true,
    "display_duration": 30,
    "location_city": "Dallas",
    "location_state": "Texas",
    "location_country": "US",
    "units": "imperial",
    "update_interval": 1800,
    "show_current_weather": true,
    "show_hourly_forecast": true,
    "show_daily_forecast": true,
    "transition": {
      "type": "redraw",
      "speed": 2,
      "enabled": true
    }
  }
}
```

And in `config/config_secrets.json`:

```json
{
  "ledmatrix-weather": {
    "api_key": "your_openweathermap_api_key_here"
  }
}
```

## Plugin Configuration Schema

The plugin expects configuration under either:
- `ledmatrix-weather` (plugin ID from manifest)
- `weather` (legacy/deprecated)

The system checks both when loading configuration.

## Testing the Plugin

1. Enable the plugin in config
2. Restart the service: `sudo systemctl restart ledmatrix`
3. Check logs: `sudo journalctl -u ledmatrix -f`
4. Wait for update interval (default 30 minutes) or force update
5. Check if weather modes appear in display rotation

## Still Having Issues?

1. Run the troubleshooting script: `./troubleshoot_weather.sh`
2. Check service status: `sudo systemctl status ledmatrix`
3. Review logs for specific error messages
4. Verify all configuration files are valid JSON
5. Ensure file permissions are correct:
   ```bash
   ls -la config/config.json config/config_secrets.json
   ```

## API Key Security

**Recommended:** Store API key in `config/config_secrets.json` with restricted permissions:

```bash
chmod 640 config/config_secrets.json
```

This file is not tracked by git (should be in .gitignore).

## Plugin ID Note

The weather plugin ID is `ledmatrix-weather` (from manifest.json). Configuration should use this ID, though the system also checks for `weather` for backward compatibility.




