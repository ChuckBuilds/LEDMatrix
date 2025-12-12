/**
 * API client for plugin operations.
 * 
 * Handles all communication with the /api/v3/plugins endpoints.
 */

const PluginAPI = {
    /**
     * Base URL for API endpoints.
     */
    baseURL: '/api/v3',
    
    /**
     * Make an API request.
     * 
     * @param {string} endpoint - API endpoint
     * @param {string} method - HTTP method
     * @param {Object} data - Request body data
     * @returns {Promise<Object>} Response data
     */
    async request(endpoint, method = 'GET', data = null) {
        const url = `${this.baseURL}${endpoint}`;
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json'
            }
        };
        
        if (data && method !== 'GET') {
            options.body = JSON.stringify(data);
        }
        
        try {
            const response = await fetch(url, options);
            const responseData = await response.json();
            
            if (!response.ok) {
                // Handle structured errors
                if (responseData.error_code) {
                    throw responseData;
                }
                throw new Error(responseData.message || `HTTP ${response.status}`);
            }
            
            return responseData;
        } catch (error) {
            // Re-throw structured errors
            if (error.error_code) {
                throw error;
            }
            // Wrap network errors
            throw {
                error_code: 'NETWORK_ERROR',
                message: error.message || 'Network error',
                original_error: error
            };
        }
    },
    
    /**
     * Get installed plugins.
     * 
     * @returns {Promise<Array>} List of installed plugins
     */
    async getInstalledPlugins() {
        const response = await this.request('/plugins/installed');
        return response.data || [];
    },
    
    /**
     * Toggle plugin enabled/disabled.
     * 
     * @param {string} pluginId - Plugin identifier
     * @param {boolean} enabled - Whether plugin should be enabled
     * @returns {Promise<Object>} Response data
     */
    async togglePlugin(pluginId, enabled) {
        return await this.request('/plugins/toggle', 'POST', {
            plugin_id: pluginId,
            enabled: enabled
        });
    },
    
    /**
     * Get plugin configuration.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Promise<Object>} Plugin configuration
     */
    async getPluginConfig(pluginId) {
        const response = await this.request(`/plugins/config?plugin_id=${pluginId}`);
        return response.data || {};
    },
    
    /**
     * Save plugin configuration.
     * 
     * @param {string} pluginId - Plugin identifier
     * @param {Object} config - Configuration data
     * @returns {Promise<Object>} Response data
     */
    async savePluginConfig(pluginId, config) {
        return await this.request('/plugins/config', 'POST', {
            plugin_id: pluginId,
            config: config
        });
    },
    
    /**
     * Reset plugin configuration to defaults.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Promise<Object>} Response data
     */
    async resetPluginConfig(pluginId) {
        return await this.request(`/plugins/config/reset?plugin_id=${pluginId}`, 'POST');
    },
    
    /**
     * Get plugin schema.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Promise<Object>} Plugin schema
     */
    async getPluginSchema(pluginId) {
        const response = await this.request(`/plugins/schema?plugin_id=${pluginId}`);
        return response.data?.schema || null;
    },
    
    /**
     * Install plugin from store.
     * 
     * @param {string} pluginId - Plugin identifier
     * @param {string} branch - Optional branch name to install from
     * @returns {Promise<Object>} Response data
     */
    async installPlugin(pluginId, branch = null) {
        const data = {
            plugin_id: pluginId
        };
        if (branch) {
            data.branch = branch;
        }
        return await this.request('/plugins/install', 'POST', data);
    },
    
    /**
     * Update plugin.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Promise<Object>} Response data
     */
    async updatePlugin(pluginId) {
        return await this.request('/plugins/update', 'POST', {
            plugin_id: pluginId
        });
    },
    
    /**
     * Uninstall plugin.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Promise<Object>} Response data
     */
    async uninstallPlugin(pluginId) {
        return await this.request('/plugins/uninstall', 'POST', {
            plugin_id: pluginId
        });
    },
    
    /**
     * Get plugin store.
     * 
     * @returns {Promise<Array>} List of available plugins
     */
    async getPluginStore() {
        const response = await this.request('/plugins/store');
        return response.data || [];
    },
    
    /**
     * Get plugin health.
     * 
     * @param {string} pluginId - Optional plugin identifier (null for all)
     * @returns {Promise<Object>} Health data
     */
    async getPluginHealth(pluginId = null) {
        const endpoint = pluginId 
            ? `/plugins/health/${pluginId}`
            : '/plugins/health';
        const response = await this.request(endpoint);
        return response.data || {};
    }
};

// Export
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PluginAPI;
} else {
    window.PluginAPI = PluginAPI;
}

