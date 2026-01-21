/**
 * Array Table Widget
 *
 * Generic table-based array-of-objects editor.
 * Handles adding, removing, and editing array items with object properties.
 * Reads column definitions from the schema's items.properties.
 *
 * Usage in config_schema.json:
 *   "my_array": {
 *     "type": "array",
 *     "x-widget": "array-table",
 *     "x-columns": ["name", "code", "priority", "enabled"],  // optional
 *     "items": {
 *       "type": "object",
 *       "properties": {
 *         "name": { "type": "string" },
 *         "code": { "type": "string" },
 *         "priority": { "type": "integer", "default": 50 },
 *         "enabled": { "type": "boolean", "default": true }
 *       }
 *     }
 *   }
 *
 * @module ArrayTableWidget
 */

(function() {
    'use strict';

    // Ensure LEDMatrixWidgets registry exists
    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[ArrayTableWidget] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    /**
     * Register the array-table widget
     */
    window.LEDMatrixWidgets.register('array-table', {
        name: 'Array Table Widget',
        version: '1.0.0',

        render: function(container, config, value, options) {
            console.log('[ArrayTableWidget] Render called (server-side rendered)');
        },

        getValue: function(fieldId) {
            const tbody = document.getElementById(`${fieldId}_tbody`);
            if (!tbody) return [];

            const rows = tbody.querySelectorAll('.array-table-row');
            const items = [];

            rows.forEach((row) => {
                const item = {};
                row.querySelectorAll('input').forEach(input => {
                    const name = input.getAttribute('name');
                    if (!name || name.endsWith('.enabled') || input.type === 'hidden') continue;
                    const match = name.match(/\.\d+\.([^.]+)$/);
                    if (match) {
                        const propName = match[1];
                        if (input.type === 'checkbox') {
                            item[propName] = input.checked;
                        } else if (input.type === 'number') {
                            item[propName] = input.value ? parseFloat(input.value) : null;
                        } else if (input.type !== 'hidden') {
                            item[propName] = input.value;
                        }
                    }
                });
                if (Object.keys(item).length > 0) {
                    items.push(item);
                }
            });

            return items;
        },

        setValue: function(fieldId, items, options) {
            if (!Array.isArray(items)) {
                console.error('[ArrayTableWidget] setValue expects an array');
                return;
            }

            if (!options || !options.fullKey || !options.pluginId) {
                throw new Error('ArrayTableWidget.setValue requires options.fullKey and options.pluginId');
            }

            const tbody = document.getElementById(`${fieldId}_tbody`);
            if (!tbody) {
                console.warn(`[ArrayTableWidget] tbody not found for fieldId: ${fieldId}`);
                return;
            }

            tbody.innerHTML = '';

            items.forEach((item, index) => {
                const row = createArrayTableRow(
                    fieldId,
                    options.fullKey,
                    index,
                    options.pluginId,
                    item,
                    options.itemProperties || {},
                    options.displayColumns || []
                );
                tbody.appendChild(row);
            });

            // Refresh Add button state after repopulating rows
            updateAddButtonState(fieldId);
        },

        handlers: {}
    });

    /**
     * Create a table row element for array item
     */
    function createArrayTableRow(fieldId, fullKey, index, pluginId, item, itemProperties, displayColumns) {
        item = item || {};
        const row = document.createElement('tr');
        row.className = 'array-table-row';
        row.setAttribute('data-index', index);

        displayColumns.forEach(colName => {
            const colDef = itemProperties[colName] || {};
            const colType = colDef.type || 'string';
            const colDefault = colDef.default !== undefined ? colDef.default : (colType === 'boolean' ? false : '');
            const colValue = item[colName] !== undefined ? item[colName] : colDefault;

            const cell = document.createElement('td');
            cell.className = 'px-4 py-3 whitespace-nowrap';

            if (colType === 'boolean') {
                const hiddenInput = document.createElement('input');
                hiddenInput.type = 'hidden';
                hiddenInput.name = `${fullKey}.${index}.${colName}`;
                hiddenInput.value = 'false';
                cell.appendChild(hiddenInput);

                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.name = `${fullKey}.${index}.${colName}`;
                checkbox.checked = Boolean(colValue);
                checkbox.value = 'true';
                checkbox.className = 'h-4 w-4 text-blue-600';
                cell.appendChild(checkbox);
            } else if (colType === 'integer' || colType === 'number') {
                const input = document.createElement('input');
                input.type = 'number';
                input.name = `${fullKey}.${index}.${colName}`;
                input.value = colValue !== null && colValue !== undefined ? colValue : '';
                if (colDef.minimum !== undefined) input.min = colDef.minimum;
                if (colDef.maximum !== undefined) input.max = colDef.maximum;
                input.step = colType === 'integer' ? '1' : 'any';
                input.className = 'block w-20 px-2 py-1 border border-gray-300 rounded text-sm text-center';
                if (colDef.description) input.title = colDef.description;
                cell.appendChild(input);
            } else {
                const input = document.createElement('input');
                input.type = 'text';
                input.name = `${fullKey}.${index}.${colName}`;
                input.value = colValue !== null && colValue !== undefined ? colValue : '';
                input.className = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
                if (colDef.description) input.placeholder = colDef.description;
                if (colDef.pattern) input.pattern = colDef.pattern;
                if (colDef.minLength) input.minLength = colDef.minLength;
                if (colDef.maxLength) input.maxLength = colDef.maxLength;
                cell.appendChild(input);
            }

            row.appendChild(cell);
        });

        // Actions cell
        const actionsCell = document.createElement('td');
        actionsCell.className = 'px-4 py-3 whitespace-nowrap text-center';
        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'text-red-600 hover:text-red-800 px-2 py-1';
        removeButton.onclick = function() { removeArrayTableRow(this); };
        const removeIcon = document.createElement('i');
        removeIcon.className = 'fas fa-trash';
        removeButton.appendChild(removeIcon);
        actionsCell.appendChild(removeButton);
        row.appendChild(actionsCell);

        return row;
    }

    /**
     * Update the Add button's disabled state based on current row count
     * @param {string} fieldId - Field ID to find the tbody and button
     */
    function updateAddButtonState(fieldId) {
        const tbody = document.getElementById(fieldId + '_tbody');
        if (!tbody) return;

        // Find the add button by looking for the button with matching data-field-id
        const addButton = document.querySelector(`button[data-field-id="${fieldId}"]`);
        if (!addButton) return;

        const maxItems = parseInt(addButton.getAttribute('data-max-items'), 10);
        const currentRows = tbody.querySelectorAll('.array-table-row');
        const isAtMax = currentRows.length >= maxItems;

        addButton.disabled = isAtMax;
        addButton.style.opacity = isAtMax ? '0.5' : '';
    }

    // Expose for external use if needed
    window.updateArrayTableAddButtonState = updateAddButtonState;

    /**
     * Add a new row to the array table
     * @param {HTMLElement} button - The button element with data attributes
     */
    window.addArrayTableRow = function(button) {
        const fieldId = button.getAttribute('data-field-id');
        const fullKey = button.getAttribute('data-full-key');
        const maxItems = parseInt(button.getAttribute('data-max-items'), 10);
        const pluginId = button.getAttribute('data-plugin-id');

        // Parse JSON with fallback on error
        let itemProperties = {};
        let displayColumns = [];
        const rawItemProps = button.getAttribute('data-item-properties') || '{}';
        const rawDisplayCols = button.getAttribute('data-display-columns') || '[]';

        try {
            itemProperties = JSON.parse(rawItemProps);
        } catch (e) {
            console.error('[ArrayTableWidget] Failed to parse data-item-properties:', rawItemProps, e);
            itemProperties = {};
        }

        try {
            displayColumns = JSON.parse(rawDisplayCols);
        } catch (e) {
            console.error('[ArrayTableWidget] Failed to parse data-display-columns:', rawDisplayCols, e);
            displayColumns = [];
        }

        const tbody = document.getElementById(fieldId + '_tbody');
        if (!tbody) return;

        const currentRows = tbody.querySelectorAll('.array-table-row');
        if (currentRows.length >= maxItems) {
            const notifyFn = window.showNotification || alert;
            notifyFn(`Maximum ${maxItems} items allowed`, 'error');
            return;
        }

        const newIndex = currentRows.length;
        const row = createArrayTableRow(fieldId, fullKey, newIndex, pluginId, {}, itemProperties, displayColumns);
        tbody.appendChild(row);

        // Update button state after adding
        updateAddButtonState(fieldId);
    };

    /**
     * Remove a row from the array table
     * @param {HTMLElement} button - The remove button element
     */
    window.removeArrayTableRow = function(button) {
        const row = button.closest('tr');
        if (!row) return;

        if (confirm('Remove this item?')) {
            const tbody = row.parentElement;
            if (!tbody) return;

            // Get fieldId from tbody id (format: {fieldId}_tbody)
            const fieldId = tbody.id.replace('_tbody', '');

            row.remove();

            // Re-index remaining rows
            const rows = tbody.querySelectorAll('.array-table-row');
            rows.forEach(function(r, index) {
                r.setAttribute('data-index', index);
                r.querySelectorAll('input').forEach(function(input) {
                    const name = input.getAttribute('name');
                    if (name) {
                        input.setAttribute('name', name.replace(/\.\d+\./, '.' + index + '.'));
                    }
                });
            });

            // Update button state after removing
            updateAddButtonState(fieldId);
        }
    };

    /**
     * Initialize all array table add buttons on page load
     */
    function initArrayTableButtons() {
        const addButtons = document.querySelectorAll('button[data-field-id][data-max-items]');
        addButtons.forEach(function(button) {
            const fieldId = button.getAttribute('data-field-id');
            updateAddButtonState(fieldId);
        });
    }

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initArrayTableButtons);
    } else {
        initArrayTableButtons();
    }

    console.log('[ArrayTableWidget] Array table widget registered');
})();
