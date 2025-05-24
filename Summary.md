# LED Matrix Display System

A sophisticated LED matrix display system that provides real-time information display capabilities for various data sources. The system is highly configurable and supports multiple display modes that can be enabled or disabled based on user preferences.

## Core Features

### Time and Weather
- Real-time clock display
- Weather information with custom icons
- Calendar event display

### Sports Information
The system supports live, recent, and upcoming game information for multiple sports leagues:
- NHL (Hockey)
- NBA (Basketball)
- MLB (Baseball)
- NFL (Football)
- NCAA Football
- NCAA Men's Basketball
- NCAA Men's Baseball
- Soccer

### Financial Information
- Real-time stock price updates
- Stock news headlines
- Customizable stock watchlists

### Entertainment
- Music playback information from multiple sources:
  - Spotify integration
  - YouTube Music integration
- Album art display
- Now playing information with scrolling text
- YouTube video information display

### Custom Display Features
- Text display capabilities
- Font testing and customization
- Configurable display modes
- Cache management for improved performance

## Technical Features

### Configuration Management
- JSON-based configuration system
- Modular design allowing easy enabling/disabling of features
- Customizable display settings for each module

### Display Management
- Efficient LED matrix control
- Support for different display modes
- Smooth transitions between different information displays
- Customizable display durations and rotation patterns

### Authentication and API Integration
- Spotify authentication support
- YouTube Music integration
- Various sports API integrations
- Weather API integration
- Stock market data integration

### Performance Optimization
- Caching system for API responses
- Efficient image processing for album art
- Optimized text scrolling algorithms
- Background polling for real-time updates

## System Architecture

The system is built with a modular architecture that allows for easy extension and maintenance:
- `DisplayController`: Main orchestrator managing all display modes
- Individual managers for each feature (sports, weather, music, etc.)
- Separate authentication handlers for different services
- Configurable display modes and rotation patterns

## Configuration

The system can be configured through a JSON configuration file that allows users to:
- Enable/disable specific features
- Set display durations
- Configure API keys and endpoints
- Customize display modes and rotation patterns
- Set preferred music sources
- Configure sports team preferences

## Requirements

- Python 3.x
- LED Matrix hardware
- Internet connection for real-time updates
- API keys for various services (configurable)
- Required Python packages (listed in requirements.txt)

## Usage

The system can be started with the main script, which will initialize all enabled features and begin displaying information according to the configuration. Users can customize their experience by modifying the configuration file to enable/disable features and adjust display settings. 