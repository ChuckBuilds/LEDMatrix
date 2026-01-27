/**
 * LEDMatrix Date Picker Widget
 *
 * Date selection with optional min/max constraints.
 *
 * Schema example:
 * {
 *   "startDate": {
 *     "type": "string",
 *     "format": "date",
 *     "x-widget": "date-picker",
 *     "x-options": {
 *       "min": "2024-01-01",
 *       "max": "2025-12-31",
 *       "placeholder": "Select date",
 *       "clearable": true
 *     }
 *   }
 * }
 *
 * @module DatePickerWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('DatePicker', '1.0.0') : null;

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

    window.LEDMatrixWidgets.register('date-picker', {
        name: 'Date Picker Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'date_picker');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const min = xOptions.min || config.minimum || '';
            const max = xOptions.max || config.maximum || '';
            const placeholder = xOptions.placeholder || '';
            const clearable = xOptions.clearable === true;
            const disabled = xOptions.disabled === true;
            const required = xOptions.required === true;

            const currentValue = value || '';

            let html = `<div id="${fieldId}_widget" class="date-picker-widget" data-field-id="${fieldId}">`;

            html += '<div class="flex items-center">';

            html += `
                <div class="relative flex-1">
                    <input type="date"
                           id="${fieldId}_input"
                           name="${escapeHtml(options.name || fieldId)}"
                           value="${escapeHtml(currentValue)}"
                           ${min ? `min="${escapeHtml(min)}"` : ''}
                           ${max ? `max="${escapeHtml(max)}"` : ''}
                           ${placeholder ? `placeholder="${escapeHtml(placeholder)}"` : ''}
                           ${disabled ? 'disabled' : ''}
                           ${required ? 'required' : ''}
                           onchange="window.LEDMatrixWidgets.getHandlers('date-picker').onChange('${fieldId}')"
                           class="form-input w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 ${disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'} text-black pr-10">
                    <div class="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none">
                        <i class="fas fa-calendar-alt text-gray-400"></i>
                    </div>
                </div>
            `;

            if (clearable && !disabled) {
                html += `
                    <button type="button"
                            id="${fieldId}_clear"
                            onclick="window.LEDMatrixWidgets.getHandlers('date-picker').onClear('${fieldId}')"
                            class="ml-2 inline-flex items-center px-2 py-2 text-gray-400 hover:text-gray-600 ${currentValue ? '' : 'hidden'}"
                            title="Clear">
                        <i class="fas fa-times"></i>
                    </button>
                `;
            }

            html += '</div>';

            // Date constraint info
            if (min || max) {
                let constraintText = '';
                if (min && max) {
                    constraintText = `${min} to ${max}`;
                } else if (min) {
                    constraintText = `From ${min}`;
                } else {
                    constraintText = `Until ${max}`;
                }
                html += `<div class="text-xs text-gray-400 mt-1">${escapeHtml(constraintText)}</div>`;
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
            const input = document.getElementById(`${safeId}_input`);
            const clearBtn = document.getElementById(`${safeId}_clear`);

            if (input) {
                input.value = value || '';
            }
            if (clearBtn) {
                clearBtn.classList.toggle('hidden', !value);
            }
        },

        validate: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            const errorEl = document.getElementById(`${safeId}_error`);

            if (!input) return { valid: true, errors: [] };

            const isValid = input.checkValidity();

            if (errorEl) {
                if (!isValid) {
                    errorEl.textContent = input.validationMessage;
                    errorEl.classList.remove('hidden');
                    input.classList.add('border-red-500');
                } else {
                    errorEl.classList.add('hidden');
                    input.classList.remove('border-red-500');
                }
            }

            return { valid: isValid, errors: isValid ? [] : [input.validationMessage] };
        },

        handlers: {
            onChange: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('date-picker');
                const safeId = sanitizeId(fieldId);
                const clearBtn = document.getElementById(`${safeId}_clear`);
                const value = widget.getValue(fieldId);

                if (clearBtn) {
                    clearBtn.classList.toggle('hidden', !value);
                }

                widget.validate(fieldId);
                triggerChange(fieldId, value);
            },

            onClear: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('date-picker');
                widget.setValue(fieldId, '');
                triggerChange(fieldId, '');
            }
        }
    });

    console.log('[DatePickerWidget] Date picker widget registered');
})();
