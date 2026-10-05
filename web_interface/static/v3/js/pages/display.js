/*
 * pages/display.js -- the Display tab (templates/v3/partials/display.html):
 * the panel hardware, Vegas scroll mode, double-sided and multi-display
 * sync settings.
 *
 * The form itself stays plain htmx (hx-post with json-enc, and its hx-on
 * call to the shared showSaveResult, as the Rotation and General forms
 * keep). This module does what the partial's inline scripts did:
 *   - the brightness value next to its slider
 *   - the live "Your display: W x H pixels" readout
 *   - showing the Vegas and double-sided settings only while each is on
 *   - the scroll-speed hint (what the panel really does with a speed), asked
 *     of the server 150 ms after the slider stops; a newer answer wins
 *   - the Vegas plugin order (the shared plugin-order-list widget, as the
 *     Rotation tab uses; its plugin-list request carries ctx.signal)
 *   - the Advanced section's toggle button (data-action="toggle-section",
 *     calling the shared window.toggleSection the settings search also uses)
 *   - the multi-display sync role, and its status, polled every 5 s through
 *     ctx.visibility: only while the Display tab is on screen and the
 *     browser tab is visible, and never after the partial is swapped out
 *
 * The old global updateSyncUI is a deprecated alias made in core/boot.js; it
 * forwards to the export of the same name at the bottom of this file.
 */

const SYNC_POLL_MS = 5000;
const HINT_DELAY_MS = 150;
const RETRY_MS = 100;
const MAX_TRIES = 50;
const RESOLUTION_INPUTS = ['rows', 'cols', 'chain_length', 'parallel'];

// The mounted page, for the deprecated alias (boot.js).
let active = null;

function quiet(error) {
    return !!error && (error.name === 'AbortError' || error.loginRequired);
}

// ── the "Your display" readout ───────────────────────────────────────────────
// width = cols x chain_length, height = rows x parallel (the chain-length
// tooltip's math), swapped for a 90/270 orientation. A custom pixel mapper
// can change it further; src/display_geometry.py models those.
function showResolution(root) {
    const out = root.querySelector('#display-resolution-value');
    if (!out) return;
    const v = {};
    RESOLUTION_INPUTS.forEach(function(id) {
        const el = root.querySelector('#' + id);
        v[id] = el ? parseInt(el.value, 10) : NaN;
    });
    if (Object.values(v).some(function(n) { return !Number.isFinite(n) || n <= 0; })) {
        out.textContent = '—';
        return;
    }
    let w = v.cols * v.chain_length;
    let h = v.rows * v.parallel;
    const orientation = root.querySelector('#orientation');
    if (orientation && (orientation.value === '90' || orientation.value === '270')) {
        [w, h] = [h, w];
    }
    out.textContent = w + ' × ' + h + ' pixels';
}

// ── the scroll-speed hint ────────────────────────────────────────────────────
// Only speeds that advance a whole number of pixels per refresh look smooth,
// and which those are depends on the panel, so the server works it out.
function refreshScrollSpeedHint(root, ctx) {
    const win = root.ownerDocument.defaultView;
    const slider = root.querySelector('#vegas_scroll_speed');
    win.clearTimeout(ctx.state.hintTimer);
    ctx.state.hintTimer = win.setTimeout(function() {
        ctx.state.hintTimer = null;
        const hint = root.querySelector('#vegas_scroll_speed_hint');
        if (!hint || !slider || ctx.signal.aborted) return;
        ctx.state.hintSeq = (ctx.state.hintSeq || 0) + 1;
        const seq = ctx.state.hintSeq;
        const q = new win.URLSearchParams({ speed: slider.value, min: slider.min, max: slider.max });
        ctx.api.get('/api/v3/config/scroll-speed-advice?' + q, { signal: ctx.signal })
            .then(function(body) {
                if (seq !== ctx.state.hintSeq || body.status !== 'success') return;
                renderScrollSpeedHint(root, hint, slider, body.data);
            })
            .catch(function(error) {
                // A refused request keeps the last hint, as before; a failed
                // or unreadable one clears it.
                if (quiet(error) || error.body) return;
                hint.textContent = '';
            });
    }, HINT_DELAY_MS);
}

function renderScrollSpeedHint(root, hint, slider, a) {
    const doc = root.ownerDocument;
    const win = doc.defaultView;
    const ap = a.applied;
    const motion = ap.pixels_per_frame + ' px every ' + ap.frame_hold +
        ' refresh' + (ap.frame_hold === 1 ? '' : 'es');
    hint.textContent = '';
    hint.className = 'mt-1 text-xs ' + (a.smooth && a.exact ? 'text-green-700' : 'text-amber-700');
    const line = doc.createElement('span');
    if (a.smooth && a.exact) {
        line.textContent = 'Smooth on this panel (' + motion + ', ' + a.refresh_hz + ' Hz).';
    } else {
        line.textContent = a.requested + ' px/s will run as ' + ap.pixels_per_second +
            ' px/s (' + motion + ', ' + ap.steppiness + ') on this ' + a.refresh_hz +
            ' Hz panel.' + (a.alternatives.length ? ' Smooth speeds: ' : '');
    }
    hint.appendChild(line);
    a.alternatives.forEach(function(alt, i) {
        if (i > 0) hint.appendChild(doc.createTextNode(' '));
        const value = Math.round(alt.pixels_per_second);
        const btn = doc.createElement('button');
        btn.type = 'button';
        btn.className = 'underline font-medium';
        btn.textContent = value + ' px/s';
        btn.addEventListener('click', function() {
            slider.value = value;
            slider.dispatchEvent(new win.Event('input', { bubbles: true }));
        });
        hint.appendChild(btn);
    });
}

// ── the Vegas plugin order ───────────────────────────────────────────────────
// The widget is a classic deferred script and normally loads before this
// module runs; the short retry covers a page that arrives first anyway.
function startPluginOrder(root, ctx) {
    const container = root.querySelector('#vegas_plugin_order');
    if (!container) return;
    const win = root.ownerDocument.defaultView;
    let tries = 0;
    function attempt() {
        ctx.state.orderTimer = null;
        if (ctx.signal.aborted) return;
        const widget = win.PluginOrderList;
        if (!widget) {
            if (++tries < MAX_TRIES) {
                ctx.state.orderTimer = win.setTimeout(attempt, RETRY_MS);
            } else {
                container.textContent = 'Could not load the reorder widget — reload the page to try again.';
                container.className = 'text-sm text-red-500';
            }
            return;
        }
        widget.init({
            containerId: 'vegas_plugin_order',
            orderInputId: 'vegas_plugin_order_value',
            excludedInputId: 'vegas_excluded_plugins_value',
            showVegasModeBadge: true,
            signal: ctx.signal,
        });
    }
    attempt();
}

// ── multi-display sync ───────────────────────────────────────────────────────
function syncRole(root) {
    const select = root.querySelector('#sync_role');
    return select ? select.value : null;
}

/** Show the status bar and the follower's Position field for the chosen role. */
function showSyncRole(root) {
    const role = syncRole(root);
    const bar = root.querySelector('#sync_status_bar');
    const position = root.querySelector('#setting-display-sync_follower_position');
    const errorDetail = root.querySelector('#sync_error_detail');
    if (!bar || !position) return role;
    if (role === 'standalone') {
        bar.classList.add('hidden');
        if (errorDetail) errorDetail.classList.add('hidden');
        position.style.display = 'none';
    } else {
        bar.classList.remove('hidden');
        position.style.display = role === 'follower' ? '' : 'none';
    }
    return role;
}

function pollSyncStatus(root, ctx) {
    const role = syncRole(root);
    if (!role || role === 'standalone' || ctx.signal.aborted) return;
    ctx.api.get('/api/v3/sync/status', { signal: ctx.signal })
        .then(function(body) { renderSyncStatus(root, body.data || {}); })
        .catch(function(error) {
            if (!quiet(error)) renderSyncStatus(root, { state: 'unknown' });
        });
}

function renderSyncStatus(root, d) {
    const doc = root.ownerDocument;
    const content = root.querySelector('#sync_status_content');
    const errorDetail = root.querySelector('#sync_error_detail');
    const errorText = root.querySelector('#sync_error_text');
    if (!content) return;

    const state = d.state || 'unknown';
    const role = d.role || 'unknown';
    let icon, colorClass, text;

    if (state === 'connected' || state === 'follower') {
        icon = '●';
        colorClass = 'bg-green-50 border-green-200 text-green-800';
        const peer = d.peer_ip || d.leader_ip || 'peer';
        text = role === 'leader'
            ? 'Follower connected — ' + peer + ' (chain ' + (d.peer_chain || '?') + ')'
            : 'Receiving from leader — ' + peer;
        errorDetail.classList.add('hidden');
    } else if (state === 'incompatible') {
        icon = '⚠';
        colorClass = 'bg-yellow-50 border-yellow-200 text-yellow-800';
        text = 'Follower connected but incompatible panels';
        if (d.error) {
            errorText.textContent = d.error;
            errorDetail.classList.remove('hidden');
        }
    } else if (state === 'no_peer' || state === 'standalone') {
        icon = '○';
        colorClass = 'bg-gray-50 border-gray-200 text-gray-600';
        text = role === 'leader' ? 'No follower detected' : 'Searching for leader…';
        errorDetail.classList.add('hidden');
    } else if (state === 'starting') {
        icon = '○';
        colorClass = 'bg-gray-50 border-gray-200 text-gray-500';
        text = 'Display process starting…';
        errorDetail.classList.add('hidden');
    } else {
        icon = '✕';
        colorClass = 'bg-red-50 border-red-200 text-red-700';
        text = 'Sync status unavailable';
        errorDetail.classList.add('hidden');
    }

    content.className = 'flex items-start space-x-2 p-3 rounded-lg border text-sm ' + colorClass;
    content.textContent = '';
    const iconSpan = doc.createElement('span');
    iconSpan.className = 'font-bold text-lg leading-none';
    iconSpan.textContent = icon;
    const textSpan = doc.createElement('span');
    textSpan.textContent = text;
    content.appendChild(iconSpan);
    content.appendChild(textSpan);
}

/** The Role menu changed: show what goes with the new role, and ask for its status now. */
function changeSyncRole(root, ctx) {
    if (showSyncRole(root) !== 'standalone') pollSyncStatus(root, ctx);
}

// ── lifecycle ────────────────────────────────────────────────────────────────
export function init(root, ctx) {
    const win = root.ownerDocument.defaultView;
    const on = { signal: ctx.signal };
    const $ = function(id) { return root.querySelector('#' + id); };

    const brightness = $('brightness');
    const brightnessValue = $('brightness-value');
    if (brightness && brightnessValue) {
        brightness.addEventListener('input', function() { brightnessValue.textContent = brightness.value; }, on);
    }

    RESOLUTION_INPUTS.forEach(function(id) {
        const el = $(id);
        if (el) el.addEventListener('input', function() { showResolution(root); }, on);
    });
    const orientation = $('orientation');
    if (orientation) orientation.addEventListener('change', function() { showResolution(root); }, on);
    showResolution(root);

    // Hidden rather than disabled, so the fields keep submitting and the
    // server still sees an "off" state to persist.
    [['vegas_scroll_enabled', 'vegas_scroll_settings', 'block'],
     ['double_sided_enabled', 'double_sided_settings', 'grid']].forEach(function(spec) {
        const box = $(spec[0]);
        const settings = $(spec[1]);
        if (!box || !settings) return;
        box.addEventListener('change', function() { settings.style.display = box.checked ? spec[2] : 'none'; }, on);
    });

    const speed = $('vegas_scroll_speed');
    const speedValue = $('vegas_scroll_speed_value');
    if (speed && speedValue) {
        speed.addEventListener('input', function() {
            speedValue.textContent = speed.value;
            refreshScrollSpeedHint(root, ctx);
        }, on);
        refreshScrollSpeedHint(root, ctx);
    }

    root.addEventListener('click', function(event) {
        const button = event.target && event.target.closest ? event.target.closest('[data-action="toggle-section"]') : null;
        if (!button || !root.contains(button)) return;
        if (typeof win.toggleSection === 'function') win.toggleSection(button.getAttribute('data-section'));
    }, on);

    const role = $('sync_role');
    if (role) role.addEventListener('change', function() { changeSyncRole(root, ctx); }, on);
    showSyncRole(root);
    // The first request goes out as soon as the tab is on screen (at once,
    // when it already is), then every 5 s while it stays there.
    ctx.visibility.every(SYNC_POLL_MS, function() { pollSyncStatus(root, ctx); });

    startPluginOrder(root, ctx);
    active = ctx;
}

export function destroy(root, ctx) {
    const win = root.ownerDocument.defaultView;
    ['hintTimer', 'orderTimer'].forEach(function(name) {
        if (ctx.state[name]) {
            win.clearTimeout(ctx.state[name]);
            ctx.state[name] = null;
        }
    });
    // The sync poll is ctx.visibility's: it stops when ctx.signal aborts.
    if (active === ctx) active = null;
}

// ── the old global, kept as a deprecated alias (boot.js) ─────────────────────
/** window.updateSyncUI(): the Role menu's old onchange handler. */
export function updateSyncUI() {
    if (active) changeSyncRole(active.root, active);
}
