# Sports News Manager

A comprehensive RSS feed ticker system for displaying sports news headlines with dynamic scrolling and intelligent rotation.

## Features

### 🏈 Multiple Sports Feeds
- **NFL**: Latest NFL news and updates
- **NCAA Football**: College football news
- **MLB**: Major League Baseball news
- **NBA**: Basketball news and updates
- **NHL**: Hockey news
- **NCAA Basketball**: College basketball updates
- **Big 10**: Big Ten conference news
- **Top Sports**: General ESPN sports news
- **Custom Feeds**: Add your own RSS feeds

### 📺 Smart Display Features
- **Dynamic Length Detection**: Automatically calculates headline length and adjusts scroll timing
- **Perfect Spacing**: Ensures headlines don't cut off mid-text or loop unnecessarily
- **Intelligent Rotation**: Prevents repetitive content by rotating through different headlines
- **Configurable Speed**: Adjustable scroll speed and timing
- **Visual Separators**: Color-coded separators between different news sources

### ⚙️ Configuration Options
- Enable/disable individual sports feeds
- Set number of headlines per feed (1-5)
- Adjust scroll speed and timing
- Configure rotation behavior
- Customize fonts and colors
- Add custom RSS feeds

## Default RSS Feeds

The system comes pre-configured with these ESPN RSS feeds:

```
MLB: http://espn.com/espn/rss/mlb/news
NFL: http://espn.go.com/espn/rss/nfl/news
NCAA FB: https://www.espn.com/espn/rss/ncf/news
NHL: https://www.espn.com/espn/rss/nhl/news
NBA: https://www.espn.com/espn/rss/nba/news
TOP SPORTS: https://www.espn.com/espn/rss/news
BIG10: https://www.espn.com/blog/feed?blog=bigten
NCAA: https://www.espn.com/espn/rss/ncaa/news
Other: https://www.coveringthecorner.com/rss/current.xml
```

## Usage

### Command Line Management

Use the `enable_news_manager.py` script to manage the news manager:

```bash
# Check current status
python3 enable_news_manager.py status

# Enable news manager
python3 enable_news_manager.py enable

# Disable news manager
python3 enable_news_manager.py disable
```

### Web Interface

Access the news manager through the web interface:

1. Open your browser to `http://your-display-ip:5000`
2. Click on the "News Manager" tab
3. Configure your preferred settings:
   - Enable/disable the news manager
   - Select which sports feeds to display
   - Set headlines per feed (1-5)
   - Configure scroll speed and timing
   - Add custom RSS feeds
   - Enable/disable rotation

### Configuration File

Direct configuration via `config/config.json`:

```json
{
  "news_manager": {
    "enabled": true,
    "update_interval": 300,
    "scroll_speed": 2,
    "scroll_delay": 0.02,
    "headlines_per_feed": 2,
    "enabled_feeds": ["NFL", "NCAA FB"],
    "custom_feeds": {
      "My Team": "https://example.com/rss"
    },
    "rotation_enabled": true,
    "rotation_threshold": 3,
    "font_size": 12,
    "font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "text_color": [255, 255, 255],
    "separator_color": [255, 0, 0]
  }
}
```

## How It Works

### Dynamic Length Calculation

The system intelligently calculates the display time for each headline:

1. **Text Measurement**: Uses PIL to measure the exact pixel width of each headline
2. **Scroll Distance**: Calculates total distance needed (text width + display width)
3. **Timing Calculation**: Determines exact scroll time based on speed settings
4. **Perfect Spacing**: Ensures smooth transitions between headlines

### Rotation Algorithm

Prevents repetitive content by:

1. **Tracking Display Count**: Monitors how many times each headline has been shown
2. **Threshold Management**: After a configured number of cycles, rotates to new content
3. **Feed Balancing**: Ensures even distribution across selected feeds
4. **Freshness**: Prioritizes newer headlines when available

### Example Calculation

For a headline "Breaking: Major trade shakes up NFL draft prospects" (51 characters):

- **Estimated Width**: ~306 pixels (6 pixels per character average)
- **Display Width**: 128 pixels
- **Total Scroll Distance**: 306 + 128 = 434 pixels
- **Scroll Speed**: 2 pixels per frame
- **Frame Delay**: 0.02 seconds
- **Total Time**: (434 ÷ 2) × 0.02 = 4.34 seconds

## Testing

### RSS Feed Test

Test the RSS feeds directly:

```bash
python3 simple_news_test.py
```

This will:
- Test connectivity to ESPN RSS feeds
- Parse sample headlines
- Calculate scroll timing
- Demonstrate rotation logic

### Integration Test

Test the full news manager without hardware dependencies:

```bash
python3 test_news_manager.py
```

## API Endpoints

The system provides REST API endpoints for external control:

- `GET /news_manager/status` - Get current status and configuration
- `POST /news_manager/update` - Update configuration
- `POST /news_manager/refresh` - Force refresh of news data

## Troubleshooting

### Common Issues

1. **RSS Feed Not Loading**
   - Check internet connectivity
   - Verify RSS URL is valid
   - Check for rate limiting

2. **Slow Performance**
   - Reduce number of enabled feeds
   - Increase update interval
   - Check network latency

3. **Text Not Displaying**
   - Verify font path exists
   - Check text color settings
   - Ensure display dimensions are correct

### Debug Mode

Enable debug logging by setting the log level:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Customization

### Adding Custom Feeds

Add your own RSS feeds through the web interface or configuration:

```json
"custom_feeds": {
  "My Local Team": "https://myteam.com/rss",
  "Sports Blog": "https://sportsblog.com/feed"
}
```

### Styling Options

Customize the appearance:

- **Font Size**: Adjust text size (8-24 pixels)
- **Colors**: RGB values for text and separators
- **Font Path**: Use different system fonts
- **Scroll Speed**: 1-10 pixels per frame
- **Timing**: 0.01-0.1 seconds per frame

## Performance

The news manager is optimized for:

- **Low Memory Usage**: Efficient caching and cleanup
- **Network Efficiency**: Smart update intervals and retry logic
- **Smooth Scrolling**: Consistent frame rates
- **Fast Loading**: Parallel RSS feed processing

## Future Enhancements

Planned features:
- Breaking news alerts
- Team-specific filtering
- Score integration
- Social media feeds
- Voice announcements
- Mobile app control

## Support

For issues or questions:
1. Check the troubleshooting section
2. Review the logs for error messages
3. Test individual RSS feeds
4. Verify configuration settings