# Widget Development Guide

## Overview

The LEDMatrix Widget Registry system allows plugins to use reusable UI components for configuration forms. This enables:

- **Reusable Components**: Use existing widgets (file upload, checkboxes, etc.) without custom code
- **Custom Widgets**: Create plugin-specific widgets without modifying the LEDMatrix codebase
- **Backwards Compatibility**: Existing plugins continue to work without changes

## Available Core Widgets

### File Upload Widget (`file-upload`)

Upload and manage image files with drag-and-drop support, preview, delete, and scheduling.

**Schema Configuration:**
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

**Used by:** static-image, news plugins

### Checkbox Group Widget (`checkbox-group`)

Multi-select checkboxes for array fields with enum items.

**Schema Configuration:**
```json
{
  "type": "array",
  "x-widget": "checkbox-group",
  "items": {
    "type": "string",
    "enum": ["option1", "option2", "option3"]
  },
  "x-options": {
    "labels": {
      "option1": "Option 1 Label",
      "option2": "Option 2 Label"
    }
  }
}
```

**Used by:** odds-ticker, news plugins

### Custom Feeds Widget (`custom-feeds`)

Table-based RSS feed editor with logo uploads.

**Schema Configuration:**
```json
{
  "type": "array",
  "x-widget": "custom-feeds",
  "items": {
    "type": "object",
    "properties": {
      "name": { "type": "string" },
      "url": { "type": "string", "format": "uri" },
      "enabled": { "type": "boolean" },
      "logo": { "type": "object" }
    }
  },
  "maxItems": 50
}
```

**Used by:** news plugin (for custom RSS feeds)

## Using Existing Widgets

To use an existing widget in your plugin's `config_schema.json`, simply add the `x-widget` property:

```json
{
  "properties": {
    "my_images": {
      "type": "array",
      "x-widget": "file-upload",
      "x-upload-config": {
        "plugin_id": "my-plugin",
        "max_files": 5
      }
    },
    "enabled_leagues": {
      "type": "array",
      "x-widget": "checkbox-group",
      "items": {
        "type": "string",
        "enum": ["nfl", "nba", "mlb"]
      },
      "x-options": {
        "labels": {
          "nfl": "NFL",
          "nba": "NBA",
          "mlb": "MLB"
        }
      }
    }
  }
}
```

The widget will be automatically rendered when the plugin configuration form is loaded.

## Creating Custom Widgets

### Step 1: Create Widget File

Create a JavaScript file in your plugin directory. The recommended location is `widgets/[widget-name].js`:

```javascript
// Ensure LEDMatrixWidgets registry is available
if (typeof window.LEDMatrixWidgets === 'undefined') {
    console.error('LEDMatrixWidgets registry not found');
    return;
}

// Register your widget
window.LEDMatrixWidgets.register('my-custom-widget', {
    name: 'My Custom Widget',
    version: '1.0.0',
    
    /**
     * Render the widget HTML
     * @param {HTMLElement} container - Container element to render into
     * @param {Object} config - Widget configuration from schema
     * @param {*} value - Current value
     * @param {Object} options - Additional options (fieldId, pluginId, etc.)
     */
    render: function(container, config, value, options) {
        const fieldId = options.fieldId || container.id;
        
        // Always escape HTML to prevent XSS
        const escapeHtml = (text) => {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        };
        
        container.innerHTML = `
            <div class="my-custom-widget">
                <input type="text" 
                       id="${fieldId}_input" 
                       value="${escapeHtml(value || '')}"
                       class="w-full px-3 py-2 border border-gray-300 rounded">
            </div>
        `;
        
        // Attach event listeners
        const input = container.querySelector('input');
        input.addEventListener('change', (e) => {
            this.handlers.onChange(fieldId, e.target.value);
        });
    },
    
    /**
     * Get current value from widget
     */
    getValue: function(fieldId) {
        const input = document.querySelector(`#${fieldId}_input`);
        return input ? input.value : null;
    },
    
    /**
     * Set value programmatically
     */
    setValue: function(fieldId, value) {
        const input = document.querySelector(`#${fieldId}_input`);
        if (input) {
            input.value = value || '';
        }
    },
    
    /**
     * Event handlers
     */
    handlers: {
        onChange: function(fieldId, value) {
            // Trigger form change event
            const event = new CustomEvent('widget-change', {
                detail: { fieldId, value },
                bubbles: true
            });
            document.dispatchEvent(event);
        }
    }
});
```

### Step 2: Reference Widget in Schema

In your plugin's `config_schema.json`:

```json
{
  "properties": {
    "my_field": {
      "type": "string",
      "description": "My custom field",
      "x-widget": "my-custom-widget",
      "default": ""
    }
  }
}
```

### Step 3: Widget Loading

The widget will be automatically loaded when the plugin configuration form is rendered. The system will:

1. Check if widget is registered in the core registry
2. If not found, attempt to load from plugin directory: `/static/plugin-widgets/[plugin-id]/[widget-name].js`
3. Render the widget using the registered `render` function

**Note:** Currently, widgets are server-side rendered via Jinja2 templates. Custom widgets registered via the registry will have their handlers available, but full client-side rendering is a future enhancement.

## Widget API Reference

### Widget Definition Object

```javascript
{
    name: string,           // Human-readable widget name
    version: string,        // Widget version
    render: function,       // Required: Render function
    getValue: function,     // Optional: Get current value
    setValue: function,     // Optional: Set value programmatically
    handlers: object        // Optional: Event handlers
}
```

### Render Function

```javascript
render(container, config, value, options)
```

**Parameters:**
- `container` (HTMLElement): Container element to render into
- `config` (Object): Widget configuration from schema
- `value` (*): Current field value
- `options` (Object): Additional options
  - `fieldId` (string): Field ID
  - `pluginId` (string): Plugin ID
  - `fullKey` (string): Full field key path

### Get Value Function

```javascript
getValue(fieldId)
```

**Returns:** Current widget value

### Set Value Function

```javascript
setValue(fieldId, value)
```

**Parameters:**
- `fieldId` (string): Field ID
- `value` (*): Value to set

## Examples

See [`web_interface/static/v3/js/widgets/example-color-picker.js`](../web_interface/static/v3/js/widgets/example-color-picker.js) for a complete example of a custom color picker widget.

## Best Practices

### Security

1. **Always escape HTML**: Use `escapeHtml()` or `textContent` to prevent XSS
2. **Validate inputs**: Validate user input before processing
3. **Sanitize values**: Clean values before storing

### Performance

1. **Lazy loading**: Load widget scripts only when needed
2. **Event delegation**: Use event delegation for dynamic content
3. **Debounce**: Debounce frequent events (e.g., input changes)

### Accessibility

1. **Labels**: Always associate labels with inputs
2. **ARIA attributes**: Use appropriate ARIA attributes
3. **Keyboard navigation**: Ensure keyboard accessibility

## Troubleshooting

### Widget Not Loading

1. Check browser console for errors
2. Verify widget file path is correct
3. Ensure `LEDMatrixWidgets.register()` is called
4. Check that widget name matches schema `x-widget` value

### Widget Not Rendering

1. Verify `render` function is defined
2. Check container element exists
3. Ensure widget is registered before form loads
4. Check for JavaScript errors in console

### Value Not Saving

1. Ensure widget triggers `widget-change` event
2. Verify form submission includes widget value
3. Check `getValue` function returns correct type
4. Verify field name matches schema property

## Current Implementation Status

**Phase 1 Complete:**
- ✅ Widget registry system created
- ✅ Core widgets extracted to separate files
- ✅ Widget handlers available globally (backwards compatible)
- ✅ Plugin widget loading system implemented

**Current Behavior:**
- Widgets are server-side rendered via Jinja2 templates (existing behavior preserved)
- Widget handlers are registered and available globally
- Custom widgets can be created and registered
- Full client-side rendering is a future enhancement

**Backwards Compatibility:**
- All existing plugins using widgets continue to work without changes
- Server-side rendering remains the primary method
- Widget registry provides foundation for future enhancements

## See Also

- [Widget README](../web_interface/static/v3/js/widgets/README.md) - Complete widget development guide with examples
- [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md) - General plugin development
- [Plugin Configuration Guide](PLUGIN_CONFIGURATION_GUIDE.md) - Configuration setup
