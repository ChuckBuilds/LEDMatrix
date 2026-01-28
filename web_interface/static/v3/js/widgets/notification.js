/**
 * LEDMatrix Notification Widget
 *
 * Global notification/toast system for displaying messages to users.
 * Consolidates all notification functionality into a single widget.
 *
 * Usage:
 *   window.showNotification('Message here', 'success');
 *   window.showNotification('Error occurred', 'error');
 *   window.LEDMatrixWidgets.get('notification').show('Custom message', { type: 'warning', duration: 5000 });
 *
 * Types: success, error, warning, info (default)
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
        defaultDuration: 4000,
        fadeOutDuration: 300,
        maxNotifications: 5,
        position: 'top-right' // top-right, top-left, bottom-right, bottom-left
    };

    // Type-specific styling
    const TYPE_STYLES = {
        success: {
            bg: 'bg-green-500',
            icon: 'fa-check-circle',
            label: 'Success'
        },
        error: {
            bg: 'bg-red-500',
            icon: 'fa-exclamation-circle',
            label: 'Error'
        },
        warning: {
            bg: 'bg-yellow-500',
            icon: 'fa-exclamation-triangle',
            label: 'Warning'
        },
        info: {
            bg: 'bg-blue-500',
            icon: 'fa-info-circle',
            label: 'Info'
        }
    };

    // Track active notifications
    let activeNotifications = [];
    let notificationCounter = 0;

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
            container.setAttribute('aria-live', 'polite');
            container.setAttribute('aria-label', 'Notifications');
            document.body.appendChild(container);
        }

        return container;
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

    /**
     * Remove a notification by ID
     * @param {string} notificationId - Notification ID
     * @param {boolean} immediate - Skip fade animation
     */
    function removeNotification(notificationId, immediate = false) {
        const notification = document.getElementById(notificationId);
        if (!notification) return;

        if (immediate) {
            notification.remove();
        } else {
            notification.style.transition = `opacity ${CONFIG.fadeOutDuration}ms, transform ${CONFIG.fadeOutDuration}ms`;
            notification.style.opacity = '0';
            notification.style.transform = 'translateX(100%)';

            setTimeout(() => {
                notification.remove();
            }, CONFIG.fadeOutDuration);
        }

        // Remove from tracking array
        activeNotifications = activeNotifications.filter(id => id !== notificationId);
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

        const type = options.type || 'info';
        const duration = options.duration !== undefined ? options.duration : CONFIG.defaultDuration;
        const showIcon = options.showIcon !== false;
        const dismissible = options.dismissible !== false;

        const style = TYPE_STYLES[type] || TYPE_STYLES.info;
        const container = getContainer();
        const notificationId = `notification_${++notificationCounter}`;

        // Enforce max notifications limit
        while (activeNotifications.length >= CONFIG.maxNotifications) {
            removeNotification(activeNotifications[0], true);
        }

        // Create notification element
        const notification = document.createElement('div');
        notification.id = notificationId;
        notification.className = `${style.bg} text-white px-4 py-3 rounded-lg shadow-lg flex items-center gap-3 pointer-events-auto transform transition-all duration-300 ease-out`;
        notification.style.opacity = '0';
        notification.style.transform = 'translateX(100%)';
        notification.setAttribute('role', 'alert');

        // Build content
        let html = '';

        if (showIcon) {
            html += `<i class="fas ${style.icon} flex-shrink-0"></i>`;
        }

        html += `<span class="flex-1 text-sm">${escapeHtml(message)}</span>`;

        if (dismissible) {
            html += `
                <button type="button"
                        onclick="window.LEDMatrixWidgets.get('notification').dismiss('${notificationId}')"
                        class="flex-shrink-0 ml-2 w-5 h-5 flex items-center justify-center rounded-full opacity-70 hover:opacity-100 hover:bg-white hover:bg-opacity-20 transition-all duration-150"
                        aria-label="Dismiss notification">
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M6 18L18 6M6 6l12 12"></path>
                    </svg>
                </button>
            `;
        }

        notification.innerHTML = html;
        container.appendChild(notification);
        activeNotifications.push(notificationId);

        // Trigger animation (need to wait for DOM update)
        requestAnimationFrame(() => {
            notification.style.opacity = '1';
            notification.style.transform = 'translateX(0)';
        });

        // Auto-dismiss (0 = no auto-dismiss)
        if (duration > 0) {
            setTimeout(() => {
                removeNotification(notificationId);
            }, duration);
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
        ids.forEach(id => removeNotification(id, true));
    }

    // Register the widget
    window.LEDMatrixWidgets.register('notification', {
        name: 'Notification Widget',
        version: '1.0.0',

        /**
         * Show a notification
         * @param {string} message - Message to display
         * @param {Object} options - Configuration options
         * @param {string} options.type - Notification type: success, error, warning, info
         * @param {number} options.duration - Auto-dismiss duration in ms (0 = no auto-dismiss)
         * @param {boolean} options.showIcon - Show type icon (default: true)
         * @param {boolean} options.dismissible - Show dismiss button (default: true)
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

    // Global shorthand function (backwards compatible with existing code)
    window.showNotification = function(message, type = 'info') {
        return showNotification(message, { type: type });
    };

    // Initialize container on load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', getContainer);
    } else {
        getContainer();
    }

    console.log('[NotificationWidget] Notification widget registered');
})();
