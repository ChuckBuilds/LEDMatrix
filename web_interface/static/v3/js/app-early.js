/* global debugLog */
// Early helpers and the app() stub (must run before Alpine init)
// Extracted from templates/v3/base.html so browsers cache it as a static asset.
        // Helper function to get installed plugins with fallback
        // Must be defined before app() function that uses it
        async function getInstalledPluginsSafe() {
            if (window.PluginAPI && window.PluginAPI.getInstalledPlugins) {
                try {
                    const plugins = await window.PluginAPI.getInstalledPlugins();
                    // Ensure plugins is always an array
                    const pluginsArray = Array.isArray(plugins) ? plugins : [];
                    return { status: 'success', data: { plugins: pluginsArray } };
                } catch (error) {
                    console.error('Error using PluginAPI.getInstalledPlugins, falling back to direct fetch:', error);
                    // Fall through to direct fetch
                }
            }
            // Fallback to direct fetch if PluginAPI not loaded
            const response = await fetch('/api/v3/plugins/installed');
            return await response.json();
        }

        // Global event listener for pluginsUpdated - works even if Alpine isn't ready yet
        // This ensures tabs update when plugins_manager.js loads plugins
        document.addEventListener('pluginsUpdated', function(event) {
            debugLog('[GLOBAL] Received pluginsUpdated event:', event.detail?.plugins?.length || 0, 'plugins');
            const plugins = event.detail?.plugins || [];
            
            // Update window.installedPlugins
            window.installedPlugins = plugins;
            
            // Try to update Alpine component if it exists (only if using full implementation)
            if (window.Alpine) {
                const appElement = document.querySelector('[x-data="app()"]');
                if (appElement && appElement._x_dataStack && appElement._x_dataStack[0]) {
                    const appComponent = appElement._x_dataStack[0];
                    appComponent.installedPlugins = plugins;
                    // Only call updatePluginTabs if it's the full implementation (has _doUpdatePluginTabs)
                    if (typeof appComponent.updatePluginTabs === 'function' && 
                        appComponent.updatePluginTabs.toString().includes('_doUpdatePluginTabs')) {
                        debugLog('[GLOBAL] Updating plugin tabs via Alpine component (full implementation)');
                        appComponent.updatePluginTabs();
                        return; // Full implementation handles it, don't do direct update
                    }
                }
            }
            
            // Only do direct DOM update if full implementation isn't available yet
            const pluginTabsRow = document.getElementById('plugin-tabs-row');
            const pluginTabsNav = pluginTabsRow?.querySelector('nav');
            if (pluginTabsRow && pluginTabsNav && plugins.length > 0) {
                // Clear existing plugin tabs (except Plugin Manager)
                const existingTabs = pluginTabsNav.querySelectorAll('.plugin-tab');
                existingTabs.forEach(tab => tab.remove());
                
                // Add tabs for each installed plugin
                plugins.forEach(plugin => {
                    const tabButton = document.createElement('button');
                    tabButton.type = 'button';
                    tabButton.setAttribute('data-plugin-id', plugin.id);
                    tabButton.className = `plugin-tab nav-tab`;
                    tabButton.onclick = function() {
                        // Try to set activeTab via Alpine if available
                        if (window.Alpine) {
                            const appElement = document.querySelector('[x-data="app()"]');
                            if (appElement && appElement._x_dataStack && appElement._x_dataStack[0]) {
                                appElement._x_dataStack[0].activeTab = plugin.id;
                                // Only call updatePluginTabStates if it exists
                                if (typeof appElement._x_dataStack[0].updatePluginTabStates === 'function') {
                                    appElement._x_dataStack[0].updatePluginTabStates();
                                }
                            }
                        }
                    };
                    const iconClass = (plugin.icon || 'fas fa-puzzle-piece').replace(/"/g, '&quot;');
                    tabButton.innerHTML = `<i class="${iconClass}"></i>${(plugin.name || plugin.id).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}`;
                    pluginTabsNav.appendChild(tabButton);
                });
                debugLog('[GLOBAL] Updated plugin tabs directly:', plugins.length, 'tabs added');
            }
        });

        // Guard flag to prevent duplicate stub-to-full enhancement
        window._appEnhanced = false;

        // Define app() function early so Alpine can find it when it initializes
        // This is a complete implementation that will work immediately
        (function() {
            const isAPMode = window.location.hostname === '192.168.4.1' || 
                           window.location.hostname.startsWith('192.168.4.');
            
            // Create the app function - will be enhanced by full implementation later
            window.app = function() {
                return {
                    activeTab: isAPMode ? 'wifi' : 'overview',
                    mobileNavOpen: false,
                    installedPlugins: [],
                    
                    init() {
                        // Try to enhance immediately with full implementation
                        const tryEnhance = () => {
                            if (window._appEnhanced) return true;
                            if (typeof window.app === 'function') {
                                const fullApp = window.app();
                                // Check if this is the full implementation (has updatePluginTabs with proper implementation)
                                if (fullApp && typeof fullApp.updatePluginTabs === 'function' && fullApp.updatePluginTabs.toString().includes('_doUpdatePluginTabs')) {
                                    window._appEnhanced = true;
                                    // Preserve runtime state that should not be reset
                                    const preservedPlugins = this.installedPlugins;
                                    const preservedTab = this.activeTab;
                                    const defaultTab = isAPMode ? 'wifi' : 'overview';
                                    const wasInitialized = this._initialized;
                                    Object.assign(this, fullApp);
                                    // Restore runtime state if non-default
                                    if (preservedPlugins && preservedPlugins.length > 0) {
                                        this.installedPlugins = preservedPlugins;
                                    }
                                    if (preservedTab && preservedTab !== defaultTab) {
                                        this.activeTab = preservedTab;
                                    }
                                    if (wasInitialized) {
                                        this._initialized = wasInitialized;
                                    }
                                    // Only call init if not already initialized
                                    if (typeof this.init === 'function' && !this._initialized) {
                                        this.init();
                                    }
                                    return true;
                                }
                            }
                            return false;
                        };
                        
                        // Set up event listener for pluginsUpdated in stub (only if not already enhanced)
                        // The full implementation will have its own listener, so we only need this for the stub
                        if (!this._pluginsUpdatedListenerSet) {
                            const handlePluginsUpdated = (event) => {
                                debugLog('[STUB] Received pluginsUpdated event:', event.detail?.plugins?.length || 0, 'plugins');
                                const plugins = event.detail?.plugins || [];
                                // Only update if we're still in stub mode (not enhanced yet)
                                if (typeof this.updatePluginTabs === 'function' && !this.updatePluginTabs.toString().includes('_doUpdatePluginTabs')) {
                                    this.installedPlugins = plugins;
                                    if (this.$nextTick && typeof this.$nextTick === 'function') {
                                        this.$nextTick(() => {
                                            this.updatePluginTabs();
                                        });
                                    } else {
                                        setTimeout(() => {
                                            this.updatePluginTabs();
                                        }, 100);
                                    }
                                }
                            };
                            document.addEventListener('pluginsUpdated', handlePluginsUpdated);
                            this._pluginsUpdatedListenerSet = true;
                            debugLog('[STUB] init: Set up pluginsUpdated event listener');
                        }
                        
                        // Try immediately - if full implementation is already loaded, use it right away
                        if (!tryEnhance()) {
                            // Full implementation not ready yet, load plugins directly while waiting
                            this.loadInstalledPluginsDirectly();
                            // Try again very soon to enhance with full implementation
                            setTimeout(tryEnhance, 10);
                            
                            // Also set up a periodic check to update tabs if plugins get loaded by plugins_manager.js
                            let retryCount = 0;
                            const maxRetries = 20; // Check for 2 seconds (20 * 100ms)
                            const checkAndUpdateTabs = () => {
                                if (retryCount >= maxRetries) {
                                    // Fallback: if plugins_manager.js hasn't loaded after 2 seconds, fetch directly
                                    if (!window.installedPlugins || window.installedPlugins.length === 0) {
                                        debugLog('[STUB] checkAndUpdateTabs: Fallback - fetching plugins directly after timeout');
                                        this.loadInstalledPluginsDirectly();
                                    }
                                    return;
                                }
                                
                                // Check if plugins are available (either from window or component)
                                const plugins = window.installedPlugins || this.installedPlugins || [];
                                if (plugins.length > 0) {
                                    debugLog('[STUB] checkAndUpdateTabs: Found', plugins.length, 'plugins, updating tabs');
                                    this.installedPlugins = plugins;
                                    if (typeof this.updatePluginTabs === 'function') {
                                        this.updatePluginTabs();
                                    }
                                } else {
                                    retryCount++;
                                    setTimeout(checkAndUpdateTabs, 100);
                                }
                            };
                            // Start checking after a short delay
                            setTimeout(checkAndUpdateTabs, 200);
                        } else {
                            // Full implementation loaded, but still set up fallback timer
                            setTimeout(() => {
                                if (!window.installedPlugins || window.installedPlugins.length === 0) {
                                    debugLog('[STUB] init: Fallback timer - fetching plugins directly');
                                    this.loadInstalledPluginsDirectly();
                                }
                            }, 2000);
                        }
                    },
                    
                    // Direct plugin loading for stub (before full implementation loads)
                    async loadInstalledPluginsDirectly() {
                        try {
                            debugLog('[STUB] loadInstalledPluginsDirectly: Starting...');
                            // Ensure DOM is ready
                            const ensureDOMReady = () => {
                                return new Promise((resolve) => {
                                    if (document.readyState === 'complete' || document.readyState === 'interactive') {
                                        // Use requestAnimationFrame to ensure DOM is painted
                                        requestAnimationFrame(() => {
                                            setTimeout(resolve, 50); // Small delay to ensure rendering
                                        });
                                    } else {
                                        document.addEventListener('DOMContentLoaded', () => {
                                            requestAnimationFrame(() => {
                                                setTimeout(resolve, 50);
                                            });
                                        });
                                    }
                                });
                            };
                            
                            await ensureDOMReady();
                            
                            const data = await getInstalledPluginsSafe();
                            if (data.status === 'success') {
                                const plugins = data.data.plugins || [];
                                debugLog('[STUB] loadInstalledPluginsDirectly: Loaded', plugins.length, 'plugins');
                                
                                // Update both component and window
                                this.installedPlugins = plugins;
                                window.installedPlugins = plugins;
                                
                                // Dispatch event so global listener can update tabs
                                document.dispatchEvent(new CustomEvent('pluginsUpdated', {
                                    detail: { plugins: plugins }
                                }));
                                debugLog('[STUB] loadInstalledPluginsDirectly: Dispatched pluginsUpdated event');
                                
                                // Update tabs if we have the method - use $nextTick if available
                                if (typeof this.updatePluginTabs === 'function') {
                                    if (this.$nextTick && typeof this.$nextTick === 'function') {
                                        this.$nextTick(() => {
                                            this.updatePluginTabs();
                                        });
                                    } else {
                                        // Fallback: wait a bit for DOM
                                        setTimeout(() => {
                                            this.updatePluginTabs();
                                        }, 100);
                                    }
                                }
                            } else {
                                console.warn('[STUB] loadInstalledPluginsDirectly: Failed to load plugins:', data.message);
                            }
                        } catch (error) {
                            console.error('[STUB] loadInstalledPluginsDirectly: Error loading plugins:', error);
                        }
                    },
                    
                    // Stub methods that will be replaced by full implementation
                    loadTabContent: function(tab) {},
                    loadInstalledPlugins: async function() {
                        // Try to use global function if available, otherwise use direct loading
                        if (typeof window.loadInstalledPlugins === 'function') {
                            await window.loadInstalledPlugins();
                            // Update tabs after loading (window.installedPlugins should be set by the global function)
                            if (window.installedPlugins && Array.isArray(window.installedPlugins)) {
                                this.installedPlugins = window.installedPlugins;
                                this.updatePluginTabs();
                            }
                        } else if (typeof window.pluginManager?.loadInstalledPlugins === 'function') {
                            await window.pluginManager.loadInstalledPlugins();
                            // Update tabs after loading
                            if (window.installedPlugins && Array.isArray(window.installedPlugins)) {
                                this.installedPlugins = window.installedPlugins;
                                this.updatePluginTabs();
                            }
                        } else {
                            // Fallback to direct loading (which already calls updatePluginTabs)
                            await this.loadInstalledPluginsDirectly();
                        }
                    },
                    updatePluginTabs: function() {
                        // Basic implementation for stub - will be replaced by full implementation
                        // Debounce to prevent multiple rapid calls
                        if (this._updatePluginTabsTimeout) {
                            clearTimeout(this._updatePluginTabsTimeout);
                        }
                        
                        this._updatePluginTabsTimeout = setTimeout(() => {
                            debugLog('[STUB] updatePluginTabs: Executing with', this.installedPlugins?.length || 0, 'plugins');
                            const pluginTabsRow = document.getElementById('plugin-tabs-row');
                            const pluginTabsNav = pluginTabsRow?.querySelector('nav');
                            if (!pluginTabsRow || !pluginTabsNav) {
                                console.warn('[STUB] updatePluginTabs: Plugin tabs container not found');
                                return;
                            }
                            if (!this.installedPlugins || this.installedPlugins.length === 0) {
                                debugLog('[STUB] updatePluginTabs: No plugins to display');
                                return;
                            }
                            
                            // Check if tabs are already correct by comparing plugin IDs
                            const existingTabs = pluginTabsNav.querySelectorAll('.plugin-tab');
                            const existingIds = Array.from(existingTabs).map(tab => tab.getAttribute('data-plugin-id')).sort().join(',');
                            const currentIds = this.installedPlugins.map(p => p.id).sort().join(',');
                            
                            if (existingIds === currentIds && existingTabs.length === this.installedPlugins.length) {
                                debugLog('[STUB] updatePluginTabs: Tabs already match, skipping update');
                                return;
                            }
                            
                            // Clear existing plugin tabs (except Plugin Manager)
                            existingTabs.forEach(tab => tab.remove());
                            debugLog('[STUB] updatePluginTabs: Cleared', existingTabs.length, 'existing tabs');
                            
                            // Add tabs for each installed plugin
                            this.installedPlugins.forEach(plugin => {
                                const tabButton = document.createElement('button');
                                tabButton.type = 'button';
                                tabButton.setAttribute('data-plugin-id', plugin.id);
                                tabButton.className = `plugin-tab nav-tab ${this.activeTab === plugin.id ? 'nav-tab-active' : ''}`;
                                tabButton.onclick = () => {
                                    this.activeTab = plugin.id;
                                    if (typeof this.updatePluginTabStates === 'function') {
                                        this.updatePluginTabStates();
                                    }
                                };
                                const div = document.createElement('div');
                                div.textContent = plugin.name || plugin.id;
                                const iconClass = (plugin.icon || 'fas fa-puzzle-piece').replace(/"/g, '&quot;');
                                tabButton.innerHTML = `<i class="${iconClass}"></i>${div.innerHTML}`;
                                pluginTabsNav.appendChild(tabButton);
                            });
                            debugLog('[STUB] updatePluginTabs: Added', this.installedPlugins.length, 'plugin tabs');
                        }, 100);
                    },
                    showNotification: function(message, type) {},
                    escapeHtml: function(text) { return String(text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
                };
            };
        })();
