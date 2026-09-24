/* global showNotification */
/*
 * app.js -- page-wide behaviour that is not the Alpine app itself.
 *
 * Deferred, first of the scripts at the end of <body>; after app-shell.js
 * and Alpine.
 *
 * Load order (templates/v3/base.html):
 *   <head>, blocking:  debugLog and theme inline scripts; the htmx loader
 *                      (injects htmx.min.js with a dynamic <script>);
 *                      js/htmx-config.js; the loadPartialDirect fallback;
 *                      js/app-early.js
 *   <head>, defer:     js/app-shell.js, then js/alpinejs.min.js (Alpine
 *                      starts as soon as it runs, so app-shell.js's app()
 *                      is the one Alpine uses)
 *   end of <body>, defer, in this order: app.js, js/tooltips.js,
 *                      js/settings-search.js, js/utils/dialog.js,
 *                      js/utils/error_handler.js, js/plugins/api_client.js,
 *                      state_manager.js, install_manager.js, list_filter.js,
 *                      the widget bundle (web_interface/widget_bundle.py),
 *                      plugins_manager.js
 *   Tab partials arrive later through htmx; their inline scripts run on
 *   htmx:afterSwap (js/htmx-config.js).
 *
 * Owns: button loading states and the fallback result toast for htmx
 * requests; the unsaved-changes guard for plugin config forms; the "restart
 * the display" banner; the floating live preview; aria-current on the nav;
 * the mobile nav drawer's keyboard handling; header widget placement.
 *
 * Globals: showSaveResult, showRestartPending, dismissRestartPending,
 * restartPendingNow, toggleFloatingPreview, applyFloatingPreviewSize,
 * cycleFloatingPreviewSize, updateFloatingPreviewVisibility,
 * previewPluginNow, updateNavAriaCurrent, placeHeaderWidgets.
 */

// HTMX response handlers
document.body.addEventListener('htmx:beforeRequest', function(event) {
    // Show loading states for buttons
    const btn = event.target.closest('button, .btn');
    if (btn) {
        btn.classList.add('loading');
        const textEl = btn.querySelector('.btn-text');
        if (textEl) textEl.style.opacity = '0.5';
    }
});

document.body.addEventListener('htmx:afterRequest', function(event) {
    // Remove loading states
    const btn = event.target.closest('button, .btn');
    if (btn) {
        btn.classList.remove('loading');
        const textEl = btn.querySelector('.btn-text');
        if (textEl) textEl.style.opacity = '1';
    }

    // Show the server's message, unless the element that made the request
    // (or its form) has its own after-request handler: every such handler in
    // the templates reports the result itself, and this used to repeat it,
    // so each save showed two toasts.
    const response = event.detail.xhr;
    const elt = event.detail.elt;
    const reportsItself = elt && elt.closest &&
        elt.closest('[hx-on\\:\\:after-request], [hx-on\\:htmx\\:after-request]');
    if (!reportsItself && response && response.responseText) {
        try {
            const data = JSON.parse(response.responseText);
            if (data.message) {
                showNotification(data.message, data.status || 'info');
            }
        } catch {
            // Not JSON, ignore
        }
    }

    // Main-config saves (display hardware, rotation/durations, general) only
    // take effect after a display-service restart — surface the reminder
    // banner. Plugin config saves apply live and are deliberately excluded.
    try {
        const cfg = event.detail.requestConfig;
        if (cfg && cfg.verb === 'post' &&
            (cfg.path || '').includes('/api/v3/config/main') &&
            response && response.status >= 200 && response.status < 300) {
            window.showRestartPending();
        }
    } catch { /* banner is best-effort */ }
});

/**
 * Shows the outcome of a settings form save as one notification. Used by the
 * hx-on:htmx:after-request of the Display, Rotation & Durations and General
 * forms. Only a 2xx response counts as saved (a network failure is status 0);
 * the server's message is shown when there is one, and its status can refine
 * a success but never overturn a failure.
 * @param {XMLHttpRequest} xhr - event.detail.xhr
 * @param {string} savedText - message for a success without one
 * @param {string} failedText - message for a failure without one
 */
window.showSaveResult = function(xhr, savedText, failedText) {
    const httpSuccess = xhr.status >= 200 && xhr.status < 300;
    let message = httpSuccess ? savedText : failedText;
    let status = httpSuccess ? 'success' : 'error';
    try {
        const data = JSON.parse(xhr.responseText);
        if (data.message) message = data.message;
        if (httpSuccess && data.status) status = data.status;
    } catch {
        // Non-JSON body: keep the status-code verdict.
    }
    showNotification(message, status);
};

// ===== Unsaved-changes guard =====
// Plugin config panels are Alpine x-if templates: navigating away DESTROYS
// the panel and revisiting re-fetches it, silently discarding any edits.
// (System tabs use x-show + data-loaded and persist, so they're exempt.)
// Track dirty forms and confirm before a lossy navigation.
(function() {
    function markDirty(e) {
        const form = e.target && e.target.closest ? e.target.closest('form') : null;
        if (form) form.setAttribute('data-dirty', '');
    }
    document.body.addEventListener('input', markDirty);
    document.body.addEventListener('change', markDirty);

    // A successful submit makes the form clean again
    document.body.addEventListener('htmx:afterRequest', function(event) {
        const xhr = event.detail.xhr;
        const form = event.detail.elt && event.detail.elt.closest ? event.detail.elt.closest('form') : null;
        if (form && xhr && xhr.status >= 200 && xhr.status < 300) {
            form.removeAttribute('data-dirty');
        }
    });

    // Capture phase so this runs before Alpine's bubbling @click switches tabs
    document.addEventListener('click', function(e) {
        const tabBtn = e.target && e.target.closest ? e.target.closest('.nav-tab') : null;
        if (!tabBtn) return;
        const lossy = Array.prototype.filter.call(
            document.querySelectorAll('.plugin-config-tab form[data-dirty]'),
            function(f) { return f.offsetParent !== null; }
        );
        if (lossy.length === 0) return;
        if (!window.confirm('You have unsaved plugin settings — leaving this page will discard them. Leave anyway?')) {
            e.stopPropagation();
            e.preventDefault();
        }
    }, true);

    // Full page unload loses every panel's edits
    window.addEventListener('beforeunload', function(e) {
        const dirty = Array.prototype.some.call(
            document.querySelectorAll('form[data-dirty]'),
            function(f) { return f.offsetParent !== null; }
        );
        if (dirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });
})();

// ===== Restart-pending banner =====
// Shown after restart-requiring saves; persists across tab switches (and
// reloads, via sessionStorage) until the display restarts or it's dismissed.
window.showRestartPending = function(message) {
    try {
        sessionStorage.setItem('ledmatrix-restart-pending', '1');
        // Persisted alongside the flag: a code update and a config save want
        // different wording, and the banner outlives the page that raised it.
        if (message) sessionStorage.setItem('ledmatrix-restart-pending-text', message);
        else sessionStorage.removeItem('ledmatrix-restart-pending-text');
    } catch { /* private browsing */ }
    const banner = document.getElementById('restart-pending-banner');
    const text = document.getElementById('restart-pending-text');
    if (text) {
        // Without the else-branch a config save inherited whatever wording the
        // previous update left in the DOM: showRestartPending() clears the
        // stored text but used to leave the element itself alone. The default
        // is read back from the server-rendered copy rather than duplicated
        // here, so the template stays the one place that owns the string.
        if (text.dataset.defaultText === undefined) {
            text.dataset.defaultText = text.textContent.trim();
        }
        text.textContent = message || text.dataset.defaultText;
    }
    if (banner) banner.style.display = 'block';
};

window.dismissRestartPending = function() {
    try {
        sessionStorage.removeItem('ledmatrix-restart-pending');
        sessionStorage.removeItem('ledmatrix-restart-pending-text');
    } catch { /* no-op */ }
    const banner = document.getElementById('restart-pending-banner');
    if (banner) banner.style.display = 'none';
};

window.restartPendingNow = function() {
    const btn = document.getElementById('restart-pending-btn');
    if (btn) btn.disabled = true;
    fetch('/api/v3/system/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'restart_display_service' })
    })
    .then(r => r.json())
    .then(data => {
        showNotification(data.message || 'Display restarting…', data.status || 'success');
        window.dismissRestartPending();
    })
    .catch(err => {
        showNotification('Error restarting display: ' + err.message, 'error');
    })
    .finally(() => { if (btn) btn.disabled = false; });
};

document.addEventListener('DOMContentLoaded', function() {
    try {
        if (sessionStorage.getItem('ledmatrix-restart-pending') === '1') {
            const banner = document.getElementById('restart-pending-banner');
            const saved = sessionStorage.getItem('ledmatrix-restart-pending-text');
            const text = document.getElementById('restart-pending-text');
            if (text && saved) text.textContent = saved;
            if (banner) banner.style.display = 'block';
        }
    } catch { /* no-op */ }
});

// SSE streams (and window.reconnectSSE) are owned by window.LEDStreams in
// js/app-shell.js — do not open EventSources for stats/display here.

// Auto-resize textareas
document.addEventListener('DOMContentLoaded', function() {
    const textareas = document.querySelectorAll('textarea');
    textareas.forEach(textarea => {
        textarea.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = this.scrollHeight + 'px';
        });
    });
});

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    // Ctrl/Cmd + R to refresh
    if ((e.ctrlKey || e.metaKey) && e.key === 'r') {
        e.preventDefault();
        location.reload();
    }

    // Ctrl/Cmd + S to save current form
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        const form = document.querySelector('form');
        if (form) {
            form.dispatchEvent(new Event('submit'));
        }
    }
});

// Error handling for unhandled promise rejections
window.addEventListener('unhandledrejection', function(event) {
    console.error('Unhandled promise rejection:', event.reason);
    showNotification('An unexpected error occurred', 'error');
});

// ===== Floating live preview =====
// A mini preview of the display, available on every tab except Overview
// (which has the full-size one). Open/closed state persists per browser;
// frames arrive via the existing SSE stream (updateDisplayPreview in
// app-shell.js feeds #floating-preview-img).
window.toggleFloatingPreview = function(open) {
    try { localStorage.setItem('ledmatrix-floating-preview', open ? '1' : '0'); } catch { /* no-op */ }
    window.updateFloatingPreviewVisibility();
};

// Preset widths the size button cycles through (px). Desktop users can also
// drag the panel's native resize handle (CSS resize: both).
const FLOATING_PREVIEW_SIZES = [192, 256, 384, 512];

window.applyFloatingPreviewSize = function() {
    const panel = document.getElementById('floating-preview');
    if (!panel) return;
    let size = 256;
    try { size = parseInt(localStorage.getItem('ledmatrix-floating-preview-size'), 10) || 256; } catch { /* no-op */ }
    panel.style.width = size + 'px';
    // Clear any manual drag-resize height so the image's aspect ratio rules
    panel.style.height = '';
};

window.cycleFloatingPreviewSize = function() {
    let size = 256;
    try { size = parseInt(localStorage.getItem('ledmatrix-floating-preview-size'), 10) || 256; } catch { /* no-op */ }
    const idx = FLOATING_PREVIEW_SIZES.indexOf(size);
    const next = FLOATING_PREVIEW_SIZES[(idx + 1) % FLOATING_PREVIEW_SIZES.length];
    try { localStorage.setItem('ledmatrix-floating-preview-size', String(next)); } catch { /* no-op */ }
    window.applyFloatingPreviewSize();
};

window.updateFloatingPreviewVisibility = function(tab) {
    const panel = document.getElementById('floating-preview');
    const toggle = document.getElementById('floating-preview-toggle');
    if (!panel || !toggle) return;
    let active = tab;
    if (!active) {
        const app = window.getApp();
        active = app && app.activeTab;
    }
    const onOverview = active === 'overview';
    let open = false;
    try { open = localStorage.getItem('ledmatrix-floating-preview') === '1'; } catch { /* no-op */ }
    const showPanel = !onOverview && open;
    panel.style.display = showPanel ? 'block' : 'none';
    toggle.style.display = (!onOverview && !open) ? 'flex' : 'none';
    if (showPanel) {
        window.applyFloatingPreviewSize();
        // Show the last cached frame immediately — SSE only pushes on
        // display changes, so a freshly opened panel would otherwise stay
        // empty until the next change.
        const img = document.getElementById('floating-preview-img');
        if (img && !img.src && window._lastPreviewFrame) {
            img.src = 'data:image/png;base64,' + window._lastPreviewFrame;
        }
    }
    // The display SSE stream is only open while a preview is on screen
    if (window.LEDStreams) window.LEDStreams.refresh();
};

document.addEventListener('DOMContentLoaded', function() {
    window.updateFloatingPreviewVisibility();
});

// Run a plugin on the real display for 60s via the existing on-demand API
// and open the floating preview so the effect is visible while configuring.
window.previewPluginNow = function(pluginId) {
    fetch('/api/v3/display/on-demand/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: pluginId, duration: 60 })
    })
    .then(r => r.json())
    .then(data => {
        showNotification(data.message || ('Previewing ' + pluginId + ' for 60 seconds'),
                         data.status || 'success');
        if (data.status === 'success') window.toggleFloatingPreview(true);
    })
    .catch(err => {
        showNotification('Preview failed: ' + err.message, 'error');
    });
};

// ===== Nav accessibility =====
// aria-current tracks the active tab. Buttons are matched by their Alpine
// @click expression ("activeTab = '<tab>'"), which works for both the static
// system tabs and the dynamically injected plugin tabs.
window.updateNavAriaCurrent = function(tab) {
    document.querySelectorAll('.nav-tab').forEach(function(btn) {
        const expr = btn.getAttribute('@click') || btn.getAttribute('x-on:click') || '';
        const isCurrent = expr.indexOf("activeTab = '" + tab + "'") !== -1;
        if (isCurrent) {
            btn.setAttribute('aria-current', 'page');
        } else {
            btn.removeAttribute('aria-current');
        }
    });
};

// Escape closes the mobile nav drawer and returns focus to the hamburger;
// opening the drawer moves focus to its first tab.
(function() {
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        const data = window.getApp();
        if (data && data.mobileNavOpen) {
            data.mobileNavOpen = false;
            const burger = document.querySelector('[aria-controls="site-nav"]');
            if (burger) burger.focus();
        }
    });
    document.addEventListener('click', function(e) {
        const burger = e.target && e.target.closest
            ? e.target.closest('[aria-controls="site-nav"]') : null;
        if (!burger) return;
        // The click handler toggles mobileNavOpen; focus the first tab once
        // the drawer has slid in (matches the CSS transition timing).
        setTimeout(function() {
            const data = window.getApp();
            if (data && data.mobileNavOpen) {
                const first = document.querySelector('#site-nav .nav-tab');
                if (first) first.focus();
            }
        }, 120);
    });
})();

// ===== Mobile nav: header-widget relocation =====
// Below the md breakpoint the settings-search box and system-stats block are
// MOVED (same DOM nodes, listeners intact) from the header into the nav
// drawer's #drawer-widgets slot; at md and up they move back. Single-instance
// constraint: settings-search.js and the SSE stats updater both look these
// elements up by id, so they must never be duplicated.
window.placeHeaderWidgets = function() {
    const drawer = document.getElementById('drawer-widgets');
    const header = document.getElementById('header-widgets');
    const search = document.getElementById('settings-search-wrap');
    const stats = document.getElementById('system-stats');
    if (!drawer || !header) return;

    const desktop = window.matchMedia('(min-width: 768px)').matches;
    if (desktop) {
        // Restore original header order: search before the theme toggle,
        // stats as the last item.
        const themeToggle = document.getElementById('theme-toggle');
        if (search && search.parentElement !== header) {
            header.insertBefore(search, themeToggle || null);
        }
        if (stats && stats.parentElement !== header) {
            header.appendChild(stats);
        }
    } else {
        if (search && search.parentElement !== drawer) drawer.appendChild(search);
        if (stats && stats.parentElement !== drawer) drawer.appendChild(stats);
    }
};

document.addEventListener('DOMContentLoaded', function() {
    window.placeHeaderWidgets();
    window.matchMedia('(min-width: 768px)').addEventListener('change', window.placeHeaderWidgets);
});
