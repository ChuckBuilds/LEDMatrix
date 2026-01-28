/**
 * LEDMatrix Color Picker Widget
 *
 * Color selection with preview and hex/RGB input.
 *
 * Schema example:
 * {
 *   "backgroundColor": {
 *     "type": "string",
 *     "x-widget": "color-picker",
 *     "x-options": {
 *       "showHexInput": true,
 *       "showPreview": true,
 *       "presets": ["#ff0000", "#00ff00", "#0000ff", "#ffffff", "#000000"],
 *       "format": "hex"  // "hex", "rgb", "rgba"
 *     }
 *   }
 * }
 *
 * @module ColorPickerWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('ColorPicker', '1.0.0') : null;

    function escapeHtml(text) {
        if (base) return base.escapeHtml(text);
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function sanitizeId(id) {
        if (base) return base.sanitizeId(id);
        return String(id).replace(/[^a-zA-Z0-9_-]/g, '_');
    }

    function triggerChange(fieldId, value) {
        if (base) {
            base.triggerChange(fieldId, value);
        } else {
            const event = new CustomEvent('widget-change', {
                detail: { fieldId, value },
                bubbles: true,
                cancelable: true
            });
            document.dispatchEvent(event);
        }
    }

    function isValidHex(hex) {
        return /^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$/.test(hex);
    }

    function normalizeHex(hex) {
        if (!hex) return '#000000';
        hex = String(hex).trim();
        if (!hex.startsWith('#')) hex = '#' + hex;
        // Expand 3-digit hex
        if (hex.length === 4) {
            hex = '#' + hex[1] + hex[1] + hex[2] + hex[2] + hex[3] + hex[3];
        }
        return hex.toLowerCase();
    }

    /**
     * Sanitize and validate a hex color, returning a safe 7-char #rrggbb string.
     * Falls back to #000000 for any invalid input.
     */
    function sanitizeHex(value) {
        const normalized = normalizeHex(value);
        // Validate it's exactly #rrggbb format with valid hex chars
        if (/^#[0-9a-f]{6}$/.test(normalized)) {
            return normalized;
        }
        return '#000000';
    }

    const DEFAULT_PRESETS = [
        '#000000', '#ffffff', '#ff0000', '#00ff00', '#0000ff',
        '#ffff00', '#00ffff', '#ff00ff', '#808080', '#ffa500'
    ];

    window.LEDMatrixWidgets.register('color-picker', {
        name: 'Color Picker Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'color_picker');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const showHexInput = xOptions.showHexInput !== false;
            const showPreview = xOptions.showPreview !== false;
            // Ensure presets is always an array to prevent crashes on .map()
            const presets = Array.isArray(xOptions.presets) ? xOptions.presets : DEFAULT_PRESETS;
            const disabled = xOptions.disabled === true;

            const currentValue = sanitizeHex(value);

            let html = `<div id="${fieldId}_widget" class="color-picker-widget" data-field-id="${fieldId}">`;

            // Main color picker row
            html += '<div class="flex items-center gap-3">';

            // Native color input
            html += `
                <input type="color"
                       id="${fieldId}_color"
                       value="${currentValue}"
                       ${disabled ? 'disabled' : ''}
                       onchange="window.LEDMatrixWidgets.getHandlers('color-picker').onColorChange('${fieldId}')"
                       class="w-12 h-10 rounded cursor-pointer border border-gray-300 ${disabled ? 'opacity-50 cursor-not-allowed' : ''}">
            `;

            // Hex input
            if (showHexInput) {
                html += `
                    <div class="flex items-center">
                        <span class="text-gray-400 mr-1">#</span>
                        <input type="text"
                               id="${fieldId}_hex"
                               value="${currentValue.substring(1)}"
                               maxlength="6"
                               ${disabled ? 'disabled' : ''}
                               onchange="window.LEDMatrixWidgets.getHandlers('color-picker').onHexChange('${fieldId}')"
                               oninput="window.LEDMatrixWidgets.getHandlers('color-picker').onHexInput('${fieldId}')"
                               class="w-20 px-2 py-1 text-sm font-mono rounded border border-gray-300 focus:border-blue-500 focus:ring-blue-500 ${disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'} text-black uppercase">
                    </div>
                `;
            }

            // Preview box
            if (showPreview) {
                html += `
                    <div id="${fieldId}_preview"
                         class="w-20 h-10 rounded border border-gray-300 shadow-inner"
                         style="background-color: ${currentValue};">
                    </div>
                `;
            }

            html += '</div>';

            // Hidden input for form submission
            html += `<input type="hidden" id="${fieldId}_input" name="${escapeHtml(options.name || fieldId)}" value="${currentValue}">`;

            // Preset colors - only render valid hex colors
            if (Array.isArray(presets) && presets.length > 0) {
                const validPresets = (Array.isArray(presets) ? presets : []).map(p => normalizeHex(p)).filter(p => isValidHex(p));
                if (validPresets.length > 0) {
                    html += `
                        <div class="flex flex-wrap gap-1 mt-3">
                            <span class="text-xs text-gray-400 w-full mb-1">Quick colors:</span>
                    `;
                    for (const normalized of validPresets) {
                        html += `
                            <button type="button"
                                    ${disabled ? 'disabled' : ''}
                                    data-color="${escapeHtml(normalized)}"
                                    onclick="window.LEDMatrixWidgets.getHandlers('color-picker').onPresetClick('${fieldId}', this.dataset.color)"
                                    class="w-6 h-6 rounded border border-gray-300 hover:scale-110 transition-transform ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}"
                                    style="background-color: ${escapeHtml(normalized)};"
                                    title="${escapeHtml(normalized)}">
                            </button>
                        `;
                    }
                    html += '</div>';
                }
            }

            // Error message area
            html += `<div id="${fieldId}_error" class="text-sm text-red-600 mt-1 hidden"></div>`;

            html += '</div>';

            container.innerHTML = html;
        },

        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            return input ? input.value : '';
        },

        setValue: function(fieldId, value) {
            const safeId = sanitizeId(fieldId);
            const sanitized = sanitizeHex(value);

            const colorInput = document.getElementById(`${safeId}_color`);
            const hexInput = document.getElementById(`${safeId}_hex`);
            const preview = document.getElementById(`${safeId}_preview`);
            const hidden = document.getElementById(`${safeId}_input`);

            if (colorInput) colorInput.value = sanitized;
            if (hexInput) hexInput.value = sanitized.substring(1);
            if (preview) preview.style.backgroundColor = sanitized;
            if (hidden) hidden.value = sanitized;
        },

        handlers: {
            onColorChange: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const colorInput = document.getElementById(`${safeId}_color`);
                const value = sanitizeHex(colorInput?.value);

                const widget = window.LEDMatrixWidgets.get('color-picker');
                widget.setValue(fieldId, value);
                triggerChange(fieldId, value);
            },

            onHexChange: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const hexInput = document.getElementById(`${safeId}_hex`);
                const errorEl = document.getElementById(`${safeId}_error`);

                const rawValue = '#' + (hexInput?.value || '000000');
                const normalized = normalizeHex(rawValue);

                if (!isValidHex(normalized)) {
                    if (errorEl) {
                        errorEl.textContent = 'Invalid hex color';
                        errorEl.classList.remove('hidden');
                    }
                    return;
                }

                if (errorEl) {
                    errorEl.classList.add('hidden');
                }

                // Use sanitized value for setting
                const sanitized = sanitizeHex(normalized);
                const widget = window.LEDMatrixWidgets.get('color-picker');
                widget.setValue(fieldId, sanitized);
                triggerChange(fieldId, sanitized);
            },

            onHexInput: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const hexInput = document.getElementById(`${safeId}_hex`);

                if (hexInput) {
                    // Filter to only valid hex characters
                    hexInput.value = hexInput.value.replace(/[^0-9A-Fa-f]/g, '').toUpperCase();
                }
            },

            onPresetClick: function(fieldId, color) {
                const sanitized = sanitizeHex(color);
                const widget = window.LEDMatrixWidgets.get('color-picker');
                widget.setValue(fieldId, sanitized);
                triggerChange(fieldId, sanitized);
            }
        }
    });

    console.log('[ColorPickerWidget] Color picker widget registered');
})();
