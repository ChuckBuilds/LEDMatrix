/**
 * API client for plugin operations.
 * 
 * Handles all communication with the /api/v3/plugins endpoints.
 * Includes request throttling and caching for performance optimization.
 */

// Request throttling utility
const RequestThrottler = {
    pending: new Map(),
    cache: new Map(),
    cacheTTL: 5000, // 5 seconds cache for GET requests
    debug: false, // Set to true to enable logging
    
    /**
     * Throttle a request to prevent rapid-fire calls
     */
    async throttle(key, fn, delay = 300) {
        // Check cache first
        const cached = this.cache.get(key);
        if (cached && (Date.now() - cached.timestamp) < this.cacheTTL) {
            if (this.debug) {
                console.log('[RequestThrottler] Cache hit for:', key);
            }
            return cached.data;
        }
        
        // Check if request is already pending
        if (this.pending.has(key)) {
            if (this.debug) {
                console.log('[RequestThrottler] Reusing pending request for:', key);
            }
            return this.pending.get(key);
        }
        
        if (this.debug) {
            console.log('[RequestThrottler] Creating new request for:', key);
        }
        
        // Create throttled request with abort support
        let abortController = null;
        const promise = new Promise((resolve, reject) => {
            const timeoutId = setTimeout(async () => {
                try {
                    const result = await fn();
                    // Cache successful GET requests
                    if (key.includes('GET')) {
                        this.cache.set(key, {
                            data: result,
                            timestamp: Date.now()
                        });
                        if (this.debug) {
                            console.log('[RequestThrottler] Cached response for:', key);
                        }
                    }
                    resolve(result);
                } catch (error) {
                    // Don't cache errors
                    if (this.debug) {
                        console.error('[RequestThrottler] Request failed for:', key, error);
                    }
                    reject(error);
                } finally {
                    this.pending.delete(key);
                }
            }, delay);
            
            // Store abort controller if available
            if (fn.abort && typeof fn.abort === 'function') {
                abortController = fn.abort;
            }
        });
        
        // Add abort method if available
        if (abortController) {
            promise.abort = () => {
                if (this.debug) {
                    console.log('[RequestThrottler] Aborting request for:', key);
                }
                abortController.abort();
                this.pending.delete(key);
            };
        }
        
        this.pending.set(key, promise);
        return promise;
    },
    
    /**
     * Clear cache for a specific key or all cache
     */
    clearCache(key = null) {
        if (key) {
            this.cache.delete(key);
        } else {
            this.cache.clear();
        }
    },
    
    /**
     * Enable or disable debug logging
     */
    setDebug(enabled) {
        this.debug = enabled;
    },
    
    /**
     * Get statistics about pending requests and cache
     */
    getStats() {
        return {
            pendingCount: this.pending.size,
            cacheSize: this.cache.size,
            pendingKeys: Array.from(this.pending.keys()),
            cacheKeys: Array.from(this.cache.keys())
        };
    }
};

const PluginAPI = {
    /**
     * Base URL for API endpoints.
     */
    baseURL: '/api/v3',
    
    /**
     * Make an API request with throttling and caching.
     * 
     * @param {string} endpoint - API endpoint
     * @param {string} method - HTTP method
     * @param {Object} data - Request body data
     * @param {boolean} useThrottle - Whether to throttle this request (default: true for GET)
     * @returns {Promise<Object>} Response data
     */
    async request(endpoint, method = 'GET', data = null, useThrottle = null) {
        // Default throttling: only for GET requests
        if (useThrottle === null) {
            useThrottle = method === 'GET';
        }
        
        const requestKey = `${method}:${endpoint}:${data ? JSON.stringify(data) : ''}`;
        
        const makeRequest = async () => {
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
        };
        
        // Use throttling for GET requests, immediate execution for POST/PUT/DELETE
        if (useThrottle && method === 'GET') {
            return await RequestThrottler.throttle(requestKey, makeRequest, 100);
        } else {
            return await makeRequest();
        }
    },
    
    /**
     * Batch multiple requests together for better performance
     * 
     * @param {Array} requests - Array of {endpoint, method, data} objects
     * @returns {Promise<Array>} Array of response data
     */
    async batch(requests) {
        return Promise.all(requests.map(req => 
            this.request(req.endpoint, req.method || 'GET', req.data || null, false)
        ));
    },
    
    /**
     * Clear API cache
     */
    clearCache() {
        RequestThrottler.clearCache();
    },
    
    /**
     * Get installed plugins.
     * 
     * @returns {Promise<Array>} List of installed plugins
     */
    async getInstalledPlugins() {
        const response = await this.request('/plugins/installed');
        return response.data?.plugins || [];
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

