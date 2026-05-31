/**
 * Array Table Widget
 *
 * Generic table-based array-of-objects editor.
 * Handles adding, removing, and editing array items with object properties.
 * Reads column definitions from the schema's items.properties.
 *
 * Supported x-widget hints on item properties:
 *   date-picker  → <input type="date">
 *   time-picker  → <input type="time">
 *   file-upload-single → compact path input + upload button
 *   (enum values always render as <select>)
 *
 * Non-displayed properties (objects like layout/style) are stored in a hidden
 * cell and editable via the ⚙ row editor modal.
 *
 * @module ArrayTableWidget
 */

(function() {
    'use strict';

    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[ArrayTableWidget] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    // ─── Widget registration ────────────────────────────────────────────────

    window.LEDMatrixWidgets.register('array-table', {
        name: 'Array Table Widget',
        version: '2.0.0',

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

                // Collect all named form controls (input + select), skip type=hidden except
                // for boolean hidden sentinels (those end in the field name only, not .enabled).
                row.querySelectorAll('input, select').forEach(el => {
                    const name = el.getAttribute('name');
                    if (!name) return;
                    // Skip hidden inputs that are boolean sentinels (they duplicate checkboxes)
                    if (el.type === 'hidden' && !el.dataset.nestedProp) return;

                    // Nested advanced props stored in hidden cell
                    if (el.dataset.nestedProp) {
                        const propPath = el.dataset.nestedProp;
                        setNestedValue(item, propPath, coerceValue(el.value, el.dataset.propType || 'string'));
                        return;
                    }

                    // Standard display-column props: name matches fullKey.index.propName[.subKey...]
                    const match = name.match(/\.\d+\.(.+)$/);
                    if (!match) return;
                    const propPath = match[1];

                    if (el.tagName === 'SELECT') {
                        setNestedValue(item, propPath, el.value);
                    } else if (el.type === 'checkbox') {
                        setNestedValue(item, propPath, el.checked);
                    } else if (el.type === 'number') {
                        setNestedValue(item, propPath, el.value !== '' ? parseFloat(el.value) : null);
                    } else {
                        setNestedValue(item, propPath, el.value);
                    }
                });

                if (Object.keys(item).length > 0) items.push(item);
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
            if (!tbody) return;

            tbody.innerHTML = '';
            items.forEach((item, index) => {
                const row = createArrayTableRow(
                    fieldId, options.fullKey, index, options.pluginId,
                    item, options.itemProperties || {}, options.displayColumns || [],
                    options.fullItemProperties || options.itemProperties || {}
                );
                tbody.appendChild(row);
            });
            updateAddButtonState(fieldId);
        },

        handlers: {}
    });

    // ─── Helpers ────────────────────────────────────────────────────────────

    function safeSetHTML(target, html) {
        target.textContent = '';
        // createContextualFragment parses html relative to the document context
        // without executing scripts — a widely recognised safe insertion method.
        const frag = document.createRange().createContextualFragment(html);
        target.appendChild(frag);
    }

    // Keys that must never be assigned to prevent prototype pollution.
    const _FORBIDDEN_KEYS = new Set(['__proto__', 'prototype', 'constructor']);

    function setNestedValue(obj, path, value) {
        const parts = path.split('.');
        let cur = obj;
        for (let i = 0; i < parts.length - 1; i++) {
            const key = parts[i];
            if (_FORBIDDEN_KEYS.has(key)) return;
            // Use hasOwnProperty to avoid reading inherited prototype properties,
            // and defineProperty to write without triggering prototype setters.
            if (!Object.hasOwn(cur, key) ||
                typeof Object.getOwnPropertyDescriptor(cur, key).value !== 'object') {
                Object.defineProperty(cur, key, {
                    value: Object.create(null), writable: true,
                    enumerable: true, configurable: true
                });
            }
            cur = Object.getOwnPropertyDescriptor(cur, key).value;
        }
        const lastKey = parts[parts.length - 1];
        if (!_FORBIDDEN_KEYS.has(lastKey)) {
            Object.defineProperty(cur, lastKey, {
                value: value, writable: true, enumerable: true, configurable: true
            });
        }
    }

    function coerceValue(strVal, typeHint) {
        if (strVal === '' || strVal === null || strVal === undefined) return null;
        if (typeHint === 'integer') return parseInt(strVal, 10);
        if (typeHint === 'number') return parseFloat(strVal);
        if (typeHint === 'boolean') return strVal === 'true' || strVal === '1';
        // nullable integer/number: "integer|null"
        if (typeHint && typeHint.includes('integer')) return strVal !== '' ? parseInt(strVal, 10) : null;
        if (typeHint && typeHint.includes('number'))  return strVal !== '' ? parseFloat(strVal) : null;
        return strVal;
    }

    // ─── Cell rendering ─────────────────────────────────────────────────────

    /**
     * Create one <td> for a display column.
     */
    function createCell(fullKey, index, colName, colDef, colValue, pluginId) {
        const colType   = Array.isArray(colDef.type) ? colDef.type.find(t => t !== 'null') || 'string' : (colDef.type || 'string');
        const xWidget   = colDef['x-widget'] || colDef['x_widget'];
        const enumVals  = colDef.enum;
        const inputName = `${fullKey}.${index}.${colName}`;

        const cell = document.createElement('td');
        cell.className = 'px-3 py-3 whitespace-nowrap';
        cell.style.verticalAlign = 'middle';

        if (colType === 'boolean') {
            // Boolean: hidden sentinel + visible checkbox
            const hidden = document.createElement('input');
            hidden.type  = 'hidden';
            hidden.name  = inputName;
            hidden.value = 'false';
            cell.appendChild(hidden);

            const cb = document.createElement('input');
            cb.type      = 'checkbox';
            cb.name      = inputName;
            cb.checked   = Boolean(colValue);
            cb.value     = 'true';
            cb.className = 'h-4 w-4 text-blue-600';
            cell.appendChild(cb);

        } else if (colType === 'integer' || colType === 'number') {
            const inp = document.createElement('input');
            inp.type      = 'number';
            inp.name      = inputName;
            inp.value     = colValue !== null && colValue !== undefined ? colValue : '';
            if (colDef.minimum !== undefined) inp.min  = colDef.minimum;
            if (colDef.maximum !== undefined) inp.max  = colDef.maximum;
            inp.step      = colType === 'integer' ? '1' : 'any';
            inp.className = 'block w-20 px-2 py-1 border border-gray-300 rounded text-sm text-center';
            if (colDef.description) inp.title = colDef.description;
            cell.appendChild(inp);

        } else if (Array.isArray(enumVals) && enumVals.length > 0) {
            // Enum: render <select>
            cell.style.minWidth = '90px';
            const sel = document.createElement('select');
            sel.name      = inputName;
            sel.className = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm bg-white';
            enumVals.forEach(opt => {
                if (opt === null) return;
                const o = document.createElement('option');
                o.value    = opt;
                o.textContent = opt;
                if (String(colValue) === String(opt)) o.selected = true;
                sel.appendChild(o);
            });
            // If current value didn't match any option, set to first
            if (!sel.value && enumVals.length > 0) sel.value = enumVals[0];
            cell.appendChild(sel);

        } else if (xWidget === 'date-picker') {
            cell.style.minWidth = '140px';
            const inp = document.createElement('input');
            inp.type      = 'date';
            inp.name      = inputName;
            inp.value     = colValue || '';
            inp.className = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
            inp.style.minWidth = '128px';
            if (colDef.description) inp.title = colDef.description;
            cell.appendChild(inp);

        } else if (xWidget === 'time-picker') {
            cell.style.minWidth = '115px';
            const inp = document.createElement('input');
            inp.type      = 'time';
            inp.name      = inputName;
            inp.value     = colValue || '00:00';
            inp.className = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
            inp.style.minWidth = '100px';
            cell.appendChild(inp);

        } else if (xWidget === 'file-upload-single') {
            // Compact: text input (stores path) + upload button
            cell.style.minWidth = '200px';
            const wrap = document.createElement('div');
            wrap.className = 'flex items-center gap-1';

            const pathInput = document.createElement('input');
            pathInput.type        = 'text';
            pathInput.name        = inputName;
            pathInput.id          = `${fullKey}_${index}_${colName}`.replace(/\./g,'_');
            pathInput.value       = colValue || '';
            pathInput.className   = 'block px-1 py-1 border border-gray-300 rounded text-xs flex-1';
            pathInput.style.minWidth = '100px';
            pathInput.placeholder = 'path…';

            const preview = document.createElement('img');
            preview.className    = 'w-6 h-6 object-cover rounded flex-shrink-0';
            preview.style.display = colValue ? 'inline' : 'none';
            if (colValue) { preview.src = '/' + colValue; preview.onerror = () => { preview.style.display = 'none'; }; }

            const labelEl = document.createElement('label');
            labelEl.className = 'cursor-pointer flex-shrink-0 inline-flex items-center px-1 py-1 bg-blue-50 border border-blue-200 rounded text-xs text-blue-600 hover:bg-blue-100';
            labelEl.title = 'Upload image';
            labelEl.innerHTML = '<i class="fas fa-upload"></i>';

            const fileInput = document.createElement('input');
            fileInput.type   = 'file';
            fileInput.accept = 'image/png,image/jpeg,image/bmp,image/gif';
            fileInput.style.display = 'none';
            fileInput.dataset.pluginId    = pluginId;
            fileInput.dataset.targetInput = pathInput.id;
            fileInput.dataset.previewImg  = preview.id || '';
            fileInput.onchange = function(e) {
                window.handleArrayTableImageUpload(e, pathInput, preview, pluginId);
            };
            labelEl.appendChild(fileInput);

            wrap.appendChild(preview);
            wrap.appendChild(pathInput);
            wrap.appendChild(labelEl);
            cell.appendChild(wrap);

        } else {
            // Default: text input
            const inp = document.createElement('input');
            inp.type      = 'text';
            inp.name      = inputName;
            inp.value     = colValue !== null && colValue !== undefined ? colValue : '';
            inp.className = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
            if (colDef.description) inp.placeholder = colDef.description;
            if (colDef.pattern)     inp.pattern = colDef.pattern;
            if (colDef.minLength)   inp.minLength = colDef.minLength;
            if (colDef.maxLength)   inp.maxLength = colDef.maxLength;
            cell.appendChild(inp);
        }

        return cell;
    }

    /**
     * Create a hidden <td> holding flat hidden inputs for non-displayed properties
     * (including nested objects like layout/style).
     */
    function createAdvancedCell(fullKey, index, nonDisplayedProps, item) {
        const cell = document.createElement('td');
        cell.style.display = 'none';
        cell.className = 'array-table-advanced-data';
        cell.dataset.propSchema = JSON.stringify(nonDisplayedProps);

        Object.entries(nonDisplayedProps).forEach(([propName, propSchema]) => {
            const propType = Array.isArray(propSchema.type)
                ? propSchema.type.find(t => t !== 'null') || 'string'
                : (propSchema.type || 'string');

            if (propType === 'object' && propSchema.properties) {
                const nestedVal = (item && item[propName]) || {};
                Object.entries(propSchema.properties).forEach(([subName, subSchema]) => {
                    const subType = Array.isArray(subSchema.type)
                        ? subSchema.type.find(t => t !== 'null') || 'string'
                        : (subSchema.type || 'string');
                    const defaultVal = subSchema.default !== undefined ? subSchema.default : null;
                    const currentVal = nestedVal[subName] !== undefined ? nestedVal[subName] : defaultVal;

                    const hidden = document.createElement('input');
                    hidden.type  = 'hidden';
                    hidden.name  = `${fullKey}.${index}.${propName}.${subName}`;
                    hidden.value = currentVal !== null && currentVal !== undefined ? String(currentVal) : '';
                    hidden.dataset.nestedProp = `${propName}.${subName}`;
                    hidden.dataset.propType   = subType;
                    hidden.dataset.propSchema = JSON.stringify(subSchema);
                    cell.appendChild(hidden);
                });
            } else {
                const defaultVal = propSchema.default !== undefined ? propSchema.default : null;
                const currentVal = item && item[propName] !== undefined ? item[propName] : defaultVal;

                const hidden = document.createElement('input');
                hidden.type  = 'hidden';
                hidden.name  = `${fullKey}.${index}.${propName}`;
                hidden.value = currentVal !== null && currentVal !== undefined ? String(currentVal) : '';
                hidden.dataset.nestedProp = propName;
                hidden.dataset.propType   = propType;
                hidden.dataset.propSchema = JSON.stringify(propSchema);
                cell.appendChild(hidden);
            }
        });

        return cell;
    }

    // ─── Row creation ────────────────────────────────────────────────────────

    function createArrayTableRow(fieldId, fullKey, index, pluginId, item, itemProperties, displayColumns, fullItemProperties) {
        item               = item || {};
        fullItemProperties = fullItemProperties || itemProperties;

        const row = document.createElement('tr');
        row.className = 'array-table-row';
        row.setAttribute('data-index', index);

        // Visible column cells
        displayColumns.forEach(colName => {
            const colDef   = itemProperties[colName] || {};
            const colType  = Array.isArray(colDef.type) ? colDef.type.find(t => t !== 'null') || 'string' : (colDef.type || 'string');
            const colDefault = colDef.default !== undefined ? colDef.default
                : (colType === 'boolean' ? false : colType === 'time-picker' ? '00:00' : '');
            const colValue = item[colName] !== undefined ? item[colName] : colDefault;
            row.appendChild(createCell(fullKey, index, colName, colDef, colValue, pluginId));
        });

        // Determine non-displayed properties (these go into the advanced cell + edit modal)
        const nonDisplayed = {};
        Object.keys(fullItemProperties).forEach(k => {
            if (!displayColumns.includes(k) && k !== 'id') {
                nonDisplayed[k] = fullItemProperties[k];
            }
        });
        const hasAdvanced = Object.keys(nonDisplayed).length > 0;

        // Actions cell
        const actionsCell = document.createElement('td');
        actionsCell.className   = 'px-3 py-3 whitespace-nowrap text-center';
        actionsCell.style.minWidth = '90px';
        actionsCell.style.verticalAlign = 'middle';

        const removeBtn = document.createElement('button');
        removeBtn.type      = 'button';
        removeBtn.className = 'text-red-600 hover:text-red-800 px-2 py-1';
        removeBtn.onclick   = function() { window.removeArrayTableRow(this); };
        removeBtn.innerHTML = '<i class="fas fa-trash"></i>';
        actionsCell.appendChild(removeBtn);

        if (hasAdvanced) {
            const editBtn = document.createElement('button');
            editBtn.type      = 'button';
            editBtn.className = 'text-blue-500 hover:text-blue-700 px-2 py-1 ml-1';
            editBtn.title     = 'Edit advanced properties (layout, style…)';
            editBtn.onclick   = function() { window.openArrayTableRowEditor(this); };
            editBtn.innerHTML = '<i class="fas fa-sliders-h"></i>';
            actionsCell.appendChild(editBtn);
        }

        row.appendChild(actionsCell);

        // Hidden advanced data cell
        if (hasAdvanced) {
            row.appendChild(createAdvancedCell(fullKey, index, nonDisplayed, item));
        }

        return row;
    }

    // ─── Row editor modal ────────────────────────────────────────────────────

    window.openArrayTableRowEditor = function(button) {
        const row         = button.closest('tr');
        const advancedCell = row.querySelector('.array-table-advanced-data');
        if (!advancedCell) return;

        const schema = JSON.parse(advancedCell.dataset.propSchema || '{}');
        // Close any existing modal
        const existing = document.getElementById('array-row-editor-modal');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id        = 'array-row-editor-modal';
        // Use inline styles for position/dimensions — inset-0 may be purged from the CSS bundle
        // since it only appears in JS-generated markup, not in scanned templates.
        overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;z-index:9999;display:flex;align-items:center;justify-content:center;padding:1rem;background:rgba(0,0,0,0.5);';
        overlay.onclick   = function(e) { if (e.target === overlay) window.closeArrayTableRowEditor(); };

        const dialog = document.createElement('div');
        dialog.className = 'bg-white rounded-lg shadow-xl max-w-lg w-full max-h-screen overflow-y-auto';

        // Header
        safeSetHTML(dialog, `
            <div class="flex items-center justify-between px-5 py-4 border-b border-gray-200">
                <h3 class="text-base font-semibold text-gray-900">Advanced Properties</h3>
                <button type="button" onclick="window.closeArrayTableRowEditor()"
                        class="text-gray-400 hover:text-gray-600"><i class="fas fa-times"></i></button>
            </div>`;

        const body = document.createElement('div');
        body.className = 'px-5 py-4 space-y-4';

        // Render a field for each advanced property
        Object.entries(schema).forEach(([propName, propSchema]) => {
            const propType = Array.isArray(propSchema.type)
                ? propSchema.type.find(t => t !== 'null') || 'string'
                : (propSchema.type || 'string');
            const label = propSchema.title || propName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            const desc  = propSchema.description || '';

            if (propType === 'object' && propSchema.properties) {
                // Section for nested object
                const section = document.createElement('div');
                section.className = 'border border-gray-200 rounded-lg p-3';
                const _secH4 = document.createElement('h4');
                _secH4.className = 'text-sm font-medium text-gray-700 mb-3';
                _secH4.textContent = label;
                section.appendChild(_secH4);

                const grid = document.createElement('div');
                grid.className = 'grid grid-cols-2 gap-3';

                Object.entries(propSchema.properties).forEach(([subName, subSchema]) => {
                    const subType    = Array.isArray(subSchema.type) ? subSchema.type.find(t => t !== 'null') || 'string' : (subSchema.type || 'string');
                    const subLabel   = subSchema.title || subName.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                    const subDesc    = subSchema.description || '';
                    const nestedPath = `${propName}.${subName}`;

                    // Read current value from hidden input
                    const hiddenInput = advancedCell.querySelector(`[data-nested-prop="${nestedPath}"]`);
                    const currentVal  = hiddenInput ? hiddenInput.value : (subSchema.default !== undefined ? subSchema.default : '');

                    const fieldDiv = document.createElement('div');
                    const _subLbl = document.createElement('label');
                    _subLbl.className = 'block text-xs font-medium text-gray-600 mb-1';
                    _subLbl.title = subDesc;
                    _subLbl.textContent = subLabel;
                    fieldDiv.appendChild(_subLbl);
                    fieldDiv.appendChild(buildModalInput(nestedPath, subSchema, subType, currentVal));
                    grid.appendChild(fieldDiv);
                });

                section.appendChild(grid);
                body.appendChild(section);
            } else {
                // Flat property
                const hiddenInput = advancedCell.querySelector(`[data-nested-prop="${propName}"]`);
                const currentVal  = hiddenInput ? hiddenInput.value : (propSchema.default !== undefined ? propSchema.default : '');

                const fieldDiv = document.createElement('div');
                const _flatLbl = document.createElement('label');
                _flatLbl.className = 'block text-sm font-medium text-gray-700 mb-1';
                _flatLbl.title = desc;
                _flatLbl.textContent = label;
                fieldDiv.appendChild(_flatLbl);
                fieldDiv.appendChild(buildModalInput(propName, propSchema, propType, currentVal));
                body.appendChild(fieldDiv);
            }
        });

        dialog.appendChild(body);

        // Footer
        const footer = document.createElement('div');
        footer.className = 'flex justify-end gap-3 px-5 py-4 border-t border-gray-200 bg-gray-50 rounded-b-lg';
        safeSetHTML(footer, `
            <button type="button" onclick="window.closeArrayTableRowEditor()"
                    class="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-md hover:bg-gray-100">Cancel</button>
            <button type="button" id="array-row-editor-save"
                    class="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-md">Save</button>`;

        // Save handler
        footer.querySelector('#array-row-editor-save').onclick = function() {
            body.querySelectorAll('[data-modal-prop]').forEach(el => {
                const propPath   = el.dataset.modalProp;
                const targetInput = advancedCell.querySelector(`[data-nested-prop="${propPath}"]`);
                if (!targetInput) return;
                if (el.type === 'checkbox') {
                    targetInput.value = el.checked ? 'true' : 'false';
                } else {
                    targetInput.value = el.value;
                }
            });
            window.closeArrayTableRowEditor();
        };

        dialog.appendChild(footer);
        overlay.appendChild(dialog);
        document.body.appendChild(overlay);
    };

    window.closeArrayTableRowEditor = function() {
        const modal = document.getElementById('array-row-editor-modal');
        if (modal) modal.remove();
    };

    /**
     * Build a single form control for the row editor modal.
     */
    function buildModalInput(propPath, schema, propType, currentVal) {
        const xWidget  = schema['x-widget'] || schema['x_widget'];
        const enumVals = schema.enum;
        const wrap     = document.createElement('div');

        if (propType === 'boolean') {
            const cb = document.createElement('input');
            cb.type              = 'checkbox';
            cb.className         = 'h-4 w-4 text-blue-600';
            cb.checked           = currentVal === 'true' || currentVal === true || currentVal === 1;
            cb.dataset.modalProp = propPath;
            wrap.appendChild(cb);
            return wrap;
        }

        // Array[3] with x-widget color-picker → R/G/B row
        if ((propType === 'array' || xWidget === 'color-picker') &&
            (schema.minItems === 3 || schema.maxItems === 3 || xWidget === 'color-picker')) {
            const parts  = currentVal ? String(currentVal).split(',').map(s => s.trim()) : ['', '', ''];
            const rVal   = parts[0] || '';
            const gVal   = parts[1] || '';
            const bVal   = parts[2] || '';

            // Hex color picker for visual selection
            const hexVal = (rVal && gVal && bVal)
                ? '#' + [rVal, gVal, bVal].map(n => parseInt(n, 10).toString(16).padStart(2, '0')).join('')
                : '#ffffff';

            const colorRow = document.createElement('div');
            colorRow.className = 'flex items-center gap-2 flex-wrap';

            const colorPick = document.createElement('input');
            colorPick.type  = 'color';
            colorPick.value = hexVal;
            colorPick.className = 'h-8 w-10 cursor-pointer rounded border';
            colorRow.appendChild(colorPick);

            ['R', 'G', 'B'].forEach((ch, i) => {
                const lbl = document.createElement('label');
                lbl.className = 'text-xs text-gray-500';
                lbl.textContent = ch;
                const numInp = document.createElement('input');
                numInp.type      = 'number';
                numInp.min       = '0';
                numInp.max       = '255';
                numInp.step      = '1';
                numInp.value     = [rVal, gVal, bVal][i];
                numInp.className = 'w-14 px-1 py-1 border border-gray-300 rounded text-sm text-center';
                numInp.dataset.colorChannel = i;
                colorRow.appendChild(lbl);
                colorRow.appendChild(numInp);
            });

            // Hidden aggregate input that the save handler reads
            const agg = document.createElement('input');
            agg.type              = 'hidden';
            agg.value             = `${rVal},${gVal},${bVal}`;
            agg.dataset.modalProp = propPath;
            colorRow.appendChild(agg);

            // Sync: color picker → R/G/B numbers + agg
            colorPick.oninput = function() {
                const hex = colorPick.value;
                const r   = parseInt(hex.slice(1,3), 16);
                const g   = parseInt(hex.slice(3,5), 16);
                const b   = parseInt(hex.slice(5,7), 16);
                const nums = colorRow.querySelectorAll('input[data-color-channel]');
                if (nums[0]) nums[0].value = r;
                if (nums[1]) nums[1].value = g;
                if (nums[2]) nums[2].value = b;
                agg.value = `${r},${g},${b}`;
            };

            // Sync: R/G/B numbers → color picker + agg
            colorRow.querySelectorAll('input[data-color-channel]').forEach(inp => {
                inp.oninput = function() {
                    const nums = colorRow.querySelectorAll('input[data-color-channel]');
                    const r = parseInt(nums[0] ? nums[0].value : 0, 10) || 0;
                    const g = parseInt(nums[1] ? nums[1].value : 0, 10) || 0;
                    const b = parseInt(nums[2] ? nums[2].value : 0, 10) || 0;
                    colorPick.value = '#' + [r,g,b].map(n => n.toString(16).padStart(2,'0')).join('');
                    agg.value = `${r},${g},${b}`;
                };
            });

            wrap.appendChild(colorRow);
            return wrap;
        }

        if (Array.isArray(enumVals) && enumVals.length > 0) {
            const sel = document.createElement('select');
            sel.className        = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm bg-white';
            sel.dataset.modalProp = propPath;
            enumVals.forEach(opt => {
                if (opt === null) return;
                const o = document.createElement('option');
                o.value = opt; o.textContent = opt;
                if (String(currentVal) === String(opt)) o.selected = true;
                sel.appendChild(o);
            });
            wrap.appendChild(sel);
            return wrap;
        }

        if (xWidget === 'date-picker') {
            const inp = document.createElement('input');
            inp.type              = 'date';
            inp.value             = currentVal || '';
            inp.className         = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
            inp.dataset.modalProp = propPath;
            wrap.appendChild(inp);
            return wrap;
        }

        if (xWidget === 'time-picker') {
            const inp = document.createElement('input');
            inp.type              = 'time';
            inp.value             = currentVal || '00:00';
            inp.className         = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
            inp.dataset.modalProp = propPath;
            wrap.appendChild(inp);
            return wrap;
        }

        if (propType === 'integer' || propType === 'number') {
            const inp = document.createElement('input');
            inp.type              = 'number';
            inp.value             = currentVal !== '' && currentVal !== null ? currentVal : '';
            inp.className         = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
            inp.dataset.modalProp = propPath;
            if (schema.minimum !== undefined) inp.min  = schema.minimum;
            if (schema.maximum !== undefined) inp.max  = schema.maximum;
            inp.step = propType === 'integer' ? '1' : 'any';
            if (schema.description) inp.placeholder = schema.description;
            wrap.appendChild(inp);
            return wrap;
        }

        // Default: text
        const inp = document.createElement('input');
        inp.type              = 'text';
        inp.value             = currentVal || '';
        inp.className         = 'block w-full px-2 py-1 border border-gray-300 rounded text-sm';
        inp.dataset.modalProp = propPath;
        if (schema.description) inp.placeholder = schema.description;
        wrap.appendChild(inp);
        return wrap;
    }


    // ─── In-cell image upload ────────────────────────────────────────────────

    /**
     * Called from file-upload-single cells inside array-table rows.
     * Uploads the selected file and updates the path text input.
     */
    window.handleArrayTableImageUpload = async function(event, pathInput, previewImg, pluginId) {
        const file = event.target.files && event.target.files[0];
        if (!file) return;

        const notifyFn = window.showNotification || console.log;
        const allowed  = ['image/png', 'image/jpeg', 'image/bmp', 'image/gif'];
        if (!allowed.includes(file.type)) {
            notifyFn(`File type "${file.type}" not allowed`, 'error');
            return;
        }
        if (file.size > 5 * 1024 * 1024) {
            notifyFn('File exceeds 5MB limit', 'error');
            return;
        }

        const formData = new FormData();
        formData.append('plugin_id', pluginId);
        formData.append('files', file);

        try {
            const resp = await fetch('/api/v3/plugins/assets/upload', { method: 'POST', body: formData });
            if (!resp.ok) throw new Error(`Server error ${resp.status}`);
            const data = await resp.json();
            if (data.status === 'success' && data.uploaded_files && data.uploaded_files[0]) {
                const path = data.uploaded_files[0].path;
                pathInput.value = path;
                if (previewImg) { previewImg.src = '/' + path; previewImg.style.display = 'inline'; }
                notifyFn('Image uploaded', 'success');
            } else {
                throw new Error(data.message || 'Upload failed');
            }
        } catch (err) {
            notifyFn('Upload error: ' + err.message, 'error');
        } finally {
            event.target.value = '';
        }
    };

    // ─── Button helpers ──────────────────────────────────────────────────────

    function updateAddButtonState(fieldId) {
        const tbody     = document.getElementById(fieldId + '_tbody');
        const addButton = document.querySelector(`button[data-field-id="${fieldId}"]`);
        if (!tbody || !addButton) return;
        const maxItems    = parseInt(addButton.getAttribute('data-max-items'), 10);
        const currentRows = tbody.querySelectorAll('.array-table-row').length;
        const isAtMax     = currentRows >= maxItems;
        addButton.disabled    = isAtMax;
        addButton.style.opacity = isAtMax ? '0.5' : '';
    }

    window.updateArrayTableAddButtonState = updateAddButtonState;

    window.addArrayTableRow = function(button) {
        const fieldId        = button.getAttribute('data-field-id');
        const fullKey        = button.getAttribute('data-full-key');
        const maxItems       = parseInt(button.getAttribute('data-max-items'), 10);
        const pluginId       = button.getAttribute('data-plugin-id');

        let itemProperties     = {};
        let displayColumns     = [];
        let fullItemProperties = {};

        try { itemProperties     = JSON.parse(button.getAttribute('data-item-properties')     || '{}'); } catch(_e) {}
        try { displayColumns     = JSON.parse(button.getAttribute('data-display-columns')      || '[]'); } catch(_e) {}
        try { fullItemProperties = JSON.parse(button.getAttribute('data-full-item-properties') || '{}'); } catch(_e) { fullItemProperties = itemProperties; }

        const tbody = document.getElementById(fieldId + '_tbody');
        if (!tbody) return;

        const currentRows = tbody.querySelectorAll('.array-table-row').length;
        if (currentRows >= maxItems) {
            (window.showNotification || alert)(`Maximum ${maxItems} items allowed`, 'error');
            return;
        }

        const newIndex = currentRows;
        const row = createArrayTableRow(fieldId, fullKey, newIndex, pluginId, {}, itemProperties, displayColumns, fullItemProperties);
        tbody.appendChild(row);
        updateAddButtonState(fieldId);
    };

    window.removeArrayTableRow = function(button) {
        const row = button.closest('tr');
        if (!row) return;
        if (!confirm('Remove this item?')) return;

        const tbody  = row.parentElement;
        if (!tbody) return;
        const fieldId = tbody.id.replace('_tbody', '');
        row.remove();

        // Re-index remaining rows
        tbody.querySelectorAll('.array-table-row').forEach((r, index) => {
            r.setAttribute('data-index', index);
            r.querySelectorAll('input, select').forEach(el => {
                const name = el.getAttribute('name');
                if (name) el.setAttribute('name', name.replace(/\.\d+\./, '.' + index + '.'));
                // Also update data-nested-prop-based inputs (they don't have regular names needing re-index)
            });
        });

        updateAddButtonState(fieldId);
    };

    function initArrayTableButtons() {
        document.querySelectorAll('button[data-field-id][data-max-items]').forEach(button => {
            updateAddButtonState(button.getAttribute('data-field-id'));
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initArrayTableButtons);
    } else {
        initArrayTableButtons();
    }

    console.log('[ArrayTableWidget] Array table widget registered (v2.0.0)');
})();
