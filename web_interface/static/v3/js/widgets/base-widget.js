/**
 * LEDMatrix Base Widget Class
 * 
 * Provides common functionality and utilities for all widgets.
 * Widgets can extend this or use it as a reference for best practices.
 * 
 * @module BaseWidget
 */

(function() {
    'use strict';

    /**
     * Base Widget Class
     * Provides common utilities and patterns for widgets
     */
    class BaseWidget {
        constructor(name, version) {
            this.name = name;
            this.version = version || '1.0.0';
        }
        
        /**
         * Validate widget configuration
         * @param {Object} config - Configuration object from schema
         * @param {Object} schema - Full schema object
         * @returns {Object} Validation result {valid: boolean, errors: Array}
         */
        validateConfig(config, schema) {
            const errors = [];
            
            if (!config) {
                errors.push('Configuration is required');
                return { valid: false, errors };
            }
            
            // Add widget-specific validation here
            // This is a base implementation that can be overridden
            
            return {
                valid: errors.length === 0,
                errors
            };
        }
        
        /**
         * Sanitize value for storage
         * @param {*} value - Raw value from widget
         * @returns {*} Sanitized value
         */
        sanitizeValue(value) {
            // Base implementation - widgets should override for specific needs
            if (typeof value === 'string') {
                // Basic XSS prevention
                return value.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '');
            }
            return value;
        }
        
        /**
         * Get field ID from container or options
         * @param {HTMLElement} container - Container element
         * @param {Object} options - Options object
         * @returns {string} Field ID
         */
        getFieldId(container, options) {
            if (options && options.fieldId) {
                return options.fieldId;
            }
            if (container && container.id) {
                return container.id.replace(/_widget_container$/, '');
            }
            return null;
        }
        
        /**
         * Show error message
         * @param {HTMLElement} container - Container element
         * @param {string} message - Error message
         */
        showError(container, message) {
            if (!container) return;
            
            // Remove existing error
            const existingError = container.querySelector('.widget-error');
            if (existingError) {
                existingError.remove();
            }
            
            // Create error element using DOM APIs to prevent XSS
            const errorEl = document.createElement('div');
            errorEl.className = 'widget-error text-sm text-red-600 mt-2';
            
            const icon = document.createElement('i');
            icon.className = 'fas fa-exclamation-circle mr-1';
            errorEl.appendChild(icon);
            
            const messageText = document.createTextNode(message);
            errorEl.appendChild(messageText);
            
            container.appendChild(errorEl);
        }
        
        /**
         * Clear error message
         * @param {HTMLElement} container - Container element
         */
        clearError(container) {
            if (!container) return;
            const errorEl = container.querySelector('.widget-error');
            if (errorEl) {
                errorEl.remove();
            }
        }
        
        /**
         * Escape HTML to prevent XSS
         * Always escapes the input, even for non-strings, by coercing to string first
         * @param {*} text - Text to escape (will be coerced to string)
         * @returns {string} Escaped text
         */
        escapeHtml(text) {
            // Always coerce to string first, then escape
            const textStr = String(text);
            const div = document.createElement('div');
            div.textContent = textStr;
            return div.innerHTML;
        }
        
        /**
         * Sanitize identifier for use in DOM IDs and CSS selectors
         * @param {string} id - Identifier to sanitize
         * @returns {string} Sanitized identifier safe for DOM/CSS
         */
        sanitizeId(id) {
            if (typeof id !== 'string') {
                id = String(id);
            }
            // Allow only alphanumeric, underscore, and hyphen
            return id.replace(/[^a-zA-Z0-9_-]/g, '_');
        }
        
        /**
         * Trigger widget change event
         * @param {string} fieldId - Field ID
         * @param {*} value - New value
         */
        triggerChange(fieldId, value) {
            const event = new CustomEvent('widget-change', {
                detail: { fieldId, value },
                bubbles: true,
                cancelable: true
            });
            document.dispatchEvent(event);
        }
        
        /**
         * Get notification function (if available)
         * @returns {Function|null} Notification function or null
         */
        getNotificationFunction() {
            if (typeof window.showNotification === 'function') {
                return window.showNotification;
            }
            return null;
        }
        
        /**
         * Show notification
         * @param {string} message - Message to show
         * @param {string} type - Notification type (success, error, info, warning)
         */
        notify(message, type) {
            const notifyFn = this.getNotificationFunction();
            if (notifyFn) {
                notifyFn(message, type);
            } else {
                console.log(`[${type.toUpperCase()}] ${message}`);
            }
        }
    }
    
    // Export for use in widget implementations
    if (typeof window !== 'undefined') {
        window.BaseWidget = BaseWidget;
    }
    
    console.log('[BaseWidget] Base widget class loaded');
})();
