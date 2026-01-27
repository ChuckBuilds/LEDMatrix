/**
 * LEDMatrix Number Input Widget
 *
 * Enhanced number input with min/max/step, formatting, and increment buttons.
 *
 * Schema example:
 * {
 *   "brightness": {
 *     "type": "number",
 *     "x-widget": "number-input",
 *     "minimum": 0,
 *     "maximum": 100,
 *     "x-options": {
 *       "step": 5,
 *       "prefix": null,
 *       "suffix": "%",
 *       "showButtons": true,
 *       "format": "integer"  // "integer", "decimal", "percent"
 *     }
 *   }
 * }
 *
 * @module NumberInputWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('NumberInput', '1.0.0') : null;

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

    window.LEDMatrixWidgets.register('number-input', {
        name: 'Number Input Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'number_input');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const min = config.minimum !== undefined ? config.minimum : (xOptions.min !== undefined ? xOptions.min : null);
            const max = config.maximum !== undefined ? config.maximum : (xOptions.max !== undefined ? xOptions.max : null);
            const step = xOptions.step || (config.type === 'integer' ? 1 : 'any');
            const prefix = xOptions.prefix || '';
            const suffix = xOptions.suffix || '';
            const showButtons = xOptions.showButtons !== false;
            const disabled = xOptions.disabled === true;
            const placeholder = xOptions.placeholder || '';

            const currentValue = value !== null && value !== undefined ? value : '';

            let html = `<div id="${fieldId}_widget" class="number-input-widget" data-field-id="${fieldId}" data-min="${min !== null ? min : ''}" data-max="${max !== null ? max : ''}" data-step="${step}">`;

            html += '<div class="flex items-center">';

            if (prefix) {
                html += `<span class="inline-flex items-center px-3 text-sm text-gray-500 bg-gray-100 border border-r-0 border-gray-300 rounded-l-md">${escapeHtml(prefix)}</span>`;
            }

            if (showButtons && !disabled) {
                html += `
                    <button type="button"
                            onclick="window.LEDMatrixWidgets.getHandlers('number-input').onDecrement('${fieldId}')"
                            class="inline-flex items-center px-3 py-2 text-gray-600 bg-gray-100 border border-r-0 border-gray-300 hover:bg-gray-200 ${prefix ? '' : 'rounded-l-md'}">
                        <i class="fas fa-minus text-xs"></i>
                    </button>
                `;
            }

            const inputRoundedClass = showButtons || prefix || suffix ? '' : 'rounded-md';

            html += `
                <input type="number"
                       id="${fieldId}_input"
                       name="${options.name || fieldId}"
                       value="${currentValue}"
                       placeholder="${escapeHtml(placeholder)}"
                       ${min !== null ? `min="${min}"` : ''}
                       ${max !== null ? `max="${max}"` : ''}
                       step="${step}"
                       ${disabled ? 'disabled' : ''}
                       onchange="window.LEDMatrixWidgets.getHandlers('number-input').onChange('${fieldId}')"
                       oninput="window.LEDMatrixWidgets.getHandlers('number-input').onInput('${fieldId}')"
                       class="form-input w-24 text-center ${inputRoundedClass} border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 ${disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'} text-black placeholder:text-gray-400">
            `;

            if (showButtons && !disabled) {
                html += `
                    <button type="button"
                            onclick="window.LEDMatrixWidgets.getHandlers('number-input').onIncrement('${fieldId}')"
                            class="inline-flex items-center px-3 py-2 text-gray-600 bg-gray-100 border border-l-0 border-gray-300 hover:bg-gray-200 ${suffix ? '' : 'rounded-r-md'}">
                        <i class="fas fa-plus text-xs"></i>
                    </button>
                `;
            }

            if (suffix) {
                html += `<span class="inline-flex items-center px-3 text-sm text-gray-500 bg-gray-100 border border-l-0 border-gray-300 rounded-r-md">${escapeHtml(suffix)}</span>`;
            }

            html += '</div>';

            // Range indicator if min/max specified
            if (min !== null || max !== null) {
                const rangeText = min !== null && max !== null
                    ? `${min} - ${max}`
                    : (min !== null ? `Min: ${min}` : `Max: ${max}`);
                html += `<div class="text-xs text-gray-400 mt-1">${escapeHtml(rangeText)}</div>`;
            }

            // Error message area
            html += `<div id="${fieldId}_error" class="text-sm text-red-600 mt-1 hidden"></div>`;

            html += '</div>';

            container.innerHTML = html;
        },

        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            if (!input || input.value === '') return null;
            const num = parseFloat(input.value);
            return isNaN(num) ? null : num;
        },

        setValue: function(fieldId, value) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            if (input) {
                input.value = value !== null && value !== undefined ? value : '';
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
                const widget = window.LEDMatrixWidgets.get('number-input');
                widget.validate(fieldId);
                triggerChange(fieldId, widget.getValue(fieldId));
            },

            onInput: function(fieldId) {
                // Real-time input handling if needed
            },

            onIncrement: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const widget = document.getElementById(`${safeId}_widget`);
                const input = document.getElementById(`${safeId}_input`);
                if (!input || !widget) return;

                const step = parseFloat(widget.dataset.step) || 1;
                const max = widget.dataset.max !== '' ? parseFloat(widget.dataset.max) : Infinity;
                const current = parseFloat(input.value) || 0;
                const newValue = Math.min(current + step, max);

                input.value = newValue;
                this.onChange(fieldId);
            },

            onDecrement: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const widget = document.getElementById(`${safeId}_widget`);
                const input = document.getElementById(`${safeId}_input`);
                if (!input || !widget) return;

                const step = parseFloat(widget.dataset.step) || 1;
                const min = widget.dataset.min !== '' ? parseFloat(widget.dataset.min) : -Infinity;
                const current = parseFloat(input.value) || 0;
                const newValue = Math.max(current - step, min);

                input.value = newValue;
                this.onChange(fieldId);
            }
        }
    });

    console.log('[NumberInputWidget] Number input widget registered');
})();
