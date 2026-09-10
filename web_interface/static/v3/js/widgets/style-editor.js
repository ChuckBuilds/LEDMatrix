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

    /** The font catalog, fetched once per page. */
    function loadFonts() {
        if (FONT_CACHE) { return Promise.resolve(FONT_CACHE); }
        if (FONT_INFLIGHT) { return FONT_INFLIGHT; }
        FONT_INFLIGHT = fetch('/api/v3/fonts/catalog')
            .then(function (r) { return r.json(); })
            .then(function (payload) {
                var catalog = (payload && payload.data && payload.data.catalog) || {};
                FONT_CACHE = Object.keys(catalog).map(function (key) {
                    var entry = catalog[key];
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
                FONT_CACHE = [];
                return FONT_CACHE;
            });
        return FONT_INFLIGHT;
    }

    function el(tag, attrs, children) {
        var node = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) {
            if (k === 'class') { node.className = attrs[k]; }
            else if (k === 'text') { node.textContent = attrs[k]; }
            else if (attrs[k] !== null && attrs[k] !== undefined) {
                node.setAttribute(k, attrs[k]);
            }
        });
        (children || []).forEach(function (c) { node.appendChild(c); });
        return node;
    }

    /** Walk a nested value object by path segments. */
    function at(value, path) {
        var cur = value;
        for (var i = 0; i < path.length; i++) {
            if (cur === null || typeof cur !== 'object') { return undefined; }
            cur = cur[path[i]];
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
        return order.filter(function (k) {
            return k !== 'layout' && k !== 'modes'
                && props[k] && props[k].properties;
        });
    }

    function titleOf(schema, key) {
        var prop = (schema.properties || {})[key] || {};
        return prop.title || key.replace(/_/g, ' ');
    }

    // ---- individual controls -------------------------------------------

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
        var channels = [0, 1, 2].map(function (i) {
            var input = el('input', { type: 'hidden', name: name + '.' + i });
            if (has) { input.value = current[i]; }
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
            [1, 3, 5].forEach(function (start, i) {
                channels[i].value = parseInt(hex.substr(start, 2), 16);
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
     * The columns a table needs, derived from the schema rather than fixed.
     *
     * Two sources: the sub-fields elements declare (font, font_size,
     * text_color, visible, align) and the sub-fields their layout blocks
     * declare (x_offset, y_offset, scale).
     */
    function columnsFor(schema) {
        var props = schema.properties || {};
        var layoutProps = (props.layout || {}).properties || {};
        var seen = {};
        elementKeys(schema).forEach(function (key) {
            Object.keys((props[key] || {}).properties || {}).forEach(
                function (f) { seen[f] = 'element'; });
            Object.keys((layoutProps[key] || {}).properties || {}).forEach(
                function (f) { seen[f] = 'layout'; });
        });
        var known = COLUMN_ORDER.filter(function (f) { return seen[f]; });
        // Anything the schema declares that this file has never heard of
        // still gets a column, rather than silently vanishing.
        var extra = Object.keys(seen).filter(function (f) {
            return COLUMN_ORDER.indexOf(f) === -1;
        }).sort();
        return known.concat(extra).map(function (f) {
            return {
                key: f,
                where: seen[f],
                label: COLUMN_LABELS[f] || f.replace(/_/g, ' ')
            };
        });
    }

    /** Build the control for one cell from its schema property. */
    function control(opts) {
        var prop = opts.prop;
        var declared = prop.type;
        var types = Array.isArray(declared) ? declared : [declared];

        if (opts.key === 'font' || prop['x-widget'] === 'font-selector') {
            return fontControl(opts.name, opts.current, opts.fonts,
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
        var key = opts.key;
        var value = opts.value;
        var optional = opts.optional;

        var props = ((schema.properties || {})[key] || {}).properties || {};
        var layoutProps = ((schema.properties || {}).layout || {}).properties || {};
        var axes = (layoutProps[key] || {}).properties || {};

        var row = el('div', {
            class: 'style-editor-row grid items-center gap-2 py-1',
            'data-element': key
        });
        row.appendChild(el('div', {
            class: 'text-sm text-gray-700 style-editor-label',
            text: titleOf(schema, key)
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
            var inLayout = col.where === 'layout';
            var prop = inLayout ? axes[col.key] : props[col.key];
            if (!prop) {
                // This element does not declare that field; keep the grid
                // aligned with an empty cell.
                row.appendChild(el('span'));
                return;
            }
            var path = inLayout ? ['layout', key, col.key] : [key, col.key];
            var base = inLayout ? opts.layoutPrefix + '.' + key
                                : opts.prefix + '.' + key;
            var node = control({
                key: col.key,
                prop: prop,
                name: base + '.' + col.key,
                current: effective(value, path, prop, optional),
                optional: optional,
                fonts: opts.fonts,
                onFontChange: function () { syncSize(fontSelect); }
            });
            if (col.key === 'font') { fontSelect = node; }
            if (col.key === 'font_size') {
                sizeInput = node;
                var cell = el('div', { class: 'flex items-center' });
                cell.appendChild(node);
                cell.appendChild(sizeNote);
                node = cell;
            }
            row.appendChild(node);
        });

        if (fontSelect) { syncSize(fontSelect); }
        return row;
    }

    function header(columns) {
        var row = el('div', {
            class: 'style-editor-row style-editor-head grid gap-2 pb-1 mb-1 border-b border-gray-300'
        });
        ['Element'].concat(columns.map(function (c) { return c.label; }))
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
        var columns = columnsFor(opts.schema);
        // Sized here rather than in CSS: the column count depends on what
        // the plugin declared.
        wrap.style.gridTemplateColumns = '';
        wrap.style.setProperty('--style-editor-columns',
            'minmax(7rem, 1.4fr) ' + columns.map(function (c) {
                return COLUMN_WIDTHS[c.key] || '5rem';
            }).join(' '));
        wrap.appendChild(header(columns));
        elementKeys(opts.schema).forEach(function (key) {
            wrap.appendChild(elementRow({
                schema: opts.schema,
                key: key,
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

            loadFonts().then(function (fonts) {
                var modeProps = ((schema.properties || {}).modes || {}).properties || {};
                var modes = Object.keys(modeProps);

                var panels = {};
                var tabs = null;
                if (modes.length) {
                    tabs = el('div', { class: 'style-editor-tabs flex gap-1 mb-2' });
                    root.appendChild(tabs);
                }

                function panel(id, node) {
                    panels[id] = node;
                    node.classList.add('style-editor-panel');
                    root.appendChild(node);
                }

                function show(id) {
                    Object.keys(panels).forEach(function (k) {
                        panels[k].hidden = (k !== id);
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

                panel('__base__', table({
                    schema: schema, prefix: base, value: current,
                    fonts: fonts, optional: false
                }));
                tab('__base__', 'All modes');

                modes.forEach(function (mode) {
                    var modeSchema = modeProps[mode] || {};
                    var modeValue = at(current, ['modes', mode]) || {};
                    var node = el('div');
                    node.appendChild(el('p', {
                        class: 'text-xs text-gray-500 mb-2',
                        text: 'Anything left blank follows the "All modes" tab.'
                    }));
                    node.appendChild(table({
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

    console.log('[StyleEditor] widget registered');
})();
