# Web Interface Troubleshooting Guide

## Quick Diagnosis Steps

Since the web interface doesn't seem to run and shows no logging after reorganization, follow these steps **on your Raspberry Pi** to diagnose the issue:

### 1. Check Service Status

```bash
# Check if the web service is running
sudo systemctl status ledmatrix-web

# Check if it's enabled to start on boot
sudo systemctl is-enabled ledmatrix-web
```

### 2. View Service Logs

The service logs to **syslog**, not stdout. Use these commands to view logs:

```bash
# View recent web interface logs
sudo journalctl -u ledmatrix-web -n 50 --no-pager

# Follow logs in real-time
sudo journalctl -u ledmatrix-web -f

# View logs since last boot
sudo journalctl -u ledmatrix-web -b
```

### 3. Check Configuration

```bash
# Check if web_display_autostart is enabled in config
cat ~/LEDMatrix/config/config.json | grep web_display_autostart

# Should show: "web_display_autostart": true
```

If it shows `false` or is missing, the web interface won't start (by design).

### 4. Test Manual Startup

Try starting the web interface manually to see error messages:

```bash
cd ~/LEDMatrix
python3 web_interface/start.py
```

This will show any import errors or startup issues directly in the terminal.

## Common Issues and Solutions

### Issue 1: Service Not Running

**Symptom:** `systemctl status ledmatrix-web` shows "inactive (dead)"

**Solutions:**
```bash
# Start the service
sudo systemctl start ledmatrix-web

# Enable it to start on boot
sudo systemctl enable ledmatrix-web

# Check status again
sudo systemctl status ledmatrix-web
```

### Issue 2: web_display_autostart is False

**Symptom:** Service starts but immediately exits gracefully

**Solution:**
```bash
# Edit config.json
nano ~/LEDMatrix/config/config.json

# Set web_display_autostart to true:
"web_display_autostart": true

# Restart the service
sudo systemctl restart ledmatrix-web
```

### Issue 3: Import Errors

**Symptom:** Service fails immediately with import errors in logs

**Possible causes:**
- Missing dependencies
- Python path issues
- Circular import problems

**Solutions:**

```bash
# Install/reinstall web dependencies
cd ~/LEDMatrix
pip3 install --break-system-packages -r web_interface/requirements.txt

# Check for Python errors
python3 -c "from web_interface.app import app; print('OK')"
```

### Issue 4: Port Already in Use

**Symptom:** Error message about port 5000 being in use

**Solution:**
```bash
# Check what's using port 5000
sudo lsof -i :5000

# Kill the process if needed
sudo kill -9 <PID>

# Or change the port in web_interface/start.py
```

### Issue 5: Permission Issues

**Symptom:** Permission denied errors in logs

**Solution:**
```bash
# Ensure proper ownership
cd ~/LEDMatrix
sudo chown -R ledpi:ledpi .

# Restart service
sudo systemctl restart ledmatrix-web
```

### Issue 6: Flask/Blueprint Import Errors

**Symptom:** ImportError or ModuleNotFoundError in logs

**Check these files exist:**
```bash
ls -la ~/LEDMatrix/web_interface/app.py
ls -la ~/LEDMatrix/web_interface/start.py
ls -la ~/LEDMatrix/web_interface/blueprints/api_v3.py
ls -la ~/LEDMatrix/web_interface/blueprints/pages_v3.py
```

If any are missing, you may need to restore from git or the reorganization.

## Detailed Logging Commands

### View All Web Service Logs
```bash
# Show all logs with timestamps
sudo journalctl -u ledmatrix-web --no-pager

# Show logs from the last hour
sudo journalctl -u ledmatrix-web --since "1 hour ago"

# Show logs between specific times
sudo journalctl -u ledmatrix-web --since "2024-10-14 10:00:00" --until "2024-10-14 11:00:00"

# Show only errors
sudo journalctl -u ledmatrix-web -p err
```

### Check Python Import Issues
```bash
cd ~/LEDMatrix

# Test imports step by step
python3 -c "import sys; sys.path.insert(0, '.'); from src.config_manager import ConfigManager; print('ConfigManager OK')"

python3 -c "import sys; sys.path.insert(0, '.'); from src.plugin_system.plugin_manager import PluginManager; print('PluginManager OK')"

python3 -c "import sys; sys.path.insert(0, '.'); from web_interface.app import app; print('Flask App OK')"
```

## Service File Check

Verify the service file is correct:

```bash
cat /etc/systemd/system/ledmatrix-web.service
```

Should contain:
```
[Unit]
Description=LED Matrix Web Interface Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/ledpi/LEDMatrix
Environment=USE_THREADING=1
ExecStart=/usr/bin/python3 /home/ledpi/LEDMatrix/start_web_conditionally.py
Restart=on-failure
RestartSec=10
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=ledmatrix-web

[Install]
WantedBy=multi-user.target
```

If it's different or points to old paths, reinstall it:

```bash
cd ~/LEDMatrix
sudo bash install_web_service.sh
sudo systemctl daemon-reload
sudo systemctl restart ledmatrix-web
```

## Post-Reorganization Checklist

Verify the reorganization completed correctly:

```bash
cd ~/LEDMatrix

# These files should exist in new locations:
ls web_interface/app.py
ls web_interface/start.py
ls web_interface/requirements.txt
ls web_interface/blueprints/api_v3.py
ls web_interface/blueprints/pages_v3.py

# start_web_conditionally.py should point to new location
grep "web_interface/start.py" start_web_conditionally.py
```

## Emergency Recovery

If nothing works, you can rollback to the old structure:

```bash
cd ~/LEDMatrix

# Check git status
git status

# If changes aren't committed, revert
git checkout .

# Or restore specific files from old_web_interface
# (if that directory exists)
```

## Recommended Diagnostic Sequence

Run these commands in order to get a complete picture:

```bash
#!/bin/bash
echo "=== Web Interface Diagnostic Report ==="
echo ""
echo "1. Service Status:"
sudo systemctl status ledmatrix-web
echo ""
echo "2. Config Autostart Setting:"
cat ~/LEDMatrix/config/config.json | grep web_display_autostart
echo ""
echo "3. Recent Logs (last 20 lines):"
sudo journalctl -u ledmatrix-web -n 20 --no-pager
echo ""
echo "4. File Structure Check:"
ls -la ~/LEDMatrix/web_interface/
echo ""
echo "5. Python Import Test:"
cd ~/LEDMatrix
python3 -c "from web_interface.app import app; print('✓ Flask app imports successfully')" 2>&1
echo ""
echo "=== End of Diagnostic Report ==="
```

Save this as `diagnose_web.sh`, make it executable, and run it:

```bash
chmod +x diagnose_web.sh
./diagnose_web.sh
```

## Success Indicators

When the web interface is running correctly, you should see:

1. **Service Status:** "active (running)" in green
2. **Logs:** "Starting LED Matrix Web Interface V3..." 
3. **Network:** Accessible at `http://<pi-ip>:5000`
4. **Process:** Python process listening on port 5000

```bash
# Check if it's listening
sudo netstat -tlnp | grep :5000
# or
sudo ss -tlnp | grep :5000
```

## Contact/Help

If you've tried all these steps and it still doesn't work, collect the following information:

1. Output from the diagnostic script above
2. Full service logs: `sudo journalctl -u ledmatrix-web -n 100 --no-pager`
3. Output from manual startup attempt
4. Git status and recent commits

This will help identify the exact issue.

