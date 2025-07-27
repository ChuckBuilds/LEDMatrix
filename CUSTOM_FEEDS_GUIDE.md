# Adding Custom RSS Feeds & Sports - Complete Guide

This guide shows you **3 different ways** to add custom RSS feeds like F1, MotoGP, or any personal feeds to your news manager.

## Quick Examples

### F1 Racing Feeds
```bash
# BBC F1 (Recommended - works well)
python3 add_custom_feed_example.py add "BBC F1" "http://feeds.bbci.co.uk/sport/formula1/rss.xml"

# Motorsport.com F1
python3 add_custom_feed_example.py add "Motorsport F1" "https://www.motorsport.com/rss/f1/news/"

# Formula1.com Official
python3 add_custom_feed_example.py add "F1 Official" "https://www.formula1.com/en/latest/all.xml"
```

### Other Sports
```bash
# MotoGP
python3 add_custom_feed_example.py add "MotoGP" "https://www.motogp.com/en/rss/news"

# Tennis
python3 add_custom_feed_example.py add "Tennis" "https://www.atptour.com/en/rss/news"

# Golf
python3 add_custom_feed_example.py add "Golf" "https://www.pgatour.com/news.rss"

# Soccer/Football
python3 add_custom_feed_example.py add "ESPN Soccer" "https://www.espn.com/espn/rss/soccer/news"
```

### Personal/Blog Feeds
```bash
# Personal blog
python3 add_custom_feed_example.py add "My Blog" "https://myblog.com/rss.xml"

# Tech news
python3 add_custom_feed_example.py add "TechCrunch" "https://techcrunch.com/feed/"

# Local news
python3 add_custom_feed_example.py add "Local News" "https://localnews.com/rss"
```

---

## Method 1: Command Line (Easiest)

### Add a Feed
```bash
python3 add_custom_feed_example.py add "FEED_NAME" "RSS_URL"
```

### List All Feeds
```bash
python3 add_custom_feed_example.py list
```

### Remove a Feed
```bash
python3 add_custom_feed_example.py remove "FEED_NAME"
```

### Example: Adding F1
```bash
# Step 1: Check current feeds
python3 add_custom_feed_example.py list

# Step 2: Add BBC F1 feed
python3 add_custom_feed_example.py add "BBC F1" "http://feeds.bbci.co.uk/sport/formula1/rss.xml"

# Step 3: Verify it was added
python3 add_custom_feed_example.py list
```

---

## Method 2: Web Interface

1. **Open Web Interface**: Go to `http://your-display-ip:5000`
2. **Navigate to News Tab**: Click the "News Manager" tab
3. **Add Custom Feed**:
   - Enter feed name in "Feed Name" field (e.g., "BBC F1")
   - Enter RSS URL in "RSS Feed URL" field
   - Click "Add Feed" button
4. **Enable the Feed**: Check the checkbox next to your new feed
5. **Save Settings**: Click "Save News Settings"

---

## Method 3: Direct Config Edit

Edit `config/config.json` directly:

```json
{
  "news_manager": {
    "enabled": true,
    "enabled_feeds": ["NFL", "NCAA FB", "BBC F1"],
    "custom_feeds": {
      "BBC F1": "http://feeds.bbci.co.uk/sport/formula1/rss.xml",
      "Motorsport F1": "https://www.motorsport.com/rss/f1/news/",
      "My Blog": "https://myblog.com/rss.xml"
    },
    "headlines_per_feed": 2
  }
}
```

---

## Finding RSS Feeds

### Popular Sports RSS Feeds

| Sport | Source | RSS URL |
|-------|--------|---------|
| **F1** | BBC Sport | `http://feeds.bbci.co.uk/sport/formula1/rss.xml` |
| **F1** | Motorsport.com | `https://www.motorsport.com/rss/f1/news/` |
| **MotoGP** | Official | `https://www.motogp.com/en/rss/news` |
| **Tennis** | ATP Tour | `https://www.atptour.com/en/rss/news` |
| **Golf** | PGA Tour | `https://www.pgatour.com/news.rss` |
| **Soccer** | ESPN | `https://www.espn.com/espn/rss/soccer/news` |
| **Boxing** | ESPN | `https://www.espn.com/espn/rss/boxing/news` |
| **UFC/MMA** | ESPN | `https://www.espn.com/espn/rss/mma/news` |

### How to Find RSS Feeds
1. **Look for RSS icons** on websites
2. **Check `/rss`, `/feed`, or `/rss.xml`** paths
3. **Use RSS discovery tools** like RSS Feed Finder
4. **Check site footers** for RSS links

### Testing RSS Feeds
```bash
# Test if a feed works before adding it
python3 -c "
import feedparser
import requests
url = 'YOUR_RSS_URL_HERE'
try:
    response = requests.get(url, timeout=10)
    feed = feedparser.parse(response.content)
    print(f'SUCCESS: Feed works! Title: {feed.feed.get(\"title\", \"N/A\")}')
    print(f'{len(feed.entries)} articles found')
    if feed.entries:
        print(f'Latest: {feed.entries[0].title}')
except Exception as e:
    print(f'ERROR: {e}')
"
```

---

## Advanced Configuration

### Controlling Feed Behavior

```json
{
  "news_manager": {
    "headlines_per_feed": 3,          // Headlines from each feed
    "scroll_speed": 2,                // Pixels per frame
    "scroll_delay": 0.02,             // Seconds between updates
    "rotation_enabled": true,         // Rotate content to avoid repetition
    "rotation_threshold": 3,          // Cycles before rotating
    "update_interval": 300            // Seconds between feed updates
  }
}
```

### Feed Priority
Feeds are displayed in the order they appear in `enabled_feeds`:
```json
"enabled_feeds": ["NFL", "BBC F1", "NCAA FB"]  // NFL first, then F1, then NCAA
```

### Custom Display Names
You can use any display name for feeds:
```bash
python3 add_custom_feed_example.py add "Formula 1" "http://feeds.bbci.co.uk/sport/formula1/rss.xml"
python3 add_custom_feed_example.py add "Basketball News" "https://www.espn.com/espn/rss/nba/news"
```

---

## Troubleshooting

### Feed Not Working?
1. **Test the RSS URL** using the testing command above
2. **Check for HTTPS vs HTTP** - some feeds require secure connections
3. **Verify the feed format** - must be valid RSS or Atom
4. **Check rate limiting** - some sites block frequent requests

### Common Issues
- **403 Forbidden**: Site blocks automated requests (try different feed)
- **SSL Errors**: Use HTTP instead of HTTPS if available
- **No Content**: Feed might be empty or incorrectly formatted
- **Slow Loading**: Increase timeout in news manager settings

### Feed Alternatives
If one feed doesn't work, try alternatives:
- **ESPN feeds** sometimes have access restrictions
- **BBC feeds** are generally reliable
- **Official sport websites** often have RSS feeds
- **News aggregators** like Google News have topic-specific feeds

---

## Real-World Example: Complete F1 Setup

```bash
# 1. List current setup
python3 add_custom_feed_example.py list

# 2. Add multiple F1 sources for better coverage
python3 add_custom_feed_example.py add "BBC F1" "http://feeds.bbci.co.uk/sport/formula1/rss.xml"
python3 add_custom_feed_example.py add "Motorsport F1" "https://www.motorsport.com/rss/f1/news/"

# 3. Add other racing series
python3 add_custom_feed_example.py add "MotoGP" "https://www.motogp.com/en/rss/news"

# 4. Verify all feeds work
python3 simple_news_test.py

# 5. Check final configuration
python3 add_custom_feed_example.py list
```

Result: Your display will now rotate between NFL, NCAA FB, BBC F1, Motorsport F1, and MotoGP headlines!

---

## Pro Tips

1. **Start Small**: Add one feed at a time and test it
2. **Mix Sources**: Use multiple sources for the same sport for better coverage
3. **Monitor Performance**: Too many feeds can slow down updates
4. **Use Descriptive Names**: "BBC F1" is better than just "F1"
5. **Test Regularly**: RSS feeds can change or break over time
6. **Backup Config**: Save your `config.json` before making changes

---

**Need help?** The news manager is designed to be flexible and user-friendly. Start with the command line method - it's the easiest way to get started!