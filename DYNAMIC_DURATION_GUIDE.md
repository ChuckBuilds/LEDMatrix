# Dynamic Duration Feature - Complete Guide

The news manager now includes intelligent **dynamic duration calculation** that automatically determines the exact time needed to display all your selected headlines without cutting off mid-scroll.

## How It Works

### Automatic Calculation
The system calculates the perfect display duration by:

1. **Measuring Text Width**: Calculates the exact pixel width of all headlines combined
2. **Computing Scroll Distance**: Determines how far text needs to scroll (display width + text width)
3. **Calculating Time**: Uses scroll speed and delay to compute exact timing
4. **Adding Buffer**: Includes configurable buffer time for smooth transitions
5. **Applying Limits**: Ensures duration stays within your min/max preferences

### Real-World Example
With current settings (4 feeds, 2 headlines each):
- **Total Headlines**: 8 headlines per cycle
- **Estimated Duration**: 57 seconds
- **Cycles per Hour**: ~63 cycles
- **Result**: Perfect timing, no cut-offs

## Configuration Options

### Core Settings
```json
{
  "news_manager": {
    "dynamic_duration": true,        // Enable/disable feature
    "min_duration": 30,              // Minimum display time (seconds)
    "max_duration": 300,             // Maximum display time (seconds)  
    "duration_buffer": 0.1,          // Buffer time (10% extra)
    "headlines_per_feed": 2,         // Headlines from each feed
    "scroll_speed": 2,               // Pixels per frame
    "scroll_delay": 0.02             // Seconds per frame
  }
}
```

### Duration Scenarios

| Scenario | Headlines | Est. Duration | Cycles/Hour |
|----------|-----------|---------------|-------------|
| **Light** | 4 headlines | 30s (min) | 120 |
| **Medium** | 6 headlines | 30s (min) | 120 |
| **Current** | 8 headlines | 57s | 63 |
| **Heavy** | 12 headlines | 85s | 42 |
| **Maximum** | 20+ headlines | 300s (max) | 12 |

## Benefits

### Perfect Timing
- **No Cut-offs**: Headlines never cut off mid-sentence
- **Complete Cycles**: Always shows full rotation of all selected content
- **Smooth Transitions**: Buffer time prevents jarring switches

### Intelligent Scaling
- **Adapts to Content**: More feeds = longer duration automatically
- **User Control**: Set your preferred min/max limits
- **Flexible**: Works with any combination of feeds and headlines

### Predictable Behavior
- **Consistent Experience**: Same content always takes same time
- **Reliable Cycling**: Know exactly when content will repeat
- **Configurable**: Adjust to your viewing preferences

## Usage Examples

### Command Line Testing
```bash
# Test dynamic duration calculations
python3 test_dynamic_duration.py

# Check current status
python3 test_dynamic_duration.py status
```

### Configuration Changes
```bash
# Add more feeds (increases duration)
python3 add_custom_feed_example.py add "Tennis" "https://www.atptour.com/en/rss/news"

# Check new duration
python3 test_dynamic_duration.py status
```

### Web Interface
1. Go to `http://display-ip:5000`
2. Click "News Manager" tab
3. Adjust "Duration Settings":
   - **Min Duration**: Shortest acceptable cycle time
   - **Max Duration**: Longest acceptable cycle time
   - **Buffer**: Extra time for smooth transitions

## Advanced Configuration

### Fine-Tuning Duration
```json
{
  "min_duration": 45,      // Increase for longer minimum cycles
  "max_duration": 180,     // Decrease for shorter maximum cycles
  "duration_buffer": 0.15  // Increase buffer for more transition time
}
```

### Scroll Speed Impact
```json
{
  "scroll_speed": 3,       // Faster scroll = shorter duration
  "scroll_delay": 0.015    // Less delay = shorter duration
}
```

### Content Control
```json
{
  "headlines_per_feed": 3, // More headlines = longer duration
  "enabled_feeds": [       // More feeds = longer duration
    "NFL", "NBA", "MLB", "NHL", "BBC F1", "Tennis"
  ]
}
```

## Troubleshooting

### Duration Too Short
- **Increase** `min_duration`
- **Add** more feeds or headlines per feed
- **Decrease** `scroll_speed`

### Duration Too Long
- **Decrease** `max_duration`
- **Remove** some feeds
- **Reduce** `headlines_per_feed`
- **Increase** `scroll_speed`

### Jerky Transitions
- **Increase** `duration_buffer`
- **Adjust** `scroll_delay`

## Disable Dynamic Duration

To use fixed timing instead:
```json
{
  "dynamic_duration": false,
  "fixed_duration": 60      // Fixed 60-second cycles
}
```

## Technical Details

### Calculation Formula
```
total_scroll_distance = display_width + text_width
frames_needed = total_scroll_distance / scroll_speed
base_time = frames_needed * scroll_delay
buffer_time = base_time * duration_buffer
final_duration = base_time + buffer_time (within min/max limits)
```

### Display Integration
The display controller automatically:
1. Calls `news_manager.get_dynamic_duration()`
2. Uses returned value for display timing
3. Switches to next mode after exact calculated time
4. Logs duration decisions for debugging

## Best Practices

1. **Start Conservative**: Use default settings initially
2. **Test Changes**: Use test script to preview duration changes
3. **Monitor Performance**: Watch for smooth transitions
4. **Adjust Gradually**: Make small changes to settings
5. **Consider Viewing**: Match duration to your typical viewing patterns

The dynamic duration feature ensures your news ticker always displays complete, perfectly-timed content cycles regardless of how many feeds or headlines you configure!