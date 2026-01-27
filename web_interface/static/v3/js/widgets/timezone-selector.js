/**
 * LEDMatrix Timezone Selector Widget
 *
 * Dropdown for selecting IANA timezone with grouped regions.
 *
 * Schema example:
 * {
 *   "timezone": {
 *     "type": "string",
 *     "x-widget": "timezone-selector",
 *     "x-options": {
 *       "showOffset": true,
 *       "placeholder": "Select timezone..."
 *     }
 *   }
 * }
 *
 * @module TimezoneSelectorWidget
 */

(function() {
    'use strict';

    const base = window.BaseWidget ? new window.BaseWidget('TimezoneSelector', '1.0.0') : null;

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

    // IANA timezone list grouped by region
    const TIMEZONE_GROUPS = {
        'US & Canada': [
            { value: 'America/New_York', label: 'Eastern Time (New York)' },
            { value: 'America/Chicago', label: 'Central Time (Chicago)' },
            { value: 'America/Denver', label: 'Mountain Time (Denver)' },
            { value: 'America/Phoenix', label: 'Mountain Time - Arizona (Phoenix)' },
            { value: 'America/Los_Angeles', label: 'Pacific Time (Los Angeles)' },
            { value: 'America/Anchorage', label: 'Alaska Time (Anchorage)' },
            { value: 'Pacific/Honolulu', label: 'Hawaii Time (Honolulu)' },
            { value: 'America/Detroit', label: 'Eastern Time (Detroit)' },
            { value: 'America/Indiana/Indianapolis', label: 'Eastern Time (Indianapolis)' },
            { value: 'America/Toronto', label: 'Eastern Time (Toronto)' },
            { value: 'America/Vancouver', label: 'Pacific Time (Vancouver)' },
            { value: 'America/Edmonton', label: 'Mountain Time (Edmonton)' },
            { value: 'America/Winnipeg', label: 'Central Time (Winnipeg)' },
            { value: 'America/Halifax', label: 'Atlantic Time (Halifax)' },
            { value: 'America/St_Johns', label: 'Newfoundland Time (St. Johns)' }
        ],
        'Mexico & Central America': [
            { value: 'America/Mexico_City', label: 'Mexico City' },
            { value: 'America/Cancun', label: 'Cancun' },
            { value: 'America/Tijuana', label: 'Tijuana' },
            { value: 'America/Guatemala', label: 'Guatemala' },
            { value: 'America/Costa_Rica', label: 'Costa Rica' },
            { value: 'America/Panama', label: 'Panama' },
            { value: 'America/El_Salvador', label: 'El Salvador' },
            { value: 'America/Tegucigalpa', label: 'Honduras' },
            { value: 'America/Managua', label: 'Nicaragua' },
            { value: 'America/Belize', label: 'Belize' }
        ],
        'South America': [
            { value: 'America/Sao_Paulo', label: 'Sao Paulo' },
            { value: 'America/Buenos_Aires', label: 'Buenos Aires' },
            { value: 'America/Santiago', label: 'Santiago' },
            { value: 'America/Lima', label: 'Lima' },
            { value: 'America/Bogota', label: 'Bogota' },
            { value: 'America/Caracas', label: 'Caracas' },
            { value: 'America/La_Paz', label: 'La Paz' },
            { value: 'America/Montevideo', label: 'Montevideo' },
            { value: 'America/Asuncion', label: 'Asuncion' },
            { value: 'America/Guayaquil', label: 'Guayaquil' }
        ],
        'Europe': [
            { value: 'Europe/London', label: 'London (GMT/BST)' },
            { value: 'Europe/Dublin', label: 'Dublin' },
            { value: 'Europe/Paris', label: 'Paris' },
            { value: 'Europe/Berlin', label: 'Berlin' },
            { value: 'Europe/Madrid', label: 'Madrid' },
            { value: 'Europe/Rome', label: 'Rome' },
            { value: 'Europe/Amsterdam', label: 'Amsterdam' },
            { value: 'Europe/Brussels', label: 'Brussels' },
            { value: 'Europe/Vienna', label: 'Vienna' },
            { value: 'Europe/Zurich', label: 'Zurich' },
            { value: 'Europe/Stockholm', label: 'Stockholm' },
            { value: 'Europe/Oslo', label: 'Oslo' },
            { value: 'Europe/Copenhagen', label: 'Copenhagen' },
            { value: 'Europe/Helsinki', label: 'Helsinki' },
            { value: 'Europe/Warsaw', label: 'Warsaw' },
            { value: 'Europe/Prague', label: 'Prague' },
            { value: 'Europe/Budapest', label: 'Budapest' },
            { value: 'Europe/Athens', label: 'Athens' },
            { value: 'Europe/Bucharest', label: 'Bucharest' },
            { value: 'Europe/Sofia', label: 'Sofia' },
            { value: 'Europe/Lisbon', label: 'Lisbon' },
            { value: 'Europe/Moscow', label: 'Moscow' },
            { value: 'Europe/Kiev', label: 'Kyiv' },
            { value: 'Europe/Istanbul', label: 'Istanbul' }
        ],
        'UK & Ireland': [
            { value: 'Europe/London', label: 'London' },
            { value: 'Europe/Dublin', label: 'Dublin' },
            { value: 'Europe/London', label: 'Belfast' }  // Belfast uses Europe/London (canonical IANA identifier)
        ],
        'Asia': [
            { value: 'Asia/Tokyo', label: 'Tokyo' },
            { value: 'Asia/Seoul', label: 'Seoul' },
            { value: 'Asia/Shanghai', label: 'Shanghai' },
            { value: 'Asia/Hong_Kong', label: 'Hong Kong' },
            { value: 'Asia/Taipei', label: 'Taipei' },
            { value: 'Asia/Singapore', label: 'Singapore' },
            { value: 'Asia/Kuala_Lumpur', label: 'Kuala Lumpur' },
            { value: 'Asia/Bangkok', label: 'Bangkok' },
            { value: 'Asia/Ho_Chi_Minh', label: 'Ho Chi Minh City' },
            { value: 'Asia/Jakarta', label: 'Jakarta' },
            { value: 'Asia/Manila', label: 'Manila' },
            { value: 'Asia/Kolkata', label: 'India (Kolkata)' },
            { value: 'Asia/Mumbai', label: 'Mumbai' },
            { value: 'Asia/Dhaka', label: 'Dhaka' },
            { value: 'Asia/Karachi', label: 'Karachi' },
            { value: 'Asia/Dubai', label: 'Dubai' },
            { value: 'Asia/Riyadh', label: 'Riyadh' },
            { value: 'Asia/Jerusalem', label: 'Jerusalem' },
            { value: 'Asia/Tehran', label: 'Tehran' },
            { value: 'Asia/Kabul', label: 'Kabul' },
            { value: 'Asia/Kathmandu', label: 'Kathmandu' },
            { value: 'Asia/Colombo', label: 'Colombo' },
            { value: 'Asia/Yangon', label: 'Yangon' }
        ],
        'Australia & Pacific': [
            { value: 'Australia/Sydney', label: 'Sydney' },
            { value: 'Australia/Melbourne', label: 'Melbourne' },
            { value: 'Australia/Brisbane', label: 'Brisbane' },
            { value: 'Australia/Perth', label: 'Perth' },
            { value: 'Australia/Adelaide', label: 'Adelaide' },
            { value: 'Australia/Darwin', label: 'Darwin' },
            { value: 'Australia/Hobart', label: 'Hobart' },
            { value: 'Pacific/Auckland', label: 'Auckland' },
            { value: 'Pacific/Fiji', label: 'Fiji' },
            { value: 'Pacific/Guam', label: 'Guam' },
            { value: 'Pacific/Port_Moresby', label: 'Port Moresby' },
            { value: 'Pacific/Noumea', label: 'Noumea' }
        ],
        'Africa': [
            { value: 'Africa/Cairo', label: 'Cairo' },
            { value: 'Africa/Johannesburg', label: 'Johannesburg' },
            { value: 'Africa/Lagos', label: 'Lagos' },
            { value: 'Africa/Nairobi', label: 'Nairobi' },
            { value: 'Africa/Casablanca', label: 'Casablanca' },
            { value: 'Africa/Algiers', label: 'Algiers' },
            { value: 'Africa/Tunis', label: 'Tunis' },
            { value: 'Africa/Accra', label: 'Accra' },
            { value: 'Africa/Addis_Ababa', label: 'Addis Ababa' },
            { value: 'Africa/Dar_es_Salaam', label: 'Dar es Salaam' }
        ],
        'Atlantic': [
            { value: 'Atlantic/Reykjavik', label: 'Reykjavik (Iceland)' },
            { value: 'Atlantic/Azores', label: 'Azores' },
            { value: 'Atlantic/Cape_Verde', label: 'Cape Verde' },
            { value: 'Atlantic/Bermuda', label: 'Bermuda' }
        ],
        'UTC': [
            { value: 'UTC', label: 'UTC (Coordinated Universal Time)' },
            { value: 'Etc/GMT', label: 'GMT (Greenwich Mean Time)' },
            { value: 'Etc/GMT+0', label: 'GMT+0' },
            { value: 'Etc/GMT-1', label: 'GMT-1 (UTC+1)' },
            { value: 'Etc/GMT-2', label: 'GMT-2 (UTC+2)' },
            { value: 'Etc/GMT+1', label: 'GMT+1 (UTC-1)' },
            { value: 'Etc/GMT+2', label: 'GMT+2 (UTC-2)' }
        ]
    };

    // Get current UTC offset for a timezone
    function getTimezoneOffset(tz) {
        try {
            const now = new Date();
            const formatter = new Intl.DateTimeFormat('en-US', {
                timeZone: tz,
                timeZoneName: 'shortOffset'
            });
            const parts = formatter.formatToParts(now);
            const offsetPart = parts.find(p => p.type === 'timeZoneName');
            return offsetPart ? offsetPart.value : '';
        } catch (e) {
            return '';
        }
    }

    window.LEDMatrixWidgets.register('timezone-selector', {
        name: 'Timezone Selector Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            const fieldId = sanitizeId(options.fieldId || container.id || 'timezone_selector');
            const xOptions = config['x-options'] || config['x_options'] || {};
            const showOffset = xOptions.showOffset !== false;
            const placeholder = xOptions.placeholder || 'Select timezone...';
            const disabled = xOptions.disabled === true;

            // Validate current value - must be a valid timezone string
            const currentValue = (typeof value === 'string' && value.trim()) ? value.trim() : '';

            let html = `<div id="${fieldId}_widget" class="timezone-selector-widget" data-field-id="${fieldId}">`;

            // Hidden input for form submission
            html += `<input type="hidden" id="${fieldId}_data" name="${escapeHtml(options.name || fieldId)}" value="${escapeHtml(currentValue)}">`;

            html += `
                <select id="${fieldId}_input"
                        ${disabled ? 'disabled' : ''}
                        onchange="window.LEDMatrixWidgets.getHandlers('timezone-selector').onChange('${fieldId}')"
                        class="form-select w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 ${disabled ? 'bg-gray-100 cursor-not-allowed' : 'bg-white'} text-black">
            `;

            // Placeholder option
            html += `<option value="" ${!currentValue ? 'selected' : ''} disabled>${escapeHtml(placeholder)}</option>`;

            // Build options grouped by region
            for (const [groupName, timezones] of Object.entries(TIMEZONE_GROUPS)) {
                html += `<optgroup label="${escapeHtml(groupName)}">`;

                for (const tz of timezones) {
                    const isSelected = currentValue === tz.value;
                    let displayLabel = tz.label;

                    // Add UTC offset if enabled
                    if (showOffset) {
                        const offset = getTimezoneOffset(tz.value);
                        if (offset) {
                            displayLabel = `${tz.label} (${offset})`;
                        }
                    }

                    html += `<option value="${escapeHtml(tz.value)}" ${isSelected ? 'selected' : ''}>${escapeHtml(displayLabel)}</option>`;
                }

                html += '</optgroup>';
            }

            html += '</select>';

            // Show current time in selected timezone
            html += `<div id="${fieldId}_preview" class="text-sm text-gray-500 mt-2 ${currentValue ? '' : 'hidden'}">
                <span class="font-medium">Current time:</span>
                <span id="${fieldId}_time"></span>
            </div>`;

            html += '</div>';

            container.innerHTML = html;

            // Update time preview if value is set
            if (currentValue) {
                this.handlers.updateTimePreview(fieldId, currentValue);
            }
        },

        getValue: function(fieldId) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            return input ? input.value : '';
        },

        setValue: function(fieldId, value) {
            const safeId = sanitizeId(fieldId);
            const input = document.getElementById(`${safeId}_input`);
            const hiddenInput = document.getElementById(`${safeId}_data`);

            if (input) {
                input.value = value || '';
            }
            if (hiddenInput) {
                hiddenInput.value = value || '';
            }

            this.handlers.updateTimePreview(fieldId, value);
        },

        handlers: {
            onChange: function(fieldId) {
                const safeId = sanitizeId(fieldId);
                const widget = window.LEDMatrixWidgets.get('timezone-selector');
                const value = widget.getValue(fieldId);

                // Update hidden input for form submission
                const hiddenInput = document.getElementById(`${safeId}_data`);
                if (hiddenInput) {
                    hiddenInput.value = value;
                }

                widget.handlers.updateTimePreview(fieldId, value);
                triggerChange(fieldId, value);
            },

            updateTimePreview: function(fieldId, timezone) {
                const safeId = sanitizeId(fieldId);
                const previewEl = document.getElementById(`${safeId}_preview`);
                const timeEl = document.getElementById(`${safeId}_time`);

                if (!previewEl || !timeEl) return;

                if (!timezone) {
                    previewEl.classList.add('hidden');
                    return;
                }

                try {
                    const now = new Date();
                    const formatter = new Intl.DateTimeFormat('en-US', {
                        timeZone: timezone,
                        weekday: 'short',
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                        hour12: true
                    });
                    timeEl.textContent = formatter.format(now);
                    previewEl.classList.remove('hidden');
                } catch (e) {
                    previewEl.classList.add('hidden');
                }
            }
        }
    });

    // Expose timezone data for external use
    window.LEDMatrixWidgets.get('timezone-selector').TIMEZONE_GROUPS = TIMEZONE_GROUPS;

    console.log('[TimezoneSelectorWidget] Timezone selector widget registered');
})();
