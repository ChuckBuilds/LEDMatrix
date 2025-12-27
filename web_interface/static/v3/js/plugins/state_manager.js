/**
 * Frontend plugin state management.
 * 
 * Manages local state for installed plugins and provides state synchronization.
 */

const PluginStateManager = {
    /**
     * Installed plugins state.
     */
    installedPlugins: [],
    
    /**
     * Current plugin configuration state.
     */
    currentConfig: null,
    
    /**
     * Load installed plugins.
     * 
     * @returns {Promise<Array>} List of installed plugins
     */
    async loadInstalledPlugins() {
        try {
            const plugins = await window.PluginAPI.getInstalledPlugins();
            this.installedPlugins = plugins;
            window.installedPlugins = plugins; // For backward compatibility
            return plugins;
        } catch (error) {
            if (window.errorHandler) {
                window.errorHandler.displayError(error, 'Failed to load installed plugins');
            }
            throw error;
        }
    },
    
    /**
     * Get plugin by ID.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Object|null} Plugin object or null
     */
    getPlugin(pluginId) {
        return this.installedPlugins.find(p => p.id === pluginId) || null;
    },
    
    /**
     * Update plugin state.
     * 
     * @param {string} pluginId - Plugin identifier
     * @param {Object} updates - State updates
     */
    updatePlugin(pluginId, updates) {
        const plugin = this.getPlugin(pluginId);
        if (plugin) {
            Object.assign(plugin, updates);
        }
    },
    
    /**
     * Set plugin enabled state.
     * 
     * @param {string} pluginId - Plugin identifier
     * @param {boolean} enabled - Whether plugin is enabled
     */
    setPluginEnabled(pluginId, enabled) {
        this.updatePlugin(pluginId, { enabled });
    },
    
    /**
     * Get current plugin configuration.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Promise<Object>} Plugin configuration
     */
    async getPluginConfig(pluginId) {
        try {
            const config = await window.PluginAPI.getPluginConfig(pluginId);
            this.currentConfig = { pluginId, config };
            return config;
        } catch (error) {
            if (window.errorHandler) {
                window.errorHandler.displayError(error, `Failed to load config for ${pluginId}`);
            }
            throw error;
        }
    },
    
    /**
     * Save plugin configuration.
     * 
     * @param {string} pluginId - Plugin identifier
     * @param {Object} config - Configuration data
     * @returns {Promise<Object>} Save result
     */
    async savePluginConfig(pluginId, config) {
        try {
            const result = await window.PluginAPI.savePluginConfig(pluginId, config);
            
            // Update local state
            if (this.currentConfig && this.currentConfig.pluginId === pluginId) {
                this.currentConfig.config = config;
            }
            
            return result;
        } catch (error) {
            if (window.errorHandler) {
                window.errorHandler.displayError(error, `Failed to save config for ${pluginId}`);
            }
            throw error;
        }
    }
};

// Export
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PluginStateManager;
} else {
    window.PluginStateManager = PluginStateManager;
}

