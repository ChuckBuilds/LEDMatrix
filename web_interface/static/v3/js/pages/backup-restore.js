/*
 * pages/backup-restore.js -- the Backup & Restore tab
 * (templates/v3/partials/backup_restore.html).
 *
 * The buttons carry data-action; one delegated listener on the page root
 * handles them all, including each history row's Delete. Everything the
 * server sends back (file names, host names, plugin ids, error messages) is
 * drawn with textContent.
 *
 * Reads (the summary, the history list, inspecting a file) carry ctx.signal
 * and are cancelled when the page is swapped away. Writes (export, delete,
 * restore) are not: the server always finishes them and the result is still
 * reported in a notification, but nothing is drawn into a page that is gone.
 *
 * The old globals (exportBackup, loadBackupList, validateRestoreFile,
 * clearRestore, runRestore) are deprecated aliases made in core/boot.js; they
 * forward to the exports at the bottom of this file.
 */

const URLS = {
    preview: '/api/v3/backup/preview',
    list: '/api/v3/backup/list',
    exportZip: '/api/v3/backup/export',
    validate: '/api/v3/backup/validate',
    restore: '/api/v3/backup/restore',
    download: '/api/v3/backup/download/',
    remove: '/api/v3/backup/',
};

// Checkbox id -> RestoreOptions key (backup.py's _RESTORE_OPTION_KEYS).
const RESTORE_OPTIONS = [
    ['opt-config', 'restore_config'],
    ['opt-secrets', 'restore_secrets'],
    ['opt-wifi', 'restore_wifi'],
    ['opt-fonts', 'restore_fonts'],
    ['opt-plugin-uploads', 'restore_plugin_uploads'],
    ['opt-reinstall', 'reinstall_plugins'],
];

// The mounted page, for the deprecated aliases (boot.js).
let active = null;

function isQuiet(error) {
    return !!error && (error.name === 'AbortError' || error.loginRequired);
}

function message(error) {
    return (error && error.message) || 'Unknown error';
}

export function formatSize(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    let size = bytes;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
    return size.toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
}

function el(doc, tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

/** "<strong>label</strong> value" as one line. */
function labelled(doc, tag, label, value) {
    const line = el(doc, tag);
    line.append(el(doc, 'strong', null, label), doc.createTextNode(' ' + value));
    return line;
}

function list(values, map, separator) {
    return (values || []).map(map || String).join(separator || ', ');
}

function setBusy(button, busy, idleIcon, idleText, busyText) {
    const doc = button.ownerDocument;
    button.disabled = busy;
    button.replaceChildren(el(doc, 'i', busy ? 'fas fa-spinner fa-spin mr-2' : 'fas ' + idleIcon + ' mr-2'),
                           doc.createTextNode(busy ? busyText : idleText));
}

function elements(root) {
    const $ = function(id) { return root.querySelector('#' + id); };
    return {
        preview: $('export-preview'),
        exportButton: $('export-backup-btn'),
        fileInput: $('restore-file-input'),
        restorePreview: $('restore-preview'),
        restorePreviewBody: $('restore-preview-body'),
        restoreButton: $('run-restore-btn'),
        result: $('restore-result'),
        history: $('backup-history'),
    };
}

/** The "what a new backup would hold" summary on the Export card. */
export function loadPreview(ctx) {
    const box = ctx.state.els.preview;
    const doc = ctx.root.ownerDocument;
    return ctx.api.get(URLS.preview, { signal: ctx.signal }).then(function(body) {
        const d = body.data || {};
        const fonts = Array.isArray(d.user_fonts) ? d.user_fonts : [];
        const ul = el(doc, 'ul', 'list-disc pl-5 space-y-1');
        function item(label, value, after) {
            const li = el(doc, 'li', null, label + ': ');
            li.append(el(doc, 'strong', null, String(value)));
            if (after) li.append(doc.createTextNode(' ' + after));
            ul.append(li);
        }
        item('Main config', d.has_config ? 'yes' : 'no');
        item('Secrets', d.has_secrets ? 'yes' : 'no');
        item('WiFi config', d.has_wifi ? 'yes' : 'no');
        item('User fonts', fonts.length, fonts.length ? '(' + list(fonts) + ')' : '');
        item('Plugin image uploads', d.plugin_uploads || 0, 'file(s)');
        item('Installed plugins', (d.plugins || []).length);
        box.replaceChildren(ul);
    }).catch(function(err) {
        if (isQuiet(err)) return;
        box.textContent = 'Could not load preview: ' + message(err);
    });
}

function historyTable(doc, entries) {
    const table = el(doc, 'table', 'min-w-full divide-y divide-gray-200');
    const head = el(doc, 'tr');
    ['Filename', 'Size', 'Created'].forEach(function(label) {
        head.append(el(doc, 'th', 'text-left py-2', label));
    });
    head.append(el(doc, 'th'));
    const thead = el(doc, 'thead');
    thead.append(head);
    const tbody = el(doc, 'tbody', 'divide-y divide-gray-100');
    entries.forEach(function(entry) {
        const name = String(entry.filename);
        const row = el(doc, 'tr');
        const actions = el(doc, 'td', 'py-2 text-right space-x-2');
        const link = el(doc, 'a', 'text-blue-600 hover:underline', 'Download');
        link.href = URLS.download + encodeURIComponent(name);
        const remove = el(doc, 'button', 'text-red-600 hover:underline', 'Delete');
        remove.type = 'button';
        remove.dataset.action = 'delete';
        remove.dataset.filename = name;
        remove.setAttribute('aria-label', 'Delete ' + name);
        actions.append(link, doc.createTextNode(' '), remove);
        row.append(el(doc, 'td', 'py-2 font-mono text-xs', name),
                   el(doc, 'td', 'py-2', formatSize(entry.size)),
                   el(doc, 'td', 'py-2', String(entry.created_at)),
                   actions);
        tbody.append(row);
    });
    table.append(thead, tbody);
    return table;
}

/** The Backup history card. A newer load supersedes an older one. */
export function loadList(ctx) {
    const box = ctx.state.els.history;
    const doc = ctx.root.ownerDocument;
    const seq = (ctx.state.listSeq || 0) + 1;
    ctx.state.listSeq = seq;
    box.textContent = 'Loading…';
    return ctx.api.get(URLS.list, { signal: ctx.signal }).then(function(body) {
        if (ctx.state.listSeq !== seq) return;
        const entries = Array.isArray(body.data) ? body.data : [];
        box.replaceChildren(entries.length ? historyTable(doc, entries)
            : el(doc, 'p', null, 'No backups have been created yet.'));
    }).catch(function(err) {
        if (isQuiet(err) || ctx.state.listSeq !== seq) return;
        box.textContent = 'Could not load backups: ' + message(err);
    });
}

/** Create a backup, start its download, and refresh the history. */
export function exportZip(ctx) {
    const button = ctx.state.els.exportButton;
    if (button.disabled) return Promise.resolve(false);
    setBusy(button, true, 'fa-download', 'Download backup', 'Creating…');
    return ctx.api.post(URLS.exportZip, {}).then(function(body) {
        ctx.notify('Backup created: ' + body.filename, 'success');
        if (ctx.signal.aborted) return true;
        ctx.root.ownerDocument.defaultView.location.assign(URLS.download + encodeURIComponent(body.filename));
        return loadList(ctx).then(function() { return true; });
    }).catch(function(err) {
        if (!isQuiet(err)) ctx.notify('Export failed: ' + message(err), 'error');
        return false;
    }).finally(function() {
        setBusy(button, false, 'fa-download', 'Download backup');
    });
}

/** Ask, then delete one stored backup. */
export function deleteBackup(ctx, filename) {
    const win = ctx.root.ownerDocument.defaultView;
    if (!win.confirm('Delete ' + filename + '?')) return Promise.resolve(false);
    return ctx.api.del(URLS.remove + encodeURIComponent(filename)).then(function() {
        ctx.notify('Backup deleted', 'success');
        if (!ctx.signal.aborted) loadList(ctx);
        return true;
    }).catch(function(err) {
        if (!isQuiet(err)) ctx.notify('Delete failed: ' + message(err), 'error');
        return false;
    });
}

function hideRestore(ctx) {
    ctx.state.inspected = null;
    ctx.state.els.restorePreview.classList.add('hidden');
    ctx.state.els.result.classList.add('hidden');
}

function renderManifest(ctx, manifest) {
    const doc = ctx.root.ownerDocument;
    const detected = manifest.detected_contents || [];
    const plugins = manifest.plugins || [];
    ctx.state.els.restorePreviewBody.replaceChildren(
        labelled(doc, 'div', 'Created:', String(manifest.created_at || 'unknown')),
        labelled(doc, 'div', 'Source host:', String(manifest.hostname || 'unknown')),
        labelled(doc, 'div', 'LEDMatrix version:', String(manifest.ledmatrix_version || 'unknown')),
        labelled(doc, 'div', 'Includes:', detected.length ? list(detected) : '(nothing detected)'),
        labelled(doc, 'div', 'Plugins referenced:',
                 plugins.length ? list(plugins, function(p) { return String(p && p.plugin_id); }) : 'none'),
    );
    ctx.state.els.restorePreview.classList.remove('hidden');
}

/** Upload the chosen file for inspection and show what it holds. */
export function inspect(ctx) {
    const input = ctx.state.els.fileInput;
    const file = input.files && input.files[0];
    if (!file) {
        ctx.notify('Choose a backup file first', 'error');
        return Promise.resolve(false);
    }
    const win = ctx.root.ownerDocument.defaultView;
    const form = new win.FormData();
    form.append('backup_file', file);
    return ctx.api.request('POST', URLS.validate, { body: form, signal: ctx.signal }).then(function(body) {
        // A different file picked while this one was being inspected.
        if (!input.files || input.files[0] !== file) return false;
        ctx.state.inspected = file;
        renderManifest(ctx, body.data || {});
        return true;
    }).catch(function(err) {
        if (!isQuiet(err)) ctx.notify('Invalid backup: ' + message(err), 'error');
        return false;
    });
}

/** The Cancel button: forget the inspected file. */
export function clear(ctx) {
    hideRestore(ctx);
    ctx.state.els.fileInput.value = '';
}

function renderResult(ctx, data, partial) {
    const doc = ctx.root.ownerDocument;
    const result = ctx.state.els.result;
    result.className = (partial
        ? 'bg-yellow-50 border-yellow-200 text-yellow-800'
        : 'bg-green-50 border-green-200 text-green-800') + ' border rounded-md p-4';
    const lines = [
        el(doc, 'h3', 'font-medium mb-2', partial ? 'Restore complete with warnings' : 'Restore complete'),
        labelled(doc, 'div', 'Restored:', list(data.restored) || 'none'),
        labelled(doc, 'div', 'Skipped:', list(data.skipped) || 'none'),
        labelled(doc, 'div', 'Plugins installed:', list(data.plugins_installed) || 'none'),
        labelled(doc, 'div', 'Plugins failed:', list(data.plugins_failed, function(p) {
            return String(p.plugin_id) + ' (' + String(p.error) + ')';
        }) || 'none'),
        labelled(doc, 'div', 'Errors:', list(data.errors, String, '; ') || 'none'),
    ];
    if ((data.restored || []).length || (data.plugins_installed || []).length) {
        lines.push(el(doc, 'p', 'mt-2', 'Restart the display service to apply all changes.'));
    }
    result.replaceChildren(...lines);
}

/** Ask, then restore the inspected file with the chosen options. */
export function restore(ctx) {
    const file = ctx.state.inspected;
    if (!file) {
        ctx.notify('Inspect the file before restoring', 'error');
        return Promise.resolve(false);
    }
    const button = ctx.state.els.restoreButton;
    if (button.disabled) return Promise.resolve(false); // one already running
    const win = ctx.root.ownerDocument.defaultView;
    if (!win.confirm('Restore from this backup? Current configuration will be overwritten.')) {
        return Promise.resolve(false);
    }
    const options = {};
    RESTORE_OPTIONS.forEach(function(pair) {
        const box = ctx.root.querySelector('#' + pair[0]);
        options[pair[1]] = !!(box && box.checked);
    });
    const form = new win.FormData();
    form.append('backup_file', file);
    form.append('options', JSON.stringify(options));

    setBusy(button, true, 'fa-upload', 'Restore now', 'Restoring…');
    return ctx.api.request('POST', URLS.restore, { body: form }).then(function(body) {
        const data = body.data || {};
        const partial = (data.plugins_failed || []).length > 0 || (data.errors || []).length > 0;
        if (!ctx.signal.aborted) {
            renderResult(ctx, data, partial);
            ctx.state.els.result.classList.remove('hidden');
        }
        ctx.notify(partial ? 'Restore complete with warnings' : 'Restore complete',
                   partial ? 'warning' : 'success');
        return true;
    }).catch(function(err) {
        if (isQuiet(err)) return false;
        const errors = (err.body && err.body.data && err.body.data.errors) || [];
        ctx.notify('Restore failed: ' + ((err.body && err.body.message) || errors.join('; ')
            || err.message || 'Restore had errors'), 'error');
        return false;
    }).finally(function() {
        setBusy(button, false, 'fa-upload', 'Restore now');
    });
}

const ACTIONS = {
    'export': exportZip,
    'refresh': loadList,
    'inspect': inspect,
    'restore': restore,
    'cancel': clear,
};

export function init(root, ctx) {
    const els = elements(root);
    ctx.state.els = els;
    ctx.state.inspected = null;
    const on = { signal: ctx.signal };

    root.addEventListener('click', function(event) {
        const button = event.target.closest('button[data-action]');
        if (!button || !root.contains(button)) return;
        if (button.dataset.action === 'delete') {
            deleteBackup(ctx, button.dataset.filename);
            return;
        }
        const action = ACTIONS[button.dataset.action];
        if (action) action(ctx);
    }, on);
    // A newly picked file has to be inspected again before it can be restored.
    els.fileInput.addEventListener('change', function() { hideRestore(ctx); }, on);

    active = ctx;
    loadPreview(ctx);
    loadList(ctx);
}

export function destroy(root, ctx) {
    if (active === ctx) active = null;
}

// ── the old globals, kept as deprecated aliases (boot.js) ────────────────────
function forward(action) {
    return function() { return active ? action(active) : Promise.resolve(false); };
}
/** window.exportBackup() */
export const exportBackup = forward(exportZip);
/** window.loadBackupList() */
export const loadBackupList = forward(loadList);
/** window.validateRestoreFile() */
export const validateRestoreFile = forward(inspect);
/** window.clearRestore() */
export const clearRestore = forward(clear);
/** window.runRestore() */
export const runRestore = forward(restore);
