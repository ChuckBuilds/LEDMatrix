/**
 * Google Calendar Picker Widget
 *
 * Renders a dynamic multi-select checklist of Google Calendars fetched from
 * /api/v3/plugins/calendar/list-calendars. Selected IDs are stored in a hidden
 * comma-separated input so the existing backend parser works unchanged.
 *
 * @module GoogleCalendarPickerWidget
 */

(function () {
    'use strict';

    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[GoogleCalendarPickerWidget] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    window.LEDMatrixWidgets.register('google-calendar-picker', {
        name: 'Google Calendar Picker Widget',
        version: '1.0.0',

        /**
         * Render the widget into container.
         * @param {HTMLElement} container
         * @param {Object} config  - schema config (unused)
         * @param {Array}  value   - current array of selected calendar IDs
         * @param {Object} options - { fieldId, pluginId, name }
         */
        render: function (container, config, value, options) {
            const fieldId = options.fieldId;
            const name = options.name;
            const currentIds = Array.isArray(value) ? value : ['primary'];

            // Hidden input — this is what the form submits
            const hiddenInput = document.createElement('input');
            hiddenInput.type = 'hidden';
            hiddenInput.id = fieldId + '_hidden';
            hiddenInput.name = name;
            hiddenInput.value = currentIds.join(', ');

            // Current selection summary — kept in sync whenever the hidden value changes
            const summary = document.createElement('p');
            summary.id = fieldId + '_summary';
            summary.className = 'text-xs text-gray-400 mt-1';
            summary.textContent = 'Currently selected: ' + currentIds.join(', ');

            // "Load My Calendars" button
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.id = fieldId + '_load_btn';
            btn.className = 'px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-md flex items-center gap-1.5';
            btn.innerHTML = '<i class="fas fa-calendar-alt"></i> Load My Calendars';
            btn.addEventListener('click', function () {
                loadCalendars(fieldId, hiddenInput, listContainer, btn, summary);
            });

            // Status / list area
            const listContainer = document.createElement('div');
            listContainer.id = fieldId + '_list';
            listContainer.className = 'mt-2';

            container.appendChild(btn);
            container.appendChild(summary);
            container.appendChild(listContainer);
            container.appendChild(hiddenInput);
        },

        getValue: function (fieldId) {
            const hidden = document.getElementById(fieldId + '_hidden');
            if (!hidden || !hidden.value.trim()) return [];
            return hidden.value.split(',').map(s => s.trim()).filter(Boolean);
        },

        setValue: function (fieldId, values) {
            const hidden = document.getElementById(fieldId + '_hidden');
            if (hidden) {
                hidden.value = (Array.isArray(values) ? values : []).join(', ');
            }
        },

        handlers: {}
    });

    /**
     * Fetch calendar list from backend and render checkboxes.
     */
    function loadCalendars(fieldId, hiddenInput, listContainer, btn, summary) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
        listContainer.innerHTML = '';

        fetch('/api/v3/plugins/calendar/list-calendars')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                btn.disabled = false;
                if (data.status !== 'success') {
                    btn.innerHTML = '<i class="fas fa-calendar-alt"></i> Load My Calendars';
                    showError(listContainer, data.message || 'Failed to load calendars.');
                    return;
                }
                btn.innerHTML = '<i class="fas fa-sync-alt"></i> Refresh Calendars';
                renderCheckboxes(fieldId, data.calendars, hiddenInput, listContainer, summary);
            })
            .catch(function (err) {
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-calendar-alt"></i> Load My Calendars';
                showError(listContainer, 'Request failed: ' + err.message);
            });
    }

    /**
     * Render a checklist of calendars, pre-checking those already in the hidden input.
     */
    function renderCheckboxes(fieldId, calendars, hiddenInput, listContainer, summary) {
        listContainer.innerHTML = '';

        if (!calendars || calendars.length === 0) {
            showError(listContainer, 'No calendars found on this account.');
            return;
        }

        const wrapper = document.createElement('div');
        wrapper.className = 'mt-2 space-y-1.5 border border-gray-700 rounded-md p-3 bg-gray-800';

        // Track selected IDs — seed from the hidden input so manually-typed IDs are preserved
        let selectedIds = hiddenInput.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean);

        function syncHiddenAndSummary() {
            hiddenInput.value = selectedIds.join(', ');
            summary.textContent = 'Currently selected: ' + (selectedIds.length ? selectedIds.join(', ') : '(none)');
        }

        calendars.forEach(function (cal) {
            const isChecked = selectedIds.includes(cal.id);

            const label = document.createElement('label');
            label.className = 'flex items-center gap-2 cursor-pointer';

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'h-4 w-4 text-blue-600 border-gray-300 rounded';
            checkbox.value = cal.id;
            checkbox.checked = isChecked;
            checkbox.addEventListener('change', function () {
                if (checkbox.checked) {
                    if (!selectedIds.includes(cal.id)) selectedIds.push(cal.id);
                } else {
                    selectedIds = selectedIds.filter(function (id) { return id !== cal.id; });
                }
                // Ensure at least one calendar is selected
                if (selectedIds.length === 0) {
                    checkbox.checked = true;
                    selectedIds.push(cal.id);
                    if (window.showNotification) {
                        window.showNotification('At least one calendar must be selected.', 'warning');
                    }
                }
                syncHiddenAndSummary();
            });

            const nameSpan = document.createElement('span');
            nameSpan.className = 'text-sm text-gray-200 flex-1';
            nameSpan.textContent = cal.summary + (cal.primary ? ' (primary)' : '');

            const idSpan = document.createElement('span');
            idSpan.className = 'text-xs text-gray-500 font-mono truncate max-w-xs';
            idSpan.textContent = cal.id;
            idSpan.title = cal.id;

            label.appendChild(checkbox);
            label.appendChild(nameSpan);
            label.appendChild(idSpan);
            wrapper.appendChild(label);
        });

        listContainer.appendChild(wrapper);
    }

    function showError(container, message) {
        container.innerHTML = '';
        const p = document.createElement('p');
        p.className = 'text-xs text-red-400 mt-1 flex items-center gap-1';
        p.innerHTML = '<i class="fas fa-exclamation-triangle"></i> ' + escapeHtml(message);
        container.appendChild(p);
    }

    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    console.log('[GoogleCalendarPickerWidget] registered');
})();
