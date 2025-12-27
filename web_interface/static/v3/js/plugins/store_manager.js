/**
 * Plugin store management.
 * 
 * Handles plugin store browsing, searching, and installation.
 */

const PluginStoreManager = {
    /**
     * Cache for plugin store data.
     */
    cache: null,
    cacheTimestamp: null,
    CACHE_DURATION: 5 * 60 * 1000, // 5 minutes
    
    /**
     * Load plugin store.
     * 
     * @param {boolean} useCache - Whether to use cached data
     * @returns {Promise<Array>} List of plugins
     */
    async loadStore(useCache = true) {
        // Check cache
        if (useCache && this.cache && this.cacheTimestamp) {
            const age = Date.now() - this.cacheTimestamp;
            if (age < this.CACHE_DURATION) {
                return this.cache;
            }
        }
        
        try {
            const plugins = await window.PluginAPI.getPluginStore();
            this.cache = plugins;
            this.cacheTimestamp = Date.now();
            return plugins;
        } catch (error) {
            if (window.errorHandler) {
                window.errorHandler.displayError(error, 'Failed to load plugin store');
            }
            throw error;
        }
    },
    
    /**
     * Search plugin store.
     * 
     * @param {string} query - Search query
     * @returns {Promise<Array>} Filtered list of plugins
     */
    async searchStore(query) {
        const plugins = await this.loadStore();
        
        if (!query || query.trim() === '') {
            return plugins;
        }
        
        const lowerQuery = query.toLowerCase();
        return plugins.filter(plugin => {
            const name = (plugin.name || '').toLowerCase();
            const description = (plugin.description || '').toLowerCase();
            const author = (plugin.author || '').toLowerCase();
            const category = (plugin.category || '').toLowerCase();
            
            return name.includes(lowerQuery) ||
                   description.includes(lowerQuery) ||
                   author.includes(lowerQuery) ||
                   category.includes(lowerQuery);
        });
    },
    
    /**
     * Install plugin from store.
     * 
     * @param {string} pluginId - Plugin identifier
     * @param {string} branch - Optional branch name to install from
     * @returns {Promise<Object>} Installation result
     */
    async installPlugin(pluginId, branch = null) {
        try {
            const result = await window.PluginAPI.installPlugin(pluginId, branch);
            
            // Clear cache
            this.cache = null;
            this.cacheTimestamp = null;
            
            return result;
        } catch (error) {
            if (window.errorHandler) {
                window.errorHandler.displayError(error, `Failed to install plugin ${pluginId}`);
            }
            throw error;
        }
    }
};

// Export
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PluginStoreManager;
} else {
    window.PluginStoreManager = PluginStoreManager;
}

