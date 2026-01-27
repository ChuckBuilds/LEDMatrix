/**
 * LEDMatrix Schedule Picker Widget
 *
 * Composite widget combining enable toggle, mode switch (global/per-day),
 * and time range configurations. Composes day-selector and time-range widgets.
 *
 * Can be used standalone in schedule.html or by plugins via x-widget: "schedule-picker".
 *
 * Schema example:
 * {
 *   "schedule": {
 *     "type": "object",
 *     "x-widget": "schedule-picker",
 *     "x-options": {
 *       "showModeToggle": true,     // Allow switching global/per-day
 *       "showEnableToggle": true,   // Show enabled checkbox
 *       "compactMode": false,       // Compact layout for embedded use
 *       "defaultMode": "global"     // Default mode: "global" or "per_day"
 *     }
 *   }
 * }
 *
 * API-compatible output format:
 * {
 *   enabled: boolean,
 *   mode: "global" | "per_day",
 *   start_time: "HH:MM",           // if global mode
 *   end_time: "HH:MM",             // if global mode
 *   days: {                        // if per_day mode
 *     monday: { enabled: boolean, start_time: "HH:MM", end_time: "HH:MM" },
 *     ...
 *   }
 * }
 *
 * @module SchedulePickerWidget
 */

(function() {
    'use strict';

    const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

    const DAY_LABELS = {
        monday: 'Monday',
        tuesday: 'Tuesday',
        wednesday: 'Wednesday',
        thursday: 'Thursday',
        friday: 'Friday',
        saturday: 'Saturday',
        sunday: 'Sunday'
    };

    // Use BaseWidget utilities if available
    const base = window.BaseWidget ? new window.BaseWidget('SchedulePicker', '1.0.0') : null;

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

    /**
     * Generate default schedule config
     */
    function getDefaultSchedule() {
        const days = {};
        DAYS.forEach(day => {
            days[day] = {
                enabled: true,
                start_time: '07:00',
                end_time: '23:00'
            };
        });
        return {
            enabled: false,
            mode: 'global',
            start_time: '07:00',
            end_time: '23:00',
            days: days
        };
    }

    /**
     * Merge user value with defaults
     */
    function normalizeSchedule(value) {
        const defaults = getDefaultSchedule();
        if (!value || typeof value !== 'object') {
            return defaults;
        }

        const schedule = {
            enabled: value.enabled === true,
            mode: value.mode === 'per_day' ? 'per_day' : 'global',
            start_time: value.start_time || defaults.start_time,
            end_time: value.end_time || defaults.end_time,
            days: {}
        };

        // Merge days
        DAYS.forEach(day => {
            const dayConfig = (value.days && value.days[day]) || defaults.days[day];
            schedule.days[day] = {
                enabled: dayConfig.enabled !== false,
                start_time: dayConfig.start_time || defaults.days[day].start_time,
                end_time: dayConfig.end_time || defaults.days[day].end_time
            };
        });

        return schedule;
    }

    window.LEDMatrixWidgets.register('schedule-picker', {
        name: 'Schedule Picker Widget',
        version: '1.0.0',

        /**
         * Render the schedule picker widget
         * @param {HTMLElement} container - Container element
         * @param {Object} config - Schema configuration
         * @param {Object} value - Schedule configuration object
         * @param {Object} options - Additional options (fieldId, pluginId)
         */
        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'schedule');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const showModeToggle = xOptions.showModeToggle !== false;
            const showEnableToggle = xOptions.showEnableToggle !== false;
            const compactMode = xOptions.compactMode === true;

            const schedule = normalizeSchedule(value);

            let html = `<div id="${fieldId}_widget" class="schedule-picker-widget" data-field-id="${fieldId}">`;

            // Hidden inputs for API-compatible form submission
            html += this._renderHiddenInputs(fieldId, schedule);

            // Enable toggle
            if (showEnableToggle) {
                html += `
                    <div class="bg-blue-50 rounded-lg p-4 mb-4">
                        <label class="flex items-center cursor-pointer">
                            <input type="checkbox"
                                   id="${fieldId}_enabled"
                                   ${schedule.enabled ? 'checked' : ''}
                                   onchange="window.LEDMatrixWidgets.getHandlers('schedule-picker').onEnabledChange('${fieldId}', this.checked)"
                                   class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                            <span class="ml-2 text-sm font-medium text-gray-900">Enable Schedule</span>
                        </label>
                        <p class="mt-1 text-sm text-gray-600">When enabled, the display will only operate during specified hours.</p>
                    </div>
                `;
            }

            // Mode selection
            if (showModeToggle) {
                html += `
                    <div class="bg-gray-50 rounded-lg p-4 mb-4">
                        <h3 class="text-md font-medium text-gray-900 mb-4">Schedule Mode</h3>
                        <div class="space-y-3">
                            <label class="flex items-center cursor-pointer">
                                <input type="radio"
                                       name="${fieldId}_mode"
                                       value="global"
                                       id="${fieldId}_mode_global"
                                       ${schedule.mode === 'global' ? 'checked' : ''}
                                       onchange="window.LEDMatrixWidgets.getHandlers('schedule-picker').onModeChange('${fieldId}', 'global')"
                                       class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300">
                                <span class="ml-2 text-sm font-medium text-gray-900">Global Schedule</span>
                            </label>
                            <p class="ml-6 text-sm text-gray-600">Use the same start and end time for all days of the week</p>

                            <label class="flex items-center cursor-pointer mt-4">
                                <input type="radio"
                                       name="${fieldId}_mode"
                                       value="per_day"
                                       id="${fieldId}_mode_per_day"
                                       ${schedule.mode === 'per_day' ? 'checked' : ''}
                                       onchange="window.LEDMatrixWidgets.getHandlers('schedule-picker').onModeChange('${fieldId}', 'per_day')"
                                       class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300">
                                <span class="ml-2 text-sm font-medium text-gray-900">Per-Day Schedule</span>
                            </label>
                            <p class="ml-6 text-sm text-gray-600">Set different times for each day of the week</p>
                        </div>
                    </div>
                `;
            }

            // Global schedule section
            const globalDisplay = schedule.mode === 'global' ? 'block' : 'none';
            html += `
                <div id="${fieldId}_global_section" class="bg-gray-50 rounded-lg p-4 mb-4" style="display: ${globalDisplay};">
                    <h3 class="text-md font-medium text-gray-900 mb-4">Global Times</h3>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div class="form-group">
                            <label for="${fieldId}_global_start" class="block text-sm font-medium text-gray-700">Start Time</label>
                            <input type="time"
                                   id="${fieldId}_global_start"
                                   value="${escapeHtml(schedule.start_time)}"
                                   onchange="window.LEDMatrixWidgets.getHandlers('schedule-picker').onGlobalTimeChange('${fieldId}')"
                                   class="form-control mt-1">
                            <p class="mt-1 text-sm text-gray-600">When to start displaying content (HH:MM)</p>
                        </div>
                        <div class="form-group">
                            <label for="${fieldId}_global_end" class="block text-sm font-medium text-gray-700">End Time</label>
                            <input type="time"
                                   id="${fieldId}_global_end"
                                   value="${escapeHtml(schedule.end_time)}"
                                   onchange="window.LEDMatrixWidgets.getHandlers('schedule-picker').onGlobalTimeChange('${fieldId}')"
                                   class="form-control mt-1">
                            <p class="mt-1 text-sm text-gray-600">When to stop displaying content (HH:MM)</p>
                        </div>
                    </div>
                </div>
            `;

            // Per-day schedule section
            const perDayDisplay = schedule.mode === 'per_day' ? 'block' : 'none';
            html += `
                <div id="${fieldId}_perday_section" style="display: ${perDayDisplay};">
                    <h3 class="text-md font-medium text-gray-900 mb-4">Day-Specific Times</h3>
                    <div class="bg-gray-50 rounded-lg p-4">
                        <div class="overflow-x-auto">
                            <table class="min-w-full divide-y divide-gray-200">
                                <thead>
                                    <tr class="bg-gray-100">
                                        <th class="px-3 py-2 text-left text-xs font-semibold text-gray-700 uppercase">Day</th>
                                        <th class="px-3 py-2 text-left text-xs font-semibold text-gray-700 uppercase">Enabled</th>
                                        <th class="px-3 py-2 text-left text-xs font-semibold text-gray-700 uppercase">Start</th>
                                        <th class="px-3 py-2 text-left text-xs font-semibold text-gray-700 uppercase">End</th>
                                    </tr>
                                </thead>
                                <tbody class="bg-white divide-y divide-gray-200">
            `;

            // Render each day row
            DAYS.forEach(day => {
                const dayConfig = schedule.days[day];
                const disabled = !dayConfig.enabled;
                const disabledClass = disabled ? 'bg-gray-100' : '';

                html += `
                    <tr class="hover:bg-gray-50" id="${fieldId}_row_${day}">
                        <td class="px-3 py-2 whitespace-nowrap">
                            <span class="text-sm font-medium text-gray-900">${escapeHtml(DAY_LABELS[day])}</span>
                        </td>
                        <td class="px-3 py-2 whitespace-nowrap">
                            <input type="checkbox"
                                   id="${fieldId}_${day}_enabled"
                                   ${dayConfig.enabled ? 'checked' : ''}
                                   onchange="window.LEDMatrixWidgets.getHandlers('schedule-picker').onDayEnabledChange('${fieldId}', '${day}', this.checked)"
                                   class="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded">
                        </td>
                        <td class="px-3 py-2 whitespace-nowrap">
                            <input type="time"
                                   id="${fieldId}_${day}_start"
                                   value="${escapeHtml(dayConfig.start_time)}"
                                   ${disabled ? 'disabled' : ''}
                                   onchange="window.LEDMatrixWidgets.getHandlers('schedule-picker').onDayTimeChange('${fieldId}', '${day}')"
                                   class="block w-full px-2 py-1 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500 ${disabledClass}">
                        </td>
                        <td class="px-3 py-2 whitespace-nowrap">
                            <input type="time"
                                   id="${fieldId}_${day}_end"
                                   value="${escapeHtml(dayConfig.end_time)}"
                                   ${disabled ? 'disabled' : ''}
                                   onchange="window.LEDMatrixWidgets.getHandlers('schedule-picker').onDayTimeChange('${fieldId}', '${day}')"
                                   class="block w-full px-2 py-1 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-blue-500 focus:border-blue-500 ${disabledClass}">
                        </td>
                    </tr>
                `;
            });

            html += `
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;

            html += '</div>';

            container.innerHTML = html;
        },

        /**
         * Render hidden inputs for form submission
         * These match the existing API format
         */
        _renderHiddenInputs: function(fieldId, schedule) {
            let html = '';

            // Enabled state (hidden input ensures value is always sent, even when checkbox is unchecked)
            html += `<input type="hidden" id="${fieldId}_enabled_hidden" name="enabled" value="${schedule.enabled}">`;

            // Mode indicator (for the widget to track internally)
            html += `<input type="hidden" id="${fieldId}_mode_value" name="mode" value="${schedule.mode}">`;

            // Global times (used when mode is global)
            html += `<input type="hidden" id="${fieldId}_start_time_hidden" name="start_time" value="${escapeHtml(schedule.start_time)}">`;
            html += `<input type="hidden" id="${fieldId}_end_time_hidden" name="end_time" value="${escapeHtml(schedule.end_time)}">`;

            // Per-day values (used when mode is per_day)
            DAYS.forEach(day => {
                const dayConfig = schedule.days[day];
                html += `<input type="hidden" id="${fieldId}_${day}_enabled_hidden" name="${day}_enabled" value="${dayConfig.enabled}">`;
                html += `<input type="hidden" id="${fieldId}_${day}_start_hidden" name="${day}_start" value="${escapeHtml(dayConfig.start_time)}">`;
                html += `<input type="hidden" id="${fieldId}_${day}_end_hidden" name="${day}_end" value="${escapeHtml(dayConfig.end_time)}">`;
            });

            return html;
        },

        /**
         * Get current schedule value
         * @param {string} fieldId - Field ID
         * @returns {Object} Schedule configuration object
         */
        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const widget = document.getElementById(`${safeId}_widget`);
            if (!widget) return getDefaultSchedule();

            const enabledCheckbox = document.getElementById(`${safeId}_enabled`);
            const modeGlobal = document.getElementById(`${safeId}_mode_global`);
            const globalStart = document.getElementById(`${safeId}_global_start`);
            const globalEnd = document.getElementById(`${safeId}_global_end`);

            const schedule = {
                enabled: enabledCheckbox ? enabledCheckbox.checked : false,
                mode: (modeGlobal && modeGlobal.checked) ? 'global' : 'per_day',
                start_time: globalStart ? globalStart.value : '07:00',
                end_time: globalEnd ? globalEnd.value : '23:00',
                days: {}
            };

            DAYS.forEach(day => {
                const dayEnabled = document.getElementById(`${safeId}_${day}_enabled`);
                const dayStart = document.getElementById(`${safeId}_${day}_start`);
                const dayEnd = document.getElementById(`${safeId}_${day}_end`);

                schedule.days[day] = {
                    enabled: dayEnabled ? dayEnabled.checked : true,
                    start_time: dayStart ? dayStart.value : '07:00',
                    end_time: dayEnd ? dayEnd.value : '23:00'
                };
            });

            return schedule;
        },

        /**
         * Set schedule value
         * @param {string} fieldId - Field ID
         * @param {Object} value - Schedule configuration object
         */
        setValue: function(fieldId, value) {
            const safeId = sanitizeId(fieldId);
            const schedule = normalizeSchedule(value);

            // Set enabled
            const enabledCheckbox = document.getElementById(`${safeId}_enabled`);
            if (enabledCheckbox) enabledCheckbox.checked = schedule.enabled;

            // Set mode
            const modeGlobal = document.getElementById(`${safeId}_mode_global`);
            const modePerDay = document.getElementById(`${safeId}_mode_per_day`);
            if (modeGlobal) modeGlobal.checked = schedule.mode === 'global';
            if (modePerDay) modePerDay.checked = schedule.mode === 'per_day';

            // Set global times
            const globalStart = document.getElementById(`${safeId}_global_start`);
            const globalEnd = document.getElementById(`${safeId}_global_end`);
            if (globalStart) globalStart.value = schedule.start_time;
            if (globalEnd) globalEnd.value = schedule.end_time;

            // Set per-day values
            DAYS.forEach(day => {
                const dayConfig = schedule.days[day];
                const dayEnabled = document.getElementById(`${safeId}_${day}_enabled`);
                const dayStart = document.getElementById(`${safeId}_${day}_start`);
                const dayEnd = document.getElementById(`${safeId}_${day}_end`);

                if (dayEnabled) dayEnabled.checked = dayConfig.enabled;
                if (dayStart) {
                    dayStart.value = dayConfig.start_time;
                    dayStart.disabled = !dayConfig.enabled;
                    dayStart.classList.toggle('bg-gray-100', !dayConfig.enabled);
                }
                if (dayEnd) {
                    dayEnd.value = dayConfig.end_time;
                    dayEnd.disabled = !dayConfig.enabled;
                    dayEnd.classList.toggle('bg-gray-100', !dayConfig.enabled);
                }
            });

            // Update visibility
            this.handlers.onModeChange(fieldId, schedule.mode);

            // Update hidden inputs
            this._updateHiddenInputs(fieldId);
        },

        /**
         * Update all hidden inputs to match current state
         */
        _updateHiddenInputs: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const schedule = this.getValue(fieldId);

            // Enabled
            const enabledHidden = document.getElementById(`${safeId}_enabled_hidden`);
            if (enabledHidden) enabledHidden.value = schedule.enabled;

            // Mode
            const modeHidden = document.getElementById(`${safeId}_mode_value`);
            if (modeHidden) modeHidden.value = schedule.mode;

            // Global times
            const startHidden = document.getElementById(`${safeId}_start_time_hidden`);
            const endHidden = document.getElementById(`${safeId}_end_time_hidden`);
            if (startHidden) startHidden.value = schedule.start_time;
            if (endHidden) endHidden.value = schedule.end_time;

            // Per-day values
            DAYS.forEach(day => {
                const dayConfig = schedule.days[day];
                const enabledHidden = document.getElementById(`${safeId}_${day}_enabled_hidden`);
                const startHidden = document.getElementById(`${safeId}_${day}_start_hidden`);
                const endHidden = document.getElementById(`${safeId}_${day}_end_hidden`);

                if (enabledHidden) enabledHidden.value = dayConfig.enabled;
                if (startHidden) startHidden.value = dayConfig.start_time;
                if (endHidden) endHidden.value = dayConfig.end_time;
            });
        },

        handlers: {
            /**
             * Handle enabled toggle change
             */
            onEnabledChange: function(fieldId, enabled) {
                const widget = window.LEDMatrixWidgets.get('schedule-picker');
                widget._updateHiddenInputs(fieldId);
                triggerChange(fieldId, widget.getValue(fieldId));
            },

            /**
             * Handle mode switch
             */
            onModeChange: function(fieldId, mode) {
                const safeId = sanitizeId(fieldId);
                const globalSection = document.getElementById(`${safeId}_global_section`);
                const perDaySection = document.getElementById(`${safeId}_perday_section`);

                if (globalSection) globalSection.style.display = mode === 'global' ? 'block' : 'none';
                if (perDaySection) perDaySection.style.display = mode === 'per_day' ? 'block' : 'none';

                const widget = window.LEDMatrixWidgets.get('schedule-picker');
                widget._updateHiddenInputs(fieldId);
                triggerChange(fieldId, widget.getValue(fieldId));
            },

            /**
             * Handle global time change
             */
            onGlobalTimeChange: function(fieldId) {
                const widget = window.LEDMatrixWidgets.get('schedule-picker');
                widget._updateHiddenInputs(fieldId);
                triggerChange(fieldId, widget.getValue(fieldId));
            },

            /**
             * Handle day enabled change
             */
            onDayEnabledChange: function(fieldId, day, enabled) {
                const safeId = sanitizeId(fieldId);
                const dayStart = document.getElementById(`${safeId}_${day}_start`);
                const dayEnd = document.getElementById(`${safeId}_${day}_end`);

                if (dayStart) {
                    dayStart.disabled = !enabled;
                    dayStart.classList.toggle('bg-gray-100', !enabled);
                    // Set default value only when enabling and input is empty
                    if (enabled && !dayStart.value) {
                        dayStart.value = '07:00';
                    }
                }

                if (dayEnd) {
                    dayEnd.disabled = !enabled;
                    dayEnd.classList.toggle('bg-gray-100', !enabled);
                    // Set default value only when enabling and input is empty
                    if (enabled && !dayEnd.value) {
                        dayEnd.value = '23:00';
                    }
                }

                const widget = window.LEDMatrixWidgets.get('schedule-picker');
                widget._updateHiddenInputs(fieldId);
                triggerChange(fieldId, widget.getValue(fieldId));
            },

            /**
             * Handle day time change
             */
            onDayTimeChange: function(fieldId, day) {
                const widget = window.LEDMatrixWidgets.get('schedule-picker');
                widget._updateHiddenInputs(fieldId);
                triggerChange(fieldId, widget.getValue(fieldId));
            }
        }
    });

    // Expose constants for external use
    window.LEDMatrixWidgets.get('schedule-picker').DAYS = DAYS;
    window.LEDMatrixWidgets.get('schedule-picker').DAY_LABELS = DAY_LABELS;
    window.LEDMatrixWidgets.get('schedule-picker').getDefaultSchedule = getDefaultSchedule;
    window.LEDMatrixWidgets.get('schedule-picker').normalizeSchedule = normalizeSchedule;

    console.log('[SchedulePickerWidget] Schedule picker widget registered');
})();
