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
            if (typeof window.showNotification === 'function') {
                window.showNotification('Failed to load installed plugins: '
                    + ((error && error.message) || String(error)), 'error');
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

