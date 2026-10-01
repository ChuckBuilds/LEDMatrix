/*
 * pages/cache.js -- the Cache tab (templates/v3/partials/cache.html).
 *
 * The reference conversion for docs/WEB_FRONTEND_ARCHITECTURE.md: the partial
 * carries no <script>; its root is <div data-page="cache">, and the page
 * registry (core/registry.js) calls init() once when it appears and destroy()
 * when it is swapped away. Every listener is registered with ctx.signal and
 * every request carries it, so destroy has nothing left to undo by hand.
 *
 * ctx (from core/boot.js): ctx.api (core/api.js), ctx.notify, ctx.signal,
 * ctx.state.
 */

const LIST_URL = '/api/v3/cache/list';
const DELETE_URL = '/api/v3/cache/delete';

// The mounted page, for the deprecated window.deleteCacheFile alias (boot.js).
let active = null;

function isQuiet(error) {
    // A cancelled request (the page was swapped away) or the login redirect
    // (the browser is already leaving): nothing to tell the user.
    return !!error && (error.name === 'AbortError' || error.loginRequired);
}

function elements(root) {
    return {
        dir: root.querySelector('#cache-dir'),
        tbody: root.querySelector('#cache-files-tbody'),
        empty: root.querySelector('#cache-empty'),
        error: root.querySelector('#cache-error'),
        errorMessage: root.querySelector('#cache-error-message'),
        refresh: root.querySelector('#refresh-cache-btn'),
    };
}

function el(doc, tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function messageRow(doc, iconClass, text) {
    const row = el(doc, 'tr');
    const cell = el(doc, 'td', 'px-6 py-8 text-center text-gray-500');
    cell.colSpan = 5;
    cell.append(el(doc, 'i', iconClass), el(doc, 'p', null, text));
    row.append(cell);
    return row;
}

function ageClass(seconds) {
    if (seconds < 300) return 'text-green-600 font-medium'; // under 5 minutes
    if (seconds < 3600) return 'text-yellow-600';            // under an hour
    return 'text-red-600';
}

function formatModified(value) {
    return new Date(value).toLocaleString('en-US', {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false,
    });
}

function cacheRow(doc, file) {
    const row = el(doc, 'tr', 'hover:bg-gray-50');

    const keyCell = el(doc, 'td', 'px-6 py-4 whitespace-nowrap');
    keyCell.append(el(doc, 'div', 'text-sm font-medium text-gray-900 font-mono', String(file.key)),
                   el(doc, 'div', 'text-xs text-gray-500', String(file.filename)));

    const ageCell = el(doc, 'td', 'px-6 py-4 whitespace-nowrap');
    ageCell.append(el(doc, 'span', 'text-sm ' + ageClass(file.age_seconds), String(file.age_display)));

    const sizeCell = el(doc, 'td', 'px-6 py-4 whitespace-nowrap');
    sizeCell.append(el(doc, 'span', 'text-sm text-gray-600', String(file.size_display)));

    const modifiedCell = el(doc, 'td', 'px-6 py-4 whitespace-nowrap');
    modifiedCell.append(el(doc, 'span', 'text-sm text-gray-600', formatModified(file.modified_datetime)));

    const actionCell = el(doc, 'td', 'px-6 py-4 whitespace-nowrap text-right text-sm font-medium');
    const button = el(doc, 'button',
                      'text-red-600 hover:text-red-900 px-3 py-1 rounded hover:bg-red-50 transition-colors');
    button.type = 'button';
    button.dataset.cacheKey = String(file.key);
    button.title = 'Delete cache file';
    button.setAttribute('aria-label', 'Delete cache file ' + file.key);
    button.append(el(doc, 'i', 'fas fa-trash mr-1'), doc.createTextNode('Delete'));
    actionCell.append(button);

    row.append(keyCell, ageCell, sizeCell, modifiedCell, actionCell);
    return row;
}

function showError(ctx, message) {
    const { tbody, empty, error, errorMessage } = ctx.state.els;
    tbody.replaceChildren();
    empty.classList.add('hidden');
    error.classList.remove('hidden');
    errorMessage.textContent = message;
}

function render(ctx, data) {
    const { tbody, empty, error, dir } = ctx.state.els;
    const doc = ctx.root.ownerDocument;
    error.classList.add('hidden');

    dir.textContent = data.cache_dir || 'Not configured';
    // Toggled, not added: a refresh keeps the same element, so a directory
    // that appears later must lose the grey "Not configured" style.
    dir.classList.toggle('text-gray-500', !data.cache_dir);

    const files = Array.isArray(data.cache_files) ? data.cache_files : [];
    if (!files.length) {
        tbody.replaceChildren();
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');
    tbody.replaceChildren(...files.map(function(file) { return cacheRow(doc, file); }));
}

/** Fetch and draw the cache list. A newer load supersedes an older one. */
export function load(ctx) {
    const { tbody, empty, error } = ctx.state.els;
    const seq = (ctx.state.seq || 0) + 1;
    ctx.state.seq = seq;
    tbody.replaceChildren(messageRow(ctx.root.ownerDocument, 'fas fa-spinner fa-spin text-2xl mb-2',
                                     'Loading cache files...'));
    empty.classList.add('hidden');
    error.classList.add('hidden');

    return ctx.api.get(LIST_URL, { signal: ctx.signal }).then(function(body) {
        if (ctx.state.seq !== seq) return;
        render(ctx, body.data || {});
    }).catch(function(err) {
        if (isQuiet(err) || ctx.state.seq !== seq) return;
        showError(ctx, err.network || !err.status
            ? 'Error loading cache files: ' + err.message
            : (err.message || 'Failed to load cache files'));
    });
}

/** Ask, then delete one cache entry and reload the list. */
export function deleteEntry(ctx, key) {
    const win = ctx.root.ownerDocument.defaultView;
    if (!win.confirm('Are you sure you want to delete the cache file for "' + key + '"?')) {
        return Promise.resolve(false);
    }
    return ctx.api.post(DELETE_URL, { key: key }, { signal: ctx.signal }).then(function(body) {
        ctx.notify(body.message || 'Cache file deleted successfully', 'success');
        load(ctx);
        return true;
    }).catch(function(err) {
        if (isQuiet(err)) return false;
        ctx.notify(err.network || !err.status
            ? 'Error deleting cache file: ' + err.message
            : (err.message || 'Failed to delete cache file'), 'error');
        return false;
    });
}

export function init(root, ctx) {
    const els = elements(root);
    ctx.state.els = els;

    els.refresh.addEventListener('click', function() { load(ctx); }, { signal: ctx.signal });
    // One delegated listener for every row's Delete button, however often
    // the rows are redrawn.
    els.tbody.addEventListener('click', function(event) {
        const button = event.target.closest('button[data-cache-key]');
        if (button && els.tbody.contains(button)) deleteEntry(ctx, button.dataset.cacheKey);
    }, { signal: ctx.signal });

    active = ctx;
    load(ctx);
}

export function destroy(root, ctx) {
    if (active === ctx) active = null;
}

/** The old window.deleteCacheFile(key), kept as a deprecated alias (boot.js). */
export function deleteCacheFile(key) {
    return active ? deleteEntry(active, key) : Promise.resolve(false);
}
