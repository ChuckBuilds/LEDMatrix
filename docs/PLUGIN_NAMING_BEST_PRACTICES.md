# Plugin Naming Best Practices

## Display Mode Naming Conflicts

### The Issue

With the plugin-first dispatch system, plugins are checked BEFORE legacy managers. This means if a plugin and a legacy manager use the same display mode name, **the plugin will take precedence** and the legacy manager will never be called.

### Example Conflict

**Before fix:**
```
Legacy Manager: src/clock.py          → mode "clock"
Plugin:         plugins/clock-simple/  → mode "clock"  ❌ CONFLICT!
```

When "clock" mode is active:
1. Plugin-first dispatch checks plugin_modes
2. Finds "clock" → calls clock-simple plugin
3. Legacy Clock manager never gets called ❌

### Naming Convention

To avoid conflicts, **plugins should use unique mode names** that include the plugin ID:

✅ **GOOD:**
```
Plugin ID: clock-simple
Display Mode: "clock-simple"
```

✅ **GOOD:**
```
Plugin ID: nhl-scores
Display Modes: ["nhl-scores", "nhl-live", "nhl-recent"]
```

❌ **BAD:**
```
Plugin ID: weather-animated
Display Mode: "weather"  ← Conflicts with legacy weather manager
```

❌ **BAD:**
```
Plugin ID: clock-advanced
Display Mode: "clock"  ← Conflicts with legacy clock manager
```

## Checking for Conflicts

### 1. Check Legacy Manager Modes

Current legacy manager display modes (as of 2025-01-09):
- `clock`
- `weather_current`, `weather_hourly`, `weather_daily`
- `stocks`
- `stock_news`
- `odds_ticker`
- `leaderboard`
- `calendar`
- `youtube`
- `text_display`
- `static_image`
- `of_the_day`
- `news_manager`
- `music`
- Sports modes: `nhl_live`, `nhl_recent`, `nhl_upcoming`, `nba_live`, `nba_recent`, etc.

**Rule:** Plugin modes should NOT use any of these exact names.

### 2. Use Plugin ID in Mode Name

**Best Practice:** Include the plugin ID in the display mode name:

```json
{
  "id": "my-plugin",
  "display_modes": ["my-plugin"]
}
```

Or for multiple modes:
```json
{
  "id": "hockey-advanced",
  "display_modes": [
    "hockey-advanced-live",
    "hockey-advanced-scores",
    "hockey-advanced-stats"
  ]
}
```

### 3. Check Before Publishing

Before publishing a plugin, search the codebase for conflicting mode names:

```bash
# Check if mode name is used in legacy code
grep -r "display_mode == 'your-mode-name'" src/
grep -r "available_modes.append('your-mode-name')" src/
```

## Migration Strategy

### When Migrating Legacy Manager to Plugin

When you migrate a legacy manager to a plugin, you have two choices:

#### Option 1: Keep the Same Mode Name (Replacement)

Use this when you want to **completely replace** the legacy manager:

```json
{
  "id": "clock-advanced",
  "display_modes": ["clock"]
}
```

Then:
1. Disable/remove legacy clock initialization
2. Plugin takes over "clock" mode
3. Users see seamless transition

#### Option 2: Use New Mode Name (Coexistence)

Use this when you want **both versions available**:

```json
{
  "id": "clock-advanced",
  "display_modes": ["clock-advanced"]
}
```

Then:
- Legacy "clock" still works
- Plugin "clock-advanced" available too
- Users can choose which to enable

## Current Plugins

### hello-world
- **ID:** `hello-world`
- **Modes:** `["hello-world"]`
- **Status:** ✅ No conflicts

### clock-simple
- **ID:** `clock-simple`
- **Modes:** `["clock-simple"]` (updated from `["clock"]`)
- **Status:** ✅ No conflicts (after fix)

## Checking Your Plugin

Before enabling a plugin, verify no conflicts exist:

### Step 1: Check Manifest
```bash
cat plugins/your-plugin/manifest.json | grep display_modes
```

### Step 2: Check Available Modes
```bash
# Run display controller and look for conflicts
python3 run.py 2>&1 | grep "Available display modes"
```

### Step 3: Test Rotation
```bash
# Watch mode switching
python3 run.py 2>&1 | grep -E "Showing|Switching to"
```

If you see both the legacy mode and plugin mode appearing separately, there's likely a conflict or misconfiguration.

## Web UI Considerations

The web UI should eventually:
1. Show which modes are plugins vs legacy
2. Warn about naming conflicts
3. Allow disabling conflicting modes
4. Show which manager/plugin handles each mode

## Future Improvements

### 1. Conflict Detection at Startup

Add to `display_controller.py`:
```python
def _check_mode_conflicts(self):
    """Warn about display mode naming conflicts."""
    legacy_modes = set(['clock', 'weather_current', ...])
    plugin_modes = set(self.plugin_modes.keys())
    
    conflicts = legacy_modes & plugin_modes
    if conflicts:
        logger.warning(f"Display mode conflicts detected: {conflicts}")
        logger.warning("Plugins will take precedence over legacy managers")
```

### 2. Mode Registry

Future enhancement: Create a mode registry that tracks:
- Mode name
- Handler (plugin ID or "legacy")
- Priority (plugin vs legacy)
- Conflicts

### 3. Plugin Metadata

Add to manifest.json:
```json
{
  "replaces": ["clock"],  // This plugin replaces legacy clock
  "conflicts_with": ["other-clock-plugin"]  // Known conflicts
}
```

## Summary

✅ **DO:**
- Use plugin ID in display mode names
- Check for legacy mode conflicts
- Document your plugin's display modes
- Use unique, descriptive mode names

❌ **DON'T:**
- Use generic mode names (e.g., "clock", "weather")
- Assume mode names are unique
- Mix legacy and plugin with same mode name unintentionally

## Quick Reference

```bash
# Check what modes are currently registered
python3 -c "
from src.display_controller import DisplayController
dc = DisplayController()
print('Available modes:', dc.available_modes)
print('Plugin modes:', list(dc.plugin_modes.keys()))
"
```

This will show you all active display modes and which are handled by plugins.

