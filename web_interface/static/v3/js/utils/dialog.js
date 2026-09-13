/**
 * Shared modal-dialog accessibility helper.
 *
 * The UI has several modal implementations (inline markup toggled with
 * style.display, markup injected and removed, the `hidden` class). This helper
 * deliberately does not show or hide anything — each caller keeps its own
 * visibility logic — it only supplies what every modal was missing:
 *
 *   - role="dialog", aria-modal="true" and aria-labelledby on the panel
 *   - initial focus inside the panel
 *   - Tab / Shift+Tab kept inside the panel while it is open
 *   - Escape routed to the caller's close function
 *   - focus returned to whatever was focused before the dialog opened
 *
 * Usage:
 *   const release = window.LEDDialog.trap(panelEl, {
 *       labelledBy: 'my-modal-title',   // id of the visible heading (optional)
 *       label: 'Plugin files',          // aria-label when there is no heading
 *       initialFocus: inputEl,          // element or selector (optional)
 *       onEscape: closeMyModal          // omit to ignore Escape
 *   });
 *   // ...when the modal closes (from any path):
 *   release();
 *
 * Nested dialogs stack: only the top-most trap handles Tab and Escape.
 * release() is idempotent, and calling trap() again on the same panel
 * releases the previous trap first.
 */
(function () {
    'use strict';

    const FOCUSABLE = [
        'a[href]', 'area[href]', 'button:not([disabled])',
        'input:not([disabled]):not([type="hidden"])', 'select:not([disabled])',
        'textarea:not([disabled])', 'iframe', '[contenteditable="true"]',
        '[tabindex]:not([tabindex="-1"])'
    ].join(',');

    const stack = [];

    function visible(el) {
        return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    }

    function focusables(panel) {
        return Array.prototype.filter.call(panel.querySelectorAll(FOCUSABLE), visible);
    }

    function onKeydown(e) {
        const top = stack[stack.length - 1];
        if (!top) return;
        if (e.key === 'Escape' && top.onEscape) {
            e.preventDefault();
            e.stopPropagation();
            top.onEscape(e);
            return;
        }
        if (e.key !== 'Tab') return;
        const items = focusables(top.panel);
        if (items.length === 0) {
            e.preventDefault();
            top.panel.focus();
            return;
        }
        const first = items[0];
        const last = items[items.length - 1];
        const active = document.activeElement;
        if (!top.panel.contains(active)) {
            e.preventDefault();
            first.focus();
        } else if (e.shiftKey && active === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && active === last) {
            e.preventDefault();
            first.focus();
        }
    }

    function trap(panel, opts) {
        if (!panel) return function () {};
        opts = opts || {};

        const existing = stack.find(function (t) { return t.panel === panel; });
        if (existing) existing.release();

        panel.setAttribute('role', opts.role || 'dialog');
        panel.setAttribute('aria-modal', 'true');
        if (opts.labelledBy) {
            panel.setAttribute('aria-labelledby', opts.labelledBy);
        } else if (opts.label) {
            panel.setAttribute('aria-label', opts.label);
        }
        if (!panel.hasAttribute('tabindex')) panel.setAttribute('tabindex', '-1');

        const entry = {
            panel: panel,
            onEscape: opts.onEscape || null,
            returnTo: document.activeElement,
            released: false,
            release: null
        };

        entry.release = function () {
            if (entry.released) return;
            entry.released = true;
            const i = stack.indexOf(entry);
            if (i !== -1) stack.splice(i, 1);
            if (stack.length === 0) document.removeEventListener('keydown', onKeydown, true);
            const target = entry.returnTo;
            if (target && typeof target.focus === 'function' && document.contains(target)) {
                target.focus();
            }
        };

        stack.push(entry);
        if (stack.length === 1) document.addEventListener('keydown', onKeydown, true);

        // Focus after the caller has made the panel visible.
        requestAnimationFrame(function () {
            if (entry.released) return;
            let target = opts.initialFocus;
            if (typeof target === 'string') target = panel.querySelector(target);
            if (!target || !visible(target)) target = focusables(panel)[0] || panel;
            target.focus();
        });

        return entry.release;
    }

    window.LEDDialog = { trap: trap };
})();
