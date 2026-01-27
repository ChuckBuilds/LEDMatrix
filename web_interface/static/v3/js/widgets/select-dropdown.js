/**
 * LEDMatrix Select Dropdown Widget
 *
 * Enhanced dropdown select with custom labels.
 *
 * Schema example:
 * {
 *   "theme": {
 *     "type": "string",
 *     "x-widget": "select-dropdown",
 *     "enum": ["light", "dark", "auto"],
 *     "x-options": {
 *       "placeholder": "Select a theme...",
 *       "labels": {
 *         "light": "Light Mode",
 *         "dark": "Dark Mode",
 *         "auto": "System Default"
 *       }
 *     }
 *   }
 * }
 *
 * @module SelectDropdownWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('SelectDropdown', '1.0.0') : null;

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

    window.LEDMatrixWidgets.register('select-dropdown', {
        name: 'Select Dropdown Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'select');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const enumValues = config.enum || xOptions.options || [];
            const placeholder = xOptions.placeholder || 'Select...';
            const labels = xOptions.labels || {};
            const icons = xOptions.icons || {};
            const disabled = xOptions.disabled === true;
            const required = xOptions.required === true;

            const currentValue = value !== null && value !== undefined ? String(value) : '';

            let html = `<div id="${fieldId}_widget" class="select-dropdown-widget" data-field-id="${fieldId}">`;

            html += `
                <select id="${fieldId}_input"
                        name="${options.name || fieldId}"
                        ${disabled ? 'disabled' : ''}
                        ${required ? 'required' : ''}
                        onchange="window.LEDMatrixWidgets.getHandlers('select-dropdown').onChange('${fieldId}')"
                        class="form-select w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 ${disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'} text-black">
            `;

            // Placeholder option
            if (placeholder && !required) {
                html += `<option value="" ${!currentValue ? 'selected' : ''}>${escapeHtml(placeholder)}</option>`;
            }

            // Options
            for (const optValue of enumValues) {
                const label = labels[optValue] || String(optValue).replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                const isSelected = String(optValue) === currentValue;
                html += `<option value="${escapeHtml(String(optValue))}" ${isSelected ? 'selected' : ''}>${escapeHtml(label)}</option>`;
            }

            html += '</select>';

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
            const input = document.getElementById(`${safeId}_input`);
            if (input) {
                input.value = value !== null && value !== undefined ? String(value) : '';
            }
        },

        handlers: {
            onChange: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('select-dropdown');
                triggerChange(fieldId, widget.getValue(fieldId));
            }
        }
    });

    console.log('[SelectDropdownWidget] Select dropdown widget registered');
})();
