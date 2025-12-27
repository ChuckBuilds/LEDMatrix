/**
 * Configuration diff viewer.
 * 
 * Shows what changed in configuration before saving.
 */

const ConfigDiffViewer = {
    /**
     * Original configuration state (before changes).
     */
    originalConfigs: new Map(),
    
    /**
     * Store original configuration for a plugin.
     * 
     * @param {string} pluginId - Plugin identifier
     * @param {Object} config - Original configuration
     */
    storeOriginal(pluginId, config) {
        this.originalConfigs.set(pluginId, JSON.parse(JSON.stringify(config)));
    },
    
    /**
     * Get original configuration for a plugin.
     * 
     * @param {string} pluginId - Plugin identifier
     * @returns {Object|null} Original configuration
     */
    getOriginal(pluginId) {
        return this.originalConfigs.get(pluginId) || null;
    },
    
    /**
     * Clear stored original configuration.
     * 
     * @param {string} pluginId - Plugin identifier
     */
    clearOriginal(pluginId) {
        this.originalConfigs.delete(pluginId);
    },
    
    /**
     * Compare two configuration objects and return differences.
     * 
     * @param {Object} oldConfig - Old configuration
     * @param {Object} newConfig - New configuration
     * @returns {Object} Differences object with added, removed, and changed keys
     */
    compare(oldConfig, newConfig) {
        const differences = {
            added: {},
            removed: {},
            changed: {},
            unchanged: {}
        };
        
        // Get all keys from both configs
        const allKeys = new Set([
            ...Object.keys(oldConfig || {}),
            ...Object.keys(newConfig || {})
        ]);
        
        for (const key of allKeys) {
            const oldValue = oldConfig?.[key];
            const newValue = newConfig?.[key];
            
            if (!(key in (oldConfig || {}))) {
                // Key was added
                differences.added[key] = newValue;
            } else if (!(key in (newConfig || {}))) {
                // Key was removed
                differences.removed[key] = oldValue;
            } else if (JSON.stringify(oldValue) !== JSON.stringify(newValue)) {
                // Key was changed
                differences.changed[key] = {
                    old: oldValue,
                    new: newValue
                };
            } else {
                // Key unchanged
                differences.unchanged[key] = oldValue;
            }
        }
        
        return differences;
    },
    
    /**
     * Check if there are any differences.
     * 
     * @param {Object} differences - Differences object from compare()
     * @returns {boolean} True if there are changes
     */
    hasChanges(differences) {
        return Object.keys(differences.added).length > 0 ||
               Object.keys(differences.removed).length > 0 ||
               Object.keys(differences.changed).length > 0;
    },
    
    /**
     * Format differences for display.
     * 
     * @param {Object} differences - Differences object
     * @returns {string} HTML formatted diff
     */
    formatDiff(differences) {
        const parts = [];
        
        // Added keys
        if (Object.keys(differences.added).length > 0) {
            parts.push(`
                <div class="mb-4">
                    <h4 class="text-sm font-semibold text-green-800 mb-2">
                        <i class="fas fa-plus-circle mr-1"></i>Added
                    </h4>
                    <div class="bg-green-50 border border-green-200 rounded p-3">
                        ${Object.entries(differences.added).map(([key, value]) => `
                            <div class="mb-2">
                                <code class="text-xs font-mono bg-green-100 px-1 py-0.5 rounded">${this.escapeHtml(key)}</code>
                                <span class="text-sm text-gray-700 ml-2">=</span>
                                <pre class="mt-1 text-xs bg-white p-2 rounded border border-green-200 overflow-auto">${this.escapeHtml(JSON.stringify(value, null, 2))}</pre>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `);
        }
        
        // Removed keys
        if (Object.keys(differences.removed).length > 0) {
            parts.push(`
                <div class="mb-4">
                    <h4 class="text-sm font-semibold text-red-800 mb-2">
                        <i class="fas fa-minus-circle mr-1"></i>Removed
                    </h4>
                    <div class="bg-red-50 border border-red-200 rounded p-3">
                        ${Object.entries(differences.removed).map(([key, value]) => `
                            <div class="mb-2">
                                <code class="text-xs font-mono bg-red-100 px-1 py-0.5 rounded">${this.escapeHtml(key)}</code>
                                <span class="text-sm text-gray-700 ml-2">=</span>
                                <pre class="mt-1 text-xs bg-white p-2 rounded border border-red-200 overflow-auto line-through text-gray-500">${this.escapeHtml(JSON.stringify(value, null, 2))}</pre>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `);
        }
        
        // Changed keys
        if (Object.keys(differences.changed).length > 0) {
            parts.push(`
                <div class="mb-4">
                    <h4 class="text-sm font-semibold text-yellow-800 mb-2">
                        <i class="fas fa-edit mr-1"></i>Changed
                    </h4>
                    <div class="bg-yellow-50 border border-yellow-200 rounded p-3">
                        ${Object.entries(differences.changed).map(([key, change]) => `
                            <div class="mb-3">
                                <code class="text-xs font-mono bg-yellow-100 px-1 py-0.5 rounded">${this.escapeHtml(key)}</code>
                                <div class="mt-2 grid grid-cols-2 gap-2">
                                    <div>
                                        <div class="text-xs font-medium text-red-700 mb-1">Old Value:</div>
                                        <pre class="text-xs bg-white p-2 rounded border border-red-200 overflow-auto">${this.escapeHtml(JSON.stringify(change.old, null, 2))}</pre>
                                    </div>
                                    <div>
                                        <div class="text-xs font-medium text-green-700 mb-1">New Value:</div>
                                        <pre class="text-xs bg-white p-2 rounded border border-green-200 overflow-auto">${this.escapeHtml(JSON.stringify(change.new, null, 2))}</pre>
                                    </div>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `);
        }
        
        if (parts.length === 0) {
            return '<div class="text-sm text-gray-600 text-center py-4">No changes detected</div>';
        }
        
        return parts.join('');
    },
    
    /**
     * Show diff modal before saving.
     * 
     * @param {string} pluginId - Plugin identifier
     * @param {Object} newConfig - New configuration
     * @returns {Promise<boolean>} Promise resolving to true if user confirms, false if cancelled
     */
    async showDiffModal(pluginId, newConfig) {
        return new Promise((resolve) => {
            const original = this.getOriginal(pluginId);
            if (!original) {
                // No original to compare, proceed without diff
                resolve(true);
                return;
            }
            
            const differences = this.compare(original, newConfig);
            
            if (!this.hasChanges(differences)) {
                // No changes, proceed without showing modal
                resolve(true);
                return;
            }
            
            // Create modal
            const modalContainer = document.createElement('div');
            modalContainer.id = 'config-diff-modal-container';
            modalContainer.className = 'fixed inset-0 z-50 overflow-y-auto';
            modalContainer.innerHTML = `
                <div class="flex items-center justify-center min-h-screen px-4 pt-4 pb-20 text-center sm:block sm:p-0">
                    <div class="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity" onclick="this.closest('#config-diff-modal-container').remove(); window.__configDiffResolve(false);"></div>
                    
                    <div class="inline-block align-bottom bg-white rounded-lg text-left overflow-hidden shadow-xl transform transition-all sm:my-8 sm:align-middle sm:max-w-4xl sm:w-full">
                        <div class="bg-white px-4 pt-5 pb-4 sm:p-6 sm:pb-4">
                            <div class="sm:flex sm:items-start">
                                <div class="mx-auto flex-shrink-0 flex items-center justify-center h-12 w-12 rounded-full bg-blue-100 sm:mx-0 sm:h-10 sm:w-10">
                                    <i class="fas fa-code-branch text-blue-600"></i>
                                </div>
                                <div class="mt-3 text-center sm:mt-0 sm:ml-4 sm:text-left flex-1">
                                    <h3 class="text-lg leading-6 font-medium text-gray-900">Review Configuration Changes</h3>
                                    <div class="mt-4 max-h-96 overflow-y-auto">
                                        ${this.formatDiff(differences)}
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="bg-gray-50 px-4 py-3 sm:px-6 sm:flex sm:flex-row-reverse">
                            <button id="config-diff-confirm-btn" 
                                    class="w-full inline-flex justify-center rounded-md border border-transparent shadow-sm px-4 py-2 bg-blue-600 text-base font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 sm:ml-3 sm:w-auto sm:text-sm">
                                <i class="fas fa-check mr-2"></i>Save Changes
                            </button>
                            <button id="config-diff-cancel-btn" 
                                    class="mt-3 w-full inline-flex justify-center rounded-md border border-gray-300 shadow-sm px-4 py-2 bg-white text-base font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 sm:mt-0 sm:ml-3 sm:w-auto sm:text-sm">
                                Cancel
                            </button>
                        </div>
                    </div>
                </div>
            `;
            
            document.body.appendChild(modalContainer);
            
            // Store resolve function globally (hack for onclick handlers)
            window.__configDiffResolve = resolve;
            
            // Attach event listeners
            const confirmBtn = modalContainer.querySelector('#config-diff-confirm-btn');
            const cancelBtn = modalContainer.querySelector('#config-diff-cancel-btn');
            
            confirmBtn.addEventListener('click', () => {
                modalContainer.remove();
                delete window.__configDiffResolve;
                resolve(true);
            });
            
            cancelBtn.addEventListener('click', () => {
                modalContainer.remove();
                delete window.__configDiffResolve;
                resolve(false);
            });
        });
    },
    
    /**
     * Escape HTML to prevent XSS.
     */
    escapeHtml(text) {
        if (typeof text !== 'string') {
            text = String(text);
        }
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
};

// Export
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ConfigDiffViewer;
} else {
    window.ConfigDiffViewer = ConfigDiffViewer;
}

