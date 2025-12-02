# Quick Installation Verification Guide

## Immediate Steps to Verify Installation

### 1. Check Service Status

```bash
# Check all LED Matrix services
sudo systemctl status ledmatrix.service
sudo systemctl status ledmatrix-web.service
sudo systemctl status ledmatrix-wifi-monitor.service

# Or check all at once
systemctl is-active ledmatrix.service ledmatrix-web.service ledmatrix-wifi-monitor.service
```

### 2. Run Verification Script

```bash
cd ~/LEDMatrix
./scripts/verify_installation.sh
```

### 3. Check Web Interface

```bash
# Find your Pi's IP address
hostname -I

# Check if web interface is listening
sudo netstat -tuln | grep 5001
# OR
sudo ss -tuln | grep 5001

# Test web interface (replace with your Pi's IP)
curl -I http://localhost:5001
```

### 4. Check Network Status

```bash
# Check WiFi connection
nmcli device status

# Check if AP mode is active (this would explain SSH loss)
sudo systemctl status hostapd
sudo systemctl status dnsmasq

# Check WiFi monitor logs
sudo journalctl -u ledmatrix-wifi-monitor -n 50
```

### 5. Check Python Dependencies

```bash
# Test rgbmatrix import
python3 -c "from rgbmatrix import RGBMatrix, RGBMatrixOptions; print('✓ rgbmatrix OK')"

# Check key packages
python3 -c "import flask, requests, PIL, numpy; print('✓ All packages OK')"
```

### 6. Check Configuration Files

```bash
# Verify config files exist
ls -la ~/LEDMatrix/config/config.json
ls -la ~/LEDMatrix/config/config_secrets.json

# Check file permissions
ls -la ~/LEDMatrix/assets
ls -la /var/cache/ledmatrix
```

## If SSH is Unavailable

### Connect via AP Mode

1. Look for WiFi network: **LEDMatrix-Setup**
2. Password: `ledmatrix123`
3. SSH to: `ssh devpi@192.168.4.1`
4. Then reconnect to your WiFi:
   ```bash
   sudo nmcli device wifi connect "YourWiFiSSID" password "YourPassword"
   ```

### Disable AP Mode

```bash
sudo systemctl stop hostapd dnsmasq
sudo systemctl stop ledmatrix-wifi-monitor
sudo nmcli device wifi connect "YourWiFiSSID" password "YourPassword"
```

## Common Issues

### Services Not Running

```bash
# Start services manually
sudo systemctl start ledmatrix.service
sudo systemctl start ledmatrix-web.service

# Enable auto-start on boot
sudo systemctl enable ledmatrix.service
sudo systemctl enable ledmatrix-web.service
```

### Web Interface Not Accessible

```bash
# Check if service is running
sudo systemctl status ledmatrix-web.service

# Check logs for errors
sudo journalctl -u ledmatrix-web.service -n 50

# Restart service
sudo systemctl restart ledmatrix-web.service
```

### Permission Issues

```bash
# Log out and back in to refresh group memberships
# OR run:
newgrp systemd-journal

# Fix cache permissions
sudo ~/LEDMatrix/scripts/fix_perms/fix_cache_permissions.sh
```

## Full Verification Checklist

- [ ] All three services are installed and running
- [ ] Python dependencies are installed (rgbmatrix, flask, etc.)
- [ ] Configuration files exist (config.json, config_secrets.json)
- [ ] Web interface is accessible on port 5001
- [ ] WiFi is connected (or AP mode is active if intentional)
- [ ] File permissions are correct (assets, cache directories)
- [ ] No errors in service logs

## Next Steps After Verification

1. **Access Web Interface**: `http://<pi-ip>:5001`
2. **Configure Settings**: Edit `config/config.json` as needed
3. **Add API Keys**: Update `config/config_secrets.json` with your API keys
4. **Test Display**: Start the LED Matrix service and verify display works
5. **Enable Plugins**: Configure and enable desired plugins

