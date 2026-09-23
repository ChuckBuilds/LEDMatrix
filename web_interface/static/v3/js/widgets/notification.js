/**
 * LEDMatrix Notification Widget
 *
 * Global notification/toast system for displaying messages to users.
 * This is the single implementation: the early fallbacks in app-shell.js,
 * app.js and partials/fonts.html only queue messages until this widget has
 * loaded (see window.__pendingNotifications) and then delegate to it.
 *
 * Usage:
 *   window.showNotification('Message here', 'success');
 *   window.showNotification('Error occurred', 'error');
 *   window.LEDMatrixWidgets.get('notification').show('Custom message', { type: 'warning', duration: 5000 });
 *
 * Types: success, error, warning, info (default)
 *
 * Accessibility:
 *   - Toasts are announced through two persistent, visually hidden live
 *     regions: errors use an assertive region (role="alert"), everything else
 *     a polite one (role="status"). The visible toast is not itself a live
 *     region, so each message is announced exactly once.
 *   - Errors stay on screen for at least ERROR_MIN_DURATION and always have a
 *     dismiss button.
 *   - Auto-dismiss pauses while the pointer is over a toast or focus is in it.
 *   - Slide animations are skipped under prefers-reduced-motion.
 *
 * @module NotificationWidget
 */

(function() {
    'use strict';

    // Ensure LEDMatrixWidgets registry exists
    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[NotificationWidget] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    // Configuration
    const CONFIG = {
        containerId: 'notifications',
        politeRegionId: 'notifications-live-polite',
        assertiveRegionId: 'notifications-live-assertive',
        defaultDuration: 4000,
        warningDuration: 6000,
        errorMinDuration: 10000,
        fadeOutDuration: 300,
        maxNotifications: 5,
        position: 'top-right' // top-right, top-left, bottom-right, bottom-left
    };

    // Type-specific styling. `bg` class names are kept for anyone styling
    // against them; `color` is applied inline so white text meets 4.5:1
    // contrast even where the utility classes are missing or lighter.
    const TYPE_STYLES = {
        success: {
            bg: 'bg-green-500',
            color: '#047857',
            icon: 'fa-check-circle',
            label: 'Success'
        },
        error: {
            bg: 'bg-red-500',
            color: '#b91c1c',
            icon: 'fa-exclamation-circle',
            label: 'Error'
        },
        warning: {
            bg: 'bg-yellow-500',
            color: '#b45309',
            icon: 'fa-exclamation-triangle',
            label: 'Warning'
        },
        info: {
            bg: 'bg-blue-500',
            color: '#1d4ed8',
            icon: 'fa-info-circle',
            label: 'Info'
        }
    };
    // Lookups go through a Map so an arbitrary type string can't reach
    // Object.prototype properties.
    const STYLE_BY_TYPE = new Map(Object.entries(TYPE_STYLES));

    const VISUALLY_HIDDEN = 'position:absolute;width:1px;height:1px;padding:0;margin:-1px;' +
        'overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;';

    // Track active notifications
    let activeNotifications = [];
    // onAction callbacks for notifications with an inline action button,
    // keyed by notification id (cleaned up on dismiss). A Map rather than a
    // plain object so ids can never collide with prototype properties.
    const actionCallbacks = new Map();
    // Auto-dismiss timers, keyed by notification id: { timer, remaining, startedAt }
    const timers = new Map();
    let notificationCounter = 0;

    function prefersReducedMotion() {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }

    /**
     * Get or create the notifications container
     * @returns {HTMLElement} Container element
     */
    function getContainer() {
        let container = document.getElementById(CONFIG.containerId);

        if (!container) {
            container = document.createElement('div');
            container.id = CONFIG.containerId;
            container.className = 'fixed top-4 right-4 z-50 space-y-2 pointer-events-none';
            document.body.appendChild(container);
        }

        if (!container.dataset.ledNotifications) {
            container.dataset.ledNotifications = 'true';
            // Announcements go through the dedicated live regions below; a
            // live container would announce every toast a second time.
            container.removeAttribute('aria-live');
            container.setAttribute('role', 'region');
            container.setAttribute('aria-label', 'Notifications');
            // Let clicks pass through the empty parts of the stack even when
            // the utility classes are not defined.
            container.style.pointerEvents = 'none';
        }

        getLiveRegion(CONFIG.politeRegionId, 'status', 'polite');
        getLiveRegion(CONFIG.assertiveRegionId, 'alert', 'assertive');

        return container;
    }

    function getLiveRegion(id, role, politeness) {
        let region = document.getElementById(id);
        if (!region) {
            region = document.createElement('div');
            region.id = id;
            region.setAttribute('role', role);
            region.setAttribute('aria-live', politeness);
            region.setAttribute('aria-atomic', 'true');
            region.style.cssText = VISUALLY_HIDDEN;
            document.body.appendChild(region);
        }
        return region;
    }

    /**
     * Announce a message to screen readers once.
     */
    function announce(text, assertive) {
        const region = getLiveRegion(
            assertive ? CONFIG.assertiveRegionId : CONFIG.politeRegionId,
            assertive ? 'alert' : 'status',
            assertive ? 'assertive' : 'polite'
        );
        // Clear first so repeating the same message is announced again.
        region.textContent = '';
        setTimeout(() => { region.textContent = text; }, 50);
    }

    /**
     * Escape HTML to prevent XSS
     * @param {string} text - Text to escape
     * @returns {string} Escaped text
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    function clearTimer(notificationId) {
        const t = timers.get(notificationId);
        if (t && t.timer) clearTimeout(t.timer);
        timers.delete(notificationId);
    }

    function startTimer(notificationId, ms) {
        const t = timers.get(notificationId) || {};
        if (t.timer) clearTimeout(t.timer);
        t.remaining = ms;
        t.startedAt = Date.now();
        t.timer = setTimeout(() => removeNotification(notificationId), ms);
        timers.set(notificationId, t);
    }

    function pauseTimer(notificationId) {
        const t = timers.get(notificationId);
        if (!t || !t.timer) return;
        clearTimeout(t.timer);
        t.timer = null;
        t.remaining = Math.max(0, t.remaining - (Date.now() - t.startedAt));
    }

    function resumeTimer(notificationId) {
        const t = timers.get(notificationId);
        if (!t || t.timer) return;
        // Give the reader a moment after they move away.
        startTimer(notificationId, Math.max(t.remaining, 2000));
    }

    /**
     * Remove a notification by ID
     * @param {string} notificationId - Notification ID
     * @param {boolean} immediate - Skip fade animation
     */
    function removeNotification(notificationId, immediate = false) {
        clearTimer(notificationId);
        const notification = document.getElementById(notificationId);

        // Remove from tracking array
        activeNotifications = activeNotifications.filter(id => id !== notificationId);
        actionCallbacks.delete(notificationId);

        if (!notification) return;

        // If focus is inside the toast, don't strand it on a removed node.
        const hadFocus = notification.contains(document.activeElement);

        if (immediate || prefersReducedMotion()) {
            notification.remove();
        } else {
            notification.style.transition = `opacity ${CONFIG.fadeOutDuration}ms, transform ${CONFIG.fadeOutDuration}ms`;
            notification.style.opacity = '0';
            notification.style.transform = 'translateX(100%)';

            setTimeout(() => {
                notification.remove();
            }, CONFIG.fadeOutDuration);
        }

        if (hadFocus) {
            const next = activeNotifications.length
                ? document.querySelector(`#${activeNotifications[activeNotifications.length - 1]} button`)
                : null;
            if (next) {
                next.focus();
            } else if (document.activeElement && typeof document.activeElement.blur === 'function') {
                document.activeElement.blur();
            }
        }
    }

    /**
     * Show a notification
     * @param {string} message - Message to display
     * @param {Object|string} options - Options object or type string
     * @returns {string} Notification ID (for manual dismissal)
     */
    function showNotification(message, options = {}) {
        // Handle legacy call signature: showNotification(message, type)
        if (typeof options === 'string') {
            options = { type: options };
        }
        options = options || {};

        const type = STYLE_BY_TYPE.has(options.type) ? options.type : 'info';
        const isError = type === 'error';
        let duration = options.duration !== undefined
            ? Number(options.duration)
            : (isError ? CONFIG.errorMinDuration
                : type === 'warning' ? CONFIG.warningDuration : CONFIG.defaultDuration);
        // Errors must stay long enough to read; 0 still means "until dismissed".
        if (isError && duration > 0 && duration < CONFIG.errorMinDuration) {
            duration = CONFIG.errorMinDuration;
        }
        const showIcon = options.showIcon !== false;
        // Errors always get a dismiss button.
        const dismissible = isError || duration <= 0 || options.dismissible !== false;

        const style = STYLE_BY_TYPE.get(type);
        const container = getContainer();
        const notificationId = `notification_${++notificationCounter}`;
        const reduceMotion = prefersReducedMotion();

        // Enforce max notifications limit
        while (activeNotifications.length >= CONFIG.maxNotifications) {
            removeNotification(activeNotifications[0], true);
        }

        // Create notification element
        const notification = document.createElement('div');
        notification.id = notificationId;
        notification.className = `${style.bg} text-white px-4 py-3 rounded-lg shadow-lg flex items-center gap-3 pointer-events-auto transform transition-all duration-300 ease-out`;
        notification.style.backgroundColor = style.color;
        notification.style.color = '#ffffff';
        notification.style.pointerEvents = 'auto';
        notification.dataset.type = type;
        if (reduceMotion) {
            notification.style.transition = 'none';
        } else {
            notification.style.opacity = '0';
            notification.style.transform = 'translateX(100%)';
        }

        // Build content
        let html = '';

        if (showIcon) {
            html += `<i class="fas ${style.icon} flex-shrink-0" aria-hidden="true"></i>`;
        }

        html += `<span class="flex-1 text-sm"><span style="${VISUALLY_HIDDEN}">${style.label}: </span>${escapeHtml(message)}</span>`;

        // Optional inline action button (e.g. "Restart Now" on a restart nudge).
        // The callback is stored by id and invoked via triggerAction, which
        // also dismisses the notification.
        if (options.actionLabel && typeof options.onAction === 'function') {
            actionCallbacks.set(notificationId, options.onAction);
            html += `
                <button type="button"
                        onclick="window.LEDMatrixWidgets.get('notification').triggerAction('${notificationId}')"
                        class="flex-shrink-0 ml-2 px-3 py-1 text-xs font-semibold rounded-md bg-white bg-opacity-20 hover:bg-opacity-30 transition-colors duration-150"
                        style="background:rgba(255,255,255,.2);color:inherit;border:0;">
                    ${escapeHtml(options.actionLabel)}
                </button>
            `;
        }

        if (dismissible) {
            html += `
                <button type="button"
                        onclick="window.LEDMatrixWidgets.get('notification').dismiss('${notificationId}')"
                        class="flex-shrink-0 ml-2 w-5 h-5 flex items-center justify-center rounded-full opacity-70 hover:opacity-100 hover:bg-white hover:bg-opacity-20 transition-all duration-150"
                        style="width:1.75rem;height:1.75rem;display:inline-flex;align-items:center;justify-content:center;background:transparent;color:inherit;border:0;border-radius:9999px;cursor:pointer;"
                        aria-label="Dismiss ${style.label.toLowerCase()} notification">
                    <svg class="w-3 h-3" width="12" height="12" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M6 18L18 6M6 6l12 12"></path>
                    </svg>
                </button>
            `;
        }

        notification.innerHTML = html;
        container.appendChild(notification);
        activeNotifications.push(notificationId);

        announce(`${style.label}: ${String(message)}`, isError);

        if (!reduceMotion) {
            // Trigger animation (need to wait for DOM update)
            requestAnimationFrame(() => {
                notification.style.opacity = '1';
                notification.style.transform = 'translateX(0)';
            });
        }

        // Auto-dismiss (0 = no auto-dismiss), paused while hovered or focused
        if (duration > 0) {
            startTimer(notificationId, duration);
            notification.addEventListener('mouseenter', () => pauseTimer(notificationId));
            notification.addEventListener('mouseleave', () => {
                if (!notification.contains(document.activeElement)) resumeTimer(notificationId);
            });
            notification.addEventListener('focusin', () => pauseTimer(notificationId));
            notification.addEventListener('focusout', (e) => {
                if (!notification.contains(e.relatedTarget) && !notification.matches(':hover')) {
                    resumeTimer(notificationId);
                }
            });
        }

        // Log for debugging
        console.log(`[${type.toUpperCase()}]`, message);

        return notificationId;
    }

    /**
     * Clear all active notifications
     */
    function clearAll() {
        const ids = [...activeNotifications];
        ids.forEach(id => { removeNotification(id, true); });
    }

    // Register the widget
    window.LEDMatrixWidgets.register('notification', {
        name: 'Notification Widget',
        version: '1.1.0',

        /**
         * Show a notification
         * @param {string} message - Message to display
         * @param {Object} options - Configuration options
         * @param {string} options.type - Notification type: success, error, warning, info
         * @param {number} options.duration - Auto-dismiss duration in ms (0 = no auto-dismiss).
         *        Errors are kept for at least 10s.
         * @param {boolean} options.showIcon - Show type icon (default: true)
         * @param {boolean} options.dismissible - Show dismiss button (default: true; always true for errors)
         * @returns {string} Notification ID
         */
        show: showNotification,

        /**
         * Dismiss a specific notification
         * @param {string} notificationId - Notification ID to dismiss
         */
        dismiss: function(notificationId) {
            removeNotification(notificationId);
        },

        /**
         * Invoke a notification's onAction callback (see options.actionLabel /
         * options.onAction on show) and dismiss it.
         * @param {string} notificationId - Notification ID whose action to run
         */
        triggerAction: function(notificationId) {
            const cb = actionCallbacks.get(notificationId);
            removeNotification(notificationId);
            if (typeof cb === 'function') cb();
        },

        /**
         * Clear all notifications
         */
        clearAll: clearAll,

        /**
         * Get active notification count
         * @returns {number} Number of active notifications
         */
        getActiveCount: function() {
            return activeNotifications.length;
        },

        // Widget interface methods (for consistency with other widgets)
        render: function() {
            // Notification widget doesn't render into a container
            // It manages its own container
            getContainer();
        },

        getValue: function() {
            return activeNotifications.length;
        },

        setValue: function() {
            // No-op for notification widget
        },

        handlers: {
            dismiss: function(notificationId) {
                removeNotification(notificationId);
            }
        }
    });

    // Global shorthand function (backwards compatible with existing code).
    // Accepts either the legacy type string or a full options object
    // ({ type, duration, actionLabel, onAction, ... }).
    // This always replaces the early fallbacks, whatever order they loaded in.
    window.showNotification = function(message, type = 'info') {
        return showNotification(message, typeof type === 'string' ? { type: type } : (type || {}));
    };
    window.showNotification.__ledNotificationWidget = true;

    function flushPending() {
        getContainer();
        const pending = window.__pendingNotifications;
        window.__pendingNotifications = [];
        if (Array.isArray(pending)) {
            pending.forEach(args => {
                try { window.showNotification(args[0], args[1]); } catch (e) { console.error(e); }
            });
        }
    }

    // Initialize container (and show anything queued before we loaded)
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', flushPending);
    } else {
        flushPending();
    }
})();
