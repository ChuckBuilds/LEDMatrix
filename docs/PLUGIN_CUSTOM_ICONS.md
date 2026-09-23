# Plugin Custom Icons Guide

## Overview

A plugin can name an icon for its tab in the web interface's second nav row
(next to **Plugin Manager**) with the `icon` field in `manifest.json`.

> **Status:** the tab code honors `icon`, but `GET /api/v3/plugins/installed`
> (`web_interface/blueprints/api_v3/plugins.py`) does not currently include
> the manifest's `icon` in its response, so every tab shows the default
> puzzle piece. Setting `icon` is harmless and will take effect once the API
> passes it through again.

## Font Awesome classes only

`icon` is used verbatim as the CSS class of an `<i>` element
(`iconEl.className = plugin.icon || 'fas fa-puzzle-piece'` in
`web_interface/static/v3/js/app-shell.js` and the same fallback in
`app-early.js`). So it must be a Font Awesome class string. Emoji, image
paths and URLs are not supported: they would end up as a meaningless class
name and render nothing.

The web interface bundles Font Awesome Free 6
(`web_interface/static/v3/vendor/fontawesome/`), so any free `fas`, `far` or
`fab` icon works.

```json
{
  "id": "my-plugin",
  "name": "Weather Display",
  "icon": "fas fa-cloud-sun"
}
```

Some common choices:

- Clock / calendar: `fas fa-clock`, `fas fa-calendar-alt`
- Weather: `fas fa-cloud-sun`, `fas fa-cloud-rain`
- Sports: `fas fa-football-ball`, `fas fa-basketball-ball`, `fas fa-trophy`
- Music: `fas fa-music`, `fas fa-headphones`
- Finance: `fas fa-chart-line`, `fas fa-dollar-sign`
- News: `fas fa-newspaper`, `fas fa-rss`
- Games: `fas fa-gamepad`, `fas fa-dice`

Browse the rest in the [Font Awesome gallery](https://fontawesome.com/icons)
(filter to Free, version 6).

## Default

With no `icon` (or an empty one) the tab shows `fas fa-puzzle-piece`.

## Troubleshooting

1. Check the class name against the Font Awesome 6 Free gallery; a Pro-only
   or misspelled class renders as a blank space.
2. Include the style prefix (`fas`, `far` or `fab`) as well as the icon
   class.
3. See the status note above: the icon is currently not passed through by
   the API.

## Related Documentation

- [Plugin Configuration Tabs](PLUGIN_CONFIGURATION_TABS.md)
- [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md)
