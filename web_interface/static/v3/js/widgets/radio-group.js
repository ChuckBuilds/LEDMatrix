/**
 * LEDMatrix Radio Group Widget
 *
 * Exclusive option selection with radio buttons.
 *
 * Schema example:
 * {
 *   "displayMode": {
 *     "type": "string",
 *     "x-widget": "radio-group",
 *     "enum": ["auto", "manual", "scheduled"],
 *     "x-options": {
 *       "layout": "vertical",  // "vertical", "horizontal"
 *       "labels": {
 *         "auto": "Automatic",
 *         "manual": "Manual Control",
 *         "scheduled": "Scheduled"
 *       },
 *       "descriptions": {
 *         "auto": "System decides when to display",
 *         "manual": "You control when content shows",
 *         "scheduled": "Display at specific times"
 *       }
 *     }
 *   }
 * }
 *
 * @module RadioGroupWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('RadioGroup', '1.0.0') : null;

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

    window.LEDMatrixWidgets.register('radio-group', {
        name: 'Radio Group Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'radio_group');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const enumValues = config.enum || xOptions.options || [];
            const layout = xOptions.layout || 'vertical';
            const labels = xOptions.labels || {};
            const descriptions = xOptions.descriptions || {};
            const disabled = xOptions.disabled === true;

            const currentValue = value !== null && value !== undefined ? String(value) : '';

            const containerClass = layout === 'horizontal' ? 'flex flex-wrap gap-4' : 'space-y-3';

            let html = `<div id="${fieldId}_widget" class="radio-group-widget ${containerClass}" data-field-id="${fieldId}">`;

            for (const optValue of enumValues) {
                const optId = `${fieldId}_${sanitizeId(String(optValue))}`;
                const label = labels[optValue] || String(optValue).replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                const description = descriptions[optValue] || '';
                const isChecked = String(optValue) === currentValue;

                html += `
                    <label class="flex items-start cursor-pointer ${disabled ? 'opacity-50' : ''}">
                        <div class="flex items-center h-5">
                            <input type="radio"
                                   id="${optId}"
                                   name="${options.name || fieldId}"
                                   value="${escapeHtml(String(optValue))}"
                                   ${isChecked ? 'checked' : ''}
                                   ${disabled ? 'disabled' : ''}
                                   onchange="window.LEDMatrixWidgets.getHandlers('radio-group').onChange('${fieldId}', '${escapeHtml(String(optValue))}')"
                                   class="h-4 w-4 text-blue-600 border-gray-300 focus:ring-blue-500 ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}">
                        </div>
                        <div class="ml-3">
                            <span class="text-sm font-medium text-gray-900">${escapeHtml(label)}</span>
                            ${description ? `<p class="text-xs text-gray-500">${escapeHtml(description)}</p>` : ''}
                        </div>
                    </label>
                `;
            }

            html += '</div>';

            container.innerHTML = html;
        },

        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const widget = document.getElementById(`${safeId}_widget`);
            if (!widget) return '';

            const checked = widget.querySelector('input[type="radio"]:checked');
            return checked ? checked.value : '';
        },

        setValue: function(fieldId, value) {
            const safeId = sanitizeId(fieldId);
            const widget = document.getElementById(`${safeId}_widget`);
            if (!widget) return;

            const radios = widget.querySelectorAll('input[type="radio"]');
            radios.forEach(radio => {
                radio.checked = radio.value === String(value);
            });
        },

        handlers: {
            onChange: function(fieldId, value) {
                triggerChange(fieldId, value);
            }
        }
    });

    console.log('[RadioGroupWidget] Radio group widget registered');
})();
