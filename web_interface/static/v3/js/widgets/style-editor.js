/**
 * Style Editor Widget
 *
 * One compact row per display element -- font, size, colour, X/Y nudge --
 * instead of the nested accordions the generic object renderer produces. A
 * realistic scoreboard declares seven elements with layout offsets and three
 * modes, which comes to 65 nested sections and five levels of clicking to
 * reach one per-mode font size.
 *
 * Two things about this widget are load-bearing:
 *
 * 1. It emits ordinary inputs with the same dotted names the generic
 *    renderer would produce (`customization.score_text.font`,
 *    `customization.score_text.text_color.0`, `customization.layout.
 *    score_text.x_offset`, `customization.modes.live....`). The whole
 *    save/validate/merge pipeline is therefore untouched: no hidden JSON
 *    blob, no new server-side parsing.
 *
 * 2. It is driven entirely by the schema block it is handed. It renders
 *    whatever sub-fields each element declares, so fields added to the
 *    schema later appear here without touching this file.
 *
 * Mode tabs edit `customization.modes.<mode>`, whose fields mean "inherit"
 * when blank. Blank must therefore post as empty (-> null), never as 0, or
 * every mode would pin itself to the base the first time it was saved.
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
     * Element blocks in a customization schema, in declared order.
     * `layout` and `modes` are containers, not elements.
     */
    function elementKeys(schema) {
        var props = (schema && schema.properties) || {};
        var order = schema['x-propertyOrder'] || Object.keys(props);
        return order.filter(function (k) {
            return k !== 'layout' && k !== 'modes'
                && props[k] && props[k].type === 'object' && props[k].properties;
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
        select.addEventListener('change', onChange);
        return select;
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
            var input = el('input', {
                type: 'hidden',
                name: name + '.' + i
            });
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

        var clearBtn = null;
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

    // ---- rows ------------------------------------------------------------

    /**
     * One element's row. `prefix` is the dotted path the inputs post under;
     * `optional` marks a mode layer, where every field may be left blank.
     */
    function elementRow(opts) {
        var schema = opts.schema;
        var key = opts.key;
        var prefix = opts.prefix;
        var value = opts.value;
        var fonts = opts.fonts;
        var optional = opts.optional;

        var props = ((schema.properties || {})[key] || {}).properties || {};
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

        function syncSize(select) {
            // A BDF font has exactly one usable pixel size; offering a free
            // number there is offering something that cannot take effect.
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

        var fontSelect = null;
        if (props.font) {
            fontSelect = fontControl(prefix + '.font',
                                     effective(value, [key, 'font'],
                                               props.font, optional),
                                     fonts, optional,
                                     function () { syncSize(fontSelect); });
            row.appendChild(fontSelect);
        } else {
            row.appendChild(el('span'));
        }

        if (props.font_size) {
            var cell = el('div', { class: 'flex items-center' });
            sizeInput = numberControl(prefix + '.font_size',
                                      effective(value, [key, 'font_size'],
                                                props.font_size, optional),
                                      props.font_size,
                                      optional ? 'inherit' : '');
            cell.appendChild(sizeInput);
            cell.appendChild(sizeNote);
            row.appendChild(cell);
        } else {
            row.appendChild(el('span'));
        }

        if (props.text_color) {
            row.appendChild(colourControl(prefix + '.text_color',
                                          effective(value, [key, 'text_color'],
                                                    props.text_color, optional),
                                          optional));
        } else {
            row.appendChild(el('span'));
        }

        // Offsets live in a sibling `layout` block, not inside the element,
        // which is exactly why this widget takes the whole customization
        // object rather than being registered per element.
        var layoutProps = ((schema.properties || {}).layout || {}).properties || {};
        var axes = (layoutProps[key] || {}).properties || {};
        ['x_offset', 'y_offset'].forEach(function (axis) {
            if (!axes[axis]) { row.appendChild(el('span')); return; }
            row.appendChild(numberControl(
                opts.layoutPrefix + '.' + key + '.' + axis,
                effective(value, ['layout', key, axis], axes[axis], optional),
                axes[axis],
                optional ? 'inherit' : '0'));
        });

        if (fontSelect) { syncSize(fontSelect); }
        return row;
    }

    function header(schema) {
        var cols = ['Element', 'Font', 'Size', 'Colour', 'X', 'Y'];
        var row = el('div', {
            class: 'style-editor-row style-editor-head grid gap-2 pb-1 mb-1 border-b border-gray-300'
        });
        cols.forEach(function (c) {
            row.appendChild(el('div', {
                class: 'text-xs font-semibold text-gray-500 uppercase',
                text: c
            }));
        });
        return row;
    }

    function table(opts) {
        var wrap = el('div', { class: 'style-editor-table' });
        wrap.appendChild(header(opts.schema));
        elementKeys(opts.schema).forEach(function (key) {
            wrap.appendChild(elementRow({
                schema: opts.schema,
                key: key,
                prefix: opts.prefix + '.' + key,
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
        version: '1.0.0',

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

        getValue: function (fieldId) {
            // The inputs are ordinary named form fields; the form itself is
            // the source of truth, so there is no separate value to hand back.
            return null;
        }
    });

    console.log('[StyleEditor] widget registered');
})();
