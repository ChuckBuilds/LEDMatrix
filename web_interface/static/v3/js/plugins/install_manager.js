/**
 * Plugin installation and update management.
 * 
 * Handles plugin installation, updates, and uninstallation operations.
 */

const PluginInstallManager = {
    /**
     * Install a plugin.
     * 
     * @param {string} pluginId - Plugin identifier
     * @param {string} branch - Optional branch name to install from
     * @returns {Promise<Object>} Installation result
     */
    async install(pluginId, branch = null) {
        try {
            const result = await window.PluginAPI.installPlugin(pluginId, branch);
            
            // Refresh installed plugins list
            if (window.PluginStateManager) {
                await window.PluginStateManager.loadInstalledPlugins();
            }
            
            return result;
        } catch (error) {
            if (window.errorHandler) {
                window.errorHandler.displayError(error, `Failed to install plugin ${pluginId}`);
            }
            throw error;
        }
    },
    
    /**
     * Update a plugin.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Promise<Object>} Update result
     */
    async update(pluginId) {
        try {
            const result = await window.PluginAPI.updatePlugin(pluginId);
            
            // Refresh installed plugins list
            if (window.PluginStateManager) {
                await window.PluginStateManager.loadInstalledPlugins();
            }
            
            return result;
        } catch (error) {
            if (window.errorHandler) {
                window.errorHandler.displayError(error, `Failed to update plugin ${pluginId}`);
            }
            throw error;
        }
    },
    
    /**
     * Uninstall a plugin.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Promise<Object>} Uninstall result
     */
    async uninstall(pluginId) {
        try {
            const result = await window.PluginAPI.uninstallPlugin(pluginId);
            
            // Refresh installed plugins list
            if (window.PluginStateManager) {
                await window.PluginStateManager.loadInstalledPlugins();
            }
            
            return result;
        } catch (error) {
            if (window.errorHandler) {
                window.errorHandler.displayError(error, `Failed to uninstall plugin ${pluginId}`);
            }
            throw error;
        }
    },
    
    /**
     * Whether POST /plugins/update can update this installed-list entry.
     *
     * /plugins/installed also lists installed Starlark apps as virtual
     * `starlark:<app_id>` entries (flagged `is_starlark_app`) so they can be
     * seen and toggled with everything else. They are not plugin directories:
     * the plugin updater has nothing to update for them, and there is no
     * Starlark update route (reinstalling from the repository would reset the
     * app's saved settings), so they are left out of update-all.
     *
     * @param {Object} plugin - Entry from /plugins/installed
     * @returns {boolean}
     */
    isUpdatablePlugin(plugin) {
        if (!plugin || typeof plugin.id !== 'string' || !plugin.id) return false;
        return !plugin.is_starlark_app && !plugin.id.startsWith('starlark:');
    },

    /**
     * The entries update-all sends to POST /plugins/update, in list order.
     *
     * @param {Array} plugins - Entries from /plugins/installed
     * @returns {Array}
     */
    updatablePlugins(plugins) {
        return (Array.isArray(plugins) ? plugins : []).filter(p => this.isUpdatablePlugin(p));
    },

    /**
     * Backoff (ms) before re-sending an update whose request never got an
     * answer. Covers a web-service restart (~3s on a Pi) with room to spare.
     */
    NETWORK_RETRY_DELAYS_MS: [1000, 2000, 4000, 8000, 15000],

    /**
     * Update all plugins.
     *
     * @param {Function} onProgress - Optional callback(index, total, pluginId) for progress updates
     * @param {Object} options - Optional { sleep(ms), retryDelaysMs } (tests inject these)
     * @returns {Promise<Array>} Update results, one per plugin sent
     */
    async updateAll(onProgress, options = {}) {
        // Prefer PluginStateManager if populated, fall back to window.installedPlugins
        // (plugins_manager.js populates window.installedPlugins independently)
        const stateManagerPlugins = window.PluginStateManager && window.PluginStateManager.installedPlugins;
        const listed = (stateManagerPlugins && stateManagerPlugins.length > 0)
            ? stateManagerPlugins
            : (window.installedPlugins || []);
        // Snapshot: the list can be replaced while this loop is awaiting.
        const plugins = this.updatablePlugins(listed);

        if (!plugins.length) {
            return [];
        }
        const sleep = options.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
        const retryDelays = options.retryDelaysMs || this.NETWORK_RETRY_DELAYS_MS;
        const results = [];

        for (let i = 0; i < plugins.length; i++) {
            const plugin = plugins[i];
            if (onProgress) onProgress(i + 1, plugins.length, plugin.id);
            for (let attempt = 0; ; attempt++) {
                try {
                    const result = await window.PluginAPI.updatePlugin(plugin.id);
                    results.push({ pluginId: plugin.id, success: true, result });
                    break;
                } catch (error) {
                    // No HTTP answer at all (connection refused/reset, e.g. the
                    // web service restarting mid-run): the server never saw or
                    // never finished this plugin, so send it again once it is
                    // back rather than skipping it. An HTTP error response is
                    // the server's answer and is not retried.
                    if (error && error.error_code === 'NETWORK_ERROR' && attempt < retryDelays.length) {
                        await sleep(retryDelays[attempt]);
                        continue;
                    }
                    results.push({ pluginId: plugin.id, success: false, error });
                    break;
                }
            }
        }

        // Reload plugin list once at the end
        if (window.PluginStateManager) {
            await window.PluginStateManager.loadInstalledPlugins();
        }

        return results;
    }
};

// Export
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PluginInstallManager;
} else {
    window.PluginInstallManager = PluginInstallManager;
    window.updateAllPlugins = (onProgress) => PluginInstallManager.updateAll(onProgress);
}

