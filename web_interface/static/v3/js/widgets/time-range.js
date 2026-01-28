/**
 * LEDMatrix Time Range Widget
 *
 * Reusable paired start/end time inputs with validation.
 * Can be used by any plugin via x-widget: "time-range" in their schema.
 *
 * Schema example:
 * {
 *   "quiet_hours": {
 *     "type": "object",
 *     "x-widget": "time-range",
 *     "properties": {
 *       "start_time": { "type": "string", "format": "time" },
 *       "end_time": { "type": "string", "format": "time" }
 *     },
 *     "x-options": {
 *       "allowOvernight": true,    // Allow end < start (overnight schedules)
 *       "showDuration": false,     // Show calculated duration
 *       "disabled": false,         // Start disabled
 *       "startLabel": "Start",     // Custom label for start time
 *       "endLabel": "End"          // Custom label for end time
 *     }
 *   }
 * }
 *
 * @module TimeRangeWidget
 */

(function() {
    'use strict';

    // Use BaseWidget utilities if available
    const base = window.BaseWidget ? new window.BaseWidget('TimeRange', '1.0.0') : null;

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

    function showError(container, message) {
        if (base) {
            base.showError(container, message);
        } else {
            clearError(container);
            const errorEl = document.createElement('div');
            errorEl.className = 'widget-error text-sm text-red-600 mt-2';
            errorEl.textContent = message;
            container.appendChild(errorEl);
        }
    }

    function clearError(container) {
        if (base) {
            base.clearError(container);
        } else {
            const errorEl = container.querySelector('.widget-error');
            if (errorEl) errorEl.remove();
        }
    }

    /**
     * Parse time string to minutes since midnight
     * @param {string} timeStr - Time in HH:MM format
     * @returns {number} Minutes since midnight, or -1 if invalid
     */
    function parseTimeToMinutes(timeStr) {
        if (!timeStr || typeof timeStr !== 'string') return -1;
        const match = timeStr.match(/^(\d{1,2}):(\d{2})$/);
        if (!match) return -1;
        const hours = parseInt(match[1], 10);
        const minutes = parseInt(match[2], 10);
        if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return -1;
        return hours * 60 + minutes;
    }

    /**
     * Calculate duration between two times
     * @param {string} startTime - Start time HH:MM
     * @param {string} endTime - End time HH:MM
     * @param {boolean} allowOvernight - Whether overnight is allowed
     * @returns {string} Duration string
     */
    function calculateDuration(startTime, endTime, allowOvernight) {
        const startMinutes = parseTimeToMinutes(startTime);
        const endMinutes = parseTimeToMinutes(endTime);

        if (startMinutes < 0 || endMinutes < 0) return '';

        let durationMinutes;
        if (endMinutes >= startMinutes) {
            durationMinutes = endMinutes - startMinutes;
        } else if (allowOvernight) {
            durationMinutes = (24 * 60 - startMinutes) + endMinutes;
        } else {
            return 'Invalid range';
        }

        const hours = Math.floor(durationMinutes / 60);
        const minutes = durationMinutes % 60;

        if (hours === 0) return `${minutes}m`;
        if (minutes === 0) return `${hours}h`;
        return `${hours}h ${minutes}m`;
    }

    window.LEDMatrixWidgets.register('time-range', {
        name: 'Time Range Widget',
        version: '1.0.0',

        /**
         * Render the time range widget
         * @param {HTMLElement} container - Container element
         * @param {Object} config - Schema configuration
         * @param {Object} value - Object with start_time and end_time
         * @param {Object} options - Additional options (fieldId, pluginId)
         */
        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'time_range');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const allowOvernight = xOptions.allowOvernight !== false;
            const showDuration = xOptions.showDuration === true;
            const disabled = xOptions.disabled === true;
            const startLabel = xOptions.startLabel || 'Start Time';
            const endLabel = xOptions.endLabel || 'End Time';

            // Normalize value
            const startTime = (value && value.start_time) || '07:00';
            const endTime = (value && value.end_time) || '23:00';

            const disabledAttr = disabled ? 'disabled' : '';
            const disabledClass = disabled ? 'bg-gray-100 cursor-not-allowed' : '';
            const inputName = options.name || fieldId;

            let html = `<div id="${fieldId}_widget" class="time-range-widget" data-field-id="${fieldId}" data-allow-overnight="${allowOvernight}">`;

            // Hidden inputs for form submission
            html += `<input type="hidden" id="${fieldId}_start_time" name="${inputName}_start_time" value="${escapeHtml(startTime)}">`;
            html += `<input type="hidden" id="${fieldId}_end_time" name="${inputName}_end_time" value="${escapeHtml(endTime)}">`;

            html += `<div class="grid grid-cols-1 md:grid-cols-2 gap-4">`;

            // Start time input
            html += `
                <div class="form-group">
                    <label for="${fieldId}_start_input" class="block text-sm font-medium text-gray-700">${escapeHtml(startLabel)}</label>
                    <input type="time"
                           id="${fieldId}_start_input"
                           value="${escapeHtml(startTime)}"
                           ${disabledAttr}
                           onchange="window.LEDMatrixWidgets.getHandlers('time-range').onChange('${fieldId}')"
                           class="form-control mt-1 ${disabledClass}">
                </div>
            `;

            // End time input
            html += `
                <div class="form-group">
                    <label for="${fieldId}_end_input" class="block text-sm font-medium text-gray-700">${escapeHtml(endLabel)}</label>
                    <input type="time"
                           id="${fieldId}_end_input"
                           value="${escapeHtml(endTime)}"
                           ${disabledAttr}
                           onchange="window.LEDMatrixWidgets.getHandlers('time-range').onChange('${fieldId}')"
                           class="form-control mt-1 ${disabledClass}">
                </div>
            `;

            html += '</div>';

            // Duration display
            if (showDuration) {
                const duration = calculateDuration(startTime, endTime, allowOvernight);
                html += `
                    <div id="${fieldId}_duration" class="mt-2 text-sm text-gray-500">
                        Duration: <span class="font-medium">${escapeHtml(duration)}</span>
                    </div>
                `;
            }

            html += '</div>';

            container.innerHTML = html;
        },

        /**
         * Get current time range value
         * @param {string} fieldId - Field ID
         * @returns {Object} Object with start_time and end_time
         */
        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const startInput = document.getElementById(`${safeId}_start_input`);
            const endInput = document.getElementById(`${safeId}_end_input`);

            return {
                start_time: startInput ? startInput.value : '',
                end_time: endInput ? endInput.value : ''
            };
        },

        /**
         * Set time range value
         * @param {string} fieldId - Field ID
         * @param {Object} value - Object with start_time and end_time
         */
        setValue: function(fieldId, value) {
            const safeId = sanitizeId(fieldId);
            const startInput = document.getElementById(`${safeId}_start_input`);
            const endInput = document.getElementById(`${safeId}_end_input`);
            const startHidden = document.getElementById(`${safeId}_start_time`);
            const endHidden = document.getElementById(`${safeId}_end_time`);

            const startTime = (value && value.start_time) || '';
            const endTime = (value && value.end_time) || '';

            if (startInput) startInput.value = startTime;
            if (endInput) endInput.value = endTime;
            if (startHidden) startHidden.value = startTime;
            if (endHidden) endHidden.value = endTime;

            // Update duration if shown
            this.handlers.updateDuration(fieldId);
        },

        /**
         * Validate the time range
         * @param {string} fieldId - Field ID
         * @returns {Object} { valid: boolean, errors: Array }
         */
        validate: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const widget = document.getElementById(`${safeId}_widget`);
            const value = this.getValue(fieldId);
            const errors = [];

            // Check for empty values
            if (!value.start_time) {
                errors.push('Start time is required');
            }
            if (!value.end_time) {
                errors.push('End time is required');
            }

            // Validate time format
            if (value.start_time && parseTimeToMinutes(value.start_time) < 0) {
                errors.push('Invalid start time format');
            }
            if (value.end_time && parseTimeToMinutes(value.end_time) < 0) {
                errors.push('Invalid end time format');
            }

            // Check for valid range if overnight not allowed
            if (widget && errors.length === 0) {
                const allowOvernight = widget.dataset.allowOvernight === 'true';
                if (!allowOvernight) {
                    const startMinutes = parseTimeToMinutes(value.start_time);
                    const endMinutes = parseTimeToMinutes(value.end_time);
                    if (endMinutes <= startMinutes) {
                        errors.push('End time must be after start time');
                    }
                }
            }

            // Show/clear errors
            if (widget) {
                if (errors.length > 0) {
                    showError(widget, errors[0]);
                } else {
                    clearError(widget);
                }
            }

            return {
                valid: errors.length === 0,
                errors
            };
        },

        /**
         * Set disabled state
         * @param {string} fieldId - Field ID
         * @param {boolean} disabled - Whether to disable
         */
        setDisabled: function(fieldId, disabled) {
            const safeId = sanitizeId(fieldId);
            const startInput = document.getElementById(`${safeId}_start_input`);
            const endInput = document.getElementById(`${safeId}_end_input`);

            if (startInput) {
                startInput.disabled = disabled;
                startInput.classList.toggle('bg-gray-100', disabled);
                startInput.classList.toggle('cursor-not-allowed', disabled);
            }
            if (endInput) {
                endInput.disabled = disabled;
                endInput.classList.toggle('bg-gray-100', disabled);
                endInput.classList.toggle('cursor-not-allowed', disabled);
            }
        },

        handlers: {
            /**
             * Handle time input change
             * @param {string} fieldId - Field ID
             */
            onChange: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('time-range');
                const value = widget.getValue(fieldId);
                const safeId = sanitizeId(fieldId);

                // Update hidden inputs
                const startHidden = document.getElementById(`${safeId}_start_time`);
                const endHidden = document.getElementById(`${safeId}_end_time`);
                if (startHidden) startHidden.value = value.start_time;
                if (endHidden) endHidden.value = value.end_time;

                // Update duration
                this.updateDuration(fieldId);

                // Validate
                widget.validate(fieldId);

                // Trigger change event
                triggerChange(fieldId, value);
            },

            /**
             * Update duration display
             * @param {string} fieldId - Field ID
             */
            updateDuration: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const durationEl = document.getElementById(`${safeId}_duration`);
                if (!durationEl) return;

                const widget = window.LEDMatrixWidgets.get('time-range');
                const value = widget.getValue(fieldId);
                const widgetEl = document.getElementById(`${safeId}_widget`);
                const allowOvernight = widgetEl && widgetEl.dataset.allowOvernight === 'true';

                const duration = calculateDuration(value.start_time, value.end_time, allowOvernight);
                const spanEl = durationEl.querySelector('span');
                if (spanEl) {
                    spanEl.textContent = duration;
                }
            }
        }
    });

    // Expose utility functions for external use
    window.LEDMatrixWidgets.get('time-range').parseTimeToMinutes = parseTimeToMinutes;
    window.LEDMatrixWidgets.get('time-range').calculateDuration = calculateDuration;

    console.log('[TimeRangeWidget] Time range widget registered');
})();
