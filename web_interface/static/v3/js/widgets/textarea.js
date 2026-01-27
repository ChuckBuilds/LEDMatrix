/**
 * LEDMatrix Textarea Widget
 *
 * Multi-line text input with character count and resize options.
 *
 * Schema example:
 * {
 *   "description": {
 *     "type": "string",
 *     "x-widget": "textarea",
 *     "x-options": {
 *       "rows": 4,
 *       "placeholder": "Enter description...",
 *       "maxLength": 500,
 *       "resize": "vertical",  // "none", "vertical", "horizontal", "both"
 *       "showCount": true
 *     }
 *   }
 * }
 *
 * @module TextareaWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('Textarea', '1.0.0') : null;

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

    const RESIZE_CLASSES = {
        none: 'resize-none',
        vertical: 'resize-y',
        horizontal: 'resize-x',
        both: 'resize'
    };

    window.LEDMatrixWidgets.register('textarea', {
        name: 'Textarea Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'textarea');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const rows = xOptions.rows || 4;
            const placeholder = xOptions.placeholder || '';
            const maxLength = xOptions.maxLength || config.maxLength || null;
            const minLength = xOptions.minLength || config.minLength || 0;
            const resize = xOptions.resize || 'vertical';
            const showCount = xOptions.showCount !== false && maxLength;
            const disabled = xOptions.disabled === true;

            const currentValue = value !== null && value !== undefined ? String(value) : '';
            const resizeClass = RESIZE_CLASSES[resize] || RESIZE_CLASSES.vertical;

            let html = `<div id="${fieldId}_widget" class="textarea-widget" data-field-id="${fieldId}">`;

            html += `
                <textarea id="${fieldId}_input"
                          name="${escapeHtml(options.name || fieldId)}"
                          rows="${rows}"
                          placeholder="${escapeHtml(placeholder)}"
                          ${maxLength ? `maxlength="${maxLength}"` : ''}
                          ${minLength ? `minlength="${minLength}"` : ''}
                          ${disabled ? 'disabled' : ''}
                          onchange="window.LEDMatrixWidgets.getHandlers('textarea').onChange('${fieldId}')"
                          oninput="window.LEDMatrixWidgets.getHandlers('textarea').onInput('${fieldId}')"
                          class="form-textarea w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 ${resizeClass} ${disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'} text-black placeholder:text-gray-400">${escapeHtml(currentValue)}</textarea>
            `;

            // Character count
            if (showCount) {
                html += `
                    <div class="flex justify-end mt-1">
                        <span id="${fieldId}_count" class="text-xs text-gray-400">${currentValue.length}/${maxLength}</span>
                    </div>
                `;
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
            if (input) {
                input.value = value !== null && value !== undefined ? String(value) : '';
                this.handlers.onInput(fieldId);
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
                const widget = window.LEDMatrixWidgets.get('textarea');
                widget.validate(fieldId);
                triggerChange(fieldId, widget.getValue(fieldId));
            },

            onInput: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const input = document.getElementById(`${safeId}_input`);
                const countEl = document.getElementById(`${safeId}_count`);

                if (countEl && input) {
                    const maxLength = input.maxLength;
                    if (maxLength > 0) {
                        countEl.textContent = `${input.value.length}/${maxLength}`;
                        // Change color when near limit
                        if (input.value.length >= maxLength * 0.9) {
                            countEl.classList.remove('text-gray-400');
                            countEl.classList.add('text-amber-500');
                        } else {
                            countEl.classList.remove('text-amber-500');
                            countEl.classList.add('text-gray-400');
                        }
                    }
                }
            }
        }
    });

    console.log('[TextareaWidget] Textarea widget registered');
})();
