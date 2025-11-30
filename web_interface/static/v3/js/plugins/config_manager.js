/**
 * Plugin configuration form management.
 * 
 * Handles configuration form generation, validation, and submission.
 */

const PluginConfigManager = {
    /**
     * Current plugin configuration state.
     */
    currentState: {
        pluginId: null,
        config: null,
        schema: null,
        jsonEditor: null
    },
    
    /**
     * Initialize configuration for a plugin.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Promise<Object>} Configuration and schema
     */
    async initialize(pluginId) {
        try {
            const [config, schema] = await Promise.all([
                window.PluginAPI.getPluginConfig(pluginId),
                window.PluginAPI.getPluginSchema(pluginId)
            ]);
            
            this.currentState = {
                pluginId,
                config,
                schema,
                jsonEditor: null
            };
            
            return { config, schema };
        } catch (error) {
            if (window.errorHandler) {
                window.errorHandler.displayError(error, `Failed to initialize config for ${pluginId}`);
            }
            throw error;
        }
    },
    
    /**
     * Reset configuration to defaults.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Promise<Object>} Default configuration
     */
    async resetToDefaults(pluginId) {
        try {
            const result = await window.PluginAPI.resetPluginConfig(pluginId);
            
            // Reload configuration
            if (this.currentState.pluginId === pluginId) {
                await this.initialize(pluginId);
            }
            
            return result;
        } catch (error) {
            if (window.errorHandler) {
                window.errorHandler.displayError(error, `Failed to reset config for ${pluginId}`);
            }
            throw error;
        }
    },
    
    /**
     * Save configuration.
     * 
     * @param {string} pluginId - Plugin identifier
     * @param {Object} config - Configuration data
     * @returns {Promise<Object>} Save result
     */
    async save(pluginId, config) {
        try {
            const result = await window.PluginAPI.savePluginConfig(pluginId, config);
            
            // Update local state
            if (this.currentState.pluginId === pluginId) {
                this.currentState.config = config;
            }
            
            return result;
        } catch (error) {
            if (window.errorHandler) {
                window.errorHandler.displayError(error, `Failed to save config for ${pluginId}`);
            }
            throw error;
        }
    },
    
    /**
     * Validate configuration against schema.
     * 
     * @param {Object} config - Configuration data
     * @param {Object} schema - JSON schema
     * @returns {Object} Validation result with errors
     */
    validate(config, schema) {
        // Basic validation - full validation happens on server
        const errors = [];
        
        if (!schema || !schema.properties) {
            return { valid: true, errors: [] };
        }
        
        // Check required fields
        if (schema.required) {
            for (const field of schema.required) {
                if (!(field in config)) {
                    errors.push(`Required field '${field}' is missing`);
                }
            }
        }
        
        return {
            valid: errors.length === 0,
            errors: errors
        };
    }
};

// Export
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PluginConfigManager;
} else {
    window.PluginConfigManager = PluginConfigManager;
}

