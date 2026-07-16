/* global showNotification, updateSystemStats, updateDisplayPreview, htmx, debugLog */
// LED Matrix v3 JavaScript
// Additional helpers for HTMX and Alpine.js integration

// Global notification system
window.showNotification = function(message, type = 'info') {
    // Use Alpine.js notification if available
    if (window.Alpine) {
        // This would trigger the Alpine.js notification system
        const event = new CustomEvent('show-notification', {
            detail: { message, type }
        });
        document.dispatchEvent(event);
    } else {
        // Fallback notification — user-facing last resort, so never gated
        console.info(`${type}: ${message}`);
    }
};

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

    // Handle response notifications
    const response = event.detail.xhr;
    if (response && response.responseText) {
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
window.showRestartPending = function() {
    try { sessionStorage.setItem('ledmatrix-restart-pending', '1'); } catch { /* private browsing */ }
    const banner = document.getElementById('restart-pending-banner');
    if (banner) banner.style.display = 'block';
};

window.dismissRestartPending = function() {
    try { sessionStorage.removeItem('ledmatrix-restart-pending'); } catch { /* no-op */ }
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
            if (banner) banner.style.display = 'block';
        }
    } catch { /* no-op */ }
});

// SSE reconnection helper — closes and reopens both SSE streams,
// reattaching the open/error handlers defined in base.html.
window.reconnectSSE = function() {
    if (window.statsSource) {
        window.statsSource.close();
        window.statsSource = new EventSource('/api/v3/stream/stats');
        window.statsSource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            if (typeof updateSystemStats === 'function') updateSystemStats(data);
        };
        if (window._statsOpenHandler) window.statsSource.addEventListener('open', window._statsOpenHandler);
        if (window._statsErrorHandler) window.statsSource.addEventListener('error', window._statsErrorHandler);
    }

    if (window.displaySource) {
        window.displaySource.close();
        window.displaySource = new EventSource('/api/v3/stream/display');
        window.displaySource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            if (typeof updateDisplayPreview === 'function') updateDisplayPreview(data);
        };
        if (window._displayErrorHandler) window.displaySource.addEventListener('error', window._displayErrorHandler);
    }
};

// Utility functions
window.hexToRgb = function(hex) {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16)
    } : null;
};

window.rgbToHex = function(r, g, b) {
    return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
};

// Form validation helpers
window.validateForm = function(form) {
    const inputs = form.querySelectorAll('input[required], select[required], textarea[required]');
    let isValid = true;

    inputs.forEach(input => {
        if (!input.value.trim()) {
            input.classList.add('border-red-500');
            isValid = false;
        } else {
            input.classList.remove('border-red-500');
        }
    });

    return isValid;
};

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

// Plugin management helpers
window.installPlugin = function(pluginId) {
    fetch('/api/v3/plugins/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: pluginId })
    })
    .then(response => response.json())
    .then(data => {
        showNotification(data.message, data.status);
        if (data.status === 'success') {
            // Refresh plugin list
            htmx.ajax('GET', '/v3/partials/plugins', '#plugins-content');
        }
    })
    .catch(error => {
        showNotification('Error installing plugin: ' + error.message, 'error');
    });
};

// Font management helpers
window.uploadFont = function(fileInput) {
    const file = fileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('font_file', file);
    formData.append('font_family', file.name.replace(/\.[^/.]+$/, '').toLowerCase().replace(/[^a-z0-9]/g, '_'));

    fetch('/api/v3/fonts/upload', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        showNotification(data.message, data.status);
        if (data.status === 'success') {
            // Refresh fonts list
            htmx.ajax('GET', '/v3/partials/fonts', '#fonts-content');
        }
    })
    .catch(error => {
        showNotification('Error uploading font: ' + error.message, 'error');
    });
};

// Tab switching helper
window.switchTab = function(tabName) {
    // Update Alpine.js active tab if available
    if (window.Alpine) {
        // Dispatch event for Alpine.js
        const event = new CustomEvent('switch-tab', {
            detail: { tab: tabName }
        });
        document.dispatchEvent(event);
    }
};

// Error handling for unhandled promise rejections
window.addEventListener('unhandledrejection', function(event) {
    console.error('Unhandled promise rejection:', event.reason);
    showNotification('An unexpected error occurred', 'error');
});

// Performance monitoring
window.performanceMonitor = {
    startTime: performance.now(),

    mark: function(name) {
        if (window.performance.mark) {
            performance.mark(name);
        }
    },

    measure: function(name, start, end) {
        if (window.performance.measure) {
            performance.measure(name, start, end);
        }
    },

    getMeasures: function() {
        if (window.performance && window.performance.getEntriesByType) {
            return window.performance.getEntriesByType('measure');
        }
        return [];
    },
    
    getMetrics: function() {
        if (!window.performance || !window.performance.getEntriesByType) {
            return {};
        }
        
        const navigation = window.performance.getEntriesByType('navigation')[0];
        const paint = window.performance.getEntriesByType('paint');
        const resources = window.performance.getEntriesByType('resource');
        
        return {
            domContentLoaded: navigation ? navigation.domContentLoadedEventEnd - navigation.domContentLoadedEventStart : 0,
            loadComplete: navigation ? navigation.loadEventEnd - navigation.fetchStart : 0,
            firstPaint: paint.find(p => p.name === 'first-paint')?.startTime || 0,
            firstContentfulPaint: paint.find(p => p.name === 'first-contentful-paint')?.startTime || 0,
            resourceCount: resources.length,
            totalResourceSize: resources.reduce((sum, r) => sum + (r.transferSize || 0), 0),
            measures: this.measures
        };
    },
    
    logMetrics: function() {
        const metrics = this.getMetrics();
        console.group('Performance Metrics');
        debugLog('DOM Content Loaded:', metrics.domContentLoaded?.toFixed(2) || 'N/A', 'ms');
        debugLog('Load Complete:', metrics.loadComplete?.toFixed(2) || 'N/A', 'ms');
        debugLog('First Paint:', metrics.firstPaint?.toFixed(2) || 'N/A', 'ms');
        debugLog('First Contentful Paint:', metrics.firstContentfulPaint?.toFixed(2) || 'N/A', 'ms');
        debugLog('Resources:', metrics.resourceCount || 0, 'files,', (metrics.totalResourceSize / 1024).toFixed(2) || '0', 'KB');
        if (Object.keys(metrics.measures || {}).length > 0) {
            debugLog('Custom Measures:', metrics.measures);
        }
        console.groupEnd();
    }
};

// Initialize performance monitoring
document.addEventListener('DOMContentLoaded', function() {
    window.performanceMonitor.mark('app-start');
    
    // Log metrics after page load
    window.addEventListener('load', function() {
        setTimeout(() => {
            window.performanceMonitor.mark('app-loaded');
            window.performanceMonitor.measure('app-load-time', 'app-start', 'app-loaded');
            if (window.location.search.includes('debug=perf')) {
                window.performanceMonitor.logMetrics();
            }
        }, 100);
    });
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

window.updateFloatingPreviewVisibility = function(tab) {
    const panel = document.getElementById('floating-preview');
    const toggle = document.getElementById('floating-preview-toggle');
    if (!panel || !toggle) return;
    let active = tab;
    if (!active) {
        const el = document.querySelector('[x-data="app()"]') || document.querySelector('[x-data]');
        const data = el && el._x_dataStack && el._x_dataStack[0];
        active = data && data.activeTab;
    }
    const onOverview = active === 'overview';
    let open = false;
    try { open = localStorage.getItem('ledmatrix-floating-preview') === '1'; } catch { /* no-op */ }
    panel.style.display = (!onOverview && open) ? 'block' : 'none';
    toggle.style.display = (!onOverview && !open) ? 'flex' : 'none';
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
    function appData() {
        const el = document.querySelector('[x-data="app()"]') || document.querySelector('[x-data]');
        return el && el._x_dataStack && el._x_dataStack[0];
    }
    document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') return;
        const data = appData();
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
            const data = appData();
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
