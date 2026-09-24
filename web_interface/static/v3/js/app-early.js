/* global debugLog */
/*
 * app-early.js -- helpers every other script relies on, and the app() stub.
 *
 * A blocking <script> in <head>, so everything here exists before any other
 * script, widget or partial runs.
 *
 * Load order (templates/v3/base.html):
 *   <head>, blocking:  debugLog and theme inline scripts; the htmx loader
 *                      (injects htmx.min.js with a dynamic <script>);
 *                      js/htmx-config.js; the loadPartialDirect fallback;
 *                      js/app-early.js
 *   <head>, defer:     js/app-shell.js, then js/alpinejs.min.js (Alpine
 *                      starts as soon as it runs, so app-shell.js's app()
 *                      is the one Alpine uses)
 *   end of <body>, defer, in this order: app.js, js/tooltips.js,
 *                      js/settings-search.js, js/utils/dialog.js,
 *                      js/utils/error_handler.js, js/plugins/api_client.js,
 *                      state_manager.js, install_manager.js, list_filter.js,
 *                      the widget bundle (web_interface/widget_bundle.py),
 *                      plugins_manager.js
 *   Tab partials arrive later through htmx; their inline scripts run on
 *   htmx:afterSwap (js/htmx-config.js).
 *
 * Globals:
 *   window.LEDEscape    html / attr / jsStringAttr, the only HTML escaper
 *   window.escapeHtml / window.escapeAttribute   aliases of LEDEscape.html /
 *                       .attr, kept for plugin pages
 *   window.getApp()     the root Alpine component (<body x-data="app()">)
 *   window.app          a stub app() so Alpine can start before app-shell.js
 *                       has run; app-shell.js replaces it with the full one
 *                       (in the normal load order Alpine only ever sees the
 *                       full one, see the note on the stub below)
 *   getInstalledPluginsSafe()   installed list via PluginAPI or fetch
 *   a pluginsUpdated listener that draws the plugin tab row while the app is
 *   not the full implementation yet
 */

        // ===== window.LEDEscape: the web UI's HTML escaping =====
        // This file is a blocking <script> in <head>, so every later script,
        // widget and partial can call these directly.
        //   html(v)          text for element content or a quoted attribute
        //                    value: & < > " ' become entities, null and
        //                    undefined become ''. Escaping quotes is what makes
        //                    it attribute-safe; a textContent/innerHTML round
        //                    trip only escapes & < and >.
        //   attr(v)          the same function, for call sites that want the
        //                    attribute context to read explicitly.
        //   jsStringAttr(v)  a quoted JS string literal for an inline handler
        //                    attribute: onclick='f(${jsStringAttr(id)})'.
        //                    JSON.stringify alone leaves ' and & untouched, so a
        //                    value containing ' could close a single-quoted
        //                    attribute. The browser decodes the entities before
        //                    it parses the handler, so the JS sees the literal.
        window.LEDEscape = (function() {
            const ENTITIES = new Map([['&', '&amp;'], ['<', '&lt;'], ['>', '&gt;'], ['"', '&quot;'], ["'", '&#39;']]);
            function html(value) {
                return value == null ? '' : String(value).replace(/[&<>"']/g, c => ENTITIES.get(c));
            }
            function jsStringAttr(value) {
                return html(JSON.stringify(value == null ? '' : String(value)));
            }
            // An attribute value needs no more than html(): it escapes both quote characters.
            function attr(value) {
                return html(value);
            }
            return Object.freeze({ html: html, attr: attr, jsStringAttr: jsStringAttr });
        })();

        // Kept for plugin web UIs and third-party plugin pages that call them.
        window.escapeHtml = window.LEDEscape.html;
        window.escapeAttribute = window.LEDEscape.attr;

        // ===== window.getApp(): the root Alpine component =====
        // The data of <body x-data="app()"> (activeTab, installedPlugins, ...),
        // read through Alpine's public Alpine.$data rather than the private
        // el._x_dataStack. Null until Alpine has initialised the app.
        window.getApp = function() {
            const el = document.querySelector('[x-data="app()"]');
            if (!el || !window.Alpine) return null;
            const data = window.Alpine.$data(el);
            return data && 'activeTab' in data ? data : null;
        };

        // The installed-plugin list through PluginAPI, or a plain fetch if
        // PluginAPI (loaded later, deferred) is not there yet.
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

        // Builds the plugin tab row from a pluginsUpdated event while the app is
        // not app-shell.js's full implementation (Alpine not started yet, or
        // still on the stub below). The full app() has its own pluginsUpdated
        // listener, registered in its init().
        document.addEventListener('pluginsUpdated', function(event) {
            const appComponent = window.getApp();
            if (appComponent && typeof appComponent._doUpdatePluginTabs === 'function') return;
            debugLog('[GLOBAL] Received pluginsUpdated event:', event.detail?.plugins?.length || 0, 'plugins');
            const plugins = event.detail?.plugins || [];
            if (appComponent) {
                appComponent.installedPlugins = plugins;
            }

            const pluginTabsRow = document.getElementById('plugin-tabs-row');
            const pluginTabsNav = pluginTabsRow?.querySelector('nav');
            if (pluginTabsRow && pluginTabsNav && plugins.length > 0) {
                // Clear existing plugin tabs (except Plugin Manager)
                const existingTabs = pluginTabsNav.querySelectorAll('.plugin-tab');
                existingTabs.forEach(tab => { tab.remove(); });
                
                // Add tabs for each installed plugin
                plugins.forEach(plugin => {
                    const tabButton = document.createElement('button');
                    tabButton.type = 'button';
                    tabButton.setAttribute('data-plugin-id', plugin.id);
                    tabButton.className = `plugin-tab nav-tab`;
                    tabButton.onclick = function() {
                        const app = window.getApp();
                        if (app) {
                            app.activeTab = plugin.id;
                            if (typeof app.updatePluginTabStates === 'function') {
                                app.updatePluginTabStates();
                            }
                        }
                    };
                    // Built with DOM APIs (no innerHTML): the icon class and
                    // name come from plugin manifests, which are only
                    // semi-trusted input.
                    const tabIcon = document.createElement('i');
                    tabIcon.className = plugin.icon || 'fas fa-puzzle-piece';
                    tabButton.textContent = '';
                    tabButton.appendChild(tabIcon);
                    tabButton.appendChild(document.createTextNode(plugin.name || plugin.id));
                    pluginTabsNav.appendChild(tabButton);
                });
                debugLog('[GLOBAL] Updated plugin tabs directly:', plugins.length, 'tabs added');
            }
        });

        // Guard flag to prevent duplicate stub-to-full enhancement
        window._appEnhanced = false;

        // The stub app(). If Alpine initialises before app-shell.js has run,
        // this object is what it gets; its init() copies the full
        // implementation in as soon as app-shell.js has replaced window.app
        // (the "enhancement", guarded by window._appEnhanced), and draws the
        // plugin tabs itself until then. base.html now loads app-shell.js
        // before Alpine, so in practice Alpine calls the full app() directly
        // and this stub never runs.
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
                            existingTabs.forEach(tab => { tab.remove(); });
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
                                // DOM APIs instead of innerHTML: manifest
                                // icon/name are semi-trusted input.
                                const tabIcon = document.createElement('i');
                                tabIcon.className = plugin.icon || 'fas fa-puzzle-piece';
                                tabButton.textContent = '';
                                tabButton.appendChild(tabIcon);
                                tabButton.appendChild(document.createTextNode(plugin.name || plugin.id));
                                pluginTabsNav.appendChild(tabButton);
                            });
                            debugLog('[STUB] updatePluginTabs: Added', this.installedPlugins.length, 'plugin tabs');
                        }, 100);
                    },
                    showNotification: function(message, type) {}
                };
            };
        })();
