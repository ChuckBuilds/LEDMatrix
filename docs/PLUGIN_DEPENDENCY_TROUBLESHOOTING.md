# Plugin Dependency Installation Troubleshooting

This guide helps resolve issues with automatic plugin dependency installation in the LEDMatrix system.

## Common Error Symptoms

### Permission Errors
```
ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: '/root/.local'
WARNING: The directory '/root/.cache/pip' or its parent directory is not owned or is not writable
```

### Context Mismatch
```
WARNING: Installing plugin dependencies for current user (not root). 
These will NOT be accessible to the systemd service.
```

## Root Cause

Plugin dependencies must be installed in a context accessible to the LEDMatrix systemd service, which runs as root. Permission errors typically occur when:

1. The pip cache directory has incorrect permissions
2. The process tries to install to user directories without proper permissions
3. Environment variables (like HOME) are not set correctly for the service context

## Solutions

### Solution 1: Use the Manual Installation Script (Recommended)

We provide a helper script that handles dependency installation correctly:

```bash
# Run as root to install system-wide (for production)
sudo /home/ledpi/LEDMatrix/scripts/install_plugin_dependencies.sh

# After installation, restart the service
sudo systemctl restart ledmatrix
```

This script:
- Detects all plugins with requirements.txt files
- Installs dependencies with correct permissions
- Uses `--no-cache-dir` to avoid cache permission issues
- Provides detailed logging for troubleshooting

### Solution 2: Manual Installation per Plugin

If you need to install dependencies for a specific plugin:

```bash
# Navigate to the plugin directory
cd /home/ledpi/LEDMatrix/plugins/PLUGIN-NAME

# Install as root (system-wide)
sudo pip3 install --break-system-packages --no-cache-dir -r requirements.txt

# Restart the service
sudo systemctl restart ledmatrix
```

### Solution 3: Fix Cache Directory Permissions

If you specifically have cache permission issues:

```bash
# Option A: Skip the cache (recommended)
sudo pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Option B: Fix cache permissions (if needed)
sudo mkdir -p /root/.cache/pip
sudo chown -R root:root /root/.cache
sudo chmod -R 755 /root/.cache
```

### Solution 4: Install via Web Interface

The web interface handles dependency installation correctly in the service context:

1. Access the web interface (usually http://ledpi:8080)
2. Navigate to Plugin Store or Plugin Management
3. Install plugins through the web UI
4. The system will automatically handle dependencies

## Prevention

### For Plugin Developers

When creating plugins with dependencies:

1. **Keep requirements minimal**: Only include essential packages
2. **Test installation**: Verify your requirements.txt works with:
   ```bash
   sudo pip3 install --break-system-packages --no-cache-dir -r requirements.txt
   ```
3. **Document dependencies**: Note any system packages needed (via apt)

### For Users

1. **Use web interface**: Install plugins via the web UI when possible
2. **Install as root**: When using SSH/terminal, use sudo for plugin installations
3. **Restart service**: After manual installations, restart the ledmatrix service

## Technical Details

### How Dependency Installation Works

The `PluginManager._install_plugin_dependencies()` method:

1. Detects if running as root using `os.geteuid() == 0`
2. If root: Uses system-wide installation with `--break-system-packages --no-cache-dir`
3. If not root: Uses user installation with `--user --break-system-packages --no-cache-dir`
4. The `--no-cache-dir` flag prevents cache-related permission issues

### Why `--break-system-packages`?

Debian 12+ (Bookworm) and Raspberry Pi OS based on it implement PEP 668, which prevents pip from installing packages system-wide by default. The `--break-system-packages` flag overrides this protection, which is necessary for the plugin system.

### Service Context

The ledmatrix.service runs as:
- **User**: root
- **WorkingDirectory**: /home/ledpi/LEDMatrix
- **Python**: /usr/bin/python3

Dependencies must be installed in root's Python environment or system-wide to be accessible.

## Checking Installation

Verify dependencies are installed correctly:

```bash
# Check as root (how the service sees it)
sudo python3 -c "import package_name"

# List installed packages
pip3 list

# Check specific package
pip3 show package_name
```

## Getting Help

If you continue to experience issues:

1. Check the service logs:
   ```bash
   sudo journalctl -u ledmatrix -f
   ```

2. Check pip logs (created by manual script):
   ```bash
   cat /tmp/pip_install_*.log
   ```

3. Verify plugin manifest is correct:
   ```bash
   cat /home/ledpi/LEDMatrix/plugins/PLUGIN-NAME/manifest.json
   ```

4. Check plugin requirements:
   ```bash
   cat /home/ledpi/LEDMatrix/plugins/PLUGIN-NAME/requirements.txt
   ```

## Related Documentation

- [Plugin Dependency Guide](PLUGIN_DEPENDENCY_GUIDE.md)
- [Plugin Development Guide](docs/plugin_development.md)
- [Troubleshooting Quick Start](TROUBLESHOOTING_QUICK_START.md)

