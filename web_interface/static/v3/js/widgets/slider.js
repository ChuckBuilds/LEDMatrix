/**
 * LEDMatrix Slider Widget
 *
 * Range slider with value display and optional tick marks.
 *
 * Schema example:
 * {
 *   "volume": {
 *     "type": "number",
 *     "x-widget": "slider",
 *     "minimum": 0,
 *     "maximum": 100,
 *     "x-options": {
 *       "step": 5,
 *       "showValue": true,
 *       "showMinMax": true,
 *       "suffix": "%",
 *       "color": "blue"  // "blue", "green", "red", "purple"
 *     }
 *   }
 * }
 *
 * @module SliderWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('Slider', '1.0.0') : null;

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

    const COLOR_CLASSES = {
        blue: 'accent-blue-600',
        green: 'accent-green-600',
        red: 'accent-red-600',
        purple: 'accent-purple-600',
        amber: 'accent-amber-500'
    };

    window.LEDMatrixWidgets.register('slider', {
        name: 'Slider Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'slider');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const min = config.minimum !== undefined ? config.minimum : (xOptions.min !== undefined ? xOptions.min : 0);
            const max = config.maximum !== undefined ? config.maximum : (xOptions.max !== undefined ? xOptions.max : 100);
            const step = xOptions.step || 1;
            const showValue = xOptions.showValue !== false;
            const showMinMax = xOptions.showMinMax !== false;
            const suffix = xOptions.suffix || '';
            const prefix = xOptions.prefix || '';
            const color = xOptions.color || 'blue';
            const disabled = xOptions.disabled === true;

            const currentValue = value !== null && value !== undefined ? value : min;
            const colorClass = COLOR_CLASSES[color] || COLOR_CLASSES.blue;

            let html = `<div id="${fieldId}_widget" class="slider-widget" data-field-id="${fieldId}" data-prefix="${escapeHtml(prefix)}" data-suffix="${escapeHtml(suffix)}">`;

            // Value display above slider
            if (showValue) {
                html += `
                    <div class="flex justify-center mb-2">
                        <span id="${fieldId}_value" class="text-lg font-semibold text-gray-700">
                            ${escapeHtml(prefix)}${currentValue}${escapeHtml(suffix)}
                        </span>
                    </div>
                `;
            }

            // Slider
            html += `
                <input type="range"
                       id="${fieldId}_input"
                       name="${escapeHtml(options.name || fieldId)}"
                       value="${currentValue}"
                       min="${min}"
                       max="${max}"
                       step="${step}"
                       ${disabled ? 'disabled' : ''}
                       oninput="window.LEDMatrixWidgets.getHandlers('slider').onInput('${fieldId}')"
                       onchange="window.LEDMatrixWidgets.getHandlers('slider').onChange('${fieldId}')"
                       class="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer ${colorClass} ${disabled ? 'opacity-50 cursor-not-allowed' : ''}">
            `;

            // Min/Max labels
            if (showMinMax) {
                html += `
                    <div class="flex justify-between mt-1">
                        <span class="text-xs text-gray-400">${escapeHtml(prefix)}${min}${escapeHtml(suffix)}</span>
                        <span class="text-xs text-gray-400">${escapeHtml(prefix)}${max}${escapeHtml(suffix)}</span>
                    </div>
                `;
            }

            html += '</div>';

            container.innerHTML = html;
        },

        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            if (!input) return null;
            const num = parseFloat(input.value);
            return isNaN(num) ? null : num;
        },

        setValue: function(fieldId, value) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            const valueEl = document.getElementById(`${safeId}_value`);
            const widget = document.getElementById(`${safeId}_widget`);

            if (input) {
                input.value = value !== null && value !== undefined ? value : input.min;
            }
            if (valueEl && widget) {
                const prefix = widget.dataset.prefix || '';
                const suffix = widget.dataset.suffix || '';
                valueEl.textContent = `${prefix}${input.value}${suffix}`;
            }
        },

        handlers: {
            onInput: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const input = document.getElementById(`${safeId}_input`);
                const valueEl = document.getElementById(`${safeId}_value`);
                const widget = document.getElementById(`${safeId}_widget`);

                if (valueEl && input && widget) {
                    const prefix = widget.dataset.prefix || '';
                    const suffix = widget.dataset.suffix || '';
                    valueEl.textContent = `${prefix}${input.value}${suffix}`;
                }
            },

            onChange: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('slider');
                triggerChange(fieldId, widget.getValue(fieldId));
            }
        }
    });

    console.log('[SliderWidget] Slider widget registered');
})();
