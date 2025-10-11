# Plugin Custom Icons Guide

## Overview

Plugins can specify custom icons that appear next to their name in the web interface tabs. This makes your plugin instantly recognizable and adds visual polish to the UI.

## Icon Types Supported

The system supports three types of icons:

### 1. Font Awesome Icons (Recommended)

The web interface uses Font Awesome 6, giving you access to thousands of icons.

**Example:**
```json
{
  "id": "my-plugin",
  "name": "Weather Display",
  "icon": "fas fa-cloud-sun"
}
```

**Common Font Awesome Icons:**
- Clock: `fas fa-clock`
- Weather: `fas fa-cloud-sun`, `fas fa-cloud-rain`
- Calendar: `fas fa-calendar`, `fas fa-calendar-alt`
- Sports: `fas fa-football-ball`, `fas fa-basketball-ball`
- Music: `fas fa-music`, `fas fa-headphones`
- Finance: `fas fa-chart-line`, `fas fa-dollar-sign`
- News: `fas fa-newspaper`, `fas fa-rss`
- Settings: `fas fa-cog`, `fas fa-sliders-h`
- Timer: `fas fa-stopwatch`, `fas fa-hourglass`
- Alert: `fas fa-bell`, `fas fa-exclamation-triangle`
- Heart: `fas fa-heart`, `far fa-heart` (outline)
- Star: `fas fa-star`, `far fa-star` (outline)
- Image: `fas fa-image`, `fas fa-camera`
- Video: `fas fa-video`, `fas fa-film`
- Game: `fas fa-gamepad`, `fas fa-dice`

**Browse all icons:** [Font Awesome Icon Gallery](https://fontawesome.com/icons)

### 2. Emoji Icons (Fun & Simple)

Use any emoji character for a colorful, fun icon.

**Example:**
```json
{
  "id": "hello-world",
  "name": "Hello World",
  "icon": "👋"
}
```

**Popular Emojis:**
- Time: ⏰ 🕐 ⏱️ ⏲️
- Weather: ☀️ ⛅ 🌤️ 🌧️ ⛈️ 🌩️ ❄️
- Sports: ⚽ 🏀 🏈 ⚾ 🎾 🏐
- Music: 🎵 🎶 🎸 🎹 🎤
- Money: 💰 💵 💴 💶 💷
- Calendar: 📅 📆
- News: 📰 📻 📡
- Fun: 🎮 🎲 🎯 🎨 🎭
- Nature: 🌍 🌎 🌏 🌳 🌺 🌸
- Food: 🍕 🍔 🍟 🍦 ☕ 🍰

### 3. Custom Image URLs (Advanced)

Use a custom image file for ultimate branding.

**Example:**
```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "icon": "/plugins/my-plugin/icon.png"
}
```

**Requirements:**
- Image should be 16x16 to 32x32 pixels
- Supported formats: PNG, SVG, JPG, GIF
- Can be a relative path, absolute path, or external URL
- SVG recommended for best quality at any size

## How to Add an Icon

### Step 1: Choose Your Icon

Decide which type suits your plugin:
- **Font Awesome**: Professional, consistent with UI
- **Emoji**: Fun, colorful, no setup needed
- **Custom Image**: Unique branding, requires image file

### Step 2: Add to manifest.json

Add the `icon` field to your plugin's `manifest.json`:

```json
{
  "id": "my-weather-plugin",
  "name": "Weather Display",
  "version": "1.0.0",
  "author": "Your Name",
  "description": "Shows weather information",
  "icon": "fas fa-cloud-sun",  // ← Add this line
  "entry_point": "manager.py",
  ...
}
```

### Step 3: Test Your Plugin

1. Install or update your plugin
2. Open the web interface
3. Look for your plugin's tab
4. The icon should appear next to the plugin name

## Examples

### Weather Plugin
```json
{
  "id": "weather-advanced",
  "name": "Weather Advanced",
  "icon": "fas fa-cloud-sun",
  "description": "Advanced weather display with forecasts"
}
```
**Result:** Tab shows: `☁️ Weather Advanced`

### Clock Plugin
```json
{
  "id": "digital-clock",
  "name": "Digital Clock",
  "icon": "⏰",
  "description": "A beautiful digital clock"
}
```
**Result:** Tab shows: `⏰ Digital Clock`

### Sports Scores Plugin
```json
{
  "id": "sports-scores",
  "name": "Sports Scores",
  "icon": "fas fa-trophy",
  "description": "Live sports scores"
}
```
**Result:** Tab shows: `🏆 Sports Scores`

### Custom Branding
```json
{
  "id": "company-dashboard",
  "name": "Company Dashboard",
  "icon": "/plugins/company-dashboard/logo.svg",
  "description": "Company metrics display"
}
```
**Result:** Tab shows: `[logo] Company Dashboard`

## Best Practices

### 1. Choose Meaningful Icons
- Icon should relate to plugin functionality
- Users should understand what the plugin does at a glance
- Avoid generic icons for specific functionality

### 2. Keep It Simple
- Simpler icons work better at small sizes
- Avoid icons with too much detail
- Test how your icon looks at 16x16 pixels

### 3. Match the UI Style
- Font Awesome icons match the interface best
- If using emoji, consider contrast with background
- Custom images should use similar color schemes

### 4. Consider Accessibility
- Icons should be recognizable without color
- Don't rely solely on color to convey meaning
- The plugin name should be descriptive

### 5. Test on Different Displays
- Check icon clarity on various screen sizes
- Ensure emoji render correctly on target devices
- Custom images should have good contrast

## Icon Categories

Here are recommended icons by plugin category:

### Time & Calendar
- `fas fa-clock`, `fas fa-calendar`, `fas fa-hourglass`
- Emoji: ⏰ 📅 ⏱️

### Weather
- `fas fa-cloud-sun`, `fas fa-temperature-high`, `fas fa-wind`
- Emoji: ☀️ 🌧️ ⛈️

### Finance & Stocks
- `fas fa-chart-line`, `fas fa-dollar-sign`, `fas fa-coins`
- Emoji: 💰 📈 💵

### Sports & Games
- `fas fa-football-ball`, `fas fa-trophy`, `fas fa-gamepad`
- Emoji: ⚽ 🏀 🎮

### Entertainment
- `fas fa-music`, `fas fa-film`, `fas fa-tv`
- Emoji: 🎵 🎬 📺

### News & Information
- `fas fa-newspaper`, `fas fa-rss`, `fas fa-info-circle`
- Emoji: 📰 📡 ℹ️

### Utilities
- `fas fa-tools`, `fas fa-cog`, `fas fa-wrench`
- Emoji: 🔧 ⚙️ 🛠️

### Social Media
- `fab fa-twitter`, `fab fa-facebook`, `fab fa-instagram`
- Emoji: 📱 💬 📧

## Troubleshooting

### Icon Not Showing
1. Check that the `icon` field is correctly spelled in `manifest.json`
2. For Font Awesome icons, verify the class name is correct
3. For custom images, check that the file path is accessible
4. Refresh the plugins in the web interface
5. Check browser console for errors

### Emoji Looks Wrong
- Some emojis render differently on different platforms
- Try a different emoji if one doesn't work well
- Consider using Font Awesome instead for consistency

### Custom Image Not Loading
- Verify the image file exists in the specified path
- Check file permissions (should be readable)
- Try using an absolute path or URL
- Ensure image format is supported (PNG, SVG, JPG, GIF)
- Check image dimensions (16x16 to 32x32 recommended)

### Icon Too Large/Small
- Font Awesome and emoji icons automatically size correctly
- For custom images, adjust the image file dimensions
- SVG images scale best

## Default Behavior

If you don't specify an `icon` field in your manifest:
- The plugin tab will show a default puzzle piece icon: 🧩
- This is the fallback for all plugins without custom icons

## Technical Details

The icon system works as follows:

1. **Frontend reads manifest**: When plugins load, the web interface reads each plugin's `manifest.json`
2. **Icon detection**: The `getPluginIcon()` function determines icon type:
   - Contains `fa-` → Font Awesome icon
   - 1-4 characters → Emoji
   - Starts with `http://`, `https://`, or `/` → Custom image
   - Otherwise → Default puzzle piece
3. **Rendering**: Icon HTML is generated and inserted into:
   - Tab button in navigation bar
   - Configuration page header

## Advanced: Dynamic Icons

Want to change icons programmatically? While not officially supported, you could:

1. Store multiple icon options in your manifest
2. Use JavaScript to swap icons based on plugin state
3. Update the manifest dynamically and refresh plugins

**Example (advanced):**
```json
{
  "id": "status-display",
  "icon": "fas fa-circle",
  "icon_states": {
    "active": "fas fa-check-circle",
    "error": "fas fa-exclamation-circle",
    "warning": "fas fa-exclamation-triangle"
  }
}
```

## Related Documentation

- [Plugin Configuration Tabs](PLUGIN_CONFIGURATION_TABS.md) - Main plugin tabs documentation
- [Plugin Development Guide](plugin_docs/) - How to create plugins
- [Font Awesome Icons](https://fontawesome.com/icons) - Browse all available icons
- [Emoji Reference](https://unicode.org/emoji/charts/full-emoji-list.html) - All emoji options

## Summary

Adding a custom icon to your plugin:

1. **Choose** your icon (Font Awesome, emoji, or custom image)
2. **Add** the `icon` field to `manifest.json`
3. **Test** in the web interface

That's it! Your plugin now has a professional, recognizable icon in the UI. 🎨

