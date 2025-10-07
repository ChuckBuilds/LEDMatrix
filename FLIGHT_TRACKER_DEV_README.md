# Flight Tracker Development Viewer

A Windows development tool for visualizing the same geographic tiles and ADS-B data that the LED Matrix project displays on the Raspberry Pi.

## Features

- **Map Tile Display**: Shows the same OpenStreetMap tiles used by the LED Matrix
- **ADS-B Data Overlay**: Displays aircraft positions with altitude-based coloring
- **Interactive Controls**: Adjust center location, radius, and zoom
- **Real-time Updates**: Fetches live aircraft data from SkyAware
- **Altitude Color Coding**: Same color scheme as the LED Matrix display

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements-dev.txt
```

2. Ensure you have access to the SkyAware API (same URL as configured in your LED Matrix)

## Usage

Run the development viewer:
```bash
python flight_tracker_dev_viewer.py
```

### Controls

- **Center Lat/Lon**: Set the center point of the map
- **Radius**: Set the radius in miles to display
- **Update Location**: Refresh the map with new coordinates

### Configuration

The viewer automatically loads configuration from:
1. `config/config.json` (if available)
2. `config/config.template.json` (fallback)
3. Default values (if no config files found)

### Map Tiles

The viewer uses the same tile providers as the LED Matrix:
- **OpenStreetMap** (default)
- **CartoDB Light**
- **CartoDB Dark**
- **Stamen Terrain**

Tiles are cached locally for performance.

## Features Matching LED Matrix

### Map Background
- Same tile fetching logic
- Same zoom level calculation based on radius
- Same fade intensity and caching
- Same tile composition and cropping

### ADS-B Data
- Same aircraft filtering by distance
- Same altitude-based color coding
- Same heading arrow display
- Same data processing pipeline

### Display Elements
- Center position marker (white cross)
- Aircraft with heading arrows
- Altitude-based color coding
- Real-time updates

## Troubleshooting

### No Aircraft Data
- Check that SkyAware URL is accessible
- Verify network connectivity
- Check that aircraft are within the specified radius

### Map Tiles Not Loading
- Check internet connectivity
- Verify tile provider URLs are accessible
- Check tile cache directory permissions

### Performance Issues
- Reduce map radius for faster tile loading
- Increase update interval for less frequent updates
- Clear tile cache if tiles are corrupted

## Development Notes

This viewer is designed to match the LED Matrix display as closely as possible:

- Uses the same tile fetching and caching logic
- Implements the same coordinate transformations
- Uses the same altitude color coding
- Processes ADS-B data identically

The main differences are:
- GUI display instead of LED matrix
- Larger display area for better visibility
- Interactive controls for testing different locations
- Real-time updates instead of fixed intervals

## File Structure

```
flight_tracker_dev_viewer.py  # Main application
requirements-dev.txt         # Python dependencies
FLIGHT_TRACKER_DEV_README.md # This file
```

## Integration with LED Matrix Project

This development viewer is designed to work alongside the main LED Matrix project:

1. **Shared Configuration**: Uses the same config files
2. **Same Logic**: Implements identical tile fetching and aircraft processing
3. **Testing**: Perfect for testing new features before deploying to Pi
4. **Debugging**: Easier to debug issues in a GUI environment

The viewer can be used to:
- Test new map locations
- Verify aircraft data processing
- Debug tile fetching issues
- Validate configuration changes
- Develop new features before Pi deployment
