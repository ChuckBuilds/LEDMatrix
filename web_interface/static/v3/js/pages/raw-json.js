/*
 * pages/raw-json.js -- the Config Editor tab (templates/v3/partials/raw_json.html):
 * plain-text editors for config.json and config_secrets.json.
 *
 * The buttons carry data-action ("format", "validate", "save") and
 * data-editor ("main", "secrets"); one delegated listener on the page root
 * handles all six. Validation messages are built with textContent, so a
 * parser message that quotes the user's text stays text.
 *
 * Saves are writes: they are not cancelled when the page is swapped away,
 * so the server always finishes and the result is still reported.
 *
 * The old globals (formatJson, manualValidateJson, validateJSON,
 * saveMainConfig, saveSecretsConfig) are deprecated aliases made in
 * core/boot.js; they forward to the exports at the bottom of this file.
 */

const EDITORS = {
    main: {
        editor: 'main-config-editor', validation: 'main-config-validation',
        url: '/api/v3/config/raw/main', file: 'config.json',
    },
    secrets: {
        editor: 'secrets-config-editor', validation: 'secrets-config-validation',
        url: '/api/v3/config/raw/secrets', file: 'config_secrets.json',
    },
};

// The mounted page, for the deprecated aliases (boot.js).
let active = null;

function el(doc, tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function parseError(text) {
    try {
        JSON.parse(text);
        return null;
    } catch (error) {
        return error;
    }
}

function parts(ctx, kind) {
    const spec = EDITORS[kind];
    if (!spec) return null;
    const textarea = ctx.root.querySelector('#' + spec.editor);
    const validation = ctx.root.querySelector('#' + spec.validation);
    return textarea && validation ? { spec: spec, textarea: textarea, validation: validation } : null;
}

/** The short "Valid JSON" / "Invalid JSON: ..." line under an editor. True when valid. */
export function validate(ctx, kind) {
    const p = parts(ctx, kind);
    if (!p) return true;
    const doc = ctx.root.ownerDocument;
    const error = parseError(p.textarea.value);
    const line = error
        ? el(doc, 'span', 'text-red-600')
        : el(doc, 'span', 'text-green-600');
    line.append(el(doc, 'i', (error ? 'fas fa-times-circle' : 'fas fa-check-circle') + ' mr-1'),
                doc.createTextNode(error ? 'Invalid JSON: ' + error.message : 'Valid JSON'));
    p.validation.replaceChildren(line);
    return !error;
}

/** The Validate button: a detailed box, and a notification. */
export function validateDetailed(ctx, kind) {
    const p = parts(ctx, kind);
    if (!p) return false;
    const doc = ctx.root.ownerDocument;
    const error = parseError(p.textarea.value);
    const box = el(doc, 'div', error
        ? 'p-3 bg-red-50 border border-red-200 rounded-md'
        : 'p-3 bg-green-50 border border-green-200 rounded-md');
    const row = el(doc, 'div', 'flex items-start');
    const text = el(doc, 'div');
    const detail = el(doc, 'div', error ? 'text-sm text-red-700 mt-1' : 'text-sm text-green-700 mt-1');
    if (error) {
        row.append(el(doc, 'i', 'fas fa-times-circle text-red-600 text-xl mr-3 mt-1'));
        text.append(el(doc, 'div', 'font-semibold text-red-800', '✗ Invalid JSON syntax'));
        detail.append(el(doc, 'strong', null, 'Error:'), doc.createTextNode(' ' + error.message));
    } else {
        row.append(el(doc, 'i', 'fas fa-check-circle text-green-600 text-xl mr-3 mt-1'));
        text.append(el(doc, 'div', 'font-semibold text-green-800', '✓ JSON is valid!'));
        ['✓ Valid JSON syntax', '✓ Proper structure', '✓ No syntax errors detected'].forEach(function(line, i) {
            if (i) detail.append(el(doc, 'br'));
            detail.append(doc.createTextNode(line));
        });
    }
    text.append(detail);
    row.append(text);
    box.append(row);
    p.validation.replaceChildren(box);
    ctx.notify(error ? 'JSON validation failed: ' + error.message : 'JSON validation successful!',
               error ? 'error' : 'success');
    return !error;
}

/** The Format button: re-indent valid JSON by four spaces. */
export function format(ctx, kind) {
    const p = parts(ctx, kind);
    if (!p) return false;
    try {
        p.textarea.value = JSON.stringify(JSON.parse(p.textarea.value), null, 4);
        validate(ctx, kind);
        ctx.notify('JSON formatted successfully!', 'success');
        return true;
    } catch (error) {
        validate(ctx, kind);
        ctx.notify('Cannot format invalid JSON: ' + error.message, 'error');
        return false;
    }
}

/** The Save button: validate, then post the parsed file. */
export function save(ctx, kind) {
    const p = parts(ctx, kind);
    if (!p) return Promise.resolve(false);
    if (!validate(ctx, kind)) {
        ctx.notify('Invalid JSON! Please fix errors before saving.', 'error');
        return Promise.resolve(false);
    }
    const config = JSON.parse(p.textarea.value);
    return ctx.api.post(p.spec.url, config).then(function() {
        ctx.notify(p.spec.file + ' saved successfully!', 'success');
        return true;
    }).catch(function(err) {
        if (err && err.loginRequired) return false;
        ctx.notify('Error saving ' + p.spec.file + ': ' + ((err && err.message) || 'An error occurred'), 'error');
        return false;
    });
}

const ACTIONS = { format: format, validate: validateDetailed, save: save };

export function init(root, ctx) {
    const on = { signal: ctx.signal };
    root.addEventListener('click', function(event) {
        const button = event.target.closest('button[data-action][data-editor]');
        if (!button || !root.contains(button)) return;
        const action = ACTIONS[button.dataset.action];
        if (action) action(ctx, button.dataset.editor);
    }, on);
    Object.keys(EDITORS).forEach(function(kind) {
        const p = parts(ctx, kind);
        if (!p) return;
        p.textarea.addEventListener('input', function() { validate(ctx, kind); }, on);
        validate(ctx, kind);
    });
    active = ctx;
}

export function destroy(root, ctx) {
    if (active === ctx) active = null;
}

// ── the old globals, kept as deprecated aliases (boot.js) ────────────────────
// They took element ids; map those back to an editor.
function kindOf(editorId) {
    return Object.keys(EDITORS).find(function(kind) { return EDITORS[kind].editor === editorId; });
}

/** window.formatJson(editorId, validationDivId) */
export function formatJson(editorId) {
    return active ? format(active, kindOf(editorId)) : false;
}

/** window.manualValidateJson(editorId, validationDivId) */
export function manualValidateJson(editorId) {
    return active ? validateDetailed(active, kindOf(editorId)) : false;
}

/** window.validateJSON(editorId, validationDivId): true when valid. */
export function validateJSON(editorId) {
    return active ? validate(active, kindOf(editorId)) : true;
}

/** window.saveMainConfig() */
export function saveMainConfig() {
    return active ? save(active, 'main') : Promise.resolve(false);
}

/** window.saveSecretsConfig() */
export function saveSecretsConfig() {
    return active ? save(active, 'secrets') : Promise.resolve(false);
}
