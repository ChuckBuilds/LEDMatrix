# Installation Scripts

This directory contains scripts for installing and configuring the LEDMatrix system.

## Scripts

- **`install_service.sh`** - Installs the main LED Matrix display service (systemd)
- **`install_web_service.sh`** - Installs the web interface service (systemd)
- **`install_wifi_monitor.sh`** - Installs the WiFi monitor daemon service
- **`setup_cache.sh`** - Sets up persistent cache directory with proper permissions
- **`configure_web_sudo.sh`** - Configures passwordless sudo access for web interface actions
- **`migrate_config.sh`** - Migrates configuration files to new formats (if needed)

## Usage

These scripts are typically called by `first_time_install.sh` in the project root, but can also be run individually if needed.

**Note:** Most installation scripts require `sudo` privileges to install systemd services and configure system settings.

