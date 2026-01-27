/**
 * LEDMatrix Text Input Widget
 *
 * Enhanced text input with validation, placeholder, and pattern support.
 *
 * Schema example:
 * {
 *   "username": {
 *     "type": "string",
 *     "x-widget": "text-input",
 *     "x-options": {
 *       "placeholder": "Enter username",
 *       "pattern": "^[a-zA-Z0-9_]+$",
 *       "patternMessage": "Only letters, numbers, and underscores allowed",
 *       "minLength": 3,
 *       "maxLength": 20,
 *       "prefix": "@",
 *       "suffix": null,
 *       "clearable": true
 *     }
 *   }
 * }
 *
 * @module TextInputWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('TextInput', '1.0.0') : null;

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

    window.LEDMatrixWidgets.register('text-input', {
        name: 'Text Input Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'text_input');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const placeholder = xOptions.placeholder || '';
            const pattern = xOptions.pattern || '';
            const patternMessage = xOptions.patternMessage || 'Invalid format';

            // Sanitize minLength/maxLength - must be finite non-negative integers
            const rawMinLength = parseInt(xOptions.minLength, 10);
            const rawMaxLength = parseInt(xOptions.maxLength, 10);
            const minLength = (Number.isFinite(rawMinLength) && rawMinLength >= 0 && rawMinLength <= 10000000)
                ? rawMinLength : null;
            const maxLength = (Number.isFinite(rawMaxLength) && rawMaxLength >= 0 && rawMaxLength <= 10000000)
                ? rawMaxLength : null;

            const prefix = xOptions.prefix || '';
            const suffix = xOptions.suffix || '';
            const clearable = xOptions.clearable === true;
            const disabled = xOptions.disabled === true;

            const currentValue = value !== null && value !== undefined ? String(value) : '';

            let html = `<div id="${fieldId}_widget" class="text-input-widget" data-field-id="${fieldId}" data-pattern-message="${escapeHtml(patternMessage)}">`;

            // Container for prefix/input/suffix layout
            const hasAddons = prefix || suffix || clearable;
            if (hasAddons) {
                html += '<div class="flex items-center">';
                if (prefix) {
                    html += `<span class="inline-flex items-center px-3 text-sm text-gray-500 bg-gray-100 border border-r-0 border-gray-300 rounded-l-md">${escapeHtml(prefix)}</span>`;
                }
            }

            const roundedClass = hasAddons
                ? (prefix && suffix ? '' : (prefix ? 'rounded-r-md' : 'rounded-l-md'))
                : 'rounded-md';

            html += `
                <input type="text"
                       id="${fieldId}_input"
                       name="${escapeHtml(options.name || fieldId)}"
                       value="${escapeHtml(currentValue)}"
                       placeholder="${escapeHtml(placeholder)}"
                       ${pattern ? `pattern="${escapeHtml(pattern)}"` : ''}
                       ${minLength !== null ? `minlength="${minLength}"` : ''}
                       ${maxLength !== null ? `maxlength="${maxLength}"` : ''}
                       ${disabled ? 'disabled' : ''}
                       onchange="window.LEDMatrixWidgets.getHandlers('text-input').onChange('${fieldId}')"
                       oninput="window.LEDMatrixWidgets.getHandlers('text-input').onInput('${fieldId}')"
                       class="form-input flex-1 ${roundedClass} border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 ${disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'} text-black placeholder:text-gray-400">
            `;

            if (clearable && !disabled) {
                html += `
                    <button type="button"
                            id="${fieldId}_clear"
                            onclick="window.LEDMatrixWidgets.getHandlers('text-input').onClear('${fieldId}')"
                            class="inline-flex items-center px-2 text-gray-400 hover:text-gray-600 ${currentValue ? '' : 'hidden'}"
                            title="Clear">
                        <i class="fas fa-times"></i>
                    </button>
                `;
            }

            if (suffix) {
                html += `<span class="inline-flex items-center px-3 text-sm text-gray-500 bg-gray-100 border border-l-0 border-gray-300 rounded-r-md">${escapeHtml(suffix)}</span>`;
            }

            if (hasAddons) {
                html += '</div>';
            }

            // Validation message area
            html += `<div id="${fieldId}_error" class="text-sm text-red-600 mt-1 hidden"></div>`;

            // Character count if maxLength specified
            if (maxLength !== null) {
                html += `<div id="${fieldId}_count" class="text-xs text-gray-400 mt-1 text-right">${currentValue.length}/${maxLength}</div>`;
            }

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
                this.handlers.onInput(fieldId);
            }
        },

        validate: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            const errorEl = document.getElementById(`${safeId}_error`);
            const widget = document.getElementById(`${safeId}_widget`);

            if (!input) return { valid: true, errors: [] };

            // Clear any prior custom validity to avoid stale errors
            input.setCustomValidity('');

            let isValid = input.checkValidity();
            let errorMessage = input.validationMessage;

            // Use custom pattern message if pattern mismatch
            if (!isValid && input.validity.patternMismatch && widget) {
                const patternMessage = widget.dataset.patternMessage;
                if (patternMessage) {
                    errorMessage = patternMessage;
                    input.setCustomValidity(patternMessage);
                    // Re-check validity with custom message set
                    isValid = input.checkValidity();
                }
            }

            if (errorEl) {
                if (!isValid) {
                    errorEl.textContent = errorMessage;
                    errorEl.classList.remove('hidden');
                    input.classList.add('border-red-500');
                } else {
                    errorEl.classList.add('hidden');
                    input.classList.remove('border-red-500');
                }
            }

            return { valid: isValid, errors: isValid ? [] : [errorMessage] };
        },

        handlers: {
            onChange: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('text-input');
                widget.validate(fieldId);
                triggerChange(fieldId, widget.getValue(fieldId));
            },

            onInput: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const input = document.getElementById(`${safeId}_input`);
                const clearBtn = document.getElementById(`${safeId}_clear`);
                const countEl = document.getElementById(`${safeId}_count`);

                // Clear any stale custom validity to allow form submission after user fixes input
                if (input && input.validity.customError) {
                    input.setCustomValidity('');
                }

                if (clearBtn) {
                    clearBtn.classList.toggle('hidden', !input.value);
                }

                if (countEl && input) {
                    const maxLength = input.maxLength;
                    if (maxLength > 0) {
                        countEl.textContent = `${input.value.length}/${maxLength}`;
                    }
                }
            },

            onClear: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('text-input');
                widget.setValue(fieldId, '');
                triggerChange(fieldId, '');
            }
        }
    });

    console.log('[TextInputWidget] Text input widget registered');
})();
