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
     * @returns {Promise<void>} Promise that resolves when widget is loaded
     */
    window.LEDMatrixWidgets.loadPluginWidget = async function(pluginId, widgetName) {
        if (!pluginId || !widgetName) {
            throw new Error('Plugin ID and widget name are required');
        }

        // Check if widget is already registered
        if (this.has(widgetName)) {
            console.log(`[PluginWidgetLoader] Widget ${widgetName} already registered`);
            return;
        }

        // Try multiple possible paths for plugin widgets
        const possiblePaths = [
            `/static/plugin-widgets/${pluginId}/${widgetName}.js`,
            `/plugins/${pluginId}/widgets/${widgetName}.js`,
            `/static/plugins/${pluginId}/widgets/${widgetName}.js`
        ];

        let lastError = null;
        for (const widgetPath of possiblePaths) {
            try {
                // Dynamic import of plugin widget
                await import(widgetPath);
                console.log(`[PluginWidgetLoader] Loaded plugin widget: ${pluginId}/${widgetName} from ${widgetPath}`);
                
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
     * @returns {Promise<boolean>} True if widget is available (either already registered or successfully loaded)
     */
    window.LEDMatrixWidgets.ensureWidget = async function(widgetName, pluginId) {
        // Check if widget is already registered
        if (this.has(widgetName)) {
            return true;
        }

        // If plugin ID provided, try to load as plugin widget
        if (pluginId) {
            try {
                await this.loadPluginWidget(pluginId, widgetName);
                return this.has(widgetName);
            } catch (error) {
                console.warn(`[PluginWidgetLoader] Could not load widget ${widgetName} from plugin ${pluginId}:`, error);
                // Continue to check if it's a core widget
            }
        }

        // Widget not found
        return false;
    };

    /**
     * Load all widgets specified in plugin manifest
     * @param {string} pluginId - Plugin ID
     * @param {Object} manifest - Plugin manifest object
     * @returns {Promise<Array<string>>} Array of successfully loaded widget names
     */
    window.LEDMatrixWidgets.loadPluginWidgetsFromManifest = async function(pluginId, manifest) {
        if (!manifest || !manifest.widgets || !Array.isArray(manifest.widgets)) {
            return [];
        }

        const loadedWidgets = [];
        
        for (const widgetDef of manifest.widgets) {
            const widgetName = widgetDef.name || widgetDef.script?.replace(/\.js$/, '');
            if (!widgetName) {
                console.warn(`[PluginWidgetLoader] Invalid widget definition in manifest:`, widgetDef);
                continue;
            }

            try {
                await this.loadPluginWidget(pluginId, widgetName);
                if (this.has(widgetName)) {
                    loadedWidgets.push(widgetName);
                }
            } catch (error) {
                console.error(`[PluginWidgetLoader] Failed to load widget ${widgetName} from plugin ${pluginId}:`, error);
            }
        }

        return loadedWidgets;
    };

    console.log('[PluginWidgetLoader] Plugin widget loader initialized');
})();
