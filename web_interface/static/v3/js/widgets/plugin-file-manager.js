/**
 * Plugin File Manager Widget
 *
 * Reusable inline file manager for plugins that manage files via the
 * web_ui_actions system. Driven entirely by x-widget-config in the schema —
 * no external HTML file or iframe needed.
 *
 * Any plugin can adopt this widget by:
 *   1. Defining web_ui_actions in manifest.json (list, get, save, upload,
 *      delete, create, toggle) with ui_hidden: true
 *   2. Adding x-widget: "plugin-file-manager" to a field in config_schema.json
 *      with x-widget-config mapping the action IDs
 *
 * Schema example:
 * {
 *   "file_manager": {
 *     "type": "null",
 *     "title": "Data Files",
 *     "x-widget": "plugin-file-manager",
 *     "x-widget-config": {
 *       "actions": {
 *         "list":   "list-files",
 *         "get":    "get-file",
 *         "save":   "save-file",
 *         "upload": "upload-file",
 *         "delete": "delete-file",
 *         "create": "create-file",
 *         "toggle": "toggle-category"
 *       },
 *       "upload_hint":     "JSON files with day numbers 1–365 as keys",
 *       "directory_label": "of_the_day/",
 *       "create_fields": [
 *         { "key": "category_name", "label": "Category Name",
 *           "placeholder": "e.g., my_words", "pattern": "^[a-z0-9_]+$",
 *           "hint": "Lowercase letters, numbers, underscores" },
 *         { "key": "display_name", "label": "Display Name",
 *           "placeholder": "e.g., My Words", "hint": "Optional — auto-generated if blank" }
 *       ]
 *     }
 *   }
 * }
 *
 * @module PluginFileManagerWidget
 */

(function () {
    'use strict';

    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[PluginFileManager] LEDMatrixWidgets registry not found.');
        return;
    }

    // ─── Inject widget-scoped styles once ────────────────────────────────────

    if (!document.getElementById('pfm-styles')) {
        const style = document.createElement('style');
        style.id = 'pfm-styles';
        style.textContent = `
.pfm-root { font-family: inherit; }
.pfm-header { display:flex; align-items:center; justify-content:space-between;
              margin-bottom:.75rem; }
.pfm-title  { font-size:1rem; font-weight:600; color:#111827; }
.pfm-dir    { font-size:.75rem; color:#6b7280; margin-top:.125rem; }
.pfm-upload { border:2px dashed #d1d5db; border-radius:.5rem; padding:1.25rem;
              text-align:center; cursor:pointer; transition:border-color .15s,background .15s; }
.pfm-upload:hover,.pfm-upload.dragover { border-color:#3b82f6; background:#eff6ff; }
.pfm-upload p  { font-size:.875rem; color:#4b5563; margin:.25rem 0 0; }
.pfm-upload small { font-size:.75rem; color:#9ca3af; }
.pfm-grid  { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
             gap:.75rem; margin-top:.75rem; }
.pfm-card  { border:1px solid #e5e7eb; border-radius:.5rem; padding:.875rem;
             background:#fff; transition:box-shadow .15s; }
.pfm-card:hover { box-shadow:0 1px 4px rgba(0,0,0,.1); }
.pfm-card.disabled { opacity:.55; }
.pfm-card-top  { display:flex; align-items:center; justify-content:space-between;
                 margin-bottom:.5rem; }
.pfm-card-icon { width:2rem; height:2rem; background:#f3f4f6; border-radius:.375rem;
                 display:flex; align-items:center; justify-content:center;
                 color:#6b7280; font-size:1rem; }
.pfm-card-name { font-weight:600; color:#111827; font-size:.875rem; margin:.375rem 0 .125rem; }
.pfm-card-meta { font-size:.75rem; color:#6b7280; line-height:1.5; }
.pfm-card-actions { display:flex; gap:.375rem; margin-top:.625rem; }
.pfm-btn       { display:inline-flex; align-items:center; gap:.25rem; padding:.375rem .75rem;
                 border-radius:.375rem; font-size:.8125rem; font-weight:500;
                 border:none; cursor:pointer; transition:background .15s; }
.pfm-btn-primary   { background:#2563eb; color:#fff; flex:1; justify-content:center; }
.pfm-btn-primary:hover  { background:#1d4ed8; }
.pfm-btn-danger    { background:#dc2626; color:#fff; }
.pfm-btn-danger:hover   { background:#b91c1c; }
.pfm-btn-secondary { background:#f3f4f6; color:#374151; border:1px solid #d1d5db; }
.pfm-btn-secondary:hover { background:#e5e7eb; }
.pfm-btn-sm { padding:.25rem .5rem; font-size:.75rem; }
.pfm-btn-create { background:#059669; color:#fff; }
.pfm-btn-create:hover { background:#047857; }
.pfm-toggle-wrap  { display:flex; align-items:center; gap:.375rem; }
.pfm-toggle-label { font-size:.75rem; color:#6b7280; }
.pfm-toggle-cb    { position:relative; display:inline-block; width:2rem; height:1.125rem; }
.pfm-toggle-cb input { opacity:0; width:0; height:0; }
.pfm-toggle-slider { position:absolute; inset:0; background:#d1d5db; border-radius:9999px;
                     cursor:pointer; transition:background .2s; }
.pfm-toggle-slider:before { content:''; position:absolute; height:.75rem; width:.75rem;
                             left:.1875rem; bottom:.1875rem; background:#fff;
                             border-radius:50%; transition:transform .2s; }
.pfm-toggle-cb input:checked + .pfm-toggle-slider { background:#10b981; }
.pfm-toggle-cb input:checked + .pfm-toggle-slider:before { transform:translateX(.875rem); }
.pfm-empty { text-align:center; padding:2rem; color:#9ca3af; }
.pfm-empty i { font-size:2rem; margin-bottom:.5rem; display:block; }

/* Modal */
.pfm-overlay { position:fixed; inset:0; background:rgba(0,0,0,.5);
               display:flex; align-items:flex-start; justify-content:center;
               z-index:9999; padding:2rem 1rem; overflow-y:auto; }
.pfm-modal   { background:#fff; border-radius:.75rem; width:100%; max-width:56rem;
               box-shadow:0 20px 50px rgba(0,0,0,.3); margin:auto; }
.pfm-modal-header { display:flex; align-items:center; justify-content:space-between;
                    padding:1rem 1.25rem; border-bottom:1px solid #e5e7eb; }
.pfm-modal-title  { font-size:1rem; font-weight:600; color:#111827; }
.pfm-modal-body   { padding:1.25rem; overflow-y:auto; max-height:70vh; }
.pfm-modal-footer { display:flex; justify-content:flex-end; gap:.5rem;
                    padding:.875rem 1.25rem; border-top:1px solid #e5e7eb;
                    background:#f9fafb; border-radius:0 0 .75rem .75rem; }

/* Entry table */
.pfm-table-wrap { overflow-x:auto; }
.pfm-table { width:100%; border-collapse:collapse; font-size:.8125rem; }
.pfm-table th { background:#f9fafb; text-align:left; padding:.5rem .625rem;
                font-weight:600; color:#374151; border-bottom:1px solid #e5e7eb;
                white-space:nowrap; position:sticky; top:0; }
.pfm-table td { padding:.375rem .625rem; border-bottom:1px solid #f3f4f6;
                vertical-align:top; }
.pfm-table tr.today-row td { background:#fef9c3; }
.pfm-table td input, .pfm-table td textarea {
  width:100%; border:1px solid #d1d5db; border-radius:.25rem;
  padding:.25rem .375rem; font-size:.8125rem; font-family:inherit;
  resize:vertical; background:#fff; }
.pfm-table td input:focus, .pfm-table td textarea:focus {
  outline:none; border-color:#3b82f6; }
.pfm-day-col { width:3rem; text-align:center; font-weight:600;
               color:#6b7280; white-space:nowrap; }
.pfm-pagination { display:flex; align-items:center; justify-content:space-between;
                  margin-top:.75rem; font-size:.8125rem; color:#6b7280; }
.pfm-page-jump  { display:flex; align-items:center; gap:.375rem; font-size:.8125rem; }
.pfm-page-jump input { width:3.5rem; padding:.25rem .375rem; border:1px solid #d1d5db;
                        border-radius:.25rem; text-align:center; }

/* Form in create modal */
.pfm-field { margin-bottom:.875rem; }
.pfm-field label { display:block; font-size:.875rem; font-weight:500;
                   color:#374151; margin-bottom:.25rem; }
.pfm-field input { width:100%; padding:.4rem .625rem; border:1px solid #d1d5db;
                   border-radius:.375rem; font-size:.875rem; }
.pfm-field input:focus { outline:none; border-color:#3b82f6; }
.pfm-field-hint { font-size:.75rem; color:#9ca3af; margin-top:.2rem; }
.pfm-field-error { font-size:.75rem; color:#dc2626; margin-top:.2rem; }

/* Delete danger box */
.pfm-danger-box { background:#fef2f2; border:1px solid #fecaca;
                  border-radius:.5rem; padding:.875rem; font-size:.875rem;
                  color:#991b1b; }
`;
        document.head.appendChild(style);
    }

    // ─── Safe HTML helper ─────────────────────────────────────────────────────

    /**
     * Parse html in a sandboxed DOMParser document (scripts never execute) and
     * replace target's children with the result.  All dynamic values in html
     * must be escaped by the caller before passing here.
     */
    function safeSetHTML(target, html) {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        target.textContent = '';
        const frag = document.createDocumentFragment();
        Array.from(doc.body.childNodes).forEach(function(n) { frag.appendChild(n); });
        target.appendChild(frag);
    }

    // ─── Per-instance state ───────────────────────────────────────────────────

    const _state = new Map(); // fieldId → { pluginId, actions, createFields, files, page, entriesPerPage, modal }

    function getState(fieldId) {
        if (!_state.has(fieldId)) _state.set(fieldId, {
            pluginId: '', actions: {}, createFields: [], uploadHint: '',
            directoryLabel: '', files: [], page: 1, entriesPerPage: 20,
            currentModal: null
        });
        return _state.get(fieldId);
    }

    // ─── API helper ───────────────────────────────────────────────────────────

    async function callAction(pluginId, actionId, params = {}) {
        const resp = await fetch('/api/v3/plugins/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plugin_id: pluginId, action_id: actionId, params })
        });
        return resp.json();
    }

    function notify(msg, type) {
        if (window.showNotification) window.showNotification(msg, type);
        else console.log(`[PFM][${type}] ${msg}`);
    }

    function escHtml(s) {
        const d = document.createElement('div');
        d.textContent = String(s ?? '');
        return d.innerHTML;
    }

    function formatSize(bytes) {
        if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
        return (bytes / 1024).toFixed(2) + ' KB';
    }

    function formatDate(iso) {
        try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }); }
        catch { return iso; }
    }

    // ─── Core: load files ─────────────────────────────────────────────────────

    async function loadFiles(fieldId) {
        const st = getState(fieldId);
        const root = document.getElementById(`${fieldId}_pfm`);
        if (!root) return;
        const grid = root.querySelector('.pfm-grid');
        if (grid) safeSetHTML(grid, '<div class="pfm-empty"><i class="fas fa-spinner fa-spin"></i>Loading…</div>');

        const data = await callAction(st.pluginId, st.actions.list).catch(() => null);
        if (!data || data.status !== 'success') {
            if (grid) safeSetHTML(grid, '<div class="pfm-empty"><i class="fas fa-exclamation-circle"></i>Failed to load files.</div>');
            return;
        }
        st.files = data.files || [];
        renderCards(fieldId);
    }

    // ─── Card grid ────────────────────────────────────────────────────────────

    function renderCards(fieldId) {
        const st = getState(fieldId);
        const root = document.getElementById(`${fieldId}_pfm`);
        if (!root) return;
        const grid = root.querySelector('.pfm-grid');
        if (!grid) return;

        if (!st.files.length) {
            safeSetHTML(grid, '<div class="pfm-empty"><i class="fas fa-folder-open"></i>No files yet. Create or upload one.</div>');
            return;
        }

        // Remove any existing delegated listener before re-render
        if (st._gridClickHandler) grid.removeEventListener('click', st._gridClickHandler);
        if (st._gridChangeHandler) grid.removeEventListener('change', st._gridChangeHandler);

        // Event delegation: handles edit/delete/toggle via data attributes so
        // filenames and category names are never interpolated into JS string literals.
        st._gridClickHandler = function(e) {
            const btn = e.target.closest('[data-pfm-action]');
            if (!btn) return;
            const action = btn.dataset.pfmAction;
            const fId    = btn.dataset.pfmField;
            if (action === 'edit')   window._pfmOpenEdit(fId, btn.dataset.pfmFile);
            if (action === 'delete') window._pfmOpenDelete(fId, btn.dataset.pfmFile);
        };
        st._gridChangeHandler = function(e) {
            const inp = e.target.closest('[data-pfm-action="toggle"]');
            if (!inp) return;
            window._pfmToggle(inp.dataset.pfmField, inp.dataset.pfmCategory, inp.checked);
        };
        grid.addEventListener('click',  st._gridClickHandler);
        grid.addEventListener('change', st._gridChangeHandler);

        safeSetHTML(grid, st.files.map(f => `
            <div class="pfm-card${f.enabled === false ? ' disabled' : ''}" data-filename="${escHtml(f.filename)}" data-category="${escHtml(f.category_name)}">
                <div class="pfm-card-top">
                    <span class="pfm-toggle-label">${f.enabled !== false ? 'Enabled' : 'Disabled'}</span>
                    ${st.actions.toggle ? `
                    <label class="pfm-toggle-cb" title="${f.enabled !== false ? 'Click to disable' : 'Click to enable'}">
                        <input type="checkbox" ${f.enabled !== false ? 'checked' : ''}
                            data-pfm-action="toggle" data-pfm-field="${escHtml(fieldId)}"
                            data-pfm-category="${escHtml(f.category_name)}">
                        <span class="pfm-toggle-slider"></span>
                    </label>` : ''}
                </div>
                <div class="pfm-card-icon"><i class="fas fa-file-code"></i></div>
                <div class="pfm-card-name">${escHtml(f.display_name || f.filename)}</div>
                <div class="pfm-card-meta">
                    ${escHtml(f.filename)}<br>
                    ${f.entry_count != null ? escHtml(f.entry_count) + ' entries' : ''}&nbsp;•&nbsp;${formatSize(f.size)}<br>
                    ${formatDate(f.modified)}
                </div>
                <div class="pfm-card-actions">
                    ${st.actions.get && st.actions.save ? `
                    <button class="pfm-btn pfm-btn-primary"
                            data-pfm-action="edit" data-pfm-field="${escHtml(fieldId)}"
                            data-pfm-file="${escHtml(f.filename)}">
                        <i class="fas fa-edit"></i> Edit
                    </button>` : ''}
                    ${st.actions.delete ? `
                    <button class="pfm-btn pfm-btn-danger pfm-btn-sm"
                            data-pfm-action="delete" data-pfm-field="${escHtml(fieldId)}"
                            data-pfm-file="${escHtml(f.filename)}">
                        <i class="fas fa-trash"></i>
                    </button>` : ''}
                </div>
            </div>`).join('');
    }

    // ─── Edit modal ───────────────────────────────────────────────────────────

    window._pfmOpenEdit = async function (fieldId, filename) {
        const st = getState(fieldId);
        const overlay = createOverlay(fieldId);
        // Build modal using DOM methods so filename never enters a JS string literal.
        const modal = document.createElement('div');
        modal.className = 'pfm-modal';
        safeSetHTML(modal, `
            <div class="pfm-modal-header">
                <span class="pfm-modal-title"><i class="fas fa-edit mr-2"></i>${escHtml(filename)}</span>
                <button class="pfm-btn pfm-btn-secondary pfm-btn-sm" id="${escHtml(fieldId)}_modal_close">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="pfm-modal-body" id="${escHtml(fieldId)}_edit_body">
                <div class="pfm-empty"><i class="fas fa-spinner fa-spin"></i>Loading…</div>
            </div>
            <div class="pfm-modal-footer">
                <button class="pfm-btn pfm-btn-secondary" id="${escHtml(fieldId)}_modal_cancel">Cancel</button>
                <button class="pfm-btn pfm-btn-primary" id="${escHtml(fieldId)}_save_btn">
                    <i class="fas fa-save mr-1"></i>Save
                </button>
            </div>`;
        overlay.appendChild(modal);
        // Bind events after DOM insertion — filename captured in closure, not in HTML.
        modal.querySelector(`#${CSS.escape(fieldId)}_modal_close`).addEventListener('click', () => window._pfmCloseModal(fieldId));
        modal.querySelector(`#${CSS.escape(fieldId)}_modal_cancel`).addEventListener('click', () => window._pfmCloseModal(fieldId));
        modal.querySelector(`#${CSS.escape(fieldId)}_save_btn`).addEventListener('click', () => window._pfmSave(fieldId, filename));

        const data = await callAction(st.pluginId, st.actions.get, { filename }).catch(() => null);
        const body = document.getElementById(`${fieldId}_edit_body`);
        if (!data || data.status !== 'success' || !body) {
            if (body) safeSetHTML(body, '<div class="pfm-empty" style="color:#dc2626">Failed to load file.</div>');
            return;
        }

        const content = data.content || data.data || {};
        st._editFilename = filename;

        if (isTabular(content)) {
            // Table path: track cell edits live in _editData
            st._editData = content;
            renderEntryTable(fieldId, body, content);
        } else {
            // Textarea path: _editData stays null; save() reads from the <textarea>
            st._editData = null;
            safeSetHTML(body, `
                <textarea id="${escHtml(fieldId)}_json_ta" rows="20"
                    style="width:100%;font-family:monospace;font-size:.75rem;border:1px solid #d1d5db;border-radius:.375rem;padding:.5rem;"
                >${escHtml(JSON.stringify(content, null, 2))}</textarea>
                <div id="${escHtml(fieldId)}_json_err" style="color:#dc2626;font-size:.75rem;margin-top:.25rem;"></div>`;
        }
    };

    function isTabular(data) {
        if (typeof data !== 'object' || Array.isArray(data)) return false;
        const keys = Object.keys(data);
        if (!keys.length) return false;
        const first = data[keys[0]];
        if (typeof first !== 'object' || Array.isArray(first)) return false;
        const entryKeys = Object.keys(first);
        return entryKeys.length > 0 && entryKeys.length <= 8;
    }

    function renderEntryTable(fieldId, container, content) {
        const st = getState(fieldId);
        const entries = Object.entries(content).sort((a, b) => parseInt(a[0]) - parseInt(b[0]));
        if (!entries.length) { container.textContent = 'No entries.'; return; }

        const cols = Object.keys(entries[0][1]);
        const MS_PER_DAY = 86400 * 1000; // eslint-disable-line no-magic-numbers -- 86400s/day is not magic
        const todayDoy = Math.ceil((new Date() - new Date(new Date().getFullYear(), 0, 0)) / MS_PER_DAY);
        const total = entries.length;
        const perPage = st.entriesPerPage;

        function buildPage(page) {
            const start = (page - 1) * perPage; // eslint-disable-line no-magic-numbers
            const pageEntries = entries.slice(start, start + perPage);
            const totalPages = Math.ceil(total / perPage);

            safeSetHTML(container, `
                <div class="pfm-table-info" style="font-size:.75rem;color:#6b7280;margin-bottom:.375rem;">
                    ${total} entries total
                    <button class="pfm-btn pfm-btn-secondary pfm-btn-sm" style="margin-left:.5rem"
                        onclick="(function(){const targetPage=Math.ceil(${todayDoy}/${perPage});window._pfmTablePage('${fieldId}',targetPage);setTimeout(function(){const row=document.querySelector('tr[data-day=\\'${todayDoy}\\']');if(row)row.scrollIntoView({block:'center'});},60);})()">
                        <i class="fas fa-calendar-day"></i> Jump to today (day ${todayDoy})
                    </button>
                </div>
                <div id="${fieldId}_tbl_wrap" class="pfm-table-wrap" style="max-height:52vh;overflow-y:auto;">
                    <table class="pfm-table">
                        <thead>
                            <tr>
                                <th class="pfm-day-col">Day</th>
                                ${cols.map(c => `<th>${escHtml(c.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()))}</th>`).join('')}
                            </tr>
                        </thead>
                        <tbody>
                            ${pageEntries.map(([day, val]) => `
                            <tr data-day="${day}" class="${parseInt(day) === todayDoy ? 'today-row' : ''}">
                                <td class="pfm-day-col" style="user-select:none;">${escHtml(day)}</td>
                                ${cols.map(col => {
                                    const v = val[col] ?? '';
                                    const isLong = String(v).length > 60 || col === 'description' || col === 'definition' || col === 'content';
                                    return isLong
                                        ? `<td><textarea data-day="${day}" data-col="${escHtml(col)}" rows="2"
                                            oninput="window._pfmCellEdit('${fieldId}','${day}','${escHtml(col)}',this.value)"
                                            >${escHtml(String(v))}</textarea></td>`
                                        : `<td><input type="text" data-day="${day}" data-col="${escHtml(col)}"
                                            value="${escHtml(String(v))}"
                                            oninput="window._pfmCellEdit('${fieldId}','${day}','${escHtml(col)}',this.value)"></td>`;
                                }).join('')}
                            </tr>`).join('')}
                        </tbody>
                    </table>
                </div>
                <div class="pfm-pagination">
                    <span>Page ${page} of ${totalPages}</span>
                    <div class="pfm-page-jump">
                        <button class="pfm-btn pfm-btn-secondary pfm-btn-sm"
                                ${page <= 1 ? 'disabled' : ''}
                                onclick="window._pfmTablePage('${fieldId}',${page - 1})">‹ Prev</button>
                        <span>Go to</span>
                        <input type="number" min="1" max="${totalPages}" value="${page}"
                            onchange="window._pfmTablePage('${fieldId}',+this.value)">
                        <button class="pfm-btn pfm-btn-secondary pfm-btn-sm"
                                ${page >= totalPages ? 'disabled' : ''}
                                onclick="window._pfmTablePage('${fieldId}',${page + 1})">Next ›</button>
                    </div>
                </div>`;
            st._tablePage = page;
            st._tableEntries = entries;
            st._tableCols = cols;
        }

        // Store buildPage in per-instance state so multiple instances don't
        // clobber each other's pagination via a shared global.
        st._buildPage = buildPage;
        buildPage(st._tablePage || 1);
    }

    // Global dispatcher — resolves the per-instance buildPage from state so
    // multiple plugin-file-manager instances don't clobber each other.
    window._pfmTablePage = function (fId, p) {
        const s = getState(fId);
        if (s._buildPage) {
            const total = s._tableEntries ? s._tableEntries.length : 0;
            const totalP = Math.ceil(total / s.entriesPerPage) || 1;
            s._buildPage(Math.max(1, Math.min(p, totalP)));
        }
    };

    window._pfmCellEdit = function (fieldId, day, col, value) {
        const st = getState(fieldId);
        if (st._editData && st._editData[day]) st._editData[day][col] = value;
    };

    window._pfmSave = async function (fieldId, filename) {
        const st = getState(fieldId);
        const saveBtn = document.getElementById(`${fieldId}_save_btn`);
        let content;

        // Try getting from inline table data first, then textarea fallback
        if (st._editData) {
            content = st._editData;
        } else {
            const ta = document.getElementById(`${fieldId}_json_ta`);
            if (!ta) return;
            try { content = JSON.parse(ta.value); }
            catch (e) {
                const errEl = document.getElementById(`${fieldId}_json_err`);
                if (errEl) errEl.textContent = 'Invalid JSON: ' + e.message;
                return;
            }
        }

        if (saveBtn) { saveBtn.disabled = true; (function(b){b.textContent='';const i=document.createElement('i');i.className='fas fa-spinner fa-spin mr-1';b.appendChild(i);b.appendChild(document.createTextNode('Saving…'));})(saveBtn); }

        const result = await callAction(st.pluginId, st.actions.save, {
            filename, content: JSON.stringify(content)
        }).catch(() => ({ status: 'error', message: 'Network error' }));

        if (saveBtn) { saveBtn.disabled = false; (function(b){b.textContent='';const i=document.createElement('i');i.className='fas fa-save mr-1';b.appendChild(i);b.appendChild(document.createTextNode('Save'));})(saveBtn); }

        if (result.status === 'success') {
            notify('File saved successfully', 'success');
            window._pfmCloseModal(fieldId);
            await loadFiles(fieldId);
        } else {
            notify('Save failed: ' + (result.message || 'Unknown error'), 'error');
        }
    };

    // ─── Delete modal ─────────────────────────────────────────────────────────

    window._pfmOpenDelete = function (fieldId, filename) {
        const overlay = createOverlay(fieldId);
        const modal = document.createElement('div');
        modal.className = 'pfm-modal';
        modal.style.maxWidth = '28rem';
        safeSetHTML(modal, `
            <div class="pfm-modal-header">
                <span class="pfm-modal-title"><i class="fas fa-trash mr-2"></i>Delete File</span>
                <button class="pfm-btn pfm-btn-secondary pfm-btn-sm" id="${escHtml(fieldId)}_del_close">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="pfm-modal-body">
                <div class="pfm-danger-box">
                    <strong>${escHtml(filename)}</strong> will be permanently deleted and removed
                    from the plugin configuration. This cannot be undone.
                </div>
            </div>
            <div class="pfm-modal-footer">
                <button class="pfm-btn pfm-btn-secondary" id="${escHtml(fieldId)}_del_cancel">Cancel</button>
                <button class="pfm-btn pfm-btn-danger" id="${escHtml(fieldId)}_del_confirm">
                    <i class="fas fa-trash mr-1"></i>Delete
                </button>
            </div>`;
        overlay.appendChild(modal);
        modal.querySelector(`#${CSS.escape(fieldId)}_del_close`).addEventListener('click', () => window._pfmCloseModal(fieldId));
        modal.querySelector(`#${CSS.escape(fieldId)}_del_cancel`).addEventListener('click', () => window._pfmCloseModal(fieldId));
        modal.querySelector(`#${CSS.escape(fieldId)}_del_confirm`).addEventListener('click', () => window._pfmConfirmDelete(fieldId, filename));
    };

    window._pfmConfirmDelete = async function (fieldId, filename) {
        const st = getState(fieldId);
        const result = await callAction(st.pluginId, st.actions.delete, { filename })
            .catch(() => ({ status: 'error', message: 'Network error' }));
        if (result.status === 'success') {
            notify('File deleted', 'success');
            window._pfmCloseModal(fieldId);
            await loadFiles(fieldId);
        } else {
            notify('Delete failed: ' + (result.message || ''), 'error');
        }
    };

    // ─── Create modal ─────────────────────────────────────────────────────────

    window._pfmOpenCreate = function (fieldId) {
        const st = getState(fieldId);
        const fields = st.createFields;
        const overlay = createOverlay(fieldId);
        const modal = document.createElement('div');
        modal.className = 'pfm-modal';
        modal.style.maxWidth = '32rem';
        safeSetHTML(modal, `
            <div class="pfm-modal-header">
                <span class="pfm-modal-title"><i class="fas fa-plus-circle mr-2"></i>Create New File</span>
                <button class="pfm-btn pfm-btn-secondary pfm-btn-sm" id="${escHtml(fieldId)}_cre_close">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="pfm-modal-body">
                <div id="${escHtml(fieldId)}_create_err" class="pfm-field-error" style="margin-bottom:.5rem;"></div>
                ${fields.map(f => `
                <div class="pfm-field">
                    <label for="${escHtml(fieldId)}_cf_${escHtml(f.key)}">${escHtml(f.label || f.key)}</label>
                    <input type="text" id="${escHtml(fieldId)}_cf_${escHtml(f.key)}"
                           placeholder="${escHtml(f.placeholder || '')}"
                           ${f.pattern ? `pattern="${escHtml(f.pattern)}"` : ''}>
                    ${f.hint ? `<div class="pfm-field-hint">${escHtml(f.hint)}</div>` : ''}
                </div>`).join('')}
            </div>
            <div class="pfm-modal-footer">
                <button class="pfm-btn pfm-btn-secondary" id="${escHtml(fieldId)}_cre_cancel">Cancel</button>
                <button class="pfm-btn pfm-btn-create" id="${escHtml(fieldId)}_create_btn">
                    <i class="fas fa-plus mr-1"></i>Create
                </button>
            </div>
            </div>`;
        overlay.appendChild(modal);
        modal.querySelector(`#${CSS.escape(fieldId)}_cre_close`).addEventListener('click', () => window._pfmCloseModal(fieldId));
        modal.querySelector(`#${CSS.escape(fieldId)}_cre_cancel`).addEventListener('click', () => window._pfmCloseModal(fieldId));
        modal.querySelector(`#${CSS.escape(fieldId)}_create_btn`).addEventListener('click', () => window._pfmConfirmCreate(fieldId));
    };

    window._pfmConfirmCreate = async function (fieldId) {
        const st = getState(fieldId);
        const errEl = document.getElementById(`${fieldId}_create_err`);
        const btn = document.getElementById(`${fieldId}_create_btn`);
        const params = {};

        for (const f of st.createFields) {
            const inp = document.getElementById(`${fieldId}_cf_${f.key}`);
            if (!inp) continue;
            const val = inp.value.trim();
            // Client-side pattern validation omitted — server-side create-file script validates.
            params[f.key] = val;
        }

        if (btn) { btn.disabled = true; (function(b){b.textContent='';const i=document.createElement('i');i.className='fas fa-spinner fa-spin mr-1';b.appendChild(i);b.appendChild(document.createTextNode('Creating…'));})(btn); }
        if (errEl) errEl.textContent = '';

        const result = await callAction(st.pluginId, st.actions.create, params)
            .catch(() => ({ status: 'error', message: 'Network error' }));

        if (btn) { btn.disabled = false; (function(b){b.textContent='';const i=document.createElement('i');i.className='fas fa-plus mr-1';b.appendChild(i);b.appendChild(document.createTextNode('Create'));})(btn); }

        if (result.status === 'success') {
            notify('File created', 'success');
            window._pfmCloseModal(fieldId);
            await loadFiles(fieldId);
        } else {
            if (errEl) errEl.textContent = result.message || 'Create failed';
        }
    };

    // ─── Toggle ───────────────────────────────────────────────────────────────

    window._pfmToggle = async function (fieldId, categoryName, enabled) {
        const st = getState(fieldId);
        const result = await callAction(st.pluginId, st.actions.toggle, { category_name: categoryName, enabled })
            .catch(() => ({ status: 'error' }));
        if (result.status === 'success') {
            notify(enabled ? `${categoryName} enabled` : `${categoryName} disabled`, 'success');
            await loadFiles(fieldId);
        } else {
            notify('Toggle failed', 'error');
            await loadFiles(fieldId); // revert UI
        }
    };

    // ─── Upload ───────────────────────────────────────────────────────────────

    window._pfmUpload = async function (fieldId, file) {
        const st = getState(fieldId);
        const notifyFn = window.showNotification || console.log;
        if (!file.name.toLowerCase().endsWith('.json')) {
            notifyFn('Only .json files can be uploaded', 'error'); return;
        }
        let content;
        try { content = await file.text(); JSON.parse(content); }
        catch { notifyFn('File contains invalid JSON', 'error'); return; }

        const result = await callAction(st.pluginId, st.actions.upload, {
            filename: file.name, content
        }).catch(() => ({ status: 'error', message: 'Network error' }));

        if (result.status === 'success') {
            notify('File uploaded: ' + (result.filename || file.name), 'success');
            await loadFiles(fieldId);
        } else {
            notify('Upload failed: ' + (result.message || ''), 'error');
        }
    };

    // ─── Modal helpers ────────────────────────────────────────────────────────

    function createOverlay(fieldId) {
        window._pfmCloseModal(fieldId); // close any open modal first
        const overlay = document.createElement('div');
        overlay.className = 'pfm-overlay';
        overlay.id = `${fieldId}_pfm_overlay`;
        // Close on backdrop click
        overlay.addEventListener('click', e => { if (e.target === overlay) window._pfmCloseModal(fieldId); });
        document.body.appendChild(overlay);
        getState(fieldId).currentModal = overlay;
        return overlay;
    }

    window._pfmCloseModal = function (fieldId) {
        const st = getState(fieldId);
        if (st.currentModal) { st.currentModal.remove(); st.currentModal = null; }
        st._editData = null;
        st._editFilename = null;
    };

    // ─── Widget registration ──────────────────────────────────────────────────

    window.LEDMatrixWidgets.register('plugin-file-manager', {
        name: 'Plugin File Manager Widget',
        version: '1.0.0',

        render: function (container, config, value, options) {
            const fieldId  = (options.fieldId || container.id || 'pfm').replace(/[^a-zA-Z0-9_-]/g, '_');
            const wc       = config['x-widget-config'] || {};
            const actions  = wc.actions        || {};
            const pluginId = options.pluginId   || '';

            const st = getState(fieldId);
            Object.assign(st, {
                pluginId,
                actions,
                createFields:   wc.create_fields    || [],
                uploadHint:     wc.upload_hint       || 'Upload JSON files',
                directoryLabel: wc.directory_label   || ''
            });

            safeSetHTML(container, `
                <div class="pfm-root" id="${fieldId}_pfm">
                    <div class="pfm-header">
                        <div>
                            <div class="pfm-title">File Explorer</div>
                            ${st.directoryLabel ? `<div class="pfm-dir">Manage files in <code>${escHtml(st.directoryLabel)}</code></div>` : ''}
                        </div>
                        <div style="display:flex;gap:.375rem;">
                            ${actions.create ? `
                            <button class="pfm-btn pfm-btn-create"
                                    onclick="window._pfmOpenCreate('${fieldId}')">
                                <i class="fas fa-plus mr-1"></i>New File
                            </button>` : ''}
                        </div>
                    </div>

                    ${actions.upload ? `
                    <div class="pfm-upload" id="${fieldId}_upload_zone"
                         onclick="document.getElementById('${fieldId}_file_input').click()"
                         ondragover="event.preventDefault();this.classList.add('dragover')"
                         ondragleave="this.classList.remove('dragover')"
                         ondrop="this.classList.remove('dragover');event.preventDefault();
                                 if(event.dataTransfer.files[0])window._pfmUpload('${fieldId}',event.dataTransfer.files[0])">
                        <input type="file" id="${fieldId}_file_input" accept=".json"
                               style="display:none"
                               onchange="if(this.files[0])window._pfmUpload('${fieldId}',this.files[0]);this.value=''">
                        <i class="fas fa-cloud-upload-alt" style="font-size:1.5rem;color:#9ca3af;"></i>
                        <p>Drag and drop or click to upload</p>
                        <small>${escHtml(st.uploadHint)}</small>
                    </div>` : ''}

                    <div class="pfm-grid">
                        <div class="pfm-empty"><i class="fas fa-spinner fa-spin"></i>Loading…</div>
                    </div>
                </div>`;

            loadFiles(fieldId);
        },

        getValue: function () { return null; }, // file ops are immediate; nothing to submit
        setValue: function (fieldId) { loadFiles(fieldId); }
    });

    console.log('[PluginFileManager] plugin-file-manager widget registered');
})();
