# Web Interface Troubleshooting - Quick Start

## The Problem
After reorganizing the web interface, it doesn't seem to run and shows no logging.

## Why You're Not Seeing Logs

**The web service logs to syslog, NOT stdout!** 

The systemd service is configured with:
```
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=ledmatrix-web
```

## Immediate Actions (Run on Raspberry Pi)

### 1. Run the Diagnostic Script
```bash
ssh ledpi@<your-pi-ip>
cd ~/LEDMatrix
bash scripts/diagnose_web_interface.sh
```

This automated script will check everything and tell you what's wrong.

### 2. View the Actual Logs
```bash
# View recent logs
sudo journalctl -u ledmatrix-web -n 50 --no-pager

# Follow logs in real-time
sudo journalctl -u ledmatrix-web -f
```

### 3. Check Service Status
```bash
sudo systemctl status ledmatrix-web
```

### 4. Try Manual Start (Best for Debugging)
```bash
cd ~/LEDMatrix
python3 web_interface/start.py
```

This will show errors directly in your terminal.

## Most Likely Issues

### Issue 1: web_display_autostart is False
The web interface is designed NOT to start if this config is false.

**Fix:**
```bash
nano ~/LEDMatrix/config/config.json
# Change: "web_display_autostart": true
sudo systemctl restart ledmatrix-web
```

### Issue 2: Service Not Started
**Fix:**
```bash
sudo systemctl start ledmatrix-web
sudo systemctl enable ledmatrix-web
```

### Issue 3: Import Errors
**Fix:**
```bash
cd ~/LEDMatrix
pip3 install --break-system-packages -r web_interface/requirements.txt
sudo systemctl restart ledmatrix-web
```

## Full Documentation

- **Comprehensive Guide:** `docs/WEB_INTERFACE_TROUBLESHOOTING.md`
- **Reorganization Info:** `WEB_INTERFACE_REORGANIZATION.md`

## After Fixing

Once it's working, you should see:
- Service status: "active (running)" in green
- Accessible at: `http://<your-pi-ip>:5000`
- Logs showing: "Starting LED Matrix Web Interface V3..."

## Need Help?

Run the diagnostic script and share its output - it will show exactly what's wrong!

