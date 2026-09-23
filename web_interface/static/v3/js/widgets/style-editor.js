/**
 * Style Editor Widget
 *
 * One compact row per display element -- font, size, colour, alignment,
 * visibility, X/Y nudge, scale -- instead of the nested accordions the
 * generic object renderer produces. A realistic scoreboard declares seven
 * elements with layout offsets and three modes, which comes to 65 nested
 * sections and five levels of clicking to reach one per-mode font size.
 *
 * Three things about this widget are load-bearing:
 *
 * 1. It emits ordinary inputs with the same dotted names the generic
 *    renderer would produce (`customization.score_text.font`,
 *    `customization.score_text.text_color.0`, `customization.layout.
 *    score_text.x_offset`, `customization.modes.live....`). The whole
 *    save/validate/merge pipeline is therefore untouched: no hidden JSON
 *    blob, no new server-side parsing.
 *
 * 2. Its columns come from the schema, not from a list in here. A column
 *    appears when any element declares that field, so a plugin adding a
 *    field to its schema gets a control without this file changing, and a
 *    logo that declares only offsets and a scale gets no empty font cell.
 *
 * 3. Mode tabs edit `customization.modes.<mode>`, whose fields mean
 *    "inherit" when blank. Blank must post as empty (-> null), never as 0,
 *    or every mode would pin itself to the base the first time it was saved.
 *
 * @module StyleEditorWidget
 */

(function () {
    'use strict';

    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[StyleEditor] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    var FONT_CACHE = null;
    var FONT_INFLIGHT = null;

    /**
     * Read one own property, by a key that came from data.
     *
     * Every lookup in here is keyed by something out of a schema or a saved
     * config -- an element name, a mode name, a field name. A key of
     * `__proto__` or `constructor` would otherwise walk up the prototype
     * chain and hand back a function instead of a schema, so reads go
     * through here and misses come back undefined.
     */
    function own(obj, key) {
        if (!obj || typeof obj !== 'object') { return undefined; }
        // Via the descriptor rather than obj[key]: this is the one read that
        // cannot avoid a data-supplied key, and going through the descriptor
        // means there is no computed member access here at all.
        var descriptor = Object.getOwnPropertyDescriptor(obj, key);
        return descriptor ? descriptor.value : undefined;
    }

    /** `own`, but always an object -- for `(x || {}).properties` chains. */
    function ownObj(obj, key) {
        var found = own(obj, key);
        return (found && typeof found === 'object') ? found : {};
    }

    /** The font catalog, fetched once per page. */
    function loadFonts() {
        if (FONT_CACHE) { return Promise.resolve(FONT_CACHE); }
        if (FONT_INFLIGHT) { return FONT_INFLIGHT; }
        FONT_INFLIGHT = fetch('/api/v3/fonts/catalog')
            .then(function (r) {
                if (!r.ok) { throw new Error('font catalog fetch failed: ' + r.status); }
                return r.json();
            })
            .then(function (payload) {
                var catalog = (payload && payload.data && payload.data.catalog) || {};
                FONT_CACHE = Object.keys(catalog).map(function (key) {
                    var entry = ownObj(catalog, key);
                    return {
                        filename: entry.filename,
                        label: entry.display_name || entry.filename,
                        scalable: entry.scalable !== false,
                        nativeSize: entry.native_size || null
                    };
                }).sort(function (a, b) { return a.label.localeCompare(b.label); });
                return FONT_CACHE;
            })
            .catch(function (e) {
                console.warn('[StyleEditor] could not load the font catalog', e);
                // Leave FONT_CACHE unset and clear FONT_INFLIGHT so the next
                // call retries instead of being stuck on an empty result.
                FONT_INFLIGHT = null;
                return [];
            });
        return FONT_INFLIGHT;
    }

    function el(tag, attrs, children) {
        var node = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) {
            var v = own(attrs, k);
            if (k === 'class') { node.className = v; }
            else if (k === 'text') { node.textContent = v; }
            else if (v !== null && v !== undefined) {
                node.setAttribute(k, v);
            }
        });
        (children || []).forEach(function (c) { node.appendChild(c); });
        return node;
    }

    /** Walk a nested value object by path segments. */
    function at(value, path) {
        var cur = value;
        // Consumed rather than indexed, so no step reads path[i].
        var remaining = (path || []).slice();
        while (remaining.length) {
            if (cur === null || typeof cur !== 'object') { return undefined; }
            cur = own(cur, remaining.shift());
        }
        return cur;
    }

    /**
     * What a control should show: the configured value if there is one,
     * else the schema default.
     *
     * The base tab must fall back to the default rather than leaving the
     * control empty. An empty <select> shows its first option, which for
     * fonts is whatever sorts first alphabetically -- so an untouched
     * scoreboard claimed every element used 10x20.bdf, and the size control
     * then locked itself to that bitmap font's fixed size. A mode tab is the
     * opposite: blank there means inherit, so it must stay blank.
     */
    function effective(value, path, prop, optional) {
        var v = at(value, path);
        if (v !== undefined && v !== null) { return v; }
        if (optional) { return undefined; }
        return prop ? prop.default : undefined;
    }

    /**
     * Element blocks in a customization schema, in declared order.
     * `layout` and `modes` are containers, not elements.
     */
    function elementKeys(schema) {
        var props = (schema && schema.properties) || {};
        var order = schema['x-propertyOrder'] || Object.keys(props);
        var shaped = order.filter(function (k) {
            return k !== 'layout' && k !== 'modes'
                && own(props, k) && ownObj(props, k).properties;
        });
        // Core marks the blocks it recognises as styling. Prefer that: a
        // customization block can also hold a feature of its own -- football
        // keeps favorite_result_colors there -- and treating one as an element
        // gives every row that feature's fields as extra columns.
        var managed = shaped.filter(function (k) {
            return ownObj(props, k)['x-style-managed'] === true;
        });
        // Nothing marked means the schema never went through expansion, so
        // fall back to the shape test rather than rendering an empty table.
        return managed.length ? managed : shaped;
    }

    function titleOf(schema, key) {
        var prop = ownObj(schema.properties || {}, key);
        return prop.title || key.replace(/_/g, ' ');
    }

    // ---- individual controls -------------------------------------------

    /**
     * Fonts this field can actually honour.
     *
     * A bitmap (BDF) face renders at the one size baked into the file and
     * ignores the size setting, so an element that caps size at 16 can
     * still be handed a 27px face and overflow a 32px panel. The cap comes
     * from the element's own font_size.maximum, so this hides exactly the
     * faces that cannot meet the limit the plugin already declared --
     * rather than a list curated by hand, which is what these fields used
     * to carry and why an uploaded font could never appear in one.
     */
    function usableFonts(fonts, maxFixedSize, current) {
        if (!maxFixedSize) { return fonts; }
        return fonts.filter(function (f) {
            // Keep the font already saved on this element even if it no
            // longer fits the cap -- dropping it would leave the select
            // with nothing chosen and silently misrepresent the config.
            if (current && f.filename === current) { return true; }
            if (f.scalable !== false) { return true; }
            // An unknown native size is not evidence it is too big.
            if (!f.nativeSize) { return true; }
            return f.nativeSize <= maxFixedSize;
        });
    }

    function fontControl(name, current, fonts, optional, onChange) {
        var select = el('select', {
            name: name,
            class: 'form-control text-sm style-editor-font'
        });
        if (optional) {
            select.appendChild(el('option', { value: '', text: 'Inherit' }));
        }
        fonts.forEach(function (f) {
            var opt = el('option', { value: f.filename, text: f.label });
            opt.dataset.scalable = f.scalable ? '1' : '0';
            opt.dataset.nativeSize = f.nativeSize || '';
            select.appendChild(opt);
        });
        if (current) { select.value = current; }
        if (onChange) { select.addEventListener('change', onChange); }
        return select;
    }

    function enumControl(name, current, choices, optional) {
        var select = el('select', {
            name: name,
            class: 'form-control text-sm style-editor-enum'
        });
        if (optional) {
            select.appendChild(el('option', { value: '', text: 'Inherit' }));
        }
        choices.forEach(function (c) {
            select.appendChild(el('option', {
                value: c,
                text: String(c).replace(/_/g, ' ')
            }));
        });
        if (current !== undefined && current !== null) { select.value = current; }
        return select;
    }

    function booleanControl(name, current, optional) {
        if (optional) {
            // Three states, not two: on, off, and "follow the base". A bare
            // checkbox cannot say the third, and an unchecked box would read
            // as "hide this in live mode" rather than "no preference".
            return enumControl(name, boolAsString(current),
                               ['true', 'false'], true);
        }
        // An unchecked checkbox posts nothing at all, so the hidden field
        // carries the value and the checkbox drives it. The save path turns
        // "true"/"false" into a real boolean.
        var wrap = el('div', { class: 'flex items-center' });
        var hidden = el('input', { type: 'hidden', name: name });
        var box = el('input', { type: 'checkbox', class: 'style-editor-check' });
        box.checked = current !== false;
        hidden.value = box.checked ? 'true' : 'false';
        box.addEventListener('change', function () {
            hidden.value = box.checked ? 'true' : 'false';
        });
        wrap.appendChild(box);
        wrap.appendChild(hidden);
        return wrap;
    }

    function boolAsString(value) {
        if (value === true) { return 'true'; }
        if (value === false) { return 'false'; }
        return undefined;
    }

    function numberControl(name, current, prop, placeholder) {
        var input = el('input', {
            type: 'number',
            name: name,
            class: 'form-control text-sm style-editor-size',
            placeholder: placeholder || ''
        });
        if (prop && prop.minimum !== undefined) { input.min = prop.minimum; }
        if (prop && prop.maximum !== undefined) { input.max = prop.maximum; }
        if (prop && prop.type
            && String(prop.type).indexOf('number') !== -1) {
            input.step = 'any';
        }
        // Blank stays blank. For a mode field that is the inherit sentinel;
        // writing 0 here would pin the mode to the base on the next save.
        if (current !== undefined && current !== null) { input.value = current; }
        return input;
    }

    function toHex(rgb) {
        if (!Array.isArray(rgb) || rgb.length < 3) { return '#ffffff'; }
        return '#' + rgb.slice(0, 3).map(function (c) {
            var v = Math.max(0, Math.min(255, parseInt(c, 10) || 0));
            return ('0' + v.toString(16)).slice(-2);
        }).join('');
    }

    function colourControl(name, current, optional) {
        // Three number inputs carry the value (name.0/.1/.2 is what the save
        // path recombines); the colour swatch is the human control and just
        // drives them.
        var wrap = el('div', { class: 'flex items-center gap-1' });
        var has = Array.isArray(current) && current.length >= 3;
        var startValues = has ? current.slice(0, 3) : [];
        var channels = startValues.concat([null, null, null])
            .slice(0, 3)
            .map(function (channelValue, i) {
                var input = el('input', {
                    type: 'hidden', name: name + '.' + i
                });
                if (has) { input.value = channelValue; }
                return input;
            });

        // A disabled input is not submitted at all, which is how an unset
        // optional colour says nothing rather than posting three empty
        // strings. The server copes with those too, but not posting them is
        // both clearer and one less thing depending on that.
        function setSubmitted(on) {
            channels.forEach(function (c) { c.disabled = !on; });
        }
        setSubmitted(!optional || has);

        var clearBtn = null;
        var swatch = el('input', {
            type: 'color',
            class: 'style-editor-colour',
            value: has ? toHex(current) : '#ffffff'
        });
        swatch.addEventListener('input', function () {
            var hex = swatch.value;
            channels.forEach(function (channel, i) {
                channel.value = parseInt(hex.substr(1 + i * 2, 2), 16);
            });
            setSubmitted(true);
            if (clearBtn) { clearBtn.classList.remove('hidden'); }
        });
        wrap.appendChild(swatch);
        channels.forEach(function (c) { wrap.appendChild(c); });

        if (optional) {
            // A mode colour must be able to go back to "inherit", which means
            // posting nothing at all -- an <input type=color> has no empty
            // state of its own.
            clearBtn = el('button', {
                type: 'button',
                class: 'text-xs text-gray-500 hover:text-gray-800 style-editor-clear',
                title: 'Inherit the colour above',
                text: '×'
            });
            if (!has) { clearBtn.classList.add('hidden'); }
            clearBtn.addEventListener('click', function () {
                channels.forEach(function (c) { c.value = ''; });
                setSubmitted(false);
                swatch.value = '#ffffff';
                clearBtn.classList.add('hidden');
            });
            wrap.appendChild(clearBtn);
        }
        return wrap;
    }

    // ---- columns ---------------------------------------------------------

    var COLUMN_ORDER = ['font', 'font_size', 'text_color', 'align', 'visible',
                        'x_offset', 'y_offset', 'scale'];
    var COLUMN_LABELS = {
        font: 'Font', font_size: 'Size', text_color: 'Colour',
        align: 'Align', visible: 'Show',
        x_offset: 'X', y_offset: 'Y', scale: 'Scale'
    };
    var COLUMN_WIDTHS = {
        font: 'minmax(8rem, 2fr)', text_color: '4.5rem', align: '6rem',
        visible: '3.5rem'
    };

    /**
     * Where an element's offsets live in the layout block, or null.
     *
     * A hand-written schema does not line up: football styles 'score_text'
     * but positions it under 'score'. Core resolves that through the same
     * alias map the renderer reads offsets with and records the answer as
     * x-layout-key, so the rules exist once, in element_style.py. Without
     * the annotation both blocks share one key, as the compact declaration
     * does.
     */
    function layoutKeyFor(schema, key) {
        var props = (schema && schema.properties) || {};
        var layoutProps = ownObj(props, 'layout').properties || {};
        var declared = ownObj(props, key)['x-layout-key'];
        // Only an object-shaped entry holds offsets. A leaf straight under
        // layout (a show_logo toggle) is a control of its own, so it is never
        // claimed here and always gets a position row.
        if (typeof declared === 'string' && ownObj(layoutProps, declared).properties) {
            return declared;
        }
        return ownObj(layoutProps, key).properties ? key : null;
    }

    /** One row per styled element, paired with its offsets if it has any. */
    function styleRows(schema) {
        return elementKeys(schema).map(function (key) {
            return { key: key, layoutKey: layoutKeyFor(schema, key) };
        });
    }

    /**
     * One row per positioned thing that has no style block of its own.
     *
     * Football positions both logos, timeouts, possession, down-and-distance,
     * the date, the time and the records, none of which has a font or a
     * colour. The editor takes the whole layout section over, so anything
     * not drawn here has no control at all.
     */
    function positionRows(schema) {
        var layout = ownObj((schema && schema.properties) || {}, 'layout');
        var layoutProps = layout.properties || {};
        var claimed = styleRows(schema).map(function (r) { return r.layoutKey; });
        var order = layout['x-propertyOrder'] || Object.keys(layoutProps);
        return order.filter(function (lk) {
            var entry = own(layoutProps, lk);
            // Object-shaped entries carry offsets; a leaf is itself the
            // control. Both need a row here, because the editor removes the
            // fallback's whole layout section.
            return entry && typeof entry === 'object' && claimed.indexOf(lk) === -1;
        }).map(function (lk) {
            return { key: null, layoutKey: lk };
        });
    }

    function layoutTitleOf(schema, layoutKey) {
        var layoutProps = ownObj((schema && schema.properties) || {}, 'layout').properties || {};
        return ownObj(layoutProps, layoutKey).title || layoutKey.replace(/_/g, ' ');
    }

    /**
     * The columns a table needs, derived from the schema rather than fixed.
     *
     * Three sources: the sub-fields a row's style block declares (font,
     * font_size, text_color, visible, align), the sub-fields of its layout
     * entry (x_offset, y_offset, scale), and a layout entry whose own value
     * *is* the field -- a show_logo toggle straight under layout has no x/y
     * object underneath, so it gets a column keyed to itself.
     *
     * `rows` defaults to every row the schema produces, style and position.
     */
    function columnsFor(schema, rows) {
        var props = schema.properties || {};
        var layoutProps = ownObj(props, 'layout').properties || {};
        rows = rows || styleRows(schema).concat(positionRows(schema));
        // A Map, not an object: the keys are field names out of a schema, so
        // a field literally named "constructor" is a column like any other
        // and never touches a prototype.
        var seen = new Map();
        rows.forEach(function (row) {
            if (row.key) {
                Object.keys(ownObj(props, row.key).properties || {}).forEach(
                    function (f) { seen.set(f, 'element'); });
            }
            if (!row.layoutKey) { return; }
            var entry = own(layoutProps, row.layoutKey);
            if (entry && typeof entry === 'object' && entry.properties) {
                Object.keys(entry.properties).forEach(
                    function (f) { seen.set(f, 'layout'); });
            } else if (entry && typeof entry === 'object') {
                // A leaf. Namespaced so an unrelated column sharing its name
                // -- another entry's "scale" axis, say -- can never shadow it
                // and leave this row's only control blank.
                seen.set('layout-leaf:' + row.layoutKey, 'layout-leaf');
            }
        });
        var known = COLUMN_ORDER.filter(function (f) { return seen.get(f); });
        // Anything the schema declares that this file has never heard of
        // still gets a column, rather than silently vanishing.
        var extra = Array.from(seen.keys()).filter(function (f) {
            return COLUMN_ORDER.indexOf(f) === -1;
        }).sort();
        return known.concat(extra).map(function (f) {
            var isLeaf = f.indexOf('layout-leaf:') === 0;
            var fieldKey = isLeaf ? f.slice('layout-leaf:'.length) : f;
            return {
                key: fieldKey,
                where: seen.get(f),
                label: own(COLUMN_LABELS, fieldKey) || fieldKey.replace(/_/g, ' ')
            };
        });
    }

    /** Build the control for one cell from its schema property. */
    function control(opts) {
        var prop = opts.prop;
        var declared = prop.type;
        var types = Array.isArray(declared) ? declared : [declared];
        var xOptions = prop['x-options'] || prop['x_options'] || {};
        var cap = Number(xOptions.maxFixedSize) || opts.maxFixedSize || null;

        if (opts.key === 'font' || prop['x-widget'] === 'font-selector') {
            return fontControl(opts.name, opts.current,
                               usableFonts(opts.fonts, cap, opts.current),
                               opts.optional, opts.onFontChange);
        }
        if (types.indexOf('boolean') !== -1) {
            return booleanControl(opts.name, opts.current, opts.optional);
        }
        if (types.indexOf('array') !== -1) {
            return colourControl(opts.name, opts.current, opts.optional);
        }
        if (Array.isArray(prop.enum)) {
            return enumControl(opts.name, opts.current, prop.enum,
                               opts.optional);
        }
        return numberControl(opts.name, opts.current, prop,
                             opts.optional ? 'inherit' : '');
    }

    function elementRow(opts) {
        var schema = opts.schema;
        var key = opts.row.key;              // null for a position-only row
        var layoutKey = opts.row.layoutKey;  // null for a style-only row
        var value = opts.value;
        var optional = opts.optional;

        var props = key ? (ownObj(schema.properties || {}, key).properties || {}) : {};
        var layoutProps = ownObj(schema.properties || {}, 'layout').properties || {};
        var axes = layoutKey ? (ownObj(layoutProps, layoutKey).properties || {}) : {};

        var row = el('div', {
            class: 'style-editor-row grid items-center gap-2 py-1',
            'data-element': key || ('layout.' + layoutKey)
        });
        row.appendChild(el('div', {
            class: 'text-sm text-gray-700 style-editor-label',
            text: key ? titleOf(schema, key) : layoutTitleOf(schema, layoutKey)
        }));

        var sizeInput = null;
        var sizeNote = el('span', { class: 'text-xs text-gray-500 ml-1' });
        var fontSelect = null;

        function syncSize(select) {
            // A BDF font has exactly one usable pixel size; offering a free
            // number there offers something that cannot take effect.
            if (!sizeInput || !select) { return; }
            var opt = select.options[select.selectedIndex];
            var scalable = !opt || opt.dataset.scalable !== '0';
            if (scalable) {
                sizeInput.disabled = false;
                sizeInput.title = '';
                sizeNote.textContent = '';
            } else {
                sizeInput.disabled = true;
                sizeInput.value = opt.dataset.nativeSize || '';
                sizeInput.title = 'This is a bitmap font; it renders at one fixed size.';
                sizeNote.textContent = 'fixed';
            }
        }

        opts.columns.forEach(function (col) {
            var isLeaf = col.where === 'layout-leaf';
            var inLayout = col.where === 'layout';
            // A leaf column belongs to the one position row named after it;
            // every other row leaves that cell blank.
            var prop = isLeaf ? (col.key === layoutKey ? own(layoutProps, layoutKey) : null)
                     : inLayout ? own(axes, col.key) : own(props, col.key);
            var cell;
            if (!prop) {
                // This element does not declare that field; keep the grid
                // aligned with an empty cell.
                row.appendChild(el('span'));
                return;
            }
            var path = isLeaf ? ['layout', layoutKey]
                     : inLayout ? ['layout', layoutKey, col.key] : [key, col.key];
            // Posted under the key the schema declares, never the style key:
            // a plugin's own offset reader looks up layout.score, so a value
            // saved as layout.score_text would be kept and never drawn. A
            // leaf is the field itself, so its name has no sub-field suffix --
            // the same name the generic renderer would have posted.
            var base = (isLeaf || inLayout) ? opts.layoutPrefix + '.' + layoutKey
                                            : opts.prefix + '.' + key;
            var node = control({
                key: col.key,
                prop: prop,
                name: isLeaf ? base : base + '.' + col.key,
                current: effective(value, path, prop, optional),
                optional: optional,
                fonts: opts.fonts,
                // The element's declared size ceiling is what a fixed-size
                // font has to fit under.
                maxFixedSize: (props.font_size || {}).maximum || null,
                onFontChange: function () { syncSize(fontSelect); }
            });
            if (col.key === 'font') { fontSelect = node; }
            if (col.key === 'font_size') {
                sizeInput = node;
                cell = el('div', { class: 'flex items-center' });
                cell.appendChild(node);
                cell.appendChild(sizeNote);
                node = cell;
            }
            row.appendChild(node);
        });

        if (fontSelect) { syncSize(fontSelect); }
        return row;
    }

    function header(columns, firstLabel) {
        var row = el('div', {
            class: 'style-editor-row style-editor-head grid gap-2 pb-1 mb-1 border-b border-gray-300'
        });
        [firstLabel || 'Element'].concat(columns.map(function (c) { return c.label; }))
            .forEach(function (label) {
                row.appendChild(el('div', {
                    class: 'text-xs font-semibold text-gray-500 uppercase',
                    text: label
                }));
            });
        return row;
    }

    function table(opts) {
        var wrap = el('div', { class: 'style-editor-table' });
        // Columns per table, so the positions table does not inherit a Font
        // column and the style table does not grow a "home x offset" one.
        var columns = columnsFor(opts.schema, opts.rows);
        // Sized here rather than in CSS: the column count depends on what
        // the plugin declared.
        wrap.style.gridTemplateColumns = '';
        wrap.style.setProperty('--style-editor-columns',
            'minmax(7rem, 1.4fr) ' + columns.map(function (c) {
                return COLUMN_WIDTHS[c.key] || '5rem';
            }).join(' '));
        wrap.appendChild(header(columns, opts.firstLabel));
        opts.rows.forEach(function (row) {
            wrap.appendChild(elementRow({
                schema: opts.schema,
                row: row,
                columns: columns,
                prefix: opts.prefix,
                layoutPrefix: opts.prefix + '.layout',
                value: opts.value,
                fonts: opts.fonts,
                optional: opts.optional
            }));
        });
        return wrap;
    }

    /** One panel's tables: the styled elements, then anything only positioned. */
    function panelBody(opts) {
        var body = el('div');
        var shared = {
            schema: opts.schema, prefix: opts.prefix, value: opts.value,
            fonts: opts.fonts, optional: opts.optional
        };
        var styled = styleRows(opts.schema);
        if (styled.length) {
            body.appendChild(table(Object.assign({ rows: styled }, shared)));
        }
        var positioned = positionRows(opts.schema);
        if (positioned.length) {
            body.appendChild(el('div', {
                class: 'text-xs font-semibold text-gray-500 uppercase mt-4 mb-1 style-editor-subhead',
                text: 'Other positions'
            }));
            body.appendChild(table(Object.assign(
                { rows: positioned, firstLabel: 'Item' }, shared)));
        }
        return body;
    }

    // ---- widget ----------------------------------------------------------

    window.LEDMatrixWidgets.register('style-editor', {
        name: 'Style Editor',
        version: '1.1.0',

        render: function (container, config, value, options) {
            var schema = (config && config.schema) || {};
            var base = (options && options.name) || 'customization';
            var current = value || {};

            container.innerHTML = '';
            var root = el('div', { class: 'style-editor' });
            container.appendChild(root);

            // Published synchronously, because the host reads it the moment
            // this returns while the panels below wait on the font catalog.
            // layout and modes count as ours: their fields appear as columns
            // in these rows, so leaving them to the generic renderer would
            // post every offset twice from two different controls.
            var owned = elementKeys(schema);
            if (ownObj(schema.properties || {}, 'layout').properties) {
                owned = owned.concat(['layout']);
            }
            if (ownObj(schema.properties || {}, 'modes').properties) {
                owned = owned.concat(['modes']);
            }
            container.dataset.ownedKeys = owned.join(',');

            loadFonts().then(function (fonts) {
                var modeProps = ownObj(schema.properties || {}, 'modes').properties || {};
                var modes = Object.keys(modeProps);

                // A list, not a keyed object: the ids are mode names out of
                // the schema, and nothing here needs a lookup by key.
                var panels = [];
                var tabs = null;
                if (modes.length) {
                    tabs = el('div', { class: 'style-editor-tabs flex gap-1 mb-2' });
                    root.appendChild(tabs);
                }

                function panel(id, node) {
                    panels.push({ id: id, node: node });
                    node.classList.add('style-editor-panel');
                    root.appendChild(node);
                }

                function show(id) {
                    panels.forEach(function (p) {
                        p.node.hidden = (p.id !== id);
                    });
                    if (!tabs) { return; }
                    Array.prototype.forEach.call(tabs.children, function (b) {
                        b.classList.toggle('is-active', b.dataset.panel === id);
                    });
                }

                function tab(id, label) {
                    if (!tabs) { return; }
                    var b = el('button', {
                        type: 'button',
                        class: 'style-editor-tab text-sm px-3 py-1 rounded',
                        text: label
                    });
                    b.dataset.panel = id;
                    b.addEventListener('click', function () { show(id); });
                    tabs.appendChild(b);
                }

                panel('__base__', panelBody({
                    schema: schema, prefix: base, value: current,
                    fonts: fonts, optional: false
                }));
                tab('__base__', 'All modes');

                modes.forEach(function (mode) {
                    var modeSchema = ownObj(modeProps, mode);
                    var modeValue = at(current, ['modes', mode]) || {};
                    var node = el('div');
                    node.appendChild(el('p', {
                        class: 'text-xs text-gray-500 mb-2',
                        text: 'Anything left blank follows the "All modes" tab.'
                    }));
                    node.appendChild(panelBody({
                        schema: modeSchema,
                        prefix: base + '.modes.' + mode,
                        value: modeValue,
                        fonts: fonts,
                        optional: true
                    }));
                    panel(mode, node);
                    tab(mode, modeSchema.title || mode);
                });

                show('__base__');
            });
        },

        getValue: function () {
            // The inputs are ordinary named form fields; the form itself is
            // the source of truth, so there is no separate value to hand back.
            return null;
        }
    });
})();
