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
     * @returns {Promise<Array>} Update results
     */
    async updateAll() {
        if (!window.PluginStateManager || !window.PluginStateManager.installedPlugins) {
            throw new Error('Installed plugins not loaded');
        }

        const plugins = window.PluginStateManager.installedPlugins;
        const results = [];

        for (const plugin of plugins) {
            try {
                const result = await this.update(plugin.id);
                results.push({ pluginId: plugin.id, success: true, result });
            } catch (error) {
                results.push({ pluginId: plugin.id, success: false, error });
            }
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

