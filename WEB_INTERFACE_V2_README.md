# LED Matrix Web Interface V2

A modern, lightweight, and feature-rich web interface for controlling and customizing your LED Matrix display. This interface provides real-time display monitoring, drag-and-drop layout editing, and comprehensive system management.

## Features

### 🖥️ Real-Time Display Preview
- Live display monitoring with WebSocket connectivity
- Scaled-up preview for better visibility
- Real-time updates as content changes
- Screenshot capture functionality

### ✏️ Display Editor Mode
- **Drag-and-drop interface** for creating custom layouts
- **Element palette** with text, weather icons, shapes, and more
- **Properties panel** for fine-tuning element appearance
- **Real-time preview** of changes
- **Save/load custom layouts** for reuse

### 📊 System Monitoring
- **Real-time system stats** (CPU temperature, memory usage, uptime)
- **Service status monitoring** 
- **Performance metrics** with visual indicators
- **Connection status** indicator

### ⚙️ Configuration Management
- **Modern tabbed interface** for easy navigation
- **Real-time configuration updates**
- **Visual controls** (sliders, toggles, dropdowns)
- **Instant feedback** on changes

### 🎨 Modern UI Design
- **Responsive design** that works on desktop and mobile
- **Dark/light theme support**
- **Smooth animations** and transitions
- **Professional card-based layout**
- **Color-coded status indicators**

## Installation

### Prerequisites
- Python 3.7+
- LED Matrix hardware properly configured
- Existing LED Matrix project setup

### Quick Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements_web_v2.txt
   ```

2. **Make the startup script executable:**
   ```bash
   chmod +x start_web_v2.py
   ```

3. **Start the web interface:**
   ```bash
   python3 start_web_v2.py
   ```

4. **Access the interface:**
   Open your browser and navigate to `http://your-pi-ip:5001`

### Advanced Setup

For production use, you can set up the web interface as a systemd service:

1. **Create a service file:**
   ```bash
   sudo nano /etc/systemd/system/ledmatrix-web.service
   ```

2. **Add the following content:**
   ```ini
   [Unit]
   Description=LED Matrix Web Interface V2
   After=network.target
   
   [Service]
   Type=simple
   User=pi
   WorkingDirectory=/home/pi/LEDMatrix
   ExecStart=/usr/bin/python3 /home/pi/LEDMatrix/start_web_v2.py
   Restart=always
   RestartSec=10
   
   [Install]
   WantedBy=multi-user.target
   ```

3. **Enable and start the service:**
   ```bash
   sudo systemctl enable ledmatrix-web
   sudo systemctl start ledmatrix-web
   ```

## Usage Guide

### Getting Started

1. **Connect to your display:**
   - The interface will automatically attempt to connect to your LED matrix
   - Check the connection status indicator in the bottom-right corner

2. **Monitor your system:**
   - View real-time system stats in the header
   - Check service status and performance metrics in the Overview tab

3. **Control your display:**
   - Use the Start/Stop buttons to control display operation
   - Take screenshots for documentation or troubleshooting

### Using the Display Editor

1. **Enter Editor Mode:**
   - Click the "Enter Editor" button to pause normal display operation
   - The display will switch to editor mode, allowing you to customize layouts

2. **Add Elements:**
   - Drag elements from the palette onto the display preview
   - Elements will appear where you drop them
   - Click on elements to select and edit their properties

3. **Customize Elements:**
   - Use the Properties panel to adjust position, color, text, and other settings
   - Changes are reflected in real-time on the display

4. **Save Your Layout:**
   - Click "Save Layout" to store your custom design
   - Layouts are saved locally and can be reloaded later

### Element Types

#### Text Elements
- **Static text:** Display fixed text with custom positioning and colors
- **Data-driven text:** Display dynamic data using template variables
- **Clock elements:** Show current time with customizable formats

#### Visual Elements
- **Weather icons:** Display weather conditions with various icon styles
- **Rectangles:** Create borders, backgrounds, or decorative elements
- **Lines:** Add separators or decorative lines

#### Advanced Elements
- **Data text:** Connect to live data sources (weather, stocks, etc.)
- **Template text:** Use variables like `{weather.temperature}` in text

### Configuration Management

#### Display Settings
- **Brightness:** Adjust LED brightness (1-100%)
- **Schedule:** Set automatic on/off times
- **Hardware settings:** Configure matrix dimensions and timing

#### System Management
- **Service control:** Start, stop, or restart the LED matrix service
- **System updates:** Pull latest code from git repository
- **Log viewing:** Access system logs for troubleshooting
- **System reboot:** Safely restart the system

## API Reference

The web interface provides a REST API for programmatic control:

### Display Control
- `POST /api/display/start` - Start the display
- `POST /api/display/stop` - Stop the display
- `GET /api/display/current` - Get current display image

### Editor Mode
- `POST /api/editor/toggle` - Toggle editor mode
- `POST /api/editor/preview` - Update preview with layout

### Configuration
- `POST /api/config/save` - Save configuration changes
- `GET /api/system/status` - Get system status

### System Actions
- `POST /api/system/action` - Execute system actions

## Customization

### Creating Custom Layouts

Layouts are stored as JSON files with the following structure:

```json
{
  "layout_name": {
    "elements": [
      {
        "type": "text",
        "x": 10,
        "y": 10,
        "properties": {
          "text": "Hello World",
          "color": [255, 255, 255],
          "font_size": "normal"
        }
      }
    ],
    "description": "Layout description",
    "created": "2024-01-01T00:00:00",
    "modified": "2024-01-01T00:00:00"
  }
}
```

### Adding Custom Element Types

You can extend the layout manager to support custom element types:

1. **Add the element type to the palette** in `templates/index_v2.html`
2. **Implement the rendering logic** in `src/layout_manager.py`
3. **Update the properties panel** to support element-specific settings

### Theming

The interface uses CSS custom properties for easy theming. Modify the `:root` section in the HTML template to change colors:

```css
:root {
    --primary-color: #2c3e50;
    --secondary-color: #3498db;
    --accent-color: #e74c3c;
    /* ... more color variables */
}
```

## Troubleshooting

### Common Issues

1. **Connection Failed:**
   - Check that the LED matrix hardware is properly connected
   - Verify that the display service is running
   - Check firewall settings on port 5001

2. **Editor Mode Not Working:**
   - Ensure you have proper permissions to control the display
   - Check that the display manager is properly initialized
   - Review logs for error messages

3. **Performance Issues:**
   - Monitor system resources in the Overview tab
   - Reduce display update frequency if needed
   - Check for memory leaks in long-running sessions

### Getting Help

1. **Check the logs:**
   - Use the "View Logs" button in the System tab
   - Check `/tmp/web_interface_v2.log` for detailed error messages

2. **System status:**
   - Monitor the system stats for resource usage
   - Check service status indicators

3. **Debug mode:**
   - Set `debug=True` in `web_interface_v2.py` for detailed error messages
   - Use browser developer tools to check for JavaScript errors

## Performance Considerations

### Raspberry Pi Optimization

The interface is designed to be lightweight and efficient for Raspberry Pi:

- **Minimal resource usage:** Uses efficient WebSocket connections
- **Optimized image processing:** Scales images appropriately for web display
- **Caching:** Reduces unnecessary API calls and processing
- **Background processing:** Offloads heavy operations to background threads

### Network Optimization

- **Compressed data transfer:** Uses efficient binary protocols where possible
- **Selective updates:** Only sends changed data to reduce bandwidth
- **Connection management:** Automatic reconnection on network issues

## Security Considerations

- **Local network only:** Interface is designed for local network access
- **Sudo permissions:** Some system operations require sudo access
- **File permissions:** Ensure proper permissions on configuration files
- **Firewall:** Consider firewall rules for port 5001

## Future Enhancements

Planned features for future releases:

- **Multi-user support** with role-based permissions
- **Plugin system** for custom element types
- **Animation support** for dynamic layouts
- **Mobile app** companion
- **Cloud sync** for layout sharing
- **Advanced scheduling** with conditional logic
- **Integration APIs** for smart home systems

## Contributing

We welcome contributions! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly on Raspberry Pi hardware
5. Submit a pull request

## License

This project is licensed under the MIT License. See the LICENSE file for details.

## Support

For support and questions:

- Check the troubleshooting section above
- Review the system logs
- Open an issue on the project repository
- Join the community discussions

---

**Enjoy your new modern LED Matrix web interface!** 🎉