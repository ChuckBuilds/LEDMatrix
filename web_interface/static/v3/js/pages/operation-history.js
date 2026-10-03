/*
 * pages/operation-history.js -- the Operation History tab
 * (templates/v3/partials/operation_history.html).
 *
 * A read-only audit list: the page loads up to 1,000 records once, then
 * filters, searches and pages through them in the browser. Rows are built
 * with textContent, never markup strings, so a plugin id or error message
 * from the log is always shown as text.
 *
 * Every listener and read request carries ctx.signal. Clearing the history is
 * a write, so it is left to finish if the page is swapped away mid-request;
 * its result is then only reported, not drawn.
 */

const HISTORY_URL = '/api/v3/plugins/operation/history';
const LIST_URL = HISTORY_URL + '?limit=1000';
const PLUGINS_URL = '/api/v3/plugins/installed';
const PAGE_SIZE = 50;
const SEARCH_DELAY_MS = 300;

const STATUS_CLASSES = {
    success: 'bg-green-100 text-green-800',
    failed: 'bg-red-100 text-red-800',
};
const TYPE_CLASSES = {
    install: 'bg-blue-100 text-blue-800',
    update: 'bg-purple-100 text-purple-800',
    uninstall: 'bg-red-100 text-red-800',
    enable: 'bg-green-100 text-green-800',
    disable: 'bg-yellow-100 text-yellow-800',
    configure: 'bg-teal-100 text-teal-800',
};
const NEUTRAL_CLASS = 'bg-gray-100 text-gray-800';

function isQuiet(error) {
    // A cancelled request (the page was swapped away) or the login redirect
    // (the browser is already leaving): nothing to tell the user.
    return !!error && (error.name === 'AbortError' || error.loginRequired);
}

/**
 * The server says "completed" or "success" for a success and "error" or
 * "failed" for a failure; the status filter's values are success/failed.
 */
export function normalizeStatus(status) {
    const s = String(status || '').toLowerCase();
    if (s === 'completed' || s === 'success') return 'success';
    if (s === 'error' || s === 'failed') return 'failed';
    return s;
}

/** The short "v1.2 | commit: abc1234" summary of a record's details. */
export function detailsSummary(details) {
    if (!details || typeof details !== 'object') return '';
    const parts = [];
    if (details.version) parts.push('v' + details.version);
    if (details.branch) parts.push('branch: ' + details.branch);
    if (details.commit) parts.push('commit: ' + String(details.commit).substring(0, 7));
    if (details.previous_commit && details.commit && details.previous_commit !== details.commit) {
        parts.push('from: ' + String(details.previous_commit).substring(0, 7));
    }
    if (details.preserve_config !== undefined) {
        parts.push(details.preserve_config ? 'config preserved' : 'config removed');
    }
    return parts.join(' | ');
}

/** The records that pass the current filters. */
export function filterRecords(records, filters) {
    const search = (filters.search || '').toLowerCase();
    return records.filter(function(record) {
        if (filters.plugin && record.plugin_id !== filters.plugin) return false;
        if (filters.type && record.operation_type !== filters.type) return false;
        if (filters.status && normalizeStatus(record.status) !== filters.status) return false;
        if (search) {
            const searchable = [
                record.operation_type, record.plugin_id, record.status, record.user, record.error,
                JSON.stringify(record.details || {}),
            ].join(' ').toLowerCase();
            if (!searchable.includes(search)) return false;
        }
        return true;
    });
}

function elements(root) {
    const $ = function(id) { return root.querySelector('#' + id); };
    return {
        plugin: $('history-plugin-filter'),
        type: $('history-type-filter'),
        status: $('history-status-filter'),
        search: $('history-search'),
        refresh: $('refresh-history-btn'),
        clear: $('clear-history-btn'),
        tbody: $('history-table-body'),
        start: $('history-start'),
        end: $('history-end'),
        total: $('history-total'),
        prev: $('history-prev-btn'),
        next: $('history-next-btn'),
    };
}

function el(doc, tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function messageRow(doc, text) {
    const row = el(doc, 'tr');
    const cell = el(doc, 'td', 'px-6 py-4 text-center text-gray-500', text);
    cell.colSpan = 6;
    row.append(cell);
    return row;
}

function badge(doc, className, text) {
    return el(doc, 'span', 'px-2 py-1 text-xs font-medium rounded ' + className, text);
}

function historyRow(doc, record) {
    const row = el(doc, 'tr', 'hover:bg-gray-50');
    const when = new Date(record.timestamp || record.created_at || Date.now());
    const status = normalizeStatus(record.status);
    const type = record.operation_type || 'unknown';

    const typeCell = el(doc, 'td', 'px-6 py-4 whitespace-nowrap text-sm');
    typeCell.append(badge(doc, TYPE_CLASSES[String(type).toLowerCase()] || NEUTRAL_CLASS, String(type)));
    const statusCell = el(doc, 'td', 'px-6 py-4 whitespace-nowrap text-sm');
    statusCell.append(badge(doc, STATUS_CLASSES[status] || NEUTRAL_CLASS, status));

    const detailsCell = el(doc, 'td', 'px-6 py-4 text-sm text-gray-500');
    const summary = detailsSummary(record.details);
    detailsCell.append(summary ? el(doc, 'span', 'text-xs text-gray-500', summary) : doc.createTextNode('-'));
    if (record.error) detailsCell.append(el(doc, 'div', 'text-red-600 text-xs mt-1', String(record.error)));

    row.append(
        el(doc, 'td', 'px-6 py-4 whitespace-nowrap text-sm text-gray-900', when.toLocaleString()),
        typeCell,
        el(doc, 'td', 'px-6 py-4 whitespace-nowrap text-sm text-gray-900', String(record.plugin_id || '-')),
        statusCell,
        el(doc, 'td', 'px-6 py-4 whitespace-nowrap text-sm text-gray-500', String(record.user || '-')),
        detailsCell,
    );
    return row;
}

function render(ctx) {
    const s = ctx.state;
    const { tbody, start: startEl, end: endEl, total, prev, next } = s.els;
    const doc = ctx.root.ownerDocument;
    const pages = Math.max(1, Math.ceil(s.filtered.length / PAGE_SIZE));
    if (s.page > pages) s.page = pages;
    const start = (s.page - 1) * PAGE_SIZE;
    const end = Math.min(start + PAGE_SIZE, s.filtered.length);
    const rows = s.filtered.slice(start, end);

    tbody.replaceChildren(...(rows.length
        ? rows.map(function(record) { return historyRow(doc, record); })
        : [messageRow(doc, 'No operations found')]));

    // Kept in step even when nothing matches, so "Showing 1 to 50" does not
    // linger under an empty table.
    startEl.textContent = String(s.filtered.length ? start + 1 : 0);
    endEl.textContent = String(end);
    total.textContent = String(s.filtered.length);
    prev.disabled = s.page <= 1;
    next.disabled = end >= s.filtered.length;
}

/** Re-apply the filters to the loaded records and draw the first page. */
export function applyFilters(ctx, keepPage) {
    const { plugin, type, status, search } = ctx.state.els;
    ctx.state.filtered = filterRecords(ctx.state.records, {
        plugin: plugin.value, type: type.value, status: status.value, search: search.value,
    });
    if (!keepPage) ctx.state.page = 1;
    render(ctx);
}

/** Fetch the history and draw it. A newer load supersedes an older one. */
export function load(ctx) {
    const seq = (ctx.state.seq || 0) + 1;
    ctx.state.seq = seq;
    return ctx.api.get(LIST_URL, { signal: ctx.signal }).then(function(body) {
        if (ctx.state.seq !== seq) return;
        ctx.state.records = Array.isArray(body.data) ? body.data : [];
        applyFilters(ctx, true);
    }).catch(function(err) {
        if (isQuiet(err) || ctx.state.seq !== seq) return;
        ctx.notify(err.network || !err.status
            ? 'Error loading operation history'
            : 'Failed to load operation history', 'error');
    });
}

function installedPlugins(ctx) {
    // PluginAPI (js/plugins/api_client.js) caches the installed list it
    // shares with the Plugin Manager, so prefer it: a reloaded tab then costs
    // no extra request. Without it, ask the API directly.
    const pluginApi = ctx.root.ownerDocument.defaultView.PluginAPI;
    if (pluginApi && typeof pluginApi.getInstalledPlugins === 'function') {
        return Promise.resolve(pluginApi.getInstalledPlugins());
    }
    return ctx.api.get(PLUGINS_URL, { signal: ctx.signal }).then(function(body) {
        return (body.data && Array.isArray(body.data.plugins)) ? body.data.plugins : [];
    });
}

/** Fill the plugin filter with the installed plugins' ids. */
export function loadPluginFilter(ctx) {
    return installedPlugins(ctx).then(function(plugins) {
        if (ctx.signal.aborted || !Array.isArray(plugins)) return;
        const doc = ctx.root.ownerDocument;
        const select = ctx.state.els.plugin;
        plugins.map(function(p) { return p && p.id; }).filter(Boolean).forEach(function(id) {
            const option = doc.createElement('option');
            option.value = String(id);
            option.textContent = String(id);
            select.append(option);
        });
    }).catch(function(err) {
        if (!isQuiet(err)) ctx.root.ownerDocument.defaultView.console.error('Error loading plugins for filter:', err);
    });
}

/** Ask, then clear the whole history. */
export function clearHistory(ctx) {
    const win = ctx.root.ownerDocument.defaultView;
    if (!win.confirm('Are you sure you want to clear the operation history? This cannot be undone.')) {
        return Promise.resolve(false);
    }
    return ctx.api.del(HISTORY_URL).then(function() {
        ctx.state.seq = (ctx.state.seq || 0) + 1; // an older load must not redraw the old list
        if (ctx.signal.aborted) return true;
        ctx.state.records = [];
        applyFilters(ctx);
        return true;
    }).catch(function(err) {
        if (isQuiet(err)) return false;
        ctx.notify(err.network || !err.status ? 'Error clearing history'
            : (err.message || 'Failed to clear history'), 'error');
        return false;
    });
}

export function init(root, ctx) {
    const els = elements(root);
    const win = root.ownerDocument.defaultView;
    const on = { signal: ctx.signal };
    Object.assign(ctx.state, { els: els, records: [], filtered: [], page: 1, searchTimer: null });

    els.refresh.addEventListener('click', function() { load(ctx); }, on);
    els.clear.addEventListener('click', function() { clearHistory(ctx); }, on);
    els.prev.addEventListener('click', function() {
        if (ctx.state.page > 1) { ctx.state.page--; render(ctx); }
    }, on);
    els.next.addEventListener('click', function() {
        if (ctx.state.page * PAGE_SIZE < ctx.state.filtered.length) { ctx.state.page++; render(ctx); }
    }, on);
    [els.plugin, els.type, els.status].forEach(function(select) {
        select.addEventListener('change', function() { applyFilters(ctx); }, on);
    });
    els.search.addEventListener('input', function() {
        win.clearTimeout(ctx.state.searchTimer);
        ctx.state.searchTimer = win.setTimeout(function() {
            ctx.state.searchTimer = null;
            applyFilters(ctx);
        }, SEARCH_DELAY_MS);
    }, on);

    loadPluginFilter(ctx);
    load(ctx);
}

export function destroy(root, ctx) {
    if (ctx.state.searchTimer) {
        root.ownerDocument.defaultView.clearTimeout(ctx.state.searchTimer);
        ctx.state.searchTimer = null;
    }
}
