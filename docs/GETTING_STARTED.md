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
2. Open browser to: `http://192.168.4.1:5000`
3. Navigate to the WiFi tab
4. Click "Scan" to find your WiFi network
5. Select your network, enter password
6. Click "Connect"
7. Wait for connection (LED matrix will show confirmation)

**If already connected to WiFi:**
1. Find your Pi's IP address (check your router, or run `hostname -I` on the Pi)
2. Open browser to: `http://your-pi-ip:5000`

### 3. Access the Web Interface

Once connected, access the web interface:

```
http://your-pi-ip:5000
```

You should see:
- Overview tab with system stats
- Live display preview
- Quick action buttons

---

## Initial Configuration (15 Minutes)

### Step 1: Configure Display Hardware

1. Open the **Display** tab
2. Set your matrix configuration:
   - **Rows**: 32 or 64 (match your hardware)
   - **Columns**: commonly 64 or 96; the web UI accepts any integer
     in the 16–128 range, but 64 and 96 are the values the bundled
     panel hardware ships with
   - **Chain Length**: Number of panels chained horizontally
   - **Hardware Mapping**: usually `adafruit-hat-pwm` (with the PWM jumper
     mod) or `adafruit-hat` (without). See the root README for the full list.
   - **Brightness**: 70–90 is fine for indoor use
3. Click **Save**
4. From the **Overview** tab, click **Restart Display Service** to apply

**Tip:** if the display shows garbage or nothing, the most common culprits
are an incorrect `hardware_mapping`, a `gpio_slowdown` value that doesn't
match your Pi model, or panels needing the E-line mod. See
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

### Step 2: Set Timezone and Location

1. Open the **General** tab
2. Set your timezone (e.g., `America/New_York`) and location
3. Click **Save**

Correct timezone ensures accurate time display, and location is used by
weather and other location-aware plugins.

### Step 3: Install Plugins

1. Open the **Plugin Manager** tab
2. Scroll to the **Plugin Store** section to browse available plugins
3. Click **Install** on the plugins you want
4. Wait for installation to finish — installed plugins appear in the
   **Installed Plugins** section above and get their own tab in the second
   nav row
5. Toggle the plugin to enabled
6. From **Overview**, click **Restart Display Service**

You can also install community plugins straight from a GitHub URL using the
**Install from GitHub** section further down the same tab — see
[PLUGIN_STORE_GUIDE.md](PLUGIN_STORE_GUIDE.md) for details.

### Step 4: Configure Plugins

1. Each installed plugin gets its own tab in the second navigation row
2. Open that plugin's tab to edit its settings (favorite teams, API keys,
   update intervals, display duration, etc.)
3. Click **Save**
4. Restart the display service from **Overview** so the new settings take
   effect

**Example: Weather Plugin**
- Set your location (city, state, country)
- Add an API key from OpenWeatherMap (free signup) to
  `config/config_secrets.json` or directly in the plugin's config screen
- Set the update interval (300 seconds is reasonable)

---

## Testing Your Display

### Run a single plugin on demand

The fastest way to verify a plugin works without waiting for the rotation:

1. Open the plugin's tab (second nav row)
2. Scroll to **On-Demand Controls**
3. Click **Run On-Demand** — the plugin runs immediately even if disabled
4. Click **Stop On-Demand** to return to the normal rotation

### Check the live preview and logs

- The **Overview** tab shows a **Live Display Preview** that mirrors what's
  on the matrix in real time — handy for debugging without looking at the
  panel.
- The **Logs** tab streams the display and web service logs. Look for
  `ERROR` lines if something isn't working; normal operation just shows
  `INFO` messages about plugin rotation.

---

## Common First-Time Issues

### Display Not Showing Anything

**Check:**
1. Power supply connected and adequate (5V, 4A minimum)
2. LED matrix connected to the bonnet/HAT correctly
3. Display service running: `sudo systemctl status ledmatrix`
4. Hardware configuration matches your matrix (rows/cols/chain length)

**Fix:**
1. Restart from the **Overview** tab → **Restart Display Service**
2. Or via SSH: `sudo systemctl restart ledmatrix`

### Web Interface Won't Load

**Check:**
1. Pi is connected to network: `ping your-pi-ip`
2. Web service running: `sudo systemctl status ledmatrix-web`
3. Correct port: the web UI listens on `:5000`
4. Firewall not blocking port 5000

**Fix:**
1. Restart web service: `sudo systemctl restart ledmatrix-web`
2. Check logs: `sudo journalctl -u ledmatrix-web -n 50`

### Plugins Not Showing

**Check:**
1. Plugin is enabled (toggle on the **Plugin Manager** tab)
2. Display service was restarted after enabling
3. Plugin's display duration is non-zero
4. No errors in the **Logs** tab for that plugin

**Fix:**
1. Enable the plugin from **Plugin Manager**
2. Click **Restart Display Service** on **Overview**
3. Check the **Logs** tab for plugin-specific errors

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

**Adjust display durations:**
- Each plugin's tab has a **Display Duration (seconds)** field — set how
  long that plugin stays on screen each rotation.

**Organize plugin order:**
- Use the **Plugin Manager** tab to enable/disable plugins. The display
  cycles through enabled plugins in the order they appear.

**Add more plugins:**
- Check the **Plugin Store** section of **Plugin Manager** for new plugins.
- Install community plugins straight from a GitHub URL via
  **Install from GitHub** on the same tab.

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
├── plugin-repos/             # Installed plugins (default location)
├── cache/                    # Cached data
└── web_interface/            # Web interface files
```

> The plugin install location is configurable via
> `plugin_system.plugins_directory` in `config.json`. The default is
> `plugin-repos/`. Plugin discovery (`PluginManager.discover_plugins()`)
> only scans the configured directory — it does not fall back to
> `plugins/`. However, the Plugin Store install/update path and the
> web UI's schema loader do also probe `plugins/` so the dev symlinks
> created by `scripts/dev/dev_plugin_setup.sh` keep working.

### Web Interface

```
Main Interface: http://your-pi-ip:5000

System tabs:
- Overview          System stats, live preview, quick actions
- General           Timezone, location, plugin-system settings
- WiFi              Network selection and AP-mode setup
- Schedule          Power and dim schedules
- Display           Matrix hardware configuration
- Config Editor     Raw config.json editor
- Fonts             Upload and manage fonts
- Logs              Real-time log viewing
- Cache             Cached data inspection and cleanup
- Operation History Recent service operations

Plugin tabs (second row):
- Plugin Manager    Browse the Plugin Store, install/enable plugins
- <plugin-id>       One tab per installed plugin for its config
```

### WiFi Access Point

```
Network Name: LEDMatrix-Setup
Password: (none - open network)
URL when connected: http://192.168.4.1:5000
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
