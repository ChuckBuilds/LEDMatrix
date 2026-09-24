# LEDMatrix Widget Development Guide

Widgets are the controls the web UI draws for fields in a plugin's
`config_schema.json`. A field picks one with `"x-widget": "<name>"`. This
directory holds the built-in widgets, the registry they register with, and
the loader for widgets a plugin ships itself.

The page loads every file here as one bundle, `/assets/widgets.js`, built by
[`web_interface/widget_bundle.py`](../../../../widget_bundle.py) in the order
set by `BUNDLE_ORDER` there.

## Built-in widgets

| `x-widget` | Field type | What it draws |
|---|---|---|
| `text-input` | string | Text field with optional length limits |
| `textarea` | string | Multi-line text |
| `email-input` | string | Email field with format check |
| `url-input` | string | URL field with format check |
| `password-input` | string | Password field with show/hide toggle |
| `select-dropdown` | string | Dropdown for an `enum` |
| `radio-group` | string | Radio buttons for an `enum` |
| `date-picker` | string | Date input |
| `time-picker` | string | Time input, `HH:MM` (24-hour) |
| `color-picker` | string or array | Colour picker; hex string, or `[r, g, b]` on an array field |
| `font-selector` | string | Font from `assets/fonts/` (TTF and BDF), fetched from the API |
| `timezone-selector` | string | IANA timezone, grouped by region |
| `file-upload-single` | string | One image upload; stores the uploaded file's relative path |
| `google-oauth` | string | Step 2 of the calendar plugin's Google sign-in |
| `plugin-file-manager` | null | Inline file manager driven by the plugin's `web_ui_actions` |
| `json-file-manager` | null | JSON data-file manager driven by `web_ui_actions` |
| `toggle-switch` | boolean | On/off switch |
| `slider` | integer / number | Range slider using `minimum` / `maximum` |
| `number-input` | integer / number | Number field with min/max check |
| `file-upload` | array | Multi-image upload with preview, delete and scheduling |
| `checkbox-group` | array | Checkboxes for an array of `enum` items |
| `day-selector` | array | Days of the week |
| `custom-feeds` | array | RSS feed table with per-feed logo upload |
| `array-table` | array | Table editor for an array of objects |
| `google-calendar-picker` | array | Calendars from the user's Google account |
| `schedule-picker` | object | Enable toggle, global/per-day mode and times |
| `time-range` | object | Start and end time pair |
| `style-editor` | object | One row per display element: font, size, colour, alignment, offsets |

Other files here:

| File | Purpose |
|---|---|
| `registry.js` | `window.LEDMatrixWidgets`: `register()`, `get()` |
| `base-widget.js` | Shared helpers (`escapeHtml`, `sanitizeId`) other widgets use |
| `notification.js` | Toast notifications; owns `window.showNotification` |
| `plugin-order-list.js` | Drag-and-drop plugin order list used by the Display and Durations tabs (`window.PluginOrderList`) |
| `plugin-loader.js` | Loads a plugin-supplied widget on demand |
| `example-color-picker.js` | Example custom widget. Not bundled: it registers `color-picker` and would replace the real one |

Each widget file's header comment gives its schema options. The sections
below cover the ones that need more than a line.

### `file-upload`

```json
{
  "type": "array",
  "x-widget": "file-upload",
  "x-upload-config": {
    "plugin_id": "my-plugin",
    "max_files": 10,
    "max_size_mb": 5,
    "allowed_types": ["image/png", "image/jpeg", "image/bmp", "image/gif"]
  }
}
```

### `file-upload-single`

Uploads one image to the plugin's asset folder
(`assets/plugins/<plugin_id>/uploads/`) and stores the returned relative path
in a string field. `plugin_id` is filled in from the page; don't put it in
the schema. Use it for per-row images inside an `array-table`.

```json
{
  "image_path": {
    "type": "string",
    "x-widget": "file-upload-single",
    "x-upload-config": {
      "allowed_types": ["image/png", "image/jpeg", "image/bmp", "image/gif"],
      "max_size_mb": 5
    }
  }
}
```

### `checkbox-group`

```json
{
  "type": "array",
  "x-widget": "checkbox-group",
  "items": {"type": "string", "enum": ["option1", "option2", "option3"]},
  "x-options": {"labels": {"option1": "Option 1 Label", "option2": "Option 2 Label"}}
}
```

### `custom-feeds`

```json
{
  "type": "array",
  "x-widget": "custom-feeds",
  "items": {
    "type": "object",
    "properties": {
      "name": {"type": "string"},
      "url": {"type": "string", "format": "uri"},
      "enabled": {"type": "boolean"},
      "logo": {"type": "object"}
    }
  },
  "maxItems": 50
}
```

### `plugin-file-manager`

A card grid, upload zone, create/delete dialogs and a table editor for the
plugin's data files, rendered inline. File operations call
`/api/v3/plugins/action` as soon as the user acts; they are not part of
**Save Configuration**. `plugin_id` is filled in from the page.

```json
{
  "file_manager": {
    "type": "null",
    "title": "Data Files",
    "x-widget": "plugin-file-manager",
    "x-widget-config": {
      "actions": {
        "list": "list-files",
        "get": "get-file",
        "save": "save-file",
        "upload": "upload-file",
        "delete": "delete-file",
        "create": "create-file",
        "toggle": "toggle-category"
      },
      "upload_hint": "JSON files with day numbers 1–365 as keys",
      "directory_label": "my_data/",
      "create_fields": [
        {"key": "category_name", "label": "Category Name",
         "pattern": "^[a-z0-9_]+$", "hint": "Lowercase letters, numbers, underscores"},
        {"key": "display_name", "label": "Display Name", "hint": "Optional"}
      ]
    }
  }
}
```

The action ids refer to entries in the plugin's `web_ui_actions`
([docs/PLUGIN_WEB_UI_ACTIONS.md](../../../../../docs/PLUGIN_WEB_UI_ACTIONS.md)).
`list` is required: without it the widget stays on its loading state. Leave
out any other action to hide its control. The editor shows a table when a
file is an object of objects with the same keys, otherwise a JSON text area.

## Schema keywords the form understands

These work on any field, with or without a widget.

### Option labels: `x-options.labels`

A plain `enum` renders as a dropdown whose option text is the value with
underscores replaced and title case applied (`day_first` → "Day First").
`x-options.labels` sets the visible text instead:

```json
{
  "date_format": {
    "type": "string",
    "enum": ["abbrev", "numeric", "day_first"],
    "default": "abbrev",
    "x-options": {"labels": {"abbrev": "Sep 19", "numeric": "9/19", "day_first": "19 Sep"}}
  }
}
```

Labels are display only; the stored value is still the enum value. The map
may be partial. Older cores ignore `x-options` and show the fallback text.
`array-table` columns accept the same `x-options.labels`, but their fallback
is the raw value (so a ticker symbol `aapl` stays `aapl`).

### Advanced settings: `x-advanced`

`"x-advanced": true` on a top-level, non-object property moves it into a
collapsed **Advanced Settings** section at the bottom of the plugin's page.
Use it for settings most users never change (timeouts, cache TTLs, styling
overrides); keep anything needed to get the plugin working in the main form.
The settings search still finds and expands advanced fields. It is ignored
on `object` properties and by older cores.

### Hidden fields: `x-display: "hidden"`

`"x-display": "hidden"` keeps a property in the schema without drawing a
control, for a deprecated key that existing configs still carry or an
internal value such as a generated row id.

- Not rendered at any depth: top level, inside an object section, or as a
  column or row-editor field of an array of objects. Hidden fields are left
  out of Advanced Settings and the settings search.
- Saving the form never changes a hidden value. Array rows carry it through;
  a new row gets no value.
- A JSON `POST /api/v3/plugins/config` can still set it.
- Older cores ignore the flag and render the field.

## Creating a custom widget

### 1. Write the widget

Put it in your plugin's `widgets/` directory as `widgets/<name>.js`. That
directory is the only place the core serves plugin widgets from.

```javascript
(function () {
    'use strict';
    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('LEDMatrixWidgets registry not found');
        return;
    }

    const sanitizeId = (id) => String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
    // The page's shared escaper covers HTML content and quoted attribute
    // values. A textContent/innerHTML round trip leaves quotes alone, so it
    // is not safe inside value="...".
    const escapeHtml = (text) => window.LEDEscape.html(text);

    window.LEDMatrixWidgets.register('my-custom-widget', {
        name: 'My Custom Widget',
        version: '1.0.0',

        render: function (container, config, value, options) {
            const fieldId = options.fieldId || container.id;
            const safeId = sanitizeId(fieldId);
            container.innerHTML = `
                <input type="text" id="${safeId}_input"
                       value="${escapeHtml(value || '')}"
                       class="w-full px-3 py-2 border border-gray-300 rounded">`;
            const input = container.querySelector(`#${safeId}_input`);
            input.addEventListener('change', (e) => {
                this.handlers.onChange(fieldId, e.target.value);
            });
        },

        getValue: function (fieldId) {
            const input = document.querySelector(`#${sanitizeId(fieldId)}_input`);
            return input ? input.value : null;
        },

        setValue: function (fieldId, value) {
            const input = document.querySelector(`#${sanitizeId(fieldId)}_input`);
            if (input) input.value = value || '';
        },

        handlers: {
            onChange: function (fieldId, value) {
                document.dispatchEvent(new CustomEvent('widget-change', {
                    detail: { fieldId, value }, bubbles: true
                }));
            }
        }
    });
})();
```

[`example-color-picker.js`](example-color-picker.js) is a longer example.

### 2. Reference it in the schema

```json
{
  "properties": {
    "my_field": {"type": "string", "x-widget": "my-custom-widget", "default": ""}
  }
}
```

### 3. Declare it in `manifest.json`

The manifest is the allowlist: a widget is served only if the plugin
declares it.

```json
{
  "widgets": [
    {"name": "my-custom-widget", "script": "my-custom-widget.js",
     "description": "What this widget is for"}
  ]
}
```

`name` is what `x-widget` uses. `script` is optional, defaults to
`<name>.js`, and must be a plain filename directly inside `widgets/`. Both
are validated against `schema/manifest_schema.json`.

### 4. How it loads

When the config form reaches a field whose `x-widget` is not a built-in:

1. If the name is already registered, that widget renders the field.
2. Otherwise the page fetches `/static/plugin-widgets/<plugin-id>/<name>.js`
   (`serve_plugin_widget` in
   [`web_interface/blueprints/pages_v3.py`](../../../../blueprints/pages_v3.py)),
   which serves the declared script from the plugin's `widgets/` directory.
3. The widget's `render()` draws the field.

The fetch is a dynamic `import()`, so the file must parse as an ES module.
An IIFE does; modules are strict mode, and a `return` outside a function is a
syntax error.

If the widget fails to load (not declared, file missing, script throws, or
it never calls `register`), the field falls back to a plain text input
holding the current value, so a broken widget never costs the user their
setting.

**Limitation:** only `string` fields without an `enum` take this path.
[`plugin_config.html`](../../../../templates/v3/partials/plugin_config.html)
renders `object`, `array`, `boolean`, `integer`, `number` and `enum` fields
with its own branches, which only know the built-in names, so a plugin's own
widget on one of those is ignored.

## Widget API

```javascript
{
    name: string,        // human-readable name
    version: string,
    render: function,    // required: render(container, config, value, options)
    getValue: function,  // optional: getValue(fieldId) -> value
    setValue: function,  // optional: setValue(fieldId, value)
    handlers: object     // optional: e.g. onChange(fieldId, value)
}
```

`render()` arguments:

- `container` — element to render into
- `config` — the field's schema, including `x-widget-config` / `x-options`
- `value` — current value
- `options` — `fieldId`, `pluginId`, `fullKey` (dotted path of the field)

## Guidelines

- Escape values before putting them in HTML (`textContent` or an
  `escapeHtml` helper).
- Sanitise `fieldId` before using it in an `id`, `getElementById()` or a CSS
  selector: allow only `[A-Za-z0-9_-]`. `BaseWidget` has `sanitizeId()`.
- Associate labels with inputs and keep the widget usable from the keyboard.
- Debounce events that fire on every keystroke.

## Troubleshooting

**Widget not loading**
- Check the browser console.
- The widget must be declared in `manifest.json` and live in `widgets/`.
- The name passed to `register()` must match `x-widget`.
- The field must be a non-enum `string` (see the limitation above).

**Value not saving**
- Fire a `widget-change` event on change.
- `getValue()` must return the type the schema expects.
- Check the field name matches the schema property.
