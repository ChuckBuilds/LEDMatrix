/**
 * JsonFileManager — reusable JSON file management widget for LEDMatrix plugins.
 *
 * Usage via config_schema.json:
 *   "file_manager": {
 *     "type": "null",
 *     "title": "Data Files",
 *     "x-widget": "json-file-manager",
 *     "x-widget-config": {
 *       "actions": {
 *         "list":   "list-files",   // required
 *         "get":    "get-file",     // required for editing
 *         "save":   "save-file",    // required for editing
 *         "upload": "upload-file",  // optional
 *         "delete": "delete-file",  // optional
 *         "create": "create-file",  // optional
 *         "toggle": "toggle-category" // optional
 *       },
 *       "upload_hint":      "Hint text under the drop zone",
 *       "directory_label":  "of_the_day/",
 *       "create_fields": [
 *         { "key": "category_name", "label": "Category Name",
 *           "placeholder": "my_words", "pattern": "^[a-z0-9_]+$",
 *           "hint": "Used as filename" },
 *         { "key": "display_name", "label": "Display Name",
 *           "placeholder": "My Words" }
 *       ],
 *       "toggle_key": "category_name"
 *     }
 *   }
 *
 * No CDN dependencies. Works on all modern browsers.
 */
(function () {
    'use strict';

    class JsonFileManager {
        constructor(container, config, pluginId) {
            // Prevent duplicate instances on the same container
            if (container._jfmInstance) {
                container._jfmInstance._destroy();
            }
            container._jfmInstance = this;

            this.el         = container;
            this.pluginId   = pluginId;
            this.actions    = config.actions || {};
            this.uploadHint = config.upload_hint || '';
            this.dirLabel   = config.directory_label || '';
            this.createFields = config.create_fields || [];
            this.toggleKey  = config.toggle_key || null;

            // Unique prefix for all DOM IDs in this instance
            this._uid = 'jfm_' + Array.from(crypto.getRandomValues(new Uint8Array(4)), b => b.toString(16).padStart(2, '0')).join('');

            // Mutable state
            this._editFile   = null;
            this._deleteFile = null;
            this._keyHandler = this._onKey.bind(this);
            // Focus-trap release functions for open modals (utils/dialog.js)
            this._releases   = {};

            this._inject();
            this._bind();
            this._loadList();
        }

        // ── Lifecycle ────────────────────────────────────────────────────────

        _destroy() {
            document.removeEventListener('keydown', this._keyHandler);
            Object.keys(this._releases).forEach(k => this._release(k));
            this.el._jfmInstance = null;
        }

        // Open a modal's focus trap. Escape is routed here, so _onKey no
        // longer handles it.
        _trap(key, modal, titleId, initialFocus, onClose) {
            if (!modal || !window.LEDDialog) return;
            const box = modal.querySelector('.jfm-modal-box');
            this._releases[key] = window.LEDDialog.trap(box, {
                labelledBy: titleId,
                initialFocus: initialFocus || null,
                onEscape: onClose
            });
        }

        _release(key) {
            const release = this._releases[key];
            if (release) {
                delete this._releases[key];
                release();
            }
        }

        // ── DOM Injection ────────────────────────────────────────────────────

        _inject() {
            const u = this._uid;
            const hasUpload = !!this.actions.upload;
            const hasCreate = !!this.actions.create;
            const hasDelete = !!this.actions.delete;

            this.el.innerHTML = this._css(u) + `
<div id="${u}" class="jfm">

  <div class="jfm-header">
    <div class="jfm-header-left">
      <span class="jfm-title">Data Files</span>
      ${this.dirLabel ? `<code class="jfm-dir">${this._esc(this.dirLabel)}</code>` : ''}
    </div>
    <div class="jfm-header-right">
      ${hasCreate ? `<button type="button" class="jfm-btn jfm-btn-primary jfm-btn-sm" data-jfm="open-create">+ New File</button>` : ''}
      <button type="button" class="jfm-btn jfm-btn-ghost jfm-btn-sm" data-jfm="refresh" title="Refresh file list">&#8635;</button>
    </div>
  </div>

  <div id="${u}-list" class="jfm-list">
    <div class="jfm-loading"><span class="jfm-spin"></span> Loading…</div>
  </div>

  ${hasUpload ? `
  <div class="jfm-upload-wrap">
    <input type="file" accept=".json" id="${u}-fileinput" tabindex="-1">
    <div class="jfm-dropzone" id="${u}-dropzone" data-jfm="open-picker" role="button" tabindex="0"
         aria-label="Upload JSON file">
      <span class="jfm-drop-icon">&#128193;</span>
      <p class="jfm-drop-primary">Drop a JSON file here, or click to browse</p>
      ${this.uploadHint ? `<p class="jfm-drop-hint">${this._esc(this.uploadHint)}</p>` : ''}
    </div>
  </div>` : ''}

  <!-- ── Edit modal ─────────────────────────────────────── -->
  <div class="jfm-modal" id="${u}-edit-modal" hidden>
    <div class="jfm-modal-box jfm-modal-wide">
      <div class="jfm-modal-head">
        <span id="${u}-edit-title" class="jfm-modal-title">Edit file</span>
        <div class="jfm-modal-tools">
          <button type="button" class="jfm-btn jfm-btn-ghost jfm-btn-sm" data-jfm="fmt">Format</button>
          <button type="button" class="jfm-btn jfm-btn-ghost jfm-btn-sm" data-jfm="validate">Validate</button>
          <button type="button" class="jfm-close-btn" data-jfm="close-edit" aria-label="Close editor">&times;</button>
        </div>
      </div>
      <div id="${u}-edit-err" class="jfm-err-bar" role="alert" hidden></div>
      <textarea id="${u}-editor" class="jfm-editor"
                spellcheck="false" autocomplete="off"
                autocorrect="off" autocapitalize="off"
                aria-label="JSON editor" aria-describedby="${u}-editor-hint"></textarea>
      <span id="${u}-editor-hint" hidden>Tab inserts two spaces. Shift+Tab moves focus back to the toolbar. Ctrl+S saves.</span>
      <div class="jfm-modal-foot">
        <span id="${u}-charcount" class="jfm-stat"></span>
        <button type="button" class="jfm-btn jfm-btn-ghost" data-jfm="close-edit">Cancel</button>
        <button type="button" class="jfm-btn jfm-btn-primary" data-jfm="save" id="${u}-save-btn">Save</button>
      </div>
    </div>
  </div>

  <!-- ── Delete modal ───────────────────────────────────── -->
  ${hasDelete ? `
  <div class="jfm-modal" id="${u}-del-modal" hidden>
    <div class="jfm-modal-box">
      <div class="jfm-modal-head">
        <span id="${u}-del-title" class="jfm-modal-title">Delete file</span>
        <button type="button" class="jfm-close-btn" data-jfm="close-del" aria-label="Close">&times;</button>
      </div>
      <div class="jfm-modal-body">
        <p>Delete <strong id="${u}-del-name"></strong>?</p>
        <p class="jfm-muted">This permanently removes the file and its entry from the plugin configuration.</p>
      </div>
      <div class="jfm-modal-foot">
        <button type="button" class="jfm-btn jfm-btn-ghost" data-jfm="close-del" id="${u}-del-cancel">Cancel</button>
        <button type="button" class="jfm-btn jfm-btn-danger" data-jfm="confirm-del" id="${u}-del-btn">Delete</button>
      </div>
    </div>
  </div>` : ''}

  <!-- ── Create modal ───────────────────────────────────── -->
  ${hasCreate ? `
  <div class="jfm-modal" id="${u}-create-modal" hidden>
    <div class="jfm-modal-box">
      <div class="jfm-modal-head">
        <span id="${u}-create-title" class="jfm-modal-title">Create new file</span>
        <button type="button" class="jfm-close-btn" data-jfm="close-create" aria-label="Close">&times;</button>
      </div>
      <div class="jfm-modal-body">
        ${this.createFields.map(f => `
        <div class="jfm-field">
          <label for="${u}-cf-${this._esc(f.key)}">${this._esc(f.label)}</label>
          <input type="text" id="${u}-cf-${this._esc(f.key)}"
                 placeholder="${this._esc(f.placeholder || '')}"
                 ${f.pattern ? `pattern="${this._esc(f.pattern)}"` : ''}>
          ${f.hint ? `<span class="jfm-hint">${this._esc(f.hint)}</span>` : ''}
        </div>`).join('')}
      </div>
      <div class="jfm-modal-foot">
        <button type="button" class="jfm-btn jfm-btn-ghost" data-jfm="close-create">Cancel</button>
        <button type="button" class="jfm-btn jfm-btn-primary" data-jfm="do-create" id="${u}-create-btn">Create</button>
      </div>
    </div>
  </div>` : ''}

</div>`;  // end #${u}

            // Cache frequently-used elements
            this._root        = document.getElementById(u);
            this._listEl      = document.getElementById(`${u}-list`);
            this._editorEl    = document.getElementById(`${u}-editor`);
            this._editModal   = document.getElementById(`${u}-edit-modal`);
            this._delModal    = document.getElementById(`${u}-del-modal`);
            this._createModal = document.getElementById(`${u}-create-modal`);
            this._dropzone    = document.getElementById(`${u}-dropzone`);
            this._fileInput   = document.getElementById(`${u}-fileinput`);
        }

        _css(u) {
            // Colors come from the app theme tokens (app.css :root / [data-theme="dark"])
            // with the original light values as fallbacks for pages without app.css.
            // Shades the tokens don't cover are local variables with a dark override.
            return `<style>
#${u}{--jfm-border-strong:#d1d5db;--jfm-border-hover:#9ca3af;--jfm-subtle:#f3f4f6;--jfm-accent-soft:#93c5fd;--jfm-danger-border:#fecaca;--jfm-danger-text:#991b1b;--jfm-text-muted:#6b7280;}
[data-theme="dark"] #${u}{--jfm-border-strong:#4b5563;--jfm-border-hover:#6b7280;--jfm-subtle:#374151;--jfm-accent-soft:#1e40af;--jfm-danger-border:#991b1b;--jfm-danger-text:#fecaca;--jfm-text-muted:#9ca3af;}
#${u}{font-family:inherit;color:var(--color-text-primary,#111827);}
#${u} *{box-sizing:border-box;}

/* Header */
#${u} .jfm-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:.875rem;gap:.5rem;}
#${u} .jfm-header-left{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;}
#${u} .jfm-title{font-size:.9375rem;font-weight:600;color:var(--color-text-primary,#111827);}
#${u} .jfm-dir{font-size:.75rem;color:var(--jfm-text-muted);background:var(--jfm-subtle);padding:.125rem .375rem;border-radius:.25rem;font-family:monospace;}
#${u} .jfm-header-right{display:flex;gap:.375rem;align-items:center;flex-shrink:0;}

/* Buttons */
#${u} .jfm-btn{display:inline-flex;align-items:center;gap:.25rem;padding:.4375rem .875rem;border-radius:.375rem;border:1px solid var(--jfm-border-strong);background:var(--color-surface,#fff);color:var(--color-text-secondary,#374151);font-size:.875rem;font-weight:500;cursor:pointer;transition:background .12s,border-color .12s,opacity .12s;line-height:1.25;}
#${u} .jfm-btn:hover:not(:disabled){background:var(--color-background,#f9fafb);border-color:var(--jfm-border-hover);}
#${u} .jfm-btn:focus-visible,#${u} .jfm-close-btn:focus-visible{outline:2px solid var(--color-primary,#3b82f6);outline-offset:1px;}
#${u} .jfm-btn:disabled{opacity:.5;cursor:not-allowed;}
#${u} .jfm-btn-sm{padding:.3125rem .625rem;font-size:.8125rem;}
#${u} .jfm-btn-primary{background:var(--color-primary,#3b82f6);border-color:var(--color-primary,#3b82f6);color:#fff;}
#${u} .jfm-btn-primary:hover:not(:disabled){background:var(--color-primary-hover,#2563eb);border-color:var(--color-primary-hover,#2563eb);}
#${u} .jfm-btn-danger{background:#dc2626;border-color:#dc2626;color:#fff;}
#${u} .jfm-btn-danger:hover:not(:disabled){background:#b91c1c;border-color:#b91c1c;}
#${u} .jfm-btn-ghost{background:transparent;border-color:transparent;color:var(--jfm-text-muted);}
#${u} .jfm-btn-ghost:hover:not(:disabled){background:var(--jfm-subtle);color:var(--color-text-secondary,#374151);}
#${u} .jfm-close-btn{display:flex;align-items:center;justify-content:center;width:2rem;height:2rem;border:none;background:none;color:var(--jfm-text-muted);font-size:1.25rem;cursor:pointer;border-radius:.25rem;padding:0;line-height:1;}
#${u} .jfm-close-btn:hover{background:var(--jfm-subtle);color:var(--color-text-secondary,#374151);}

/* File list */
#${u} .jfm-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:.625rem;margin-bottom:1rem;min-height:5rem;}
#${u} .jfm-loading{grid-column:1/-1;display:flex;align-items:center;justify-content:center;gap:.5rem;padding:2rem;color:var(--jfm-text-muted);font-size:.875rem;}
#${u} .jfm-empty{grid-column:1/-1;text-align:center;padding:2.5rem 1rem;color:var(--jfm-text-muted);}
#${u} .jfm-empty-icon{font-size:2.25rem;margin-bottom:.625rem;}
#${u} .jfm-empty-title{font-weight:600;color:var(--color-text-secondary,#374151);margin:0 0 .25rem;}
#${u} .jfm-empty-sub{font-size:.875rem;margin:0;}

/* File cards */
#${u} .jfm-card{border:1px solid var(--color-border,#e5e7eb);border-radius:.5rem;padding:.875rem;background:var(--color-surface,#fff);display:flex;flex-direction:column;gap:.5rem;transition:border-color .15s,box-shadow .15s;}
#${u} .jfm-card:hover{border-color:var(--jfm-accent-soft);box-shadow:var(--shadow-md,0 2px 8px rgba(59,130,246,.1));}
#${u} .jfm-card.jfm-off{opacity:.6;}
#${u} .jfm-card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:.5rem;}
#${u} .jfm-card-name{font-weight:600;font-size:.9375rem;word-break:break-word;color:var(--color-text-primary,#111827);flex:1;}
#${u} .jfm-card-meta{font-size:.75rem;color:var(--jfm-text-muted);display:flex;flex-direction:column;gap:.125rem;line-height:1.5;}
#${u} .jfm-card-actions{display:flex;gap:.375rem;padding-top:.5rem;border-top:1px solid var(--color-border-light,#f3f4f6);margin-top:.125rem;}
#${u} .jfm-card-actions .jfm-btn{flex:1;justify-content:center;}
#${u} .jfm-card-actions .jfm-del{flex:0 0 auto;}

/* Toggle */
#${u} .jfm-toggle{display:flex;align-items:center;gap:.3125rem;font-size:.75rem;color:var(--jfm-text-muted);white-space:nowrap;flex-shrink:0;}
#${u} .jfm-toggle input[type=checkbox]{width:.9375rem;height:.9375rem;cursor:pointer;accent-color:#22c55e;margin:0;}

/* Upload zone */
#${u} .jfm-upload-wrap{margin-top:.25rem;}
#${u} input[type=file]#${u}-fileinput{position:absolute;left:-9999px;width:1px;height:1px;opacity:0;}
#${u} .jfm-dropzone{border:2px dashed var(--jfm-border-strong);border-radius:.5rem;padding:1.25rem 1rem;text-align:center;cursor:pointer;transition:border-color .15s,background .15s;background:var(--color-background,#f9fafb);user-select:none;}
#${u} .jfm-dropzone:hover,#${u} .jfm-dropzone:focus-visible,#${u} .jfm-dropzone.jfm-over{border-color:var(--color-primary,#3b82f6);background:var(--color-info-bg,#eff6ff);border-style:solid;outline:none;}
#${u} .jfm-drop-icon{font-size:1.75rem;display:block;margin-bottom:.375rem;}
#${u} .jfm-drop-primary{font-size:.875rem;color:var(--color-text-secondary,#374151);margin:0 0 .25rem;}
#${u} .jfm-drop-hint{font-size:.75rem;color:var(--jfm-text-muted);margin:0;}

/* Modals */
#${u} .jfm-modal{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:9999;display:flex;align-items:center;justify-content:center;padding:1rem;backdrop-filter:blur(1px);}
#${u} .jfm-modal[hidden]{display:none;}
#${u} .jfm-modal-box{background:var(--color-surface,#fff);color:var(--color-text-primary,#111827);border-radius:.5rem;box-shadow:0 20px 40px rgba(0,0,0,.15);display:flex;flex-direction:column;width:100%;max-width:440px;max-height:92vh;}
#${u} .jfm-modal-box:focus{outline:none;}
#${u} .jfm-modal-wide{max-width:880px;}
#${u} .jfm-modal-head{display:flex;justify-content:space-between;align-items:center;padding:.875rem 1.125rem;border-bottom:1px solid var(--color-border,#e5e7eb);flex-shrink:0;gap:.5rem;}
#${u} .jfm-modal-title{font-weight:600;font-size:.9375rem;color:var(--color-text-primary,#111827);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
#${u} .jfm-modal-tools{display:flex;gap:.25rem;align-items:center;flex-shrink:0;}
#${u} .jfm-modal-body{padding:1.125rem;overflow-y:auto;flex:1;}
#${u} .jfm-modal-foot{display:flex;gap:.5rem;justify-content:flex-end;align-items:center;padding:.75rem 1.125rem;border-top:1px solid var(--color-border,#e5e7eb);flex-shrink:0;background:var(--color-background,#f9fafb);border-radius:0 0 .5rem .5rem;}
#${u} .jfm-stat{margin-right:auto;font-size:.75rem;color:var(--jfm-text-muted);font-variant-numeric:tabular-nums;}

/* JSON editor */
#${u} .jfm-editor{display:block;width:100%;min-height:400px;height:58vh;max-height:64vh;resize:vertical;font-family:'Courier New',Consolas,ui-monospace,monospace;font-size:.8rem;line-height:1.55;padding:.75rem 1rem;border:none;border-radius:0;outline:none;white-space:pre;overflow:auto;color:var(--color-text-primary,#1e293b);background:var(--color-background,#fafafa);tab-size:2;}
#${u} .jfm-editor:focus-visible{box-shadow:inset 0 0 0 2px var(--color-primary,#3b82f6);}
#${u} .jfm-err-bar{background:var(--color-error-bg,#fef2f2);border-bottom:1px solid var(--jfm-danger-border);color:var(--jfm-danger-text);font-size:.8125rem;padding:.5rem 1.125rem;flex-shrink:0;line-height:1.4;}
#${u} .jfm-err-bar[hidden]{display:none;}

/* Create form */
#${u} .jfm-field{margin-bottom:.875rem;}
#${u} .jfm-field:last-child{margin-bottom:0;}
#${u} .jfm-field label{display:block;font-size:.875rem;font-weight:500;color:var(--color-text-secondary,#374151);margin-bottom:.3125rem;}
#${u} .jfm-field input{width:100%;padding:.4375rem .75rem;border:1px solid var(--jfm-border-strong);border-radius:.375rem;font-size:.875rem;color:var(--color-text-primary,#111827);background:var(--color-surface,#fff);}
#${u} .jfm-field input:focus{outline:none;border-color:var(--color-primary,#3b82f6);box-shadow:0 0 0 3px rgba(59,130,246,.25);}
#${u} .jfm-hint{display:block;font-size:.75rem;color:var(--jfm-text-muted);margin-top:.25rem;}
#${u} .jfm-muted{font-size:.875rem;color:var(--jfm-text-muted);margin-top:.375rem;}

/* Spinner */
#${u} .jfm-spin{display:inline-block;width:.9rem;height:.9rem;border:2px solid var(--color-border,#e5e7eb);border-top-color:var(--color-primary,#3b82f6);border-radius:50%;animation:jfm-spin-${u} .6s linear infinite;vertical-align:middle;}
@keyframes jfm-spin-${u}{to{transform:rotate(360deg);}}

@media (prefers-reduced-motion: reduce){
#${u} .jfm-btn,#${u} .jfm-card,#${u} .jfm-dropzone{transition:none;}
#${u} .jfm-spin{animation-duration:1.5s;}
#${u} .jfm-modal{backdrop-filter:none;}
}
</style>`;
        }

        // ── Event Binding ────────────────────────────────────────────────────

        _bind() {
            // Delegated clicks on the widget root
            this._root.addEventListener('click', this._onClick.bind(this));
            this._root.addEventListener('change', this._onChange.bind(this));

            // Drag-and-drop on the dropzone
            if (this._dropzone) {
                this._dropzone.addEventListener('dragover', e => {
                    e.preventDefault();
                    this._dropzone.classList.add('jfm-over');
                });
                this._dropzone.addEventListener('dragleave', () => {
                    this._dropzone.classList.remove('jfm-over');
                });
                this._dropzone.addEventListener('drop', e => {
                    e.preventDefault();
                    this._dropzone.classList.remove('jfm-over');
                    const file = e.dataTransfer?.files[0];
                    if (file) this._uploadFile(file);
                });
                // Keyboard activation of drop zone
                this._dropzone.addEventListener('keydown', e => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        this._fileInput?.click();
                    }
                });
            }

            // Modal backdrop clicks
            [this._editModal, this._delModal, this._createModal].forEach(m => {
                if (m) m.addEventListener('click', e => { if (e.target === m) this._closeAll(); });
            });

            // Editor: char count + Tab indent
            if (this._editorEl) {
                this._editorEl.addEventListener('input', () => this._updateStat());
                this._editorEl.addEventListener('keydown', e => {
                    // Plain Tab indents; Shift+Tab is left alone so keyboard
                    // users can always move focus out of the editor.
                    if (e.key === 'Tab' && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
                        e.preventDefault();
                        const s = this._editorEl.selectionStart;
                        const end = this._editorEl.selectionEnd;
                        const v = this._editorEl.value;
                        this._editorEl.value = v.slice(0, s) + '  ' + v.slice(end);
                        this._editorEl.selectionStart = this._editorEl.selectionEnd = s + 2;
                        this._updateStat();
                    }
                });
            }

            // Global keyboard shortcuts
            document.addEventListener('keydown', this._keyHandler);
        }

        _onKey(e) {
            // Escape is handled by the dialog focus trap (see _trap).
            const editOpen   = this._editModal   && !this._editModal.hidden;
            if ((e.ctrlKey || e.metaKey) && e.key === 's' && editOpen) {
                e.preventDefault();
                this._doSave();
            }
        }

        _onClick(e) {
            const btn = e.target.closest('[data-jfm]');
            if (!btn) return;
            const action = btn.dataset.jfm;

            switch (action) {
                case 'refresh':      this._loadList(); break;
                case 'open-picker':  this._fileInput?.click(); break;
                case 'open-create':  this._openCreate(); break;
                case 'close-edit':   this._closeEdit(); break;
                case 'close-del':    this._closeDel(); break;
                case 'close-create': this._closeCreate(); break;
                case 'fmt':          this._formatJson(); break;
                case 'validate':     this._validateJson(); break;
                case 'save':         this._doSave(); break;
                case 'confirm-del':  this._doDelete(); break;
                case 'do-create':    this._doCreate(); break;
                case 'edit-file': {
                    const card = btn.closest('[data-jfm-file]');
                    if (card) this._openEdit(card.dataset.jfmFile);
                    break;
                }
                case 'del-file': {
                    const card = btn.closest('[data-jfm-file]');
                    if (card) this._openDel(card.dataset.jfmFile);
                    break;
                }
            }
        }

        _onChange(e) {
            // Toggle checkbox
            if (e.target.classList.contains('jfm-toggle-cb')) {
                const catName = e.target.dataset.cat;
                const enabled = e.target.checked;
                this._doToggle(catName, enabled, e.target);
            }
            // File input
            if (e.target === this._fileInput) {
                const file = e.target.files?.[0];
                if (file) this._uploadFile(file);
                e.target.value = '';
            }
        }

        // ── API helper ───────────────────────────────────────────────────────

        async _api(actionKey, params) {
            const actionId = Object.prototype.hasOwnProperty.call(this.actions, actionKey) ? this.actions[actionKey] : undefined;
            if (!actionId) throw new Error(`Action "${actionKey}" not configured`);
            const body = { plugin_id: this.pluginId, action_id: actionId };
            if (params !== undefined) body.params = params;
            const r = await fetch('/api/v3/plugins/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            if (!r.ok) throw new Error('Server error ' + r.status);
            const ct = r.headers.get('content-type') || '';
            if (!ct.includes('application/json')) {
                const txt = await r.text();
                throw new Error('Unexpected response: ' + txt.slice(0, 120));
            }
            return r.json();
        }

        // ── File List ────────────────────────────────────────────────────────

        async _loadList() {
            this._listEl.innerHTML = `<div class="jfm-loading"><span class="jfm-spin"></span> Loading…</div>`;
            try {
                const data = await this._api('list');
                if (data.status !== 'success') throw new Error(data.message || 'Load failed');
                this._renderList(data.files || []);
            } catch (err) {
                this._listEl.innerHTML = `
                    <div class="jfm-empty">
                        <div class="jfm-empty-icon">&#9888;</div>
                        <p class="jfm-empty-title">Failed to load files</p>
                        <p class="jfm-empty-sub">${this._esc(err.message)}</p>
                    </div>`;
            }
        }

        _renderList(files) {
            if (!files.length) {
                this._listEl.innerHTML = `
                    <div class="jfm-empty">
                        <div class="jfm-empty-icon">&#128193;</div>
                        <p class="jfm-empty-title">No files yet</p>
                        <p class="jfm-empty-sub">Upload or create a JSON file to get started</p>
                    </div>`;
                return;
            }
            this._listEl.innerHTML = files.map(f => this._card(f)).join('');
        }

        _card(f) {
            const enabled     = f.enabled !== false;
            const displayName = this._esc(f.display_name || f.filename);
            const filename    = this._esc(f.filename);
            const catName     = this.toggleKey ? this._esc(f[this.toggleKey] || '') : '';
            const showToggle  = !!(this.actions.toggle && this.toggleKey && f[this.toggleKey]);
            const hasEdit     = !!this.actions.get && !!this.actions.save;
            const hasDelete   = !!this.actions.delete;

            return `
<div class="jfm-card${enabled ? '' : ' jfm-off'}" data-jfm-file="${filename}">
  <div class="jfm-card-top">
    <span class="jfm-card-name" title="${filename}">${displayName}</span>
    ${showToggle ? `
    <label class="jfm-toggle" title="${enabled ? 'Enabled — click to disable' : 'Disabled — click to enable'}">
      <input type="checkbox" class="jfm-toggle-cb" data-cat="${catName}" ${enabled ? 'checked' : ''}>
      <span>${enabled ? 'On' : 'Off'}</span>
    </label>` : ''}
  </div>
  <div class="jfm-card-meta">
    <span>&#128196; ${filename}</span>
    <span>&#128202; ${f.entry_count ?? 0} entries &middot; ${this._fmtSize(f.size || 0)}</span>
    <span>&#128337; ${this._fmtDate(f.modified)}</span>
  </div>
  <div class="jfm-card-actions">
    ${hasEdit ? `<button type="button" class="jfm-btn jfm-btn-sm" data-jfm="edit-file">&#9998; Edit</button>` : ''}
    ${hasDelete ? `<button type="button" class="jfm-btn jfm-btn-danger jfm-btn-sm jfm-del" data-jfm="del-file" title="Delete file">&#128465;</button>` : ''}
  </div>
</div>`;
        }

        // ── Edit flow ────────────────────────────────────────────────────────

        async _openEdit(filename) {
            this._editFile = filename;
            document.getElementById(`${this._uid}-edit-title`).textContent = `Edit: ${filename}`;
            this._clearErr();
            this._editorEl.value = 'Loading…';
            this._updateStat();
            this._editModal.hidden = false;
            this._trap('edit', this._editModal, `${this._uid}-edit-title`, this._editorEl, () => this._closeEdit());

            try {
                const data = await this._api('get', { filename });
                if (data.status !== 'success') throw new Error(data.message || 'Load failed');
                this._editorEl.value = JSON.stringify(data.content, null, 2);
                this._updateStat();
                this._editorEl.focus();
                this._editorEl.setSelectionRange(0, 0);
                this._editorEl.scrollTop = 0;
            } catch (err) {
                this._showErr(`Couldn't load this file (${err.message}). Close the editor and try again.`);
                this._editorEl.value = '';
            }
        }

        _closeEdit() {
            if (this._editModal) this._editModal.hidden = true;
            this._editFile = null;
            this._clearErr();
            this._release('edit');
        }

        _formatJson() {
            try {
                const parsed = JSON.parse(this._editorEl.value);
                this._editorEl.value = JSON.stringify(parsed, null, 2);
                this._updateStat();
                this._clearErr();
            } catch (err) {
                this._showErr('Invalid JSON — ' + err.message);
            }
        }

        _validateJson() {
            try {
                const parsed = JSON.parse(this._editorEl.value);
                const n = (typeof parsed === 'object' && parsed !== null) ? Object.keys(parsed).length : '?';
                this._clearErr();
                this._notify(`Valid JSON — ${n} top-level keys`, 'success');
            } catch (err) {
                this._showErr('Invalid JSON — ' + err.message);
            }
        }

        async _doSave() {
            if (!this._editFile) return;
            let contentStr;
            try {
                const parsed = JSON.parse(this._editorEl.value);
                contentStr = JSON.stringify(parsed, null, 2);
            } catch (err) {
                this._showErr('Cannot save — fix JSON first: ' + err.message);
                return;
            }
            const btn = document.getElementById(`${this._uid}-save-btn`);
            this._busy(btn, 'Saving…');
            try {
                const data = await this._api('save', { filename: this._editFile, content: contentStr });
                if (data.status !== 'success') throw new Error(data.message || 'Save failed');
                this._notify('File saved', 'success');
                this._closeEdit();
                this._loadList();
            } catch (err) {
                this._showErr('Save failed: ' + err.message);
            } finally {
                this._idle(btn, 'Save');
            }
        }

        // ── Delete flow ──────────────────────────────────────────────────────

        _openDel(filename) {
            this._deleteFile = filename;
            const el = document.getElementById(`${this._uid}-del-name`);
            if (el) el.textContent = filename;
            if (this._delModal) {
                this._delModal.hidden = false;
                // Destructive dialog: start on Cancel.
                this._trap('del', this._delModal, `${this._uid}-del-title`,
                    document.getElementById(`${this._uid}-del-cancel`), () => this._closeDel());
            }
        }

        _closeDel() {
            if (this._delModal) this._delModal.hidden = true;
            this._deleteFile = null;
            this._release('del');
        }

        async _doDelete() {
            if (!this._deleteFile) return;
            const btn = document.getElementById(`${this._uid}-del-btn`);
            this._busy(btn, 'Deleting…');
            try {
                const data = await this._api('delete', { filename: this._deleteFile });
                if (data.status !== 'success') throw new Error(data.message || 'Delete failed');
                this._notify('File deleted', 'success');
                this._closeDel();
                this._loadList();
            } catch (err) {
                this._notify('Delete failed: ' + err.message, 'error');
            } finally {
                this._idle(btn, 'Delete');
            }
        }

        // ── Create flow ──────────────────────────────────────────────────────

        _openCreate() {
            if (!this._createModal) return;
            this.createFields.forEach(f => {
                const el = document.getElementById(`${this._uid}-cf-${f.key}`);
                if (el) el.value = '';
            });
            this._createModal.hidden = false;
            const first = this.createFields[0];
            const firstInput = first ? document.getElementById(`${this._uid}-cf-${first.key}`) : null;
            this._trap('create', this._createModal, `${this._uid}-create-title`, firstInput, () => this._closeCreate());
        }

        _closeCreate() {
            if (this._createModal) this._createModal.hidden = true;
            this._release('create');
        }

        async _doCreate() {
            const params = {};
            for (const f of this.createFields) {
                const el  = document.getElementById(`${this._uid}-cf-${f.key}`);
                const val = (el?.value || '').trim();
                // display_name may be blank — auto-derived from category_name below
                if (!val && f.key !== 'display_name') {
                    this._notify(`"${f.label}" is required`, 'error');
                    el?.focus();
                    return;
                }
                if (f.pattern && val && el && el.validity.patternMismatch) {
                    this._notify(`"${f.label}" format is invalid`, 'error');
                    el?.focus();
                    return;
                }
                if (val) params[f.key] = val;
            }
            // Auto-derive display_name from category_name when left blank
            if (!params.display_name && params.category_name) {
                params.display_name = params.category_name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            }
            const btn = document.getElementById(`${this._uid}-create-btn`);
            this._busy(btn, 'Creating…');
            try {
                const data = await this._api('create', params);
                if (data.status !== 'success') throw new Error(data.message || 'Create failed');
                this._notify('File created', 'success');
                this._closeCreate();
                this._loadList();
            } catch (err) {
                this._notify('Create failed: ' + err.message, 'error');
            } finally {
                this._idle(btn, 'Create');
            }
        }

        // ── Upload ───────────────────────────────────────────────────────────

        async _uploadFile(file) {
            if (!file.name.endsWith('.json')) {
                this._notify('Please select a .json file', 'error');
                return;
            }
            let content;
            try {
                content = await file.text();
                JSON.parse(content); // client-side validation
            } catch (err) {
                this._notify('Invalid JSON: ' + err.message, 'error');
                return;
            }
            if (this._dropzone) this._dropzone.style.opacity = '.5';
            try {
                const data = await this._api('upload', { filename: file.name, content });
                if (data.status !== 'success') throw new Error(data.message || 'Upload failed');
                this._notify(`"${file.name}" uploaded`, 'success');
                this._loadList();
            } catch (err) {
                this._notify('Upload failed: ' + err.message, 'error');
            } finally {
                if (this._dropzone) this._dropzone.style.opacity = '';
            }
        }

        // ── Toggle ───────────────────────────────────────────────────────────

        async _doToggle(catName, enabled, checkbox) {
            checkbox.disabled = true;
            try {
                const params = { enabled };
                if (this.toggleKey) params[this.toggleKey] = catName;
                const data = await this._api('toggle', params);
                if (data.status !== 'success') throw new Error(data.message || 'Toggle failed');
                this._notify(enabled ? 'Category enabled' : 'Category disabled', 'success');
                this._loadList();
            } catch (err) {
                this._notify('Toggle failed: ' + err.message, 'error');
                checkbox.checked = !enabled; // revert
                checkbox.disabled = false;
            }
        }

        // ── Helpers ──────────────────────────────────────────────────────────

        _closeAll() {
            this._closeEdit();
            this._closeDel();
            this._closeCreate();
        }

        _updateStat() {
            const v = this._editorEl?.value || '';
            const lines = v ? v.split('\n').length : 0;
            const el = document.getElementById(`${this._uid}-charcount`);
            if (el) el.textContent = `${lines.toLocaleString()} lines · ${v.length.toLocaleString()} chars`;
        }

        _showErr(msg) {
            const el = document.getElementById(`${this._uid}-edit-err`);
            if (el) { el.textContent = msg; el.hidden = false; }
        }

        _clearErr() {
            const el = document.getElementById(`${this._uid}-edit-err`);
            if (el) { el.textContent = ''; el.hidden = true; }
        }

        _notify(msg, type) {
            if (typeof window.showNotification === 'function') {
                window.showNotification(msg, type || 'info');
            } else {
                console.info(`[JsonFileManager] ${type || 'info'}: ${msg}`);
            }
        }

        _busy(btn, label) {
            if (!btn) return;
            btn._jfmOrigText = btn.textContent;
            btn.disabled = true;
            btn.textContent = '';
            const spin = document.createElement('span');
            spin.className = 'jfm-spin';
            btn.appendChild(spin);
            btn.appendChild(document.createTextNode(' ' + label));
        }

        _idle(btn, label) {
            if (!btn) return;
            btn.disabled = false;
            btn.textContent = btn._jfmOrigText !== undefined ? btn._jfmOrigText : label;
            delete btn._jfmOrigText;
        }

        // Quotes too: the result lands in quoted attribute values (title=,
        // data-cat=, pattern=, ...), and the textContent/innerHTML round-trip
        // only escapes &, < and >.
        _esc(str) {
            const d = document.createElement('div');
            d.textContent = String(str ?? '');
            return d.innerHTML
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        _fmtSize(bytes) {
            if (!bytes) return '0 B';
            const i = Math.min(Math.floor(Math.log2(bytes + 1) / 10), 2);
            const unit = ['B', 'KB', 'MB'][i];
            const val  = bytes / Math.pow(1024, i);
            return (i ? val.toFixed(1) : val) + ' ' + unit;
        }

        _fmtDate(str) {
            if (!str) return '—';
            try {
                return new Date(str).toLocaleDateString(undefined, {
                    month: 'short', day: 'numeric', year: 'numeric'
                });
            } catch { return str; }
        }
    }

    // ── Widget registry integration ──────────────────────────────────────────

    window.JsonFileManager = JsonFileManager;

    if (typeof window.LEDMatrixWidgets !== 'undefined') {
        window.LEDMatrixWidgets.register('json-file-manager', {
            name: 'JSON File Manager',
            version: '1.0.0',
            render(container, config, _value, options) {
                new JsonFileManager(container, config || {}, options?.pluginId || '');
            },
            getValue() { return null; },
            setValue() {}
        });
        console.log('[JsonFileManager] Registered with LEDMatrixWidgets');
    } else {
        console.log('[JsonFileManager] Loaded (LEDMatrixWidgets registry not available)');
    }
})();
