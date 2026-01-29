# Getting Started with LEDMatrix

## Welcome

This guide will help you set up your LEDMatrix display for the first time and get it running in under 30 minutes.

---

## Prerequisites

**Hardware:**
- Raspberry Pi (3, 4, or 5 recommended)
- RGB LED Matrix panel (32x64 or 64x64)
- Adafruit RGB Matrix HAT or similar
- Power supply (5V, 4A minimum recommended)
- MicroSD card (16GB minimum)

**Network:**
- WiFi network (or Ethernet cable)
- Computer with web browser on same network

---

## Quick Start (5 Minutes)

### 1. First Boot

1. Insert the MicroSD card with LEDMatrix installed
2. Connect the LED matrix to your Raspberry Pi
3. Plug in the power supply
4. Wait for the Pi to boot (about 60 seconds)

**Expected Behavior:**
- LED matrix will light up
- Display will show default plugins (clock, weather, etc.)
- Pi creates WiFi network "LEDMatrix-Setup" if not connected

### 2. Connect to WiFi

**If you see "LEDMatrix-Setup" WiFi network:**
1. Connect your device to "LEDMatrix-Setup" (open network, no password)
2. Open browser to: `http://192.168.4.1:5050`
3. Navigate to the WiFi tab
4. Click "Scan" to find your WiFi network
5. Select your network, enter password
6. Click "Connect"
7. Wait for connection (LED matrix will show confirmation)

**If already connected to WiFi:**
1. Find your Pi's IP address (check your router, or run `hostname -I` on the Pi)
2. Open browser to: `http://your-pi-ip:5050`

### 3. Access the Web Interface

Once connected, access the web interface:

```
http://your-pi-ip:5050
```

You should see:
- Overview tab with system stats
- Live display preview
- Quick action buttons

---

## Initial Configuration (15 Minutes)

### Step 1: Configure Display Hardware

1. Navigate to Settings → **Display Settings**
2. Set your matrix configuration:
   - **Rows**: 32 or 64 (match your hardware)
   - **Columns**: 64, 128, or 256 (match your hardware)
   - **Chain Length**: Number of panels chained together
   - **Brightness**: 50-75% recommended for indoor use
3. Click **Save Configuration**
4. Click **Restart Display** to apply changes

**Tip:** If the display doesn't look right, try different hardware mapping options.

### Step 2: Set Timezone and Location

1. Navigate to Settings → **General Settings**
2. Set your timezone (e.g., "America/New_York")
3. Set your location (city, state, country)
4. Click **Save Configuration**

**Why it matters:** Correct timezone ensures accurate time display. Location enables weather and location-based features.

### Step 3: Install Plugins

1. Navigate to **Plugin Store** tab
2. Browse available plugins:
   - **Time & Date**: Clock, calendar
   - **Weather**: Weather forecasts
   - **Sports**: NHL, NBA, NFL, MLB scores
   - **Finance**: Stocks, crypto
   - **Custom**: Community plugins
3. Click **Install** on desired plugins
4. Wait for installation to complete
5. Navigate to **Plugin Management** tab
6. Enable installed plugins (toggle switch)
7. Click **Restart Display**

**Popular First Plugins:**
- `clock-simple` - Simple digital clock
- `weather` - Weather forecast
- `nhl-scores` - NHL scores (if you're a hockey fan)

### Step 4: Configure Plugins

1. Navigate to **Plugin Management** tab
2. Find a plugin you installed
3. Click the ⚙️ **Configure** button
4. Edit settings (e.g., favorite teams, update intervals)
5. Click **Save**
6. Click **Restart Display**

**Example: Weather Plugin**
- Set your location (city, state, country)
- Add API key from OpenWeatherMap (free signup)
- Set update interval (300 seconds recommended)

---

## Testing Your Display

### Quick Test

1. Navigate to **Overview** tab
2. Click **Test Display** button
3. You should see a test pattern on your LED matrix

### Manual Plugin Trigger

1. Navigate to **Plugin Management** tab
2. Find a plugin
3. Click **Show Now** button
4. The plugin should display immediately
5. Click **Stop** to return to rotation

### Check Logs

1. Navigate to **Logs** tab
2. Watch real-time logs
3. Look for any ERROR messages
4. Normal operation shows INFO messages about plugin rotation

---

## Common First-Time Issues

### Display Not Showing Anything

**Check:**
1. Power supply connected and adequate (5V, 4A minimum)
2. LED matrix connected to GPIO pins correctly
3. Display service running: `sudo systemctl status ledmatrix`
4. Hardware configuration matches your matrix (rows/columns)

**Fix:**
1. Restart display: Settings → Overview → Restart Display
2. Or via SSH: `sudo systemctl restart ledmatrix`

### Web Interface Won't Load

**Check:**
1. Pi is connected to network: `ping your-pi-ip`
2. Web service running: `sudo systemctl status ledmatrix-web`
3. Correct port: Use `:5050` not `:5000`
4. Firewall not blocking port 5050

**Fix:**
1. Restart web service: `sudo systemctl restart ledmatrix-web`
2. Check logs: `sudo journalctl -u ledmatrix-web -n 50`

### Plugins Not Showing

**Check:**
1. Plugins are enabled (toggle switch in Plugin Management)
2. Display has been restarted after enabling
3. Plugin duration is reasonable (not too short)
4. No errors in logs for the plugin

**Fix:**
1. Enable plugin in Plugin Management
2. Restart display
3. Check logs for plugin-specific errors

### Weather Plugin Shows "No Data"

**Check:**
1. API key configured (OpenWeatherMap)
2. Location is correct (city, state, country)
3. Internet connection working

**Fix:**
1. Sign up at openweathermap.org (free)
2. Add API key to config_secrets.json or plugin config
3. Restart display

---

## Next Steps

### Customize Your Display

**Adjust Display Durations:**
- Navigate to Settings → Durations
- Set how long each plugin displays
- Save and restart

**Organize Plugin Order:**
- Use Plugin Management to enable/disable plugins
- Display cycles through enabled plugins in order

**Add More Plugins:**
- Check Plugin Store regularly for new plugins
- Install from GitHub URLs for custom/community plugins

### Enable Advanced Features

**Vegas Scroll Mode:**
- Continuous scrolling ticker display
- See [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) for details

**On-Demand Display:**
- Manually trigger specific plugins
- Pin important information
- See [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) for details

**Background Services:**
- Non-blocking data fetching
- Faster plugin rotation
- See [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) for details

### Explore Documentation

- [WEB_INTERFACE_GUIDE.md](WEB_INTERFACE_GUIDE.md) - Complete web interface guide
- [WIFI_NETWORK_SETUP.md](WIFI_NETWORK_SETUP.md) - WiFi configuration details
- [PLUGIN_STORE_GUIDE.md](PLUGIN_STORE_GUIDE.md) - Installing and managing plugins
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Solving common issues
- [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) - Advanced functionality

### Join the Community

- Report issues on GitHub
- Share your custom plugins
- Help others in discussions
- Contribute improvements

---

## Quick Reference

### Service Commands

```bash
# Check status
sudo systemctl status ledmatrix
sudo systemctl status ledmatrix-web

# Restart services
sudo systemctl restart ledmatrix
sudo systemctl restart ledmatrix-web

# View logs
sudo journalctl -u ledmatrix -f
sudo journalctl -u ledmatrix-web -f
```

### File Locations

```
/home/ledpi/LEDMatrix/
├── config/
│   ├── config.json           # Main configuration
│   ├── config_secrets.json   # API keys and secrets
│   └── wifi_config.json      # WiFi settings
├── plugins/                  # Installed plugins
├── cache/                    # Cached data
└── web_interface/            # Web interface files
```

### Web Interface

```
Main Interface: http://your-pi-ip:5050

Tabs:
- Overview: System stats and quick actions
- General Settings: Timezone, location, autostart
- Display Settings: Hardware configuration
- Durations: Plugin display times
- Sports Configuration: Per-league settings
- Plugin Management: Enable/disable, configure
- Plugin Store: Install new plugins
- Font Management: Upload and manage fonts
- Logs: Real-time log viewing
```

### WiFi Access Point

```
Network Name: LEDMatrix-Setup
Password: (none - open network)
URL when connected: http://192.168.4.1:5050
```

---

## Congratulations!

Your LEDMatrix display is now set up and running. Explore the web interface, try different plugins, and customize it to your liking.

**Need Help?**
- Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Review detailed guides for specific features
- Report issues on GitHub
- Ask questions in community discussions

Enjoy your LED matrix display!
