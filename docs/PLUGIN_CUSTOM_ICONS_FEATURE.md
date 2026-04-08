# Plugin Custom Icons Feature

> ⚠️ **Status:** this doc describes the v2 web interface
> implementation of plugin custom icons. The feature **regressed when
> the v3 web interface was built** — the `getPluginIcon()` helper
> referenced below lived in `templates/index_v2.html` (which is now
> archived) and was not ported to the v3 templates. Plugin tab icons
> in v3 are hardcoded to `fas fa-puzzle-piece`
> (`web_interface/templates/v3/base.html:515` and `:774`). The
> `icon` field in `manifest.json` is currently silently ignored.

## What Was Implemented

You asked: **"How could a plugin add their own custom icon?"**

**Answer:** Plugins can now specify custom icons in their `manifest.json` file using the `icon` field!

## Features Delivered

✅ **Font Awesome Support** - Use any Font Awesome icon (e.g., `fas fa-clock`)  
✅ **Emoji Support** - Use any emoji character (e.g., `⏰` or `👋`)  
✅ **Custom Image Support** - Use custom image files or URLs  
✅ **Automatic Detection** - System automatically detects icon type  
✅ **Fallback Support** - Default puzzle piece icon if none specified  
✅ **Tab & Header Icons** - Icons appear in both tab buttons and configuration page headers  

## How It Works

### For Plugin Developers

Simply add an `icon` field to your plugin's `manifest.json`:

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "icon": "fas fa-star",  // ← Add this line
  "config_schema": "config_schema.json",
  ...
}
```

### Three Icon Types Supported

#### 1. Font Awesome Icons (Recommended)
```json
"icon": "fas fa-clock"
```

Best for: Professional, consistent UI appearance

#### 2. Emoji Icons (Fun!)
```json
"icon": "⏰"
```

Best for: Colorful, fun plugins; no setup needed

#### 3. Custom Images
```json
"icon": "/plugins/my-plugin/logo.png"
```

Best for: Unique branding; requires image file

## Implementation Details

### Frontend Changes (`templates/index_v2.html`)

**New Function: `getPluginIcon(plugin)`**
- Checks if plugin has `icon` field in manifest
- Detects icon type automatically:
  - Contains `fa-` → Font Awesome
  - 1-4 characters → Emoji
  - Starts with URL/path → Custom image
  - Otherwise → Default puzzle piece

**Updated Functions:**
- `generatePluginTabs()` - Uses custom icon for tab button
- `generatePluginConfigForm()` - Uses custom icon in page header

### Example Plugin Updates

**hello-world plugin:**
```json
"icon": "👋"
```

**clock-simple plugin:**
```json
"icon": "fas fa-clock"
```

## Code Example

Here's what the icon detection logic does. **Important:** Plugin manifests must be treated as untrusted input and require escaping/validation before rendering.

```javascript
// Helper function to escape HTML entities
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Helper function to validate and sanitize image URLs
function isValidImageUrl(url) {
    if (!url || typeof url !== 'string') {
        return false;
    }
    
    // Only allow http, https, or relative paths starting with /
    const allowedProtocols = ['http:', 'https:'];
    const urlLower = url.toLowerCase().trim();
    
    // Reject dangerous protocols
    if (urlLower.startsWith('javascript:') || 
        urlLower.startsWith('data:') || 
        urlLower.startsWith('vbscript:') ||
        urlLower.startsWith('onerror=') ||
        urlLower.startsWith('onload=')) {
        return false;
    }
    
    // Allow relative paths starting with /
    if (url.startsWith('/')) {
        return true;
    }
    
    // Validate absolute URLs
    try {
        const urlObj = new URL(url);
        return allowedProtocols.includes(urlObj.protocol);
    } catch (e) {
        // Invalid URL format
        return false;
    }
}

// Helper function to safely validate Font Awesome class names
function isValidFontAwesomeClass(icon) {
    // Whitelist pattern: only allow alphanumeric, dash, underscore, and spaces
    // Must contain 'fa-' for Font Awesome
    const faPattern = /^[a-zA-Z0-9\s_-]*fa-[a-zA-Z0-9-]+[a-zA-Z0-9\s_-]*$/;
    return faPattern.test(icon) && icon.includes('fa-');
}

function getPluginIcon(plugin) {
    if (plugin.icon) {
        const icon = String(plugin.icon).trim();
        
        // Font Awesome icon - escape class name to prevent XSS
        if (isValidFontAwesomeClass(icon)) {
            const escapedIcon = escapeHtml(icon);
            return `<i class="${escapedIcon}"></i>`;
        }
        
        // Emoji - use textContent to safely render (no HTML injection possible)
        if (icon.length <= 4) {
            // Create element and set textContent (safe from XSS)
            const span = document.createElement('span');
            span.style.fontSize = '1.1em';
            span.textContent = icon; // textContent automatically escapes
            return span.outerHTML;
        }
        
        // Custom image - validate URL and set src attribute safely
        if (isValidImageUrl(icon)) {
            // Create img element and set attributes safely
            const img = document.createElement('img');
            img.src = icon; // URL already validated
            img.alt = '';
            img.style.width = '16px';
            img.style.height = '16px';
            return img.outerHTML;
        }
    }
    
    // Default fallback
    return '<i class="fas fa-puzzle-piece"></i>';
}
```

**Security Notes:**
- Plugin manifests are treated as untrusted input
- All text content is escaped using `escapeHtml()` or `textContent`
- Image URLs are validated to only allow `http://`, `https://`, or relative paths starting with `/`
- Dangerous protocols (`javascript:`, `data:`, etc.) are explicitly rejected
- Font Awesome class names are validated against a whitelist pattern
- DOM elements are created and attributes set directly rather than using string interpolation

## Visual Examples

### Before (No Custom Icons)
```
[🧩 Hello World] [🧩 Clock Simple] [🧩 Weather Display]
```

### After (With Custom Icons)
```
[👋 Hello World] [⏰ Clock Simple] [☀️ Weather Display]
```

## Documentation Created

📚 **Comprehensive guide:** `docs/PLUGIN_CUSTOM_ICONS.md`

Contains:
- Complete icon type explanations
- Font Awesome icon recommendations by category
- Emoji suggestions for common plugin types
- Custom image guidelines
- Best practices and troubleshooting
- Examples for every use case

📝 **Updated existing docs:**
- `PLUGIN_CONFIGURATION_TABS.md` - Added icon reference
- `PLUGIN_CONFIG_TABS_SUMMARY.md` - Added icon quick tip
- `PLUGIN_CONFIG_QUICK_START.md` - Added icon bonus section

## Popular Icon Recommendations

### By Plugin Category

**Time & Calendar**
- Font Awesome: `fas fa-clock`, `fas fa-calendar`, `fas fa-hourglass`
- Emoji: ⏰ 📅 ⏱️

**Weather**
- Font Awesome: `fas fa-cloud-sun`, `fas fa-temperature-high`
- Emoji: ☀️ 🌧️ ⛈️

**Finance**
- Font Awesome: `fas fa-chart-line`, `fas fa-dollar-sign`
- Emoji: 💰 📈 💵

**Sports**
- Font Awesome: `fas fa-football-ball`, `fas fa-trophy`
- Emoji: ⚽ 🏀 🎮

**Music**
- Font Awesome: `fas fa-music`, `fas fa-headphones`
- Emoji: 🎵 🎶 🎸

**News**
- Font Awesome: `fas fa-newspaper`, `fas fa-rss`
- Emoji: 📰 📡 📻

**Utilities**
- Font Awesome: `fas fa-tools`, `fas fa-cog`
- Emoji: 🔧 ⚙️ 🛠️

## Usage Examples

### Weather Plugin
```json
{
  "id": "weather-pro",
  "name": "Weather Pro",
  "icon": "fas fa-cloud-sun",
  "description": "Advanced weather display"
}
```
Result: `☁️ Weather Pro` tab

### Game Scores
```json
{
  "id": "game-scores",
  "name": "Game Scores",
  "icon": "🎮",
  "description": "Live game scores"
}
```
Result: `🎮 Game Scores` tab

### Custom Branding
```json
{
  "id": "company-metrics",
  "name": "Company Metrics",
  "icon": "/plugins/company-metrics/logo.svg",
  "description": "Internal dashboard"
}
```
Result: `[logo] Company Metrics` tab

## Benefits

### For Users
- **Visual Recognition** - Instantly identify plugins
- **Better Navigation** - Find plugins faster
- **Professional Appearance** - Polished, modern UI

### For Developers
- **Easy to Add** - Just one line in manifest
- **Flexible Options** - Choose what fits your plugin
- **No Code Required** - Pure configuration

### For the Project
- **Plugin Differentiation** - Each plugin stands out
- **Enhanced UX** - More intuitive interface
- **Branding Support** - Plugins can show identity

## Backward Compatibility

✅ **Fully backward compatible**  
- Plugins without `icon` field still work  
- Default puzzle piece icon used automatically  
- No breaking changes to existing plugins  

## Testing

To test custom icons:

1. **Open web interface** at `http://your-pi-ip:5000`
2. **Check installed plugins**:
   - Hello World should show 👋
   - Clock Simple should show 🕐
3. **Install a new plugin** with custom icon
4. **Verify icon appears** in:
   - Tab navigation bar
   - Plugin configuration page header

## File Changes

### Modified Files
- `templates/index_v2.html`
  - Added `getPluginIcon()` function
  - Updated `generatePluginTabs()` 
  - Updated `generatePluginConfigForm()`

### Updated Plugin Manifests
- `ledmatrix-plugins/plugins/hello-world/manifest.json` - Added emoji icon
- `ledmatrix-plugins/plugins/clock-simple/manifest.json` - Added Font Awesome icon

### New Documentation
- `docs/PLUGIN_CUSTOM_ICONS.md` - Complete guide (80+ lines)

### Updated Documentation
- `docs/PLUGIN_CONFIGURATION_TABS.md`
- `docs/PLUGIN_CONFIG_TABS_SUMMARY.md`
- `docs/PLUGIN_CONFIG_QUICK_START.md`

## Quick Reference

### Add Icon to Your Plugin

```json
{
  "id": "your-plugin",
  "name": "Your Plugin Name",
  "icon": "fas fa-star",  // or emoji or image URL
  "config_schema": "config_schema.json",
  ...
}
```

### Icon Format Examples

```json
// Font Awesome
"icon": "fas fa-star"
"icon": "far fa-heart"
"icon": "fab fa-twitter"

// Emoji
"icon": "⭐"
"icon": "❤️"
"icon": "🐦"

// Custom Image
"icon": "/plugins/my-plugin/icon.png"
"icon": "https://example.com/logo.svg"
```

## Browse Available Icons

- **Font Awesome:** [fontawesome.com/icons](https://fontawesome.com/icons) (Free tier includes 2,000+ icons)
- **Emojis:** [unicode.org/emoji](https://unicode.org/emoji/charts/full-emoji-list.html)

## Best Practices

1. **Choose meaningful icons** - Icon should relate to plugin function
2. **Keep it simple** - Works better at small sizes
3. **Test visibility** - Ensure icon is clear at 16px
4. **Match UI style** - Font Awesome recommended for consistency
5. **Document choice** - Note icon meaning in plugin README

## Troubleshooting

**Icon not showing?**
- Check manifest syntax (JSON valid?)
- Verify icon field spelling
- Refresh plugins in web interface
- Check browser console for errors

**Wrong icon appearing?**
- Font Awesome: Verify class name at fontawesome.com
- Emoji: Try different emoji (platform rendering varies)
- Custom image: Check file path and permissions

## Future Enhancements

Possible future improvements:
- Icon picker in plugin store
- Animated icons support
- SVG path support
- Icon themes/styles
- Dynamic icon changes based on state

## Summary

**Mission accomplished!** 🎉

Plugins can now have custom icons by adding one line to their manifest:

```json
"icon": "fas fa-your-icon"
```

Three formats supported:
- ✅ Font Awesome (professional)
- ✅ Emoji (fun)
- ✅ Custom images (branded)

The feature is:
- ✅ Easy to use (one line)
- ✅ Flexible (three options)
- ✅ Backward compatible
- ✅ Well documented
- ✅ Already working in example plugins

**Ready to use!** 🚀

