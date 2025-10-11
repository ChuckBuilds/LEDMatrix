# Plugin Configuration Tabs - Architecture

## System Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Web Browser                              │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Tab Navigation Bar                     │   │
│  │  [Overview] [General] ... [Plugins] [Plugin X] [Plugin Y]│   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─────────────────┐  ┌──────────────────────────────────┐     │
│  │  Plugins Tab    │  │   Plugin X Configuration Tab     │     │
│  │                 │  │                                    │     │
│  │ • Install       │  │  Form Generated from Schema:      │     │
│  │ • Update        │  │  • Boolean → Toggle               │     │
│  │ • Uninstall     │  │  • Number → Number Input          │     │
│  │ • Enable        │  │  • String → Text Input            │     │
│  │ • [Configure]──────→  • Array → Comma Input           │     │
│  │                 │  │  • Enum → Dropdown                │     │
│  └─────────────────┘  │                                    │     │
│                        │  [Save] [Back] [Reset]            │     │
│                        └──────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ HTTP API
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Flask Backend                               │
│  ┌───────────────────────────────────────────────────────┐     │
│  │         /api/plugins/installed                         │     │
│  │  • Discover plugins in plugins/ directory              │     │
│  │  • Load manifest.json for each plugin                  │     │
│  │  • Load config_schema.json if exists                   │     │
│  │  • Load current config from config.json                │     │
│  │  • Return combined data to frontend                    │     │
│  └───────────────────────────────────────────────────────┘     │
│                                                                   │
│  ┌───────────────────────────────────────────────────────┐     │
│  │         /api/plugins/config                            │     │
│  │  • Receive key-value pair                              │     │
│  │  • Update config.json                                  │     │
│  │  • Return success/error                                │     │
│  └───────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ File System
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      File System                                 │
│                                                                   │
│  plugins/                                                         │
│  ├── hello-world/                                                │
│  │   ├── manifest.json ───┐                                     │
│  │   ├── config_schema.json ─┼─→ Defines UI structure           │
│  │   ├── manager.py          │                                   │
│  │   └── requirements.txt    │                                   │
│  └── clock-simple/            │                                   │
│      ├── manifest.json        │                                   │
│      └── config_schema.json ──┘                                  │
│                                                                   │
│  config/                                                          │
│  └── config.json ────────────→ Stores configuration values       │
│      {                                                            │
│        "hello-world": {                                           │
│          "enabled": true,                                         │
│          "message": "Hello!",                                     │
│          ...                                                      │
│        }                                                          │
│      }                                                            │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Page Load Sequence

```
User Opens Web Interface
         │
         ▼
DOMContentLoaded Event
         │
         ▼
refreshPlugins()
         │
         ▼
GET /api/plugins/installed
         │
         ├─→ For each plugin directory:
         │   ├─→ Read manifest.json
         │   ├─→ Read config_schema.json (if exists)
         │   └─→ Read config from config.json
         │
         ▼
Return JSON Array:
[{
  id: "hello-world",
  name: "Hello World",
  config: { enabled: true, message: "Hello!" },
  config_schema_data: {
    properties: {
      enabled: { type: "boolean", ... },
      message: { type: "string", ... }
    }
  }
}, ...]
         │
         ▼
generatePluginTabs(plugins)
         │
         ├─→ For each plugin:
         │   ├─→ Create tab button
         │   ├─→ Create tab content div
         │   └─→ generatePluginConfigForm(plugin)
         │           │
         │           ├─→ Read schema properties
         │           ├─→ Get current config values
         │           └─→ Generate HTML form inputs
         │
         ▼
Tabs Rendered in UI
```

### 2. Configuration Save Sequence

```
User Modifies Form
         │
         ▼
User Clicks "Save"
         │
         ▼
savePluginConfiguration(pluginId)
         │
         ├─→ Get form data
         ├─→ For each field:
         │   ├─→ Get schema type
         │   ├─→ Convert value to correct type
         │   │   • boolean: checkbox.checked
         │   │   • integer: parseInt()
         │   │   • number: parseFloat()
         │   │   • array: split(',')
         │   │   • string: as-is
         │   │
         │   └─→ POST /api/plugins/config
         │       {
         │         plugin_id: "hello-world",
         │         key: "message",
         │         value: "Hello, World!"
         │       }
         │
         ▼
Backend Updates config.json
         │
         ▼
Return Success
         │
         ▼
Show Notification
         │
         ▼
Refresh Plugins
```

## Class and Function Hierarchy

### Frontend (JavaScript)

```
Window Load
  └── DOMContentLoaded
      └── refreshPlugins()
          ├── fetch('/api/plugins/installed')
          ├── renderInstalledPlugins(plugins)
          └── generatePluginTabs(plugins)
              └── For each plugin:
                  ├── Create tab button
                  ├── Create tab content
                  └── generatePluginConfigForm(plugin)
                      ├── Read config_schema_data
                      ├── Read current config
                      └── Generate form HTML
                          ├── Boolean → Toggle switch
                          ├── Number → Number input
                          ├── String → Text input
                          ├── Array → Comma-separated input
                          └── Enum → Select dropdown

User Interactions
  ├── configurePlugin(pluginId)
  │   └── showTab(`plugin-${pluginId}`)
  │
  ├── savePluginConfiguration(pluginId)
  │   ├── Process form data
  │   ├── Convert types per schema
  │   └── For each field:
  │       └── POST /api/plugins/config
  │
  └── resetPluginConfig(pluginId)
      ├── Get schema defaults
      └── For each field:
          └── POST /api/plugins/config
```

### Backend (Python)

```
Flask Routes
  ├── /api/plugins/installed (GET)
  │   └── api_plugins_installed()
  │       ├── PluginManager.discover_plugins()
  │       ├── For each plugin:
  │       │   ├── PluginManager.get_plugin_info()
  │       │   ├── Load config_schema.json
  │       │   └── Load config from config.json
  │       └── Return JSON response
  │
  └── /api/plugins/config (POST)
      └── api_plugin_config()
          ├── Parse request JSON
          ├── Load current config
          ├── Update config[plugin_id][key] = value
          └── Save config.json
```

## File Structure

```
LEDMatrix/
│
├── web_interface_v2.py
│   └── Flask backend with plugin API endpoints
│
├── templates/
│   └── index_v2.html
│       └── Frontend with dynamic tab generation
│
├── config/
│   └── config.json
│       └── Stores all plugin configurations
│
├── plugins/
│   ├── hello-world/
│   │   ├── manifest.json           ← Plugin metadata
│   │   ├── config_schema.json      ← UI schema definition
│   │   ├── manager.py              ← Plugin logic
│   │   └── requirements.txt
│   │
│   └── clock-simple/
│       ├── manifest.json
│       ├── config_schema.json
│       └── manager.py
│
└── docs/
    ├── PLUGIN_CONFIGURATION_TABS.md       ← Full documentation
    ├── PLUGIN_CONFIG_TABS_SUMMARY.md      ← Implementation summary
    ├── PLUGIN_CONFIG_QUICK_START.md       ← Quick start guide
    └── PLUGIN_CONFIG_ARCHITECTURE.md      ← This file
```

## Key Design Decisions

### 1. Dynamic Tab Generation

**Why**: Plugins are installed/uninstalled dynamically  
**How**: JavaScript creates/removes tab elements on plugin list refresh  
**Benefit**: No server-side template rendering needed

### 2. JSON Schema as Source of Truth

**Why**: Standard, well-documented, validation-ready  
**How**: Frontend interprets schema to generate forms  
**Benefit**: Plugin developers use familiar format

### 3. Individual Config Updates

**Why**: Simplifies backend API  
**How**: Each field saved separately via `/api/plugins/config`  
**Benefit**: Atomic updates, easier error handling

### 4. Type Conversion in Frontend

**Why**: HTML forms only return strings  
**How**: JavaScript converts based on schema type before sending  
**Benefit**: Backend receives correctly-typed values

### 5. No Nested Objects

**Why**: Keeps UI simple  
**How**: Only flat property structures supported  
**Benefit**: Easy form generation, clear to users

## Extension Points

### Adding New Input Types

Location: `generatePluginConfigForm()` in `index_v2.html`

```javascript
if (type === 'your-new-type') {
    formHTML += `
        <!-- Your custom input HTML -->
    `;
}
```

### Custom Validation

Location: `savePluginConfiguration()` in `index_v2.html`

```javascript
// Add validation before sending
if (!validateCustomConstraint(value, propSchema)) {
    throw new Error('Validation failed');
}
```

### Backend Hook

Location: `api_plugin_config()` in `web_interface_v2.py`

```python
# Add custom logic before saving
if plugin_id == 'special-plugin':
    value = transform_value(value)
```

## Performance Considerations

### Frontend

- **Tab Generation**: O(n) where n = number of plugins (typically < 20)
- **Form Generation**: O(m) where m = number of config properties (typically < 10)
- **Memory**: Each plugin tab ~5KB HTML
- **Total Impact**: Negligible for typical use cases

### Backend

- **Schema Loading**: Cached after first load
- **Config Updates**: Single file write (atomic)
- **API Calls**: One per config field on save (sequential)
- **Optimization**: Could batch updates in single API call

## Security Considerations

1. **Input Validation**: Schema constraints enforced client-side (UX) and should be enforced server-side
2. **Path Traversal**: Plugin paths validated against known plugin directory
3. **XSS**: All user inputs escaped before rendering in HTML
4. **CSRF**: Flask CSRF tokens should be used in production
5. **File Permissions**: config.json requires write access

## Error Handling

### Frontend

- Network errors: Show notification, don't crash
- Schema errors: Graceful fallback to no config tab
- Type errors: Log to console, continue processing other fields

### Backend

- Invalid plugin_id: 400 Bad Request
- Schema not found: Return null, frontend handles gracefully
- Config save error: 500 Internal Server Error with message

## Testing Strategy

### Unit Tests

- `generatePluginConfigForm()` for each schema type
- Type conversion logic in `savePluginConfiguration()`
- Backend schema loading logic

### Integration Tests

- Full save flow: form → API → config.json
- Tab generation from API response
- Reset to defaults

### E2E Tests

- Install plugin → verify tab appears
- Configure plugin → verify config saved
- Uninstall plugin → verify tab removed

## Monitoring

### Frontend Metrics

- Time to generate tabs
- Form submission success rate
- User interactions (configure, save, reset)

### Backend Metrics

- API response times
- Config update success rate
- Schema loading errors

### User Feedback

- Are users finding the configuration interface?
- Are validation errors clear?
- Are default values sensible?

## Future Roadmap

### Phase 2: Enhanced Validation
- Real-time validation feedback
- Custom error messages
- Dependent field validation

### Phase 3: Advanced Inputs
- Color pickers for RGB arrays
- File upload for assets
- Rich text editor for descriptions

### Phase 4: Configuration Management
- Export/import configurations
- Configuration presets
- Version history/rollback

### Phase 5: Developer Tools
- Schema editor in web UI
- Live preview while editing schema
- Validation tester

