# Plugin Dependency Installation Guide

## Overview

The LEDMatrix system has smart dependency installation that adapts based on who is running it. This guide explains how it works and potential pitfalls.

## How It Works

### Execution Context Detection

The plugin manager checks if it's running as root:
```python
running_as_root = os.geteuid() == 0
```

Based on this, it chooses the appropriate installation method:

| Running As | Installation Method | Location | Accessible To |
|------------|-------------------|----------|---------------|
| **root** (systemd service) | System-wide (`--break-system-packages`) | `/usr/local/lib/python3.X/dist-packages/` | All users |
| **ledpi** or other user | User-specific (`--user`) | `~/.local/lib/python3.X/site-packages/` | Only that user |

## Common Scenarios

### ✅ Scenario 1: Normal Production Use (Recommended)

**What:** Services running via systemd

```bash
sudo systemctl start ledmatrix
sudo systemctl start ledmatrix-web
```

- **Runs as:** root (configured in .service files)
- **Installs to:** System-wide
- **Result:** ✅ Works perfectly, all dependencies accessible

### ✅ Scenario 2: Web Interface Plugin Installation

**What:** Installing/enabling plugins via web interface at `http://pi-ip:5001`

- **Web service runs as:** root (ledmatrix-web.service)
- **Installs to:** System-wide
- **Result:** ✅ Works perfectly, systemd service can access them

### ✅ Scenario 3: Manual Testing as ledpi (Read-only)

**What:** Running display manually as ledpi to test/debug

```bash
# As ledpi user
cd /home/ledpi/LEDMatrix
python3 run.py
```

- **Runs as:** ledpi
- **Can import:** ✅ System-wide packages (installed by root)
- **Result:** ✅ Works! Can use existing plugins with root-installed dependencies

### ⚠️ Scenario 4: Manual Plugin Installation as ledpi (Problematic)

**What:** Enabling a NEW plugin and running manually as ledpi

```bash
# As ledpi user
cd /home/ledpi/LEDMatrix
# Edit config to enable new plugin
nano config/config.json
# Run display - will try to install new plugin dependencies
python3 run.py
```

**What Happens:**
1. Plugin manager runs as `ledpi`
2. Installs dependencies with `--user` flag
3. Dependencies go to `~/.local/lib/python3.X/site-packages/`
4. ⚠️ **Warning logged:** "Installing plugin dependencies for current user (not root)"

**Problem:**
- When systemd service restarts (as root), it **can't see** `~/.local/` packages
- Plugin will fail to load for the systemd service

**Solution:**
After testing, restart the service to install dependencies system-wide:
```bash
sudo systemctl restart ledmatrix
```

## Best Practices

### For Production/Normal Use

1. **Always use the web interface** to install/enable plugins
2. **Or restart the systemd service** after config changes:
   ```bash
   sudo systemctl restart ledmatrix
   ```

### For Development/Testing

1. **Read existing plugins:** Safe to run as `ledpi` - can import system packages
2. **Test new plugins:** Use sudo or restart service to install dependencies:
   ```bash
   # Option 1: Run as root
   sudo python3 run.py
   
   # Option 2: Install deps manually
   sudo pip3 install --break-system-packages -r plugins/my-plugin/requirements.txt
   python3 run.py
   
   # Option 3: Let service install them
   sudo systemctl restart ledmatrix
   ```

## Warning Messages

### If you see this warning:
```
Installing plugin dependencies for current user (not root).
These will NOT be accessible to the systemd service.
For production use, install plugins via the web interface or restart the ledmatrix service.
```

**What it means:**
- You're running as a non-root user
- Dependencies were installed to your user directory only
- The systemd service won't be able to use this plugin

**What to do:**
```bash
# Restart the service to install dependencies system-wide
sudo systemctl restart ledmatrix
```

## Troubleshooting

### Plugin works when I run manually but fails in systemd service

**Cause:** Dependencies installed to user directory (`~/.local/`) instead of system-wide

**Fix:**
```bash
# Check where package is installed
pip3 list -v | grep <package-name>

# If it shows ~/.local/, reinstall system-wide:
sudo pip3 install --break-system-packages <package-name>

# Or just restart the service:
sudo systemctl restart ledmatrix
```

### Permission denied when installing dependencies

**If you see errors like:**
```
ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: '/root/.local'
WARNING: The directory '/root/.cache/pip' or its parent directory is not owned or is not writable
```

**Quick Fix - Use the Helper Script:**
```bash
sudo /home/ledpi/LEDMatrix/scripts/install_plugin_dependencies.sh
sudo systemctl restart ledmatrix
```

**Manual Fix:**
```bash
# Install dependencies with --no-cache-dir to avoid cache permission issues
cd /home/ledpi/LEDMatrix/plugins/PLUGIN-NAME
sudo pip3 install --break-system-packages --no-cache-dir -r requirements.txt
sudo systemctl restart ledmatrix
```

**For more detailed troubleshooting, see:** [Plugin Dependency Troubleshooting Guide](PLUGIN_DEPENDENCY_TROUBLESHOOTING.md)

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────┐
│                    LEDMatrix Services                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ledmatrix.service (User=root)                              │
│  ledmatrix-web.service (User=root)                          │
│  ├── Install dependencies system-wide                        │
│  └── Accessible to all users                                 │
│                                                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Manual execution as ledpi                                   │
│  ├── Can READ system-wide packages ✅                        │
│  ├── WRITES go to ~/.local/ ⚠️                               │
│  └── Not accessible to root service                          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Recommendations

1. **For end users:** Always use the web interface for plugin management
2. **For developers:** Be aware of the user context when testing
3. **For plugin authors:** Test with `sudo systemctl restart ledmatrix` to ensure dependencies install correctly
4. **For CI/CD:** Always run installation as root or use the service

## Helper Scripts

### Install Plugin Dependencies Script

Located at: `scripts/install_plugin_dependencies.sh`

This script automatically finds and installs dependencies for all plugins:

```bash
# Run as root (recommended for production)
sudo /home/ledpi/LEDMatrix/scripts/install_plugin_dependencies.sh

# Make executable if needed
chmod +x /home/ledpi/LEDMatrix/scripts/install_plugin_dependencies.sh
```

Features:
- Auto-detects all plugins with requirements.txt
- Uses correct installation method (system-wide vs user)
- Bypasses pip cache to avoid permission issues
- Provides detailed logging and error messages

## Files to Reference

- Service configs: `ledmatrix.service`, `ledmatrix-web.service`
- Plugin manager: `src/plugin_system/plugin_manager.py`
- Installation script: `first_time_install.sh`
- Dependency installer: `scripts/install_plugin_dependencies.sh`
- Troubleshooting guide: `PLUGIN_DEPENDENCY_TROUBLESHOOTING.md`

