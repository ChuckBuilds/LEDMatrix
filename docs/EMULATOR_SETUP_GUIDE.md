# LEDMatrix Emulator Setup Guide

## Overview

The LEDMatrix emulator allows you to run and test LEDMatrix displays on your computer without requiring physical LED matrix hardware. This is perfect for development, testing, and demonstration purposes.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Running the Emulator](#running-the-emulator)
5. [Display Adapters](#display-adapters)
6. [Troubleshooting](#troubleshooting)
7. [Advanced Configuration](#advanced-configuration)

## Prerequisites

### System Requirements
- Python 3.7 or higher
- Windows, macOS, or Linux
- At least 2GB RAM (4GB recommended)
- Internet connection for plugin downloads

### Required Software
- Python 3.7+
- pip (Python package manager)
- Git (for plugin management)

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/LEDMatrix.git
cd LEDMatrix
```

### 2. Install Emulator Dependencies

Install the emulator-specific requirements:

```bash
pip install -r requirements-emulator.txt
```

This installs:
- `RGBMatrixEmulator` - The core emulation library
- Additional dependencies for display adapters

### 3. Install Standard Dependencies

```bash
pip install -r requirements.txt
```

## Configuration

### 1. Emulator Configuration File

The emulator uses `emulator_config.json` for configuration. Here's the default configuration:

```json
{
    "pixel_outline": 0,
    "pixel_size": 16,
    "pixel_style": "square",
    "pixel_glow": 6,
    "display_adapter": "pygame",
    "icon_path": null,
    "emulator_title": null,
    "suppress_font_warnings": false,
    "suppress_adapter_load_errors": false,
    "browser": {
        "_comment": "For use with the browser adapter only.",
        "port": 8888,
        "target_fps": 24,
        "fps_display": false,
        "quality": 70,
        "image_border": true,
        "debug_text": false,
        "image_format": "JPEG"
    },
    "log_level": "info"
}
```

### 2. Configuration Options

| Option | Description | Default | Values |
|--------|-------------|---------|--------|
| `pixel_outline` | Pixel border thickness | 0 | 0-5 |
| `pixel_size` | Size of each pixel | 16 | 8-64 |
| `pixel_style` | Pixel shape | "square" | "square", "circle" |
| `pixel_glow` | Glow effect intensity | 6 | 0-20 |
| `display_adapter` | Display backend | "pygame" | "pygame", "browser" |
| `emulator_title` | Window title | null | Any string |
| `suppress_font_warnings` | Hide font warnings | false | true/false |
| `suppress_adapter_load_errors` | Hide adapter errors | false | true/false |

### 3. Browser Adapter Configuration

When using the browser adapter, additional options are available:

| Option | Description | Default |
|--------|-------------|---------|
| `port` | Web server port | 8888 |
| `target_fps` | Target frames per second | 24 |
| `fps_display` | Show FPS counter | false |
| `quality` | Image compression quality | 70 |
| `image_border` | Show image border | true |
| `debug_text` | Show debug information | false |
| `image_format` | Image format | "JPEG" |

## Running the Emulator

### 1. Set Environment Variable

Enable emulator mode by setting the `EMULATOR` environment variable:

**Windows (Command Prompt):**
```cmd
set EMULATOR=true
python run.py
```

**Windows (PowerShell):**
```powershell
$env:EMULATOR="true"
python run.py
```

**Linux/macOS:**
```bash
export EMULATOR=true
python3 run.py
```

### 2. Alternative: Direct Python Execution

You can also run the emulator directly:

```bash
EMULATOR=true python3 run.py
```

### 3. Verify Emulator Mode

When running in emulator mode, you should see:
- A window displaying the LED matrix simulation
- Console output indicating emulator mode
- No hardware initialization errors

## Display Adapters

LEDMatrix supports two display adapters for the emulator:

### 1. Pygame Adapter (Default)

The pygame adapter provides a native desktop window with real-time display.

**Features:**
- Real-time rendering
- Keyboard controls
- Window resizing
- High performance

**Configuration:**
```json
{
    "display_adapter": "pygame",
    "pixel_size": 16,
    "pixel_style": "square"
}
```

**Keyboard Controls:**
- `ESC` - Exit emulator
- `F11` - Toggle fullscreen
- `+/-` - Zoom in/out
- `R` - Reset zoom

### 2. Browser Adapter

The browser adapter runs a web server and displays the matrix in a web browser.

**Features:**
- Web-based interface
- Remote access capability
- Mobile-friendly
- Screenshot capture

**Configuration:**
```json
{
    "display_adapter": "browser",
    "browser": {
        "port": 8888,
        "target_fps": 24,
        "quality": 70
    }
}
```

**Usage:**
1. Start the emulator with browser adapter
2. Open browser to `http://localhost:8888`
3. View the LED matrix display

## Troubleshooting

### Common Issues

#### 1. "ModuleNotFoundError: No module named 'RGBMatrixEmulator'"

**Solution:**
```bash
pip install RGBMatrixEmulator
```

#### 2. Pygame Window Not Opening

**Possible Causes:**
- Missing pygame installation
- Display server issues (Linux)
- Graphics driver problems

**Solutions:**
```bash
# Install pygame
pip install pygame

# For Linux, ensure X11 is running
echo $DISPLAY

# For WSL, install X server
# Windows: Install VcXsrv or Xming
```

#### 3. Browser Adapter Not Working

**Check:**
- Port 8888 is available
- Firewall allows connections
- Browser can access localhost

**Solutions:**
```bash
# Check if port is in use
netstat -an | grep 8888

# Try different port in config
"port": 8889
```

#### 4. Performance Issues

**Optimizations:**
- Reduce `pixel_size` in config
- Lower `target_fps` for browser adapter
- Close other applications
- Use pygame adapter for better performance

### Debug Mode

Enable debug logging:

```json
{
    "log_level": "debug",
    "suppress_font_warnings": false,
    "suppress_adapter_load_errors": false
}
```

## Advanced Configuration

### 1. Custom Display Dimensions

Modify the display dimensions in your main config:

```json
{
    "display": {
        "hardware": {
            "rows": 32,
            "cols": 64,
            "chain_length": 2
        }
    }
}
```

### 2. Plugin Development

For plugin development with the emulator:

```bash
# Enable emulator mode
export EMULATOR=true

# Run with specific plugin
python run.py --plugin my-plugin

# Debug mode
python run.py --debug
```

### 3. Performance Tuning

**For High-Resolution Displays:**
```json
{
    "pixel_size": 8,
    "pixel_glow": 2,
    "browser": {
        "target_fps": 15,
        "quality": 50
    }
}
```

**For Low-End Systems:**
```json
{
    "pixel_size": 12,
    "pixel_glow": 0,
    "browser": {
        "target_fps": 10,
        "quality": 30
    }
}
```

### 4. Integration with Web Interface

The emulator can work alongside the web interface:

```bash
# Terminal 1: Start emulator
export EMULATOR=true
python run.py

# Terminal 2: Start web interface
python web_interface/app.py
```

Access the web interface at `http://localhost:5000` while the emulator runs.

## Best Practices

### 1. Development Workflow

1. **Start with emulator** for initial development
2. **Test plugins** using emulator mode
3. **Validate configuration** before hardware deployment
4. **Use browser adapter** for remote testing

### 2. Plugin Testing

```bash
# Test specific plugin
export EMULATOR=true
python run.py --plugin clock-simple

# Test all plugins
export EMULATOR=true
python run.py --test-plugins
```

### 3. Configuration Management

- Keep `emulator_config.json` in version control
- Use different configs for different environments
- Document custom configurations

## Examples

### Basic Clock Display

```bash
# Start emulator with clock
export EMULATOR=true
python run.py
```

### Sports Scores

```bash
# Configure for sports display
# Edit config/config.json to enable sports plugins
export EMULATOR=true
python run.py
```

### Custom Text Display

```bash
# Use text display plugin
export EMULATOR=true
python run.py --plugin text-display --text "Hello World"
```

## Support

For additional help:

1. **Check the logs** - Enable debug mode for detailed output
2. **Review configuration** - Ensure all settings are correct
3. **Test with minimal config** - Start with default settings
4. **Community support** - Check GitHub issues and discussions

## Conclusion

The LEDMatrix emulator provides a powerful way to develop, test, and demonstrate LED matrix displays without physical hardware. With support for multiple display adapters and comprehensive configuration options, it's an essential tool for LEDMatrix development and deployment.

For more information, see the main [README.md](../README.md) and other documentation in the `docs/` directory.
