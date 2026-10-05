/*
 * pages/general.js -- the General tab (templates/v3/partials/general.html).
 *
 * The settings form stays plain htmx (hx-post to /api/v3/config/main; its
 * hx-on calls the shared showSaveResult). This module:
 *   - draws the timezone-selector widget into #timezone_container, with the
 *     saved zone from its data-timezone attribute
 *   - runs the optional Security section (web login, #683): three forms and
 *     two kinds of button, all marked data-action and handled by one
 *     delegated submit and one delegated click listener on the page root, so
 *     a token row added later needs no listener of its own
 *
 * Login changes are writes: they are not cancelled when the page is swapped
 * away, so the server always finishes and the result is still reported, but
 * nothing is drawn into a page that has gone. Token names reach the page as
 * text.
 *
 * window.webLogin (setPassword, disable, createToken, copyToken, revoke) is a
 * deprecated alias made in core/boot.js; it forwards to the webLogin export
 * at the bottom of this file.
 */

const API = '/api/v3/auth';
const RETRY_MS = 50;
const MAX_TRIES = 200;

// The mounted page, for the deprecated alias (boot.js).
let active = null;

function windowOf(ctx) {
    return ctx.root.ownerDocument.defaultView;
}

function find(ctx, id) {
    return ctx.root.querySelector('#' + id);
}

function field(form, name) {
    const el = form.querySelector('[name="' + name + '"]');
    return el ? el.value : '';
}

/**
 * A login request. Resolves to {ok, d}: d is the JSON body, also for a
 * refused request (its message is shown). A network failure rejects; the
 * optional login's own "sign in again" answer resolves to null, since the
 * page is already on its way to the login form.
 */
function send(ctx, method, url, body) {
    return ctx.api.request(method, url, body === undefined ? {} : { json: body }).then(function(d) {
        return { ok: true, d: d };
    }, function(err) {
        if (err && err.loginRequired) return null;
        // Only an HTTP answer is a refusal; anything else is a failure.
        if (!err || err.name !== 'ApiError' || err.network || !err.status) throw err;
        return { ok: false, d: err.body && typeof err.body === 'object' ? err.body : {} };
    });
}

function failed(ctx) {
    return function(err) { ctx.notify('Request failed: ' + ((err && err.message) || err), 'error'); };
}

function reloadSection(ctx) {
    if (ctx.signal.aborted) return; // already swapped for a fresh copy
    const win = windowOf(ctx);
    if (win.htmx) {
        win.htmx.ajax('GET', '/v3/partials/general', { target: '#general-content', swap: 'innerHTML' });
    } else {
        win.location.reload();
    }
}

function tokenRow(doc, record) {
    const row = doc.createElement('div');
    row.className = 'flex flex-wrap items-center justify-between gap-2 border border-gray-200 rounded-md px-4 py-2';
    row.dataset.tokenId = record.id;
    const text = doc.createElement('div');
    text.className = 'text-sm';
    [['font-semibold text-gray-900', record.name],
     ['font-mono text-gray-600 ml-2', record.prefix + '…'],
     ['text-gray-600 ml-2', 'created just now']].forEach(function(part) {
        const span = doc.createElement('span');
        span.className = part[0];
        span.textContent = part[1];
        text.appendChild(span);
    });
    const button = doc.createElement('button');
    button.type = 'button';
    button.className = 'text-sm text-red-600 hover:underline';
    button.dataset.action = 'revoke-token';
    button.dataset.tokenName = record.name;
    button.textContent = 'Revoke';
    row.appendChild(text);
    row.appendChild(button);
    return row;
}

/** The password form: set (or change) the password, then redraw the section. */
export function setPassword(ctx, form) {
    const next = field(form, 'new_password');
    if (next !== field(form, 'confirm_password')) {
        ctx.notify('The two new passwords do not match.', 'error');
        return Promise.resolve(false);
    }
    const body = { new_password: next };
    if (form.querySelector('[name="current_password"]')) body.current_password = field(form, 'current_password');
    return send(ctx, 'POST', API + '/password', body).then(function(res) {
        if (!res) return false;
        ctx.notify(res.d.message || (res.ok ? 'Saved' : 'Could not save the password'), res.ok ? 'success' : 'error');
        if (res.ok) reloadSection(ctx);
        return res.ok;
    }).catch(failed(ctx));
}

/** The "Turn login off" form. */
export function disableLogin(ctx, form) {
    if (!windowOf(ctx).confirm('Turn login off? Anyone on your network will be able to open this page.')) {
        return Promise.resolve(false);
    }
    return send(ctx, 'POST', API + '/disable', { current_password: field(form, 'current_password') }).then(function(res) {
        if (!res) return false;
        ctx.notify(res.d.message || (res.ok ? 'Login is off' : 'Could not turn login off'), res.ok ? 'success' : 'error');
        if (res.ok) reloadSection(ctx);
        return res.ok;
    }).catch(failed(ctx));
}

/** The "Create token" form: add the row and show the token once. */
export function createToken(ctx, form) {
    return send(ctx, 'POST', API + '/tokens', { name: field(form, 'name') }).then(function(res) {
        if (!res) return false;
        if (!res.ok) {
            ctx.notify(res.d.message || 'Could not create the token', 'error');
            return false;
        }
        const data = res.d.data || {};
        // app.js marks a form dirty on input and clears the mark only after
        // an htmx save; this one posts with fetch, so clear it here or a
        // reload asks "Leave site?" about a saved token.
        form.reset();
        form.removeAttribute('data-dirty');
        if (!ctx.signal.aborted) {
            const list = find(ctx, 'web-login-tokens');
            if (list && data.record) {
                const empty = list.querySelector('[data-empty]');
                if (empty) empty.remove();
                list.appendChild(tokenRow(ctx.root.ownerDocument, data.record));
            }
            const value = find(ctx, 'web-login-new-token-value');
            const box = find(ctx, 'web-login-new-token');
            if (value) value.textContent = data.token;
            if (box) box.classList.remove('hidden');
        }
        ctx.notify(res.d.message || 'Token created', 'success');
        return true;
    }).catch(failed(ctx));
}

/** The Copy button next to a new token. */
export function copyToken(ctx) {
    const win = windowOf(ctx);
    const box = find(ctx, 'web-login-new-token-value');
    if (!box) return;
    if (win.navigator.clipboard && win.isSecureContext) {
        win.navigator.clipboard.writeText(box.textContent).then(function() { ctx.notify('Token copied', 'success'); });
        return;
    }
    // Plain http on a LAN is not a secure context: select it instead.
    const range = ctx.root.ownerDocument.createRange();
    range.selectNodeContents(box);
    const selection = win.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    ctx.notify('Selected: press Ctrl+C (or Cmd+C) to copy.', 'info');
}

/** A token row's Revoke button. */
export function revokeToken(ctx, id, name) {
    if (!windowOf(ctx).confirm('Revoke the token "' + name + '"? Anything using it stops working.')) {
        return Promise.resolve(false);
    }
    return send(ctx, 'DELETE', API + '/tokens/' + encodeURIComponent(id)).then(function(res) {
        if (!res) return false;
        ctx.notify(res.d.message || (res.ok ? 'Token revoked' : 'Could not revoke the token'), res.ok ? 'success' : 'error');
        if (!res.ok) return false;
        ctx.root.querySelectorAll('#web-login-tokens [data-token-id]').forEach(function(row) {
            if (row.dataset.tokenId === id) row.remove();
        });
        return true;
    }).catch(failed(ctx));
}

const FORM_ACTIONS = { 'set-password': setPassword, 'disable-login': disableLogin, 'create-token': createToken };

function drawTimezone(root, ctx) {
    const win = root.ownerDocument.defaultView;
    let tries = 0;
    function attempt() {
        ctx.state.timer = null;
        if (ctx.signal.aborted) return;
        const container = root.querySelector('#timezone_container');
        if (!container) return;
        const widget = win.LEDMatrixWidgets && win.LEDMatrixWidgets.get('timezone-selector');
        if (!widget) {
            if (++tries < MAX_TRIES) ctx.state.timer = win.setTimeout(attempt, RETRY_MS);
            else win.console.error('[General] timezone-selector widget not available');
            return;
        }
        // Only render if container is empty (not already rendered)
        if (container.children.length > 0) return;
        widget.render(container, {
            'x-options': { showOffset: true, placeholder: 'Select your timezone...' },
        }, container.dataset.timezone || 'America/Chicago', {
            fieldId: 'timezone',
            name: 'timezone',
        });
    }
    attempt();
}

export function init(root, ctx) {
    const on = { signal: ctx.signal };
    root.addEventListener('submit', function(event) {
        const form = event.target;
        const action = form && form.dataset ? FORM_ACTIONS[form.dataset.action] : null;
        if (!action) return; // the settings form: htmx posts it
        event.preventDefault();
        action(ctx, form);
    }, on);
    root.addEventListener('click', function(event) {
        const button = event.target.closest('button[data-action]');
        if (!button || !root.contains(button)) return;
        if (button.dataset.action === 'copy-token') {
            copyToken(ctx);
        } else if (button.dataset.action === 'revoke-token') {
            const row = button.closest('[data-token-id]');
            if (row) revokeToken(ctx, row.dataset.tokenId, button.dataset.tokenName);
        }
    }, on);
    drawTimezone(root, ctx);
    active = ctx;
}

export function destroy(root, ctx) {
    if (ctx.state.timer) {
        root.ownerDocument.defaultView.clearTimeout(ctx.state.timer);
        ctx.state.timer = null;
    }
    if (active === ctx) active = null;
}

// ── the old global, kept as a deprecated alias (boot.js) ─────────────────────
// window.webLogin's methods took the form (or the token's id and name) and
// acted on the General tab on screen.
export const webLogin = Object.freeze({
    setPassword: function(form) { return active ? setPassword(active, form) : undefined; },
    disable: function(form) { return active ? disableLogin(active, form) : undefined; },
    createToken: function(form) { return active ? createToken(active, form) : undefined; },
    copyToken: function() { return active ? copyToken(active) : undefined; },
    revoke: function(id, name) { return active ? revokeToken(active, id, name) : undefined; },
});
