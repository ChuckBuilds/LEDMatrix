/**
 * ListFilter — shared search / filter / sort controller for the card-grid
 * sections of the Plugin Manager.
 *
 * The Installed Plugins, Plugin Store and Starlark Apps sections all need the
 * same machinery: debounced text search over a few fields, a handful of filter
 * axes, a sort dropdown, an active-filter count with a Clear button, and a
 * re-render. This owns that machinery; the caller keeps ownership of its own
 * card markup via the `render` callback.
 *
 * Usage:
 *
 *   const ctl = ListFilter.create({
 *       getItems: () => window.installedPlugins || [],
 *       render:   (visible, total) => renderCards(visible, total),
 *       search:   { el: 'my-search', fields: ['name', 'id', 'tags'] },
 *       sort:     { el: 'my-sort', default: 'a-z', comparators: { 'a-z': fn } },
 *       controls: [{ type: 'pills', el: '#my-pills', attr: 'data-my-filter',
 *                    key: 'filter', default: 'all', test: (item, v) => true }],
 *       clearEl: 'my-clear',
 *   });
 *   ctl.bind();    // idempotent — safe to call after every HTMX partial swap
 *   ctl.apply();   // filter + sort + render
 *
 * Optional `pagination` slices the result set and renders page controls; the
 * `render` callback then receives just the current page. Optional `persist`
 * takes read/write callbacks so the caller — not this helper — owns its
 * storage keys.
 *
 * Element references are DOM ids, except `controls[].el` which is a CSS
 * selector for the pill container.
 */
const ListFilter = (function () {
    'use strict';

    function debounce(fn, wait) {
        let timer = null;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), wait);
        };
    }

    function byId(id) {
        return id ? document.getElementById(id) : null;
    }

    // Filter axes compare against their default to decide "is this axis active",
    // so null/undefined/'' must not be conflated with a real selection.
    function sameValue(a, b) {
        if (a === b) return true;
        if (a === null || a === undefined) return b === null || b === undefined;
        return false;
    }

    // Build the lowercased search haystack. Array fields (e.g. tags) are
    // flattened in, matching the existing store/starlark search behaviour.
    //
    // Values are read out of a Map rather than via item[field], which keeps
    // static analysers from flagging a computed member access as an
    // object-injection sink. Iteration follows `fields`, NOT the object's own
    // key order: the fields are concatenated, so their order decides which
    // values end up adjacent, and a multi-word query can span a field boundary.
    function haystack(item, fields) {
        if (!item) return '';
        const values = new Map(Object.entries(item));
        const parts = [];
        (fields || []).forEach(field => {
            const value = values.get(field);
            if (Array.isArray(value)) {
                value.forEach(v => { if (v) parts.push(String(v)); });
            } else if (value) {
                parts.push(String(value));
            }
        });
        return parts.join(' ').toLowerCase();
    }

    function create(config) {
        const cfg = config || {};
        const searchCfg = cfg.search || null;
        const sortCfg = cfg.sort || null;
        const controls = Array.isArray(cfg.controls) ? cfg.controls : [];
        const pageCfg = cfg.pagination || null;
        const persistCfg = cfg.persist || null;
        const idOf = typeof cfg.idOf === 'function' ? cfg.idOf : (item => item && item.id);

        // Defaults double as the "inactive" value for each axis.
        const defaults = {};
        if (searchCfg) {
            defaults.search = '';      // trimmed — what filtering and activeCount use
            defaults.searchRaw = '';   // exactly what the user typed — what the input shows
        }
        if (sortCfg) defaults.sort = sortCfg.default !== undefined ? sortCfg.default : 'a-z';
        controls.forEach(c => {
            defaults[c.key] = c.default !== undefined ? c.default : null;
        });

        const state = Object.assign({}, defaults);

        // page/perPage sit outside `defaults` on purpose: Clear Filters returns
        // to page 1 but must NOT reset a per-page size the user chose.
        if (pageCfg) {
            state.page = 1;
            state.perPage = pageCfg.defaultPerPage || 12;
        }

        // Seed persisted values. The caller supplies read()/write() so storage
        // keys stay where they always were.
        if (persistCfg && typeof persistCfg.read === 'function') {
            const saved = persistCfg.read() || {};
            if (sortCfg && saved.sort !== undefined && saved.sort !== null) state.sort = saved.sort;
            if (pageCfg && saved.perPage) state.perPage = saved.perPage;
        }

        function persist() {
            if (persistCfg && typeof persistCfg.write === 'function') persistCfg.write(state);
        }

        // Ids that stay visible even when they no longer match the active
        // filters. Populated by the caller when the user acts on a card (e.g.
        // toggling a plugin off while filtering by Enabled) so the card they
        // just clicked doesn't vanish underneath the cursor. Cleared as soon as
        // the user touches the toolbar.
        const sticky = new Set();

        function activeCount() {
            let n = 0;
            if (searchCfg && state.search) n++;
            if (sortCfg && !sameValue(state.sort, defaults.sort)) n++;
            controls.forEach(c => {
                if (!sameValue(state[c.key], defaults[c.key])) n++;
            });
            return n;
        }

        function matches(item) {
            if (searchCfg && state.search) {
                if (!haystack(item, searchCfg.fields).includes(state.search.toLowerCase())) {
                    return false;
                }
            }
            for (const c of controls) {
                const value = state[c.key];
                if (sameValue(value, defaults[c.key])) continue;   // axis inactive
                if (typeof c.test === 'function' && !c.test(item, value)) return false;
            }
            return true;
        }

        function compute() {
            const all = (typeof cfg.getItems === 'function' ? cfg.getItems() : null) || [];
            const total = all.length;
            const list = all.filter(item => {
                if (sticky.size > 0 && sticky.has(idOf(item))) return true;
                return matches(item);
            });

            if (sortCfg && sortCfg.comparators) {
                // An unrecognised sort key falls back to the default comparator,
                // matching the switch-with-default the store code used.
                const cmp = sortCfg.comparators[state.sort] || sortCfg.comparators[defaults.sort];
                if (typeof cmp === 'function') list.sort(cmp);
            }
            return { list: list, total: total };
        }

        // Reflect current state back onto the controls, so programmatic changes
        // and a fresh partial swap both land on a correctly-lit toolbar.
        function syncControls() {
            if (searchCfg) {
                // The toolbar markup is rebuilt on every HTMX partial swap while
                // this controller (and its state) survives — put the text back.
                const el = byId(searchCfg.el);
                const text = state.searchRaw !== undefined ? state.searchRaw : state.search;
                if (el && el.value !== text) el.value = text;
            }
            if (sortCfg) {
                const el = byId(sortCfg.el);
                if (el && el.value !== state.sort) el.value = state.sort;
            }
            if (pageCfg && pageCfg.perPageEl) {
                const el = byId(pageCfg.perPageEl);
                if (el && el.value !== String(state.perPage)) el.value = String(state.perPage);
            }
            controls.forEach(c => {
                const value = state[c.key];
                if (c.type === 'pills') {
                    const container = document.querySelector(c.el);
                    if (!container) return;
                    container.querySelectorAll('[' + c.attr + ']').forEach(btn => {
                        const on = btn.getAttribute(c.attr) === String(value);
                        btn.setAttribute('data-active', on ? 'true' : 'false');
                        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
                    });
                } else if (c.type === 'select') {
                    const el = byId(c.el);
                    if (el && el.value !== (value === null ? '' : value)) {
                        el.value = value === null ? '' : value;
                    }
                } else if (c.type === 'cycle') {
                    const btn = byId(c.el);
                    // The caller renders cycle buttons so each section keeps its
                    // own label/icon/class treatment.
                    if (btn && typeof c.render === 'function') c.render(btn, value);
                }
            });
        }

        function updateChrome(list, total) {
            const n = activeCount();

            const countEl = byId(cfg.countEl);
            if (countEl && typeof cfg.countFormat === 'function') {
                countEl.textContent = cfg.countFormat(list.length, total, n > 0);
            }

            const activeEl = byId(cfg.activeCountEl);
            if (activeEl) {
                activeEl.classList.toggle('hidden', n === 0);
                activeEl.textContent = n + ' filter' + (n !== 1 ? 's' : '') + ' active';
            }

            const clearEl = byId(cfg.clearEl);
            if (clearEl) clearEl.classList.toggle('hidden', n === 0);

            if (searchCfg && searchCfg.clearEl) {
                const searchClear = byId(searchCfg.clearEl);
                if (searchClear) searchClear.classList.toggle('hidden', !state.search);
            }

            syncControls();

            if (typeof cfg.onChrome === 'function') cfg.onChrome(state, list, total);
        }

        // Page-number strip with leading/trailing ellipsis, producing the same
        // controls the plugin store has always rendered.
        //
        // Built with createElement rather than by concatenating an HTML string.
        // Nothing interpolated here is user-controlled — only page integers and
        // these class constants — but assembling markup into innerHTML is the
        // pattern static analysers flag as an XSS sink, and building nodes is no
        // less clear. It also lets each button own its listener directly instead
        // of re-querying the container afterwards.
        const PAGE_BTN_CLASS = 'px-3 py-1 text-sm rounded-md border transition-colors';
        const PAGE_ACTIVE_CLASS = 'bg-blue-600 text-white border-blue-600';
        const PAGE_NORMAL_CLASS = 'bg-white text-gray-700 border-gray-300 hover:bg-gray-100 cursor-pointer';
        const PAGE_DISABLED_CLASS = 'bg-gray-100 text-gray-400 border-gray-200 cursor-not-allowed';

        function renderPagination(containerId, totalPages, currentPage) {
            const container = byId(containerId);
            if (!container) return;

            // textContent = '' drops the previous strip without parsing markup.
            container.textContent = '';
            if (totalPages <= 1) return;

            const goTo = target => {
                if (target >= 1 && target <= totalPages && target !== currentPage) {
                    state.page = target;
                    // Page moves re-slice only; filters and sort are unchanged.
                    apply(true);
                    const grid = byId(pageCfg && pageCfg.scrollToEl);
                    if (grid && typeof grid.scrollIntoView === 'function') {
                        grid.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    }
                }
            };

            const addPageButton = (label, target, variant) => {
                const btn = document.createElement('button');
                btn.className = PAGE_BTN_CLASS + ' ' + (
                    variant === 'active' ? PAGE_ACTIVE_CLASS
                    : variant === 'disabled' ? PAGE_DISABLED_CLASS
                    : PAGE_NORMAL_CLASS);
                btn.setAttribute('data-list-page', String(target));
                if (variant === 'disabled') btn.disabled = true;
                btn.textContent = label;
                btn.addEventListener('click', () => goTo(target));
                container.appendChild(btn);
            };

            addPageButton('\u00ab', currentPage - 1, currentPage <= 1 ? 'disabled' : 'normal');

            const pages = [];
            pages.push(1);
            if (currentPage > 3) pages.push('...');
            for (let i = Math.max(2, currentPage - 1); i <= Math.min(totalPages - 1, currentPage + 1); i++) {
                pages.push(i);
            }
            if (currentPage < totalPages - 2) pages.push('...');
            if (totalPages > 1) pages.push(totalPages);

            pages.forEach(entry => {
                if (entry === '...') {
                    const gap = document.createElement('span');
                    gap.className = 'px-2 py-1 text-sm text-gray-400';
                    gap.textContent = '\u2026';
                    container.appendChild(gap);
                } else {
                    addPageButton(String(entry), entry, entry === currentPage ? 'active' : 'normal');
                }
            });

            addPageButton('\u00bb', currentPage + 1, currentPage >= totalPages ? 'disabled' : 'normal');
        }

        function apply(skipPageReset) {
            const result = compute();

            if (!pageCfg) {
                updateChrome(result.list, result.total);
                if (typeof cfg.render === 'function') cfg.render(result.list, result.total);
                return result;
            }

            if (!skipPageReset) state.page = 1;

            const total = result.list.length;
            const totalPages = Math.max(1, Math.ceil(total / state.perPage));
            if (state.page > totalPages) state.page = totalPages;

            const start = (state.page - 1) * state.perPage;
            const end = Math.min(start + state.perPage, total);
            const pageItems = result.list.slice(start, end);

            const info = total > 0
                ? (typeof pageCfg.infoFormat === 'function'
                    ? pageCfg.infoFormat(start + 1, end, total)
                    : `Showing ${start + 1}\u2013${end} of ${total}`)
                : (pageCfg.emptyText || 'No results match your filters');
            [pageCfg.infoEl, pageCfg.infoBottomEl].forEach(id => {
                const el = byId(id);
                if (el) el.textContent = info;
            });

            renderPagination(pageCfg.topEl, totalPages, state.page);
            renderPagination(pageCfg.bottomEl, totalPages, state.page);

            updateChrome(result.list, result.total);
            if (typeof cfg.render === 'function') cfg.render(pageItems, result.total);
            return result;
        }

        function setSearch(value) {
            // Keep the raw text so syncControls can put it back verbatim. Writing
            // the trimmed value into the input would eat a trailing space (and
            // reset the caret) mid-word, which makes multi-word terms untypable.
            state.searchRaw = value || '';
            state.search = state.searchRaw.trim();
            sticky.clear();
            apply();
        }

        function reset() {
            // Only the filter axes reset; a chosen page size is a preference,
            // not a filter, so it survives Clear Filters.
            Object.assign(state, defaults);
            if (pageCfg) state.page = 1;
            sticky.clear();
            if (searchCfg) {
                const el = byId(searchCfg.el);
                if (el) el.value = '';
            }
            persist();
            syncControls();
            apply();
        }

        function bind() {
            if (searchCfg) {
                const input = byId(searchCfg.el);
                if (input && !input._listFilterInit) {
                    input._listFilterInit = true;
                    const run = debounce(() => setSearch(input.value), searchCfg.debounceMs || 300);
                    input.addEventListener('input', run);
                    input.addEventListener('keydown', e => {
                        if (e.key === 'Escape') {
                            input.value = '';
                            setSearch('');
                        }
                    });
                }
                const searchClear = searchCfg.clearEl ? byId(searchCfg.clearEl) : null;
                if (searchClear && !searchClear._listFilterInit) {
                    searchClear._listFilterInit = true;
                    searchClear.addEventListener('click', () => {
                        const el = byId(searchCfg.el);
                        if (el) el.value = '';
                        setSearch('');
                    });
                }
            }

            if (sortCfg) {
                const el = byId(sortCfg.el);
                if (el && !el._listFilterInit) {
                    el._listFilterInit = true;
                    el.addEventListener('change', function () {
                        state.sort = this.value;
                        sticky.clear();
                        persist();
                        apply();
                    });
                }
            }

            controls.forEach(c => {
                if (c.type === 'pills') {
                    const container = document.querySelector(c.el);
                    if (!container || container._listFilterInit) return;
                    container._listFilterInit = true;
                    // Delegated, so the pills survive any markup re-render.
                    container.addEventListener('click', event => {
                        const btn = event.target.closest('[' + c.attr + ']');
                        if (!btn || !container.contains(btn)) return;
                        state[c.key] = btn.getAttribute(c.attr);
                        sticky.clear();
                        apply();
                    });
                } else if (c.type === 'select') {
                    const el = byId(c.el);
                    if (!el || el._listFilterInit) return;
                    el._listFilterInit = true;
                    el.addEventListener('change', function () {
                        state[c.key] = this.value;
                        sticky.clear();
                        apply();
                    });
                } else if (c.type === 'cycle') {
                    const btn = byId(c.el);
                    if (!btn || btn._listFilterInit) return;
                    btn._listFilterInit = true;
                    const values = Array.isArray(c.values) ? c.values : [null];
                    btn.addEventListener('click', () => {
                        const at = values.findIndex(v => sameValue(v, state[c.key]));
                        state[c.key] = values[(at + 1) % values.length];
                        sticky.clear();
                        apply();
                    });
                }
            });

            if (pageCfg && pageCfg.perPageEl) {
                const el = byId(pageCfg.perPageEl);
                if (el && !el._listFilterInit) {
                    el._listFilterInit = true;
                    el.addEventListener('change', function () {
                        state.perPage = parseInt(this.value) || (pageCfg.defaultPerPage || 12);
                        persist();
                        apply();
                    });
                }
            }

            const clearEl = byId(cfg.clearEl);
            if (clearEl && !clearEl._listFilterInit) {
                clearEl._listFilterInit = true;
                clearEl.addEventListener('click', reset);
            }
        }

        return {
            state: state,
            sticky: sticky,
            activeCount: activeCount,
            bind: bind,
            apply: apply,
            reset: reset,
            setSearch: setSearch,
            syncControls: syncControls,
        };
    }

    return { create: create };
})();

// Export
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ListFilter;
} else {
    window.ListFilter = ListFilter;
}
