/**
 * LEDMatrix Toggle Switch Widget
 *
 * Styled boolean toggle switch (more visual than checkbox).
 *
 * Schema example:
 * {
 *   "enabled": {
 *     "type": "boolean",
 *     "x-widget": "toggle-switch",
 *     "x-options": {
 *       "labelOn": "Enabled",
 *       "labelOff": "Disabled",
 *       "size": "medium",  // "small", "medium", "large"
 *       "colorOn": "blue", // "blue", "green", "red", "purple"
 *       "showLabels": true
 *     }
 *   }
 * }
 *
 * @module ToggleSwitchWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('ToggleSwitch', '1.0.0') : null;

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

    const SIZE_CLASSES = {
        small: {
            track: 'w-8 h-4',
            thumb: 'w-3 h-3',
            translate: 'translate-x-4'
        },
        medium: {
            track: 'w-11 h-6',
            thumb: 'w-5 h-5',
            translate: 'translate-x-5'
        },
        large: {
            track: 'w-14 h-7',
            thumb: 'w-6 h-6',
            translate: 'translate-x-7'
        }
    };

    const COLOR_CLASSES = {
        blue: 'bg-blue-600',
        green: 'bg-green-600',
        red: 'bg-red-600',
        purple: 'bg-purple-600',
        amber: 'bg-amber-500'
    };

    window.LEDMatrixWidgets.register('toggle-switch', {
        name: 'Toggle Switch Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'toggle');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const labelOn = xOptions.labelOn || 'On';
            const labelOff = xOptions.labelOff || 'Off';
            const size = xOptions.size || 'medium';
            const colorOn = xOptions.colorOn || 'blue';
            const showLabels = xOptions.showLabels !== false;
            const disabled = xOptions.disabled === true;

            const isChecked = value === true || value === 'true';
            const sizeClass = SIZE_CLASSES[size] || SIZE_CLASSES.medium;
            const colorClass = COLOR_CLASSES[colorOn] || COLOR_CLASSES.blue;

            let html = `<div id="${fieldId}_widget" class="toggle-switch-widget flex items-center" data-field-id="${fieldId}" data-label-on="${escapeHtml(labelOn)}" data-label-off="${escapeHtml(labelOff)}" data-color="${colorOn}">`;

            // Hidden checkbox for form submission
            html += `<input type="hidden" id="${fieldId}_hidden" name="${options.name || fieldId}" value="${isChecked}">`;

            html += `
                <button type="button"
                        id="${fieldId}_button"
                        role="switch"
                        aria-checked="${isChecked}"
                        ${disabled ? 'disabled' : ''}
                        onclick="window.LEDMatrixWidgets.getHandlers('toggle-switch').onToggle('${fieldId}')"
                        class="relative inline-flex flex-shrink-0 ${sizeClass.track} border-2 border-transparent rounded-full cursor-pointer transition-colors ease-in-out duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 ${isChecked ? colorClass : 'bg-gray-200'} ${disabled ? 'opacity-50 cursor-not-allowed' : ''}">
                    <span class="sr-only">Toggle</span>
                    <span id="${fieldId}_thumb"
                          class="pointer-events-none inline-block ${sizeClass.thumb} rounded-full bg-white shadow transform ring-0 transition ease-in-out duration-200 ${isChecked ? sizeClass.translate : 'translate-x-0'}"></span>
                </button>
            `;

            // Label
            if (showLabels) {
                html += `
                    <span id="${fieldId}_label" class="ml-3 text-sm font-medium ${isChecked ? 'text-gray-900' : 'text-gray-500'}">
                        ${escapeHtml(isChecked ? labelOn : labelOff)}
                    </span>
                `;
            }

            html += '</div>';

            container.innerHTML = html;
        },

        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const hidden = document.getElementById(`${safeId}_hidden`);
            return hidden ? hidden.value === 'true' : false;
        },

        setValue: function(fieldId, value) {
            const safeId = sanitizeId(fieldId);
            const isChecked = value === true || value === 'true';

            const hidden = document.getElementById(`${safeId}_hidden`);
            const button = document.getElementById(`${safeId}_button`);
            const thumb = document.getElementById(`${safeId}_thumb`);
            const label = document.getElementById(`${safeId}_label`);
            const widget = document.getElementById(`${safeId}_widget`);

            if (hidden) hidden.value = isChecked;

            if (button) {
                button.setAttribute('aria-checked', isChecked);
                // Get color class from current classes or default
                const colorClasses = Object.values(COLOR_CLASSES);
                let currentColor = 'bg-blue-600';
                for (const cls of colorClasses) {
                    if (button.classList.contains(cls)) {
                        currentColor = cls;
                        break;
                    }
                }
                if (isChecked) {
                    button.classList.remove('bg-gray-200');
                    button.classList.add(currentColor);
                } else {
                    button.classList.remove(...colorClasses);
                    button.classList.add('bg-gray-200');
                }
            }

            if (thumb) {
                // Determine size from current translate class
                const sizeKeys = Object.keys(SIZE_CLASSES);
                for (const sizeKey of sizeKeys) {
                    const sizeClass = SIZE_CLASSES[sizeKey];
                    if (thumb.classList.contains(sizeClass.thumb)) {
                        if (isChecked) {
                            thumb.classList.remove('translate-x-0');
                            thumb.classList.add(sizeClass.translate);
                        } else {
                            thumb.classList.remove(sizeClass.translate);
                            thumb.classList.add('translate-x-0');
                        }
                        break;
                    }
                }
            }

            if (label) {
                // Get labels from widget data attributes or default
                const labelOn = widget?.dataset.labelOn || 'On';
                const labelOff = widget?.dataset.labelOff || 'Off';
                label.textContent = isChecked ? labelOn : labelOff;
                if (isChecked) {
                    label.classList.remove('text-gray-500');
                    label.classList.add('text-gray-900');
                } else {
                    label.classList.remove('text-gray-900');
                    label.classList.add('text-gray-500');
                }
            }
        },

        handlers: {
            onToggle: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('toggle-switch');
                const currentValue = widget.getValue(fieldId);
                const newValue = !currentValue;
                widget.setValue(fieldId, newValue);
                triggerChange(fieldId, newValue);
            }
        }
    });

    console.log('[ToggleSwitchWidget] Toggle switch widget registered');
})();
