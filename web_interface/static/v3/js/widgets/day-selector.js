/**
 * LEDMatrix Day Selector Widget
 *
 * Reusable checkbox group for selecting days of the week.
 * Can be used by any plugin via x-widget: "day-selector" in their schema.
 *
 * Schema example:
 * {
 *   "active_days": {
 *     "type": "array",
 *     "x-widget": "day-selector",
 *     "items": { "enum": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] },
 *     "x-options": {
 *       "format": "short",      // "short" (Mon) or "long" (Monday)
 *       "layout": "horizontal", // "horizontal" or "vertical"
 *       "selectAll": true       // Show "Select All" toggle
 *     }
 *   }
 * }
 *
 * @module DaySelectorWidget
 */

(function() {
    'use strict';

    const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

    const DAY_LABELS = {
        short: {
            monday: 'Mon',
            tuesday: 'Tue',
            wednesday: 'Wed',
            thursday: 'Thu',
            friday: 'Fri',
            saturday: 'Sat',
            sunday: 'Sun'
        },
        long: {
            monday: 'Monday',
            tuesday: 'Tuesday',
            wednesday: 'Wednesday',
            thursday: 'Thursday',
            friday: 'Friday',
            saturday: 'Saturday',
            sunday: 'Sunday'
        }
    };

    // Use BaseWidget utilities if available
    const base = window.BaseWidget ? new window.BaseWidget('DaySelector', '1.0.0') : null;

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

    window.LEDMatrixWidgets.register('day-selector', {
        name: 'Day Selector Widget',
        version: '1.0.0',

        /**
         * Render the day selector widget
         * @param {HTMLElement} container - Container element
         * @param {Object} config - Schema configuration
         * @param {Array} value - Array of selected day names
         * @param {Object} options - Additional options (fieldId, pluginId)
         */
        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'day_selector');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const requestedFormat = xOptions.format || 'long';
            // Validate format exists in DAY_LABELS, default to 'long' if not
            const format = DAY_LABELS.hasOwnProperty(requestedFormat) ? requestedFormat : 'long';
            const layout = xOptions.layout || 'horizontal';
            const showSelectAll = xOptions.selectAll !== false;

            // Normalize value to array and filter to only valid days
            const rawDays = Array.isArray(value) ? value : [];
            const selectedDays = rawDays.filter(day => DAYS.includes(day));
            const inputName = options.name || fieldId;

            // Build HTML
            let html = `<div id="${fieldId}_widget" class="day-selector-widget" data-field-id="${fieldId}">`;

            // Hidden input to store the value as JSON array
            // Note: Using single quotes for attribute, JSON uses double quotes, so no escaping needed
            html += `<input type="hidden" id="${fieldId}_data" name="${escapeHtml(inputName)}" value='${JSON.stringify(selectedDays)}'>`;

            // Select All toggle
            if (showSelectAll) {
                const allSelected = selectedDays.length === DAYS.length;
                html += `
                    <div class="mb-2">
                        <label class="inline-flex items-center cursor-pointer">
                            <input type="checkbox"
                                   id="${fieldId}_select_all"
                                   ${allSelected ? 'checked' : ''}
                                   onchange="window.LEDMatrixWidgets.getHandlers('day-selector').onSelectAll('${fieldId}', this.checked)"
                                   class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                            <span class="ml-2 text-sm font-medium text-gray-700">Select All</span>
                        </label>
                    </div>
                `;
            }

            // Day checkboxes
            const containerClass = layout === 'horizontal'
                ? 'flex flex-wrap gap-3'
                : 'space-y-2';

            html += `<div class="${containerClass}">`;

            // Get the validated label map (guaranteed to exist due to format validation above)
            const labelMap = DAY_LABELS[format] || DAY_LABELS.long;

            for (const day of DAYS) {
                const isChecked = selectedDays.includes(day);
                const label = labelMap[day] || day;

                html += `
                    <label class="inline-flex items-center cursor-pointer">
                        <input type="checkbox"
                               id="${fieldId}_${day}"
                               data-day="${day}"
                               ${isChecked ? 'checked' : ''}
                               onchange="window.LEDMatrixWidgets.getHandlers('day-selector').onChange('${fieldId}')"
                               class="day-checkbox h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                        <span class="ml-1 text-sm text-gray-700">${escapeHtml(label)}</span>
                    </label>
                `;
            }

            html += '</div></div>';

            container.innerHTML = html;
        },

        /**
         * Get current selected days
         * @param {string} fieldId - Field ID
         * @returns {Array} Array of selected day names
         */
        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const widget = document.getElementById(`${safeId}_widget`);
            if (!widget) return [];

            const selectedDays = [];
            const checkboxes = widget.querySelectorAll('.day-checkbox:checked');
            checkboxes.forEach(cb => {
                selectedDays.push(cb.dataset.day);
            });

            return selectedDays;
        },

        /**
         * Set selected days
         * @param {string} fieldId - Field ID
         * @param {Array} days - Array of day names to select
         */
        setValue: function(fieldId, days) {
            const safeId = sanitizeId(fieldId);
            const widget = document.getElementById(`${safeId}_widget`);
            if (!widget) return;

            // Filter to only valid days
            const rawDays = Array.isArray(days) ? days : [];
            const selectedDays = rawDays.filter(day => DAYS.includes(day));

            // Update checkboxes
            DAYS.forEach(day => {
                const checkbox = document.getElementById(`${safeId}_${day}`);
                if (checkbox) {
                    checkbox.checked = selectedDays.includes(day);
                }
            });

            // Update hidden input
            const hiddenInput = document.getElementById(`${safeId}_data`);
            if (hiddenInput) {
                hiddenInput.value = JSON.stringify(selectedDays);
            }

            // Update select all checkbox
            const selectAllCheckbox = document.getElementById(`${safeId}_select_all`);
            if (selectAllCheckbox) {
                selectAllCheckbox.checked = selectedDays.length === DAYS.length;
            }
        },

        handlers: {
            /**
             * Handle individual day checkbox change
             * @param {string} fieldId - Field ID
             */
            onChange: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('day-selector');
                const selectedDays = widget.getValue(fieldId);

                // Update hidden input
                const safeId = sanitizeId(fieldId);
                const hiddenInput = document.getElementById(`${safeId}_data`);
                if (hiddenInput) {
                    hiddenInput.value = JSON.stringify(selectedDays);
                }

                // Update select all checkbox state
                const selectAllCheckbox = document.getElementById(`${safeId}_select_all`);
                if (selectAllCheckbox) {
                    selectAllCheckbox.checked = selectedDays.length === DAYS.length;
                }

                // Trigger change event
                triggerChange(fieldId, selectedDays);
            },

            /**
             * Handle select all toggle
             * @param {string} fieldId - Field ID
             * @param {boolean} selectAll - Whether to select all
             */
            onSelectAll: function(fieldId, selectAll) {
                const widget = window.LEDMatrixWidgets.get('day-selector');
                widget.setValue(fieldId, selectAll ? DAYS.slice() : []);

                // Trigger change event
                triggerChange(fieldId, selectAll ? DAYS.slice() : []);
            }
        }
    });

    // Expose DAYS constant for external use
    window.LEDMatrixWidgets.get('day-selector').DAYS = DAYS;
    window.LEDMatrixWidgets.get('day-selector').DAY_LABELS = DAY_LABELS;

    console.log('[DaySelectorWidget] Day selector widget registered');
})();
