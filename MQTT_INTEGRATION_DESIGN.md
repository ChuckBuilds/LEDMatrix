# MQTT HomeAssistant Integration Design Document

## Overview

This document outlines the design and implementation plan for integrating MQTT messaging with HomeAssistant into the LEDMatrix project. The integration will enable users to create custom displays that show HomeAssistant sensor data, trigger on-demand notifications, and manage home automation information on their LED matrix displays.

## Core Features

### 1. MQTT Connection Management
- **Secure Connection**: Connect to HomeAssistant MQTT broker with authentication
- **Topic Subscription**: Automatically subscribe to relevant MQTT topics
- **Connection Monitoring**: Real-time connection status and auto-reconnection
- **Error Handling**: Graceful handling of connection failures and message errors

### 2. Display Types

#### 2.1 Rotation Displays
- **Purpose**: Show recurring information in normal display rotation
- **Examples**: 
  - Current temperature from thermostats
  - Number of people home
  - Light status summary
  - Energy usage
- **Behavior**: Rotates with other managers (weather, sports, etc.)

#### 2.2 On-Demand Displays
- **Purpose**: Interrupt normal display rotation for urgent notifications
- **Examples**:
  - Doorbell rings
  - Laundry cycle complete
  - Security alerts
  - Motion detection
- **Behavior**: Takes priority over all other displays including live games
- **Duration**: Configurable display time (5-60 seconds)

### 3. Visual Display Builder

#### 3.1 Drag-and-Drop Interface
- **Elements**: Text, icons, rectangles, lines
- **Positioning**: Pixel-perfect positioning with grid overlay
- **Properties**: Real-time property editing panel
- **Preview**: Live preview with simulated MQTT data

#### 3.2 Element Types
- **Text Elements**: 
  - MQTT topic binding
  - Custom formatting (e.g., "{value}°F")
  - Font selection and sizing
  - Color customization
- **Icon Elements**:
  - Custom icon upload
  - Preset icon library
  - Automatic resizing for LED matrix
  - Fallback text when icon unavailable
- **Shape Elements**:
  - Rectangles (filled/outline)
  - Lines
  - Custom colors

#### 3.3 Layout Management
- **Multiple Displays**: Support for unlimited display configurations
- **Naming**: Descriptive names for easy management
- **Categories**: Organize by room, device type, or purpose
- **Templates**: Save and reuse common layouts

### 4. Asset Management System

#### 4.1 Icon Upload System
- **Supported Formats**: PNG, JPG, GIF
- **Auto-Optimization**: Resize and optimize for LED matrix display
- **Organization**: User uploads vs preset icons
- **Metadata**: Tags, descriptions, and usage tracking

#### 4.2 Icon Library Structure
```
assets/mqtt_icons/
├── user_uploads/
│   ├── doorbell.png
│   ├── thermostat.png
│   └── washing_machine.png
├── presets/
│   ├── temperature.png
│   ├── humidity.png
│   ├── motion.png
│   ├── door.png
│   └── light.png
└── metadata.json
```

### 5. Configuration Schema

#### 5.1 MQTT Broker Configuration
```json
{
  "mqtt": {
    "enabled": true,
    "broker": {
      "host": "192.168.1.100",
      "port": 1883,
      "username": "homeassistant",
      "password": "your_password",
      "client_id": "ledmatrix",
      "keepalive": 60,
      "ssl": false
    }
  }
}
```

#### 5.2 Display Configuration
```json
{
  "mqtt": {
    "displays": {
      "rotation": [
        {
          "name": "Living Room Temperature",
          "type": "rotation",
          "priority": 1,
          "duration": 15,
          "elements": [
            {
              "type": "icon",
              "x": 0, "y": 0,
              "width": 24, "height": 24,
              "path": "assets/mqtt_icons/presets/temperature.png"
            },
            {
              "type": "text",
              "x": 30, "y": 8,
              "mqtt_topic": "homeassistant/climate/living_room/current_temperature",
              "format": "{value}°F",
              "font_size": 12,
              "color": [255, 255, 255]
            }
          ]
        }
      ],
      "ondemand": [
        {
          "name": "Doorbell Alert",
          "type": "ondemand",
          "trigger_topic": "homeassistant/binary_sensor/doorbell/state",
          "trigger_value": "on",
          "duration": 10,
          "elements": [
            {
              "type": "icon",
              "x": 20, "y": 16,
              "width": 32, "height": 32,
              "path": "assets/mqtt_icons/user_uploads/doorbell.png"
            },
            {
              "type": "text",
              "x": 10, "y": 50,
              "text": "DOORBELL",
              "font_size": 8,
              "color": [255, 0, 0]
            }
          ]
        }
      ]
    }
  }
}
```

### 6. Priority System

#### 6.1 Display Priority Levels
1. **On-Demand MQTT** (Highest Priority)
   - Interrupts all other displays
   - Shows for configured duration
   - Returns to previous display when complete

2. **Live Sports Games** (High Priority)
   - Normal live game behavior
   - MQTT on-demand can still interrupt

3. **Rotation Displays** (Normal Priority)
   - MQTT rotation displays
   - Weather, stocks, sports, etc.

4. **Clock** (Lowest Priority)
   - Fallback when no other content

#### 6.2 Queue Management
- **On-Demand Queue**: Multiple on-demand displays can queue up
- **FIFO Processing**: First in, first out processing
- **Queue Overflow**: Limit queue size to prevent memory issues
- **Timeout Handling**: Remove stale queue entries

### 7. Web Interface Integration

#### 7.1 New MQTT Tab
- **Connection Status**: Real-time broker connection status
- **Display Builder**: Visual editor for creating layouts
- **Icon Management**: Upload and organize custom icons
- **Topic Browser**: Browse available MQTT topics
- **Preview System**: Test displays with simulated data

#### 7.2 Enhanced Editor Features
- **Grid Overlay**: Pixel-perfect positioning
- **Zoom Controls**: Scale preview for better editing
- **Element Selection**: Click to select and edit properties
- **Undo/Redo**: Edit history management
- **Import/Export**: Share display configurations

### 8. Technical Implementation

#### 8.1 Core Components

**MQTTManager Class**
```python
class MQTTManager:
    - Connection management
    - Message processing
    - Display coordination
    - Priority handling
```

**Display Builder Components**
```python
class MQTTDisplayBuilder:
    - Visual editor interface
    - Element management
    - Property editing
    - Preview generation
```

**Icon Management System**
```python
class MQTTIconManager:
    - Upload handling
    - Optimization
    - Library management
    - Metadata tracking
```

#### 8.2 Integration Points

**DisplayController Integration**
- Add MQTT manager initialization
- Implement priority system
- Handle display rotation
- Manage on-demand interruptions

**Web Interface Integration**
- New MQTT tab in existing interface
- Extend existing editor framework
- Add icon upload endpoints
- Real-time preview system

#### 8.3 Dependencies
- `paho-mqtt`: MQTT client library
- `Pillow`: Image processing for icons
- `Flask`: Web interface extensions
- `threading`: Background MQTT processing

### 9. User Experience Flow

#### 9.1 Initial Setup
1. User enables MQTT in configuration
2. Enters HomeAssistant broker details
3. Tests connection
4. System auto-discovers available topics

#### 9.2 Creating Displays
1. User opens MQTT tab in web interface
2. Selects display type (rotation/on-demand)
3. Drags elements onto canvas
4. Configures MQTT topics and formatting
5. Uploads custom icons as needed
6. Previews display with simulated data
7. Saves display configuration

#### 9.3 Managing Displays
1. View list of configured displays
2. Edit existing displays
3. Test displays with live data
4. Organize displays by category
5. Enable/disable individual displays

### 10. Error Handling & Edge Cases

#### 10.1 Connection Issues
- **Broker Unavailable**: Graceful degradation, retry logic
- **Authentication Failure**: Clear error messages, retry options
- **Network Interruption**: Auto-reconnection with exponential backoff

#### 10.2 Data Issues
- **Missing Topics**: Fallback to default values
- **Invalid Data**: Data validation and sanitization
- **Topic Changes**: Handle topic renames gracefully

#### 10.3 Display Issues
- **Missing Icons**: Fallback to text or default icon
- **Invalid Layouts**: Validation and error reporting
- **Performance**: Optimize for large numbers of displays

### 11. Security Considerations

#### 11.1 MQTT Security
- **Authentication**: Username/password authentication
- **TLS/SSL**: Optional encrypted connections
- **Topic Access**: Limit to necessary topics only

#### 11.2 File Upload Security
- **File Validation**: Check file types and sizes
- **Path Sanitization**: Prevent directory traversal
- **Storage Limits**: Limit total storage usage

### 12. Performance Considerations

#### 12.1 Memory Management
- **Message Caching**: Limit stored MQTT messages
- **Icon Optimization**: Compress and resize icons
- **Display Queue**: Limit on-demand queue size

#### 12.2 Network Optimization
- **Selective Subscriptions**: Only subscribe to needed topics
- **Message Filtering**: Filter irrelevant messages
- **Connection Pooling**: Efficient connection management

### 13. Testing Strategy

#### 13.1 Unit Tests
- MQTT connection handling
- Message processing
- Display rendering
- Icon management

#### 13.2 Integration Tests
- HomeAssistant connectivity
- Display priority system
- Web interface functionality
- End-to-end user workflows

#### 13.3 Hardware Tests
- Raspberry Pi performance
- LED matrix rendering
- Memory usage monitoring
- Long-running stability

### 14. Deployment & Migration

#### 14.1 Configuration Migration
- Add MQTT section to existing config
- Preserve existing settings
- Provide migration script

#### 14.2 Asset Migration
- Create MQTT asset directories
- Provide default icon library
- Migration documentation

#### 14.3 Dependencies
- Add MQTT library to requirements
- Update installation scripts
- Provide setup documentation

## Implementation Phases

### Phase 1: Core MQTT Infrastructure (Week 1)
- MQTTManager class implementation
- Basic connection and message handling
- Configuration schema definition
- Integration with DisplayController

### Phase 2: Display Builder (Week 2)
- Web interface MQTT tab
- Visual display editor
- Element management system
- Preview functionality

### Phase 3: Asset Management (Week 3)
- Icon upload system
- Asset organization
- Optimization and validation
- Library management interface

### Phase 4: Advanced Features (Week 4)
- Priority system implementation
- On-demand queue management
- Advanced display options
- Performance optimization

### Phase 5: Testing & Polish (Week 5)
- Comprehensive testing
- Error handling improvements
- Documentation completion
- User experience refinements

## Success Metrics

### Technical Metrics
- MQTT connection stability (>99% uptime)
- Message processing latency (<100ms)
- Display rendering performance (60fps)
- Memory usage efficiency (<50MB overhead)

### User Experience Metrics
- Display creation time (<5 minutes for basic display)
- Icon upload success rate (>95%)
- Configuration save/load reliability (>99%)
- User satisfaction with interface

### Integration Metrics
- HomeAssistant compatibility (all common topics)
- LED matrix performance (no frame drops)
- System stability (24/7 operation)
- Error recovery time (<30 seconds)

## Future Enhancements

### Advanced Display Features
- Animation support for icons
- Multi-line text with scrolling
- Progress bars and gauges
- Color gradients and effects

### Smart Home Integration
- Device status aggregation
- Scene activation displays
- Energy usage dashboards
- Security system integration

### Community Features
- Display template sharing
- Icon library contributions
- Configuration backups
- Remote management

This design document provides a comprehensive roadmap for implementing MQTT HomeAssistant integration into the LEDMatrix project. The modular approach ensures maintainability while the user-friendly interface makes the feature accessible to all skill levels.
