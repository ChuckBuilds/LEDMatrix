# Troubleshooting Guide

## Quick Diagnosis Steps

Run these checks first to quickly identify common issues:

### 1. Check Service Status

```bash
# Check all LEDMatrix services
sudo systemctl status ledmatrix
sudo systemctl status ledmatrix-web
sudo systemctl status ledmatrix-wifi-monitor

# Check AP mode services (if using WiFi)
sudo systemctl status hostapd
sudo systemctl status dnsmasq
```

**Note:** Look for `active (running)` status and check for error messages in the output.

### 2. View Service Logs

**IMPORTANT:** The web service logs to **syslog**, NOT stdout. Use `journalctl` to view logs:

```bash
# View all recent logs
sudo journalctl -u ledmatrix -n 50
sudo journalctl -u ledmatrix-web -n 50

# Follow logs in real-time
sudo journalctl -u ledmatrix -f

# View logs from last hour
sudo journalctl -u ledmatrix-web --since "1 hour ago"

# Filter for errors only
sudo journalctl -u ledmatrix -p err
```

### 3. Run Diagnostic Scripts

```bash
# Web interface diagnostics
bash scripts/diagnose_web_interface.sh

# WiFi setup verification
./scripts/verify_wifi_setup.sh

# Captive portal troubleshooting
./scripts/troubleshoot_captive_portal.sh
```

> Weather is provided by the `ledmatrix-weather` plugin (installed via the
> Plugin Store). To troubleshoot weather, check that plugin's tab in the
> web UI for its API key and recent error messages, then watch the
> **Logs** tab.

### 4. Check Configuration

```bash
# Verify web interface autostart
cat config/config.json | grep web_display_autostart

# Check plugin enabled status
cat config/config.json | grep -A 2 "plugin-id"

# Verify API keys present
ls -l config/config_secrets.json
```

### 5. Test Manual Startup

```bash
# Test web interface manually
python3 web_interface/start.py

# If it works manually but not as a service, check systemd service file
```

---

## Common Issues by Category

### Installation & Build Issues

#### Step 6 fails: "Failed building wheel for rgbmatrix"

**Symptoms:**

```
note: This error originates from a subprocess, and is likely not a problem with pip.
  ERROR: Failed building wheel for rgbmatrix
Failed to build rgbmatrix
✗ Failed to install rpi-rgb-led-matrix Python package
```

**Cause:**

Almost always the kernel's out-of-memory killer, not missing build tools. The
`rpi-rgb-led-matrix` library compiles roughly 45 C++ translation units, two of
them Cython-generated — a single `cc1plus` on those can peak near 800MB. The
build system defaults to running several of those at once, which exceeds RAM on
512MB and 1GB boards. Because the OOM killer writes nothing to pip's output, the
failure looks like a toolchain problem, and `sudo apt install -y
python-dev-is-python3 cmake build-essential` will report everything is already
up to date.

**How to confirm:**

```bash
dmesg -T | grep -i "out of memory"     # look for "Killed process ... (cc1plus)"
free -h                                 # total RAM and swap
```

**Fix:**

Current versions of the installer handle this automatically: they cap build
parallelism based on available RAM and add a temporary swapfile for the build,
removing it when the build finishes. If you are on an older checkout, or the
temporary swapfile could not be created, either force a serial compile:

```bash
sudo ./first_time_install.sh --build-jobs 1
```

or add permanent swap and re-run the installer, which resumes at Step 6:

```bash
sudo apt install -y dphys-swapfile
sudo sed -i 's/^#\?CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
sudo sed -i 's/^#\?CONF_MAXSWAP=.*/CONF_MAXSWAP=2048/' /etc/dphys-swapfile
sudo dphys-swapfile swapoff && sudo dphys-swapfile setup && sudo dphys-swapfile swapon
sudo ./first_time_install.sh
```

`CONF_MAXSWAP` matters: it defaults to 2048 and silently clamps `CONF_SWAPSIZE`,
so setting only `CONF_SWAPSIZE` to a larger value has no effect.

**Related:**

- The installer needs roughly 3GB free on the card to place the swapfile. If
  disk is tight it will say so and skip the swapfile: `sudo apt clean` first.
- `sudo bash scripts/check_system_compatibility.sh` reports RAM and disk.
- `sudo bash scripts/diagnose_dependencies.sh` dumps build-dependency state.

---

### Web Interface & Service Issues

#### Service Not Running/Starting

**Symptoms:**
- Cannot access web interface at http://your-pi-ip:5000
- `systemctl status ledmatrix-web` shows `inactive (dead)`

**Solutions:**

1. **Start the service:**
   ```bash
   sudo systemctl start ledmatrix-web
   ```

2. **Enable on boot:**
   ```bash
   sudo systemctl enable ledmatrix-web
   ```

3. **Check why it failed:**
   ```bash
   sudo journalctl -u ledmatrix-web -n 50
   ```

#### web_display_autostart is False

**Symptoms:**
- Service exists but web interface doesn't start automatically
- Logs show service starting but nothing happens

**Solution:**

```bash
# Edit config.json
nano config/config.json

# Set web_display_autostart to true
{
  "web_display_autostart": true,
  ...
}

# Restart service
sudo systemctl restart ledmatrix-web
```

#### Import or Dependency Errors

**Symptoms:**
- Logs show `ModuleNotFoundError` or `ImportError`
- Service fails to start with Python errors

**Solutions:**

1. **Install dependencies:**
   ```bash
   pip3 install --break-system-packages -r requirements.txt
   pip3 install --break-system-packages -r web_interface/requirements.txt
   ```

2. **Test imports step-by-step:**
   ```bash
   python3 -c "from src.config_manager import ConfigManager; print('OK')"
   python3 -c "from src.plugin_system.plugin_manager import PluginManager; print('OK')"
   python3 -c "from web_interface.app import app; print('OK')"
   ```

3. **Check Python path:**
   ```bash
   python3 -c "import sys; print(sys.path)"
   ```

#### Port Already in Use

**Symptoms:**
- Error: `Address already in use`
- Service fails to bind to port 5000

**Solutions:**

1. **Check what's using the port:**
   ```bash
   sudo lsof -i :5000
   ```

2. **Kill the conflicting process:**
   ```bash
   sudo kill -9 <PID>
   ```

3. **Or change the port in start.py:**
   ```python
   app.run(host='0.0.0.0', port=5051)
   ```

#### Permission Issues

**Symptoms:**
- `Permission denied` errors in logs
- Cannot read/write configuration files

**Solutions:**

```bash
# Fix ownership of LEDMatrix directory
sudo chown -R ledpi:ledpi /home/ledpi/LEDMatrix

# Fix config file permissions
sudo chmod 644 config/config.json
sudo chmod 640 config/config_secrets.json

# Verify service runs as correct user
sudo systemctl cat ledmatrix-web | grep User
```

#### Flask/Blueprint Import Errors

**Symptoms:**
- `ImportError: cannot import name 'app'`
- `ModuleNotFoundError: No module named 'blueprints'`

**Solutions:**

1. **Verify file structure:**
   ```bash
   ls -l web_interface/app.py
   ls -l web_interface/blueprints/api_v3.py
   ls -l web_interface/blueprints/pages_v3.py
   ```

2. **Check for __init__.py files:**
   ```bash
   ls -l web_interface/__init__.py
   ls -l web_interface/blueprints/__init__.py
   ```

3. **Test import manually:**
   ```bash
   cd /home/ledpi/LEDMatrix
   python3 -c "from web_interface.app import app"
   ```

---

### WiFi & AP Mode Issues

#### AP Mode Not Activating

**Symptoms:**
- WiFi disconnected but AP mode doesn't start
- Cannot find "LEDMatrix-Setup" network

**Solutions:**

1. **Check auto-enable setting:**
   ```bash
   cat config/wifi_config.json | grep auto_enable_ap_mode
   # Should show: "auto_enable_ap_mode": true
   ```

2. **Verify WiFi monitor service is running:**
   ```bash
   sudo systemctl status ledmatrix-wifi-monitor
   ```

3. **Wait for grace period (90 seconds):**
   - AP mode requires 3 consecutive disconnected checks at 30-second intervals
   - Total wait time: 90 seconds after WiFi disconnects

4. **Check if Ethernet is connected:**
   ```bash
   nmcli device status
   # If Ethernet is connected, AP mode won't activate
   ```

5. **Check required services:**
   ```bash
   sudo systemctl status hostapd
   sudo systemctl status dnsmasq
   ```

6. **Manually enable AP mode:**
   ```bash
   # Via API (the WiFi blueprint is mounted under /api/v3)
   curl -X POST http://localhost:5000/api/v3/wifi/ap/enable

   # Via Python
   python3 -c "
   from src.wifi_manager import WiFiManager
   wm = WiFiManager()
   wm.enable_ap_mode()
   "
   ```

#### Cannot Connect to AP Mode / Connection Refused

**Symptoms:**
- Can see "LEDMatrix-Setup" network but can't connect to web interface
- Browser shows "Connection Refused" or "Can't connect to server"
- AP mode active but web interface not accessible

**Solutions:**

1. **Verify web server is running:**
   ```bash
   sudo systemctl status ledmatrix-web
   # Should be active (running)
   ```

2. **Use correct IP address and port:**
   - Correct: `http://192.168.4.1:5000`
   - NOT: `http://192.168.4.1` (port 80 — nothing listens there)

3. **Check wlan0 has correct IP:**
   ```bash
   ip addr show wlan0
   # Should show: inet 192.168.4.1/24
   ```

4. **Verify hostapd and dnsmasq are running:**
   ```bash
   sudo systemctl status hostapd
   sudo systemctl status dnsmasq
   ```

5. **Test from the Pi itself:**
   ```bash
   curl http://192.168.4.1:5000
   # Should return HTML
   ```

#### DNS Resolution Failures

**Symptoms:**
- Captive portal doesn't redirect automatically
- DNS lookups fail when connected to AP mode

**Solutions:**

1. **Check dnsmasq status:**
   ```bash
   sudo systemctl status dnsmasq
   sudo journalctl -u dnsmasq -n 20
   ```

2. **Verify DNS configuration:**
   ```bash
   cat /etc/dnsmasq.conf | grep -v "^#" | grep -v "^$"
   ```

3. **Test DNS resolution:**
   ```bash
   nslookup captive.apple.com
   # Should resolve to 192.168.4.1 when in AP mode
   ```

4. **Manual captive portal testing:**
   - Try these URLs manually:
     - `http://192.168.4.1:5000`
     - `http://captive.apple.com`
     - `http://connectivitycheck.gstatic.com/generate_204`

#### Firewall Blocking Port 5000

**Symptoms:**
- Services running but cannot connect
- Works from Pi but not from other devices

**Solutions:**

1. **Check UFW status:**
   ```bash
   sudo ufw status
   ```

2. **Allow port 5000:**
   ```bash
   sudo ufw allow 5000/tcp
   ```

3. **Check iptables:**
   ```bash
   sudo iptables -L -n
   ```

4. **Temporarily disable firewall to test:**
   ```bash
   sudo ufw disable
   # Test if it works, then re-enable and add rule
   sudo ufw enable
   sudo ufw allow 5000/tcp
   ```

---

### Plugin Issues

#### Plugin Not Enabled

**Symptoms:**
- Plugin installed but doesn't appear in rotation
- Plugin shows in web interface but is greyed out

**Solutions:**

1. **Enable in configuration:**
   ```json
   {
     "plugin-id": {
       "enabled": true,
       ...
     }
   }
   ```

2. **Restart display:**
   ```bash
   sudo systemctl restart ledmatrix
   ```

3. **Verify in web interface:**
   - Open the **Plugin Manager** tab
   - Toggle the plugin switch to enable
   - From **Overview**, click **Restart Display Service**

#### Plugin Not Loading

**Symptoms:**
- Plugin enabled but not showing
- Errors in logs about plugin

**Solutions:**

1. **Check plugin directory exists:**
   ```bash
   ls -ld plugin-repos/plugin-id/
   ```

2. **Verify manifest.json:**
   ```bash
   cat plugin-repos/plugin-id/manifest.json
   # Verify all required fields present
   ```

3. **Check dependencies installed:**
   ```bash
   if [ -f plugin-repos/plugin-id/requirements.txt ]; then
     pip3 install --break-system-packages -r plugin-repos/plugin-id/requirements.txt
   fi
   ```

4. **Check logs for plugin errors:**
   ```bash
   sudo journalctl -u ledmatrix -f | grep plugin-id
   ```

5. **Test plugin import:**
   ```bash
   python3 -c "
   import sys
   sys.path.insert(0, 'plugin-repos/plugin-id')
   from manager import PluginClass
   print('Plugin imports successfully')
   "
   ```

#### Stale Cache Data

**Symptoms:**
- Plugin shows old data
- Data doesn't update even after restarting
- Clearing cache in web interface doesn't help

**Solutions:**

1. **Manual cache clearing:**

   The cache does not live in the project directory. The cache manager
   uses the first writable location among `/var/cache/ledmatrix`,
   `~/.ledmatrix_cache`, `/opt/ledmatrix/cache`, and
   `$TMPDIR/ledmatrix_cache`. The easiest option is the helper script:

   ```bash
   # Clear the cache with the helper script
   sudo python3 scripts/utils/clear_cache.py

   # Or remove files manually from the cache dir in use, e.g.:
   sudo rm -rf /var/cache/ledmatrix/*

   # Restart display
   sudo systemctl restart ledmatrix
   ```

2. **Check cache permissions:**
   ```bash
   ls -ld /var/cache/ledmatrix
   sudo ./scripts/fix_perms/fix_cache_permissions.sh
   ```

---

### Weather Plugin Specific Issues

#### Missing or Invalid API Key

**Symptoms:**
- "No Weather Data" message on display
- Logs show API authentication errors

**Solutions:**

1. **Get OpenWeatherMap API key:**
   - Sign up at https://openweathermap.org/api
   - Free tier: 1,000 calls/day, 60 calls/minute
   - Copy your API key

2. **Add to config_secrets.json (recommended):**
   ```json
   {
     "openweathermap_api_key": "your-api-key-here"
   }
   ```

3. **Or add to config.json:**
   ```json
   {
     "ledmatrix-weather": {
       "enabled": true,
       "openweathermap_api_key": "your-api-key-here",
       ...
     }
   }
   ```

4. **Secure the API key file:**
   ```bash
   chmod 640 config/config_secrets.json
   ```

5. **Restart display:**
   ```bash
   sudo systemctl restart ledmatrix
   ```

#### API Rate Limits Exceeded

**Symptoms:**
- Weather works initially then stops
- Logs show HTTP 429 errors (Too Many Requests)
- Error message: "Rate limit exceeded"

**Solutions:**

1. **Increase update interval:**
   ```json
   {
     "ledmatrix-weather": {
       "update_interval": 300,
       ...
     }
   }
   ```
   **Note:** Minimum recommended: 300 seconds (5 minutes)

2. **Check current rate limit usage:**
   - OpenWeatherMap free tier: 1,000 calls/day, 60 calls/minute
   - With 300s interval: 288 calls/day (well within limits)

3. **Monitor API calls:**
   ```bash
   sudo journalctl -u ledmatrix -f | grep "openweathermap"
   ```

#### Invalid Location Configuration

**Symptoms:**
- "No Weather Data" message
- Logs show location not found errors

**Solutions:**

1. **Use correct location format:**
   ```json
   {
     "ledmatrix-weather": {
       "city": "Tampa",
       "state": "FL",
       "country": "US"
     }
   }
   ```

2. **Use ISO country codes:**
   - US = United States
   - GB = United Kingdom
   - CA = Canada
   - etc.

3. **Test API call manually:**
   ```bash
   API_KEY="your-key-here"
   curl "http://api.openweathermap.org/data/2.5/weather?q=Tampa,FL,US&appid=${API_KEY}"
   ```

#### Network Connectivity to OpenWeatherMap

**Symptoms:**
- Other internet features work
- Weather specifically fails
- Connection timeout errors

**Solutions:**

1. **Test connectivity:**
   ```bash
   ping api.openweathermap.org
   ```

2. **Test DNS resolution:**
   ```bash
   nslookup api.openweathermap.org
   ```

3. **Test API endpoint:**
   ```bash
   curl -I https://api.openweathermap.org
   # Should return HTTP 200 or 301
   ```

4. **Check firewall:**
   ```bash
   # Ensure HTTPS (443) is allowed for outbound connections
   sudo ufw status
   ```

---

## Diagnostic Commands Reference

### Service Commands

```bash
# Check status
sudo systemctl status ledmatrix
sudo systemctl status ledmatrix-web
sudo systemctl status ledmatrix-wifi-monitor

# Start service
sudo systemctl start <service-name>

# Stop service
sudo systemctl stop <service-name>

# Restart service
sudo systemctl restart <service-name>

# Enable on boot
sudo systemctl enable <service-name>

# Disable on boot
sudo systemctl disable <service-name>

# View service file
sudo systemctl cat <service-name>

# Reload systemd after editing service files
sudo systemctl daemon-reload
```

### Log Viewing Commands

```bash
# View recent logs (last 50 lines)
sudo journalctl -u ledmatrix -n 50

# Follow logs in real-time
sudo journalctl -u ledmatrix -f

# View logs from specific time
sudo journalctl -u ledmatrix --since "1 hour ago"
sudo journalctl -u ledmatrix --since "2024-01-01 10:00:00"

# View logs until specific time
sudo journalctl -u ledmatrix --until "2024-01-01 12:00:00"

# Filter by priority (errors only)
sudo journalctl -u ledmatrix -p err

# Filter by priority (warnings and errors)
sudo journalctl -u ledmatrix -p warning

# Search logs for specific text
sudo journalctl -u ledmatrix | grep "error"
sudo journalctl -u ledmatrix | grep -i "plugin"

# View logs for multiple services
sudo journalctl -u ledmatrix -u ledmatrix-web -n 50

# Export logs to file
sudo journalctl -u ledmatrix > ledmatrix.log
```

### Network Testing Commands

```bash
# Test connectivity
ping -c 4 8.8.8.8
ping -c 4 api.openweathermap.org

# Test DNS resolution
nslookup api.openweathermap.org
dig api.openweathermap.org

# Test HTTP endpoint
curl -I http://your-pi-ip:5000
curl http://192.168.4.1:5000

# Check listening ports
sudo lsof -i :5000
sudo netstat -tuln | grep 5000

# Check network interfaces
ip addr show
nmcli device status
```

### File/Directory Verification

```bash
# Check file exists
ls -l config/config.json
ls -l plugin-repos/plugin-id/manifest.json

# Check directory structure
ls -la web_interface/
ls -la plugin-repos/

# Check file permissions
ls -l config/config_secrets.json

# Check file contents
cat config/config.json | jq .
cat config/wifi_config.json | grep auto_enable
```

### Python Import Testing

```bash
# Test core imports
python3 -c "from src.config_manager import ConfigManager; print('OK')"
python3 -c "from src.plugin_system.plugin_manager import PluginManager; print('OK')"
python3 -c "from src.display_manager import DisplayManager; print('OK')"

# Test web interface imports
python3 -c "from web_interface.app import app; print('OK')"
python3 -c "from web_interface.blueprints.api_v3 import api_v3; print('OK')"

# Test WiFi manager
python3 -c "from src.wifi_manager import WiFiManager; print('OK')"

# Test plugin import
python3 -c "
import sys
sys.path.insert(0, 'plugin-repos/plugin-id')
from manager import PluginClass
print('Plugin imports OK')
"
```

---

## Reinstalling Service Files

If a systemd service file is corrupted or missing, do NOT hand-write
one. The real unit files live in the repo's `systemd/` directory
(`ledmatrix.service`, `ledmatrix-web.service`,
`ledmatrix-wifi-monitor.service`) and contain a
`__PROJECT_ROOT_DIR__` placeholder that the install scripts substitute
with your actual checkout path:

```bash
# Reinstall the display service unit
sudo ./scripts/install/install_service.sh

# Reinstall the web interface service unit
sudo ./scripts/install/install_web_service.sh
```

Note that `ledmatrix-web.service` runs as root via
`scripts/utils/start_web_conditionally.py` — root is needed for
system operations (service control, WiFi management), and the wrapper
honors the `web_display_autostart` config flag before actually
starting the web server.

---

## Complete Diagnostic Script

Run this script for comprehensive diagnostics:

```bash
#!/bin/bash

echo "=== LEDMatrix Diagnostic Report ==="
echo ""

echo "1. Service Status:"
systemctl status ledmatrix --no-pager -n 5
systemctl status ledmatrix-web --no-pager -n 5
echo ""

echo "2. Recent Logs:"
journalctl -u ledmatrix -n 20 --no-pager
echo ""

echo "3. Configuration:"
cat config/config.json | grep -E "(web_display_autostart|enabled)"
echo ""

echo "4. Network Status:"
ip addr show | grep -E "(wlan|eth|inet )"
curl -s http://localhost:5000 > /dev/null && echo "Web interface: OK" || echo "Web interface: FAILED"
echo ""

echo "5. File Structure:"
ls -la web_interface/ | head -10
ls -la plugin-repos/ | head -10
echo ""

echo "6. Python Imports:"
python3 -c "from src.config_manager import ConfigManager" && echo "ConfigManager: OK" || echo "ConfigManager: FAILED"
python3 -c "from web_interface.app import app" && echo "Web app: OK" || echo "Web app: FAILED"
echo ""

echo "=== End Diagnostic Report ==="
```

---

## Success Indicators

A properly functioning system should show:

1. **Services Running:**
   ```
   ● ledmatrix.service - active (running)
   ● ledmatrix-web.service - active (running)
   ```

2. **Web Interface Accessible:**
   - Navigate to http://your-pi-ip:5000
   - Page loads successfully
   - Display preview visible

3. **Logs Show Normal Operation:**
   ```
   INFO: Web interface started on port 5000
   INFO: Loaded X plugins
   INFO: Display rotation active
   ```

4. **Process Listening on Port:**
   ```bash
   $ sudo lsof -i :5000
   COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
   python3  1234 ledpi  3u   IPv4  12345      0t0  TCP *:5000 (LISTEN)
   ```

5. **Plugins Loading:**
   - Logs show plugin initialization
   - Plugins appear in web interface
   - Display cycles through enabled plugins

---

## Emergency Recovery

If the system is completely broken:

### 1. Git Rollback

```bash
# View recent commits
git log --oneline -10

# Rollback to previous commit
git reset --hard HEAD~1

# Or rollback to specific commit
git reset --hard <commit-hash>

# Restart all services
sudo systemctl restart ledmatrix
sudo systemctl restart ledmatrix-web
```

### 2. Fresh Service Installation

```bash
# Reinstall WiFi monitor
sudo ./scripts/install/install_wifi_monitor.sh

# Recreate service files (substitutes __PROJECT_ROOT_DIR__ in systemd/ units)
sudo ./scripts/install/install_service.sh
sudo ./scripts/install/install_web_service.sh

# Restart
sudo systemctl restart ledmatrix ledmatrix-web
```

### 3. Full System Reboot

```bash
# As a last resort
sudo reboot
```

---

## Related Documentation

- [WEB_INTERFACE_GUIDE.md](WEB_INTERFACE_GUIDE.md) - Web interface usage
- [WIFI_NETWORK_SETUP.md](WIFI_NETWORK_SETUP.md) - WiFi configuration
- [PLUGIN_STORE_GUIDE.md](PLUGIN_STORE_GUIDE.md) - Plugin installation
- [REST_API_REFERENCE.md](REST_API_REFERENCE.md) - API documentation
