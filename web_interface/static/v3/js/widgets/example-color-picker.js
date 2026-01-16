/**
 * Example: Color Picker Widget
 * 
 * This is an example custom widget demonstrating how to create
 * a plugin-specific widget for the LEDMatrix system.
 * 
 * To use this widget:
 * 1. Copy this file to your plugin's widgets directory
 * 2. Reference it in your config_schema.json with "x-widget": "color-picker"
 * 3. The widget will be automatically loaded when the plugin config form is rendered
 * 
 * @module ColorPickerWidget
 */

(function() {
    'use strict';

    // Ensure LEDMatrixWidgets registry exists
    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[ColorPickerWidget] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    /**
     * Register the color picker widget
     */
    window.LEDMatrixWidgets.register('color-picker', {
        name: 'Color Picker Widget',
        version: '1.0.0',
        
        /**
         * Render the color picker widget
         * @param {HTMLElement} container - Container element to render into
         * @param {Object} config - Widget configuration from schema
         * @param {string} value - Current color value (hex format)
         * @param {Object} options - Additional options
         */
        render: function(container, config, value, options) {
            const fieldId = options.fieldId || container.id.replace('_widget_container', '');
            let currentValue = value || config.default || '#000000';
            
            // Validate hex color format - use safe default if invalid
            const hexColorRegex = /^#[0-9A-Fa-f]{6}$/;
            if (!hexColorRegex.test(currentValue)) {
                currentValue = '#000000';
            }
            
            // Escape HTML to prevent XSS (for HTML contexts)
            const escapeHtml = (text) => {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            };
            
            // Use validated/sanitized hex for style attribute and input values
            const safeHex = currentValue; // Already validated above
            
            container.innerHTML = `
                <div class="color-picker-widget flex items-center space-x-3">
                    <div class="flex items-center space-x-2">
                        <label for="${escapeHtml(fieldId)}_color" class="text-sm text-gray-700">Color:</label>
                        <input type="color" 
                               id="${escapeHtml(fieldId)}_color" 
                               value="${safeHex}"
                               class="h-10 w-20 border border-gray-300 rounded cursor-pointer">
                    </div>
                    <div class="flex items-center space-x-2">
                        <label for="${escapeHtml(fieldId)}_hex" class="text-sm text-gray-700">Hex:</label>
                        <input type="text" 
                               id="${escapeHtml(fieldId)}_hex" 
                               value="${safeHex}"
                               pattern="^#[0-9A-Fa-f]{6}$"
                               maxlength="7"
                               class="px-3 py-2 border border-gray-300 rounded-md text-sm font-mono w-24"
                               placeholder="#000000">
                    </div>
                    <div class="flex-1">
                        <div id="${escapeHtml(fieldId)}_preview" 
                             class="h-10 w-full border border-gray-300 rounded"
                             style="background-color: ${safeHex}">
                        </div>
                    </div>
                </div>
                <p class="text-xs text-gray-500 mt-2">Select a color using the color picker or enter a hex code</p>
            `;
            
            // Get references to elements
            const colorInput = container.querySelector('input[type="color"]');
            const hexInput = container.querySelector('input[type="text"]');
            const preview = container.querySelector(`#${fieldId}_preview`);
            
            // Update hex when color picker changes
            colorInput.addEventListener('input', (e) => {
                const color = e.target.value;
                hexInput.value = color;
                if (preview) {
                    preview.style.backgroundColor = color;
                }
                this.handlers.onChange(fieldId, color);
            });
            
            // Update color picker and preview when hex input changes
            hexInput.addEventListener('input', (e) => {
                const hex = e.target.value;
                // Validate hex format
                if (/^#[0-9A-Fa-f]{6}$/.test(hex)) {
                    colorInput.value = hex;
                    if (preview) {
                        preview.style.backgroundColor = hex;
                    }
                    hexInput.classList.remove('border-red-500');
                    hexInput.classList.add('border-gray-300');
                    this.handlers.onChange(fieldId, hex);
                } else if (hex.length > 0) {
                    // Show error state for invalid hex
                    hexInput.classList.remove('border-gray-300');
                    hexInput.classList.add('border-red-500');
                }
            });
            
            // Validate on blur
            hexInput.addEventListener('blur', (e) => {
                const hex = e.target.value;
                if (hex && !/^#[0-9A-Fa-f]{6}$/.test(hex)) {
                    // Reset to current color picker value
                    e.target.value = colorInput.value;
                    e.target.classList.remove('border-red-500');
                    e.target.classList.add('border-gray-300');
                }
            });
        },
        
        /**
         * Get current value from widget
         * @param {string} fieldId - Field ID
         * @returns {string} Current hex color value
         */
        getValue: function(fieldId) {
            const colorInput = document.querySelector(`#${fieldId}_color`);
            return colorInput ? colorInput.value : null;
        },
        
        /**
         * Set value programmatically
         * @param {string} fieldId - Field ID
         * @param {string} value - Hex color value to set
         */
        setValue: function(fieldId, value) {
            // Validate hex color format before using
            const hexColorRegex = /^#[0-9A-Fa-f]{6}$/;
            const safeValue = hexColorRegex.test(value) ? value : '#000000';
            
            const colorInput = document.querySelector(`#${fieldId}_color`);
            const hexInput = document.querySelector(`#${fieldId}_hex`);
            const preview = document.querySelector(`#${fieldId}_preview`);
            
            if (colorInput && hexInput) {
                colorInput.value = safeValue;
                hexInput.value = safeValue;
                if (preview) {
                    preview.style.backgroundColor = safeValue;
                }
            }
        },
        
        /**
         * Event handlers
         */
        handlers: {
            /**
             * Handle color change
             * @param {string} fieldId - Field ID
             * @param {string} value - New color value
             */
            onChange: function(fieldId, value) {
                // Trigger form change event for validation and saving
                const event = new CustomEvent('widget-change', {
                    detail: { fieldId, value },
                    bubbles: true,
                    cancelable: true
                });
                document.dispatchEvent(event);
                
                // Also update any hidden input if it exists
                const hiddenInput = document.querySelector(`input[name*="${fieldId}"][type="hidden"]`);
                if (hiddenInput) {
                    hiddenInput.value = value;
                }
            }
        }
    });

    console.log('[ColorPickerWidget] Color picker widget registered (example)');
})();
