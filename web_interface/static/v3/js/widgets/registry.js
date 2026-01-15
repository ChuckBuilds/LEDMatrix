/**
 * LEDMatrix Widget Registry
 * 
 * Central registry for all UI widgets used in plugin configuration forms.
 * Allows plugins to use existing widgets and enables third-party developers
 * to create custom widgets without modifying the LEDMatrix codebase.
 * 
 * @module LEDMatrixWidgets
 */

(function() {
    'use strict';

    // Global widget registry
    window.LEDMatrixWidgets = {
        _widgets: new Map(),
        _handlers: new Map(),
        
        /**
         * Register a widget with the registry
         * @param {string} widgetName - Unique identifier for the widget
         * @param {Object} definition - Widget definition object
         * @param {string} definition.name - Human-readable widget name
         * @param {string} definition.version - Widget version
         * @param {Function} definition.render - Function to render the widget HTML
         * @param {Function} definition.getValue - Function to get current widget value
         * @param {Function} definition.setValue - Function to set widget value programmatically
         * @param {Object} definition.handlers - Event handlers for the widget
         */
        register: function(widgetName, definition) {
            if (!widgetName || typeof widgetName !== 'string') {
                console.error('[WidgetRegistry] Invalid widget name:', widgetName);
                return false;
            }
            
            if (!definition || typeof definition !== 'object') {
                console.error('[WidgetRegistry] Invalid widget definition for:', widgetName);
                return false;
            }
            
            // Validate required properties
            if (typeof definition.render !== 'function') {
                console.error('[WidgetRegistry] Widget must have a render function:', widgetName);
                return false;
            }
            
            this._widgets.set(widgetName, definition);
            
            if (definition.handlers) {
                this._handlers.set(widgetName, definition.handlers);
            }
            
            console.log(`[WidgetRegistry] Registered widget: ${widgetName}`);
            return true;
        },
        
        /**
         * Get widget definition
         * @param {string} widgetName - Widget identifier
         * @returns {Object|null} Widget definition or null if not found
         */
        get: function(widgetName) {
            return this._widgets.get(widgetName) || null;
        },
        
        /**
         * Get widget handlers
         * @param {string} widgetName - Widget identifier
         * @returns {Object} Widget handlers object (empty object if not found)
         */
        getHandlers: function(widgetName) {
            return this._handlers.get(widgetName) || {};
        },
        
        /**
         * Check if widget exists in registry
         * @param {string} widgetName - Widget identifier
         * @returns {boolean} True if widget is registered
         */
        has: function(widgetName) {
            return this._widgets.has(widgetName);
        },
        
        /**
         * List all registered widgets
         * @returns {Array<string>} Array of widget names
         */
        list: function() {
            return Array.from(this._widgets.keys());
        },
        
        /**
         * Render a widget into a container element
         * @param {string} widgetName - Widget identifier
         * @param {string|HTMLElement} container - Container element or ID
         * @param {Object} config - Widget configuration from schema
         * @param {*} value - Current value for the widget
         * @param {Object} options - Additional options (fieldId, pluginId, etc.)
         * @returns {boolean} True if rendering succeeded
         */
        render: function(widgetName, container, config, value, options) {
            const widget = this.get(widgetName);
            if (!widget) {
                console.error(`[WidgetRegistry] Widget not found: ${widgetName}`);
                return false;
            }
            
            // Resolve container element
            let containerEl = container;
            if (typeof container === 'string') {
                containerEl = document.getElementById(container);
                if (!containerEl) {
                    console.error(`[WidgetRegistry] Container not found: ${container}`);
                    return false;
                }
            }
            
            if (!containerEl || !(containerEl instanceof HTMLElement)) {
                console.error('[WidgetRegistry] Invalid container element');
                return false;
            }
            
            try {
                // Call widget's render function
                widget.render(containerEl, config, value, options || {});
                return true;
            } catch (error) {
                console.error(`[WidgetRegistry] Error rendering widget ${widgetName}:`, error);
                return false;
            }
        },
        
        /**
         * Get current value from a widget
         * @param {string} widgetName - Widget identifier
         * @param {string} fieldId - Field ID
         * @returns {*} Current widget value
         */
        getValue: function(widgetName, fieldId) {
            const widget = this.get(widgetName);
            if (!widget || typeof widget.getValue !== 'function') {
                console.warn(`[WidgetRegistry] Widget ${widgetName} does not support getValue`);
                return null;
            }
            
            try {
                return widget.getValue(fieldId);
            } catch (error) {
                console.error(`[WidgetRegistry] Error getting value from widget ${widgetName}:`, error);
                return null;
            }
        },
        
        /**
         * Set value in a widget
         * @param {string} widgetName - Widget identifier
         * @param {string} fieldId - Field ID
         * @param {*} value - Value to set
         * @returns {boolean} True if setting succeeded
         */
        setValue: function(widgetName, fieldId, value) {
            const widget = this.get(widgetName);
            if (!widget || typeof widget.setValue !== 'function') {
                console.warn(`[WidgetRegistry] Widget ${widgetName} does not support setValue`);
                return false;
            }
            
            try {
                widget.setValue(fieldId, value);
                return true;
            } catch (error) {
                console.error(`[WidgetRegistry] Error setting value in widget ${widgetName}:`, error);
                return false;
            }
        },
        
        /**
         * Unregister a widget (for testing/cleanup)
         * @param {string} widgetName - Widget identifier
         * @returns {boolean} True if widget was removed
         */
        unregister: function(widgetName) {
            const removed = this._widgets.delete(widgetName);
            this._handlers.delete(widgetName);
            if (removed) {
                console.log(`[WidgetRegistry] Unregistered widget: ${widgetName}`);
            }
            return removed;
        },
        
        /**
         * Clear all registered widgets (for testing/cleanup)
         */
        clear: function() {
            this._widgets.clear();
            this._handlers.clear();
            console.log('[WidgetRegistry] Cleared all widgets');
        }
    };
    
    // Expose registry for debugging
    if (typeof window !== 'undefined' && window.console) {
        window.LEDMatrixWidgets.debug = function() {
            console.log('[WidgetRegistry] Registered widgets:', Array.from(this._widgets.keys()));
            console.log('[WidgetRegistry] Widget details:', Array.from(this._widgets.entries()).map(([name, def]) => ({
                name,
                version: def.version || 'unknown',
                hasRender: typeof def.render === 'function',
                hasGetValue: typeof def.getValue === 'function',
                hasSetValue: typeof def.setValue === 'function',
                hasHandlers: !!def.handlers
            })));
        };
    }
    
    console.log('[WidgetRegistry] Widget registry initialized');
})();
