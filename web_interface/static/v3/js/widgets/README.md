# LEDMatrix Widget Development Guide

## Overview

The LEDMatrix Widget Registry system allows plugins to use reusable UI components (widgets) for configuration forms. This system enables:

- **Reusable Components**: Use existing widgets (file upload, checkboxes, etc.) without custom code
- **Custom Widgets**: Create plugin-specific widgets without modifying the LEDMatrix codebase
- **Backwards Compatibility**: Existing plugins continue to work without changes

## Available Core Widgets

### 1. File Upload Widget (`file-upload`)

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

**Features:**
- Drag and drop file upload
- Image preview with thumbnails
- Delete functionality
- Schedule images to show at specific times
- Progress indicators during upload

### 2. Checkbox Group Widget (`checkbox-group`)

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

**Features:**
- Multiple selection from enum list
- Custom labels for each option
- Automatic JSON array serialization

### 3. Custom Feeds Widget (`custom-feeds`)

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

**Features:**
- Add/remove feed rows
- Logo upload per feed
- Enable/disable individual feeds
- Automatic row re-indexing

## Using Existing Widgets

To use an existing widget in your plugin's `config_schema.json`, simply add the `x-widget` property to your field definition:

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
    }
  }
}
```

The widget will be automatically rendered when the plugin configuration form is loaded.

## Creating Custom Widgets

### Step 1: Create Widget File

Create a JavaScript file in your plugin directory (e.g., `widgets/my-widget.js`):

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
        // Sanitize fieldId for safe use in DOM IDs and selectors
        const sanitizeId = (id) => String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
        const safeFieldId = sanitizeId(fieldId);
        
        const html = `
            <div class="my-custom-widget">
                <input type="text" 
                       id="${safeFieldId}_input" 
                       value="${this.escapeHtml(value || '')}"
                       class="w-full px-3 py-2 border border-gray-300 rounded">
            </div>
        `;
        container.innerHTML = html;
        
        // Attach event listeners
        const input = container.querySelector(`#${safeFieldId}_input`);
        if (input) {
            input.addEventListener('change', (e) => {
                this.handlers.onChange(fieldId, e.target.value);
            });
        }
    },
    
    /**
     * Get current value from widget
     * @param {string} fieldId - Field ID
     * @returns {*} Current value
     */
    getValue: function(fieldId) {
        // Sanitize fieldId for safe selector use
        const sanitizeId = (id) => String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
        const safeFieldId = sanitizeId(fieldId);
        const input = document.querySelector(`#${safeFieldId}_input`);
        return input ? input.value : null;
    },
    
    /**
     * Set value programmatically
     * @param {string} fieldId - Field ID
     * @param {*} value - Value to set
     */
    setValue: function(fieldId, value) {
        // Sanitize fieldId for safe selector use
        const sanitizeId = (id) => String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
        const safeFieldId = sanitizeId(fieldId);
        const input = document.querySelector(`#${safeFieldId}_input`);
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
    },
    
    /**
     * Helper: Escape HTML to prevent XSS
     */
    escapeHtml: function(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
    
    /**
     * Helper: Sanitize identifier for use in DOM IDs and CSS selectors
     */
    sanitizeId: function(id) {
        return String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
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
- `config` (Object): Widget configuration from schema (`x-widget-config` or schema properties)
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

### Event Handlers

Widgets can define custom event handlers in the `handlers` object:

```javascript
handlers: {
    onChange: function(fieldId, value) {
        // Handle value change
    },
    onFocus: function(fieldId) {
        // Handle focus
    }
}
```

## Best Practices

### Security

1. **Always escape HTML**: Use `escapeHtml()` or `textContent` to prevent XSS
2. **Validate inputs**: Validate user input before processing
3. **Sanitize values**: Clean values before storing
4. **Sanitize identifiers**: Always sanitize identifiers (like `fieldId`) used as element IDs and in CSS selectors to prevent selector injection/XSS:
   - Use `sanitizeId()` helper function (available in BaseWidget) or create your own
   - Allow only safe characters: `[A-Za-z0-9_-]`
   - Replace or remove invalid characters before using in:
     - `getElementById()`, `querySelector()`, `querySelectorAll()`
     - Setting `id` attributes
     - Building CSS selectors
   - Never interpolate raw `fieldId` into HTML strings or selectors without sanitization
   - Example: `const safeId = fieldId.replace(/[^a-zA-Z0-9_-]/g, '_');`

### Performance

1. **Lazy loading**: Load widget scripts only when needed
2. **Event delegation**: Use event delegation for dynamic content
3. **Debounce**: Debounce frequent events (e.g., input changes)

### Accessibility

1. **Labels**: Always associate labels with inputs
2. **ARIA attributes**: Use appropriate ARIA attributes
3. **Keyboard navigation**: Ensure keyboard accessibility

### Error Handling

1. **Graceful degradation**: Handle missing dependencies
2. **User feedback**: Show clear error messages
3. **Logging**: Log errors for debugging

## Examples

### Example 1: Color Picker Widget

```javascript
window.LEDMatrixWidgets.register('color-picker', {
    name: 'Color Picker',
    version: '1.0.0',
    
    render: function(container, config, value, options) {
        const fieldId = options.fieldId;
        // Sanitize fieldId for safe use in DOM IDs and selectors
        const sanitizeId = (id) => String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
        const sanitizedFieldId = sanitizeId(fieldId);
        
        container.innerHTML = `
            <div class="flex items-center space-x-2">
                <input type="color" 
                       id="${sanitizedFieldId}_color" 
                       value="${value || '#000000'}"
                       class="h-10 w-20">
                <input type="text" 
                       id="${sanitizedFieldId}_hex" 
                       value="${value || '#000000'}"
                       pattern="^#[0-9A-Fa-f]{6}$"
                       class="px-2 py-1 border rounded">
            </div>
        `;
        
        const colorInput = container.querySelector(`#${sanitizedFieldId}_color`);
        const hexInput = container.querySelector(`#${sanitizedFieldId}_hex`);
        
        if (colorInput && hexInput) {
            colorInput.addEventListener('change', (e) => {
                hexInput.value = e.target.value;
                this.handlers.onChange(fieldId, e.target.value);
            });
            
            hexInput.addEventListener('change', (e) => {
                if (/^#[0-9A-Fa-f]{6}$/.test(e.target.value)) {
                    colorInput.value = e.target.value;
                    this.handlers.onChange(fieldId, e.target.value);
                }
            });
        }
    },
    
    getValue: function(fieldId) {
        // Sanitize fieldId for safe selector use
        const sanitizeId = (id) => String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
        const sanitizedFieldId = sanitizeId(fieldId);
        const colorInput = document.querySelector(`#${sanitizedFieldId}_color`);
        return colorInput ? colorInput.value : null;
    },
    
    setValue: function(fieldId, value) {
        // Sanitize fieldId for safe selector use
        const sanitizeId = (id) => String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
        const sanitizedFieldId = sanitizeId(fieldId);
        const colorInput = document.querySelector(`#${sanitizedFieldId}_color`);
        const hexInput = document.querySelector(`#${sanitizedFieldId}_hex`);
        if (colorInput && hexInput) {
            colorInput.value = value;
            hexInput.value = value;
        }
    },
    
    handlers: {
        onChange: function(fieldId, value) {
            const event = new CustomEvent('widget-change', {
                detail: { fieldId, value },
                bubbles: true
            });
            document.dispatchEvent(event);
        }
    }
});
```

### Example 2: Slider Widget

```javascript
window.LEDMatrixWidgets.register('slider', {
    name: 'Slider Widget',
    version: '1.0.0',
    
    render: function(container, config, value, options) {
        const fieldId = options.fieldId;
        const min = config.minimum || 0;
        const max = config.maximum || 100;
        const step = config.step || 1;
        const currentValue = value !== undefined ? value : (config.default || min);
        
        container.innerHTML = `
            <div class="slider-widget">
                <input type="range" 
                       id="${fieldId}_slider"
                       min="${min}"
                       max="${max}"
                       step="${step}"
                       value="${currentValue}"
                       class="w-full">
                <div class="flex justify-between text-xs text-gray-500 mt-1">
                    <span>${min}</span>
                    <span id="${fieldId}_value">${currentValue}</span>
                    <span>${max}</span>
                </div>
            </div>
        `;
        
        const slider = container.querySelector('input[type="range"]');
        const valueDisplay = container.querySelector(`#${fieldId}_value`);
        
        slider.addEventListener('input', (e) => {
            valueDisplay.textContent = e.target.value;
            this.handlers.onChange(fieldId, parseFloat(e.target.value));
        });
    },
    
    getValue: function(fieldId) {
        const slider = document.querySelector(`#${fieldId}_slider`);
        return slider ? parseFloat(slider.value) : null;
    },
    
    setValue: function(fieldId, value) {
        const slider = document.querySelector(`#${fieldId}_slider`);
        const valueDisplay = document.querySelector(`#${fieldId}_value`);
        if (slider) {
            slider.value = value;
            if (valueDisplay) {
                valueDisplay.textContent = value;
            }
        }
    },
    
    handlers: {
        onChange: function(fieldId, value) {
            const event = new CustomEvent('widget-change', {
                detail: { fieldId, value },
                bubbles: true
            });
            document.dispatchEvent(event);
        }
    }
});
```

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

## Migration from Server-Side Rendering

Currently, widgets are server-side rendered via Jinja2 templates. The registry system provides:

1. **Backwards Compatibility**: Existing server-side rendered widgets continue to work
2. **Future Enhancement**: Client-side rendering support for custom widgets
3. **Handler Availability**: All widget handlers are available globally

Future versions may support full client-side rendering, but server-side rendering remains the primary method for core widgets.

## Support

For questions or issues:
- Check existing widget implementations for examples
- Review browser console for errors
- Test with simple widget first before complex implementations
