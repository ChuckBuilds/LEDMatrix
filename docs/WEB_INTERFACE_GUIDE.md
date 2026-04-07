# Web Interface Guide

## Overview

The LEDMatrix web interface provides a complete control panel for managing your LED matrix display. Access all features through a modern, responsive web interface that works on desktop, tablet, and mobile devices.

---

## Quick Start

### Accessing the Interface

1. Find your Raspberry Pi's IP address:
   ```bash
   hostname -I
   ```

2. Open a web browser and navigate to:
   ```
   http://your-pi-ip:5000
   ```

3. The interface will load with the Overview tab displaying system stats and a live display preview.

**Note:** If the interface doesn't load, verify the web service is running:
```bash
sudo systemctl status ledmatrix-web
```

---

## Navigation

The interface uses a two-row tab layout. The system tabs are always
present:

- **Overview** — System stats, quick actions, live display preview
- **General** — Timezone, location, plugin-system settings
- **WiFi** — Network selection and AP-mode setup
- **Schedule** — Power and dim schedules
- **Display** — Matrix hardware configuration (rows, cols, hardware
  mapping, GPIO slowdown, brightness, PWM)
- **Config Editor** — Raw `config.json` editor with validation
- **Fonts** — Upload and manage fonts
- **Logs** — Real-time log streaming
- **Cache** — Cached data inspection and cleanup
- **Operation History** — Recent service operations

A second nav row holds plugin tabs:

- **Plugin Manager** — browse the **Plugin Store** section, install
  plugins from GitHub, enable/disable installed plugins
- **&lt;plugin-id&gt;** — one tab per installed plugin for its own
  configuration form (auto-generated from the plugin's
  `config_schema.json`)

---

## Features and Usage

### Overview Tab

The Overview tab provides at-a-glance information and quick actions:

**System Stats:**
- CPU usage and temperature
- Memory usage
- Disk usage
- Network status

**Quick Actions** (verified in `web_interface/templates/v3/partials/overview.html`):
- **Start Display** / **Stop Display** — control the display service
- **Restart Display Service** — apply configuration changes
- **Restart Web Service** — restart the web UI itself
- **Update Code** — `git pull` the latest version (stashes local changes)
- **Reboot System** / **Shutdown System** — confirm-gated power controls

**Display Preview:**
- Live preview of what's currently shown on the LED matrix
- Updates in real-time
- Useful for remote monitoring

### General Tab

Configure basic system settings:

- **Timezone** — used by all time/date displays
- **Location** — city/state/country for weather and other location-aware
  plugins
- **Plugin System Settings** — including the `plugins_directory` (default
  `plugin-repos/`) used by the plugin loader
- **Autostart** options for the display service

Click **Save** to write changes to `config/config.json`. Most changes
require a display service restart from **Overview**.

### Display Tab

Configure your LED matrix hardware:

**Matrix configuration:**
- `rows` — LED rows (typically 32 or 64)
- `cols` — LED columns (typically 64 or 96)
- `chain_length` — number of horizontally chained panels
- `parallel` — number of parallel chains
- `hardware_mapping` — `adafruit-hat-pwm` (with PWM jumper mod),
  `adafruit-hat` (without), `regular`, or `regular-pi1`
- `gpio_slowdown` — must match your Pi model (3 for Pi 3, 4 for Pi 4, etc.)
- `brightness` — 0–100%
- `pwm_bits`, `pwm_lsb_nanoseconds`, `pwm_dither_bits` — PWM tuning
- Dynamic Duration — global cap for plugins that extend their display
  time based on content

Changes require **Restart Display Service** from the Overview tab.

### Plugin Manager Tab

The Plugin Manager has three main sections:

1. **Installed Plugins** — toggle installed plugins on/off, see version
   info. Each installed plugin also gets its own tab in the second nav
   row for its configuration form.
2. **Plugin Store** — browse plugins from the official
   `ledmatrix-plugins` registry. Click **Install** to fetch and
   install. Filter by category and search.
3. **Install from GitHub** — install third-party plugins by pasting a
   GitHub repository URL. **Install Single Plugin** for a single-plugin
   repo, **Load Registry** for a multi-plugin monorepo.

When a plugin is installed and enabled:
- A new tab for that plugin appears in the second nav row
- Open the tab to edit its config (auto-generated form from
  `config_schema.json`)
- The tab also exposes **Run On-Demand** / **Stop On-Demand** controls
  to render that plugin immediately, even if it's disabled in the
  rotation

### Per-plugin Configuration Tabs

Each installed plugin has its own tab in the second nav row. The form
fields are auto-generated from the plugin's `config_schema.json`, so
options always match the plugin's current code.

To temporarily run a plugin outside the normal rotation, use the
**Run On-Demand** / **Stop On-Demand** buttons inside its tab. This
works even when the plugin is disabled.

### Fonts Tab

Manage fonts for your display:

**Upload Fonts:**
- Drag and drop font files (.ttf, .otf, .bdf)
- Upload multiple files at once
- Progress indicator shows upload status

**Font Catalog:**
- View all available fonts
- See font previews
- Check font sizes and styles

**Plugin Font Overrides:**
- Set custom fonts for specific plugins
- Override default font choices
- Preview font changes

**Delete Fonts:**
- Remove unused fonts
- Free up disk space

### Logs Tab

View real-time system logs:

**Log Viewer:**
- Streaming logs from the display service
- Auto-scroll to latest entries
- Timestamps for each log entry

**Filtering:**
- Filter by log level (INFO, WARNING, ERROR)
- Search for specific text
- Filter by plugin or component

**Actions:**
- **Clear**: Clear the current view
- **Download**: Download logs for offline analysis
- **Pause**: Pause auto-scrolling

---

## Common Tasks

### Changing Display Brightness

1. Open the **Display** tab
2. Adjust the **Brightness** slider (0–100)
3. Click **Save**
4. Click **Restart Display Service** on the **Overview** tab

### Installing a New Plugin

1. Open the **Plugin Manager** tab
2. Scroll to the **Plugin Store** section and browse or search
3. Click **Install** next to the plugin
4. Toggle the plugin on in **Installed Plugins**
5. Click **Restart Display Service** on **Overview**

### Configuring a Plugin

1. Open the plugin's tab in the second nav row (each installed plugin
   has its own tab)
2. Edit the auto-generated form
3. Click **Save**
4. Restart the display service from **Overview**

### Setting Favorite Sports Teams

Sports favorites live in the relevant plugin's tab — there is no
separate "Sports Configuration" tab. For example:

1. Install **Hockey Scoreboard** from **Plugin Manager → Plugin Store**
2. Open the **Hockey Scoreboard** tab in the second nav row
3. Add your favorites under `favorite_teams.<league>` (e.g.
   `favorite_teams.nhl`)
4. Click **Save** and restart the display service

### Troubleshooting Display Issues

1. Navigate to the **Logs** tab
2. Look for ERROR or WARNING messages
3. Filter by the problematic plugin or component
4. Check the error message for clues
5. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common solutions

---

## Real-Time Features

The web interface uses Server-Sent Events (SSE) for real-time updates:

**Live Updates:**
- System stats refresh automatically every few seconds
- Display preview updates in real-time
- Logs stream continuously
- No page refresh required

**Performance:**
- Minimal bandwidth usage
- Server-side rendering for fast load times
- Progressive enhancement - works without JavaScript

---

## Mobile Access

The interface is fully responsive and works on mobile devices:

**Mobile Features:**
- Touch-friendly interface
- Responsive layout adapts to screen size
- All features available on mobile

**Tips for Mobile:**
- Use landscape mode for better visibility
- Pinch to zoom on display preview

---

## Keyboard Shortcuts

Use keyboard shortcuts for faster navigation:

- **Tab**: Navigate between form fields
- **Enter**: Submit forms
- **Esc**: Close modals
- **Ctrl+F**: Search in logs

---

## API Access

The web interface is built on a REST API that you can access programmatically:

**API Base URL:**
```
http://your-pi-ip:5000/api/v3
```

The API blueprint mounts at `/api/v3` (see
`web_interface/app.py:144`). All endpoints below are relative to that
base.

**Common Endpoints:**
- `GET /api/v3/config/main` — Get main configuration
- `POST /api/v3/config/main` — Update main configuration
- `GET /api/v3/system/status` — Get system status
- `POST /api/v3/system/action` — Control display (start/stop/restart, reboot, etc.)
- `GET /api/v3/plugins/installed` — List installed plugins
- `POST /api/v3/plugins/install` — Install a plugin from the store
- `POST /api/v3/plugins/install-from-url` — Install a plugin from a GitHub URL

**Note:** See [REST_API_REFERENCE.md](REST_API_REFERENCE.md) for complete API documentation.

---

## Troubleshooting

### Interface Won't Load

**Problem:** Browser shows "Unable to connect" or "Connection refused"

**Solutions:**
1. Verify the web service is running:
   ```bash
   sudo systemctl status ledmatrix-web
   ```

2. Start the service if stopped:
   ```bash
   sudo systemctl start ledmatrix-web
   ```

3. Check that port 5000 is not blocked by firewall
4. Verify the Pi's IP address is correct

### Changes Not Applying

**Problem:** Configuration changes don't take effect

**Solutions:**
1. Ensure you clicked "Save Configuration"
2. Restart the display service for changes to apply:
   ```bash
   sudo systemctl restart ledmatrix
   ```
3. Check logs for error messages

### Display Preview Not Updating

**Problem:** Display preview shows old content or doesn't update

**Solutions:**
1. Refresh the browser page (F5)
2. Check that the display service is running
3. Verify SSE streams are working (check browser console)

### Plugin Configuration Not Saving

**Problem:** Plugin settings revert after restart

**Solutions:**
1. Check file permissions on `config/config.json`:
   ```bash
   ls -l config/config.json
   ```
2. Ensure the web service has write permissions
3. Check logs for permission errors

---

## Security Considerations

**Network Access:**
- The interface is accessible to anyone on your local network
- No authentication is currently implemented
- Recommended for trusted networks only

**Best Practices:**
1. Run on a private network (not exposed to internet)
2. Use a firewall to restrict access if needed
3. Consider VPN access for remote control
4. Keep the system updated

---

## Technical Details

### Architecture

The web interface uses modern web technologies:

- **Backend:** Flask with Blueprint-based modular design
- **Frontend:** HTMX for dynamic content, Alpine.js for reactive components
- **Styling:** Tailwind CSS for responsive design
- **Real-Time:** Server-Sent Events (SSE) for live updates

### File Locations

**Configuration:**
- Main config: `/config/config.json`
- Secrets: `/config/config_secrets.json`
- WiFi config: `/config/wifi_config.json`

**Logs:**
- Display service: `sudo journalctl -u ledmatrix -f`
- Web service: `sudo journalctl -u ledmatrix-web -f`

**Plugins:**
- Plugin directory: configurable via
  `plugin_system.plugins_directory` in `config.json` (default
  `plugin-repos/`). Main plugin discovery only scans this directory;
  the Plugin Store install flow and the schema loader additionally
  probe `plugins/` so dev symlinks created by
  `scripts/dev/dev_plugin_setup.sh` keep working.
- Plugin config: `/config/config.json` (per-plugin sections)

---

## Related Documentation

- [PLUGIN_STORE_GUIDE.md](PLUGIN_STORE_GUIDE.md) - Installing and managing plugins
- [REST_API_REFERENCE.md](REST_API_REFERENCE.md) - Complete REST API documentation
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Troubleshooting common issues
- [FONT_MANAGER.md](FONT_MANAGER.md) - Font management details
