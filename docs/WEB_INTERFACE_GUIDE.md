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
   http://your-pi-ip:5050
   ```

3. The interface will load with the Overview tab displaying system stats and a live display preview.

**Note:** If the interface doesn't load, verify the web service is running:
```bash
sudo systemctl status ledmatrix-web
```

---

## Navigation

The interface uses a tab-based layout for easy navigation between features:

- **Overview** - System stats, quick actions, and display preview
- **General Settings** - Timezone, location, and autostart configuration
- **Display Settings** - Hardware configuration, brightness, and display options
- **Durations** - Display rotation timing configuration
- **Sports Configuration** - Per-league settings and on-demand modes
- **Plugin Management** - Install, configure, enable/disable plugins
- **Plugin Store** - Discover and install plugins
- **Font Management** - Upload fonts, manage overrides, and preview
- **Logs** - Real-time log streaming with filtering and search

---

## Features and Usage

### Overview Tab

The Overview tab provides at-a-glance information and quick actions:

**System Stats:**
- CPU usage and temperature
- Memory usage
- Disk usage
- Network status

**Quick Actions:**
- **Start/Stop Display** - Control the display service
- **Restart Display** - Restart to apply configuration changes
- **Test Display** - Run a quick test pattern

**Display Preview:**
- Live preview of what's currently shown on the LED matrix
- Updates in real-time
- Useful for remote monitoring

### General Settings Tab

Configure basic system settings:

**Timezone:**
- Set your local timezone for accurate time display
- Auto-detects common timezones

**Location:**
- Set latitude/longitude for location-based features
- Used by weather plugins and sunrise/sunset calculations

**Autostart:**
- Enable/disable display autostart on boot
- Configure systemd service settings

**Save Changes:**
- Click "Save Configuration" to apply changes
- Restart the display for changes to take effect

### Display Settings Tab

Configure your LED matrix hardware:

**Matrix Configuration:**
- Rows: Number of LED rows (typically 32 or 64)
- Columns: Number of LED columns (typically 64, 128, or 256)
- Chain Length: Number of chained panels
- Parallel Chains: Number of parallel chains

**Display Options:**
- Brightness: Adjust LED brightness (0-100%)
- Hardware Mapping: GPIO pin mapping
- Slowdown GPIO: Timing adjustment for compatibility

**Save and Apply:**
- Changes require a display restart
- Use "Test Display" to verify configuration

### Durations Tab

Control how long each plugin displays:

**Global Settings:**
- Default Duration: Default time for plugins without specific durations
- Transition Speed: Speed of transitions between plugins

**Per-Plugin Durations:**
- Set custom display duration for each plugin
- Override global default for specific plugins
- Measured in seconds

### Sports Configuration Tab

Configure sports-specific settings:

**Per-League Settings:**
- Favorite teams
- Show favorite teams only
- Include scores/standings
- Refresh intervals

**On-Demand Modes:**
- Live Priority: Show live games immediately
- Game Day Mode: Enhanced display during game days
- Score Alerts: Highlight score changes

### Plugin Management Tab

Manage installed plugins:

**Plugin List:**
- View all installed plugins
- See plugin status (enabled/disabled)
- Check last update time

**Actions:**
- **Enable/Disable**: Toggle plugin using the switch
- **Configure**: Click ⚙️ to edit plugin settings
- **Update**: Update plugin to latest version
- **Uninstall**: Remove plugin completely

**Configuration:**
- Edit plugin-specific settings
- Changes are saved to `config/config.json`
- Restart display to apply changes

**Note:** See [PLUGIN_STORE_GUIDE.md](PLUGIN_STORE_GUIDE.md) for information on installing plugins.

### Plugin Store Tab

Discover and install new plugins:

**Browse Plugins:**
- View available plugins in the official store
- Filter by category (sports, weather, time, finance, etc.)
- Search by name, description, or author

**Install Plugins:**
- Click "Install" next to any plugin
- Wait for installation to complete
- Restart the display to activate

**Install from URL:**
- Install plugins from any GitHub repository
- Paste the repository URL in the "Install from URL" section
- Review the warning about unverified plugins
- Click "Install from URL"

**Plugin Information:**
- View plugin descriptions, ratings, and screenshots
- Check compatibility and requirements
- Read user reviews (when available)

### Font Management Tab

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

1. Navigate to the **Display Settings** tab
2. Adjust the **Brightness** slider (0-100%)
3. Click **Save Configuration**
4. Restart the display for changes to take effect

### Installing a New Plugin

1. Navigate to the **Plugin Store** tab
2. Browse or search for the desired plugin
3. Click **Install** next to the plugin
4. Wait for installation to complete
5. Restart the display
6. Enable the plugin in the **Plugin Management** tab

### Configuring a Plugin

1. Navigate to the **Plugin Management** tab
2. Find the plugin you want to configure
3. Click the ⚙️ **Configure** button
4. Edit the settings in the form
5. Click **Save**
6. Restart the display to apply changes

### Setting Favorite Sports Teams

1. Navigate to the **Sports Configuration** tab
2. Select the league (NHL, NBA, MLB, NFL)
3. Choose your favorite teams from the dropdown
4. Enable "Show favorite teams only" if desired
5. Click **Save Configuration**
6. Restart the display

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
- Swipe navigation between tabs

**Tips for Mobile:**
- Use landscape mode for better visibility
- Pinch to zoom on display preview
- Long-press for context menus

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
http://your-pi-ip:5050/api
```

**Common Endpoints:**
- `GET /api/config/main` - Get configuration
- `POST /api/config/main` - Update configuration
- `GET /api/system/status` - Get system status
- `POST /api/system/action` - Control display (start/stop/restart)
- `GET /api/plugins/installed` - List installed plugins

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

3. Check that port 5050 is not blocked by firewall
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
- Plugin directory: `/plugins/`
- Plugin config: `/config/config.json` (per-plugin sections)

---

## Related Documentation

- [PLUGIN_STORE_GUIDE.md](PLUGIN_STORE_GUIDE.md) - Installing and managing plugins
- [REST_API_REFERENCE.md](REST_API_REFERENCE.md) - Complete REST API documentation
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Troubleshooting common issues
- [FONT_MANAGER.md](FONT_MANAGER.md) - Font management details
