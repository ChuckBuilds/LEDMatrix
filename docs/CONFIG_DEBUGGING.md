# Configuration Debugging Guide

This guide helps troubleshoot configuration issues in LEDMatrix.

## Configuration Files

### Main Files

| File | Purpose |
|------|---------|
| `config/config.json` | Main configuration |
| `config/config_secrets.json` | API keys and sensitive data |
| `config/config.template.json` | Template for new installations |

### Plugin Configuration

Each plugin's configuration is a top-level key in `config.json`:

```json
{
  "football-scoreboard": {
    "enabled": true,
    "display_duration": 30,
    "nfl": {
      "enabled": true,
      "live_priority": false
    }
  },
  "odds-ticker": {
    "enabled": true,
    "display_duration": 15
  }
}
```

## Schema Validation

Plugins define their configuration schema in `config_schema.json`. This enables:
- Automatic default value population
- Configuration validation
- Web UI form generation

### Missing Schema Warning

If a plugin doesn't have `config_schema.json`, you'll see:

```
WARNING - Plugin 'my-plugin' has no config_schema.json - configuration will not be validated.
```

**Fix**: Add a `config_schema.json` to your plugin directory.

### Schema Example

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "enabled": {
      "type": "boolean",
      "default": true,
      "description": "Enable or disable this plugin"
    },
    "display_duration": {
      "type": "number",
      "default": 15,
      "minimum": 1,
      "description": "How long to display in seconds"
    },
    "api_key": {
      "type": "string",
      "description": "API key for data access"
    }
  },
  "required": ["api_key"]
}
```

## Common Configuration Issues

### 1. Type Mismatches

**Problem**: String value where number expected

```json
{
  "display_duration": "30"  // Wrong: string
}
```

**Fix**: Use correct types

```json
{
  "display_duration": 30    // Correct: number
}
```

**Logged Warning**:
```
WARNING - Config display_duration has invalid string value '30', using default 15.0
```

### 2. Missing Required Fields

**Problem**: Required field not in config

```json
{
  "football-scoreboard": {
    "enabled": true
    // Missing api_key which is required
  }
}
```

**Logged Error**:
```
ERROR - Plugin football-scoreboard configuration validation failed: 'api_key' is a required property
```

### 3. Invalid Nested Objects

**Problem**: Wrong structure for nested config

```json
{
  "football-scoreboard": {
    "nfl": "enabled"  // Wrong: should be object
  }
}
```

**Fix**: Use correct structure

```json
{
  "football-scoreboard": {
    "nfl": {
      "enabled": true
    }
  }
}
```

### 4. Invalid JSON Syntax

**Problem**: Malformed JSON

```json
{
  "plugin": {
    "enabled": true,  // Trailing comma
  }
}
```

**Fix**: Remove trailing commas, ensure valid JSON

```json
{
  "plugin": {
    "enabled": true
  }
}
```

**Tip**: Validate JSON at https://jsonlint.com/

## Debugging Configuration Loading

### Enable Debug Logging

Set environment variable:
```bash
export LEDMATRIX_DEBUG=1
python run.py
```

### Check Merged Configuration

The configuration is merged with schema defaults. To see the final merged config:

1. Enable debug logging
2. Look for log entries like:
   ```
   DEBUG - Merged config with schema defaults for football-scoreboard
   ```

### Configuration Load Order

1. Load `config.json`
2. Load `config_secrets.json`
3. Merge secrets into main config
4. For each plugin:
   - Load plugin's `config_schema.json`
   - Extract default values from schema
   - Merge user config with defaults
   - Validate merged config against schema

## Web Interface Issues

### Changes Not Saving

1. Check file permissions on `config/` directory
2. Check disk space
3. Look for errors in browser console
4. Check server logs for save errors

### Form Fields Not Appearing

1. Plugin may not have `config_schema.json`
2. Schema may have syntax errors
3. Check browser console for JavaScript errors

### Checkboxes Not Working

Boolean values from checkboxes should be actual booleans, not strings:

```json
{
  "enabled": true,     // Correct
  "enabled": "true"    // Wrong
}
```

## Config Key Collision Detection

LEDMatrix detects potential config key conflicts:

### Reserved Keys

These plugin IDs will trigger a warning:
- `display`, `schedule`, `timezone`, `plugin_system`
- `display_modes`, `system`, `hardware`, `debug`
- `log_level`, `emulator`, `web_interface`

**Warning**:
```
WARNING - Plugin ID 'display' conflicts with reserved config key.
```

### Case Collisions

Plugin IDs that differ only in case:
```
WARNING - Plugin ID 'Football-Scoreboard' may conflict with 'football-scoreboard' on case-insensitive file systems.
```

## Checking Configuration via API

```bash
# Get current config
curl http://localhost:5000/api/v3/config

# Get specific plugin config
curl http://localhost:5000/api/v3/config/plugin/football-scoreboard

# Validate config without saving
curl -X POST http://localhost:5000/api/v3/config/validate \
  -H "Content-Type: application/json" \
  -d '{"football-scoreboard": {"enabled": true}}'
```

## Backup and Recovery

### Manual Backup

```bash
cp config/config.json config/config.backup.json
```

### Automatic Backups

LEDMatrix creates backups before saves:
- Location: `config/backups/`
- Format: `config_YYYYMMDD_HHMMSS.json`

### Recovery

```bash
# List backups
ls -la config/backups/

# Restore from backup
cp config/backups/config_20240115_120000.json config/config.json
```

## Troubleshooting Checklist

- [ ] JSON syntax is valid (no trailing commas, quotes correct)
- [ ] Data types match schema (numbers are numbers, not strings)
- [ ] Required fields are present
- [ ] Nested objects have correct structure
- [ ] File permissions allow read/write
- [ ] No reserved config key collisions
- [ ] Plugin has `config_schema.json` for validation

## Getting Help

1. Check logs: `tail -f logs/ledmatrix.log`
2. Enable debug: `LEDMATRIX_DEBUG=1`
3. Check error dashboard: `/api/v3/errors/summary`
4. Validate JSON: https://jsonlint.com/
5. File an issue: https://github.com/ChuckBuilds/LEDMatrix/issues
