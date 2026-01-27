/**
 * LEDMatrix Email Input Widget
 *
 * Email input with validation and common domain suggestions.
 *
 * Schema example:
 * {
 *   "email": {
 *     "type": "string",
 *     "format": "email",
 *     "x-widget": "email-input",
 *     "x-options": {
 *       "placeholder": "user@example.com",
 *       "showIcon": true
 *     }
 *   }
 * }
 *
 * @module EmailInputWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('EmailInput', '1.0.0') : null;

    function escapeHtml(text) {
        if (base) return base.escapeHtml(text);
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
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

    window.LEDMatrixWidgets.register('email-input', {
        name: 'Email Input Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'email_input');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const placeholder = xOptions.placeholder || 'email@example.com';
            const showIcon = xOptions.showIcon !== false;
            const disabled = xOptions.disabled === true;
            const required = xOptions.required === true;

            const currentValue = value || '';

            let html = `<div id="${fieldId}_widget" class="email-input-widget" data-field-id="${fieldId}">`;

            html += '<div class="relative">';

            if (showIcon) {
                html += `
                    <div class="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none">
                        <i class="fas fa-envelope text-gray-400"></i>
                    </div>
                `;
            }

            html += `
                <input type="email"
                       id="${fieldId}_input"
                       name="${options.name || fieldId}"
                       value="${escapeHtml(currentValue)}"
                       placeholder="${escapeHtml(placeholder)}"
                       ${disabled ? 'disabled' : ''}
                       ${required ? 'required' : ''}
                       onchange="window.LEDMatrixWidgets.getHandlers('email-input').onChange('${fieldId}')"
                       oninput="window.LEDMatrixWidgets.getHandlers('email-input').onInput('${fieldId}')"
                       class="form-input w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 ${showIcon ? 'pl-10' : ''} ${disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'} text-black placeholder:text-gray-400">
            `;

            html += '</div>';

            // Validation indicator
            html += `
                <div id="${fieldId}_valid" class="text-sm text-green-600 mt-1 hidden">
                    <i class="fas fa-check-circle mr-1"></i>Valid email format
                </div>
            `;

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
                input.value = value || '';
                this.handlers.onInput(fieldId);
            }
        },

        validate: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            const errorEl = document.getElementById(`${safeId}_error`);
            const validEl = document.getElementById(`${safeId}_valid`);

            if (!input) return { valid: true, errors: [] };

            const value = input.value;
            const isValid = input.checkValidity() && (!value || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value));

            if (errorEl && validEl) {
                if (!isValid && value) {
                    errorEl.textContent = 'Please enter a valid email address';
                    errorEl.classList.remove('hidden');
                    validEl.classList.add('hidden');
                    input.classList.add('border-red-500');
                    input.classList.remove('border-green-500');
                } else if (isValid && value) {
                    errorEl.classList.add('hidden');
                    validEl.classList.remove('hidden');
                    input.classList.remove('border-red-500');
                    input.classList.add('border-green-500');
                } else {
                    errorEl.classList.add('hidden');
                    validEl.classList.add('hidden');
                    input.classList.remove('border-red-500', 'border-green-500');
                }
            }

            return { valid: isValid, errors: isValid ? [] : ['Invalid email format'] };
        },

        handlers: {
            onChange: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('email-input');
                widget.validate(fieldId);
                triggerChange(fieldId, widget.getValue(fieldId));
            },

            onInput: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('email-input');
                // Validate on input for real-time feedback
                widget.validate(fieldId);
            }
        }
    });

    console.log('[EmailInputWidget] Email input widget registered');
})();
