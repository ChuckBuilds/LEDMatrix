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
     * Update all plugins.
     *
     * @param {Function} onProgress - Optional callback(index, total, pluginId) for progress updates
     * @returns {Promise<Array>} Update results
     */
    async updateAll(onProgress) {
        if (!window.PluginStateManager || !window.PluginStateManager.installedPlugins) {
            throw new Error('Installed plugins not loaded');
        }

        const plugins = window.PluginStateManager.installedPlugins;
        const results = [];

        // Call the API endpoint directly to avoid per-plugin refreshes
        for (let i = 0; i < plugins.length; i++) {
            const plugin = plugins[i];
            if (onProgress) onProgress(i + 1, plugins.length, plugin.id);
            try {
                const response = await fetch('/api/v3/plugins/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ plugin_id: plugin.id })
                });
                const data = await response.json();
                if (data.status === 'success') {
                    results.push({ pluginId: plugin.id, success: true, result: data.data || data });
                } else {
                    results.push({ pluginId: plugin.id, success: false, error: data.message || 'Unknown error' });
                }
            } catch (error) {
                results.push({ pluginId: plugin.id, success: false, error });
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
}

