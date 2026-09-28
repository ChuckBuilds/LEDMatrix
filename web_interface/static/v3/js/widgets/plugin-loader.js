/**
 * Plugin Widget Loader
 * 
 * Handles loading of plugin-specific custom widgets from plugin directories.
 * Allows third-party plugins to provide their own widget implementations.
 * 
 * @module PluginWidgetLoader
 */

(function() {
    'use strict';

    // Ensure LEDMatrixWidgets registry exists
    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[PluginWidgetLoader] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    /**
     * Load a plugin-specific widget
     * @param {string} pluginId - Plugin ID
     * @param {string} widgetName - Widget name
     * @param {string} [version] - Plugin version, appended as ?v= so an
     *     updated plugin's widget is not served from the year-long
     *     immutable cache /static/ responses get (app.py)
     * @returns {Promise<void>} Promise that resolves when widget is loaded
     */
    window.LEDMatrixWidgets.loadPluginWidget = async function(pluginId, widgetName, version) {
        if (!pluginId || !widgetName) {
            throw new Error('Plugin ID and widget name are required');
        }

        // Check if widget is already registered
        if (this.has(widgetName)) {
            if (window.debugLog) window.debugLog(`[PluginWidgetLoader] Widget ${widgetName} already registered`);
            return;
        }

        // The one route that serves plugin widgets (serve_plugin_widget in
        // blueprints/pages_v3.py); nothing is served under /plugins/<id>/ or
        // /static/plugins/<id>/, so trying those only added two failed imports.
        const versionQuery = version ? `?v=${encodeURIComponent(version)}` : '';
        const possiblePaths = [
            `/static/plugin-widgets/${pluginId}/${widgetName}.js${versionQuery}`
        ];

        let lastError = null;
        for (const widgetPath of possiblePaths) {
            try {
                // Dynamic import of plugin widget
                await import(widgetPath);
                if (window.debugLog) window.debugLog(`[PluginWidgetLoader] Loaded plugin widget: ${pluginId}/${widgetName} from ${widgetPath}`);
                
                // Verify widget was registered
                if (this.has(widgetName)) {
                    return;
                } else {
                    console.warn(`[PluginWidgetLoader] Widget ${widgetName} loaded but not registered. Make sure the script calls LEDMatrixWidgets.register().`);
                }
            } catch (error) {
                lastError = error;
                // Continue to next path
                continue;
            }
        }

        // If all paths failed, throw error
        throw new Error(`Failed to load plugin widget ${pluginId}/${widgetName} from any path. Last error: ${lastError?.message || 'Unknown error'}`);
    };

    /**
     * Auto-load widget when detected in schema
     * Called automatically when a widget is referenced in a plugin's config schema
     * @param {string} widgetName - Widget name
     * @param {string} pluginId - Plugin ID (optional, for plugin-specific widgets)
     * @param {string} [version] - Plugin version (optional, see loadPluginWidget)
     * @returns {Promise<boolean>} True if widget is available (either already registered or successfully loaded)
     */
    window.LEDMatrixWidgets.ensureWidget = async function(widgetName, pluginId, version) {
        // Check if widget is already registered
        if (this.has(widgetName)) {
            return true;
        }

        // If plugin ID provided, try to load as plugin widget
        if (pluginId) {
            try {
                await this.loadPluginWidget(pluginId, widgetName, version);
                return this.has(widgetName);
            } catch (error) {
                console.warn(`[PluginWidgetLoader] Could not load widget ${widgetName} from plugin ${pluginId}:`, error);
                // Continue to check if it's a core widget
            }
        }

        // Widget not found
        return false;
    };
})();
