# Plugin Configuration Tabs - Architecture

> This page covers internals (how the config system works under the
> hood). For designing a plugin's config schema, the canonical guide is
> [PLUGIN_CONFIGURATION_GUIDE.md](PLUGIN_CONFIGURATION_GUIDE.md); for
> the user-facing tabs feature, see
> [PLUGIN_CONFIGURATION_TABS.md](PLUGIN_CONFIGURATION_TABS.md).

## System Architecture

### Component Overview

```
┌──────────────────────────────────────────────────────────────────┐
│ Web browser (templates/v3/base.html, Alpine.js + HTMX)            │
│                                                                    │
│  Second nav row: one tab per installed plugin                      │
│  Clicking a tab: GET /v3/partials/plugin-config/<plugin_id>        │
│  → server-rendered form swapped into the tab                       │
│                                                                    │
│  Save: hx-post="/api/v3/plugins/config?plugin_id=<id>" (form data) │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│ Flask (web_interface/app.py)                                       │
│                                                                    │
│  pages_v3 blueprint (blueprints/pages_v3.py)                       │
│   _load_plugin_config_partial(plugin_id)                           │
│    • SchemaManager.load_schema() → config_schema.json              │
│    • config.json section for the plugin                            │
│    • masks x-secret fields                                         │
│    • renders partials/plugin_config.html (render_field macros)     │
│                                                                    │
│  api_v3 blueprint (blueprints/api_v3/plugins.py)                   │
│   save_plugin_config()   POST /api/v3/plugins/config               │
│   get_plugin_config()    GET  /api/v3/plugins/config               │
│   get_plugin_schema()    GET  /api/v3/plugins/schema               │
│   reset_plugin_config()  POST /api/v3/plugins/config/reset         │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│ Files                                                              │
│  plugin-repos/<id>/config_schema.json   JSON Schema (Draft-7)      │
│  config/config.json                     { "<id>": { ... } }        │
│  config/config_secrets.json             { "<id>": { secrets } }    │
└──────────────────────────────────────────────────────────────────┘
```

The plugins directory is `plugin_system.plugins_directory` in
`config/config.json` (default `plugin-repos/`). Plugin configuration lives in
`config/config.json`, not in the plugin directory, so it survives reinstalls.

## Data Flow

### 1. Rendering a plugin's tab

```
User opens the plugin's tab
        │
        ▼
GET /v3/partials/plugin-config/<plugin_id>        (pages_v3)
        │
        ├─→ Load schema (SchemaManager, no cache)
        ├─→ Load config.json[<plugin_id>]
        ├─→ Mask "x-secret" values (fails closed if the schema is unusable)
        └─→ render partials/plugin_config.html
                │
                └─→ render_field() per property, recursively:
                      boolean → toggle, number/integer → input or slider,
                      string → input / textarea / select (enum),
                      array → list or table widget,
                      object → collapsible nested section,
                      "x-widget" → a registered widget
                               (static/v3/js/widgets/, or one the plugin ships)
```

Nested objects are supported: a nested field is posted with a dotted name
(e.g. `transition.type`).

### 2. Saving

```
User clicks Save
        │
        ▼
validatePluginConfigForm() (client-side checks)
        │
        ▼
POST /api/v3/plugins/config?plugin_id=<id>   (form data, all fields of the form)
        │
        ▼
save_plugin_config()                          (api_v3/plugins.py)
        ├─→ Start from the stored config.json[<id>]
        ├─→ Apply form fields: dotted names → nested keys, "[]" checkbox
        │   groups → lists, values coerced to the schema's types
        ├─→ Merge schema defaults for keys that are still missing
        ├─→ Validate against the schema (plus core per-plugin properties);
        │   invalid → 400 with the validation errors, nothing saved
        ├─→ Split "x-secret" fields out; masked/blank secrets are dropped so
        │   an untouched secret keeps its stored value
        ├─→ Deep-merge regular fields into config.json[<id>] (atomic save)
        ├─→ Merge secrets into config_secrets.json[<id>]
        └─→ Call the loaded plugin's on_config_change() (and
            on_enable/on_disable if "enabled" changed)
        │
        ▼
One response for the whole form → notification in the UI
```

The display service picks up the new config through its config hot reload
(ConfigService) without a restart.

JSON clients can post `{"plugin_id": ..., "config": {...}}` instead; the keys
sent are merged onto the stored config the same way. See
[REST_API_REFERENCE.md](REST_API_REFERENCE.md#save-plugin-configuration).

### 3. Reset

`POST /api/v3/plugins/config/reset` replaces the plugin's section with the
schema defaults (keeping secrets unless `preserve_secrets` is false).

## Key Design Decisions

### 1. Server-side rendered forms

**Why**: One renderer for every plugin, no per-plugin frontend code
**How**: Jinja macros in `partials/plugin_config.html` walk the schema
**Benefit**: The settings search index is built from the same rendered HTML
(`/v3/settings/search-index`)

### 2. JSON Schema as source of truth

**Why**: Standard, well-documented, validation-ready
**How**: The same schema drives the form, the defaults and server-side validation
**Benefit**: Plugin developers use a familiar format

### 3. Whole-form saves that merge

**Why**: A partial form (or a field the form doesn't show) must not wipe
stored values
**How**: The handler starts from the stored section and merges what was posted
**Benefit**: One request per save, atomic write

### 4. Secrets kept out of config.json

**Why**: `config.json` is shown in the raw editor and returned by the API
**How**: `"x-secret": true` fields go to `config_secrets.json`, which is
deep-merged back into the plugin's config at load time
**Benefit**: Plugins read secrets with plain `config.get(...)`

## Extension Points

### Custom input widgets

Set `"x-widget": "<name>"` on a property. Core widgets are in
`web_interface/static/v3/js/widgets/` (see its README); a plugin can ship its
own widget script, served from `/static/plugin-widgets/<plugin_id>/<name>.js`.
See [widget-guide.md](widget-guide.md).

### Custom actions

Buttons that run plugin scripts are declared in the manifest's
`web_ui_actions`. See [PLUGIN_WEB_UI_ACTIONS.md](PLUGIN_WEB_UI_ACTIONS.md).

### Reacting to changes

Implement `on_config_change(new_config)` in the plugin (see
[PLUGIN_API_REFERENCE.md](PLUGIN_API_REFERENCE.md)).

## Where to Look

| Concern | File |
|---------|------|
| Tab partial loader | `web_interface/blueprints/pages_v3.py` (`_load_plugin_config_partial`) |
| Form template and field macros | `web_interface/templates/v3/partials/plugin_config.html` |
| Save / get / schema / reset handlers | `web_interface/blueprints/api_v3/plugins.py` |
| Schema loading, defaults, validation | `src/plugin_system/schema_manager.py` |
| Secret masking and splitting | `src/web_interface/secret_helpers.py` |
| Widgets | `web_interface/static/v3/js/widgets/` |

## Error Handling

- Unknown plugin or unreadable schema: the partial renders an error message
- Validation failure: `400` with `details` and `context.validation_errors`;
  the form shows them and nothing is saved
- Save failure: `500` with an error message; config.json is written
  atomically, so a failed save leaves the previous file intact
